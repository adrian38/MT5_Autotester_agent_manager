"""Busqueda: construccion greedy, busqueda local y multi-start."""

from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Sequence

from .models import (
    OptimizationDecision,
    PortfolioEvaluation,
    PortfolioType,
    RobustStrategySet,
)
from .curves import curve_increment_correlation
from .selection import score_set_for_portfolio
from .evaluation import (
    _evaluation_violates_dd_limits,
    _evaluation_violation_ratio,
    evaluate_portfolio,
)
from .margin import MarginModel
from .limits import SearchLimits
from .constraints import (
    _allocations_respect_constraints,
    _portfolio_active_count,
    _portfolio_corr_allowed,
    _target_group_units_pct_allowed,
    can_add_unit,
    score_increment,
    violates_correlation_limits,
)


from .greedy_increment import (
    _caps_allow,
    _IncrementRules,
    _StepScan,
    _slot_allows_increment,
    _note_dd_block,
    _portfolio_corr_rejects,
    _keep_best_candidate,
    _consider_increment,
    _repair_reduction,
    _greedy_stop_reason,
    _added_unit_decision,
    _greedy_start,
    _scan_step,
    _run_greedy_steps,
    build_portfolio_greedy,
)
from .greedy_swap import (
    _swap_respects_caps,
    _swap_respects_correlation,
    _portfolio_corr_allows,
    _swap_candidate,
    _best_swap,
    _swap_decision,
    improve_with_local_search,
    _perturbation_move,
    _perturb_decision,
    _perturbed_trial,
    improve_with_multi_start_search,
)

@dataclass
class _DeepScan:
    """El mejor movimiento profundo encontrado y cuantos se han probado."""

    best: dict[str, object] | None = None
    attempts: int = 0

    def offer(self, move: dict[str, object]) -> None:
        """Se queda con el movimiento si gana mas que el actual."""
        if self.best is None or float(move["gain"]) > float(self.best["gain"]):
            self.best = move


def _deep_gain(
    temp: PortfolioEvaluation,
    current: PortfolioEvaluation,
    limits: SearchLimits,
) -> float | None:
    """Cuanto gana el movimiento, o ``None`` si no es valido.

    La optimizacion profunda solo acepta mejoras reales que ademas respeten DD y
    correlacion de cartera; no se relaja ninguno de los dos para ganar mas.
    """
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 1e-9:
        return None
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allowed(temp, limits.existing_portfolio_curves, limits.max_portfolio_corr):
        return None
    return gain


def _deep_add_move(
    target: RobustStrategySet,
    working_sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    scan: _DeepScan,
) -> None:
    """Prueba anadir una unidad a la candidata."""
    if not _caps_allow(working_sets, target, allocations, limits):
        return
    if allocations.get(target.set_id, 0) <= 0:
        rejected_by_corr, _reason = violates_correlation_limits(
            target,
            working_sets,
            allocations,
            limits.max_pair_corr,
            limits.max_downside_corr,
            limits.max_dd_overlap,
        )
        if rejected_by_corr:
            return
    temp_allocations = allocations.copy()
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    temp = evaluate_portfolio(
        working_sets,
        temp_allocations,
        current.target_valley_dd,
        current.target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    gain = _deep_gain(temp, current, limits)
    if gain is None:
        return
    scan.offer({
        "action": "deep_add_unit",
        "from_set": None,
        "to_set": target,
        "allocations": temp_allocations,
        "evaluation": temp,
        "gain": gain,
    })


def _deep_swap_move(
    source: RobustStrategySet,
    target: RobustStrategySet,
    working_sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    minimum_active_strategies: int | None,
    scan: _DeepScan,
) -> None:
    """Prueba mover una unidad de una estrategia activa a la candidata."""
    temp_allocations = allocations.copy()
    temp_allocations[source.set_id] -= 1
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    if (
        minimum_active_strategies is not None
        and _portfolio_active_count(temp_allocations) < minimum_active_strategies
    ):
        return
    if not _swap_respects_caps(working_sets, target, temp_allocations, limits, None):
        return
    if not _swap_respects_correlation(working_sets, target, allocations, temp_allocations, limits):
        return
    temp = evaluate_portfolio(
        working_sets,
        temp_allocations,
        current.target_valley_dd,
        current.target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    gain = _deep_gain(temp, current, limits)
    if gain is None:
        return
    scan.offer({
        "action": "deep_swap_unit",
        "from_set": source,
        "to_set": target,
        "allocations": temp_allocations,
        "evaluation": temp,
        "gain": gain,
    })


def _best_deep_move(
    working_sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    minimum_active_strategies: int | None,
) -> _DeepScan:
    """Recorre las candidatas por puntuacion y se queda con el mejor movimiento."""
    scan = _DeepScan()
    ordered_targets = sorted(
        working_sets,
        key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
        reverse=True,
    )
    for target in ordered_targets:
        scan.attempts += 1
        _deep_add_move(target, working_sets, allocations, current, limits, scan)
        active_sources = [
            source for source in working_sets if allocations.get(source.set_id, 0) > 0
        ]
        for source in active_sources:
            if source.set_id == target.set_id:
                continue
            scan.attempts += 1
            _deep_swap_move(
                source, target, working_sets, allocations, current, limits,
                minimum_active_strategies, scan,
            )
    return scan


def _deep_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    iteration: int,
) -> OptimizationDecision:
    """La linea del registro que explica el movimiento profundo aplicado."""
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
        reason="Optimizacion profunda: movimiento validado contra DD, margen y correlacion",
    )


def _deep_refine_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    minimum_active_strategies: int | None,
    max_iterations: int = 160,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    working_sets = list({strategy.set_id: strategy for strategy in sets}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in working_sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0
    for iteration in range(1, max_iterations + 1):
        scan = _best_deep_move(
            working_sets, allocations, current, limits, minimum_active_strategies,
        )
        attempts += scan.attempts
        if scan.best is None:
            break
        previous = current
        allocations = scan.best["allocations"]  # type: ignore[assignment]
        current = scan.best["evaluation"]  # type: ignore[assignment]
        decision_log.append(_deep_decision(scan.best, previous, current, iteration))
    return allocations, current, decision_log, attempts


def _active_unit_allocations(allocations: dict[str, int]) -> dict[str, int]:
    return {str(set_id): int(units) for set_id, units in allocations.items() if int(units) > 0}
