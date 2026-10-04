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

def _swap_respects_caps(
    sets: list[RobustStrategySet],
    to_set: RobustStrategySet,
    temp_allocations: dict[str, int],
    limits: SearchLimits,
    minimum_active_strategies: int | None,
) -> bool:
    """Si la cartera resultante del intercambio sigue dentro de los topes."""
    if minimum_active_strategies is not None:
        active_count = sum(1 for units in temp_allocations.values() if units > 0)
        if active_count < minimum_active_strategies:
            return False
    if not _allocations_respect_constraints(
        sets,
        temp_allocations,
        limits.max_units_per_set,
        limits.max_total_units,
        limits.max_units_per_symbol,
        limits.max_sets_per_symbol,
        limits.max_sets_per_group,
        limits.margin_balance,
        limits.max_margin_pct,
        limits.margin_profile,
        limits.stock_leverage,
        limits.default_leverage,
        limits.stock_contract_size,
        limits.default_contract_size,
    ):
        return False
    return _target_group_units_pct_allowed(
        to_set,
        sets,
        temp_allocations,
        limits.max_units_per_group_pct,
        limits.group_unit_cap_bootstrap,
    )


def _swap_respects_correlation(
    sets: list[RobustStrategySet],
    to_set: RobustStrategySet,
    allocations: dict[str, int],
    temp_allocations: dict[str, int],
    limits: SearchLimits,
) -> bool:
    """La correlacion solo se comprueba si el intercambio estrena estrategia."""
    if allocations.get(to_set.set_id, 0) > 0:
        return True
    corr_allocations = temp_allocations.copy()
    corr_allocations[to_set.set_id] = 0
    rejected_by_corr, _corr_reason = violates_correlation_limits(
        to_set,
        sets,
        corr_allocations,
        limits.max_pair_corr,
        limits.max_downside_corr,
        limits.max_dd_overlap,
    )
    return not rejected_by_corr


def _portfolio_corr_allows(
    temp: PortfolioEvaluation,
    limits: SearchLimits,
    portfolio_curves: list[Sequence[float]],
) -> bool:
    """Si la curva resultante no se parece demasiado a una cartera existente."""
    if limits.max_portfolio_corr is None or not portfolio_curves:
        return True
    worst_portfolio_corr = max(
        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
        for curve in portfolio_curves
    )
    return worst_portfolio_corr <= limits.max_portfolio_corr


def _swap_candidate(
    from_set: RobustStrategySet,
    to_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    *,
    portfolio_curves: list[Sequence[float]],
    minimum_active_strategies: int | None,
    target_valley_dd: float,
    target_point_dd: float,
) -> dict[str, object] | None:
    """Mueve una unidad de una estrategia a otra. ``None`` si no vale la pena."""
    if from_set.set_id == to_set.set_id:
        return None
    temp_allocations = allocations.copy()
    temp_allocations[from_set.set_id] -= 1
    temp_allocations[to_set.set_id] += 1
    if not _swap_respects_caps(sets, to_set, temp_allocations, limits, minimum_active_strategies):
        return None
    if not _swap_respects_correlation(sets, to_set, allocations, temp_allocations, limits):
        return None
    temp = evaluate_portfolio(
        sets,
        temp_allocations,
        target_valley_dd,
        target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allows(temp, limits, portfolio_curves):
        return None
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 0:
        return None
    return {
        "from_set": from_set,
        "to_set": to_set,
        "allocations": temp_allocations,
        "evaluation": temp,
        "gain": gain,
    }


def _best_swap(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    *,
    protected_ids: set[str],
    portfolio_curves: list[Sequence[float]],
    minimum_active_strategies: int | None,
    target_valley_dd: float,
    target_point_dd: float,
) -> dict[str, object] | None:
    """El intercambio que mas beneficio gana sin romper ningun limite."""
    best_move: dict[str, object] | None = None
    for from_set in sets:
        if allocations.get(from_set.set_id, 0) <= 0:
            continue
        if from_set.set_id in protected_ids and allocations.get(from_set.set_id, 0) <= 1:
            continue
        for to_set in sets:
            move = _swap_candidate(
                from_set, to_set, sets, allocations, current, limits,
                portfolio_curves=portfolio_curves,
                minimum_active_strategies=minimum_active_strategies,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
            )
            if move is None:
                continue
            if best_move is None or float(move["gain"]) > float(best_move["gain"]):
                best_move = move
    return best_move


def _swap_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    step: int,
) -> OptimizationDecision:
    """La linea del registro que explica el intercambio aplicado."""
    from_set = best_move["from_set"]
    to_set = best_move["to_set"]
    assert isinstance(from_set, RobustStrategySet)
    assert isinstance(to_set, RobustStrategySet)
    return OptimizationDecision(
        step=step,
        action="swap_unit",
        set_id=None,
        from_set_id=from_set.set_id,
        to_set_id=to_set.set_id,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=current.total_net_profit - previous.total_net_profit,
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason="Local search improved total net profit",
    )


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    max_iterations: int = 1000,
    protected_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    decision_log: list[OptimizationDecision] = []
    portfolio_curves = list(limits.existing_portfolio_curves or [])
    protected_ids = {str(set_id) for set_id in (protected_set_ids or ())}
    for iteration in range(1, max_iterations + 1):
        best_move = _best_swap(
            sets, allocations, current, limits,
            protected_ids=protected_ids,
            portfolio_curves=portfolio_curves,
            minimum_active_strategies=minimum_active_strategies,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
        )
        if best_move is None:
            break
        previous = current
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(_swap_decision(best_move, previous, current, iteration))
    return allocations, current, decision_log


