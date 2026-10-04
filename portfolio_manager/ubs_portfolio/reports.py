"""Informes MT5: parseo, construccion de estrategias y recorte mensual."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any, Sequence

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


def _validate_report_symbols(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    final_tick_report: PeriodReport | None,
    full_history_report: PeriodReport | None,
) -> None:
    """Los cuatro informes tienen que hablar del mismo simbolo."""
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


def _merged_equity_curve(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    closed_history: list[Any],
) -> tuple[list[float], list[Any]]:
    """La curva 2020-2026 y sus puntos, de las operaciones si las hay.

    Sin historial cerrado se cae a unir las dos curvas acumuladas, que es lo
    unico que dan los informes antiguos.
    """
    if closed_history:
        curve_points = _curve_points_from_closed_trades(closed_history)
        return [0.0] + [value for _time, value in curve_points], curve_points
    curve = merge_accumulated_curves(
        report_2020_2024.pnl_curve_001,
        report_2025_2026.pnl_curve_001,
    )
    curve_points = _merge_curve_points(report_2020_2024, report_2025_2026)
    if curve_points:
        curve = [0.0] + [value for _time, value in curve_points]
    return curve, curve_points


def _combined_profit_factor(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    closed_history: list[Any],
) -> float:
    if not closed_history:
        return calc_combined_profit_factor(report_2020_2024, report_2025_2026)
    gross_profit = sum(trade.net_profit for trade in closed_history if trade.net_profit > 0)
    gross_loss = sum(trade.net_profit for trade in closed_history if trade.net_profit < 0)
    if gross_loss:
        return gross_profit / abs(gross_loss)
    return float("inf") if gross_profit else 0.0


def _worst_drawdown_observation(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    continuous_history: PeriodReport | None,
    final_tick_balance_dd_001: float,
    final_tick_equity_dd_001: float,
    final_tick_source: str,
) -> tuple[str, float, float]:
    """El peor episodio de equity observado, y de que tramo sale.

    Con historial continuo manda ese tramo y nada mas: mezclarlo con los dos
    segmentados contaria dos veces el mismo periodo.
    """
    if continuous_history is not None:
        observations = [(
            "Final Tick continuo 2020-hoy",
            continuous_history.balance_dd_metric_001,
            continuous_history.equity_dd_metric_001,
        )]
    else:
        observations = [
            ("2020-2024", report_2020_2024.balance_dd_metric_001, report_2020_2024.equity_dd_metric_001),
            ("2025-2026", report_2025_2026.balance_dd_metric_001, report_2025_2026.equity_dd_metric_001),
        ]
        if final_tick_balance_dd_001 > 0 or final_tick_equity_dd_001 > 0:
            observations.append(
                (final_tick_source, float(final_tick_balance_dd_001), float(final_tick_equity_dd_001))
            )
    return max(observations, key=lambda item: max(float(item[2]), 0.0))


@dataclass(frozen=True)
class _FinalTickInputs:
    """Lo que aporta el Final Tick a una estrategia: riesgo, rendimiento y rutas.

    Viaja junto porque son doce argumentos opcionales que nadie usa por
    separado; la firma publica los sigue aceptando sueltos.
    """

    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    balance_dd_001: float = 0.0
    equity_dd_001: float = 0.0
    net_profit_001: float = 0.0
    recent_equity_dd_001: float | None = None
    has_performance: bool = False
    source: str = "Final Tick 6M"
    report: PeriodReport | None = None
    report_path: str = ""
    full_history_report: PeriodReport | None = None
    full_history_report_path: str = ""


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
    return _robust_strategy_set(
        set_id, candidate_id, symbol, timeframe, strategy_family,
        robustness_status, already_used, report_2020_2024, report_2025_2026,
        _FinalTickInputs(
            set_path=set_path,
            is_report_path=is_report_path,
            oos_report_path=oos_report_path,
            balance_dd_001=final_tick_balance_dd_001,
            equity_dd_001=final_tick_equity_dd_001,
            net_profit_001=final_tick_net_profit_001,
            recent_equity_dd_001=recent_equity_dd_001,
            has_performance=has_final_tick_performance,
            source=final_tick_source,
            report=final_tick_report,
            report_path=final_tick_report_path,
            full_history_report=full_history_report,
            full_history_report_path=full_history_report_path,
        ),
    )


def _curve_fields(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    curve: list[float],
    curve_points: list[Any],
    closed_history: list[Any],
) -> dict[str, Any]:
    """Todo lo que se mide sobre la curva 2020-2026."""
    net_profit = curve[-1]
    valley_dd = calc_valley_dd(curve)
    return {
        "curve_2020_2026_001": curve,
        "curve_points_2020_2026_001": curve_points,
        "net_profit_2020_2026_001": net_profit,
        "valley_dd_2020_2026_001": valley_dd,
        "point_dd_2020_2026_001": calc_point_dd(curve),
        "return_dd_2020_2026": net_profit / max(valley_dd, 1.0),
        "profit_factor_2020_2026": _combined_profit_factor(
            report_2020_2024, report_2025_2026, closed_history
        ),
        "trades_2020_2026": (
            len(closed_history)
            if closed_history
            else report_2020_2024.trades + report_2025_2026.trades
        ),
    }


def _drawdown_fields(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    continuous_history: PeriodReport | None,
    final_tick: _FinalTickInputs,
) -> dict[str, Any]:
    floating_source, max_balance_dd, max_equity_dd = _worst_drawdown_observation(
        report_2020_2024, report_2025_2026, continuous_history,
        final_tick.balance_dd_001, final_tick.equity_dd_001, final_tick.source,
    )
    return {
        "max_balance_dd_001": max(float(max_balance_dd), 0.0),
        "max_equity_dd_001": max(float(max_equity_dd), 0.0),
        # MT5's maximum equity drawdown already represents the worst equity
        # episode. Subtracting a maximum balance DD measured at another
        # timestamp understates risk.
        "max_floating_dd_001": max(float(max_equity_dd), 0.0),
        "floating_dd_source": floating_source,
    }


def _final_tick_fields(
    final_tick: _FinalTickInputs, continuous_history: PeriodReport | None,
) -> dict[str, Any]:
    return {
        "set_path": final_tick.set_path,
        "is_report_path": final_tick.is_report_path,
        "oos_report_path": final_tick.oos_report_path,
        "recent_net_profit_001": float(final_tick.net_profit_001),
        "recent_equity_dd_001": max(float(
            final_tick.equity_dd_001
            if final_tick.recent_equity_dd_001 is None
            else final_tick.recent_equity_dd_001
        ), 0.0),
        "has_recent_performance": bool(final_tick.has_performance),
        "final_tick_report_path": str(final_tick.report_path),
        # La ruta del tramo continuo solo se guarda si de verdad cubre el
        # historial segmentado; si no, el informe existe pero no se uso.
        "full_history_report_path": str(
            final_tick.full_history_report_path if continuous_history is not None else ""
        ),
    }


def _robust_strategy_set(
    set_id: str,
    candidate_id: str,
    symbol: str,
    timeframe: str | None,
    strategy_family: str | None,
    robustness_status: str,
    already_used: bool,
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    final_tick: _FinalTickInputs,
) -> RobustStrategySet:
    _validate_report_symbols(
        report_2020_2024, report_2025_2026, final_tick.report, final_tick.full_history_report
    )
    continuous_history = (
        final_tick.full_history_report
        if _full_history_report_covers_segmented_history(
            report_2020_2024, report_2025_2026, final_tick.full_history_report
        )
        else None
    )
    closed_history, final_tick_tail_trades = _chronological_closed_trade_history(
        report_2020_2024, report_2025_2026, final_tick.report, continuous_history,
    )
    curve, curve_points = _merged_equity_curve(
        report_2020_2024, report_2025_2026, closed_history
    )
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
        closed_trades_2020_2026=closed_history,
        final_tick_tail_trades=final_tick_tail_trades,
        **_curve_fields(report_2020_2024, report_2025_2026, curve, curve_points, closed_history),
        **_drawdown_fields(report_2020_2024, report_2025_2026, continuous_history, final_tick),
        **_final_tick_fields(final_tick, continuous_history),
    )


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
