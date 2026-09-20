"""Curvas de equity: fusion, drawdown, correlacion y bootstrap."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
import random
from typing import Sequence

from .models import (
    BOOTSTRAP_METHOD,
    BootstrapDrawdownAnalysis,
    ClosedTrade,
    CorrelationPair,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_BOOTSTRAP_SIMULATIONS,
    PeriodReport,
    RobustStrategySet,
    _raise_if_portfolio_cancelled,
)


def merge_accumulated_curves(
    curve_2020_2024: list[float],
    curve_2025_2026: list[float],
) -> list[float]:
    if not curve_2020_2024:
        raise ValueError("2020-2024 curve is empty")
    if not curve_2025_2026:
        raise ValueError("2025-2026 curve is empty")
    last_value = curve_2020_2024[-1]
    return curve_2020_2024 + [last_value + value for value in curve_2025_2026[1:]]


def merge_incremental_curves(
    increments_2020_2024: list[float],
    increments_2025_2026: list[float],
) -> list[float]:
    return increments_2020_2024 + increments_2025_2026


def to_accumulated_curve(increments: list[float]) -> list[float]:
    curve = [0.0]
    total = 0.0
    for change in increments:
        total += change
        curve.append(total)
    return curve


def daily_pnl_series(strategy: RobustStrategySet) -> dict[str, float]:
    if strategy.curve_points_2020_2026_001:
        previous = 0.0
        series: dict[str, float] = {}
        for timestamp, value in strategy.curve_points_2020_2026_001:
            day = timestamp.date().isoformat()
            series[day] = series.get(day, 0.0) + (value - previous)
            previous = value
        return series

    increments = [
        current - previous
        for previous, current in zip(strategy.curve_2020_2026_001, strategy.curve_2020_2026_001[1:])
    ]
    return {str(index): value for index, value in enumerate(increments)}


def strategy_daily_closed_floating_dd(
    strategy: RobustStrategySet,
    *,
    full_history: bool = False,
) -> dict[str, float]:
    """Estimate per-day closed + floating DD for one 0.01-lot strategy unit.

    MT5 HTML reports parsed by this project expose closed deals/trades but do
    not expose a timestamped equity/floating-PnL series.  The closed component
    is the worst intraday closed-trade drawdown.  The floating component is a
    conservative proxy: the absolute final loss of each open losing trade is
    counted on every calendar day where the trade was open.  Winning trades do
    not add floating risk because their MAE is not present in the HTML.

    Monthly portfolios normally check only the selected target month.  When
    ``full_history`` is enabled, the same daily cap scans all historical days
    from the base + OOS reports.
    """
    month = 0 if full_history else int(strategy.target_month or 0)
    closed_by_day: dict[str, list[ClosedTrade]] = {}
    floating_by_day: dict[str, float] = {}
    history = strategy.closed_trades_2020_2026 or (
        list(strategy.report_2020_2024.closed_trades)
        + list(strategy.report_2025_2026.closed_trades)
    )
    for trade in history:
        close_time = trade.close_time
        if not month or close_time.month == month:
            closed_by_day.setdefault(close_time.date().isoformat(), []).append(trade)

        floating_risk = max(-float(trade.net_profit), 0.0)
        if floating_risk <= 0:
            continue
        open_time = trade.open_time or trade.close_time
        start_day = min(open_time.date(), trade.close_time.date())
        end_day = max(open_time.date(), trade.close_time.date())
        day = start_day
        while day <= end_day:
            if not month or day.month == month:
                day_key = day.isoformat()
                floating_by_day[day_key] = floating_by_day.get(day_key, 0.0) + floating_risk
            day += timedelta(days=1)

    closed_dd_by_day: dict[str, float] = {}
    for day_key, trades in closed_by_day.items():
        cumulative = 0.0
        trough = 0.0
        for trade in sorted(trades, key=lambda item: item.close_time):
            cumulative += float(trade.net_profit)
            trough = min(trough, cumulative)
        closed_dd_by_day[day_key] = max(-trough, 0.0)

    all_days = set(closed_dd_by_day) | set(floating_by_day)
    return {
        day: closed_dd_by_day.get(day, 0.0) + floating_by_day.get(day, 0.0)
        for day in all_days
    }


def portfolio_daily_closed_floating_dd(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    full_history: bool = False,
) -> tuple[float, dict[str, object]]:
    totals_by_day: dict[str, float] = {}
    by_set: dict[str, dict[str, object]] = {}
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        series = strategy_daily_closed_floating_dd(strategy, full_history=full_history)
        if not series:
            continue
        worst_day, worst_unit_dd = max(series.items(), key=lambda item: item[1])
        by_set[strategy.set_id] = {
            "symbol": strategy.symbol,
            "units": units,
            "worst_day": worst_day,
            "worst_unit_dd": float(worst_unit_dd),
            "worst_allocated_dd": float(worst_unit_dd) * units,
        }
        for day, value in series.items():
            totals_by_day[day] = totals_by_day.get(day, 0.0) + float(value) * units

    if not totals_by_day:
        return 0.0, {
            "enabled": False,
            "full_history": bool(full_history),
            "worst_day": None,
            "by_day": {},
            "by_set": by_set,
        }
    worst_day, worst_dd = max(totals_by_day.items(), key=lambda item: item[1])
    return float(worst_dd), {
        "enabled": True,
        "full_history": bool(full_history),
        "worst_day": worst_day,
        "worst_dd": float(worst_dd),
        "by_day": totals_by_day,
        "by_set": by_set,
    }


def portfolio_floating_overlap_audit(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    declared_floating: float,
    *,
    full_history: bool = False,
) -> dict[str, object]:
    """Compare the ``max()`` floating term against the time-aligned aggregate.

    ``evaluate_portfolio`` toma el peor episodio flotante individual a su lote y
    lo compara con el valle cerrado. Ese ``max()`` supone que solo una estrategia
    esta bajo el agua en cada momento. Alinear los dias dice cuanto se acumula de
    verdad cuando varias coinciden.

    La medida es **informativa**: usa el mismo proxy diario que el DD diario del
    proyecto (la perdida final de cada operacion perdedora pesa en cada dia que
    estuvo abierta), que exagera hacia arriba en operaciones largas y se queda
    corto en la excursion adversa de las ganadoras. Sirve para detectar que el
    supuesto del ``max()`` se esta rompiendo, no para sustituirlo en silencio.

    El solapamiento se aisla comparando el agregado contra ``worst_single``, que
    es el mismo proxy tomando solo la peor estrategia: ambos lados salen de la
    misma medida, asi que la diferencia entre ellos es coincidencia y nada mas.
    Comparar el agregado directamente contra el flotante declarado mezclaria dos
    magnitudes distintas -- el declarado es el DD de equity del informe -- y
    marcaria solapamiento donde solo hay cambio de escala.
    """
    active = {set_id: units for set_id, units in allocations.items() if int(units) > 0}
    if len(active) < 2:
        return {}
    measured, summary = portfolio_daily_closed_floating_dd(
        sets, allocations, full_history=full_history,
    )
    if not summary.get("enabled"):
        return {}
    worst_day = summary.get("worst_day")
    by_set = summary.get("by_set") or {}
    by_day = summary.get("by_day") or {}
    worst_single = max(
        (
            float(entry.get("worst_allocated_dd", 0.0))
            for entry in by_set.values() if isinstance(entry, dict)
        ),
        default=0.0,
    )
    contributions: dict[str, float] = {}
    for strategy in sets:
        units = int(active.get(strategy.set_id, 0))
        if units <= 0 or not worst_day:
            continue
        series = strategy_daily_closed_floating_dd(strategy, full_history=full_history)
        value = float(series.get(str(worst_day), 0.0)) * units
        if value > 0:
            contributions[strategy.set_id] = round(value, 2)
    return {
        "worst_day": worst_day,
        "measured_aggregate": round(float(measured), 2),
        "worst_single": round(float(worst_single), 2),
        "overlap_excess": round(max(float(measured) - float(worst_single), 0.0), 2),
        "overlap_detected": bool(float(measured) > float(worst_single) + 1e-9),
        "declared_floating_dd": round(float(declared_floating), 2),
        "exceeds_declared": bool(float(measured) > float(declared_floating) + 1e-9),
        "coincident_sets": len(contributions),
        "active_sets": len(active),
        "measured_days": len(by_day),
        "contributions": dict(
            sorted(contributions.items(), key=lambda item: item[1], reverse=True)[:10]
        ),
    }


def pearson_correlation(values_a: Sequence[float], values_b: Sequence[float]) -> float:
    if len(values_a) < 2 or len(values_b) < 2 or len(values_a) != len(values_b):
        return 0.0
    mean_a = sum(values_a) / len(values_a)
    mean_b = sum(values_b) / len(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    denom_a = math.sqrt(sum(value * value for value in centered_a))
    denom_b = math.sqrt(sum(value * value for value in centered_b))
    denom = denom_a * denom_b
    if denom <= 0:
        return 0.0
    return float(sum(a * b for a, b in zip(centered_a, centered_b)) / denom)


def curve_increment_correlation(curve_a: Sequence[float], curve_b: Sequence[float]) -> float:
    increments_a = [current - previous for previous, current in zip(curve_a, curve_a[1:])]
    increments_b = [current - previous for previous, current in zip(curve_b, curve_b[1:])]
    length = max(len(increments_a), len(increments_b))
    if length < 2:
        return 0.0
    padded_a = increments_a + [0.0] * (length - len(increments_a))
    padded_b = increments_b + [0.0] * (length - len(increments_b))
    return pearson_correlation(padded_a, padded_b)


def strategy_correlation_pair(strategy_a: RobustStrategySet, strategy_b: RobustStrategySet) -> CorrelationPair:
    series_a = daily_pnl_series(strategy_a)
    series_b = daily_pnl_series(strategy_b)
    keys = sorted(set(series_a) | set(series_b))
    values_a = [series_a.get(key, 0.0) for key in keys]
    values_b = [series_b.get(key, 0.0) for key in keys]
    pearson = pearson_correlation(values_a, values_b)

    downside_a: list[float] = []
    downside_b: list[float] = []
    overlap_losses = 0
    loss_days = 0
    for value_a, value_b in zip(values_a, values_b):
        if value_a < 0 or value_b < 0:
            downside_a.append(min(value_a, 0.0))
            downside_b.append(min(value_b, 0.0))
            loss_days += 1
            if value_a < 0 and value_b < 0:
                overlap_losses += 1

    downside = pearson_correlation(downside_a, downside_b)
    dd_overlap = overlap_losses / loss_days if loss_days else 0.0
    return CorrelationPair(
        set_id_a=strategy_a.set_id,
        set_id_b=strategy_b.set_id,
        symbol_a=strategy_a.symbol,
        symbol_b=strategy_b.symbol,
        pearson_corr=pearson,
        downside_corr=downside,
        dd_overlap=dd_overlap,
        observations=len(keys),
    )


def build_correlation_pairs(sets: Sequence[RobustStrategySet]) -> list[CorrelationPair]:
    pairs: list[CorrelationPair] = []
    for left_index, strategy_a in enumerate(sets):
        for strategy_b in sets[left_index + 1:]:
            pairs.append(strategy_correlation_pair(strategy_a, strategy_b))
    return pairs


def calc_valley_dd(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        max_dd = max(max_dd, peak - value)
    return float(max_dd)


def calc_point_dd(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    worst_loss = 0.0
    for previous, current in zip(equity_curve, equity_curve[1:]):
        change = current - previous
        if change < worst_loss:
            worst_loss = change
    return abs(float(worst_loss))


def bootstrap_valley_drawdown(
    equity_curve: Sequence[float],
    *,
    nominal_valley_dd_limit: float,
    effective_valley_dd_limit: float,
    simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS,
    block_size: int | None = None,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> BootstrapDrawdownAnalysis:
    """Estimate valley-DD risk with a deterministic circular block bootstrap.

    Consecutive P/L increments are sampled in blocks, preserving local loss
    streaks instead of independently shuffling every trade. A fixed seed makes
    proposal comparisons and saved audits reproducible.
    """
    if simulations <= 0:
        raise ValueError("Bootstrap simulations must be positive")
    increments = [
        float(current) - float(previous)
        for previous, current in zip(equity_curve, equity_curve[1:])
    ]
    observation_count = len(increments)
    if observation_count == 0:
        return BootstrapDrawdownAnalysis(
            method=BOOTSTRAP_METHOD,
            simulations=int(simulations),
            seed=int(seed),
            observations=0,
            block_size=0,
            valley_dd_p50=0.0,
            valley_dd_p95=0.0,
            nominal_valley_dd_limit=float(nominal_valley_dd_limit),
            effective_valley_dd_limit=float(effective_valley_dd_limit),
            probability_exceed_nominal_pct=0.0,
            probability_exceed_effective_pct=0.0,
            alert=False,
        )

    if block_size is None:
        block_size = min(20, max(5, int(round(math.sqrt(observation_count)))))
    block_size = min(max(int(block_size), 1), observation_count)
    rng = random.Random(int(seed))
    drawdowns: list[float] = []
    for _simulation in range(int(simulations)):
        _raise_if_portfolio_cancelled()
        sampled = 0
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        while sampled < observation_count:
            start = rng.randrange(observation_count)
            take = min(block_size, observation_count - sampled)
            for offset in range(take):
                equity += increments[(start + offset) % observation_count]
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, peak - equity)
            sampled += take
        drawdowns.append(float(max_drawdown))

    drawdowns.sort()
    p50 = _linear_percentile(drawdowns, 0.50)
    p95 = _linear_percentile(drawdowns, 0.95)
    nominal_limit = float(nominal_valley_dd_limit)
    effective_limit = float(effective_valley_dd_limit)
    exceed_nominal = sum(value > nominal_limit + 1e-9 for value in drawdowns)
    exceed_effective = sum(value > effective_limit + 1e-9 for value in drawdowns)
    return BootstrapDrawdownAnalysis(
        method=BOOTSTRAP_METHOD,
        simulations=int(simulations),
        seed=int(seed),
        observations=observation_count,
        block_size=block_size,
        valley_dd_p50=p50,
        valley_dd_p95=p95,
        nominal_valley_dd_limit=nominal_limit,
        effective_valley_dd_limit=effective_limit,
        probability_exceed_nominal_pct=exceed_nominal / simulations * 100.0,
        probability_exceed_effective_pct=exceed_effective / simulations * 100.0,
        alert=p95 > effective_limit + 1e-9,
    )


def _linear_percentile(sorted_values: Sequence[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    position = min(max(float(quantile), 0.0), 1.0) * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _curve_points_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[tuple[datetime, float]]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    total = 0.0
    points: list[tuple[datetime, float]] = []
    for trade in ordered:
        total += trade.net_profit
        points.append((trade.close_time, total))
    return points


def _merge_curve_points(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> list[tuple[datetime, float]]:
    if not report_2020_2024.pnl_points_001 and not report_2025_2026.pnl_points_001:
        return []
    last_value = report_2020_2024.pnl_curve_001[-1] if report_2020_2024.pnl_curve_001 else 0.0
    points = list(report_2020_2024.pnl_points_001)
    points.extend((timestamp, last_value + value) for timestamp, value in report_2025_2026.pnl_points_001)
    return sorted(points, key=lambda item: item[0])
