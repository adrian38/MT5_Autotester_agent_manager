"""Recortar una estrategia al mes objetivo sin perder su auditoria de riesgo.

Encima de `reports`: reutiliza sus metricas de curva y devuelve el mismo
`RobustStrategySet`, con la curva sustituida por la estacional.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from .curves import calc_point_dd, calc_valley_dd
from .models import RobustStrategySet


def _month_trade_increments(
    strategy: RobustStrategySet, target_month: int,
) -> list[tuple[datetime, float]]:
    """El incremento de cada operacion cerrada en ese mes, en orden.

    Los puntos de origen son P/L acumulado: primero se recupera el incremento
    de cada operacion y despues se queda solo con las del mes objetivo, para
    que concatenarlos de un historial estacional (todos los eneros, por
    ejemplo) y no un recorte del acumulado.
    """
    selected: list[tuple[datetime, float]] = []
    previous_value = 0.0
    for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
        increment = float(accumulated_value) - previous_value
        previous_value = float(accumulated_value)
        if timestamp.month == int(target_month):
            selected.append((timestamp, increment))
    return selected

def _month_curve_fields(selected: list[tuple[datetime, float]]) -> dict[str, Any]:
    """Curva, metricas y años del mes objetivo, a partir de sus incrementos."""
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
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    years = tuple(sorted(pnl_by_year))
    return {
        "curve_2020_2026_001": curve,
        "curve_points_2020_2026_001": points,
        "net_profit_2020_2026_001": total,
        "valley_dd_2020_2026_001": valley_dd,
        "point_dd_2020_2026_001": calc_point_dd(curve),
        "profit_factor_2020_2026": profit_factor,
        "return_dd_2020_2026": total / max(valley_dd, 1.0),
        "trades_2020_2026": len(selected),
        "month_years": years,
        "positive_month_years": tuple(year for year in years if pnl_by_year[year] > 0),
    }

def _carried_strategy_fields(strategy: RobustStrategySet) -> dict[str, Any]:
    """Lo que el recorte mensual conserva tal cual: identidad, rutas y riesgo.

    El mensual **no** pierde la auditoria de riesgo al recortar la curva.
    """
    return {
        "set_id": strategy.set_id,
        "candidate_id": strategy.candidate_id,
        "symbol": strategy.symbol,
        "timeframe": strategy.timeframe,
        "strategy_family": strategy.strategy_family,
        "robustness_status": strategy.robustness_status,
        "already_used": strategy.already_used,
        "report_2020_2024": strategy.report_2020_2024,
        "report_2025_2026": strategy.report_2025_2026,
        "set_path": strategy.set_path,
        "is_report_path": strategy.is_report_path,
        "oos_report_path": strategy.oos_report_path,
        "max_balance_dd_001": strategy.max_balance_dd_001,
        "max_equity_dd_001": strategy.max_equity_dd_001,
        "max_floating_dd_001": strategy.max_floating_dd_001,
        "floating_dd_source": strategy.floating_dd_source,
        "recent_net_profit_001": strategy.recent_net_profit_001,
        "recent_equity_dd_001": strategy.recent_equity_dd_001,
        "has_recent_performance": strategy.has_recent_performance,
        "final_tick_report_path": strategy.final_tick_report_path,
        "full_history_report_path": strategy.full_history_report_path,
        "final_tick_tail_trades": strategy.final_tick_tail_trades,
    }

def slice_strategy_set_to_month(
    strategy: RobustStrategySet,
    target_month: int,
) -> RobustStrategySet:
    """Return the strategy curve restricted to one calendar month across all years."""
    if not 1 <= int(target_month) <= 12:
        raise ValueError("target_month must be between 1 and 12")
    if not strategy.curve_points_2020_2026_001:
        raise ValueError("Strategy has no timestamped trade curve")
    selected = _month_trade_increments(strategy, target_month)
    return RobustStrategySet(
        target_month=int(target_month),
        closed_trades_2020_2026=[
            trade
            for trade in strategy.closed_trades_2020_2026
            if trade.close_time.month == int(target_month)
        ],
        **_carried_strategy_fields(strategy),
        **_month_curve_fields(selected),
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
