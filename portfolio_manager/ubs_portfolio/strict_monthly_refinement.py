"""Relleno y refinamiento de asignaciones mensuales estrictas."""

from __future__ import annotations

from .models import OptimizationDecision, PortfolioEvaluation, RobustStrategySet
from .selection import score_set_for_portfolio
from .evaluation import _evaluation_violates_dd_limits, evaluate_portfolio
from .limits import SearchLimits
from .constraints import (
    _portfolio_active_count,
    _portfolio_corr_allowed,
    violates_correlation_limits,
)
from .greedy import _DeepScan, _caps_allow, _swap_respects_caps, _swap_respects_correlation
from .strict_monthly_candidates import _strict_validation_for_allocations


def _monthly_evaluate(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
) -> PortfolioEvaluation:
    """La cartera mensual medida con los limites de la base."""
    return evaluate_portfolio(
        sets,
        allocations,
        current.target_valley_dd,
        current.target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )


def _monthly_gain(
    temp: PortfolioEvaluation,
    temp_allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
) -> float | None:
    """Cuanto gana el movimiento, o ``None`` si no supera la auditoria 5A.

    Lo que distingue al mensual del optimizador normal: una mejora que rompa la
    validacion estacional no vale, por mucho beneficio que traiga.
    """
    validation = _strict_validation_for_allocations(
        full_by_id,
        temp_allocations,
        target_month=target_month,
        target_valley_dd=current.target_valley_dd,
        target_point_dd=current.target_point_dd,
        enforce_point_dd=limits.enforce_point_dd,
    )
    if not bool(validation.get("passed")):
        return None
    gain = temp.total_net_profit - current.total_net_profit
    return gain if gain > 1e-9 else None


def _ordered_monthly_targets(
    sets: list[RobustStrategySet], allocations: dict[str, int],
) -> list[RobustStrategySet]:
    """Candidatas por puntuacion: se prueba primero la que mas promete."""
    return sorted(
        sets,
        key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
        reverse=True,
    )


def _monthly_add_candidate(
    target: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
) -> tuple[dict[str, int], PortfolioEvaluation, float] | None:
    """Una unidad mas en la candidata, si sobrevive a todos los filtros."""
    if not _caps_allow(sets, target, allocations, limits):
        return None
    if allocations.get(target.set_id, 0) <= 0:
        rejected_by_corr, _reason = violates_correlation_limits(
            target,
            sets,
            allocations,
            limits.max_pair_corr,
            limits.max_downside_corr,
            limits.max_dd_overlap,
        )
        if rejected_by_corr:
            return None
    temp_allocations = allocations.copy()
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    temp = _monthly_evaluate(sets, temp_allocations, current, limits)
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allowed(temp, limits.existing_portfolio_curves, limits.max_portfolio_corr):
        return None
    gain = _monthly_gain(temp, temp_allocations, current, limits, full_by_id, target_month)
    if gain is None:
        return None
    return temp_allocations, temp, gain


def _monthly_swap_candidate(
    source: RobustStrategySet,
    target: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
    minimum_active_strategies: int,
) -> tuple[dict[str, int], PortfolioEvaluation, float] | None:
    """Mover una unidad de una activa a la candidata, si todo lo permite."""
    temp_allocations = allocations.copy()
    temp_allocations[source.set_id] -= 1
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    if _portfolio_active_count(temp_allocations) < minimum_active_strategies:
        return None
    if not _swap_respects_caps(sets, target, temp_allocations, limits, None):
        return None
    if not _swap_respects_correlation(sets, target, allocations, temp_allocations, limits):
        return None
    temp = _monthly_evaluate(sets, temp_allocations, current, limits)
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allowed(temp, limits.existing_portfolio_curves, limits.max_portfolio_corr):
        return None
    gain = _monthly_gain(temp, temp_allocations, current, limits, full_by_id, target_month)
    if gain is None:
        return None
    return temp_allocations, temp, gain


