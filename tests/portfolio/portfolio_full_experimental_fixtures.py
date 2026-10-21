"""Reusable fixtures for full-history experimental portfolio tests."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from mt5_manager.portfolio_service import _underrepresented_recent_allocation_ids


def allocation(
    set_id: str,
    *,
    units: int = 1,
    recent: float = 10.0,
    contribution: float = 100.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        set_id=set_id,
        units=units,
        net_profit_contribution=contribution,
        recent_net_profit_001=recent,
        has_recent_performance=True,
    )


def built_result(allocations, *, profit: float | None = None) -> SimpleNamespace:
    allocations = list(allocations)
    return SimpleNamespace(
        allocations=allocations,
        total_net_profit=float(
            profit
            if profit is not None
            else sum(item.net_profit_contribution for item in allocations)
        ),
        active_strategies=len([item for item in allocations if item.units > 0]),
        actual_valley_dd=10.0,
        target_valley_dd=1000.0,
        target_point_dd=1000.0,
        total_units=sum(item.units for item in allocations),
        warnings=[],
        seasonal_coverage={},
        seasonal_validation={},
    )


def recent_fillers(result) -> set[str]:
    return _underrepresented_recent_allocation_ids(result, 5.0)


def strategy(index: int) -> SimpleNamespace:
    increments = [
        float(20 + index % 5),
        float(24 + index % 7),
        float(28 + index % 3),
    ]
    total = 0.0
    curve = [0.0]
    points = []
    for offset, increment in enumerate(increments):
        total += increment
        curve.append(total)
        points.append((datetime(2023 + offset, 6, 15), total))
    in_sample = SimpleNamespace(
        start_year=2020,
        end_year=2024,
        net_profit_001=60.0 + index,
        return_dd_ratio=2.0 + index % 4,
    )
    out_of_sample = SimpleNamespace(
        start_year=2025,
        end_year=2026,
        net_profit_001=35.0 + index,
        return_dd_ratio=1.5 + index % 3,
    )
    return SimpleNamespace(
        set_id=f"set-{index}",
        symbol=("EURUSD", "XAUUSD", "US500")[index % 3],
        robustness_status="accepted",
        already_used=False,
        report_2020_2024=in_sample,
        report_2025_2026=out_of_sample,
        curve_2020_2026_001=curve,
        curve_points_2020_2026_001=points,
        net_profit_2020_2026_001=total,
        return_dd_2020_2026=float(1 + index % 7),
        profit_factor_2020_2026=float(1.1 + (index % 5) / 10),
        valley_dd_2020_2026_001=float(20 + index % 11),
        max_floating_dd_001=float(index % 13),
        trades_2020_2026=130,
        has_recent_performance=True,
        recent_net_profit_001=float(10 + index % 5),
        recent_equity_dd_001=float(3 + index % 4),
        target_month=None,
        month_years=(),
        positive_month_years=(),
    )


def result_for(pool) -> SimpleNamespace:
    allocations = [
        SimpleNamespace(
            set_id=item.set_id,
            units=1,
            net_profit_contribution=item.net_profit_2020_2026_001,
            recent_net_profit_001=item.recent_net_profit_001,
            has_recent_performance=True,
        )
        for item in pool[: max(len(pool) // 2, 1)]
    ]
    return SimpleNamespace(
        allocations=allocations,
        total_net_profit=sum(
            item.net_profit_2020_2026_001 for item in pool
        ),
        active_strategies=len(allocations),
        actual_valley_dd=10.0,
        target_valley_dd=1000.0,
        target_point_dd=1000.0,
        total_units=len(allocations),
        warnings=[],
        seasonal_coverage={},
        seasonal_validation={},
    )
