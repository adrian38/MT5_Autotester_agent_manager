from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    optimizer_overrides,
    PortfolioResult,
    evaluate_portfolio,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    optimize_portfolio,
    portfolio_group_key,
    slice_strategy_sets_to_month,
    summarize_robust_rows,
    validate_strict_monthly_portfolio,
)

from .portfolio_improvement_common import (
    allocation_units,
    improvement_options,
    member_rows,
    recent_positive_candidates,
    unique_original_members,
    used_paths_for_improvement,
    validate_and_attach_improvement_audit,
)
from .portfolio_service import (
    ASSET_GROUPS,
    PORTFOLIO_TYPES,
    PortfolioSource,
    _optimizer_kwargs,
    _resolve_source_path,
    _seasonal_coverage,
    build_margin_model,
    cached_report,
    settings_inputs,
)


Progress = Callable[[str], None]


@dataclass(frozen=True)
class _MonthlyImprovementPool:
    """El pool de una mejora mensual: originales bloqueadas y candidatas nuevas.

    ``sliced_by_id`` esta recortado al mes objetivo y es sobre lo que se elige;
    ``full_by_id`` conserva la curva entera, que es lo que necesita la
    validacion estricta de cinco años.
    """

    original_ids: list[str]
    original_sets: list[Any]
    sliced_by_id: dict[str, Any]
    full_by_id: dict[str, Any]
    rows: list[dict[str, Any]]
    used: list[str]


def _locked_original_sets(
    source: PortfolioSource, detail: dict[str, Any], progress: Progress | None,
) -> tuple[list[Any], list[str]]:
    """Las estrategias originales, reconstruidas enteras. Si falta una, se para.

    La mejora mensual no retira ninguna automaticamente: entregar una base
    incompleta seria cambiar la cartera guardada sin decirlo.
    """
    originals = unique_original_members(detail)
    if not originals:
        raise ValueError("El portafolio mensual no contiene una base reconstruible")
    if progress:
        progress(f"1/6 · Reconstruyendo y bloqueando {len(originals)} estrategias originales")
    original_full_sets, warnings = load_robust_sets_from_rows(
        member_rows(
            originals,
            resolve_path=lambda value: _resolve_source_path(value, source.project),
        ),
        [],
        parse=cached_report,
    )
    if len(original_full_sets) != len(originals):
        raise ValueError(
            "No se pudieron reconstruir todas las estrategias originales; "
            "la mejora mensual no retirará ninguna automáticamente"
        )
    return original_full_sets, warnings