def _perturbation_move(
    source: RobustStrategySet,
    target: RobustStrategySet,
    sets: list[RobustStrategySet],
    trial_allocations: dict[str, int],
    limits: SearchLimits,
    *,
    portfolio_curves: list[Sequence[float]],
    target_valley_dd: float,
    target_point_dd: float,
) -> tuple[dict[str, int], PortfolioEvaluation] | None:
    """Movimiento de perturbacion valido. A diferencia de la busqueda local, no
    se le exige mejorar: la gracia es salir del optimo local."""
    temp_allocations = trial_allocations.copy()
    temp_allocations[source.set_id] -= 1
    temp_allocations[target.set_id] += 1
    if not _swap_respects_caps(sets, target, temp_allocations, limits, None):
        return None
    if not _swap_respects_correlation(sets, target, trial_allocations, temp_allocations, limits):
        return None
    temp = evaluate_portfolio(
        sets,
        temp_allocations,
        target_valley_dd,
        target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allows(temp, limits, portfolio_curves):
        return None
    return temp_allocations, temp


def _perturb_decision(
    source: RobustStrategySet,
    target: RobustStrategySet,
    trial: PortfolioEvaluation,
    temp: PortfolioEvaluation,
    perturbation: int,
    restart: int,
) -> OptimizationDecision:
    """La linea del registro que explica una perturbacion aceptada."""
    return OptimizationDecision(
        step=perturbation + 1,
        action="multi_start_perturb",
        set_id=None,
        from_set_id=source.set_id,
        to_set_id=target.set_id,
        gain=temp.total_net_profit - trial.total_net_profit,
        valley_cost=temp.valley_dd - trial.valley_dd,
        point_cost=temp.point_dd - trial.point_dd,
        score=temp.total_net_profit - trial.total_net_profit,
        portfolio_net_profit_after=temp.total_net_profit,
        portfolio_valley_dd_after=temp.valley_dd,
        portfolio_point_dd_after=temp.point_dd,
        reason=f"Multi-start perturbation {restart + 1}",
    )


def _perturbed_trial(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    rng: random.Random,
    *,
    perturbations: int,
    restart: int,
    portfolio_curves: list[Sequence[float]],
    target_valley_dd: float,
    target_point_dd: float,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    """Sacude la cartera con movimientos validos hasta que ninguno lo sea."""
    trial_allocations = allocations.copy()
    trial = current
    perturb_log: list[OptimizationDecision] = []
    for perturbation in range(perturbations):
        active = [item for item in sets if trial_allocations.get(item.set_id, 0) > 0]
        moves = [
            (source, target)
            for source in active for target in sets
            if source.set_id != target.set_id
        ]
        rng.shuffle(moves)
        accepted_move = False
        for source, target in moves:
            moved = _perturbation_move(
                source, target, sets, trial_allocations, limits,
                portfolio_curves=portfolio_curves,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
            )
            if moved is None:
                continue
            temp_allocations, temp = moved
            perturb_log.append(
                _perturb_decision(source, target, trial, temp, perturbation, restart)
            )
            trial_allocations, trial = temp_allocations, temp
            accepted_move = True
            break
        if not accepted_move:
            break
    return trial_allocations, trial, perturb_log


def improve_with_multi_start_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    *,
    restarts: int,
    perturbations: int = 2,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    if restarts <= 0 or perturbations <= 0 or len(sets) < 2:
        return allocations, current, [], 0

    best_allocations = allocations.copy()
    best = current
    best_log: list[OptimizationDecision] = []
    valid_restarts = 0
    portfolio_curves = list(limits.existing_portfolio_curves or [])

    for restart in range(restarts):
        trial_allocations, trial, perturb_log = _perturbed_trial(
            sets, allocations, current, limits,
            random.Random(104729 + restart * 7919 + len(sets) * 17),
            perturbations=perturbations,
            restart=restart,
            portfolio_curves=portfolio_curves,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
        )
        if not perturb_log:
            continue
        valid_restarts += 1
        trial_allocations, trial, local_log = improve_with_local_search(
            sets=sets,
            allocations=trial_allocations,
            current=trial,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            limits=limits,
            max_iterations=200,
        )
        if trial.total_net_profit > best.total_net_profit + 1e-9:
            best_allocations = trial_allocations
            best = trial
            best_log = perturb_log + local_log

    return best_allocations, best, best_log, valid_restarts
