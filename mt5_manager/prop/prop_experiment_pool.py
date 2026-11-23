"""Pool de candidatos y seeds para el laboratorio prop de solo lectura."""

from __future__ import annotations

import sqlite3
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    MarginModel,
    RobustStrategySet,
    load_robust_sets_from_rows,
    load_symbol_notional,
    portfolio_group_key,
)

from ..experiment import experiment_lab as lab
from ..experiment.experiment_seed_sets import load_seed_sets
from ..portfolio.portfolio_coordinator_core import _relocated_member
from ..portfolio.portfolio_identity import _allocation_source_rows
from ..portfolio.portfolio_service import build_margin_model, cached_report
from .prop_experiment_engine import PropStrategy, executable_pool
from .prop_profiles import profile


def _set_name(value: Any) -> str:
    return Path(str(value or "")).name.casefold()


def _logical_set_name(value: Any) -> str:
    return re.sub(r"^robust_\d{6}_", "", _set_name(value))


def _quarantine_names(source: Any) -> set[str]:
    names: set[str] = set()
    for row in source.quarantine_rows():
        path = row.get("set_path")
        names.update((_set_name(path), _logical_set_name(path)))
    return {name for name in names if name}


def _without_quarantine(items: list[Any], names: set[str]) -> list[Any]:
    return [
        item for item in items
        if _set_name(item.set_path) not in names
        and _logical_set_name(item.set_path) not in names
    ]


