"""Busqueda: construccion greedy, busqueda local y multi-start."""

from __future__ import annotations

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
from .constraints import (
    _allocations_respect_constraints,
    _portfolio_active_count,
    _portfolio_corr_allowed,
    _target_group_units_pct_allowed,
    can_add_unit,
    score_increment,
    violates_correlation_limits,
)


def build_portfolio_greedy(
    sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    initial_allocations: dict[str, int] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    prefer_breadth_below_minimum: bool = False,
    fixed_set_ids: Sequence[str] | None = None,
    allow_fixed_reductions_for_repair: bool = False,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
    target_valley_dd = capital * valley_dd_pct / 100.0
    target_point_dd = capital * point_dd_pct / 100.0
    allocations = {
        strategy.set_id: max(int((initial_allocations or {}).get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    if not _allocations_respect_constraints(
        sets,
        allocations,
        max_units_per_set,
        max_total_units,
        max_units_per_symbol,
        max_sets_per_symbol,
        max_sets_per_group,
        margin_balance,
        max_margin_pct,
        margin_profile,
        stock_leverage,
        default_leverage,
        stock_contract_size,
        default_contract_size,
    ):
        raise ValueError("Initial portfolio allocations violate configured limits")
    current = evaluate_portfolio(
        sets,
        allocations,
        target_valley_dd,
        target_point_dd,
        max_daily_dd,
        enforce_point_dd,
        daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(current) and not allow_fixed_reductions_for_repair:
        raise ValueError("Initial portfolio allocations violate DD limits")
    decision_log: list[OptimizationDecision] = []
    step = sum(allocations.values())
    max_steps = max_total_units if max_total_units is not None else 10000
    correlation_rejections = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    fixed_ids = {str(set_id) for set_id in (fixed_set_ids or ())}

    while step < max_steps:
        best_candidate: dict[str, object] | None = None
        best_repair_candidate: dict[str, object] | None = None
        blocked_by_risk = False
        # Por que se quedo sin incrementos en ESTE paso. El motivo lo decide el
        # recuento, no una cadena fija: el `stop_reason` culpaba siempre al DD y
        # mando dos veces la investigacion al sitio equivocado cuando quien
        # bloqueaba era la correlacion con el DD al 30% del presupuesto.
        step_blocks = {"dd": 0, "pair_corr": 0, "portfolio_corr": 0, "caps": 0}
        # Mientras faltan huecos por abrir, el objetivo es cuantas caben, no
        # cuanto rinde la siguiente. Eligiendo por rentabilidad se gasta la
        # holgura en la mejor candidata y las demas ya no entran, asi que el
        # resultado depende de lo gordo que sea el pool: darle mas candidatas
        # producia MENOS incorporaciones.
        opening_slots = bool(
            prefer_breadth_below_minimum
            and minimum_active_strategies is not None
            and current.active_strategies < minimum_active_strategies
        )
        for strategy in sets:
            if strategy.set_id in fixed_ids:
                continue
            if (
                maximum_active_strategies is not None
                and current.active_strategies >= maximum_active_strategies
                and allocations.get(strategy.set_id, 0) <= 0
            ):
                continue
            if (
                minimum_active_strategies is not None
                and current.active_strategies < minimum_active_strategies
                and allocations.get(strategy.set_id, 0) > 0
            ):
                # During repair, fill the missing strategy slots before adding
                # more risk to strategies that are already active.
                continue
            if not can_add_unit(
                target_set=strategy,
                sets=sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                step_blocks["caps"] += 1
                continue
            rejected_by_corr, corr_reason = violates_correlation_limits(
                strategy,
                sets,
                allocations,
                max_pair_corr,
                max_downside_corr,
                max_dd_overlap,
            )
            if rejected_by_corr:
                correlation_rejections += 1
                step_blocks["pair_corr"] += 1
                decision_log.append(
                    OptimizationDecision(
                        step=step + 1,
                        action="reject_corr",
                        set_id=strategy.set_id,
                        from_set_id=None,
                        to_set_id=None,
                        gain=0.0,
                        valley_cost=0.0,
                        point_cost=0.0,
                        score=float("-inf"),
                        portfolio_net_profit_after=current.total_net_profit,
                        portfolio_valley_dd_after=current.valley_dd,
                        portfolio_point_dd_after=current.point_dd,
                        reason=corr_reason,
                    )
                )
                continue
            temp_allocations = allocations.copy()
            temp_allocations[strategy.set_id] += 1
            temp = evaluate_portfolio(
                sets,
                temp_allocations,
                target_valley_dd,
                target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            )
            if _evaluation_violates_dd_limits(temp):
                blocked_by_risk = True
                step_blocks["dd"] += 1
                if allow_fixed_reductions_for_repair:
                    current_violation = _evaluation_violation_ratio(current)
                    temp_violation = _evaluation_violation_ratio(temp)
                    if temp_violation < current_violation - 1e-9:
                        repair_score = (current_violation - temp_violation) * 1_000_000_000.0
                        repair_score += max(temp.total_net_profit - current.total_net_profit, 0.0)
                        if (
                            best_repair_candidate is None
                            or repair_score > float(best_repair_candidate["score"])
                        ):
                            best_repair_candidate = {
                                "set": strategy,
                                "allocations": temp_allocations,
                                "evaluation": temp,
                                "score": repair_score,
                                "reason": "Replacement increment reduced the DD violation",
                            }
                continue
            if max_portfolio_corr is not None and portfolio_curves:
                worst_portfolio_corr = max(
                    curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                    for curve in portfolio_curves
                )
                if worst_portfolio_corr > max_portfolio_corr:
                    blocked_by_risk = True
                    correlation_rejections += 1
                    step_blocks["portfolio_corr"] += 1
                    decision_log.append(
                        OptimizationDecision(
                            step=step + 1,
                            action="reject_portfolio_corr",
                            set_id=strategy.set_id,
                            from_set_id=None,
                            to_set_id=None,
                            gain=0.0,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=float("-inf"),
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason=f"portfolio_corr>{max_portfolio_corr:.2f}",
                        )
                    )
                    continue
            score = score_increment(current, temp, allocations[strategy.set_id], portfolio_type)
            if score == float("-inf"):
                continue
            selection_key = (
                (-(temp.valley_dd - current.valley_dd), score)
                if opening_slots else (score,)
            )
            previous_key = (
                best_candidate.get("selection_key", (float(best_candidate["score"]),))
                if best_candidate is not None else None
            )
            if previous_key is None or selection_key > previous_key:
                best_candidate = {
                    "set": strategy,
                    "allocations": temp_allocations,
                    "evaluation": temp,
                    "score": score,
                    "selection_key": selection_key,
                    "reason": (
                        "Cheapest valid +0.01 increment while opening required slots"
                        if opening_slots else "Best valid +0.01 increment"
                    ),
                }

        if best_candidate is None and best_repair_candidate is not None:
            best_candidate = best_repair_candidate

        if best_candidate is None and allow_fixed_reductions_for_repair:
            current_violation = _evaluation_violation_ratio(current)
            missing_required_strategy = (
                minimum_active_strategies is not None
                and current.active_strategies < minimum_active_strategies
            )
            if current_violation > 1.0 or (missing_required_strategy and blocked_by_risk):
                best_reduction: tuple[float, float, RobustStrategySet, dict[str, int], PortfolioEvaluation] | None = None
                for strategy in sets:
                    if strategy.set_id not in fixed_ids or allocations.get(strategy.set_id, 0) <= 1:
                        continue
                    temp_allocations = allocations.copy()
                    temp_allocations[strategy.set_id] -= 1
                    temp = evaluate_portfolio(
                        sets,
                        temp_allocations,
                        target_valley_dd,
                        target_point_dd,
                        max_daily_dd,
                        enforce_point_dd,
                        daily_dd_full_history,
                    )
                    temp_violation = _evaluation_violation_ratio(temp)
                    if temp_violation >= current_violation - 1e-9:
                        continue
                    choice = (temp_violation, -temp.total_net_profit, strategy, temp_allocations, temp)
                    if best_reduction is None or choice[:2] < best_reduction[:2]:
                        best_reduction = choice
                if best_reduction is not None:
                    _violation, _negative_net, reduced_set, allocations, current = best_reduction
                    step = sum(allocations.values())
                    decision_log.append(
                        OptimizationDecision(
                            step=len(decision_log) + 1,
                            action="reduce_unit_for_repair",
                            set_id=reduced_set.set_id,
                            from_set_id=reduced_set.set_id,
                            to_set_id=None,
                            gain=-reduced_set.net_profit_2020_2026_001,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=-current_violation,
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason="Minimum existing-lot reduction required to make portfolio repair feasible",
                        )
                    )
                    continue

        if best_candidate is None:
            blocks = [
                f"{label} ({step_blocks[key]})"
                for key, label in (
                    ("dd", "DD limits"),
                    ("pair_corr", "correlation limits"),
                    ("portfolio_corr", "portfolio correlation"),
                    ("caps", "unit/group/margin caps"),
                )
                if step_blocks[key]
            ]
            stop_reason = (
                "No valid +0.01 increment: " + "; ".join(blocks)
                if blocks
                else "No valid +0.01 increment left in the candidate pool"
            )
            break

        selected_set = best_candidate["set"]
        assert isinstance(selected_set, RobustStrategySet)
        previous = current
        allocations = best_candidate["allocations"]  # type: ignore[assignment]
        current = best_candidate["evaluation"]  # type: ignore[assignment]
        step += 1
        decision_log.append(
            OptimizationDecision(
                step=step,
                action="add_unit",
                set_id=selected_set.set_id,
                from_set_id=None,
                to_set_id=None,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_candidate["score"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason=str(best_candidate.get("reason") or "Best valid +0.01 increment"),
            )
        )
    else:
        stop_reason = "Max optimizer iterations reached"

    return allocations, current, decision_log, stop_reason, correlation_rejections


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    max_iterations: int = 1000,
    protected_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    decision_log: list[OptimizationDecision] = []
    iteration = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    protected_ids = {str(set_id) for set_id in (protected_set_ids or ())}
    while iteration < max_iterations:
        iteration += 1
        best_move: dict[str, object] | None = None
        for from_set in sets:
            if allocations.get(from_set.set_id, 0) <= 0:
                continue
            if from_set.set_id in protected_ids and allocations.get(from_set.set_id, 0) <= 1:
                continue
            for to_set in sets:
                if from_set.set_id == to_set.set_id:
                    continue
                temp_allocations = allocations.copy()
                temp_allocations[from_set.set_id] -= 1
                temp_allocations[to_set.set_id] += 1
                if minimum_active_strategies is not None:
                    active_count = sum(1 for units in temp_allocations.values() if units > 0)
                    if active_count < minimum_active_strategies:
                        continue
                if not _allocations_respect_constraints(
                    sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    to_set,
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(to_set.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[to_set.set_id] = 0
                    rejected_by_corr, _corr_reason = violates_correlation_limits(
                        to_set,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    target_valley_dd,
                    target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    worst_portfolio_corr = max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    )
                    if worst_portfolio_corr > max_portfolio_corr:
                        continue
                gain = temp.total_net_profit - current.total_net_profit
                if gain <= 0:
                    continue
                if best_move is None or gain > float(best_move["gain"]):
                    best_move = {
                        "from_set": from_set,
                        "to_set": to_set,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert isinstance(from_set, RobustStrategySet)
        assert isinstance(to_set, RobustStrategySet)
        previous = current
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
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
        )
    return allocations, current, decision_log


def improve_with_multi_start_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    *,
    restarts: int,
    perturbations: int = 2,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    if restarts <= 0 or perturbations <= 0 or len(sets) < 2:
        return allocations, current, [], 0

    best_allocations = allocations.copy()
    best = current
    best_log: list[OptimizationDecision] = []
    valid_restarts = 0
    portfolio_curves = list(existing_portfolio_curves or [])

    for restart in range(restarts):
        rng = random.Random(104729 + restart * 7919 + len(sets) * 17)
        trial_allocations = allocations.copy()
        trial = current
        perturb_log: list[OptimizationDecision] = []

        for perturbation in range(perturbations):
            active = [item for item in sets if trial_allocations.get(item.set_id, 0) > 0]
            targets = list(sets)
            moves = [(source, target) for source in active for target in targets if source.set_id != target.set_id]
            rng.shuffle(moves)
            accepted_move = False
            for source, target in moves:
                temp_allocations = trial_allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] += 1
                if not _allocations_respect_constraints(
                    sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    target,
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if trial_allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected, _reason = violates_correlation_limits(
                        target,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    target_valley_dd,
                    target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    if max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    ) > max_portfolio_corr:
                        continue
                perturb_log.append(
                    OptimizationDecision(
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
                )
                trial_allocations = temp_allocations
                trial = temp
                accepted_move = True
                break
            if not accepted_move:
                break

        if not perturb_log:
            continue
        valid_restarts += 1
        trial_allocations, trial, local_log = improve_with_local_search(
            sets=sets,
            allocations=trial_allocations,
            current=trial,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            max_iterations=200,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        if trial.total_net_profit > best.total_net_profit + 1e-9:
            best_allocations = trial_allocations
            best = trial
            best_log = perturb_log + local_log

    return best_allocations, best, best_log, valid_restarts


def _deep_refine_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    minimum_active_strategies: int | None,
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None,
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
    margin_balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | MarginModel | None,
    stock_leverage: float,
    default_leverage: float,
    stock_contract_size: float,
    default_contract_size: float,
    max_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
    max_iterations: int = 160,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    working_sets = list({strategy.set_id: strategy for strategy in sets}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in working_sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0

    for iteration in range(1, max_iterations + 1):
        best_move: dict[str, object] | None = None
        ordered_targets = sorted(
            working_sets,
            key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
            reverse=True,
        )

        for target in ordered_targets:
            attempts += 1
            if can_add_unit(
                target_set=target,
                sets=working_sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                if allocations.get(target.set_id, 0) <= 0:
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        working_sets,
                        allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp_allocations = allocations.copy()
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                temp = evaluate_portfolio(
                    working_sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and not _evaluation_violates_dd_limits(temp)
                    and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_add_unit",
                        "from_set": None,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

            active_sources = [
                source for source in working_sets if allocations.get(source.set_id, 0) > 0
            ]
            for source in active_sources:
                if source.set_id == target.set_id:
                    continue
                attempts += 1
                temp_allocations = allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                if (
                    minimum_active_strategies is not None
                    and _portfolio_active_count(temp_allocations) < minimum_active_strategies
                ):
                    continue
                if not _allocations_respect_constraints(
                    working_sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    target,
                    working_sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        working_sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    working_sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and not _evaluation_violates_dd_limits(temp)
                    and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_swap_unit",
                        "from_set": source,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        previous = current
        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert to_set is not None and isinstance(to_set, RobustStrategySet)
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
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
        )

    return allocations, current, decision_log, attempts


def _active_unit_allocations(allocations: dict[str, int]) -> dict[str, int]:
    return {str(set_id): int(units) for set_id, units in allocations.items() if int(units) > 0}