def _best_monthly_refill(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
) -> _DeepScan:
    """La unidad que mas gana sin romper nada; solo anade, nunca quita."""
    scan = _DeepScan()
    for target in _ordered_monthly_targets(sets, allocations):
        scan.attempts += 1
        found = _monthly_add_candidate(
            target, sets, allocations, current, limits, full_by_id, target_month,
        )
        if found is None:
            continue
        temp_allocations, temp, gain = found
        scan.offer({
            "target": target,
            "allocations": temp_allocations,
            "evaluation": temp,
            "gain": gain,
        })
    return scan


def _best_monthly_deep_move(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
    minimum_active_strategies: int,
) -> _DeepScan:
    """El mejor movimiento profundo: anadir o intercambiar, siempre con 5A."""
    scan = _DeepScan()
    for target in _ordered_monthly_targets(sets, allocations):
        scan.attempts += 1
        added = _monthly_add_candidate(
            target, sets, allocations, current, limits, full_by_id, target_month,
        )
        if added is not None:
            temp_allocations, temp, gain = added
            scan.offer({
                "action": "deep_add_unit", "from_set": None, "to_set": target,
                "allocations": temp_allocations, "evaluation": temp, "gain": gain,
            })
        active_sources = [source for source in sets if allocations.get(source.set_id, 0) > 0]
        for source in active_sources:
            if source.set_id == target.set_id:
                continue
            scan.attempts += 1
            swapped = _monthly_swap_candidate(
                source, target, sets, allocations, current, limits, full_by_id,
                target_month, minimum_active_strategies,
            )
            if swapped is None:
                continue
            temp_allocations, temp, gain = swapped
            scan.offer({
                "action": "deep_swap_unit", "from_set": source, "to_set": target,
                "allocations": temp_allocations, "evaluation": temp, "gain": gain,
            })
    return scan


def _monthly_refill_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    iteration: int,
) -> OptimizationDecision:
    """La linea del registro que explica la unidad anadida por relleno seguro."""
    target = best_move["target"]
    assert isinstance(target, RobustStrategySet)
    return OptimizationDecision(
        step=iteration,
        action="strict_monthly_safe_add_unit",
        set_id=target.set_id,
        from_set_id=None,
        to_set_id=target.set_id,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=float(best_move["gain"]),
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason="Relleno seguro: unidad anadida sin romper DD, margen, correlacion ni 5A",
    )


def _monthly_deep_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    iteration: int,
) -> OptimizationDecision:
    """La linea del registro que explica el movimiento profundo mensual."""
    from_set = best_move["from_set"]
    to_set = best_move["to_set"]
    assert to_set is not None and isinstance(to_set, RobustStrategySet)
    return OptimizationDecision(
        step=iteration,
        action=str(best_move["action"]),
        set_id=to_set.set_id,
        from_set_id=from_set.set_id if isinstance(from_set, RobustStrategySet) else None,
        to_set_id=to_set.set_id,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=float(best_move["gain"]),
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason="Optimizacion profunda: movimiento validado contra DD, margen, correlacion y 5A",
    )


def _strict_monthly_safe_refill_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
    max_iterations: int = 160,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0
    for iteration in range(1, max_iterations + 1):
        scan = _best_monthly_refill(
            sets, allocations, current, limits, full_by_id, target_month,
        )
        attempts += scan.attempts
        if scan.best is None:
            break
        previous = current
        allocations = scan.best["allocations"]  # type: ignore[assignment]
        current = scan.best["evaluation"]  # type: ignore[assignment]
        decision_log.append(_monthly_refill_decision(scan.best, previous, current, iteration))
    return allocations, current, decision_log, attempts


def _strict_monthly_deep_refine_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
    minimum_active_strategies: int,
    max_iterations: int = 120,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0
    for iteration in range(1, max_iterations + 1):
        scan = _best_monthly_deep_move(
            sets, allocations, current, limits, full_by_id, target_month,
            minimum_active_strategies,
        )
        attempts += scan.attempts
        if scan.best is None:
            break
        previous = current
        allocations = scan.best["allocations"]  # type: ignore[assignment]
        current = scan.best["evaluation"]  # type: ignore[assignment]
        decision_log.append(_monthly_deep_decision(scan.best, previous, current, iteration))
    return allocations, current, decision_log, attempts
