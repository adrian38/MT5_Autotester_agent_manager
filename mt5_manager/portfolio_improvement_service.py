from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    evaluate_portfolio,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    optimize_portfolio,
    portfolio_group_key,
    summarize_robust_rows,
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
    LOCKED_VARIANTS,
    PORTFOLIO_TYPES,
    TYPE_LABELS,
    PortfolioSource,
    _is_bundle_portfolio,
    _optimizer_kwargs,
    _resolve_source_path,
    _reserve_pct,
    _seasonal_coverage,
    build_margin_model,
    cached_report,
    settings_inputs,
)


Progress = Callable[[str], None]


def _load_full_history_improvement_pool(
    source: PortfolioSource,
    detail: dict[str, Any],
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None,
) -> tuple[list[Any], list[Any], list[dict[str, Any]], list[str], list[str]]:
    base_key = str(inputs["portfolio_type"])
    originals = unique_original_members(detail, base_key)
    if not originals:
        raise ValueError("El portafolio guardado no contiene una base reconstruible")
    if progress:
        progress(f"1/5 · Reconstruyendo y bloqueando {len(originals)} estrategias originales")
    original_sets, warnings = load_robust_sets_from_rows(
        member_rows(
            originals,
            resolve_path=lambda value: _resolve_source_path(value, source.project),
        ),
        [],
        parse=cached_report,
    )
    if len(original_sets) != len(originals):
        raise ValueError(
            "No se pudieron reconstruir todas las estrategias originales; "
            "la mejora no puede retirar ninguna sin evidencia"
        )

    if progress:
        progress("2/5 · Aplicando el embudo de cuatro etapas y los filtros guardados")
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
    options = improvement_options(inputs)
    used = (
        used_paths_for_improvement(source, "full_history", portfolio_id)
        if options.exclude_used_sets
        else []
    )
    if progress:
        progress(f"3/5 · Cargando reportes de {len(rows)} candidatos nuevos")
    candidate_sets, found = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(found)
    original_ids = {strategy.set_id for strategy in original_sets}
    candidate_sets = recent_positive_candidates(candidate_sets, original_ids)
    by_id = {strategy.set_id: strategy for strategy in candidate_sets}
    by_id.update({strategy.set_id: strategy for strategy in original_sets})
    return original_sets, list(by_id.values()), rows, used, warnings


