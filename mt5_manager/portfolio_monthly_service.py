from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
    slice_strategy_sets_to_month,
    validate_strict_monthly_portfolio,
)

from .portfolio_service import (
    ASSET_GROUPS,
    PORTFOLIO_TYPES,
    TYPE_LABELS,
    PortfolioSource,
    _optimize_without_recent_fillers,
    _optimizer_kwargs,
    _seasonal_coverage,
    cached_report,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    portfolio_group_key,
    summarize_robust_rows,
)


Progress = Callable[[str], None]


def prepare_monthly_log(source: PortfolioSource, operation: str, job_id: str) -> Path:
    """Create the monthly log before the worker starts so the UI can open it immediately."""
    log_dir = source.project / "portfolio_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"manager_monthly_{operation}_{job_id}.log"
    path.write_text(
        f"{datetime.now().isoformat(timespec='seconds')} | 0/6 · Preparando cálculo mensual\n",
        encoding="utf-8",
    )
    return path


def _monthly_proposals(
    monthly_sets: list[Any],
    full_sets: list[Any],
    inputs: dict[str, Any],
    existing_curves: list[list[float]],
    progress: Progress | None = None,
) -> list[dict[str, Any]]:
    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured = float(inputs.get("dd_reserve_pct") or 0)
    specs = (
        ("profit", "Máximo beneficio", base_type, configured),
        ("balanced", "Equilibrada", PortfolioType.BALANCED, max(configured, 15.0)),
        ("margin", "Máximo margen DD", PortfolioType.CONSERVATIVE, max(configured, 25.0)),
    )
    proposals: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, (key, label, objective_type, reserve) in enumerate(specs, 1):
        if progress:
            progress(f"5/6 · Optimizando propuesta {index}/3: {label}")
        proposal_inputs = dict(inputs)
        proposal_inputs.update({
            "optimization_profile": key,
            "optimization_profile_label": label,
            "portfolio_type": objective_type.value,
            "portfolio_type_label": TYPE_LABELS[objective_type.value],
            "dd_reserve_pct": reserve,
        })
        kwargs = _optimizer_kwargs(inputs, objective_type, existing_curves, reserve)

        def optimize(candidate_sets: list[Any]) -> PortfolioResult:
            if inputs.get("strict_yearly_month_validation"):
                return optimize_strict_monthly_portfolio(
                    monthly_sets=candidate_sets,
                    full_sets=full_sets,
                    target_month=int(inputs["target_month"]),
                    use_deep_refinement=bool(inputs.get("deep_optimization")),
                    **kwargs,
                )
            return optimize_portfolio(
                raw_sets=candidate_sets,
                use_deep_refinement=bool(inputs.get("deep_optimization")),
                **kwargs,
            )

        try:
            result, _removed = _optimize_without_recent_fillers(
                monthly_sets,
                float(inputs.get("min_strategy_recent_contribution_pct") or 0.0),
                optimize,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        _seasonal_coverage(result, monthly_sets)
        proposals.append({"key": key, "label": label, "reserve_pct": reserve, "inputs": proposal_inputs, "result": result})
    if not proposals:
        raise ValueError("Ninguna propuesta mensual fue viable. " + " | ".join(errors))
    return proposals


def _strict_monthly_candidate_pool(
    raw_sets: list[Any], inputs: dict[str, Any]
) -> tuple[list[Any], list[str]]:
    """Keep sets whose best five-year month is the selected target month."""
    target_month = int(inputs["target_month"])
    selected: list[Any] = []
    for strategy in raw_sets:
        validation = validate_strict_monthly_portfolio(
            [strategy],
            {strategy.set_id: 1},
            target_month=target_month,
            target_valley_dd=1_000_000_000.0,
            target_point_dd=1_000_000_000.0,
            lookback_years=5,
        )
        if (
            int(validation.get("best_month") or 0) == target_month
            and float(validation.get("target_month_net") or 0.0) > 0
        ):
            selected.append(strategy)
    return selected, [
        "Validación estricta: reintento con "
        f"{len(selected)}/{len(raw_sets)} candidato(s) cuyo mejor mes individual 5A es el objetivo."
    ]


def generate_monthly_proposals(
    source: PortfolioSource,
    inputs: dict[str, Any],
    progress: Progress | None = None,
    *,
    exclude_portfolio_id: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if inputs.get("portfolio_scope") != "monthly":
        raise ValueError("La lógica mensual sólo admite portfolio_scope=monthly")
    if progress:
        progress("1/6 · Leyendo candidatos Final Tick aceptados")
    rows = source.candidate_rows(include_quarantined=True)
    if not rows:
        raise ValueError("No hay candidatos con Final Tick continuo y 6M aceptados")
    warnings: list[str] = []
    if progress:
        progress("2/6 · Aplicando filtros y grupos permitidos")
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows, min_positive_months=3, window_months=6, parse=cached_report,
        )
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(inputs["allowed_asset_groups"])
    group_counts: dict[str, int] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        group = portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        )
        group_counts[group] = group_counts.get(group, 0) + 1
        if group in allowed:
            filtered.append(row)
    rows = filtered
    if not rows:
        raise ValueError("No quedan candidatos tras aplicar los grupos permitidos")
    used = (
        source.used_set_paths("monthly", exclude_portfolio_id=exclude_portfolio_id)
        if inputs.get("exclude_monthly_used") else []
    )
    availability = asdict(summarize_robust_rows(rows, used))
    if progress:
        progress(f"3/6 · Cargando reportes de {len(rows)} candidatos")
    raw_sets, load_warnings = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(load_warnings)
    raw_sets = [
        strategy for strategy in raw_sets
        if portfolio_group_key(strategy.symbol, universe_files=[source.universe]) in allowed
    ]
    if not raw_sets:
        raise ValueError("No quedan sets cargados después de los filtros")
    if progress:
        progress(f"4/6 · Recortando curvas al mes {int(inputs['target_month']):02d}")
    monthly_sets, slice_warnings = slice_strategy_sets_to_month(
        raw_sets, int(inputs["target_month"]),
    )
    warnings.extend(slice_warnings)
    if not monthly_sets:
        raise ValueError("Ningún candidato tiene trades para el mes objetivo")
    existing = (
        source.saved_curves(monthly=True, exclude_portfolio_id=exclude_portfolio_id)
        if inputs.get("corr_with_monthly_portfolios") else []
    )

    def validate_strict(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not inputs.get("strict_yearly_month_validation"):
            return items
        if progress:
            progress("6/6 · Validando el mes objetivo sobre cinco años")
        valid: list[dict[str, Any]] = []
        rejected: list[str] = []
        full_by_id = {strategy.set_id: strategy for strategy in raw_sets}
        for proposal in items:
            result = proposal["result"]
            units = {
                allocation.set_id: allocation.units
                for allocation in result.allocations if allocation.units > 0
            }
            validation = validate_strict_monthly_portfolio(
                [full_by_id[set_id] for set_id in units if set_id in full_by_id],
                units,
                target_month=int(inputs["target_month"]),
                target_valley_dd=result.target_valley_dd,
                target_point_dd=result.target_point_dd,
                enforce_point_dd=False,
                lookback_years=5,
            )
            result.seasonal_validation = validation
            if validation.get("passed"):
                valid.append(proposal)
            else:
                reasons = "; ".join(str(item) for item in (validation.get("reasons") or [])[:3])
                rejected.append(f"{proposal['label']}: {reasons}")
        if not valid:
            raise ValueError(
                "Ninguna propuesta pasó la validación mensual estricta. " + " | ".join(rejected)
            )
        return valid

    try:
        proposals = _monthly_proposals(monthly_sets, raw_sets, inputs, existing, progress)
        proposals = validate_strict(proposals)
    except ValueError:
        if not inputs.get("strict_yearly_month_validation"):
            raise
        if progress:
            progress("4/6 · Reintentando con el pool mensual estricto")
        strict_raw_sets, strict_warnings = _strict_monthly_candidate_pool(raw_sets, inputs)
        if not strict_raw_sets:
            raise
        strict_monthly_sets, strict_slice_warnings = slice_strategy_sets_to_month(
            strict_raw_sets, int(inputs["target_month"]),
        )
        warnings.extend(strict_warnings)
        warnings.extend(strict_slice_warnings)
        if not strict_monthly_sets:
            raise
        proposals = _monthly_proposals(strict_monthly_sets, raw_sets, inputs, existing, progress)
        proposals = validate_strict(proposals)
    if progress and not inputs.get("strict_yearly_month_validation"):
        progress("6/6 · Preparando propuestas mensuales")
    for proposal in proposals:
        proposal["result"].warnings[:0] = warnings
    availability.update({"loaded_sets": len(raw_sets), "group_counts": group_counts, "warnings": warnings})
    return availability, proposals


def generate_monthly_completion_proposal(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    detail = source.saved_portfolio_detail(portfolio_id, "monthly")["portfolio"]
    members = list(detail.get("members") or [])
    target = max(int(detail.get("target_strategies") or 0), int(detail.get("active_strategies") or 0))
    if target <= len(members):
        raise ValueError("El portafolio ya tiene todas sus estrategias")
    if progress:
        progress(f"1/6 · Reconstruyendo {len(members)} estrategias que deben conservarse")
    required_rows = [{
        "candidate_id": item.get("candidate_id"),
        "set_path": item.get("set_path") or item.get("set_id"),
        "symbol": item.get("symbol"),
        "target_symbol": item.get("symbol"),
        "period": item.get("timeframe"),
        "family": "",
        "is_report_path": item.get("is_report_path"),
        "oos_report_path": item.get("oos_report_path"),
        "final_tick_report_path": item.get("final_tick_report_path"),
        "full_history_report_path": item.get("full_history_report_path"),
        "max_balance_dd_001": item.get("max_balance_dd_001"),
        "max_equity_dd_001": item.get("max_equity_dd_001"),
        "floating_dd_source": item.get("floating_dd_source"),
        "recent_net_profit_001": item.get("recent_net_profit_001"),
        "recent_equity_dd_001": item.get("recent_equity_dd_001"),
        "has_recent_performance": item.get("has_recent_performance"),
    } for item in members]
    required_sets, required_warnings = load_robust_sets_from_rows(
        required_rows, [], parse=cached_report,
    )
    if len(required_sets) != len(required_rows):
        raise ValueError("No se pudieron reconstruir todas las estrategias que deben conservarse")
    if progress:
        progress("2/6 · Aplicando filtros mensuales")
    rows = source.candidate_rows(include_quarantined=True)
    warnings = list(required_warnings)
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
        row for row in rows
        if portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        ) in allowed
    ]
    if progress:
        progress(f"3/6 · Cargando reportes de {len(rows)} candidatos")
    candidate_sets, load_warnings = load_robust_sets_from_rows(
        rows, [], parse=cached_report, progress=progress,
    )
    warnings.extend(load_warnings)
    full_sets = list(required_sets) + list(candidate_sets)
    if progress:
        progress(f"4/6 · Recortando curvas al mes {int(inputs['target_month']):02d}")
    required_sets, found = slice_strategy_sets_to_month(required_sets, int(inputs["target_month"]))
    warnings.extend(found)
    candidate_sets, found = slice_strategy_sets_to_month(candidate_sets, int(inputs["target_month"]))
    warnings.extend(found)
    by_id = {strategy.set_id: strategy for strategy in candidate_sets}
    by_id.update({strategy.set_id: strategy for strategy in required_sets})
    raw_sets = list(by_id.values())
    required_ids = [strategy.set_id for strategy in required_sets]
    saved_units = {
        str(item.get("set_path") or item.get("set_id") or ""): int(item.get("units") or 0)
        for item in members
    }
    initial = {strategy.set_id: saved_units.get(strategy.set_id, 0) for strategy in required_sets}
    portfolio_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    reserve = float(inputs.get("dd_reserve_pct") or 0)
    existing = (
        source.saved_curves(monthly=True, exclude_portfolio_id=portfolio_id)
        if inputs.get("corr_with_monthly_portfolios") else []
    )
    kwargs = _optimizer_kwargs(inputs, portfolio_type, existing, reserve)
    kwargs.update({
        "required_set_ids": required_ids,
        "minimum_active_strategies": target,
        "maximum_active_strategies": target,
        "required_initial_allocations": initial,
        "preserve_required_allocations": True,
    })
    if progress:
        progress(f"5/6 · Buscando sustituta para completar {len(members)}/{target}")
    result = optimize_portfolio(
        raw_sets=raw_sets,
        use_deep_refinement=bool(inputs.get("deep_optimization")),
        **kwargs,
    )
    _seasonal_coverage(result, raw_sets)
    if inputs.get("strict_yearly_month_validation"):
        if progress:
            progress("6/6 · Validando la sustitución sobre cinco años")
        full_by_id = {strategy.set_id: strategy for strategy in full_sets}
        units = {item.set_id: item.units for item in result.allocations if item.units > 0}
        validation = validate_strict_monthly_portfolio(
            [full_by_id[set_id] for set_id in units if set_id in full_by_id],
            units,
            target_month=int(inputs["target_month"]),
            target_valley_dd=result.target_valley_dd,
            target_point_dd=result.target_point_dd,
            enforce_point_dd=False,
            lookback_years=5,
        )
        result.seasonal_validation = validation
        if not validation.get("passed"):
            reasons = "; ".join(str(item) for item in (validation.get("reasons") or [])[:3])
            raise ValueError("La sustitución no pasó la validación mensual estricta: " + reasons)
    elif progress:
        progress("6/6 · Preparando la sustitución mensual")
    result.warnings[:0] = warnings
    if result.active_strategies < target:
        raise ValueError(
            f"No existe una sustituta compatible: quedaron {result.active_strategies}/{target} estrategias"
        )
    proposal_inputs = dict(inputs)
    proposal_inputs.update({
        "optimization_profile": "complete",
        "optimization_profile_label": "Completar portafolio",
    })
    availability = asdict(summarize_robust_rows(rows, []))
    availability.update({"loaded_sets": len(raw_sets), "warnings": warnings})
    return availability, [{
        "key": "complete",
        "label": "Completar portafolio",
        "reserve_pct": reserve,
        "inputs": proposal_inputs,
        "result": result,
    }]


def run_monthly_operation(
    source: PortfolioSource,
    operation: str,
    portfolio_id: int | None,
    settings: dict[str, Any],
    progress: Progress,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if operation == "complete":
        if portfolio_id is None:
            raise ValueError("Falta el portafolio que se quiere completar")
        return generate_monthly_completion_proposal(source, portfolio_id, settings, progress)
    return generate_monthly_proposals(
        source,
        settings,
        progress,
        exclude_portfolio_id=portfolio_id if operation == "reoptimize" else None,
    )
