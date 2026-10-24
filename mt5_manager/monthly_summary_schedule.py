"""Cuándo toca el resumen del mes, y con qué listones se lee.

Mismo reparto que `live_audit_schedule` y `live_account_schedule`: aquí sólo
hay aritmética y validación, ni red, ni ficheros, ni estado. Lo que cambia es
la cadencia. Aquélla es un día de la semana, aquélla otra un conjunto de días;
ésta es **un día del mes**, porque lo que se resume es un mes natural cerrado.

Tres decisiones que no se deducen del código:

- **El día se limita a 1…28.** Un 31 no existe en febrero, y una cita que unos
  meses no llega es una cita que calla justo cuando toca decidir. El resumen
  es del mes anterior, así que correrlo el día 3 no cambia lo que mide.
- **Se arma de fábrica**, al revés que los otros dos crones. Los suyos lanzan
  algo en un agente —una auditoría pausa el pipeline— y por eso nacen
  desarmados. Éste sólo lee lo que ya está archivado: no hay nada que pueda
  romper, y un resumen que hay que acordarse de encender no sirve de aviso.
- **La hora es hora de pared de una zona explícita**, con el mismo cuidado: la
  cuenta se hace sobre la hora local sin zona y la zona se pega al final.
  Restar meses a un instante con zona desplazaría la cita una hora en cada
  cambio de horario de verano.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .live_audit_schedule import (  # noqa: F401  (API de este módulo)
    normalize_timezone, parse_timestamp, resolve_zone,
)

DEFAULT_MONTHLY_SUMMARY_SETTINGS = {
    "enabled": True,
    "day": 1,
    "hour": 9,
    "minute": 0,
    "timezone": "Europe/Madrid",
    "below_streak_months": 3,
    "retry_attempts": 3,
    "retry_delay_minutes": 60,
}

_INTEGER_FIELDS = {
    "day": (1, 28),
    "hour": (0, 23),
    "minute": (0, 59),
    "below_streak_months": (2, 12),
    "retry_attempts": (0, 10),
    "retry_delay_minutes": (1, 720),
}

_BOOLEAN_FIELDS = ("enabled",)

MONTH_NAMES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


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


def normalize_monthly_summary_settings(
    value: dict[str, Any], defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normaliza la configuración persistente del cron del resumen."""
    if not isinstance(value, dict):
        raise ValueError("La configuración del resumen debe ser un objeto JSON")
    unknown = set(value) - set(DEFAULT_MONTHLY_SUMMARY_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = {**DEFAULT_MONTHLY_SUMMARY_SETTINGS, **dict(defaults or {})}
    for key in _BOOLEAN_FIELDS:
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"{key} debe ser true o false")
            normalized[key] = value[key]
    for key, (low, high) in _INTEGER_FIELDS.items():
        if key in value:
            normalized[key] = _integer(key, value[key], low, high)
    if "timezone" in value:
        normalized["timezone"] = normalize_timezone(value["timezone"])
    return normalized


def shift_month(label: str, delta: int) -> str:
    """`YYYY-MM` desplazado `delta` meses, hacia atrás con negativos."""
    year, month = (int(part) for part in label.split("-", 1))
    index = year * 12 + (month - 1) + int(delta)
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_label(moment: datetime) -> str:
    """`YYYY-MM` de un instante, sin tocar su zona."""
    return f"{moment.year:04d}-{moment.month:02d}"


def month_name(label: str) -> str:
    """«octubre de 2026», que es como se nombra un mes en la pantalla."""
    try:
        year, month = (int(part) for part in label.split("-", 1))
        return f"{MONTH_NAMES[month - 1]} de {year:04d}"
    except (IndexError, ValueError):
        return str(label)


def _occurrence(label: str, settings: dict[str, Any], zone: Any) -> datetime:
    """La cita de ese mes: su día y su hora de pared, con la zona pegada."""
    year, month = (int(part) for part in label.split("-", 1))
    wall = datetime(
        year, month, _integer("day", settings.get("day", 1), 1, 28),
        _integer("hour", settings.get("hour", 9), 0, 23),
        _integer("minute", settings.get("minute", 0), 0, 59),
    )
    return wall.replace(tzinfo=zone)


def previous_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    """La última vez que el calendario marcó ese día del mes a esa hora."""
    zone, _warning = resolve_zone(settings.get("timezone"))
    label = month_label(now.astimezone(zone))
    candidate = _occurrence(label, settings, zone)
    if candidate <= now:
        return candidate
    return _occurrence(shift_month(label, -1), settings, zone)


def next_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    """La próxima, que puede ser hoy mismo si la hora aún no ha pasado."""
    zone, _warning = resolve_zone(settings.get("timezone"))
    label = month_label(now.astimezone(zone))
    candidate = _occurrence(label, settings, zone)
    if candidate > now:
        return candidate
    return _occurrence(shift_month(label, 1), settings, zone)


def target_month(occurrence: datetime) -> str:
    """El mes que esa cita resume: el anterior al suyo, ya cerrado.

    Un resumen del mes en curso no decide nada —faltan días por operar— y es
    justo lo que la portada ya enseña prorrateado. Lo que aquí se mide es un
    mes completo, que es la unidad que la cita del día 1 deja atrás.
    """
    return shift_month(month_label(occurrence), -1)


def pending_window(
    settings: dict[str, Any], previous_completion: datetime | None, now: datetime,
) -> tuple[str, bool]:
    """`(clave del turno vigente, si el resumen sigue pendiente en él)`.

    La clave es el instante de la cita que toca, igual que en los otros dos
    crones: es lo que permite contar reintentos sin confundir dos turnos y lo
    que hace que un turno nuevo empiece con la cuenta a cero.
    """
    occurrence = previous_occurrence(now, settings)
    if previous_completion is not None and previous_completion >= occurrence:
        return occurrence.isoformat(), False
    return occurrence.isoformat(), True


def schedule_label(settings: dict[str, Any]) -> str:
    day = _integer("day", settings.get("day", 1), 1, 28)
    return (
        f"el día {day} de cada mes a las {int(settings.get('hour', 9)):02d}:"
        f"{int(settings.get('minute', 0)):02d} ({settings.get('timezone') or 'UTC'})"
    )


def schedule_description(settings: dict[str, Any]) -> str:
    """Resumen fiel de lo que hace el cron, para el diálogo."""
    streak = _integer(
        "below_streak_months", settings.get("below_streak_months", 3), 2, 12,
    )
    return (
        f"El manager resume {schedule_label(settings)} el mes natural que acaba "
        "de cerrar: lo que ganó cada cuenta real frente a lo que ese mes "
        "prometía el backtest, su drawdown frente al de los meses del propio "
        "backtest y las estrategias que degradan. **No lanza nada**: lee lo "
        "archivado por el sondeo de cuentas y por la auditoría, y declara los "
        "días del mes que se quedaron sin auditar. Un mes flojo no decide; "
        f"{streak} meses seguidos por debajo sí. Si la cita venció con el "
        "manager apagado, se recupera al volver."
    )
