from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    optimizer_overrides,
    load_robust_sets_from_rows,
    optimize_portfolio,
    summarize_robust_rows,
)

from .portfolio_improvement_common import (
    member_rows,
    recent_positive_candidates,
    used_paths_for_improvement,
)
from .portfolio_improvement_support import (
    MAX_FILLER_RETRIES,
    _candidate_rows_for_improvement,
    _saved_single_mode,
    _selected_variant_detail,
)
from .portfolio_service import (
    PORTFOLIO_TYPES,
    TYPE_LABELS,
    PortfolioSource,
    _optimizer_kwargs,
    _reserve_pct,
    _resolve_source_path,
    _seasonal_coverage,
    _underrepresented_recent_allocation_ids,
    build_margin_model,
    cached_report,
    settings_inputs,
)


Progress = Callable[[str], None]


@dataclass(frozen=True)
class _RepairContext:
    source: PortfolioSource
    portfolio_id: int
    target: str
    detail: dict[str, Any]
    inputs: dict[str, Any]
    survivor_sets: list[Any]
    pool: list[Any]
    rows: list[dict[str, Any]]
    used: list[str]
    warnings: list[str]
    excluded: list[dict[str, Any]]
    target_count: int
    reserve: float


def _repair_target(detail: dict[str, Any], requested: dict[str, Any]) -> str:
    target = str(
        requested.get("repair_portfolio_type")
        or requested.get("portfolio_type")
        or _saved_single_mode(detail)
    ).strip().lower()
    if target not in PORTFOLIO_TYPES:
        raise ValueError("Falta la variante Agresivo, Moderado o Conservador que se quiere arreglar")
    return target


