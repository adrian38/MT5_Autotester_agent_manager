"""Profit y DD de una cuenta real, por día y por mes.

Aquí sólo hay aritmética: ni red, ni ficheros, ni cuándo toca sondear. Recibe
los movimientos que el informe nativo de MT5 publica como transacciones de la
cuenta y los reparte por día UTC y por mes natural.

**El día se corta en UTC**, que es como ya lo corta todo lo demás: el informe
nativo imprime UTC sin sufijo (`live_audit_autolot._utc_datetime`) y el auditor
convierte su periodo en días completos UTC (`_audit_period`). Mezclar aquí la
hora local movería el corte, y el mismo cierre caería en días distintos según
quién preguntase.

La DD es la **misma** que ya enseña «Portafolios activos»: la mayor caída
pico-valle de la curva de operaciones cerradas, en dinero, arrancando en cero
en cada tramo. Dos cuentas distintas del mismo drawdown es justo lo que no
puede haber, así que el criterio se escribe una vez y se reutiliza.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

NON_MARKET_TYPES = frozenset({
    "balance", "credit", "correction", "bonus", "charge", "commission",
    "saldo", "credito", "crédito", "correccion", "corrección", "comision",
    "comisión", "bonificacion", "bonificación",
})
"""Tipos de transacción que no son resultado de operar.

Un ingreso no es beneficio: contarlo convertiría un depósito de mil euros en
el mejor día de la historia de la cuenta. MT5 los publica en la misma tabla
que los cierres y sólo esta columna los distingue; el informe sale en el
idioma del terminal, así que hacen falta los dos juegos de nombres.
"""

EMPTY_ROW = {"profit": 0.0, "drawdown": 0.0, "trades": 0}


def as_utc(value: datetime) -> datetime:
    """El instante en UTC; sin zona se interpreta UTC, como el informe."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_market_movement(deal: Any) -> bool:
    """Si esa transacción es resultado de operar y no un apunte de saldo."""
    kind = str(getattr(deal, "trade_type", "") or "").strip().casefold()
    return kind not in NON_MARKET_TYPES


def movements(deals: Iterable[Any]) -> list[tuple[datetime, float]]:
    """Los cierres que mueven el saldo por operar, ordenados y en UTC.

    `net_profit` y no `profit`: la comisión y el swap son parte del resultado
    de la operación, y dejarlos fuera inflaría el beneficio de una cuenta real
    exactamente en lo que el broker se queda.
    """
    found: list[tuple[datetime, float]] = []
    for deal in deals:
        moment = getattr(deal, "timestamp", None)
        if not isinstance(moment, datetime) or not is_market_movement(deal):
            continue
        amount = getattr(deal, "net_profit", None)
        if amount is None:
            continue
        found.append((as_utc(moment), float(amount)))
    found.sort(key=lambda item: item[0])
    return found


def day_key(moment: datetime) -> str:
    return as_utc(moment).date().isoformat()


def month_key(moment: datetime) -> str:
    stamp = as_utc(moment)
    return f"{stamp.year:04d}-{stamp.month:02d}"


def drawdown(amounts: Sequence[float]) -> float:
    """Mayor caída pico-valle de la curva acumulada, arrancando en cero.

    Es el criterio de `dashboard_metrics.js`: el tramo empieza sin memoria de
    lo anterior, así que la DD de un día mide lo que ese día se perdió desde su
    propio máximo, no desde el máximo histórico de la cuenta.
    """
    balance = peak = worst = 0.0
    for amount in amounts:
        balance += amount
        peak = max(peak, balance)
        worst = max(worst, peak - balance)
    return worst


def _rows(
    ordered: Sequence[tuple[datetime, float]], key: Any,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for moment, amount in ordered:
        grouped.setdefault(key(moment), []).append(amount)
    return {
        label: {
            "profit": sum(amounts),
            "drawdown": drawdown(amounts),
            "trades": len(amounts),
        }
        for label, amounts in grouped.items()
    }


def daily_rows(ordered: Sequence[tuple[datetime, float]]) -> dict[str, dict[str, Any]]:
    """`{"2026-10-20": {"profit": …, "drawdown": …, "trades": …}}`."""
    return _rows(ordered, day_key)


def monthly_rows(ordered: Sequence[tuple[datetime, float]]) -> dict[str, dict[str, Any]]:
    """`{"2026-10": {"profit": …, "drawdown": …, "trades": …}}`."""
    return _rows(ordered, month_key)


def row(rows: dict[str, Any], label: str) -> dict[str, Any]:
    """La fila de ese día o mes, o una vacía: un hueco es un cero medido.

    Una cuenta que no operó el martes tiene cero ese martes. Devolver `None`
    obligaría a cada consumidor a decidir otra vez si eso se enseña como «sin
    datos» o como cero, y las dos pantallas elegirían distinto.
    """
    value = rows.get(label)
    return dict(value) if isinstance(value, dict) else dict(EMPTY_ROW)


def previous_month(label: str) -> str:
    """El mes anterior a `YYYY-MM`, para la comparación de la tarjeta."""
    year, month = (int(part) for part in label.split("-", 1))
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


def month_number(label: str) -> int:
    """El número de mes de `YYYY-MM`: la clave de la media estacional."""
    return int(label.split("-", 1)[1])
