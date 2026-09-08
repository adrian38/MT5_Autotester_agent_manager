from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    BootstrapDrawdownAnalysis,
    PortfolioEvaluation,
    PortfolioResult,
    PortfolioType,
    bootstrap_valley_drawdown,
    evaluate_portfolio,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    optimize_portfolio,
    portfolio_group_key,
    summarize_robust_rows,
)

from .portfolio_improvement_common import (
    allocation_units,
    improvement_options as _shared_improvement_options,
    member_rows,
    recent_positive_candidates,
    unique_original_members,
    used_paths_for_improvement,
    validate_and_attach_improvement_audit,
)
from .portfolio_service import (
    ASSET_GROUPS,
    PORTFOLIO_TYPES,
    TYPE_LABELS,
    PortfolioSource,
    _is_bundle_portfolio,
    _optimizer_kwargs,
    _resolve_source_path,
    _reserve_pct,
    _seasonal_coverage,
    _underrepresented_recent_allocation_ids,
    build_margin_model,
    cached_report,
    settings_inputs,
)


Progress = Callable[[str], None]
MAX_IMPROVEMENT_ADDITIONS = 5
IMPROVEMENT_SELECTION_PRIORITIES = {"balanced", "efficiency", "stress"}


def minimum_additions(inputs: dict[str, Any]) -> int:
    # Keep the old request key usable for an already-open normal UBS page.
    value = inputs.get("improvement_min_additions", inputs.get("improvement_additions", 2))
    try:
        count = int(value)
        valid = not isinstance(value, bool) and float(value) == count
    except (TypeError, ValueError, OverflowError):
        valid, count = False, 0
    if not valid or not 1 <= count <= MAX_IMPROVEMENT_ADDITIONS:
        raise ValueError("El mínimo de estrategias a añadir debe ser un entero entre 1 y 5")
    return count


def improvement_options(inputs: dict[str, Any]):
    options = _shared_improvement_options(inputs)
    if inputs.get("improvement_min_efficiency_gain_pct") == 0:
        options = replace(options, min_efficiency_gain_pct=0.0)
    return options


def improvement_selection_priority(inputs: dict[str, Any]) -> str:
    value = str(inputs.get("improvement_selection_priority") or "balanced").strip().lower()
    if value not in IMPROVEMENT_SELECTION_PRIORITIES:
        raise ValueError(
            "La prioridad de mejora debe ser equilibrada, máxima eficiencia o menor estrés"
        )
    return value


def _attach_stress_comparison(
    *,
    result: PortfolioResult,
    baseline: PortfolioEvaluation,
    priority: str,
    baseline_stress: BootstrapDrawdownAnalysis | None = None,
) -> BootstrapDrawdownAnalysis | None:
    """Record a like-for-like bootstrap comparison without changing validity."""
    audit = (result.seasonal_validation or {}).get("portfolio_improvement")
    if not isinstance(audit, dict):
        return baseline_stress
    improved = result.stress_bootstrap
    curve = getattr(baseline, "equity_curve_2020_2026", None)
    if improved is None or not curve:
        audit["selection_priority"] = priority
        audit["stress_comparison"] = {
            "status": "unavailable",
            "selection_priority": priority,
        }
        return baseline_stress
    if baseline_stress is None:
        baseline_stress = bootstrap_valley_drawdown(
            curve,
            nominal_valley_dd_limit=improved.nominal_valley_dd_limit,
            effective_valley_dd_limit=improved.effective_valley_dd_limit,
            simulations=improved.simulations,
            block_size=improved.block_size,
            seed=improved.seed,
        )
    p95_delta = improved.valley_dd_p95 - baseline_stress.valley_dd_p95
    probability_delta = (
        improved.probability_exceed_effective_pct
        - baseline_stress.probability_exceed_effective_pct
    )
    if probability_delta > 1e-9:
        direction = "higher"
    elif probability_delta < -1e-9:
        direction = "lower"
    else:
        direction = "unchanged"
    audit["selection_priority"] = priority
    audit["stress_comparison"] = {
        "status": "completed",
        "selection_priority": priority,
        "direction": direction,
        "baseline": asdict(baseline_stress),
        "improved": asdict(improved),
        "valley_dd_p95_delta": round(float(p95_delta), 6),
        "valley_dd_p95_delta_pct": (
            round(float(p95_delta / baseline_stress.valley_dd_p95 * 100.0), 6)
            if baseline_stress.valley_dd_p95 > 0
            else None
        ),
        "probability_exceed_effective_delta_pp": round(float(probability_delta), 6),
    }
    result.warnings.insert(
        1,
        "Comparativa de estrés frente a la base: "
        f"P95 {baseline_stress.valley_dd_p95:.2f} -> {improved.valley_dd_p95:.2f}; "
        "probabilidad de exceder el DD efectivo "
        f"{baseline_stress.probability_exceed_effective_pct:.1f}% -> "
        f"{improved.probability_exceed_effective_pct:.1f}%. "
        "Dato informativo; la validez sigue determinada por los límites declarados.",
    )
    return baseline_stress


