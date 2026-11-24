"""Ventanas fijas y evaluación por fases de un Portafolio Prop."""

from __future__ import annotations

from dataclasses import replace
from collections import Counter
from datetime import date, timedelta
from math import isfinite
from typing import Sequence

from ..experiment.experiment_lab import LabAxis, valley_dd
from .prop_experiment_engine import PropStrategy
from .prop_execution import ObservedEuroRates, equivalent_volume_unit
from .prop_position_book import PositionEvent, PropPosition
from .the5ers_hyper_growth_engine import GrowthClose


PHASE_ONE_FROM = "2025-01-01"
# Los reportes MT5 empiezan en el primer día de mercado de 2025; no se exige
# cobertura artificial del festivo 2025-01-01.
PHASE_ONE_FIRST_DAY = "2025-01-02"
# El 2026-05-21 es el corte exclusivo solicitado. Hasta el 2026-11-24 la
# ventana acababa a fin de mayo, y eso dejaba fuera por cobertura incompleta a
# las seeds cuyo informe OHLC terminaba unos días antes del cierre: el eje
# exige todos sus días de mercado, así que faltar uno basta. El último día
# incluido, 2026-05-20, es miércoles y es por tanto el último que exige el eje.
PHASE_ONE_TO = "2026-05-21"
PHASE_ONE_LAST_DAY = "2026-05-20"
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
    strategies: Sequence[PropStrategy], axis: LabAxis, start: str, end: str, *,
    rates: ObservedEuroRates | None = None, discards: Counter | None = None,
) -> tuple[list[PropStrategy], LabAxis, list[str]]:
    """Exige las mismas fechas cubiertas antes de comparar composiciones."""
    calendar = _calendar_axis(axis.days, start, end)
    positions = {day: index for index, day in enumerate(calendar.days)}
    replayable = []
    rejected = Counter()
    for item in strategies:
        if not _covered(item.coverage or (), calendar.days):
            reason = 'cobertura OHLC incompleta'
        elif rates is not None:
            candidate, reason = _replay_prop_strategy(item, positions, rates)
        elif not isfinite(item.lab.margin_per_unit) or item.lab.margin_per_unit <= 0:
            reason = 'margen sin evidencia'
        else:
            candidate = _replay_strategy(item, positions)
            reason = '' if candidate else 'operaciones sin fecha o volumen'
        if reason:
            rejected[item.lab.origin_node, item.source_kind, reason] += 1
        else:
            replayable.append(candidate)
    if discards is not None:
        discards.update(rejected)
    warnings = [f'{node} · {kind}: {count} fuera por {reason}.'
                for (node, kind, reason), count in sorted(rejected.items())]
    if not replayable:
        raise ValueError("No hay estrategias con cobertura completa y operaciones reproducibles. " + " ".join(warnings))
    return replayable, calendar, warnings


def _prop_position(item, trade, lots, rates, close_in_window):
    source_lot, target_lot = lots
    contract = item.prop_contract
    prices = (trade.open_price, trade.close_price if close_in_window else trade.open_price)
    if any(price is None or not isfinite(price) or price <= 0 for price in prices):
        return None, 'precio de apertura o cierre ausente'
    direction = {'buy': 1, 'sell': -1}.get(trade.side.casefold())
    if not direction:
        return None, 'dirección de la operación ausente'
    open_rate = rates.quote_rate(contract, prices[0], trade.open_time)
    close_rate = rates.quote_rate(contract, prices[1], trade.close_time) if close_in_window else open_rate
    report_rate = rates.at(trade.currency, trade.close_time) if close_in_window else 1.0
    if not all((open_rate, close_rate, report_rate)):
        return None, 'valoración en EUR sin cotización OHLC reciente'
    return PropPosition(
        contract.symbol, trade.open_time, trade.close_time, *prices,
        open_rate, close_rate, target_lot * contract.volume.contract,
        direction, contract.leverage,
        trade.net_profit * source_lot / trade.volume * report_rate if close_in_window else 0.0,
    ), ''


def _observed_peak_margin(events):
    margin = peak = 0.0
    for event in events:
        position = event.position
        bound = max(position.margin(position.open_price, position.open_quote_rate, 1),
                    position.margin(position.close_price, position.close_quote_rate, 1))
        margin += bound if event.opening else -bound
        peak = max(peak, margin)
    return peak


def _replay_prop_strategy(item, days, rates):
    if item.prop_contract is None:
        return None, 'especificación del símbolo en The5ers sin verificar'
    if item.source_volume is None:
        return None, 'contrato o paso de lote del broker de origen sin verificar'
    if any(trade.open_time is None for trade in item.closed_trades
           if trade.close_time.date().isoformat() in days):
        return None, 'operación sin hora de apertura'
    trades = [trade for trade in item.closed_trades if trade.open_time and
              trade.open_time.date().isoformat() in days]
    volumes = {round(trade.volume, 8) for trade in trades}
    if len(volumes) != 1 or next(iter(volumes), 0) <= 0:
        return None, 'volumen variable o sin aperturas verificables en la ventana'
    lots = equivalent_volume_unit(item.source_volume, item.prop_contract.volume)
    values = [0.0] * len(days)
    by_day = {}
    for trade in trades:
        closed = days.get(trade.close_time.date().isoformat())
        position, reason = _prop_position(item, trade, lots, rates, closed is not None)
        if reason:
            return None, reason
        opened = days[position.opened.date().isoformat()]
        by_day.setdefault(opened, []).append(PositionEvent(position.opened, position, True))
        if closed is not None:
            by_day.setdefault(closed, []).append(PositionEvent(position.closed, position, False))
            values[closed] += position.closed_pnl
    events = {day: tuple(sorted(rows, key=lambda event: (event.time, not event.opening)))
              for day, rows in sorted(by_day.items())}
    margin = _observed_peak_margin(event for rows in events.values() for event in rows)
    series = tuple(values)
    return replace(
        item, series=series, closed_trades=(), lot_per_unit=lots[1], source_lot_per_unit=lots[0],
        position_events=events,
        lab=replace(item.lab, series=series, net=sum(series), valley_dd=valley_dd(series),
                    active_days=len(by_day), margin_per_unit=margin),
    ), ''
