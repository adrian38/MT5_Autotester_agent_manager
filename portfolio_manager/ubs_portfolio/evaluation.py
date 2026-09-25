"""Evaluacion de una cartera completa contra sus limites."""

from __future__ import annotations

from datetime import datetime

from .symbols import portfolio_group_key
from .models import (
    PortfolioEvaluation,
    RobustStrategySet,
    _raise_if_portfolio_cancelled,
)
from .curves import (
    calc_point_dd,
    calc_valley_dd,
    portfolio_daily_closed_floating_dd,
)


def _empty_portfolio_evaluation(
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
    target_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
) -> PortfolioEvaluation:
    """Una cartera sin ninguna unidad activa: todo a cero, limites intactos."""
    return PortfolioEvaluation(
        allocations=allocations.copy(),
        equity_curve_2020_2026=[0.0],
        total_net_profit=0.0,
        valley_dd=0.0,
        point_dd=0.0,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=0.0,
        point_usage_pct=0.0,
        total_units=0,
        total_lot=0.0,
        active_strategies=0,
        daily_dd=0.0,
        target_daily_dd=target_daily_dd,
        daily_usage_pct=0.0,
        daily_dd_full_history=bool(daily_dd_full_history),
        enforce_point_dd=bool(enforce_point_dd),
        closed_valley_dd=0.0,
        floating_dd_buffer=0.0,
    )


def _portfolio_equity_curve(
    active_sets: list[RobustStrategySet], allocations: dict[str, int],
) -> list[float]:
    """La curva combinada: por eje de tiempo si se puede, si no por indice.

    Sumar por indice exige que todas las curvas midan lo mismo; es el camino de
    los informes antiguos, que no traen marca de tiempo por operacion.
    """
    if all(strategy.curve_points_2020_2026_001 for strategy in active_sets):
        return _evaluate_portfolio_on_time_axis(active_sets, allocations)
    length = len(active_sets[0].curve_2020_2026_001)
    for strategy in active_sets:
        if len(strategy.curve_2020_2026_001) != length:
            raise ValueError("All 2020-2026 curves must have the same length")
    portfolio_curve = [0.0] * length
    for strategy in active_sets:
        units = allocations[strategy.set_id]
        for index, value in enumerate(strategy.curve_2020_2026_001):
            portfolio_curve[index] += value * units
    return portfolio_curve


def _floating_dd_buffer(
    active_sets: list[RobustStrategySet], allocations: dict[str, int],
) -> float:
    """El peor episodio flotante de una sola estrategia, a su lote asignado.

    Historical floating episodes are alternatives in time, not additive
    reserves. Keep the worst observed standalone episode and compare it with
    the closed portfolio valley. A synchronized equity time series can replace
    this proxy later and would only add floating losses that actually overlap
    at the same timestamp.
    """
    return max(
        (
            strategy.max_floating_dd_001 * allocations[strategy.set_id]
            for strategy in active_sets
        ),
        default=0.0,
    )


def evaluate_portfolio(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
    target_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> PortfolioEvaluation:
    # Es el punto caliente común de la búsqueda UBS. La comprobación por hilo
    # permite detener el UBS completo sin alterar el comportamiento mensual,
    # que no instala ninguna señal de cancelación.
    _raise_if_portfolio_cancelled()
    active_sets = [strategy for strategy in sets if allocations.get(strategy.set_id, 0) > 0]
    if not active_sets:
        return _empty_portfolio_evaluation(
            allocations, target_valley_dd, target_point_dd,
            target_daily_dd, enforce_point_dd, daily_dd_full_history,
        )
    portfolio_curve = _portfolio_equity_curve(active_sets, allocations)
    closed_valley_dd = calc_valley_dd(portfolio_curve)
    floating_dd_buffer = _floating_dd_buffer(active_sets, allocations)
    valley_dd = max(closed_valley_dd, floating_dd_buffer)
    point_dd = calc_point_dd(portfolio_curve)
    daily_dd = 0.0
    if target_daily_dd is not None:
        daily_dd, _daily_summary = portfolio_daily_closed_floating_dd(
            active_sets,
            allocations,
            full_history=bool(daily_dd_full_history),
        )
    total_units = sum(max(value, 0) for value in allocations.values())
    return PortfolioEvaluation(
        allocations=allocations.copy(),
        equity_curve_2020_2026=portfolio_curve,
        total_net_profit=portfolio_curve[-1],
        valley_dd=valley_dd,
        point_dd=point_dd,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=valley_dd / target_valley_dd * 100 if target_valley_dd > 0 else 0.0,
        point_usage_pct=point_dd / target_point_dd * 100 if target_point_dd > 0 else 0.0,
        total_units=total_units,
        total_lot=total_units * 0.01,
        active_strategies=sum(1 for value in allocations.values() if value > 0),
        daily_dd=daily_dd,
        target_daily_dd=target_daily_dd,
        daily_usage_pct=daily_dd / target_daily_dd * 100 if target_daily_dd and target_daily_dd > 0 else 0.0,
        daily_dd_full_history=bool(daily_dd_full_history),
        enforce_point_dd=bool(enforce_point_dd),
        closed_valley_dd=closed_valley_dd,
        floating_dd_buffer=floating_dd_buffer,
    )


def portfolio_group_summary(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
) -> dict[str, dict[str, float | int]]:
    stats: dict[str, dict[str, float | int]] = {}
    total_units = 0
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        group = portfolio_group_key(strategy.symbol)
        row = stats.setdefault(group, {"units": 0, "sets": 0, "unit_pct": 0.0})
        row["units"] = int(row["units"]) + units
        row["sets"] = int(row["sets"]) + 1
        total_units += units
    if total_units > 0:
        for row in stats.values():
            row["unit_pct"] = float(row["units"]) / total_units * 100.0
    return dict(sorted(stats.items(), key=lambda item: (-float(item[1]["units"]), item[0])))


def _evaluation_violates_dd_limits(evaluation: PortfolioEvaluation) -> bool:
    if evaluation.valley_dd > evaluation.target_valley_dd + 1e-9:
        return True
    if evaluation.enforce_point_dd and evaluation.point_dd > evaluation.target_point_dd + 1e-9:
        return True
    return False


def _evaluation_violation_ratio(evaluation: PortfolioEvaluation) -> float:
    ratios = [
        evaluation.valley_dd / max(evaluation.target_valley_dd, 1e-9),
    ]
    if evaluation.enforce_point_dd:
        ratios.append(evaluation.point_dd / max(evaluation.target_point_dd, 1e-9))
    return max(ratios)


def _evaluate_portfolio_on_time_axis(
    active_sets: list[RobustStrategySet],
    allocations: dict[str, int],
) -> list[float]:
    events: list[tuple[datetime, str, int, float]] = []
    for strategy in active_sets:
        previous_value = 0.0
        for index, (timestamp, value) in enumerate(strategy.curve_points_2020_2026_001):
            events.append((timestamp, strategy.set_id, index, (value - previous_value) * allocations[strategy.set_id]))
            previous_value = value

    if not events:
        return [0.0]
    curve = [0.0]
    total = 0.0
    for _timestamp, _set_id, _index, change in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        total += change
        curve.append(total)
    return curve
