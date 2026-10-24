"""Cuándo le toca sondear a las cuentas reales, y en qué moneda se suman.

Mismo reparto que `live_audit_schedule`: aquí sólo hay aritmética y validación,
ni red, ni ficheros, ni estado. Lo que cambia es la cadencia. El cron de
auditoría tiene **un** día de la semana (`weekday`); éste tiene un **conjunto**
(`weekdays`), porque lo que se pidió es «de lunes a viernes a las 23:00».

No es un modo nuevo dentro del cron de auditoría a propósito: ese registro ya
decide citas semanales que funcionan y tocarlo para añadir días movería la cita
del sábado. Son dos crones con dos registros, y lo común —la zona horaria, el
instante con zona y la política de reintentos— se importa de allí en vez de
volver a escribirse, que es lo que haría que los dos divergieran.

La hora es **hora de pared de una zona explícita**, con el mismo cuidado: la
cuenta se hace sobre la hora local sin zona y la zona se pega al final, porque
restar días a un instante con zona desplazaría la cita una hora en cada cambio
de horario de verano.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .live_audit_schedule import (  # noqa: F401  (API de este módulo)
    WEEKDAY_LABELS, audit_id_created_at, due_decision, normalize_timezone,
    parse_timestamp, resolve_zone,
)

DEFAULT_LIVE_ACCOUNT_SCHEDULER_SETTINGS = {
    "enabled": False,
    "weekdays": [0, 1, 2, 3, 4],
    "hour": 23,
    "minute": 0,
    "timezone": "Europe/Madrid",
    "retry_attempts": 3,
    "retry_delay_minutes": 10,
    "overlap_days": 3,
    "display_currency": "EUR",
    "exchange_rates": {"USD": 0.92},
    "rates_updated_at": "",
}

_INTEGER_FIELDS = {
    "hour": (0, 23),
    "minute": (0, 59),
    "retry_attempts": (0, 10),
    "retry_delay_minutes": (1, 720),
    "overlap_days": (0, 30),
}

_BOOLEAN_FIELDS = ("enabled",)

_CURRENCY_PATTERN = re.compile(r"[A-Za-z]{3}")


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


def normalize_weekdays(value: Any) -> list[int]:
    """Los días de la cita, sin repetidos y en orden. Vacío no vale.

    Un conjunto vacío no es «nunca»: para eso está el interruptor. Dejarlo
    pasar convertiría el cron en algo que no se lanza y que además dice que
    está activo.
    """
    if isinstance(value, bool) or not isinstance(value, (list, tuple, set)):
        raise ValueError("weekdays debe ser una lista de días (0 lunes … 6 domingo)")
    days = sorted({_integer("weekdays", item, 0, 6) for item in value})
    if not days:
        raise ValueError("weekdays necesita al menos un día")
    return days


def normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(text):
        raise ValueError("La moneda debe ser un código de tres letras, como EUR")
    return text


def normalize_rates(value: Any) -> dict[str, float]:
    """Cambios hacia la moneda de la portada, uno por divisa.

    Es un valor escrito a mano porque el manager no tiene ninguna fuente de
    cambios: por eso se guarda junto a su fecha y la pantalla la enseña. Un
    cambio que falta **no** se inventa con un 1,0; quien suma lo dice.
    """
    if not isinstance(value, dict):
        raise ValueError("exchange_rates debe ser un objeto {moneda: cambio}")
    rates: dict[str, float] = {}
    for currency, rate in value.items():
        try:
            number = float(rate)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"El cambio de {currency} debe ser un número") from exc
        if not number > 0 or number > 1e6:
            raise ValueError(f"El cambio de {currency} debe ser mayor que cero")
        rates[normalize_currency(currency)] = number
    return rates


def normalize_live_account_scheduler_settings(
    value: dict[str, Any], defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normaliza la configuración persistente del cron de cuentas."""
    if not isinstance(value, dict):
        raise ValueError("La configuración del sondeo debe ser un objeto JSON")
    unknown = set(value) - set(DEFAULT_LIVE_ACCOUNT_SCHEDULER_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = {**DEFAULT_LIVE_ACCOUNT_SCHEDULER_SETTINGS, **dict(defaults or {})}
    for key in _BOOLEAN_FIELDS:
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"{key} debe ser true o false")
            normalized[key] = value[key]
    for key, (low, high) in _INTEGER_FIELDS.items():
        if key in value:
            normalized[key] = _integer(key, value[key], low, high)
    if "weekdays" in value:
        normalized["weekdays"] = normalize_weekdays(value["weekdays"])
    if "timezone" in value:
        normalized["timezone"] = normalize_timezone(value["timezone"])
    if "display_currency" in value:
        normalized["display_currency"] = normalize_currency(value["display_currency"])
    if "exchange_rates" in value:
        normalized["exchange_rates"] = normalize_rates(value["exchange_rates"])
    if "rates_updated_at" in value:
        normalized["rates_updated_at"] = str(value["rates_updated_at"] or "")[:40]
    return normalized