def _row_family_allowed(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    allowed = set(settings.get("symbol_families") or [])
    disabled = {str(item).casefold() for item in settings.get("disabled_symbols") or []}
    symbol = row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol")
    return (
        str(symbol or "").casefold() not in disabled
        and (not allowed or portfolio_group_key(str(symbol or "")) in allowed)
    )


def _candidate_ohlc_row(row: dict[str, Any]) -> dict[str, Any]:
    """Conserva la elegibilidad FT6M, pero entrega sólo IS + OOS OHLC al cargador."""
    return {
        **row,
        "full_history_report_path": "",
        "final_tick_report_path": "",
        "real_tick_report_path": "",
        "final_tick_to_date": "",
    }


def _manual_ohlc_row(row: dict[str, Any]) -> dict[str, Any]:
    """Un manual conserva su histórico OHLC y nunca aporta una cola Every Tick."""
    return {**row, "final_tick_report_path": "", "real_tick_report_path": ""}


def _allowed_names(
    settings: dict[str, Any], node_id: str, source: Any,
    active_members_factory: Callable[[str, Any], list[dict[str, Any]]] | None,
) -> set[str] | None:
    if not settings["active_portfolios_only"]:
        return None
    if active_members_factory is None:
        raise ValueError("No está disponible el inventario de portafolios activos")
    return {
        _set_name(member.get("set_path") or member.get("set_id"))
        for member in active_members_factory(node_id, source)
    }


def _candidate_sets(
    source: Any, settings: dict[str, Any], allowed: set[str] | None,
    warnings: list[str], checkpoint: Callable[[str], None],
) -> tuple[list[Any], int, int]:
    if not settings["include_candidates"]:
        return [], 0, 0
    rows = source.candidate_rows(include_quarantined=False)
    if allowed is not None:
        rows = [row for row in rows if _set_name(row.get("set_path")) in allowed]
    total = len(rows)
    rows = [_candidate_ohlc_row(row) for row in rows if _row_family_allowed(row, settings)]
    checkpoint(f"{source.broker}: cargando {len(rows)} candidatas de {total}")
    loaded, notes = load_robust_sets_from_rows(
        rows, [], parse=cached_report,
        progress=lambda message: checkpoint(f"{source.broker} · {message}"),
    )
    warnings.extend(f"{source.broker}: {note}" for note in notes)
    return loaded, total, len(rows)


def _seed_sets(
    source: Any, settings: dict[str, Any], allowed: set[str] | None,
    warnings: list[str], checkpoint: Callable[[str], None],
) -> tuple[list[Any], int]:
    if not settings["include_seeds"]:
        return [], 0
    checkpoint(f"{source.broker}: leyendo seeds con Final Tick 6M aceptado")
    loaded, notes, total = load_seed_sets(
        source, parse=cached_report, limit=0,
        require_final_tick_6m=True, allowed_set_names=allowed,
    )
    loaded = _filter_symbol_families(loaded, settings)
    warnings.extend(notes)
    return loaded, total


def _manual_member_rows(source: Any, allowed: set[str] | None) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    saved_state = source.saved_portfolios("full_history")
    for saved in saved_state.get("portfolios") or []:
        if not saved.get("manual_portfolio"):
            continue
        portfolio_id = saved.get("id")
        detail = source.saved_portfolio_detail(int(portfolio_id), "full_history")
        for member in detail.get("portfolio", {}).get("members") or []:
            relocated = _relocated_member(member, source.project, portfolio_id)
            if allowed is None or _set_name(relocated.get("set_path")) in allowed:
                members.append(relocated)
    return _allocation_source_rows(members)


def _manual_sets(
    source: Any, settings: dict[str, Any], allowed: set[str] | None,
    warnings: list[str], checkpoint: Callable[[str], None],
) -> tuple[list[Any], int]:
    if not settings["include_manual_portfolios"]:
        return [], 0
    rows = [_manual_ohlc_row(row) for row in _manual_member_rows(source, allowed)]
    checkpoint(f"{source.broker}: cargando {len(rows)} estrategias de portafolios manuales")
    loaded, notes = load_robust_sets_from_rows(
        rows, [], parse=cached_report,
        progress=lambda message: checkpoint(f"{source.broker} · {message}"),
    )
    warnings.extend(f"{source.broker}: {note}" for note in notes)
    return loaded, len(rows)


def _filter_symbol_families(items: list[Any], settings: dict[str, Any]) -> list[Any]:
    allowed = set(settings.get("symbol_families") or [])
    disabled = {str(item).casefold() for item in settings.get("disabled_symbols") or []}
    if not allowed and not disabled:
        return items
    return [
        item for item in items
        if str(item.symbol).casefold() not in disabled
        and (not allowed or portfolio_group_key(item.symbol) in allowed)
    ]


def _prop_margin_model(source: Any, settings: dict[str, Any]) -> Any:
    prop_profile = profile(str(settings["profile_id"]))
    leverage = prop_profile.account_leverage
    base = build_margin_model(source, {
        "margin_profile": source.broker.lower(), "account_leverage": leverage,
    })
    normalization = getattr(source, "normalization", None)
    fallback_notional, _groups, origin = load_symbol_notional(normalization) if normalization else ({}, {}, "")
    return replace(
        base, profile=prop_profile.profile_id, account_leverage=leverage,
        group_leverage=dict(prop_profile.leverage_by_family),
        symbol_margin={}, max_product_leverage={},
        symbol_notional={**fallback_notional, **base.symbol_notional},
        group_notional={}, notional_source=base.notional_source or origin,
        stock_leverage=leverage, default_leverage=leverage,
    )


def load_origin(
    source: Any, node_id: str, settings: dict[str, Any], warnings: list[str],
    checkpoint: Callable[[str], None], allowed: set[str] | None,
) -> tuple[list[tuple[Any, str, str]], set[str], set[str], dict[str, Any]]:
    candidates, candidate_total, candidate_read = _candidate_sets(
        source, settings, allowed, warnings, checkpoint,
    )
    seeds, seed_total = _seed_sets(source, settings, allowed, warnings, checkpoint)
    manual, manual_total = _manual_sets(source, settings, allowed, warnings, checkpoint)
    unfiltered = len(candidates) + len(seeds) + len(manual)
    quarantined = _quarantine_names(source)
    candidates = _without_quarantine(candidates, quarantined)
    seeds = _without_quarantine(seeds, quarantined)
    manual = _without_quarantine(manual, quarantined)
    quarantine_filtered_out = unfiltered - len(candidates) - len(seeds) - len(manual)
    if quarantine_filtered_out:
        checkpoint(
            f"{source.broker}: {quarantine_filtered_out} estrategias excluidas por cuarentena"
        )
    after_quarantine = len(candidates) + len(seeds) + len(manual)
    candidates = _filter_symbol_families(candidates, settings)
    seeds = _filter_symbol_families(seeds, settings)
    manual = _filter_symbol_families(manual, settings)
    after_family = len(candidates) + len(seeds) + len(manual)
    candidate_names = {_logical_set_name(item.set_path) for item in candidates}
    seeds = [item for item in seeds if _logical_set_name(item.set_path) not in candidate_names]
    used_names = candidate_names | {_logical_set_name(item.set_path) for item in seeds}
    manual = [item for item in manual if _logical_set_name(item.set_path) not in used_names]
    loaded = [(item, source.broker, node_id) for item in candidates + seeds + manual]
    seed_ids = {item.set_id for item in seeds}
    manual_ids = {item.set_id for item in manual}
    meta = {
        "broker": source.broker,
        "candidate_total": candidate_total,
        "candidate_read": candidate_read,
        "candidates_loaded": len(candidates),
        "seed_total": seed_total,
        "seeds_loaded": len(seeds),
        "manual_total": manual_total,
        "manual_loaded": len(manual),
        "quarantine_filtered_out": quarantine_filtered_out,
        "family_filtered_out": after_quarantine - after_family,
        "duplicate_filtered_out": after_family - len(candidates) - len(seeds) - len(manual),
        "error": "",
    }
    return loaded, seed_ids, manual_ids, meta


def load_pool(
    settings: dict[str, Any], source_factory: Callable[[str], Any],
    checkpoint: Callable[[str], None],
    active_members_factory: Callable[[str, Any], list[dict[str, Any]]] | None = None,
    symbol_settings_factory: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[list[PropStrategy], lab.LabAxis, list[str], dict[str, Any]]:
    loaded: list[tuple[RobustStrategySet, str, str]] = []
    seed_ids: set[str] = set()
    manual_ids: set[str] = set()
    warnings: list[str] = []
    origins: dict[str, Any] = {}
    margins: dict[str, MarginModel] = {}
    for node_id in settings["source_nodes"]:
        try:
            source = source_factory(node_id)
            origin_settings = {
                **settings,
                "include_candidates": True,
                "include_seeds": True,
                "include_manual_portfolios": True,
            }
            if symbol_settings_factory is not None:
                origin_settings["disabled_symbols"] = (
                    symbol_settings_factory(node_id).get("disabled_symbols") or []
                )
            allowed = _allowed_names(origin_settings, node_id, source, active_members_factory)
            items, ids, manual, meta = load_origin(
                source, node_id, origin_settings, warnings, checkpoint, allowed,
            )
            margins[node_id] = _prop_margin_model(source, settings)
            loaded.extend(items)
            seed_ids.update(ids)
            manual_ids.update(manual)
            origins[node_id] = meta
        except (OSError, ValueError, sqlite3.Error) as exc:
            warnings.append(f"{node_id}: {exc}")
            origins[node_id] = {
                "error": str(exc), "candidates_loaded": 0,
                "seeds_loaded": 0, "manual_loaded": 0,
            }
    if not loaded:
        raise ValueError("Ningún broker devolvió estrategias cargables")
    return _assemble_pool(loaded, margins, seed_ids, manual_ids, warnings, origins, checkpoint)


def _assemble_pool(loaded, margins, seed_ids, manual_ids, warnings, origins, checkpoint):
    pool, axis, notes = lab.build_lab_strategies(
        loaded, margin_model=next(iter(margins.values())), portable_symbols=frozenset(),
        window_months=0, progress=checkpoint, margin_by_node=margins,
    )
    warnings.extend(notes)
    seed_keys = {item.key for item in pool if item.set_id in seed_ids}
    manual_keys = {item.key for item in pool if item.set_id in manual_ids}
    evidence = {(node, item.set_id): item for item, _broker, node in loaded}
    executable = executable_pool(pool, seed_keys, manual_keys)
    return [_with_evidence(item, evidence, margins) for item in executable], axis, warnings, origins


def _with_evidence(
    item: PropStrategy, evidence: dict[tuple[str, str], RobustStrategySet],
    margins: dict[str, MarginModel],
) -> PropStrategy:
    node = item.lab.origin_node
    raw = evidence[node, item.lab.set_id]
    model = margins[node]
    symbol = item.lab.symbol_key
    lot = model.symbol_min_lot.get(symbol, 0.0)
    margin = item.lab.margin_per_unit
    if model.notional_for(item.lab.symbol) is None and symbol not in model.symbol_contract_size:
        margin = float("inf")
    return replace(
        item, lot_per_unit=lot, lab=replace(item.lab, margin_per_unit=margin),
        closed_trades=tuple(raw.closed_trades_2020_2026), coverage=raw.report_coverage,
    )
