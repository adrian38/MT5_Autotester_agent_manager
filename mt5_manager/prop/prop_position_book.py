"""Mark open OHLC positions at observed fills and enforce the account's margin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import groupby


@dataclass(frozen=True, eq=False)
class PropPosition:
    symbol: str
    opened: datetime
    closed: datetime
    open_price: float
    close_price: float
    open_usd_rate: float
    close_usd_rate: float
    quantity: float
    direction: int
    leverage: float
    usd_base: bool
    closed_pnl: float

    def margin(self, price: float, rate: float, units: int) -> float:
        notional = self.quantity if self.usd_base else self.quantity * price * rate
        return notional * units / self.leverage

    def floating(self, price: float, rate: float, units: int) -> float:
        return (price - self.open_price) * self.direction * self.quantity * rate * units


@dataclass(frozen=True)
class PositionEvent:
    time: datetime
    position: PropPosition
    opening: bool
    units: int = 1

    @property
    def mark(self) -> tuple[float, float]:
        position = self.position
        return ((position.open_price, position.open_usd_rate) if self.opening
                else (position.close_price, position.close_usd_rate))


class PositionBook:
    def __init__(self, capital: float) -> None:
        self.balance = capital
        self.open_positions: dict[PropPosition, int] = {}
        self.marks: dict[str, tuple[float, float]] = {}
        self.rejections = 0
        self.peak_margin = 0.0
        self.minimum_free = capital

    def snapshot(self) -> tuple[float, float]:
        equity, margin = self.balance, 0.0
        for position, units in self.open_positions.items():
            price, rate = self.marks[position.symbol]
            equity += position.floating(price, rate, units)
            margin += position.margin(price, rate, units)
        return equity, margin

    def _loss_event(self, equity: float, margin: float, pause: float, stop_out: float) -> str:
        self.peak_margin = max(self.peak_margin, margin)
        self.minimum_free = min(self.minimum_free, equity - margin)
        if equity <= max(pause, stop_out):
            self.balance = equity
            self.open_positions.clear()
            return 'stop_out' if equity <= stop_out else 'daily_pause'
        return 'traded'

    def _close(self, events: list[PositionEvent]) -> None:
        for event in events:
            units = self.open_positions.pop(event.position, 0)
            self.balance += event.position.closed_pnl * units

    def _open(self, events: list[PositionEvent], equity: float, margin: float) -> None:
        # With simultaneous entries, test the whole batch against the same equity.
        # A composition that needs to reject any order cannot win the search.
        added = sum(event.position.margin(*self.marks[event.position.symbol], event.units)
                    for event in events)
        if margin + added > equity + 1e-9:
            self.rejections += len(events)
        else:
            self.open_positions.update((event.position, event.units) for event in events)

    def trade_day(self, events, rules, may_pass: bool) -> tuple[str, float]:
        equity, _margin = self.snapshot()
        pause = max(self.balance, equity) * (1.0 - rules.daily_pause_pct / 100.0)
        peak = self.balance
        for _when, group in groupby(events, key=lambda item: item.time):
            openings, closings = [], []
            for event in group:
                self.marks[event.position.symbol] = event.mark
                (openings if event.opening else closings).append(event)
            equity, margin = self.snapshot()
            status = self._loss_event(equity, margin, pause, rules.stop_out_level)
            if status != 'traded':
                return status, peak
            self._close([event for event in closings if event.position in self.open_positions])
            equity, margin = self.snapshot()
            status = self._loss_event(equity, margin, pause, rules.stop_out_level)
            peak = max(peak, self.balance)
            if status != 'traded' or (may_pass and self.balance + 1e-9 >= rules.target_balance):
                return status, peak
            self._open(openings, equity, margin)
            self._close([event for event in closings if event.position.opened == event.time])
            equity, margin = self.snapshot()
            status = self._loss_event(equity, margin, pause, rules.stop_out_level)
            peak = max(peak, self.balance)
            if status != 'traded' or (may_pass and self.balance + 1e-9 >= rules.target_balance):
                return status, peak
        return 'traded', peak