def _improvement_rank(
    proposal: dict[str, Any], additions: int, priority: str
) -> tuple[float, ...]:
    audit = (proposal["result"].seasonal_validation or {}).get(
        "portfolio_improvement", {}
    )
    gain = float(audit.get("efficiency_gain_pct", 0))
    comparison = audit.get("stress_comparison") or {}
    if comparison.get("status") != "completed":
        return (gain, -float(additions))
    improved = comparison.get("improved") or {}
    probability = float(improved.get("probability_exceed_effective_pct") or 0)
    p95 = float(improved.get("valley_dd_p95") or 0)
    delta = float(comparison.get("probability_exceed_effective_delta_pp") or 0)
    if priority == "efficiency":
        return (gain, -probability, -p95, -float(additions))
    if priority == "stress":
        return (-probability, -p95, gain, -float(additions))
    # Balanced is a preference, not a hidden risk limit: first prefer a result
    # whose estimated exceedance probability does not rise; if none exists,
    # choose the smallest increase. Historical efficiency breaks ties.
    if delta <= 1e-9:
        return (1.0, gain, -probability, -float(additions))
    return (0.0, -delta, gain, -float(additions))


def _selected_variant_detail(detail: dict[str, Any], target: str) -> dict[str, Any]:
    members = list(detail.get("members") or [])
    if _is_bundle_portfolio(detail):
        members = [row for row in members if row.get("variant_key") == target]
    elif str(detail.get("portfolio_type") or "") != target:
        raise ValueError("El portafolio no contiene la variante elegida")
    if not members:
        raise ValueError("No hay estrategias guardadas para la variante elegida")
    return {**detail, "members": members}


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
    """Improve only the selected saved variant and propose a new portfolio."""
    target = str(inputs["portfolio_type"])
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    variant = (detail.get("metrics") or {}).get("variants", {}).get(target, {})
    saved_inputs = variant.get("inputs") or {}
    inputs = {
        **inputs,
        **saved_inputs,
        **{key: value for key, value in inputs.items() if key.startswith("improvement_") or key.startswith("_improvement_")},
        "portfolio_type": target,
        "use_correlation": True,
    }
    inputs["margin_model"] = build_margin_model(source, inputs)
    options = improvement_options(inputs)
    detail = _selected_variant_detail(detail, target)
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
            f"Solo hay {len(raw_sets) - len(original_ids)} candidatas nuevas con aporte "
            f"Final Tick 6M positivo; se necesitan {minimum_target - len(original_ids)}"
        )

    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
    selection_reserve = float(saved_inputs["dd_reserve_pct"]) if "dd_reserve_pct" in saved_inputs else _reserve_pct(configured_reserve, base_type)
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
            f"4/5 · Buscando {options.max_additions} incorporación(es) con baja dependencia"
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
    if not minimum_target <= len(selected_ids) <= maximum_target:
        raise ValueError(
            f"El selector añadió {actual_additions} estrategias; este intento requiere "
            f"{minimum_target - len(original_ids)}"
        )
    selected_target = len(selected_ids)
    raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
    selected_sets = [raw_by_id[set_id] for set_id in selected_ids]

    if progress:
        progress("5/5 · Validando beneficio/DD de la variante elegida")
    proposals: list[dict[str, Any]] = []
    key, label, portfolio_type = target, TYPE_LABELS[target], base_type
    reserve = selection_reserve
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
    fillers = _underrepresented_recent_allocation_ids(
        result, float(inputs.get("min_strategy_recent_contribution_pct") or 0),
    ) - set(original_ids)
    if fillers:
        raise ValueError(
            "Las incorporaciones no alcanzan el aporte mínimo Final Tick 6M: "
            + ", ".join(Path(value).name for value in sorted(fillers))
        )
    baseline = evaluate_portfolio(
        original_sets,
        allocation_units(detail, key, resolve_path=resolve_saved_path),
        result.target_valley_dd,
        result.target_point_dd,
        enforce_point_dd=False,
    )
    audit = validate_and_attach_improvement_audit(
        result=result,
        baseline=baseline,
        all_sets=selected_sets,
        original_ids=original_ids,
        options=options,
        inputs=inputs,
        scope="full_history",
        minimum_gain_pct=options.min_efficiency_gain_pct,
    )
    if audit["added_count"] != actual_additions:
        raise ValueError("El ajuste final no conservó las incorporaciones seleccionadas")
    audit["target_portfolio_type"] = base_type.value
    audit["target_portfolio_type_label"] = TYPE_LABELS[base_type.value]
    audit["source_portfolio_id"] = portfolio_id
    audit["save_as_new"] = True
    # Preserve the exact selected-mode baseline for the saved comparison, even
    # if the original portfolio is later changed or deleted.
    audit["source_snapshot"] = {
        "id": portfolio_id,
        "portfolio_type": target,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capital": float(inputs["capital"]),
        "total_net_profit": float(baseline.total_net_profit),
        "actual_valley_dd": float(baseline.valley_dd),
        "total_units": sum(int(member.get("units") or 0) for member in detail["members"]),
        "total_lot": sum(float(member.get("lot") or 0) for member in detail["members"]),
        "active_strategies": len(original_ids),
        "members": [dict(member) for member in detail["members"]],
    }
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
            "improvement_source_portfolio_id": portfolio_id,
            "improvement_portfolio_type": target,
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
            # Consumed by the outer comparison across addition counts. It never
            # crosses the HTTP boundary or reaches persisted proposal inputs.
            "_improvement_baseline": baseline,
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
    """Compare valid addition counts for the selected mode; prefer fewer on ties."""
    target_type = str(
        inputs.get("improvement_portfolio_type")
        or inputs.get("portfolio_type")
        or "balanced"
    ).strip().lower()
    if target_type not in PORTFOLIO_TYPES:
        raise ValueError("Elige la variante a mejorar: Agresivo, Moderado o Conservador")
    # Only this operation overrides the saved composition objective. The chosen
    # variant drives selection and its own saved lots define the comparison.
    inputs = {
        **inputs,
        "portfolio_type": target_type,
        "improvement_portfolio_type": target_type,
    }
    requested = minimum_additions(inputs)
    priority = improvement_selection_priority(inputs)
    inputs["improvement_min_additions"] = requested
    failures: list[str] = []
    best = None
    best_rank: tuple[float, ...] | None = None
    baseline_stress: BootstrapDrawdownAnalysis | None = None
    for additions in range(requested, MAX_IMPROVEMENT_ADDITIONS + 1):
        if progress:
            progress(
                f"Mejora {TYPE_LABELS[target_type]} · probando {additions} incorporación(es) "
                f"con mínimo {requested} y límite de búsqueda {MAX_IMPROVEMENT_ADDITIONS}"
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
        improvement["minimum_additions"] = requested
        improvement["maximum_additions"] = MAX_IMPROVEMENT_ADDITIONS
        improvement["selection_priority"] = priority
        for proposal in proposals:
            proposal.setdefault("inputs", {}).update({
                "improvement_min_additions": requested,
                "improvement_max_additions": MAX_IMPROVEMENT_ADDITIONS,
                "improvement_selection_priority": priority,
            })
            baseline = proposal.pop("_improvement_baseline", None)
            if baseline is not None:
                baseline_stress = _attach_stress_comparison(
                    result=proposal["result"],
                    baseline=baseline,
                    priority=priority,
                    baseline_stress=baseline_stress,
                )
            audit = (proposal["result"].seasonal_validation or {}).get(
                "portfolio_improvement"
            )
            if isinstance(audit, dict):
                audit["minimum_additions"] = requested
                audit["maximum_additions"] = MAX_IMPROVEMENT_ADDITIONS
        rank = (
            _improvement_rank(proposals[0], additions, priority)
            if proposals else (float("-inf"),)
        )
        if best is None or best_rank is None or rank > best_rank:
            best, best_rank = (availability, proposals), rank
    if best is not None:
        return best
    detail = "; ".join(failures) if failures else "sin candidatas válidas"
    raise ValueError(
        f"No se encontró una mejora válida con al menos {requested} estrategias nuevas "
        f"(límite de búsqueda: {MAX_IMPROVEMENT_ADDITIONS}). No se rebaja el mínimo. "
        f"Intentos: {detail}"
    )
