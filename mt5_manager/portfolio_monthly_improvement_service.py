from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
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
    original_sets, found = slice_strategy_sets_to_month(
        original_full_sets, target_month,
    )
    warnings.extend(found)
    candidate_sets, found = slice_strategy_sets_to_month(
        list(full_by_id.values()), target_month,
    )
    warnings.extend(found)
    sliced_by_id = {strategy.set_id: strategy for strategy in candidate_sets}
    original_ids_ordered = [strategy.set_id for strategy in original_sets]
    resolve_saved_path = lambda value: _resolve_source_path(value, source.project)
    minimum_target = (
        len(original_ids_ordered) + options.max_additions
        if inputs.get("_improvement_exact_additions")
        else len(original_ids_ordered) + 1
    )
    maximum_target = len(original_ids_ordered) + options.max_additions
    if len(sliced_by_id) < minimum_target:
        raise ValueError(
            "No hay ninguna estrategia mensual nueva y positiva que pueda mejorar la base"
        )

    portfolio_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    reserve = float(inputs.get("dd_reserve_pct") or 0)
    existing = (
        source.saved_curves(monthly=True, exclude_portfolio_id=portfolio_id)
        if inputs.get("corr_with_monthly_portfolios")
        else []
    )
    kwargs = _optimizer_kwargs(inputs, portfolio_type, existing, reserve)
    kwargs.update(
        {
            "required_set_ids": original_ids_ordered,
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
    if progress:
        progress(
            f"5/6 · Buscando hasta {options.max_additions} estrategia(s) con originales bloqueadas"
        )
    selected_base: PortfolioResult = optimize_portfolio(
        raw_sets=list(sliced_by_id.values()),
        use_deep_refinement=False,
        **kwargs,
    )
    selected_ids = [
        allocation.set_id
        for allocation in selected_base.allocations
        if allocation.units > 0
    ]
    if not set(original_ids_ordered).issubset(selected_ids):
        raise ValueError("El selector mensual intentó retirar una estrategia original")
    actual_additions = len(selected_ids) - len(original_ids_ordered)
    if not 1 <= actual_additions <= options.max_additions:
        raise ValueError(
            "No se encontró ninguna incorporación mensual que mejorase la base "
            f"dentro del máximo de {options.max_additions}"
        )
    selected_target = len(selected_ids)
    selected_sets = [sliced_by_id[set_id] for set_id in selected_ids]
    final_kwargs = _optimizer_kwargs(inputs, portfolio_type, existing, reserve)
    final_kwargs.update(
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
    result: PortfolioResult = optimize_portfolio(
        raw_sets=selected_sets,
        use_deep_refinement=bool(inputs.get("deep_optimization")),
        **final_kwargs,
    )
    baseline = evaluate_portfolio(
        original_sets,
        allocation_units(detail, resolve_path=resolve_saved_path),
        result.target_valley_dd,
        result.target_point_dd,
        target_daily_dd=result.target_daily_dd,
        enforce_point_dd=False,
        daily_dd_full_history=bool(inputs.get("daily_dd_full_history")),
    )
    validate_and_attach_improvement_audit(
        result=result,
        baseline=baseline,
        all_sets=selected_sets,
        original_ids=original_ids_ordered,
        options=options,
        inputs=inputs,
        scope="monthly",
    )
    _seasonal_coverage(result, selected_sets)

    if progress:
        progress("6/6 · Validando la mejora mes a mes sobre cinco años")
    active_units = {
        allocation.set_id: allocation.units
        for allocation in result.allocations
        if allocation.units > 0
    }
    validation = validate_strict_monthly_portfolio(
        [full_by_id[set_id] for set_id in active_units if set_id in full_by_id],
        active_units,
        target_month=target_month,
        target_valley_dd=result.target_valley_dd,
        target_point_dd=result.target_point_dd,
        enforce_point_dd=False,
        lookback_years=5,
    )
    if not validation.get("passed"):
        reasons = "; ".join(
            str(item) for item in (validation.get("reasons") or [])[:3]
        )
        raise ValueError(
            "La mejora fue descartada por la validación mensual estricta: " + reasons
        )
    improvement_audit = dict(
        result.seasonal_validation.get("portfolio_improvement") or {}
    )
    result.seasonal_validation = {
        **validation,
        "portfolio_improvement": improvement_audit,
    }
    result.warnings.extend(warnings)
    proposal_inputs = settings_inputs(inputs)
    proposal_inputs.update(
        {
            "optimization_profile": "improve",
            "optimization_profile_label": "Mejorar base guardada",
            "improvement_original_count": len(original_ids_ordered),
            "improvement_added_count": actual_additions,
            "improvement_max_additions": options.max_additions,
        }
    )
    availability = asdict(summarize_robust_rows(rows, used))
    availability.update(
        {
            "loaded_sets": len(sliced_by_id),
            "warnings": warnings,
            "improvement": {
                "originals_locked": len(original_ids_ordered),
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
