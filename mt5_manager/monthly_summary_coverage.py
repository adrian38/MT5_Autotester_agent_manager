"""Cuánto del mes se llegó a mirar de verdad, antes de concluir nada.

Un resumen mensual que no diga esto miente por omisión. El cron de auditoría
es semanal y el mes no cierra en sábado, así que al resumir octubre pueden
faltar hasta seis días sin auditar; el sondeo de cuentas es de lunes a viernes
y puede llevar días caído. En los dos casos los números salen igual —más
pequeños— y **nada en ellos dice que están incompletos**. Es exactamente el
fallo silencioso que describe `CLAUDE.md` sobre el grafo congelado: contesta
con cifras viejas y un mes recién operado simplemente «no existe».

Por eso el veredicto de una tarjeta sin cobertura no es MANTENER: es SIN
CONCLUSIÓN. Decir «el portafolio va bien» con la mitad del mes sin auditar es
peor que no decir nada, porque se parece a una respuesta.

Aquí sólo hay aritmética de intervalos: recibe las entradas del índice del
archivo —que ya traen `period_start` y `period_end`— y el registro guardado de
la cuenta. No abre ninguna ejecución: el índice basta, y abrir cuatrocientos
ficheros para contar días sería pagar megabytes por una resta.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .live_audit_schedule import parse_timestamp

SERIES_STALE_DAYS = 3
"""Cuántos días puede faltarle a la serie de la cuenta antes de acusarla.

El sondeo corre de lunes a viernes, así que un mes que cierra en viernes tiene
su último día completo el lunes siguiente. Tres días cubren el fin de semana
sin llamar vieja a una serie que está al día.
"""


def month_bounds(label: str) -> tuple[datetime, datetime]:
    """`[inicio, fin)` del mes en UTC, que es como se corta todo lo demás."""
    year, month = (int(part) for part in label.split("-", 1))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    days = calendar.monthrange(year, month)[1]
    return start, start + timedelta(days=days)


def month_days(label: str) -> int:
    year, month = (int(part) for part in label.split("-", 1))
    return calendar.monthrange(year, month)[1]


def _clipped(
    entry: dict[str, Any], start: datetime, end: datetime,
) -> tuple[datetime, datetime] | None:
    """El periodo de esa ejecución recortado al mes, o nada si no lo toca."""
    first = parse_timestamp(entry.get("period_start"))
    last = parse_timestamp(entry.get("period_end"))
    if first is None or last is None or last <= first:
        return None
    low, high = max(first, start), min(last, end)
    return (low, high) if high > low else None


def audited_days(entries: Iterable[dict[str, Any]], label: str) -> float:
    """Días del mes cubiertos por alguna auditoría, sin contar dos veces.

    Los periodos se encadenan repitiendo a propósito el último día de la
    auditoría anterior (`live_audit_schedule.chained_period`), así que sumarlos
    uno a uno inflaría la cobertura justo en lo que se solapa. Es la misma
    unión que hace `live_audit_history.covered_days`, recortada al mes.
    """
    start, end = month_bounds(label)
    intervals = sorted(
        filter(None, (_clipped(entry, start, end) for entry in entries)),
        key=lambda pair: pair[0],
    )
    total, current = 0.0, None
    for first, last in intervals:
        if current and first <= current[1]:
            current = (current[0], max(current[1], last))
            continue
        if current:
            total += (current[1] - current[0]).total_seconds()
        current = (first, last)
    if current:
        total += (current[1] - current[0]).total_seconds()
    return total / 86400


def audit_coverage(entries: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """Qué parte del mes auditó este uso, y hasta dónde llegó."""
    days = month_days(label)
    covered = min(audited_days(entries, label), float(days))
    ends = [
        stamp for stamp in (
            parse_timestamp(entry.get("period_end")) for entry in entries
        ) if stamp is not None
    ]
    return {
        "days": covered, "month_days": days,
        "pct": covered * 100.0 / days if days else 0.0,
        "missing_days": max(0.0, days - covered),
        "audits": len(entries),
        "last_period_end": max(ends).isoformat() if ends else "",
    }


def series_coverage(stored: dict[str, Any], label: str) -> dict[str, Any]:
    """Si la serie de la cuenta llega al final del mes que se resume.

    `last_day` es el último día con algún cierre, no el último sondeado: una
    cuenta que no operó los tres últimos días del mes lo tiene antes del cierre
    sin que le falte nada. Por eso manda `updated_at`, que es cuándo se leyó.
    """
    _start, end = month_bounds(label)
    read = parse_timestamp(stored.get("updated_at"))
    needed = end + timedelta(days=SERIES_STALE_DAYS)
    return {
        "updated_at": str(stored.get("updated_at") or ""),
        "last_day": str(stored.get("last_day") or ""),
        "first_day": str(stored.get("first_day") or ""),
        "never_read": read is None,
        # Vieja es «se leyó antes de que el mes terminase de cuajar», no «se
        # leyó hace mucho»: un resumen de marzo mirado en octubre está bien
        # medido aunque su lectura tenga medio año.
        "stale": read is None or read < min(needed, datetime.now(timezone.utc)),
    }


def started_after(created_day: str, label: str) -> bool:
    """Si el portafolio nació dentro del mes que se resume o después.

    Un portafolio guardado el día 20 no tuvo octubre: tuvo once días. Medir
    esos once contra la promesa de un octubre entero lo deja por debajo
    siempre, y esa diferencia no significa nada —es el mismo suelo que
    `ai_context/live_audit_cron_and_bell.md` puso a la auditoría—.
    """
    day = str(created_day or "")[:10]
    return bool(day) and day[:7] >= label
