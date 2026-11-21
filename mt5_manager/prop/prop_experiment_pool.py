"""Pool de candidatos y seeds para el laboratorio prop de solo lectura."""

from __future__ import annotations

import sqlite3
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    load_robust_sets_from_rows,
    portfolio_group_key,
)

from ..experiment import experiment_lab as lab
from ..experiment.experiment_seed_sets import load_seed_sets
from ..experiment.experiment_service import ExperimentCoordinator
from ..portfolio.portfolio_coordinator_core import _relocated_member
from ..portfolio.portfolio_identity import _allocation_source_rows
from ..portfolio.portfolio_service import build_margin_model, cached_report
from .prop_profiles import profile


def _set_name(value: Any) -> str:
    return Path(str(value or "")).name.casefold()


def _logical_set_name(value: Any) -> str:
    return re.sub(r"^robust_\d{6}_", "", _set_name(value))


def _row_family_allowed(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    allowed = set(settings.get("symbol_families") or [])
    symbol = row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol")
    return not allowed or portfolio_group_key(str(symbol or "")) in allowed


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
    rows = [row for row in rows if _row_family_allowed(row, settings)]
    trim_settings = {"max_candidates_per_node": settings["max_candidates_per_node"]}
    rows = ExperimentCoordinator._trim_candidates(rows, trim_settings, source.broker, warnings)
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
    limit = int(settings["max_seeds_per_node"])
    if limit > 0:
        loaded = loaded[:limit]
    warnings.extend(notes)
    return loaded, total


def _manual_member_rows(source: Any, allowed: set[str] | None) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for saved in source.saved_portfolios("full_history"):
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
    rows = _manual_member_rows(source, allowed)
    checkpoint(f"{source.broker}: cargando {len(rows)} estrategias de portafolios manuales")
    loaded, notes = load_robust_sets_from_rows(
        rows, [], parse=cached_report,
        progress=lambda message: checkpoint(f"{source.broker} · {message}"),
    )
    warnings.extend(f"{source.broker}: {note}" for note in notes)
    return loaded, len(rows)


def _filter_symbol_families(items: list[Any], settings: dict[str, Any]) -> list[Any]:
    allowed = set(settings.get("symbol_families") or [])
    if not allowed:
        return items
    return [item for item in items if portfolio_group_key(item.symbol) in allowed]


def _prop_margin_model(source: Any, settings: dict[str, Any]) -> Any:
    prop_profile = profile(str(settings["profile_id"]))
    leverage = prop_profile.account_leverage
    base = build_margin_model(source, {
        "margin_profile": source.broker.lower(), "account_leverage": leverage,
    })
    return replace(
        base, profile=prop_profile.profile_id, account_leverage=leverage,
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
    candidates = _filter_symbol_families(candidates, settings)
    seeds = _filter_symbol_families(seeds, settings)
    manual = _filter_symbol_families(manual, settings)
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
        "family_filtered_out": unfiltered - len(candidates) - len(seeds) - len(manual),
        "error": "",
    }
    return loaded, seed_ids, manual_ids, meta


def load_pool(
    settings: dict[str, Any], source_factory: Callable[[str], Any],
    checkpoint: Callable[[str], None],
    active_members_factory: Callable[[str, Any], list[dict[str, Any]]] | None = None,
) -> tuple[list[lab.LabStrategy], lab.LabAxis, set[str], set[str], list[str], dict[str, Any]]:
    loaded: list[tuple[Any, str, str]] = []
    seed_ids: set[str] = set()
    manual_ids: set[str] = set()
    warnings: list[str] = []
    origins: dict[str, Any] = {}
    first_source = None
    for node_id in settings["source_nodes"]:
        try:
            source = source_factory(node_id)
            allowed = _allowed_names(settings, node_id, source, active_members_factory)
            items, ids, manual, meta = load_origin(
                source, node_id, settings, warnings, checkpoint, allowed,
            )
            first_source = first_source or source
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
    if not loaded or first_source is None:
        raise ValueError("Ningún broker devolvió estrategias cargables")
    margin = _prop_margin_model(first_source, settings)
    pool, axis, notes = lab.build_lab_strategies(
        loaded, margin_model=margin, portable_symbols=frozenset(),
        window_months=0, progress=checkpoint,
    )
    warnings.extend(notes)
    seed_keys = {item.key for item in pool if item.set_id in seed_ids}
    manual_keys = {item.key for item in pool if item.set_id in manual_ids}
    return pool, axis, seed_keys, manual_keys, warnings, origins
