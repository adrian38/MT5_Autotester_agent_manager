"""Pool de candidatos y seeds para el laboratorio prop de solo lectura."""

from __future__ import annotations

import sqlite3
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.mt5_report import parse_report
from portfolio_manager.ubs_portfolio import (
    MarginModel,
    RobustStrategySet,
    portfolio_group_key,
    portfolio_symbol_key,
)

from ..experiment import experiment_lab as lab
from ..experiment.experiment_seed_sets import accepted_seed_rows, load_seed_sets, single_report_strategy
from ..portfolio.portfolio_coordinator_core import _relocated_member
from ..portfolio.portfolio_identity import _allocation_source_rows
from .prop_experiment_engine import PropStrategy, executable_pool, set_path_is_grid
from .prop_execution import destination_contract, source_volume_spec


def _set_name(value: Any) -> str:
    return Path(str(value or "")).name.casefold()


def _logical_set_name(value: Any) -> str:
    return re.sub(r"^robust_\d{6}_", "", _set_name(value))


def _quarantine_keys(source: Any) -> set[str]:
    """Las rutas excluidas, con la clave con la que las compara el inventario.

    Compararlas por nombre de fichero daba falsos positivos: dos runs distintos
    reutilizan el mismo nombre —``..._g002_s001_v003.set`` son posiciones
    dentro de su run, no una identidad— para estrategias con otros parámetros.
    Excluir el set del 13 de agosto tiraba también al homónimo del 15, que
    nadie excluyó: seis candidatos de ICTrading que la pantalla de inventario
    sí daba por disponibles.
    """
    keys = {source._match_key(row.get("set_path")) for row in source.quarantine_rows()}
    return {key for key in keys if key}


def _without_quarantine(items: list[Any], source: Any, keys: set[str]) -> list[Any]:
    return [item for item in items if source._match_key(item.set_path) not in keys]