def _repair_inputs(
    source: PortfolioSource,
    requested: dict[str, Any],
    saved: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    inputs = {**requested, **saved, "portfolio_type": target}
    inputs["margin_model"] = build_margin_model(source, inputs)
    return inputs


def _survivor_sets(
    source: PortfolioSource,
    survivors: list[dict[str, Any]],
    progress: Progress | None,
) -> tuple[list[Any], list[str]]:
    if progress:
        progress(f"1/5 · Reconstruyendo {len(survivors)} estrategias no excluidas")
    rows = member_rows(
        survivors,
        resolve_path=lambda value: _resolve_source_path(value, source.project),
        project=source.project,
    )
    strategies, warnings = load_robust_sets_from_rows(rows, [], parse=cached_report)
    if len(strategies) != len(rows):
        raise ValueError(
            "No se pudieron reconstruir todas las estrategias no excluidas; "
            "el arreglo no puede retirar ninguna adicional"
        )
    return strategies, warnings


def _repair_pool(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    survivors: list[Any],
    warnings: list[str],
    progress: Progress | None,
) -> tuple[list[Any], list[dict[str, Any]], list[str]]:
    if progress:
        progress("2/5 · Aplicando el embudo y los filtros guardados")
    rows = _candidate_rows_for_improvement(source, inputs, warnings)
    used = (
        used_paths_for_improvement(source, "full_history", portfolio_id)
        if inputs.get("exclude_used_sets", True) else []
    )
    if progress:
        progress(f"3/5 · Cargando reportes de {len(rows)} candidatos válidos")
    candidates, found = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(found)
    survivor_ids = {strategy.set_id for strategy in survivors}
    candidates = recent_positive_candidates(candidates, survivor_ids)
    by_id = {strategy.set_id: strategy for strategy in candidates}
    by_id.update({strategy.set_id: strategy for strategy in survivors})
    return list(by_id.values()), rows, used


def _repair_context(
    source: PortfolioSource,
    portfolio_id: int,
    requested: dict[str, Any],
    progress: Progress | None,
) -> _RepairContext:
    saved_detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    target = _repair_target(saved_detail, requested)
    variant = (saved_detail.get("metrics") or {}).get("variants", {}).get(target, {})
    selected = _selected_variant_detail(saved_detail, target)
    saved_inputs = variant.get("inputs") or (selected.get("metrics") or {}).get("inputs") or {}
    inputs = _repair_inputs(source, requested, saved_inputs, target)
    members = list(selected.get("members") or [])
    excluded = [member for member in members if member.get("excluded")]
    if not excluded:
        raise ValueError("Este portafolio no tiene estrategias excluidas en la variante mostrada")
    survivors = [member for member in members if not member.get("excluded")]
    survivor_sets, warnings = _survivor_sets(source, survivors, progress)
    pool, rows, used = _repair_pool(
        source, portfolio_id, inputs, survivor_sets, warnings, progress,
    )
    portfolio_type = PORTFOLIO_TYPES[target]
    reserve = _reserve_pct(float(inputs.get("dd_reserve_pct") or 0), portfolio_type)
    return _RepairContext(
        source, portfolio_id, target, selected, inputs, survivor_sets, pool,
        rows, used, warnings, excluded, len(members), reserve,
    )


def _repair_optimizer_kwargs(context: _RepairContext) -> dict[str, Any]:
    survivor_ids = [strategy.set_id for strategy in context.survivor_sets]
    portfolio_type = PORTFOLIO_TYPES[context.target]
    existing = context.source.saved_curves(
        monthly=False, portfolio_type=portfolio_type,
        exclude_portfolio_id=context.portfolio_id,
    )
    kwargs = _optimizer_kwargs(context.inputs, portfolio_type, existing, context.reserve)
    return optimizer_overrides(kwargs, **{
        "required_set_ids": survivor_ids,
        "preserve_required_allocations": False,
        "minimum_active_strategies": context.target_count,
        "maximum_active_strategies": context.target_count,
        "prefer_breadth_below_minimum": True,
        "max_total_candidates": None,
    })


def _optimize_repair(
    context: _RepairContext,
    progress: Progress | None,
) -> tuple[PortfolioResult, list[str]]:
    survivor_ids = {strategy.set_id for strategy in context.survivor_sets}
    banned: set[str] = set()
    rejected: list[str] = []
    minimum_pct = float(context.inputs.get("min_strategy_recent_contribution_pct") or 0)
    for retry in range(MAX_FILLER_RETRIES + 1):
        pool = [strategy for strategy in context.pool if strategy.set_id not in banned]
        if len(pool) < context.target_count:
            raise ValueError("No quedan suficientes candidatas válidas para reconstruir el portafolio")
        if progress:
            progress(
                f"4/5 · Reconstruyendo {context.target_count} estrategias y reajustando lotes"
                + (f" · reintento {retry}" if retry else "")
            )
        kwargs = _repair_optimizer_kwargs(context)
        result = optimize_portfolio(
            raw_sets=pool,
            **{
                **kwargs,
                "search": kwargs["search"].with_deep_refinement(
                    bool(context.inputs.get("deep_optimization"))
                ),
            },
        )
        selected = {row.set_id for row in result.allocations if row.units > 0}
        if not survivor_ids.issubset(selected):
            raise ValueError("El optimizador intentó retirar una estrategia no excluida")
        fillers = _underrepresented_recent_allocation_ids(result, minimum_pct) - survivor_ids
        if not fillers:
            return result, rejected
        rejected.extend(sorted(fillers))
        banned |= fillers
    names = ", ".join(Path(value).name for value in rejected)
    raise ValueError(
        "Las sustitutas no alcanzan el aporte mínimo Final Tick 6M guardado: " + names
    )


def _repair_proposal(
    context: _RepairContext,
    result: PortfolioResult,
    rejected: list[str],
) -> dict[str, Any]:
    if result.active_strategies != context.target_count:
        raise ValueError(
            f"No se pudo reconstruir el tamaño original: quedaron "
            f"{result.active_strategies}/{context.target_count} estrategias"
        )
    excluded_names = [
        Path(str(member.get("set_path") or member.get("set_id") or "")).name
        for member in context.excluded
    ]
    survivor_ids = {strategy.set_id for strategy in context.survivor_sets}
    added = [row.set_id for row in result.allocations if row.units > 0 and row.set_id not in survivor_ids]
    label = f"Arreglo del portafolio #{context.portfolio_id} | modo {TYPE_LABELS[context.target]}"
    audit = {
        "source_portfolio_id": context.portfolio_id,
        "target_portfolio_type": context.target,
        "target_portfolio_type_label": TYPE_LABELS[context.target],
        "excluded_removed": excluded_names,
        "retained_count": len(survivor_ids),
        "added_count": len(added),
        "added_sets": [Path(value).name for value in added],
        "recent_contribution_rejections": [Path(value).name for value in rejected],
        "save_as_new": True,
        "label": label,
    }
    result.seasonal_validation = dict(result.seasonal_validation or {})
    result.seasonal_validation["portfolio_repair"] = audit
    inputs = settings_inputs(context.inputs)
    inputs.update({
        "optimization_profile": context.target,
        "optimization_profile_label": TYPE_LABELS[context.target],
        "portfolio_type": context.target,
        "portfolio_type_label": TYPE_LABELS[context.target],
        "composition_portfolio_type": context.target,
        "composition_portfolio_type_label": TYPE_LABELS[context.target],
        "dd_reserve_pct": context.reserve,
        "repair_source_portfolio_id": context.portfolio_id,
        "repair_portfolio_type": context.target,
        "repair_removed_count": len(excluded_names),
        "repair_added_count": len(added),
        "repair_label": label,
        "portfolio_uid": str(uuid.uuid4()),
        # Compatibilidad con nodos ya desplegados: esta pareja hace que el
        # guardado existente cree una fila individual en vez de un bundle.
        "improvement_source_portfolio_id": context.portfolio_id,
        "improvement_portfolio_type": context.target,
        "improvement_label": label,
    })
    return {
        "key": context.target,
        "label": TYPE_LABELS[context.target],
        "reserve_pct": context.reserve,
        "inputs": inputs,
        "result": result,
    }


def generate_full_history_repair(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Create a new single-mode portfolio without the excluded members."""
    context = _repair_context(source, portfolio_id, inputs, progress)
    result, rejected = _optimize_repair(context, progress)
    if progress:
        progress("5/5 · Verificando el portafolio arreglado")
    _seasonal_coverage(result, context.pool)
    result.warnings[:0] = context.warnings
    proposal = _repair_proposal(context, result, rejected)
    availability = asdict(summarize_robust_rows(context.rows, context.used))
    availability.update({
        "loaded_sets": len(context.pool),
        "warnings": context.warnings,
        "repair": {
            "removed": len(context.excluded),
            "retained": len(context.survivor_sets),
            "target": context.target_count,
        },
    })
    return availability, [proposal]
