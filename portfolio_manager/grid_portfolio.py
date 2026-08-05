"""Grid-specific UBS portfolio optimization.

The shared UBS optimizer supplies selection, correlation, closed-curve and
margin primitives. Grid portfolios dimension every strategy in executable lot
steps and bind the result only by the greater of the combined closed DD and the
worst standalone floating DD. Internal EA loss limits never replace that
portfolio-level rule.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .ubs_portfolio import (
    OptimizationDecision,
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
    UnusedSetInfo,
    bootstrap_valley_drawdown,
    evaluate_portfolio,
    optimize_portfolio,
    portfolio_group_summary,
    portfolio_margin_summary,
)


def grid_floating_dd_001(strategy: RobustStrategySet) -> float:
    """Return the open/floating part of equity DD for one tested 0.01 unit."""
    return max(
        float(strategy.max_equity_dd_001) - float(strategy.max_balance_dd_001),
        0.0,
    )


def _grid_evaluation(
    strategies: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
) -> tuple[Any, float, float]:
    evaluation = evaluate_portfolio(
        strategies,
        allocations,
        target_valley_dd,
        target_point_dd,
        enforce_point_dd=False,
    )
    by_id = {strategy.set_id: strategy for strategy in strategies}
    floating_max = max((
        grid_floating_dd_001(by_id[set_id]) * max(int(units), 0)
        for set_id, units in allocations.items()
        if units > 0 and set_id in by_id
    ), default=0.0)
    valley = max(float(evaluation.closed_valley_dd), float(floating_max))
    return evaluation, float(floating_max), valley


def _prune_to_grid_valley(
    strategies: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
) -> tuple[dict[str, int], Any, float, list[OptimizationDecision], list[str]]:
    current = {
        set_id: max(int(units), 0)
        for set_id, units in allocations.items()
        if int(units) > 0
    }
    evaluation, floating_max, valley = _grid_evaluation(
        strategies, current, target_valley_dd, target_point_dd,
    )
    decisions: list[OptimizationDecision] = []
    removed: list[str] = []
    step = 1
    while current and valley > target_valley_dd + 1e-9:
        choices: list[tuple[float, float, str, int, Any, float, float]] = []
        for set_id in current:
            trial = dict(current)
            next_units = trial[set_id] - 1
            if next_units > 0:
                trial[set_id] = next_units
            else:
                trial.pop(set_id)
            trial_eval, trial_floating, trial_valley = _grid_evaluation(
                strategies, trial, target_valley_dd, target_point_dd,
            )
            relief = valley - trial_valley
            lost_profit = evaluation.total_net_profit - trial_eval.total_net_profit
            score = lost_profit / relief if relief > 1e-9 else float("inf")
            choices.append((
                score, lost_profit, set_id, next_units,
                trial_eval, trial_floating, trial_valley,
            ))
        score, lost_profit, set_id, next_units, trial_eval, trial_floating, trial_valley = min(
            choices,
            key=lambda item: (item[0], item[1], item[2]),
        )
        if next_units > 0:
            current[set_id] = next_units
        else:
            current.pop(set_id)
            removed.append(set_id)
        decisions.append(OptimizationDecision(
            step=step,
            action="reduce_for_grid_valley" if next_units > 0 else "remove_for_grid_valley",
            set_id=set_id,
            from_set_id=set_id,
            to_set_id=None,
            gain=-float(lost_profit),
            valley_cost=float(trial_valley - valley),
            point_cost=float(trial_eval.point_dd - evaluation.point_dd),
            score=0.0 if score == float("inf") else -float(score),
            portfolio_net_profit_after=float(trial_eval.total_net_profit),
            portfolio_valley_dd_after=float(trial_valley),
            portfolio_point_dd_after=float(trial_eval.point_dd),
            reason=(
                "Reduccion de una unidad para respetar el valle grid del portafolio."
                if next_units > 0
                else "Retirada para respetar el valle grid del portafolio."
            ),
        ))
        evaluation, floating_max, valley = trial_eval, trial_floating, trial_valley
        step += 1
    if not current:
        raise ValueError(
            "Ninguna estrategia grid respeta el valle máximo entre DD flotante y DD cerrado"
        )
    return current, evaluation, floating_max, decisions, removed


def optimize_grid_portfolio(
    raw_sets: list[RobustStrategySet],
    *,
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
    **kwargs: Any,
) -> PortfolioResult:
    """Build a variable-size Grid portfolio constrained by max(floating DD, closed DD)."""
    optimizer_kwargs = dict(kwargs)
    optimizer_kwargs.update({
        "enforce_point_dd": False,
        "max_daily_dd": None,
    })
    # The shared evaluator already takes max(closed DD, floating buffer). Feed
    # it the Grid-specific floating measurement without changing UBS/monthly.
    risk_sets = [
        replace(
            strategy,
            max_floating_dd_001=grid_floating_dd_001(strategy),
            # Four-stage acceptance is the Grid quality gate. Do not add the
            # shared UBS recent-recovery rejection as a hidden fifth filter.
            has_recent_performance=False,
        )
        for strategy in raw_sets
    ]
    reserve_pct = min(max(float(optimizer_kwargs.get("dd_reserve_pct", 0.0)), 0.0), 99.0)
    target_grid_valley = (
        float(capital) * float(valley_dd_pct) / 100.0 * (1.0 - reserve_pct / 100.0)
    )
    # Candidate ranking must not evict the low-DD set before construction. This
    # was leaving an empty result even when a valid Grid strategy existed.
    risk_sets = [
        strategy for strategy in risk_sets
        if float(strategy.max_floating_dd_001) <= target_grid_valley + 1e-9
    ]
    if not risk_sets:
        raise ValueError(
            "Ninguna estrategia grid respeta el valle máximo entre DD flotante y DD cerrado"
        )
    result = optimize_portfolio(
        risk_sets,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
        portfolio_type=portfolio_type,
        **optimizer_kwargs,
    )
    initial = {
        allocation.set_id: int(allocation.units)
        for allocation in result.allocations
        if allocation.units > 0
    }
    allocations, evaluation, floating_max, decisions, removed = _prune_to_grid_valley(
        raw_sets,
        initial,
        result.target_valley_dd,
        result.target_point_dd,
    )
    original_by_id = {strategy.set_id: strategy for strategy in raw_sets}
    kept_allocations = []
    for allocation in result.allocations:
        if allocation.set_id not in allocations:
            continue
        original = original_by_id[allocation.set_id]
        units = int(allocations[allocation.set_id])
        original_units = max(int(allocation.units), 1)
        unit_lot = float(allocation.lot) / original_units
        kept_allocations.append(replace(
            allocation,
            units=units,
            lot=unit_lot * units,
            net_profit_contribution=float(original.net_profit_2020_2026_001) * units,
            standalone_valley_dd=max(
                float(original.valley_dd_2020_2026_001),
                grid_floating_dd_001(original),
            ) * units,
            standalone_point_dd=float(original.point_dd_2020_2026_001) * units,
            margin_required=float(allocation.margin_required) / original_units * units,
            margin_pct=float(allocation.margin_pct) / original_units * units,
            max_balance_dd_001=float(original.max_balance_dd_001),
            max_equity_dd_001=float(original.max_equity_dd_001),
            floating_dd_source=str(original.floating_dd_source),
            standalone_floating_dd=grid_floating_dd_001(original) * units,
        ))
    warnings = [
        warning for warning in result.warnings
        if "flotante maximo individual" not in warning
        and "DD diario visual" not in warning
    ]
    warnings.append(
        "Valle Grid UBS = max(DD flotante máximo "
        f"{floating_max:.2f}, DD cerrado combinado {evaluation.closed_valley_dd:.2f}) "
        f"= {max(floating_max, float(evaluation.closed_valley_dd)):.2f}."
    )
    warnings.append(
        "Los límites internos de pérdida o equity del EA no se usan para aceptar, rechazar ni dimensionar estrategias."
    )
    if removed:
        warnings.append(
            f"Se retiraron {len(removed)} estrategia(s) para que el valle grid agregado cupiera en el límite."
        )
    margin_summary: dict[str, object] = {}
    if optimizer_kwargs.get("margin_balance") is not None and optimizer_kwargs.get("max_margin_pct") is not None:
        margin_summary = portfolio_margin_summary(
            raw_sets,
            allocations,
            balance=float(optimizer_kwargs["margin_balance"]),
            max_margin_pct=float(optimizer_kwargs["max_margin_pct"]),
            margin_profile=optimizer_kwargs.get("margin_profile"),
            stock_leverage=float(optimizer_kwargs.get("stock_leverage", 20.0)),
            default_leverage=float(optimizer_kwargs.get("default_leverage", 500.0)),
            stock_contract_size=float(optimizer_kwargs.get("stock_contract_size", 100.0)),
            default_contract_size=float(optimizer_kwargs.get("default_contract_size", 1.0)),
        )
    actual_valley = max(float(evaluation.closed_valley_dd), float(floating_max))
    stress = bootstrap_valley_drawdown(
        evaluation.equity_curve_2020_2026,
        nominal_valley_dd_limit=float(capital) * float(valley_dd_pct) / 100.0,
        effective_valley_dd_limit=result.target_valley_dd,
    )
    removed_unused = [
        UnusedSetInfo(set_id=set_id, symbol="", score=0.0, reason="removed_for_grid_valley")
        for set_id in removed
    ]
    return replace(
        result,
        allocations=kept_allocations,
        equity_curve_2020_2026=evaluation.equity_curve_2020_2026,
        total_net_profit=float(evaluation.total_net_profit),
        actual_valley_dd=actual_valley,
        actual_closed_valley_dd=float(evaluation.closed_valley_dd),
        floating_dd_buffer=floating_max,
        actual_point_dd=float(evaluation.point_dd),
        valley_usage_pct=actual_valley / result.target_valley_dd * 100.0 if result.target_valley_dd > 0 else 0.0,
        point_usage_pct=float(evaluation.point_usage_pct),
        total_lot=round(sum(allocation.lot for allocation in kept_allocations), 2),
        total_units=sum(allocations.values()),
        active_strategies=len(kept_allocations),
        stop_reason=result.stop_reason + "; validación de valle grid conservador",
        warnings=warnings,
        decision_log=result.decision_log + decisions,
        unused_sets=result.unused_sets + removed_unused,
        group_summary=portfolio_group_summary(raw_sets, allocations),
        stress_bootstrap=stress,
        margin_summary=margin_summary,
        max_daily_dd=0.0,
        target_daily_dd=None,
        daily_dd_summary={},
        daily_dd_full_history=False,
        enforce_point_dd=False,
    )