def _monthly_improvement_rows(
    source: PortfolioSource,
    inputs: dict[str, Any],
    options: Any,
    portfolio_id: int,
    warnings: list[str],
    progress: Progress | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Las candidatas nuevas tras el embudo y los filtros mensuales."""
    if progress:
        progress("2/6 · Aplicando el embudo de cuatro etapas y los filtros mensuales")
    rows = source.candidate_rows(include_quarantined=False)
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows, min_positive_months=3, window_months=6, parse=cached_report,
        )
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(inputs.get("allowed_asset_groups") or ASSET_GROUPS)
    rows = [
        row
        for row in rows
        if portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        )
        in allowed
    ]
    used = (
        used_paths_for_improvement(source, "monthly", portfolio_id)
        if options.exclude_used_sets
        else []
    )
    return rows, used


def _monthly_improvement_pool(
    source: PortfolioSource,
    inputs: dict[str, Any],
    options: Any,
    portfolio_id: int,
    detail: dict[str, Any],
    warnings: list[str],
    progress: Progress | None,
) -> _MonthlyImprovementPool:
    """Reconstruye originales y candidatas, y recorta ambas al mes objetivo."""
    original_full_sets, found = _locked_original_sets(source, detail, progress)
    warnings.extend(found)
    rows, used = _monthly_improvement_rows(
        source, inputs, options, portfolio_id, warnings, progress
    )
    if progress:
        progress(f"3/6 · Cargando reportes de {len(rows)} candidatos nuevos")
    candidate_full_sets, found = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(found)
    original_ids = {strategy.set_id for strategy in original_full_sets}
    candidate_full_sets = recent_positive_candidates(candidate_full_sets, original_ids)
    full_by_id = {strategy.set_id: strategy for strategy in candidate_full_sets}
    full_by_id.update({strategy.set_id: strategy for strategy in original_full_sets})

    target_month = int(inputs["target_month"])
    if progress:
        progress(f"4/6 · Recortando curvas al mes {target_month:02d} sin perder auditoría de riesgo")
    original_sets, found = slice_strategy_sets_to_month(original_full_sets, target_month)
    warnings.extend(found)
    candidate_sets, found = slice_strategy_sets_to_month(
        list(full_by_id.values()), target_month,
    )
    warnings.extend(found)
    return _MonthlyImprovementPool(
        original_ids=[strategy.set_id for strategy in original_sets],
        original_sets=original_sets,
        sliced_by_id={strategy.set_id: strategy for strategy in candidate_sets},
        full_by_id=full_by_id,
        rows=rows,
        used=used,
    )


def _monthly_selection_kwargs(
    inputs: dict[str, Any],
    options: Any,
    base_kwargs: dict[str, Any],
    original_ids: list[str],
    minimum_target: int,
    maximum_target: int,
) -> dict[str, Any]:
    """Los topes de la busqueda que elige que se incorpora, con las originales fijas."""
    return optimizer_overrides(base_kwargs, **
        {
            "required_set_ids": original_ids,
            "preserve_required_allocations": False,
            "minimum_active_strategies": minimum_target,
            "maximum_active_strategies": maximum_target,
            "top_k_per_symbol": max(int(inputs["top_k_per_symbol"]), maximum_target),
            "max_sets_per_symbol": (
                maximum_target
                if options.allow_same_symbol
                else int(inputs["max_sets_per_symbol"])
            ),
            "run_local_search": False,
            "search_restarts": 0,
        }
    )


def _monthly_final_kwargs(
    inputs: dict[str, Any],
    options: Any,
    base_kwargs: dict[str, Any],
    selected_ids: list[str],
) -> dict[str, Any]:
    """Los topes del reparto final sobre la composicion ya elegida."""
    selected_target = len(selected_ids)
    return optimizer_overrides(base_kwargs, **
        {
            "required_set_ids": selected_ids,
            "preserve_required_allocations": False,
            "minimum_active_strategies": selected_target,
            "maximum_active_strategies": selected_target,
            "top_k_per_symbol": selected_target,
            "max_total_candidates": None,
            "max_sets_per_symbol": (
                selected_target
                if options.allow_same_symbol
                else int(inputs["max_sets_per_symbol"])
            ),
            "max_sets_per_group": selected_target,
            "group_unit_cap_bootstrap": max(selected_target, 1),
        }
    )


def _validated_monthly_improvement(
    result: PortfolioResult,
    pool: _MonthlyImprovementPool,
    target_month: int,
    progress: Progress | None,
) -> dict[str, int]:
    """Valida la mejora mes a mes sobre cinco años; devuelve las unidades activas.

    Se valida con la curva entera, no con la recortada: la estacionalidad de
    cinco años no se puede leer en un solo mes.
    """
    if progress:
        progress("6/6 · Validando la mejora mes a mes sobre cinco años")
    active_units = {
        allocation.set_id: allocation.units
        for allocation in result.allocations
        if allocation.units > 0
    }
    validation = validate_strict_monthly_portfolio(
        [pool.full_by_id[set_id] for set_id in active_units if set_id in pool.full_by_id],
        active_units,
        target_month=target_month,
        target_valley_dd=result.target_valley_dd,
        target_point_dd=result.target_point_dd,
        enforce_point_dd=False,
        lookback_years=5,
    )
    if not validation.get("passed"):
        reasons = "; ".join(str(item) for item in (validation.get("reasons") or [])[:3])
        raise ValueError(
            "La mejora fue descartada por la validación mensual estricta: " + reasons
        )
    result.seasonal_validation = {
        **validation,
        "portfolio_improvement": dict(
            result.seasonal_validation.get("portfolio_improvement") or {}
        ),
    }
    return active_units


def _monthly_improvement_proposal(
    result: PortfolioResult,
    inputs: dict[str, Any],
    options: Any,
    pool: _MonthlyImprovementPool,
    reserve: float,
    actual_additions: int,
    active_units: dict[str, int],
    warnings: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    proposal_inputs = settings_inputs(inputs)
    proposal_inputs.update(
        {
            "optimization_profile": "improve",
            "optimization_profile_label": "Mejorar base guardada",
            "improvement_original_count": len(pool.original_ids),
            "improvement_added_count": actual_additions,
            "improvement_max_additions": options.max_additions,
        }
    )
    availability = asdict(summarize_robust_rows(pool.rows, pool.used))
    availability.update(
        {
            "loaded_sets": len(pool.sliced_by_id),
            "warnings": warnings,
            "improvement": {
                "originals_locked": len(pool.original_ids),
                "maximum_additions": options.max_additions,
                "actual_additions": actual_additions,
                "selected_set_names": [Path(value).name for value in active_units],
            },
        }
    )
    return availability, [
        {
            "key": "improve",
            "label": "Mejorar base guardada",
            "reserve_pct": reserve,
            "inputs": proposal_inputs,
            "result": result,
        }
    ]


def _monthly_addition_targets(
    inputs: dict[str, Any], options: Any, pool: _MonthlyImprovementPool,
) -> tuple[int, int]:
    """Cuantas estrategias debe tener la mejora, como minimo y como maximo."""
    minimum_target = (
        len(pool.original_ids) + options.max_additions
        if inputs.get("_improvement_exact_additions")
        else len(pool.original_ids) + 1
    )
    if len(pool.sliced_by_id) < minimum_target:
        raise ValueError(
            "No hay ninguna estrategia mensual nueva y positiva que pueda mejorar la base"
        )
    return minimum_target, len(pool.original_ids) + options.max_additions


def _monthly_existing_curves(
    source: PortfolioSource, inputs: dict[str, Any], portfolio_id: int,
) -> list[Any]:
    """Las curvas mensuales ya guardadas, solo si se pide correlacionar con ellas."""
    if not inputs.get("corr_with_monthly_portfolios"):
        return []
    return source.saved_curves(monthly=True, exclude_portfolio_id=portfolio_id)


def _monthly_improvement_baseline(
    source: PortfolioSource,
    inputs: dict[str, Any],
    detail: dict[str, Any],
    pool: _MonthlyImprovementPool,
    result: PortfolioResult,
) -> Any:
    """La cartera original medida con los mismos limites que la mejorada."""
    return evaluate_portfolio(
        pool.original_sets,
        allocation_units(
            detail, resolve_path=lambda value: _resolve_source_path(value, source.project)
        ),
        result.target_valley_dd,
        result.target_point_dd,
        target_daily_dd=result.target_daily_dd,
        enforce_point_dd=False,
        daily_dd_full_history=bool(inputs.get("daily_dd_full_history")),
    )


def _selected_monthly_ids(
    inputs: dict[str, Any],
    options: Any,
    pool: _MonthlyImprovementPool,
    base_kwargs: dict[str, Any],
    minimum_target: int,
    maximum_target: int,
    progress: Progress | None,
) -> list[str]:
    """Que se incorpora, con las originales bloqueadas y dentro del maximo."""
    kwargs = _monthly_selection_kwargs(
        inputs, options, base_kwargs, pool.original_ids, minimum_target, maximum_target,
    )
    if progress:
        progress(
            f"5/6 · Buscando hasta {options.max_additions} estrategia(s) con originales bloqueadas"
        )
    selected_base: PortfolioResult = optimize_portfolio(
        raw_sets=list(pool.sliced_by_id.values()),
        **{**kwargs, "search": kwargs["search"].with_deep_refinement(False)},
    )
    selected_ids = [
        allocation.set_id
        for allocation in selected_base.allocations
        if allocation.units > 0
    ]
    if not set(pool.original_ids).issubset(selected_ids):
        raise ValueError("El selector mensual intentó retirar una estrategia original")
    actual_additions = len(selected_ids) - len(pool.original_ids)
    if not 1 <= actual_additions <= options.max_additions:
        raise ValueError(
            "No se encontró ninguna incorporación mensual que mejorase la base "
            f"dentro del máximo de {options.max_additions}"
        )
    return selected_ids


def _generate_monthly_improvement_attempt(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Improve one saved month while locking every original strategy."""
    options = improvement_options(inputs)
    inputs = {
        **inputs,
        "use_correlation": True,
        "margin_model": build_margin_model(source, inputs),
    }
    detail = source.saved_portfolio_detail(portfolio_id, "monthly")["portfolio"]
    warnings: list[str] = []
    pool = _monthly_improvement_pool(
        source, inputs, options, portfolio_id, detail, warnings, progress
    )
    minimum_target, maximum_target = _monthly_addition_targets(inputs, options, pool)
    portfolio_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    reserve = float(inputs.get("dd_reserve_pct") or 0)
    existing = _monthly_existing_curves(source, inputs, portfolio_id)
    selected_ids = _selected_monthly_ids(
        inputs, options, pool,
        _optimizer_kwargs(inputs, portfolio_type, existing, reserve),
        minimum_target, maximum_target, progress,
    )
    actual_additions = len(selected_ids) - len(pool.original_ids)
    selected_sets = [pool.sliced_by_id[set_id] for set_id in selected_ids]
    final_kwargs = _monthly_final_kwargs(
        inputs, options, _optimizer_kwargs(inputs, portfolio_type, existing, reserve),
        selected_ids,
    )
    result: PortfolioResult = optimize_portfolio(
        raw_sets=selected_sets,
        **{**final_kwargs, "search": final_kwargs["search"].with_deep_refinement(bool(inputs.get("deep_optimization")))},
    )
    validate_and_attach_improvement_audit(
        result=result,
        baseline=_monthly_improvement_baseline(source, inputs, detail, pool, result),
        all_sets=selected_sets,
        original_ids=pool.original_ids,
        options=options,
        inputs=inputs,
        scope="monthly",
    )
    _seasonal_coverage(result, selected_sets)
    active_units = _validated_monthly_improvement(
        result, pool, int(inputs["target_month"]), progress
    )
    result.warnings.extend(warnings)
    return _monthly_improvement_proposal(
        result, inputs, options, pool, reserve, actual_additions, active_units, warnings
    )


def generate_monthly_improvement(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Try the requested monthly maximum first and fall back to fewer additions."""
    requested = improvement_options(inputs).max_additions
    failures: list[str] = []
    for additions in range(requested, 0, -1):
        if progress:
            progress(
                f"Mejora mensual · probando {additions} incorporación(es) "
                f"del máximo {requested}"
            )
        attempt_inputs = {
            **inputs,
            "improvement_additions": additions,
            "_improvement_exact_additions": True,
        }
        try:
            availability, proposals = _generate_monthly_improvement_attempt(
                source, portfolio_id, attempt_inputs, progress,
            )
        except ValueError as exc:
            failures.append(f"{additions}: {exc}")
            continue
        improvement = availability.setdefault("improvement", {})
        improvement["maximum_additions"] = requested
        improvement["actual_additions"] = additions
        for proposal in proposals:
            proposal.setdefault("inputs", {})["improvement_max_additions"] = requested
            audit = (proposal["result"].seasonal_validation or {}).get(
                "portfolio_improvement"
            )
            if isinstance(audit, dict):
                audit["maximum_additions"] = requested
        return availability, proposals
    detail = failures[-1] if failures else "sin candidatas válidas"
    raise ValueError(
        "No se encontró una mejora mensual válida entre una estrategia y el máximo "
        f"de {requested}. Último intento: {detail}"
    )
