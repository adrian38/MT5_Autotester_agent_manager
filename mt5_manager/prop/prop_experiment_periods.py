"""Ventanas fijas y evaluación por fases de un Portafolio Prop."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from math import isfinite
from typing import Sequence

from ..experiment.experiment_lab import LabAxis, valley_dd
from .prop_experiment_engine import PropStrategy
from .the5ers_hyper_growth_engine import GrowthClose


PHASE_ONE_FROM = "2025-01-01"
# Los reportes MT5 empiezan en el primer día de mercado de 2025; no se exige
# cobertura artificial del festivo 2025-01-01.
PHASE_ONE_FIRST_DAY = "2025-01-02"
# El 2026-06-01 es el corte exclusivo solicitado. El último día incluido es
# domingo; por tanto, el último día de mercado exigido por el eje es 2026-05-29.
PHASE_ONE_TO = "2026-06-01"
PHASE_ONE_LAST_DAY = "2026-05-31"
PHASE_TWO_FROM = "2025-11-20"
PHASE_TWO_TO = "2026-11-20"
PHASE_THREE_FROM = "2020-01-01"
PHASE_THREE_TO = "2024-12-31"


def _covered(intervals: tuple[tuple[str, str], ...], days: tuple[str, ...]) -> bool:
    return all(any(start and end and start <= day <= end for start, end in intervals) for day in days)


def _calendar_axis(days: tuple[str, ...], start: str, end: str) -> LabAxis:
    observed = set(days)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    included = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        value = day.isoformat()
        if day.weekday() < 5 or value in observed:
            included.append(value)
    months = {month: index for index, month in enumerate(
        dict.fromkeys(day[:7] for day in included)
    )}
    return LabAxis(tuple(included), tuple(months[day[:7]] for day in included), len(months))


def _peak_exposure_units(trades, scale: float, lot: float) -> float:
    """Mide cuántas unidades mínimas estuvieron abiertas a la vez en el OHLC."""
    events = []
    for _index, trade in trades:
        exposure = trade.volume * scale / lot
        events.extend(((trade.open_time, 0, exposure), (trade.close_time, 1, -exposure)))
    current = peak = 0.0
    for _time, _order, change in sorted(events):
        current += change
        peak = max(peak, current)
    return max(peak, 1.0)


def _replay_strategy(item: PropStrategy, positions: dict[str, int]) -> PropStrategy | None:
    trades = [
        (positions[day], trade) for trade in item.closed_trades
        if (day := trade.close_time.date().isoformat()) in positions
    ]
    if not trades or any(trade.open_time is None for _index, trade in trades):
        return None
    volumes = {round(trade.volume, 8) for _index, trade in trades if trade.volume > 0}
    if not volumes:
        return None
    lot = item.lot_per_unit or min(volumes)
    # Un informe a volumen fijo puede venir de una pasada manual a otro lote.
    # Para tamaños variables se conserva la unidad mínima de la pasada original.
    fixed_volume = next(iter(volumes)) if len(volumes) == 1 else 0.0
    scale = lot / fixed_volume if fixed_volume > 0 else 1.0
    peak_exposure = _peak_exposure_units(trades, scale, lot)
    values = [0.0] * len(positions)
    by_day: dict[int, list[GrowthClose]] = {}
    for index, trade in trades:
        pnl = trade.net_profit * scale
        values[index] += pnl
        by_day.setdefault(index, []).append(GrowthClose(
            trade.close_time, trade.open_time.date().isoformat(), pnl,
        ))
    series = tuple(values)
    return replace(
        item, series=series, closed_trades=(), lot_per_unit=lot,
        closes={index: tuple(sorted(rows, key=lambda row: row.time)) for index, rows in by_day.items()},
        lab=replace(
            item.lab, series=series, net=sum(series), valley_dd=valley_dd(series),
            active_days=len(by_day), margin_per_unit=item.lab.margin_per_unit * peak_exposure,
        ),
    )


def replay_window(
    strategies: Sequence[PropStrategy], axis: LabAxis, start: str, end: str,
) -> tuple[list[PropStrategy], LabAxis, list[str]]:
    """Exige las mismas fechas cubiertas antes de comparar composiciones."""
    calendar = _calendar_axis(axis.days, start, end)
    positions = {day: index for index, day in enumerate(calendar.days)}
    replayable = []
    incomplete = unusable = 0
    for item in strategies:
        if not _covered(item.coverage or (), calendar.days):
            incomplete += 1
            continue
        if not isfinite(item.lab.margin_per_unit) or item.lab.margin_per_unit <= 0:
            unusable += 1
            continue
        candidate = _replay_strategy(item, positions)
        if candidate is None:
            unusable += 1
        else:
            replayable.append(candidate)
    warnings = []
    if incomplete:
        warnings.append(f"Prop: {incomplete} estrategias sin cobertura demostrada del {start} al {end}.")
    if unusable:
        warnings.append(f"Prop: {unusable} estrategias sin operaciones fechadas o margen calculable.")
    if not replayable:
        raise ValueError("No hay estrategias con cobertura completa y operaciones reproducibles. " + " ".join(warnings))
    return replayable, calendar, warnings
