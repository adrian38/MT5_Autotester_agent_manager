"""Que incrementos admite la cartera: grupo, simbolo y correlacion."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from .symbols import (
    portfolio_group_key,
    portfolio_symbol_key,
)
from .models import (
    PortfolioEvaluation,
    PortfolioType,
    RobustStrategySet,
)
from .curves import (
    curve_increment_correlation,
    strategy_correlation_pair,
)
from .margin import (
    MarginModel,
    allocations_respect_margin_limit,
)


def _candidate_group_count(sets: list[RobustStrategySet]) -> int:
    return len({portfolio_group_key(strategy.symbol) for strategy in sets})


def _target_group_units_pct_allowed(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
) -> bool:
    if max_units_per_group_pct is None:
        return True
    if _candidate_group_count(sets) <= 1:
        return True
    after_total_units = sum(max(int(value), 0) for value in allocations.values())
    after_target_units = max(int(allocations.get(target_set.set_id, 0)), 0)
    before_total_units = max(after_total_units - 1, 0)
    before_target_units = max(after_target_units - 1, 0)
    if before_total_units <= 0:
        return True

    target_group = portfolio_group_key(target_set.symbol)
    after_group_units = sum(
        max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
        if portfolio_group_key(strategy.symbol) == target_group
    )
    before_group_units = max(after_group_units - 1, 0)
    if before_group_units <= 0:
        return True

    before_active_groups: set[str] = set()
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if strategy.set_id == target_set.set_id:
            units = before_target_units
        if units > 0:
            before_active_groups.add(portfolio_group_key(strategy.symbol))
    if len(before_active_groups) < min(2, _candidate_group_count(sets)):
        return before_group_units < group_unit_cap_bootstrap

    before_group_pct = before_group_units / max(before_total_units, 1)
    if before_group_pct > max_units_per_group_pct + 1e-9:
        return False
    max_units_with_one_step_slack = math.floor(after_total_units * max_units_per_group_pct) + 1
    return after_group_units <= max_units_with_one_step_slack


def can_add_unit(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
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
) -> bool:
    current_units = allocations.get(target_set.set_id, 0)
    if max_units_per_set is not None and current_units >= max_units_per_set:
        return False
    if max_total_units is not None and sum(allocations.values()) + 1 > max_total_units:
        return False
    if max_units_per_symbol is not None:
        target_symbol = portfolio_symbol_key(target_set.symbol)
        symbol_units = sum(
            allocations.get(strategy.set_id, 0)
            for strategy in sets
            if portfolio_symbol_key(strategy.symbol) == target_symbol
        )
        if symbol_units + 1 > max_units_per_symbol:
            return False
    if max_sets_per_symbol is not None:
        target_symbol = portfolio_symbol_key(target_set.symbol)
        active_same_symbol = sum(
            1
            for strategy in sets
            if portfolio_symbol_key(strategy.symbol) == target_symbol and allocations.get(strategy.set_id, 0) > 0
        )
        if current_units == 0 and active_same_symbol >= max_sets_per_symbol:
            return False
    if max_sets_per_group is not None:
        target_group = portfolio_group_key(target_set.symbol)
        active_same_group = sum(
            1
            for strategy in sets
            if portfolio_group_key(strategy.symbol) == target_group and allocations.get(strategy.set_id, 0) > 0
        )
        if current_units == 0 and active_same_group >= max_sets_per_group:
            return False
    temp_allocations = allocations.copy()
    temp_allocations[target_set.set_id] = current_units + 1
    if not _target_group_units_pct_allowed(
        target_set,
        sets,
        temp_allocations,
        max_units_per_group_pct,
        group_unit_cap_bootstrap,
    ):
        return False
    if not allocations_respect_margin_limit(
        sets,
        temp_allocations,
        balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    ):
        return False
    return True


def violates_correlation_limits(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
) -> tuple[bool, str]:
    if allocations.get(target_set.set_id, 0) > 0:
        return False, ""
    if max_pair_corr is None and max_downside_corr is None and max_dd_overlap is None:
        return False, ""

    for active in sets:
        if active.set_id == target_set.set_id or allocations.get(active.set_id, 0) <= 0:
            continue
        pair = strategy_correlation_pair(target_set, active)
        if max_pair_corr is not None and pair.pearson_corr > max_pair_corr:
            return True, f"pair_corr>{max_pair_corr:.2f} vs {Path(active.set_id).name}"
        if max_downside_corr is not None and pair.downside_corr > max_downside_corr:
            return True, f"downside_corr>{max_downside_corr:.2f} vs {Path(active.set_id).name}"
        if max_dd_overlap is not None and pair.dd_overlap > max_dd_overlap:
            return True, f"dd_overlap>{max_dd_overlap:.2f} vs {Path(active.set_id).name}"
    return False, ""


def _allocations_respect_constraints(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None = None,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> bool:
    total_units = 0
    units_by_symbol: dict[str, int] = {}
    active_sets_by_symbol: dict[str, int] = {}
    active_sets_by_group: dict[str, int] = {}

    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        total_units += units
        if max_units_per_set is not None and units > max_units_per_set:
            return False
        if units <= 0:
            continue
        symbol_key = portfolio_symbol_key(strategy.symbol)
        units_by_symbol[symbol_key] = units_by_symbol.get(symbol_key, 0) + units
        active_sets_by_symbol[symbol_key] = active_sets_by_symbol.get(symbol_key, 0) + 1
        group_key = portfolio_group_key(strategy.symbol)
        active_sets_by_group[group_key] = active_sets_by_group.get(group_key, 0) + 1

    if max_total_units is not None and total_units > max_total_units:
        return False
    if max_units_per_symbol is not None:
        for units in units_by_symbol.values():
            if units > max_units_per_symbol:
                return False
    if max_sets_per_symbol is not None:
        for count in active_sets_by_symbol.values():
            if count > max_sets_per_symbol:
                return False
    if max_sets_per_group is not None:
        for count in active_sets_by_group.values():
            if count > max_sets_per_group:
                return False
    if not allocations_respect_margin_limit(
        sets,
        allocations,
        balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    ):
        return False
    return True


def score_increment(
    current: PortfolioEvaluation,
    temp: PortfolioEvaluation,
    current_units_for_set: int,
    portfolio_type: PortfolioType,
) -> float:
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 0:
        return float("-inf")

    valley_cost = temp.valley_dd - current.valley_dd
    point_cost = temp.point_dd - current.point_dd
    epsilon = 1e-9
    valley_cost_pct = max(valley_cost, 0.0) / max(temp.target_valley_dd, epsilon)
    point_cost_pct = (
        max(point_cost, 0.0) / max(temp.target_point_dd, epsilon)
        if temp.enforce_point_dd
        else 0.0
    )
    risk_cost = max(valley_cost_pct, point_cost_pct, epsilon)

    if valley_cost < 0 and (point_cost <= 0 or not temp.enforce_point_dd):
        base_score = gain * 10.0 + abs(valley_cost)
    elif valley_cost <= 0 and (point_cost <= 0 or not temp.enforce_point_dd):
        base_score = gain * 5.0
    else:
        if portfolio_type == PortfolioType.CONSERVATIVE:
            concentration_penalty = 1.0 + current_units_for_set * 0.30
            base_score = gain / risk_cost
        elif portfolio_type == PortfolioType.BALANCED:
            concentration_penalty = 1.0 + current_units_for_set * 0.15
            base_score = gain / risk_cost
        elif portfolio_type == PortfolioType.AGGRESSIVE:
            concentration_penalty = 1.0 + current_units_for_set * 0.05
            base_score = gain * 0.70 + (gain / risk_cost) * 0.30
        else:
            concentration_penalty = 1.0 + current_units_for_set * 0.15
            base_score = gain / risk_cost
        base_score = base_score / concentration_penalty

    if temp.enforce_point_dd and temp.point_usage_pct > 95:
        base_score *= 0.70
    if temp.valley_usage_pct > 98:
        base_score *= 0.85
    return float(base_score)


def _portfolio_active_count(allocations: dict[str, int]) -> int:
    return sum(1 for units in allocations.values() if int(units) > 0)


def _portfolio_corr_allowed(
    evaluation: PortfolioEvaluation,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
) -> bool:
    if max_portfolio_corr is None:
        return True
    curves = list(existing_portfolio_curves or [])
    if not curves:
        return True
    worst_corr = max(
        curve_increment_correlation(evaluation.equity_curve_2020_2026, curve)
        for curve in curves
    )
    return worst_corr <= max_portfolio_corr + 1e-9
