"""Pool de candidatos y seeds para el laboratorio prop de solo lectura."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    DEFAULT_ACCOUNT_LEVERAGE,
    load_robust_sets_from_rows,
)

from ..experiment import experiment_lab as lab
from ..experiment.experiment_service import ExperimentCoordinator
from ..portfolio_service import build_margin_model, cached_report
from .prop_experiment_seeds import load_seed_sets


def _candidate_sets(
    source: Any, settings: dict[str, Any], warnings: list[str], checkpoint: Callable[[str], None],
) -> tuple[list[Any], int, int]:
    if not settings["include_candidates"]:
        return [], 0, 0
    rows = source.candidate_rows(include_quarantined=False)
    total = len(rows)
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
    source: Any, settings: dict[str, Any], warnings: list[str], checkpoint: Callable[[str], None],
) -> tuple[list[Any], int]:
    if not settings["include_seeds"]:
        return [], 0
    checkpoint(f"{source.broker}: leyendo seeds aceptadas")
    loaded, notes, total = load_seed_sets(
        source, parse=cached_report, limit=int(settings["max_seeds_per_node"]),
    )
    warnings.extend(notes)
    return loaded, total


def load_origin(
    source: Any, node_id: str, settings: dict[str, Any], warnings: list[str],
    checkpoint: Callable[[str], None],
) -> tuple[list[tuple[Any, str, str]], set[str], dict[str, Any]]:
    candidates, candidate_total, candidate_read = _candidate_sets(
        source, settings, warnings, checkpoint,
    )
    seeds, seed_total = _seed_sets(source, settings, warnings, checkpoint)
    candidate_paths = {str(Path(item.set_path)).casefold() for item in candidates if item.set_path}
    seeds = [item for item in seeds if str(Path(item.set_path)).casefold() not in candidate_paths]
    loaded = [(item, source.broker, node_id) for item in candidates + seeds]
    seed_ids = {item.set_id for item in seeds}
    meta = {
        "broker": source.broker,
        "candidate_total": candidate_total,
        "candidate_read": candidate_read,
        "candidates_loaded": len(candidates),
        "seed_total": seed_total,
        "seeds_loaded": len(seeds),
        "error": "",
    }
    return loaded, seed_ids, meta


def load_pool(
    settings: dict[str, Any], source_factory: Callable[[str], Any],
    checkpoint: Callable[[str], None],
) -> tuple[list[lab.LabStrategy], lab.LabAxis, set[str], list[str], dict[str, Any]]:
    loaded: list[tuple[Any, str, str]] = []
    seed_ids: set[str] = set()
    warnings: list[str] = []
    origins: dict[str, Any] = {}
    first_source = None
    for node_id in settings["source_nodes"]:
        try:
            source = source_factory(node_id)
            items, ids, meta = load_origin(source, node_id, settings, warnings, checkpoint)
            first_source = first_source or source
            loaded.extend(items)
            seed_ids.update(ids)
            origins[node_id] = meta
        except (OSError, ValueError, sqlite3.Error) as exc:
            warnings.append(f"{node_id}: {exc}")
            origins[node_id] = {"error": str(exc), "candidates_loaded": 0, "seeds_loaded": 0}
    if not loaded or first_source is None:
        raise ValueError("Ningún broker devolvió candidatas o seeds cargables")
    margin = build_margin_model(first_source, {
        "margin_profile": first_source.broker.lower(),
        "account_leverage": DEFAULT_ACCOUNT_LEVERAGE,
    })
    pool, axis, notes = lab.build_lab_strategies(
        loaded, margin_model=margin, portable_symbols=frozenset(),
        window_months=int(settings["horizon_months"]), progress=checkpoint,
    )
    warnings.extend(notes)
    seed_keys = {item.key for item in pool if item.set_id in seed_ids}
    return pool, axis, seed_keys, warnings, origins
