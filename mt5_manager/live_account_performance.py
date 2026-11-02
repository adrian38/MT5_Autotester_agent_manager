"""Una definición de Profit visible y resultado neto para todas las pantallas.

La cuenta real guarda dos series: la columna Profit de MT5 y el efecto neto
(`Profit + comisión + swap`). Lo visible usa la primera; drawdown, objetivos y
veredictos usan la segunda, que es la comparable con el backtest.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from .live_account_archive import ordered_movements, ordered_profits
from .live_account_series import daily_rows, monthly_rows, row

Rows = dict[str, dict[str, Any]]
Aggregator = Callable[[Sequence[tuple[Any, float]]], Rows]


def account_series(
    stored: dict[str, Any],
) -> tuple[list[tuple[Any, float]], list[tuple[Any, float]] | None]:
    """Las dos series de una cuenta, con `None` cuando el Profit se desconoce.

    Un archivo anterior a la columna Profit sólo guarda el efecto neto, y
    entonces lo visible no se puede enseñar: es `None`, no el neto disfrazado.
    Una cuenta sin movimientos sí se conoce —es cero—, así que no cuenta como
    desconocida. La decisión vive aquí porque la toman las dos pantallas.
    """
    ordered = ordered_movements(stored)
    profits = ordered_profits(stored)
    return ordered, profits if stored.get("profits") or not ordered else None


def _performance_rows(
    net: Sequence[tuple[Any, float]], profits: Sequence[tuple[Any, float]] | None,
    aggregate: Aggregator,
) -> tuple[Rows, Rows]:
    """Filas visibles y netas, agregadas con exactamente el mismo corte."""
    net_rows = aggregate(net)
    gross_rows = aggregate(net if profits is None else profits)
    known = profits is not None
    visible: Rows = {}
    for label in net_rows.keys() | gross_rows.keys():
        net_row = row(net_rows, label)
        gross_row = row(gross_rows, label)
        visible[label] = {
            **net_row,
            "profit": gross_row["profit"] if known else None,
        }
    return visible, net_rows


def daily_performance(
    net: Sequence[tuple[Any, float]], profits: Sequence[tuple[Any, float]] | None,
) -> tuple[Rows, Rows]:
    """Profit MT5 visible y neto comparable por día."""
    return _performance_rows(net, profits, daily_rows)


def monthly_performance(
    net: Sequence[tuple[Any, float]], profits: Sequence[tuple[Any, float]] | None,
) -> tuple[Rows, Rows]:
    """Profit MT5 visible y neto comparable por mes."""
    return _performance_rows(net, profits, monthly_rows)