def _row_family_allowed(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    allowed = set(settings.get("symbol_families") or [])
    disabled = {str(item).casefold() for item in settings.get("disabled_symbols") or []}
    symbol = row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol")
    return (
        str(symbol or "").casefold() not in disabled
        and (not allowed or portfolio_group_key(str(symbol or "")) in allowed)
    )


def _ohlc_report(path):
    # Thousands of distinct passes belong to this job. Retain only their
    # reconstructed closed trades and let the HTML parser release raw deals.
    report = parse_report(path)
    model = str(getattr(report, 'model', '') or '').casefold()
    if model and model != '1' and 'ohlc' not in model:
        raise ValueError(f'Modelo incompatible con OHLC M1: {model}')
    return report


def _ohlc_segment(settings):
    return 'is_report_path' if settings.get('historical') else 'oos_report_path'


def _read_ohlc_sets(rows, path_key, source_label, warnings, checkpoint):
    """Use the requested OHLC pass directly, without validating an unrelated window."""
    loaded = []
    errors = []
    for index, row in enumerate(rows, 1):
        if index % 50 == 0:
            checkpoint(f'{source_label} · cargando OHLC {index}/{len(rows)}')
        try:
            path = row.get(path_key) or row.get('single_pass_report_path')
            set_path = str(row.get('set_path') or '')
            if not path or not set_path or not Path(path).is_file():
                raise ValueError('falta el informe OHLC del tramo')
            report = _ohlc_report(Path(path))
            symbol = row.get('executable_symbol') or row.get('target_symbol') or row.get('symbol')
            if symbol and portfolio_symbol_key(symbol) != portfolio_symbol_key(report.symbol):
                raise ValueError(f'símbolo OHLC {report.symbol} distinto de {symbol}')
            candidate = single_report_strategy(report, row, set_path, set_path, 'accepted', report_path=path)
            loaded.append(replace(candidate, timeframe=str(row.get('period') or report.timeframe)))
        except (OSError, ValueError) as exc:
            errors.append(f"{Path(str(row.get('set_path') or '')).name}: {exc}")
    if errors:
        warnings.append(f'{source_label}: {len(errors)}/{len(rows)} informes OHLC omitidos. '
                        + ' | '.join(errors[:5]))
    return loaded


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
    checkpoint(f"{source.broker}: cargando {len(rows)} candidatas de {total}")
    loaded = _read_ohlc_sets(rows, _ohlc_segment(settings), f'{source.broker} · candidatas', warnings, checkpoint)
    return loaded, total, len(rows)


def _seed_sets(
    source: Any, settings: dict[str, Any], allowed: set[str] | None,
    warnings: list[str], checkpoint: Callable[[str], None],
) -> tuple[list[Any], int]:
    if not settings["include_seeds"]:
        return [], 0
    checkpoint(f"{source.broker}: leyendo seeds con Final Tick 6M aceptado")
    loaded, notes, total = load_seed_sets(
        source, parse=_ohlc_report, limit=0,
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
    rows = _manual_member_rows(source, allowed)
    checkpoint(f"{source.broker}: cargando {len(rows)} estrategias de portafolios manuales")
    loaded = _read_ohlc_sets(rows, _ohlc_segment(settings), f'{source.broker} · manuales', warnings, checkpoint)
    return loaded, len(rows)


def load_fx_reference(source_factory, settings, checkpoint, currencies):
    """Read only OHLC FX observations needed to price another selected family in USD."""
    references = []
    pairs = {f'{currency}USD' for currency in currencies} | {f'USD{currency}' for currency in currencies}
    for node in settings['source_nodes']:
        try:
            source = source_factory(node)
            rows = source.candidate_rows(include_quarantined=False)
        except (OSError, ValueError, sqlite3.Error):
            continue
        selected = {pair: [] for pair in pairs}
        for row in rows:
            symbol = portfolio_symbol_key(row.get('executable_symbol') or row.get('target_symbol') or row.get('symbol'))
            if symbol in selected and len(selected[symbol]) < 12:
                selected[symbol].append(row)
        read = _read_ohlc_sets([row for group in selected.values() for row in group], _ohlc_segment(settings),
                              f'{source.broker} · cotizaciones USD', [], checkpoint)
        references.extend(trade for item in read for trade in item.closed_trades_2020_2026)
    return references


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


def _inventory_rows(rows, settings, allowed, excluded, path_field):
    families = set(settings.get("symbol_families") or [])
    disabled = {str(item).casefold() for item in settings.get("disabled_symbols") or []}
    grids = bool(settings.get("include_grids"))
    result = []
    for raw in rows:
        row = dict(raw)
        path = row.get(path_field)
        name, logical = _set_name(path), _logical_set_name(path)
        symbol = str(
            row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol") or ""
        )
        if allowed is not None and name not in allowed:
            continue
        if excluded(path) or symbol.casefold() in disabled:
            continue
        if families and portfolio_group_key(symbol) not in families:
            continue
        if not grids and set_path_is_grid(path):
            continue
        row["_logical_name"] = logical
        result.append(row)
    return result


def _source_inventory(source, settings, allowed) -> dict[str, Any]:
    quarantined = _quarantine_keys(source)

    def excluded(path: Any) -> bool:
        return source._match_key(path) in quarantined

    candidates = _inventory_rows(
        source.candidate_rows(include_quarantined=False), settings, allowed, excluded, "set_path",
    )
    seeds = _inventory_rows(
        accepted_seed_rows(source, require_final_tick_6m=True),
        settings, allowed, excluded, "seed_path",
    )
    manual = _inventory_rows(
        _manual_member_rows(source, allowed), settings, allowed, excluded, "set_path",
    )
    candidate_names = {row["_logical_name"] for row in candidates}
    seeds = [row for row in seeds if row["_logical_name"] not in candidate_names]
    used = candidate_names | {row["_logical_name"] for row in seeds}
    manual = [row for row in manual if row["_logical_name"] not in used]
    return {
        "candidates": len(candidates), "seeds": len(seeds), "manual": len(manual),
        "total": len(candidates) + len(seeds) + len(manual),
    }


def load_inventory(
    settings, source_factory, active_members_factory=None, symbol_settings_factory=None,
) -> dict[str, Any]:
    rows = []
    for node_id in settings["source_nodes"]:
        try:
            source = source_factory(node_id)
            origin_settings = dict(settings)
            if symbol_settings_factory is not None:
                origin_settings["disabled_symbols"] = (
                    symbol_settings_factory(node_id).get("disabled_symbols") or []
                )
            allowed = _allowed_names(origin_settings, node_id, source, active_members_factory)
            counts = _source_inventory(source, origin_settings, allowed)
            rows.append({"node_id": node_id, "broker": source.broker, **counts})
        except (OSError, ValueError, sqlite3.Error) as exc:
            rows.append({
                "node_id": node_id, "broker": node_id, "candidates": 0,
                "seeds": 0, "manual": 0, "total": 0, "error": str(exc),
            })
    rows.sort(key=lambda row: (row["broker"], row["node_id"]))
    return {"strategies": sum(row["total"] for row in rows), "by_broker": rows}


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
    quarantined = _quarantine_keys(source)
    candidates = _without_quarantine(candidates, source, quarantined)
    seeds = _without_quarantine(seeds, source, quarantined)
    manual = _without_quarantine(manual, source, quarantined)
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
    volume_specs = {}
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
            volume_specs[node_id] = _source_specs(source)
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
    return _assemble_pool(loaded, seed_ids, manual_ids, warnings, origins, checkpoint, volume_specs)


def _source_specs(source):
    path = getattr(source, 'symbol_specs', None)
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig')) if path else {}
        rows = raw.get('symbols', {})
    except (OSError, ValueError, AttributeError):
        rows = {}
    rows = rows if isinstance(rows, dict) else {}
    return ({str(symbol).upper(): spec for symbol, spec in rows.items() if isinstance(spec, dict)},
            source.broker, getattr(source, 'account', ''))


def _assemble_pool(loaded, seed_ids, manual_ids, warnings, origins, checkpoint, volume_specs):
    pool, axis, notes = lab.build_lab_strategies(
        loaded, margin_model=MarginModel(), portable_symbols=frozenset(),
        window_months=0, progress=checkpoint,
    )
    warnings.extend(notes)
    seed_keys = {item.key for item in pool if item.set_id in seed_ids}
    manual_keys = {item.key for item in pool if item.set_id in manual_ids}
    evidence = {(node, item.set_id): item for item, _broker, node in loaded}
    executable = executable_pool(pool, seed_keys, manual_keys)
    return [_with_evidence(item, evidence, volume_specs) for item in executable], axis, warnings, origins


def _with_evidence(
    item: PropStrategy, evidence: dict[tuple[str, str], RobustStrategySet],
    volume_specs: dict,
) -> PropStrategy:
    node = item.lab.origin_node
    raw = evidence[node, item.lab.set_id]
    contracts = [source_volume_spec(trade_symbol, *volume_specs[node]) for trade_symbol in {
        trade.symbol for trade in raw.closed_trades_2020_2026
    }] if node in volume_specs else []
    source_volume = contracts[0] if contracts and all(spec == contracts[0] for spec in contracts) else None
    return replace(
        item, lab=replace(item.lab, margin_per_unit=float('inf')),
        closed_trades=tuple(raw.closed_trades_2020_2026), coverage=raw.report_coverage,
        source_volume=source_volume, prop_contract=destination_contract(item.lab.symbol),
    )
