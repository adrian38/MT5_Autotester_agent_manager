"""Cuándo se relee la vida entera de cada cuenta, para el patrimonio.

Es un cron **distinto** del sondeo diario, y lo es por lo que pide:

- El diario relee los últimos días (`overlap_days`) porque lo que mira la
  portada es hoy y este mes. Con esa ventana, los ingresos y las retiradas de
  hace medio año no entran nunca: están repartidos por toda la vida de la
  cuenta.
- El del patrimonio pide el informe **completo** de cada cuenta. Trae todos los
  apuntes de saldo de una vez, repara un histórico que se quedó a medias y
  vuelve a cuadrar los meses viejos. Y es caro: el informe de una cuenta con un
  año de operación son nueve megas, y cada cuenta se abre en una terminal del
  agente.

Por eso tiene su propia cita, semanal, y por eso **espera a que el agente esté
libre** en vez de entrar en mitad de unas pruebas (`live_account_wealth_watch`).
Compartir el registro del cron diario habría hecho que cambiar una cadencia
moviera la otra, que es exactamente lo que ya se decidió no hacer cuando el
cron de cuentas se separó del de auditoría.

Aquí sólo hay aritmética y validación: ni red, ni ficheros, ni estado. La
cuenta de días la hace `live_account_schedule`, que ya sabe tratar una cita de
pared con zona explícita; una cita semanal es su caso de un solo día.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .live_account_schedule import (  # noqa: F401  (API de este módulo)
    WEEKDAY_LABELS, next_occurrence as _next, normalize_timezone,
    pending_window as _pending, previous_occurrence as _previous, resolve_zone,
)

DEFAULT_WEALTH_SCHEDULER_SETTINGS = {
    "enabled": False,
    "weekday": 5,
    "hour": 7,
    "minute": 0,
    "timezone": "Europe/Madrid",
    "wait_hours": 36,
}
"""Sábado a las 07:00 por defecto, y hasta 36 horas esperando al agente.

El sábado porque la relectura completa ocupa las terminales y el fin de semana
es cuando menos estorba. Las 36 horas son el tope de la espera: un agente que
lleva día y medio ocupado no es «ocupado un rato», y la cita se da por perdida
en vez de caer el lunes a media mañana sin avisar.
"""

_INTEGER_FIELDS = {
    "weekday": (0, 6),
    "hour": (0, 23),
    "minute": (0, 59),
    "wait_hours": (1, 168),
}


def _integer(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} debe ser un entero entre {low} y {high}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser un entero entre {low} y {high}") from exc
    if not low <= number <= high:
        raise ValueError(f"{name} debe estar entre {low} y {high}")
    return number


def normalize_wealth_scheduler_settings(
    value: dict[str, Any], defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normaliza la configuración persistente de la relectura completa."""
    if not isinstance(value, dict):
        raise ValueError("La configuración de la relectura debe ser un objeto JSON")
    unknown = set(value) - set(DEFAULT_WEALTH_SCHEDULER_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = {**DEFAULT_WEALTH_SCHEDULER_SETTINGS, **dict(defaults or {})}
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ValueError("enabled debe ser true o false")
        normalized["enabled"] = value["enabled"]
    for key, (low, high) in _INTEGER_FIELDS.items():
        if key in value:
            normalized[key] = _integer(key, value[key], low, high)
    if "timezone" in value:
        normalized["timezone"] = normalize_timezone(value["timezone"])
    return normalized


def weekly(settings: dict[str, Any]) -> dict[str, Any]:
    """La misma cita, en la forma que entiende `live_account_schedule`.

    Una cita semanal es un conjunto de días con un solo día dentro. Se traduce
    aquí y no se copia la aritmética: dos implementaciones de «la última vez
    que el reloj de pared marcó esa hora» acabarían discrepando en el cambio
    de horario de verano, que es justo donde esto es difícil.
    """
    return {
        "weekdays": [int(settings.get("weekday", 5))],
        "hour": int(settings.get("hour", 7)),
        "minute": int(settings.get("minute", 0)),
        "timezone": settings.get("timezone") or "Europe/Madrid",
    }


def next_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    return _next(now, weekly(settings))


def previous_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    return _previous(now, weekly(settings))


def pending_window(
    settings: dict[str, Any], previous_completion: datetime | None, now: datetime,
) -> tuple[str, bool]:
    """`(clave del turno vigente, si la relectura sigue pendiente en él)`."""
    return _pending(weekly(settings), previous_completion, now)


def schedule_label(settings: dict[str, Any]) -> str:
    day = WEEKDAY_LABELS[int(settings.get("weekday", 5))]
    return (
        f"cada {day} a las {int(settings.get('hour', 7)):02d}:"
        f"{int(settings.get('minute', 0)):02d} ({settings.get('timezone') or 'UTC'})"
    )


def schedule_description(settings: dict[str, Any]) -> str:
    """Resumen fiel de lo que hace la relectura, para el diálogo."""
    return (
        "El manager pide el informe **completo** de cada cuenta que suma en el "
        f"patrimonio, {schedule_label(settings)}. Es lo que trae todos los "
        "ingresos, las retiradas y los traspasos: el sondeo diario sólo relee "
        "los últimos días, así que por sí solo nunca completa el histórico. "
        "Cada cuenta se abre en una terminal del agente, de modo que la cita "
        f"**espera a que el agente esté libre** —hasta {int(settings.get('wait_hours', 36))} "
        "hora(s)— en vez de entrar en mitad de una generación, una auditoría o "
        "unas pruebas. Si la espera se agota, la cita se da por perdida y se "
        "vuelve a intentar en la siguiente."
    )
