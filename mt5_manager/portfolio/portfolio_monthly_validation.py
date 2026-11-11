"""Leave-one-year-out validation for experimental monthly portfolios."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from math import ceil
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    RobustStrategySet,
    calc_point_dd,
    calc_valley_dd,
    evaluate_portfolio,
)


EXPERIMENTAL_LOYO_YEARS = 5
OptimizeExactPool = Callable[..., PortfolioResult]


def _active_allocation_ids(result: PortfolioResult) -> set[str]:
    return {
        str(allocation.set_id)
        for allocation in result.allocations
        if int(allocation.units) > 0
    }


def _strategy_for_years(
    strategy: RobustStrategySet,
    years: set[int],
) -> RobustStrategySet | None:
    source_points = list(strategy.curve_points_2020_2026_001 or ())
    if not source_points:
        return None
    selected: list[tuple[Any, float]] = []
    previous_value = 0.0
    for timestamp, accumulated_value in source_points:
        increment = float(accumulated_value) - previous_value
        previous_value = float(accumulated_value)
        if int(timestamp.year) in years:
            selected.append((timestamp, increment))
    if not selected:
        return None

    total = 0.0
    curve = [0.0]
    points: list[tuple[Any, float]] = []
    pnl_by_year: dict[int, float] = {}
    gross_profit = 0.0
    gross_loss = 0.0
    for timestamp, increment in selected:
        total += increment
        curve.append(total)
        points.append((timestamp, total))
        pnl_by_year[timestamp.year] = pnl_by_year.get(timestamp.year, 0.0) + increment
        if increment >= 0:
            gross_profit += increment
        else:
            gross_loss += increment

    clone = copy.copy(strategy)
    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    clone.curve_2020_2026_001 = curve
    clone.curve_points_2020_2026_001 = points
    clone.net_profit_2020_2026_001 = total
    clone.valley_dd_2020_2026_001 = valley_dd
    clone.point_dd_2020_2026_001 = point_dd
    clone.profit_factor_2020_2026 = (
        gross_profit / abs(gross_loss)
        if gross_loss < 0
        else float("inf") if gross_profit > 0 else 0.0
    )
    clone.return_dd_2020_2026 = total / max(valley_dd, 1.0)
    clone.trades_2020_2026 = len(selected)
    clone.month_years = tuple(sorted(pnl_by_year))
    clone.positive_month_years = tuple(
        year for year in sorted(pnl_by_year) if pnl_by_year[year] > 0
    )
    if hasattr(strategy, "closed_trades_2020_2026"):
        clone.closed_trades_2020_2026 = [
            trade
            for trade in strategy.closed_trades_2020_2026
            if int(trade.close_time.year) in years
        ]
    return clone


@dataclass(frozen=True)
class _LoyoContext:
    result: PortfolioResult
    candidate_pool: list[RobustStrategySet]
    target_month: int
    optimizer_kwargs: dict[str, Any]
    years: list[int]
    final_ids: set[str]
    optimize_exact_pool: OptimizeExactPool


def _fold_optimizer_kwargs(context: _LoyoContext, training_years: set[int]) -> dict[str, Any]:
    fold_kwargs = dict(context.optimizer_kwargs)
    fold_kwargs["search_restarts"] = 0
    fold_kwargs["run_local_search"] = False
    original_minimum = int(fold_kwargs.get("min_trades_2020_2026") or 0)
    fold_kwargs["min_trades_2020_2026"] = max(
        int(ceil(original_minimum * len(training_years) / len(context.years))),
        1,
    )
    return fold_kwargs


def _evaluate_fold(context: _LoyoContext, held_out_year: int) -> dict[str, object]:
    training_years = set(context.years) - {held_out_year}
    training_sets = [
        subset
        for strategy in context.candidate_pool
        if (subset := _strategy_for_years(strategy, training_years)) is not None
    ]
    held_out_sets = [
        subset
        for strategy in context.candidate_pool
        if (subset := _strategy_for_years(strategy, {held_out_year})) is not None
    ]
    fold_result = context.optimize_exact_pool(
        training_sets,
        training_sets,
        target_month=context.target_month,
        strict_yearly_month_validation=False,
        use_deep_refinement=False,
        optimizer_kwargs=_fold_optimizer_kwargs(context, training_years),
    )
    allocations = {
        str(allocation.set_id): int(allocation.units)
        for allocation in fold_result.allocations
        if int(allocation.units) > 0
    }
    held_out = evaluate_portfolio(
        held_out_sets,
        allocations,
        float(context.result.target_valley_dd),
        float(context.result.target_point_dd),
        None,
        bool(context.optimizer_kwargs.get("enforce_point_dd", False)),
        False,
    )
    fold_ids = set(allocations)
    union = context.final_ids | fold_ids
    overlap = len(context.final_ids & fold_ids) / len(union) if union else 0.0
    dd_passed = float(held_out.valley_dd) <= float(context.result.target_valley_dd) + 1e-9
    dd_passed = dd_passed and (
        not bool(context.optimizer_kwargs.get("enforce_point_dd", False))
        or float(held_out.point_dd) <= float(context.result.target_point_dd) + 1e-9
    )
    return {
        "year": held_out_year,
        "status": "ok",
        "net": float(held_out.total_net_profit),
        "valley_dd": float(held_out.valley_dd),
        "point_dd": float(held_out.point_dd),
        "positive": float(held_out.total_net_profit) > 0.0,
        "dd_passed": dd_passed,
        "selection_overlap": overlap,
        "active_strategies": len(fold_ids),
    }


def _loyo_fold(context: _LoyoContext, held_out_year: int) -> dict[str, object]:
    try:
        return _evaluate_fold(context, held_out_year)
    except Exception as exc:
        return {
            "year": held_out_year,
            "status": "failed",
            "error": str(exc),
            "positive": False,
            "dd_passed": False,
            "selection_overlap": 0.0,
        }


def _loyo_summary(years: list[int], folds: list[dict[str, object]]) -> dict[str, object]:
    successful = [fold for fold in folds if fold["status"] == "ok"]
    positive_folds = sum(bool(fold["positive"]) for fold in successful)
    dd_passed_folds = sum(bool(fold["dd_passed"]) for fold in successful)
    mean_overlap = (
        sum(float(fold["selection_overlap"]) for fold in successful) / len(successful)
        if successful else 0.0
    )
    required_positive = max(int(ceil(len(years) * 0.6)), 1)
    passed = (
        len(successful) == len(years)
        and positive_folds >= required_positive
        and dd_passed_folds == len(years)
        and mean_overlap >= 0.35
    )
    return {
        "status": "completed",
        "years": years,
        "folds": folds,
        "successful_folds": len(successful),
        "positive_folds": positive_folds,
        "dd_passed_folds": dd_passed_folds,
        "mean_selection_overlap": mean_overlap,
        "passed": passed,
    }


def leave_one_year_out_audit(
    result: PortfolioResult,
    candidate_pool: list[RobustStrategySet],
    *,
    target_month: int,
    optimizer_kwargs: dict[str, Any],
    optimize_exact_pool: OptimizeExactPool,
) -> dict[str, object]:
    years = sorted({
        int(timestamp.year)
        for strategy in candidate_pool
        for timestamp, _value in (strategy.curve_points_2020_2026_001 or ())
    })[-EXPERIMENTAL_LOYO_YEARS:]
    if len(years) < 3:
        return {"status": "insufficient_history", "years": years, "folds": [], "passed": False}
    context = _LoyoContext(
        result, candidate_pool, target_month, optimizer_kwargs, years,
        _active_allocation_ids(result), optimize_exact_pool,
    )
    return _loyo_summary(years, [_loyo_fold(context, year) for year in years])
