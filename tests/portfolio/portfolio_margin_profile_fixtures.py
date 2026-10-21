from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    build_robust_strategy_set,
)


def period(symbol: str, name: str, start_year: int, end_year: int, price: float) -> PeriodReport:
    trade = ClosedTrade(
        open_time=datetime(start_year, 1, 1),
        close_time=datetime(start_year, 1, 2),
        symbol=symbol,
        volume=0.01,
        profit=10.0,
        open_price=price,
        close_price=price,
    )
    return replace(
        PeriodReport(
            period_name=name,
            start_year=start_year,
            end_year=end_year,
            symbol=symbol,
            timeframe="H1",
            pnl_curve_001=[0.0, 100.0],
            net_profit_001=100.0,
            valley_dd_001=0.0,
            point_dd_001=0.0,
            profit_factor=2.0,
            return_dd_ratio=100.0,
            trades=100,
            balance_dd_metric_001=10.0,
            equity_dd_metric_001=10.0,
        ),
        closed_trades=[trade],
    )


def strategy(symbol: str, price: float):
    return build_robust_strategy_set(
        set_id=f"{symbol}.set",
        candidate_id=symbol,
        symbol=symbol,
        timeframe="H1",
        strategy_family="test",
        robustness_status="accepted",
        already_used=False,
        report_2020_2024=period(symbol, "2020_2024", 2020, 2024, price),
        report_2025_2026=period(symbol, "2025_2026", 2025, 2026, price),
    )