def _generate_full_history_improvement_attempt(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Add strategies to a saved A/M/C base without ever removing an original."""
    options = improvement_options(inputs)
    inputs = {
        **inputs,
        "use_correlation": True,
        "margin_model": build_margin_model(source, inputs),
    }
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    if not _is_bundle_portfolio(detail):
        raise ValueError("Mejorar la base UBS requiere un portafolio A/M/C guardado")
    original_sets, raw_sets, rows, used, warnings = _load_full_history_improvement_pool(
        source, detail, portfolio_id, inputs, progress,
    )
    original_ids = [strategy.set_id for strategy in original_sets]
    resolve_saved_path = lambda value: _resolve_source_path(value, source.project)
    minimum_target = (
        len(original_ids) + options.max_additions
        if inputs.get("_improvement_exact_additions")
        else len(original_ids) + 1
    )
    maximum_target = len(original_ids) + options.max_additions
    if len(raw_sets) < minimum_target:
        raise ValueError(
            "No hay ninguna estrategia nueva con aporte Final Tick 6M positivo "
            "que pueda mejorar la base"
        )

    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
    selection_reserve = max(
        _reserve_pct(configured_reserve, portfolio_type)
        for _key, _label, portfolio_type in LOCKED_VARIANTS
    )
    existing = source.saved_curves(
        monthly=False,
        portfolio_type=base_type,
        exclude_portfolio_id=portfolio_id,
    )
    selector_kwargs = _optimizer_kwargs(inputs, base_type, existing, selection_reserve)
    selector_kwargs.update(
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
            # This pass chooses only the composition. Local/deep searches can
            # activate extra sets after the greedy maximum; lot refinement is
            # performed below once the selected composition is fixed.
            "run_local_search": False,
            "search_restarts": 0,
        }
    )
    if progress:
        progress(
            f"4/5 · Buscando hasta {options.max_additions} incorporación(es) con baja dependencia"
        )
    selected_base = optimize_portfolio(
        raw_sets=raw_sets,
        use_deep_refinement=False,
        **selector_kwargs,
    )
    selected_ids = [
        allocation.set_id
        for allocation in selected_base.allocations
        if allocation.units > 0
    ]
    if not set(original_ids).issubset(selected_ids):
        raise ValueError("El selector intentó retirar una estrategia original")
    actual_additions = len(selected_ids) - len(original_ids)
    if not 1 <= actual_additions <= options.max_additions:
        raise ValueError(
            "No se encontró ninguna incorporación que mejorase la base dentro "
            f"del máximo de {options.max_additions}"
        )
    selected_target = len(selected_ids)
    raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
    selected_sets = [raw_by_id[set_id] for set_id in selected_ids]

    if progress:
        progress("5/5 · Validando beneficio/DD y recalculando las variantes A/M/C")
    proposals: list[dict[str, Any]] = []
    for key, label, portfolio_type in LOCKED_VARIANTS:
        reserve = _reserve_pct(configured_reserve, portfolio_type)
        variant_existing = source.saved_curves(
            monthly=False,
            portfolio_type=portfolio_type,
            exclude_portfolio_id=portfolio_id,
        )
        kwargs = _optimizer_kwargs(inputs, portfolio_type, variant_existing, reserve)
        kwargs.update(
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
            **kwargs,
        )
        baseline = evaluate_portfolio(
            original_sets,
            allocation_units(detail, key, resolve_path=resolve_saved_path),
            result.target_valley_dd,
            result.target_point_dd,
            enforce_point_dd=False,
        )
        validate_and_attach_improvement_audit(
            result=result,
            baseline=baseline,
            all_sets=selected_sets,
            original_ids=original_ids,
            options=options,
            inputs=inputs,
            scope="full_history",
            minimum_gain_pct=(
                options.min_efficiency_gain_pct
                if portfolio_type == base_type
                else -1.0
            ),
        )
        _seasonal_coverage(result, selected_sets)
        result.warnings.extend(warnings)
        proposal_inputs = settings_inputs(inputs)
        proposal_inputs.update(
            {
                "optimization_profile": key,
                "optimization_profile_label": label,
                "portfolio_type": portfolio_type.value,
                "portfolio_type_label": TYPE_LABELS[portfolio_type.value],
                "composition_portfolio_type": base_type.value,
                "composition_portfolio_type_label": TYPE_LABELS[base_type.value],
                "dd_reserve_pct": reserve,
                "improvement_original_count": len(original_ids),
                "improvement_added_count": actual_additions,
                "improvement_max_additions": options.max_additions,
            }
        )
        proposals.append(
            {
                "key": key,
                "label": label,
                "reserve_pct": reserve,
                "inputs": proposal_inputs,
                "result": result,
            }
        )

    availability = asdict(summarize_robust_rows(rows, used))
    availability.update(
        {
            "loaded_sets": len(raw_sets),
            "warnings": warnings,
            "improvement": {
                "originals_locked": len(original_ids),
                "maximum_additions": options.max_additions,
                "actual_additions": actual_additions,
                "selected_set_names": [Path(value).name for value in selected_ids],
            },
        }
    )
    return availability, proposals


def generate_full_history_improvement(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Try the requested maximum first, then fewer additions without using fillers."""
    requested = improvement_options(inputs).max_additions
    failures: list[str] = []
    for additions in range(requested, 0, -1):
        if progress:
            progress(
                f"Mejora controlada · probando {additions} incorporación(es) "
                f"del máximo {requested}"
            )
        attempt_inputs = {
            **inputs,
            "improvement_additions": additions,
            "_improvement_exact_additions": True,
        }
        try:
            availability, proposals = _generate_full_history_improvement_attempt(
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
        "No se encontró una mejora válida entre una estrategia y el máximo "
        f"de {requested}. Último intento: {detail}"
    )
