from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    optimize_portfolio,
    optimizer_overrides,
    portfolio_group_key,
    summarize_robust_rows,
)

from .portfolio_generation import build_margin_model
from .portfolio_generation_search import _optimizer_kwargs, _seasonal_coverage
from .portfolio_identity import (
    PORTFOLIO_TYPES,
    _allocation_source_rows,
    _is_bundle_portfolio,
)
from .portfolio_persistence import settings_inputs
from .portfolio_report_cache import cached_report
from .portfolio_settings import ASSET_GROUPS

if TYPE_CHECKING:
    from .portfolio_service import PortfolioSource


def _completion_required_sets(
    members: list[dict[str, Any]], progress: Callable[[str], None] | None,
) -> tuple[list[Any], list[str]]:
    """Reconstruye las estrategias que el portafolio ya tiene y debe conservar."""
    if progress:
        progress(f"1/3 · Reconstruyendo {len(members)} estrategias que deben conservarse")
    required_rows = _allocation_source_rows(members)
    required_sets, required_warnings = load_robust_sets_from_rows(
        required_rows, [], parse=cached_report,
    )
    if len(required_sets) != len(required_rows):
        raise ValueError("No se pudieron reconstruir todas las estrategias que deben conservarse")
    return required_sets, required_warnings


def _completion_filtered_rows(
    source: PortfolioSource,
    inputs: dict[str, Any],
    rows: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """El pool sobre el que buscar la sustituta.

    No es el mismo filtrado que `_eligible_generation_rows`: completar no aplica
    los simbolos deshabilitados, acepta que el pool quede vacio —lo dira despues
    el conteo de estrategias activas— y cae a todos los grupos si no hay
    seleccion. Unificarlos cambiaria que portafolios se pueden completar.
    """
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows, min_positive_months=3, window_months=6, parse=cached_report,
        )
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(inputs.get("allowed_asset_groups") or ASSET_GROUPS)
    return [
        row for row in rows
        if portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        ) in allowed
    ]


def _completion_optimizer_kwargs(
    source: PortfolioSource,
    inputs: dict[str, Any],
    portfolio_id: int,
    portfolio_type: PortfolioType,
    members: list[dict[str, Any]],
    required_sets: list[Any],
    target: int,
) -> tuple[dict[str, Any], float]:
    """Los topes del optimizador con la composicion actual clavada dentro."""
    required_ids = [strategy.set_id for strategy in required_sets]
    saved_units = {
        str(item.get("set_path") or item.get("set_id") or ""): int(item.get("units") or 0)
        for item in members
    }
    initial = {strategy.set_id: saved_units.get(strategy.set_id, 0) for strategy in required_sets}
    reserve = float(inputs.get("dd_reserve_pct") or 0)
    existing = source.saved_curves(
        monthly=False,
        portfolio_type=portfolio_type,
        exclude_portfolio_id=portfolio_id,
    )
    kwargs = _optimizer_kwargs(inputs, portfolio_type, existing, reserve)
    return optimizer_overrides(kwargs, **{
        "required_set_ids": required_ids,
        "minimum_active_strategies": target,
        "maximum_active_strategies": target,
        "required_initial_allocations": initial,
        "preserve_required_allocations": True,
    }), reserve


def _completion_proposal(
    result: PortfolioResult, inputs: dict[str, Any], reserve: float,
) -> dict[str, Any]:
    proposal_inputs = settings_inputs(inputs)
    proposal_inputs.update({
        "optimization_profile": "complete",
        "optimization_profile_label": "Completar portafolio",
    })
    return {
        "key": "complete",
        "label": "Completar portafolio",
        "reserve_pct": reserve,
        "inputs": proposal_inputs,
        "result": result,
    }


def generate_completion_proposal(
    source: PortfolioSource,
    portfolio_id: int,
    scope: str,
    inputs: dict[str, Any],
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Complete only a full-history UBS portfolio."""
    if scope != "full_history":
        raise ValueError("El portafolio mensual debe usar portfolio_monthly_service")
    inputs = {**inputs, "margin_model": build_margin_model(source, inputs)}
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    if _is_bundle_portfolio(detail):
        raise ValueError("El portafolio A/M/C debe reoptimizarse completo; no admite completar una sola variante")
    members = list(detail.get("members") or [])
    target = max(int(detail.get("target_strategies") or 0), int(detail.get("active_strategies") or 0))
    if target <= len(members):
        raise ValueError("El portafolio ya tiene todas sus estrategias")
    required_sets, required_warnings = _completion_required_sets(members, progress)
    rows = source.candidate_rows(include_quarantined=False)
    warnings = list(required_warnings)
    rows = _completion_filtered_rows(source, inputs, rows, warnings)
    portfolio_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    used = (
        source.used_set_paths(
            "full_history",
            exclude_portfolio_id=portfolio_id,
            portfolio_type=portfolio_type,
        )
        if inputs.get("exclude_used_sets", True) else []
    )
    if progress:
        progress(f"2/3 · Cargando reportes de {len(rows)} candidatos")
    candidate_sets, load_warnings = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(load_warnings)
    by_id = {strategy.set_id: strategy for strategy in candidate_sets}
    by_id.update({strategy.set_id: strategy for strategy in required_sets})
    raw_sets = list(by_id.values())
    kwargs, reserve = _completion_optimizer_kwargs(
        source, inputs, portfolio_id, portfolio_type, members, required_sets, target,
    )
    if progress:
        progress(f"3/3 · Buscando sustituta para completar {len(members)}/{target}")
    result = optimize_portfolio(
        raw_sets=raw_sets,
        **{**kwargs, "search": kwargs["search"].with_deep_refinement(bool(inputs.get("deep_optimization")))},
    )
    _seasonal_coverage(result, raw_sets)
    result.warnings[:0] = warnings
    if result.active_strategies < target:
        raise ValueError(
            f"No existe una sustituta compatible: quedaron {result.active_strategies}/{target} estrategias"
        )
    proposal = _completion_proposal(result, inputs, reserve)
    availability = asdict(summarize_robust_rows(rows, used))
    availability.update({"loaded_sets": len(raw_sets), "warnings": warnings})
    return availability, [proposal]
