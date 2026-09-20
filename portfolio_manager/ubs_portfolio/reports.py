"""Informes MT5: parseo, construccion de estrategias y recorte mensual."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Sequence

from ..mt5_report import StrategyReport, parse_report

from .symbols import (
    _ascii_text,
    _normalize_symbol,
)
from .models import (
    ClosedTrade,
    PeriodReport,
    RobustStrategySet,
)
from .rows import (
    _parse_report_date,
    _to_float,
)
from .curves import (
    _curve_points_from_closed_trades,
    _merge_curve_points,
    calc_point_dd,
    calc_valley_dd,
    merge_accumulated_curves,
)


def extract_period_info(text: str) -> tuple[str, str, str]:
    match = re.search(
        r"([A-Z0-9]+)\s+\((\d{4}\.\d{2}\.\d{2})\s+-\s+(\d{4}\.\d{2}\.\d{2})\)",
        text,
    )
    if not match:
        raise ValueError("Period info not found")
    return match.group(1), match.group(2), match.group(3)


def build_equity_curve_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[float]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    curve = [0.0]
    total = 0.0
    for trade in ordered:
        total += trade.net_profit
        curve.append(total)
    return curve


def parse_mt5_html_report(html_path: str | Path, period_name: str) -> PeriodReport:
    report = parse_report(Path(html_path))
    return period_report_from_strategy_report(report, period_name)


def period_report_from_strategy_report(report: StrategyReport, period_name: str) -> PeriodReport:
    closed_trades = [
        ClosedTrade(
            open_time=trade.open_time,
            close_time=trade.close_time,
            symbol=report.symbol,
            volume=trade.size,
            profit=trade.profit_loss,
            open_price=trade.open_price,
            close_price=trade.close_price,
        )
        for trade in report.trades
    ]
    curve = build_equity_curve_from_closed_trades(closed_trades)
    pnl_points = _curve_points_from_closed_trades(closed_trades)
    metric_net = _metric_amount(report, "Total Net Profit", "Beneficio Neto")
    net_profit = curve[-1] if metric_net is None else metric_net
    _validate_curve_against_net(curve, net_profit)

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    gross_profit = _metric_amount(report, "Gross Profit", "Beneficio Bruto")
    gross_loss_amount = _metric_amount(report, "Gross Loss", "Perdidas Brutas", "Perdidas Brutas")
    if gross_profit is None or gross_loss_amount is None:
        profits = [trade.net_profit for trade in closed_trades]
        gross_profit = sum(value for value in profits if value > 0)
        gross_loss = sum(value for value in profits if value < 0)
    else:
        gross_loss = -abs(gross_loss_amount)
    profit_factor = _metric_amount(report, "Profit Factor", "Factor de Beneficio")
    if profit_factor is None:
        profit_factor = gross_profit / abs(gross_loss) if gross_loss else (float("inf") if gross_profit else 0.0)

    start_year, end_year = _period_years(report, period_name)
    balance_dd_metric, equity_dd_metric = maximal_drawdowns_from_report(report)
    return PeriodReport(
        period_name=period_name,
        start_year=start_year,
        end_year=end_year,
        symbol=report.symbol,
        timeframe=report.timeframe,
        pnl_curve_001=curve,
        net_profit_001=net_profit,
        valley_dd_001=valley_dd,
        point_dd_001=point_dd,
        profit_factor=float(profit_factor),
        return_dd_ratio=net_profit / max(valley_dd, 1.0),
        trades=len(closed_trades),
        gross_profit=float(gross_profit) if gross_profit is not None else None,
        gross_loss=float(gross_loss) if gross_loss is not None else None,
        closed_trades=closed_trades,
        pnl_points_001=pnl_points,
        source_path=str(report.path),
        start_date=report.period_start,
        end_date=report.period_end,
        balance_dd_metric_001=balance_dd_metric,
        equity_dd_metric_001=equity_dd_metric,
    )


def calc_combined_profit_factor(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> float:
    if (
        report_2020_2024.gross_profit is not None
        and report_2020_2024.gross_loss is not None
        and report_2025_2026.gross_profit is not None
        and report_2025_2026.gross_loss is not None
    ):
        gross_profit = report_2020_2024.gross_profit + report_2025_2026.gross_profit
        gross_loss = report_2020_2024.gross_loss + report_2025_2026.gross_loss
        if gross_loss == 0:
            return float("inf")
        return gross_profit / abs(gross_loss)
    return min(report_2020_2024.profit_factor, report_2025_2026.profit_factor)


def _report_period_bounds(report: PeriodReport) -> tuple[datetime, datetime] | None:
    start = _parse_report_date(report.start_date)
    end = _parse_report_date(report.end_date)
    if start is None and report.start_year:
        start = datetime(int(report.start_year), 1, 1)
    if end is None and report.end_year:
        end = datetime(int(report.end_year), 12, 31, 23, 59, 59)
    if start is None or end is None:
        return None
    return start, end


def _full_history_report_covers_segmented_history(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    full_history_report: PeriodReport | None,
) -> bool:
    """Only trust a continuous report when its declared period covers IS + OOS."""
    if full_history_report is None or not full_history_report.closed_trades:
        return False
    primary_start = _report_period_bounds(report_2020_2024)
    primary_end = _report_period_bounds(report_2025_2026)
    continuous = _report_period_bounds(full_history_report)
    if primary_start is None or primary_end is None or continuous is None:
        return False
    return continuous[0] <= primary_start[0] and continuous[1] >= primary_end[1]


def _chronological_closed_trade_history(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    final_tick_report: PeriodReport | None = None,
    full_history_report: PeriodReport | None = None,
) -> tuple[list[ClosedTrade], int]:
    """Use a continuous history when available, otherwise append a non-overlapping tail."""
    if _full_history_report_covers_segmented_history(
        report_2020_2024, report_2025_2026, full_history_report
    ):
        return sorted(full_history_report.closed_trades, key=lambda trade: trade.close_time), 0

    primary = sorted(
        list(report_2020_2024.closed_trades) + list(report_2025_2026.closed_trades),
        key=lambda trade: trade.close_time,
    )
    if not primary or final_tick_report is None or not final_tick_report.closed_trades:
        return primary, 0

    cutoff = primary[-1].close_time
    tail = [
        trade
        for trade in final_tick_report.closed_trades
        if trade.close_time > cutoff
    ]
    return sorted(primary + tail, key=lambda trade: trade.close_time), len(tail)


def build_robust_strategy_set(
    set_id: str,
    candidate_id: str,
    symbol: str,
    timeframe: str | None,
    strategy_family: str | None,
    robustness_status: str,
    already_used: bool,
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    *,
    set_path: str = "",
    is_report_path: str = "",
    oos_report_path: str = "",
    final_tick_balance_dd_001: float = 0.0,
    final_tick_equity_dd_001: float = 0.0,
    final_tick_net_profit_001: float = 0.0,
    recent_equity_dd_001: float | None = None,
    has_final_tick_performance: bool = False,
    final_tick_source: str = "Final Tick 6M",
    final_tick_report: PeriodReport | None = None,
    final_tick_report_path: str = "",
    full_history_report: PeriodReport | None = None,
    full_history_report_path: str = "",
) -> RobustStrategySet:
    if _normalize_symbol(report_2020_2024.symbol) != _normalize_symbol(report_2025_2026.symbol):
        raise ValueError("Cannot merge reports with different symbols")
    _validate_period_order(report_2020_2024, report_2025_2026)

    if final_tick_report is not None and (
        _normalize_symbol(report_2020_2024.symbol) != _normalize_symbol(final_tick_report.symbol)
    ):
        raise ValueError("Cannot merge Final Tick report with a different symbol")
    if full_history_report is not None and (
        _normalize_symbol(report_2020_2024.symbol) != _normalize_symbol(full_history_report.symbol)
    ):
        raise ValueError("Cannot use a continuous Final Tick report with a different symbol")

    continuous_history = (
        full_history_report
        if _full_history_report_covers_segmented_history(
            report_2020_2024, report_2025_2026, full_history_report
        )
        else None
    )
    closed_history, final_tick_tail_trades = _chronological_closed_trade_history(
        report_2020_2024,
        report_2025_2026,
        final_tick_report,
        continuous_history,
    )
    if closed_history:
        curve_points = _curve_points_from_closed_trades(closed_history)
        curve_2020_2026_001 = [0.0] + [value for _time, value in curve_points]
    else:
        curve_2020_2026_001 = merge_accumulated_curves(
            report_2020_2024.pnl_curve_001,
            report_2025_2026.pnl_curve_001,
        )
        curve_points = _merge_curve_points(report_2020_2024, report_2025_2026)
        if curve_points:
            curve_2020_2026_001 = [0.0] + [value for _time, value in curve_points]

    net_profit_2020_2026_001 = curve_2020_2026_001[-1]
    valley_dd_2020_2026_001 = calc_valley_dd(curve_2020_2026_001)
    point_dd_2020_2026_001 = calc_point_dd(curve_2020_2026_001)
    return_dd_2020_2026 = net_profit_2020_2026_001 / max(valley_dd_2020_2026_001, 1.0)
    trades_2020_2026 = (
        len(closed_history)
        if closed_history
        else report_2020_2024.trades + report_2025_2026.trades
    )
    if closed_history:
        gross_profit = sum(trade.net_profit for trade in closed_history if trade.net_profit > 0)
        gross_loss = sum(trade.net_profit for trade in closed_history if trade.net_profit < 0)
        profit_factor_2020_2026 = (
            gross_profit / abs(gross_loss)
            if gross_loss
            else (float("inf") if gross_profit else 0.0)
        )
    else:
        profit_factor_2020_2026 = calc_combined_profit_factor(report_2020_2024, report_2025_2026)
    if continuous_history is not None:
        drawdown_observations = [(
            "Final Tick continuo 2020-hoy",
            continuous_history.balance_dd_metric_001,
            continuous_history.equity_dd_metric_001,
        )]
    else:
        drawdown_observations = [
            ("2020-2024", report_2020_2024.balance_dd_metric_001, report_2020_2024.equity_dd_metric_001),
            ("2025-2026", report_2025_2026.balance_dd_metric_001, report_2025_2026.equity_dd_metric_001),
        ]
        if final_tick_balance_dd_001 > 0 or final_tick_equity_dd_001 > 0:
            drawdown_observations.append(
                (final_tick_source, float(final_tick_balance_dd_001), float(final_tick_equity_dd_001))
            )
    floating_source, max_balance_dd, max_equity_dd = max(
        drawdown_observations,
        key=lambda item: max(float(item[2]), 0.0),
    )
    # MT5's maximum equity drawdown already represents the worst equity episode.
    # Subtracting a maximum balance DD measured at another timestamp understates risk.
    max_floating_dd = max(float(max_equity_dd), 0.0)

    return RobustStrategySet(
        set_id=str(set_id),
        candidate_id=str(candidate_id),
        symbol=_normalize_symbol(symbol or report_2020_2024.symbol),
        timeframe=timeframe or report_2020_2024.timeframe,
        strategy_family=strategy_family,
        robustness_status=robustness_status,
        already_used=already_used,
        report_2020_2024=report_2020_2024,
        report_2025_2026=report_2025_2026,
        curve_2020_2026_001=curve_2020_2026_001,
        net_profit_2020_2026_001=net_profit_2020_2026_001,
        valley_dd_2020_2026_001=valley_dd_2020_2026_001,
        point_dd_2020_2026_001=point_dd_2020_2026_001,
        profit_factor_2020_2026=profit_factor_2020_2026,
        return_dd_2020_2026=return_dd_2020_2026,
        trades_2020_2026=trades_2020_2026,
        set_path=set_path,
        is_report_path=is_report_path,
        oos_report_path=oos_report_path,
        curve_points_2020_2026_001=curve_points,
        max_balance_dd_001=max(float(max_balance_dd), 0.0),
        max_equity_dd_001=max(float(max_equity_dd), 0.0),
        max_floating_dd_001=max_floating_dd,
        floating_dd_source=floating_source,
        recent_net_profit_001=float(final_tick_net_profit_001),
        recent_equity_dd_001=max(float(
            final_tick_equity_dd_001 if recent_equity_dd_001 is None else recent_equity_dd_001
        ), 0.0),
        has_recent_performance=bool(has_final_tick_performance),
        final_tick_report_path=str(final_tick_report_path),
        full_history_report_path=str(full_history_report_path if continuous_history is not None else ""),
        closed_trades_2020_2026=closed_history,
        final_tick_tail_trades=final_tick_tail_trades,
    )


def slice_strategy_set_to_month(
    strategy: RobustStrategySet,
    target_month: int,
) -> RobustStrategySet:
    """Return the strategy curve restricted to one calendar month across all years.

    The source points are accumulated trade P/L values.  We first recover each
    closed-trade increment, then keep only trades whose close timestamp belongs
    to ``target_month``.  Concatenating those increments chronologically gives a
    seasonal history such as every January available in the base + OOS reports.
    """
    if not 1 <= int(target_month) <= 12:
        raise ValueError("target_month must be between 1 and 12")
    if not strategy.curve_points_2020_2026_001:
        raise ValueError("Strategy has no timestamped trade curve")

    selected: list[tuple[datetime, float]] = []
    previous_value = 0.0
    for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
        increment = float(accumulated_value) - previous_value
        previous_value = float(accumulated_value)
        if timestamp.month == int(target_month):
            selected.append((timestamp, increment))

    total = 0.0
    curve = [0.0]
    points: list[tuple[datetime, float]] = []
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

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    years = tuple(sorted(pnl_by_year))
    positive_years = tuple(year for year in years if pnl_by_year[year] > 0)

    return RobustStrategySet(
        set_id=strategy.set_id,
        candidate_id=strategy.candidate_id,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        strategy_family=strategy.strategy_family,
        robustness_status=strategy.robustness_status,
        already_used=strategy.already_used,
        report_2020_2024=strategy.report_2020_2024,
        report_2025_2026=strategy.report_2025_2026,
        curve_2020_2026_001=curve,
        net_profit_2020_2026_001=total,
        valley_dd_2020_2026_001=valley_dd,
        point_dd_2020_2026_001=point_dd,
        profit_factor_2020_2026=profit_factor,
        return_dd_2020_2026=total / max(valley_dd, 1.0),
        trades_2020_2026=len(selected),
        set_path=strategy.set_path,
        is_report_path=strategy.is_report_path,
        oos_report_path=strategy.oos_report_path,
        curve_points_2020_2026_001=points,
        target_month=int(target_month),
        month_years=years,
        positive_month_years=positive_years,
        max_balance_dd_001=strategy.max_balance_dd_001,
        max_equity_dd_001=strategy.max_equity_dd_001,
        max_floating_dd_001=strategy.max_floating_dd_001,
        floating_dd_source=strategy.floating_dd_source,
        recent_net_profit_001=strategy.recent_net_profit_001,
        recent_equity_dd_001=strategy.recent_equity_dd_001,
        has_recent_performance=strategy.has_recent_performance,
        final_tick_report_path=strategy.final_tick_report_path,
        full_history_report_path=strategy.full_history_report_path,
        closed_trades_2020_2026=[
            trade
            for trade in strategy.closed_trades_2020_2026
            if trade.close_time.month == int(target_month)
        ],
        final_tick_tail_trades=strategy.final_tick_tail_trades,
    )


def slice_strategy_sets_to_month(
    strategies: Sequence[RobustStrategySet],
    target_month: int,
) -> tuple[list[RobustStrategySet], list[str]]:
    """Build seasonal curves and report candidates without timestamped history."""
    sliced: list[RobustStrategySet] = []
    skipped = 0
    for strategy in strategies:
        try:
            sliced.append(slice_strategy_set_to_month(strategy, target_month))
        except ValueError:
            skipped += 1
    warnings = []
    if skipped:
        warnings.append(
            f"{skipped} candidato(s) omitido(s): no tienen curva historica con fechas para el mes objetivo."
        )
    return sliced, warnings


def _metric_amount(report: StrategyReport, *keys: str) -> float | None:
    value = _first_metric(report, *keys)
    if value == "":
        return None
    return _to_float(value)


def maximal_drawdowns_from_report(report: StrategyReport) -> tuple[float, float]:
    """Return maximal balance/equity DD amounts from an MT5 report.

    The closed-trade curve only observes balance changes.  The maximal equity
    drawdown is therefore required explicitly to account for adverse floating
    P/L that recovered before the position was closed.
    """
    balance_dd = _metric_amount(
        report,
        "Balance Drawdown Maximal",
        "Reduccion maxima del balance",
    )
    equity_dd = _metric_amount(
        report,
        "Equity Drawdown Maximal",
        "Reduccion maxima de la equidad",
    )
    if equity_dd is None:
        raise ValueError("Final Tick 6M report has no maximal equity drawdown metric")
    return max(float(balance_dd or 0.0), 0.0), max(float(equity_dd), 0.0)


def _first_metric(report: StrategyReport, *keys: str) -> str:
    normalized = {_ascii_text(key): value for key, value in report.metrics.items()}
    for key in keys:
        value = report.metrics.get(key)
        if value:
            return value
        value = normalized.get(_ascii_text(key))
        if value:
            return value
    return ""


def _validate_curve_against_net(curve: list[float], html_net_profit: float) -> None:
    curve_net_profit = curve[-1] if curve else 0.0
    difference = abs(curve_net_profit - html_net_profit)
    tolerance = max(1.0, abs(html_net_profit) * 0.01)
    if difference > tolerance:
        raise ValueError("Parsed trade curve net profit differs from HTML net profit")


def _period_years(report: StrategyReport, period_name: str) -> tuple[int, int]:
    dates = [_parse_report_date(report.period_start), _parse_report_date(report.period_end)]
    if dates[0] and dates[1]:
        return dates[0].year, dates[1].year
    match = re.search(r"(\d{4})[_-](\d{4})", period_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    year = dates[0].year if dates[0] else 0
    return year, year


def _validate_period_order(report_2020_2024: PeriodReport, report_2025_2026: PeriodReport) -> None:
    first_end = _parse_report_date(report_2020_2024.end_date)
    second_start = _parse_report_date(report_2025_2026.start_date)
    if first_end and second_start and first_end >= second_start:
        raise ValueError("First report period must end before second report period starts")
