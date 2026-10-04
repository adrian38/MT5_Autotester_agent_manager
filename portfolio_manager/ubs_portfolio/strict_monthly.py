"""Busqueda mensual estricta con la auditoria estacional 5A."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Sequence

from .symbols import portfolio_symbol_key
from .models import (
    OptimizationDecision,
    PortfolioEvaluation,
    PortfolioGroupLimits,
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
    group_limits_for_portfolio_type,
)
from .selection import (
    filter_eligible_sets,
    score_set_for_portfolio,
    select_top_k_per_symbol,
    validate_strict_monthly_portfolio,
)
from .evaluation import (
    _evaluation_violates_dd_limits,
    evaluate_portfolio,
)
from .limits import CandidateFunnel, SearchLimits, SearchPlan
from .margin import MarginModel
from .constraints import (
    _allocations_respect_constraints,
    _portfolio_active_count,
    _portfolio_corr_allowed,
    _target_group_units_pct_allowed,
    can_add_unit,
    violates_correlation_limits,
)
from .greedy import (
    _DeepScan,
    _active_unit_allocations,
    _caps_allow,
    _swap_respects_caps,
    _swap_respects_correlation,
)
from .optimize import optimize_portfolio


from .strict_monthly_candidates import (
    _strict_validation_for_allocations,
    _strict_monthly_candidate_validation,
    _strict_monthly_candidate_score,
    _limit_sorted_candidates_with_symbol_reserve,
    _monthly_orderings,
    _distinct_monthly_variants,
    _strict_monthly_candidate_variants,
    _strict_monthly_violation_score,
    _MonthlyReduction,
    _best_monthly_reduction,
    _monthly_reduction_decision,
    _repair_allocations_to_strict_monthly,
)
from .strict_monthly_refinement import (
    _monthly_evaluate,
    _monthly_gain,
    _ordered_monthly_targets,
    _monthly_add_candidate,
    _monthly_swap_candidate,
    _best_monthly_refill,
    _best_monthly_deep_move,
    _monthly_refill_decision,
    _monthly_deep_decision,
    _strict_monthly_safe_refill_allocations,
    _strict_monthly_deep_refine_allocations,
)


@dataclass(frozen=True)
class _MonthlyOptimizerArgs:
    """Lo que las cuatro llamadas a ``optimize_portfolio`` del mensual comparten.

    Las tres reconstrucciones -reparacion, relleno seguro y refinamiento- solo
    se diferencian del caso base en que fijan la asignacion; todo lo demas
    viajaba copiado tres veces.
    """

    capital: float
    valley_dd_pct: float
    point_dd_pct: float
    portfolio_type: PortfolioType
    min_trades_2020_2026: int
    dd_reserve_pct: float
    limits: SearchLimits

    def base_kwargs(self) -> dict[str, object]:
        """Lo que toda llamada del mensual comparte al nivel de la funcion."""
        return {
            "capital": self.capital,
            "valley_dd_pct": self.valley_dd_pct,
            "point_dd_pct": self.point_dd_pct,
            "portfolio_type": self.portfolio_type,
        }


def _strict_monthly_limits(
    args: _MonthlyOptimizerArgs,
    group_limits: PortfolioGroupLimits,
    max_total_units: int | None,
) -> SearchLimits:
    """Topes comunes del relleno seguro y del refinamiento profundo."""
    return SearchLimits(
        max_units_per_set=args.limits.max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=args.limits.max_units_per_symbol,
        max_sets_per_symbol=args.limits.max_sets_per_symbol,
        max_sets_per_group=group_limits.max_sets,
        max_units_per_group_pct=group_limits.max_units_pct,
        group_unit_cap_bootstrap=group_limits.bootstrap_units,
        max_pair_corr=args.limits.max_pair_corr,
        max_downside_corr=args.limits.max_downside_corr,
        max_dd_overlap=args.limits.max_dd_overlap,
        existing_portfolio_curves=args.limits.existing_portfolio_curves,
        max_portfolio_corr=args.limits.max_portfolio_corr,
        margin_balance=args.limits.margin_balance,
        max_margin_pct=args.limits.max_margin_pct,
        margin_profile=args.limits.margin_profile,
        stock_leverage=args.limits.stock_leverage,
        default_leverage=args.limits.default_leverage,
        stock_contract_size=args.limits.stock_contract_size,
        default_contract_size=args.limits.default_contract_size,
        max_daily_dd=args.limits.max_daily_dd,
        enforce_point_dd=args.limits.enforce_point_dd,
        daily_dd_full_history=args.limits.daily_dd_full_history,
    )


def _active_units_of(result: PortfolioResult) -> dict[str, int]:
    """Unidades por set del resultado, sin las asignaciones a cero."""
    return {
        allocation.set_id: allocation.units
        for allocation in result.allocations
        if allocation.units > 0
    }


def _drop_preserved_warning(result: PortfolioResult) -> None:
    """Quita el aviso de 'se preservo el portafolio': aqui no informa de nada.

    Lo emite ``optimize_portfolio`` porque se le fija la asignacion, pero en el
    mensual fijarla es el mecanismo de reconstruccion, no una decision.
    """
    result.warnings = [
        warning for warning in result.warnings
        if not warning.startswith("Existing portfolio strategies and units were preserved")
    ]


def _monthly_base_evaluation(
    pool: list[RobustStrategySet],
    units: dict[str, int],
    base_result: PortfolioResult,
    args: _MonthlyOptimizerArgs,
) -> PortfolioEvaluation:
    """Evaluacion de la base sobre el pool ampliado, punto de partida comun."""
    return evaluate_portfolio(
        pool,
        units,
        base_result.target_valley_dd,
        base_result.target_point_dd,
        args.limits.max_daily_dd,
        args.limits.enforce_point_dd,
        args.limits.daily_dd_full_history,
    )


def _reoptimize_locked_monthly(
    sets: list[RobustStrategySet],
    locked: dict[str, int],
    args: _MonthlyOptimizerArgs,
) -> PortfolioResult:
    """Reconstruye el resultado sobre una asignacion ya decidida.

    No vuelve a buscar: fija las unidades y deja que el motor recalcule DD,
    margen, lotes y curva. Es la forma que comparten la reparacion estricta, el
    relleno seguro y el refinamiento profundo.
    """
    return optimize_portfolio(
        raw_sets=sets,
        limits=replace(args.limits, max_total_units=sum(locked.values())),
        funnel=CandidateFunnel(
            min_trades_2020_2026=args.min_trades_2020_2026,
            top_k_per_symbol=max(1, len(sets)),
            max_total_candidates=None,
            required_initial_allocations=locked,
            preserve_required_allocations=True,
        ),
        search=SearchPlan(run_local_search=False, dd_reserve_pct=args.dd_reserve_pct),
        **args.base_kwargs(),
    )


def _strict_monthly_variant_result(
    pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    top_k_per_symbol: int,
    max_total_units: int | None,
    run_local_search: bool,
    search_restarts: int,
) -> tuple[PortfolioResult | None, str]:
    """Optimiza una variante, la repara al 5A y devuelve el motivo si falla."""
    try:
        base_result = optimize_portfolio(
            raw_sets=pool,
            limits=replace(args.limits, max_total_units=max_total_units),
            funnel=CandidateFunnel(
                min_trades_2020_2026=args.min_trades_2020_2026,
                top_k_per_symbol=max(top_k_per_symbol, len(pool)),
                max_total_candidates=None,
            ),
            search=SearchPlan(
                run_local_search=run_local_search,
                search_restarts=int(search_restarts),
                dd_reserve_pct=args.dd_reserve_pct,
            ),
            **args.base_kwargs(),
        )
    except Exception as exc:
        return None, str(exc)
    base_units = _active_units_of(base_result)
    repaired_units, _repaired_eval, validation, repair_log = _repair_allocations_to_strict_monthly(
        pool,
        full_by_id,
        base_units,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        limits=SearchLimits(
            max_daily_dd=args.limits.max_daily_dd,
            enforce_point_dd=args.limits.enforce_point_dd,
            daily_dd_full_history=args.limits.daily_dd_full_history,
        ),
    )
    if not bool(validation.get("passed")):
        reasons = validation.get("reasons") or []
        return None, "; ".join(str(item) for item in list(reasons)[:3])
    active_repaired = _active_unit_allocations(repaired_units)
    if not active_repaired:
        return None, "reparacion estricta dejo el portafolio sin estrategias"
    if active_repaired != base_units:
        base_result, error = _rebuild_after_strict_repair(
            pool, active_repaired, repair_log, args,
        )
        if base_result is None:
            return None, error
    base_result.seasonal_validation = validation
    return base_result, ""


def _rebuild_after_strict_repair(
    pool: list[RobustStrategySet],
    active_repaired: dict[str, int],
    repair_log: list[OptimizationDecision],
    args: _MonthlyOptimizerArgs,
) -> tuple[PortfolioResult | None, str]:
    """Reconstruye el resultado cuando la reparacion cambio las unidades."""
    repaired_sets = [
        strategy for strategy in pool if strategy.set_id in active_repaired
    ]
    try:
        result = _reoptimize_locked_monthly(repaired_sets, active_repaired, args)
    except Exception as exc:
        return None, f"reparacion estricta no pudo reconstruirse: {exc}"
    _drop_preserved_warning(result)
    result.decision_log.extend(repair_log)
    result.warnings.append(
        "Reparacion estricta mensual: se redujeron "
        f"{len(repair_log)} unidad(es) para cumplir DD de todos los meses y mejor mes 5A."
    )
    return result, ""


def _select_strict_monthly_base(
    variants: Sequence[tuple[str, list[RobustStrategySet]]],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    **variant_kwargs: object,
) -> tuple[PortfolioResult, str]:
    """Primera variante que supera la auditoria estricta, o error con motivos."""
    errors: list[str] = []
    for label, pool in variants:
        result, error = _strict_monthly_variant_result(
            pool, full_by_id, args, **variant_kwargs,
        )
        if result is not None:
            return result, label
        errors.append(f"{label}: {error}")
    detail = " | ".join(errors[:6])
    raise ValueError(
        "Ninguna variante de busqueda estricta mensual fue viable."
        + (f" {detail}" if detail else "")
    )


def _monthly_candidate_pool(
    variants: Sequence[tuple[str, list[RobustStrategySet]]],
    monthly_by_id: dict[str, RobustStrategySet],
    base_result: PortfolioResult,
) -> list[RobustStrategySet]:
    """Union de todas las variantes, con la version mensual de lo ya asignado."""
    pool_by_id: dict[str, RobustStrategySet] = {}
    for _label, variant_pool in variants:
        for strategy in variant_pool:
            pool_by_id[strategy.set_id] = strategy
    for allocation in base_result.allocations:
        strategy = monthly_by_id.get(allocation.set_id)
        if strategy is not None:
            pool_by_id[allocation.set_id] = strategy
    return list(pool_by_id.values())


def _monthly_safe_refill(
    base_result: PortfolioResult,
    base_label: str,
    candidate_pool: list[RobustStrategySet],
    monthly_by_id: dict[str, RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    max_total_units: int | None,
) -> PortfolioResult:
    """Anade unidades sin romper DD, margen, correlacion ni la auditoria 5A."""
    group_limits = group_limits_for_portfolio_type(args.portfolio_type)
    base_units = _active_units_of(base_result)
    safe_allocations, safe_eval, safe_log, attempts = _strict_monthly_safe_refill_allocations(
        candidate_pool,
        full_by_id,
        base_units,
        _monthly_base_evaluation(candidate_pool, base_units, base_result, args),
        target_month=month,
        limits=_strict_monthly_limits(args, group_limits, max_total_units),
    )
    active_safe = _active_unit_allocations(safe_allocations)
    validation = _strict_validation_for_allocations(
        full_by_id,
        active_safe,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        enforce_point_dd=args.limits.enforce_point_dd,
    )
    improves = safe_eval.total_net_profit > base_result.total_net_profit + 1e-9
    if not (improves and bool(validation.get("passed"))):
        base_result.warnings.append(
            "Relleno seguro mensual: no encontro unidades adicionales validas "
            f"sin romper DD/margen/correlacion/5A ({attempts} intentos evaluados)."
        )
        base_result.warnings.append(
            f"Generacion estricta mensual OK sin optimizacion profunda; base '{base_label}'."
        )
        return base_result
    safe_sets = [
        monthly_by_id[set_id] for set_id in active_safe if set_id in monthly_by_id
    ]
    safe_result = _reoptimize_locked_monthly(safe_sets, active_safe, args)
    safe_result.seasonal_validation = validation
    _drop_preserved_warning(safe_result)
    safe_result.decision_log.extend(base_result.decision_log)
    safe_result.decision_log.extend(safe_log)
    safe_result.warnings.extend(base_result.warnings)
    safe_result.warnings.append(
        "Relleno seguro mensual aplicado sin optimizacion profunda: "
        f"net {base_result.total_net_profit:,.2f} -> {safe_result.total_net_profit:,.2f}; "
        f"unidades {base_result.total_units} -> {safe_result.total_units}; "
        f"base '{base_label}', {attempts} intentos evaluados."
    )
    return safe_result


def _monthly_deep_refine(
    base_result: PortfolioResult,
    base_label: str,
    candidate_pool: list[RobustStrategySet],
    monthly_by_id: dict[str, RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    max_total_units: int | None,
) -> PortfolioResult:
    """Busqueda profunda sobre la base estricta; solo entra si supera la 5A."""
    group_limits = group_limits_for_portfolio_type(args.portfolio_type)
    base_units = _active_units_of(base_result)
    refined_allocations, refined_eval, refinement_log, attempts = _strict_monthly_deep_refine_allocations(
        candidate_pool,
        full_by_id,
        base_units,
        _monthly_base_evaluation(candidate_pool, base_units, base_result, args),
        target_month=month,
        minimum_active_strategies=base_result.active_strategies,
        limits=_strict_monthly_limits(args, group_limits, max_total_units),
    )
    if refined_eval.total_net_profit <= base_result.total_net_profit + 1e-9:
        base_result.warnings.append(
            "Optimizacion profunda: no encontro mejora valida sobre la base estricta "
            f"({attempts} movimientos evaluados)."
        )
        return base_result
    active_refined = _active_unit_allocations(refined_allocations)
    validation = _strict_validation_for_allocations(
        full_by_id,
        active_refined,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        enforce_point_dd=args.limits.enforce_point_dd,
    )
    lost_diversification = (
        _portfolio_active_count(active_refined) < base_result.active_strategies
    )
    if lost_diversification or not bool(validation.get("passed")):
        base_result.warnings.append(
            "Optimizacion profunda: mejora descartada por diversificacion o validacion 5A."
        )
        return base_result
    refined_sets = [
        monthly_by_id[set_id] for set_id in active_refined if set_id in monthly_by_id
    ]
    refined_result = _reoptimize_locked_monthly(refined_sets, active_refined, args)
    refined_result.seasonal_validation = validation
    _drop_preserved_warning(refined_result)
    refined_result.decision_log.extend(refinement_log)
    refined_result.warnings.append(
        "Optimizacion profunda aplicada: "
        f"net {base_result.total_net_profit:,.2f} -> {refined_result.total_net_profit:,.2f}; "
        f"estrategias {base_result.active_strategies} -> {refined_result.active_strategies}; "
        f"base '{base_label}', {attempts} movimientos evaluados."
    )
    return refined_result


def _strict_monthly_search(
    variants: Sequence[tuple[str, list[RobustStrategySet]]],
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    top_k_per_symbol: int,
    run_local_search: bool,
    search_restarts: int,
    use_deep_refinement: bool,
) -> PortfolioResult:
    """Elige la base estricta y la mejora por la rama que pida el llamante."""
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    base_result, base_label = _select_strict_monthly_base(
        variants,
        full_by_id,
        args,
        month=month,
        top_k_per_symbol=top_k_per_symbol,
        max_total_units=args.limits.max_total_units,
        run_local_search=run_local_search,
        search_restarts=search_restarts,
    )
    monthly_by_id = {strategy.set_id: strategy for strategy in monthly_sets}
    branch = _monthly_deep_refine if use_deep_refinement else _monthly_safe_refill
    return branch(
        base_result,
        base_label,
        _monthly_candidate_pool(variants, monthly_by_id, base_result),
        monthly_by_id,
        full_by_id,
        args,
        month=month,
        max_total_units=args.limits.max_total_units,
    )


def optimize_strict_monthly_portfolio(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    limits: SearchLimits = SearchLimits(),
    funnel: CandidateFunnel = CandidateFunnel(),
    search: SearchPlan = SearchPlan(),
) -> PortfolioResult:
    """Cartera mensual con la prueba estacional de cinco anos dentro del bucle.

    Solo conserva los pools que superan la auditoria ano a ano y de mejor mes.
    """
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")
    reserve_factor = 1.0 - min(max(float(search.dd_reserve_pct), 0.0), 99.0) / 100.0
    variants = _strict_monthly_candidate_variants(
        monthly_sets,
        full_sets,
        target_month=month,
        target_valley_dd=float(capital) * float(valley_dd_pct) * reserve_factor / 100.0,
        target_point_dd=float(capital) * float(point_dd_pct) * reserve_factor / 100.0,
        min_trades_2020_2026=funnel.min_trades_2020_2026,
        top_k_per_symbol=funnel.top_k_per_symbol,
        max_total_candidates=funnel.max_total_candidates,
        limits=limits,
    )
    if not variants:
        raise ValueError("No hay candidatos mensuales elegibles para la busqueda estricta.")
    return _strict_monthly_search(
        variants,
        monthly_sets,
        full_sets,
        _MonthlyOptimizerArgs(
            capital=capital,
            valley_dd_pct=valley_dd_pct,
            point_dd_pct=point_dd_pct,
            portfolio_type=portfolio_type,
            min_trades_2020_2026=funnel.min_trades_2020_2026,
            dd_reserve_pct=search.dd_reserve_pct,
            limits=limits,
        ),
        month=month,
        top_k_per_symbol=funnel.top_k_per_symbol,
        run_local_search=search.run_local_search,
        search_restarts=search.search_restarts,
        use_deep_refinement=search.use_deep_refinement,
    )
