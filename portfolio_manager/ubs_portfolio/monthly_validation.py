"""Strict year, drawdown and dominance validation for monthly portfolios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .curves import calc_point_dd, calc_valley_dd
from .models import RobustStrategySet


@dataclass(frozen=True)
class _StrictMonthlyContext:
    month: int
    years_back: int
    target_valley_dd: float
    target_point_dd: float
    enforce_point_dd: bool


def _portfolio_increments(
    strategies: Sequence[RobustStrategySet], allocations: dict[str, int],
) -> list[tuple[datetime, float]]:
    increments: list[tuple[datetime, float]] = []
    for strategy in strategies:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0 or not strategy.curve_points_2020_2026_001:
            continue
        previous_value = 0.0
        for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
            increment = (float(accumulated_value) - previous_value) * units
            previous_value = float(accumulated_value)
            increments.append((timestamp, increment))
    return increments


def _empty_validation(context: _StrictMonthlyContext) -> dict[str, object]:
    return {
        "passed": False,
        "target_month": context.month,
        "lookback_years": context.years_back,
        "years": [],
        "yearly": [],
        "monthly_dd": {},
        "month_net_by_month": {},
        "best_month": None,
        "best_month_net": 0.0,
        "target_month_net": 0.0,
        "reasons": ["sin trades fechados para validar el portafolio mensual"],
        "enforce_point_dd": bool(context.enforce_point_dd),
    }


def _validation_window(
    increments: list[tuple[datetime, float]], context: _StrictMonthlyContext,
) -> tuple[list[int], list[tuple[datetime, float]]]:
    target_month_years = [
        timestamp.year
        for timestamp, _increment in increments
        if timestamp.month == context.month
    ]
    latest_year = max(target_month_years) if target_month_years else max(
        timestamp.year for timestamp, _increment in increments
    )
    earliest_year = latest_year - context.years_back + 1
    years = list(range(earliest_year, latest_year + 1))
    return years, [
        (timestamp, increment)
        for timestamp, increment in increments
        if earliest_year <= timestamp.year <= latest_year
    ]


def _curve_metrics(increments: list[tuple[datetime, float]]) -> tuple[float, float, float]:
    total = 0.0
    curve = [0.0]
    for _timestamp, increment in increments:
        total += increment
        curve.append(total)
    return total, calc_valley_dd(curve), calc_point_dd(curve)


def _dd_passed(valley_dd: float, point_dd: float, context: _StrictMonthlyContext) -> bool:
    return (
        valley_dd <= context.target_valley_dd + 1e-9
        and (not context.enforce_point_dd or point_dd <= context.target_point_dd + 1e-9)
    )


def _year_validation(
    year: int,
    increments: list[tuple[datetime, float]],
    context: _StrictMonthlyContext,
    reasons: list[str],
) -> dict[str, object]:
    selected = [
        (timestamp, increment)
        for timestamp, increment in increments
        if timestamp.year == year and timestamp.month == context.month
    ]
    selected.sort(key=lambda item: item[0])
    total, valley_dd, point_dd = _curve_metrics(selected)
    passed = bool(selected) and total > 0 and _dd_passed(valley_dd, point_dd, context)
    if not passed:
        if not selected:
            reasons.append(f"{year}: sin trades en mes {context.month:02d}")
        elif total <= 0:
            reasons.append(f"{year}: net {total:,.2f} <= 0 en mes {context.month:02d}")
        elif valley_dd > context.target_valley_dd + 1e-9:
            reasons.append(f"{year}: DD valle {valley_dd:,.2f} > {context.target_valley_dd:,.2f}")
        elif context.enforce_point_dd and point_dd > context.target_point_dd + 1e-9:
            reasons.append(f"{year}: DD puntual {point_dd:,.2f} > {context.target_point_dd:,.2f}")
    return {
        "year": year, "trades": len(selected), "net": total,
        "valley_dd": valley_dd, "point_dd": point_dd, "passed": passed,
    }


def _month_validation(
    month_no: int,
    increments: list[tuple[datetime, float]],
    context: _StrictMonthlyContext,
    reasons: list[str],
) -> dict[str, object]:
    selected = [
        (timestamp, increment)
        for timestamp, increment in increments
        if timestamp.month == month_no
    ]
    selected.sort(key=lambda item: item[0])
    total, valley_dd, point_dd = _curve_metrics(selected)
    passed_dd = _dd_passed(valley_dd, point_dd, context)
    if not passed_dd:
        label = f"mes {month_no:02d}"
        if valley_dd > context.target_valley_dd + 1e-9:
            reasons.append(f"{label}: DD valle {valley_dd:,.2f} > {context.target_valley_dd:,.2f}")
        if context.enforce_point_dd and point_dd > context.target_point_dd + 1e-9:
            reasons.append(f"{label}: DD puntual {point_dd:,.2f} > {context.target_point_dd:,.2f}")
    return {
        "trades": len(selected), "net": total, "valley_dd": valley_dd,
        "point_dd": point_dd, "passed_dd": passed_dd,
    }


def _monthly_validations(
    increments: list[tuple[datetime, float]],
    context: _StrictMonthlyContext,
    reasons: list[str],
) -> tuple[dict[int, float], dict[str, dict[str, object]]]:
    month_net_by_month = {item: 0.0 for item in range(1, 13)}
    for timestamp, increment in increments:
        month_net_by_month[timestamp.month] += increment
    monthly_dd = {
        f"{month_no:02d}": _month_validation(month_no, increments, context, reasons)
        for month_no in range(1, 13)
    }
    return month_net_by_month, monthly_dd


def _dominant_month(
    month_net_by_month: dict[int, float],
    context: _StrictMonthlyContext,
    reasons: list[str],
) -> tuple[int, float, float]:
    best_month, best_month_net = max(
        month_net_by_month.items(),
        key=lambda item: (item[1], -abs(item[0] - context.month)),
    )
    target_month_net = month_net_by_month.get(context.month, 0.0)
    if best_month != context.month:
        reasons.append(
            f"mes {context.month:02d} no es el mejor de los ultimos {context.years_back} años "
            f"(mejor {best_month:02d}: {best_month_net:,.2f} vs {target_month_net:,.2f})"
        )
    return best_month, best_month_net, target_month_net


def validate_strict_monthly_portfolio(
    strategies: Sequence[RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    lookback_years: int = 5,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    """Validate a monthly portfolio year-by-year, by DD caps, and by dominance."""
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")
    context = _StrictMonthlyContext(
        month, max(int(lookback_years), 1), float(target_valley_dd),
        float(target_point_dd), bool(enforce_point_dd),
    )
    increments = _portfolio_increments(strategies, allocations)
    if not increments:
        return _empty_validation(context)
    years, increments = _validation_window(increments, context)
    reasons: list[str] = []
    yearly = [_year_validation(year, increments, context, reasons) for year in years]
    month_net_by_month, monthly_dd = _monthly_validations(increments, context, reasons)
    best_month, best_month_net, target_month_net = _dominant_month(
        month_net_by_month, context, reasons,
    )
    return {
        "passed": not reasons, "target_month": month,
        "lookback_years": context.years_back, "years": years, "yearly": yearly,
        "monthly_dd": monthly_dd,
        "month_net_by_month": {
            f"{key:02d}": value for key, value in sorted(month_net_by_month.items())
        },
        "best_month": best_month, "best_month_net": best_month_net,
        "target_month_net": target_month_net,
        "target_valley_dd": context.target_valley_dd,
        "target_point_dd": context.target_point_dd,
        "enforce_point_dd": context.enforce_point_dd, "reasons": reasons,
    }