def _wall(now: datetime, settings: dict[str, Any]) -> tuple[datetime, Any, list[int]]:
    zone, _warning = resolve_zone(settings.get("timezone"))
    local = now.astimezone(zone)
    wall = local.replace(
        hour=int(settings.get("hour", 23)), minute=int(settings.get("minute", 0)),
        second=0, microsecond=0, tzinfo=None,
    )
    return wall, zone, normalize_weekdays(
        settings.get("weekdays") or DEFAULT_LIVE_ACCOUNT_SCHEDULER_SETTINGS["weekdays"]
    )


def previous_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    """La última vez que el reloj de pared marcó uno de esos días a esa hora."""
    wall, zone, days = _wall(now, settings)
    for offset in range(0, 8):
        candidate_wall = wall - timedelta(days=offset)
        if candidate_wall.weekday() not in days:
            continue
        candidate = candidate_wall.replace(tzinfo=zone)
        if candidate <= now:
            return candidate
    return (wall - timedelta(days=8)).replace(tzinfo=zone)


def next_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    """La próxima, que puede ser hoy mismo si la hora aún no ha pasado."""
    wall, zone, days = _wall(now, settings)
    for offset in range(0, 9):
        candidate_wall = wall + timedelta(days=offset)
        if candidate_wall.weekday() not in days:
            continue
        candidate = candidate_wall.replace(tzinfo=zone)
        if candidate > now:
            return candidate
    return (wall + timedelta(days=9)).replace(tzinfo=zone)


def weekday_label(days: list[int]) -> str:
    """Los días, dichos como los diría alguien: «de lunes a viernes»."""
    if len(days) == 7:
        return "todos los días"
    if days == [0, 1, 2, 3, 4]:
        return "de lunes a viernes"
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        return f"de {WEEKDAY_LABELS[days[0]]} a {WEEKDAY_LABELS[days[-1]]}"
    names = [WEEKDAY_LABELS[day] for day in days]
    if len(names) == 1:
        return f"cada {names[0]}"
    return f"{', '.join(names[:-1])} y {names[-1]}"


def schedule_label(settings: dict[str, Any]) -> str:
    days = normalize_weekdays(
        settings.get("weekdays") or DEFAULT_LIVE_ACCOUNT_SCHEDULER_SETTINGS["weekdays"]
    )
    return (
        f"{weekday_label(days)} a las {int(settings.get('hour', 23)):02d}:"
        f"{int(settings.get('minute', 0)):02d} ({settings.get('timezone') or 'UTC'})"
    )


def schedule_description(settings: dict[str, Any]) -> str:
    """Resumen fiel de lo que hace el sondeo, para el diálogo."""
    overlap = int(settings.get("overlap_days", 3))
    return (
        f"El manager lee las cuentas reales de los portafolios activos "
        f"{schedule_label(settings)}, un agente a la vez. Cada cuenta se abre en "
        "una terminal del agente para exportar su informe nativo, así que el "
        "sondeo pausa el pipeline mientras dura y devuelve las terminales a su "
        f"cuenta final. Reescribe los últimos {overlap} día(s) de la serie: a "
        "las 23:00 el día UTC aún no ha cerrado, y el sondeo siguiente lo "
        "corrige. Si una cita venció con el manager apagado, se recupera al "
        "volver."
    )


def pending_window(
    settings: dict[str, Any], previous_completion: datetime | None, now: datetime,
    eligible_since: datetime | None = None,
) -> tuple[str, bool]:
    """`(clave del turno vigente, si el sondeo sigue pendiente en él)`.

    La clave es el instante de la cita que toca, igual que en el cron semanal:
    es lo que permite contar reintentos sin confundir dos turnos y lo que hace
    que un turno nuevo empiece con la cuenta a cero.
    """
    occurrence = previous_occurrence(now, settings)
    if eligible_since is not None and eligible_since > occurrence:
        return occurrence.isoformat(), False
    if previous_completion is not None and previous_completion >= occurrence:
        return occurrence.isoformat(), False
    return occurrence.isoformat(), True


def snapshot_period(
    settings: dict[str, Any], last_day: str, now: datetime,
) -> tuple[str, str]:
    """Qué rango se le pide al agente: desde dónde y hasta hoy.

    Sin serie previa se pide **toda la vida de la cuenta**, que es lo que hace
    que la media y los meses anteriores existan desde el primer sondeo. Con
    serie, se reescriben los últimos días a propósito: el sondeo de las 23:00
    de Madrid ve un día UTC a medias, y el de la noche siguiente lo completa.
    """
    end = now.date()
    if not last_day:
        return "", end.isoformat()
    try:
        start = datetime.fromisoformat(last_day).date()
    except ValueError:
        return "", end.isoformat()
    start -= timedelta(days=int(settings.get("overlap_days", 3)))
    return min(start, end).isoformat(), end.isoformat()


def convert(amount: float, currency: str, settings: dict[str, Any]) -> float | None:
    """`amount` en la moneda de la portada, o `None` si falta su cambio.

    Devolver el importe crudo cuando falta el cambio es lo único que no se
    puede hacer: sumaría dólares a euros y el total de arriba sería un número
    falso sin que nada lo dijera.
    """
    display = normalize_currency(settings.get("display_currency") or "EUR")
    source = str(currency or "").strip().upper()
    if not source or source == display:
        return float(amount)
    rate = (settings.get("exchange_rates") or {}).get(source)
    try:
        number = float(rate)
    except (TypeError, ValueError):
        return None
    return float(amount) * number if number > 0 else None
