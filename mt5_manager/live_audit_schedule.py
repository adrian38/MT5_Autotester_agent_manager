"""Cuando le toca correr a cada auditoria en vivo.

Aqui solo hay aritmetica y validacion: ni red, ni ficheros, ni estado. El
planificador tenia una unica cadencia en dias; ahora tiene ademas la que pidio
el usuario -un dia de la semana a una hora fija, por defecto el sabado a las
23:00- y una politica de reintentos. Las dos conviven porque la pantalla de
configuracion de cada nodo sigue editando los dias.

La hora es **hora de pared de una zona horaria explicita**. El contenedor del
manager corre en UTC, asi que «las 23:00» sin zona serian las 00:00 o la 01:00
en Espana. La zona se guarda con el resto de la configuracion y, si la imagen
no trae su base de datos, `resolve_zone` cae a UTC y lo dice en vez de mentir
sobre la hora.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS = {
    "enabled": False,
    "mode": "weekly",
    "weekday": 5,
    "hour": 23,
    "minute": 0,
    "timezone": "Europe/Madrid",
    "interval_days": 30,
    "only_live_trading": True,
    "retry_attempts": 3,
    "retry_delay_minutes": 10,
    "chain_from_last_audit": True,
}

MANAGER_ONLY_PROFILE_KEYS = frozenset({
    "autolot", "real_attribution", "live_trading",
    "view_lot_overrides", "reviewed_strategies", "manual_pairs",
})
"""Lo que es criterio del manager y no viaja al nodo.

Se aplica al **leer** el resultado, asi que sobrevive a una ejecucion nueva del
mismo uso: los Excel de atribucion, las parejas decididas a mano, las
estrategias ya revisadas y el escalado de la vista siguen ahi despues de que el
cron vuelva a auditar. El nodo solo recibe lo necesario para auditar.
"""

MODES = ("weekly", "interval")

WEEKDAY_LABELS = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
)

# Claves tecnicas de la primera version. Se aceptan solo para migrar un JSON
# antiguo: no forman parte de la configuracion publica ni se vuelven a guardar.
_LEGACY_LIVE_AUDIT_SCHEDULER_KEYS = frozenset({
    "check_interval_minutes", "startup_delay_seconds",
})

_INTEGER_FIELDS = {
    "weekday": (0, 6),
    "hour": (0, 23),
    "minute": (0, 59),
    "interval_days": (1, 3650),
    "retry_attempts": (0, 10),
    "retry_delay_minutes": (1, 720),
}

_BOOLEAN_FIELDS = ("enabled", "only_live_trading", "chain_from_last_audit")

_TIMEZONE_PATTERN = re.compile(r"[A-Za-z0-9_+/-]{1,64}")


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


def normalize_timezone(value: Any) -> str:
    """Valida la **forma** del nombre, no que la imagen lo conozca.

    Rechazar aqui una zona que falta dejaria la configuracion sin poder
    guardarse en una imagen sin `tzdata`. El aviso lo da `resolve_zone`, que es
    quien de verdad la necesita.
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("timezone no puede estar vacia")
    if not _TIMEZONE_PATTERN.fullmatch(text):
        raise ValueError("timezone debe ser un nombre IANA, como Europe/Madrid")
    return text


def normalize_live_audit_scheduler_settings(
    value: dict[str, Any], defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normaliza la configuracion persistente del programador interno."""
    if not isinstance(value, dict):
        raise ValueError("La configuración automática debe ser un objeto JSON")
    value = {
        key: item for key, item in value.items()
        if key not in _LEGACY_LIVE_AUDIT_SCHEDULER_KEYS
    }
    defaults = {
        key: item for key, item in dict(defaults or {}).items()
        if key not in _LEGACY_LIVE_AUDIT_SCHEDULER_KEYS
    }
    unknown = set(value) - set(DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = {**DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS, **defaults}
    for key in _BOOLEAN_FIELDS:
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"{key} debe ser true o false")
            normalized[key] = value[key]
    for key, (low, high) in _INTEGER_FIELDS.items():
        if key in value:
            normalized[key] = _integer(key, value[key], low, high)
    if "mode" in value:
        mode = str(value["mode"] or "").strip().casefold()
        if mode not in MODES:
            raise ValueError("mode debe ser weekly o interval")
        normalized["mode"] = mode
    if "timezone" in value:
        normalized["timezone"] = normalize_timezone(value["timezone"])
    return normalized


def resolve_zone(name: Any) -> tuple[Any, str]:
    """Devuelve `(tzinfo, aviso)`. Sin la zona pedida, UTC y un aviso."""
    text = str(name or "").strip() or "UTC"
    if text.upper() == "UTC":
        return timezone.utc, ""
    try:
        return ZoneInfo(text), ""
    except (KeyError, ValueError, OSError) as exc:
        return timezone.utc, (
            f"El manager no conoce la zona horaria «{text}» ({exc}): "
            "la programación se interpreta en UTC."
        )


def parse_timestamp(value: Any) -> datetime | None:
    """Una marca ISO del nodo o del estado propio, siempre con zona."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def previous_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    """La ultima vez que el reloj de pared marco el dia y la hora pedidos.

    La cuenta se hace sobre la hora local y sin zona, y la zona se pega al
    final: restar siete dias a un instante con zona es aritmetica absoluta y se
    desplazaria una hora en cada cambio de horario de verano.
    """
    zone, _warning = resolve_zone(settings.get("timezone"))
    weekday = int(settings.get("weekday", 5))
    local = now.astimezone(zone)
    wall = local.replace(
        hour=int(settings.get("hour", 23)), minute=int(settings.get("minute", 0)),
        second=0, microsecond=0, tzinfo=None,
    ) - timedelta(days=(local.weekday() - weekday) % 7)
    candidate = wall.replace(tzinfo=zone)
    if candidate > now:
        candidate = (wall - timedelta(days=7)).replace(tzinfo=zone)
    return candidate


def next_occurrence(now: datetime, settings: dict[str, Any]) -> datetime:
    """La proxima, que es la anterior mas una semana de calendario."""
    previous = previous_occurrence(now, settings)
    wall = previous.replace(tzinfo=None) + timedelta(days=7)
    return wall.replace(tzinfo=previous.tzinfo)


def schedule_label(settings: dict[str, Any]) -> str:
    """Como se le cuenta la cadencia al usuario, en una linea."""
    if str(settings.get("mode") or "") == "interval":
        days = int(settings.get("interval_days", 30))
        return f"cada {days} día{'' if days == 1 else 's'}"
    weekday = WEEKDAY_LABELS[int(settings.get("weekday", 5)) % 7]
    return (
        f"cada {weekday} a las {int(settings.get('hour', 23)):02d}:"
        f"{int(settings.get('minute', 0)):02d} ({settings.get('timezone') or 'UTC'})"
    )


def pending_window(
    settings: dict[str, Any], previous_completion: datetime | None, now: datetime,
) -> tuple[str, bool]:
    """`(clave de la ventana vigente, si la auditoria sigue pendiente en ella)`.

    La clave identifica el turno: en modo semanal es el instante del sabado que
    toca, y en modo dias el momento en que vencio el intervalo. Es lo que
    permite contar reintentos sin confundir dos turnos, y lo que hace que un
    turno nuevo empiece con la cuenta a cero.
    """
    if str(settings.get("mode") or "") == "interval":
        if previous_completion is None:
            return "sin-ejecutar", True
        opened = previous_completion + timedelta(days=int(settings.get("interval_days", 30)))
        return opened.isoformat(), now >= opened
    occurrence = previous_occurrence(now, settings)
    if previous_completion is not None and previous_completion >= occurrence:
        return occurrence.isoformat(), False
    return occurrence.isoformat(), True


def chained_period(
    settings: dict[str, Any], audit: dict[str, Any], now: datetime,
) -> dict[str, str]:
    """El periodo de la proxima auditoria, enganchado al final de la anterior.

    Las operaciones reales se filtran por **hora de cierre**, asi que arrancar
    donde termino la ultima recoge justo las que entonces seguian abiertas y no
    se podian comparar. El ultimo dia se vuelve a cubrir a proposito: la
    auditoria anterior se ejecuto a media noche de ese dia y lo que cerro
    despues no estaba todavia en el historial del terminal.

    Devuelve un diccionario vacio cuando no hay auditoria previa: entonces
    manda el periodo que el usuario configuro en el perfil del uso.
    """
    if not settings.get("chain_from_last_audit", True):
        return {}
    result = audit.get("last_result") if isinstance(audit.get("last_result"), dict) else {}
    previous_end = parse_timestamp(result.get("period_end"))
    if previous_end is None:
        return {}
    # En UTC las dos, porque el nodo convierte estas fechas en dias completos
    # UTC (`_audit_period`): mezclar aqui la hora local movería el corte.
    start_date = previous_end.astimezone(timezone.utc).date()
    end_date = now.astimezone(timezone.utc).date()
    if start_date > end_date:
        return {}
    return {
        "period_mode": "fixed_dates",
        "period_start_date": start_date.isoformat(),
        "period_end_date": end_date.isoformat(),
    }


def due_decision(
    settings: dict[str, Any], record: dict[str, Any], window: str, now: datetime,
) -> str:
    """Que toca con una auditoria pendiente: `first`, `retry`, `exhausted` o nada.

    `record` es lo que el vigilante recuerda de esa auditoria en ese turno.
    Un turno nuevo siempre se lanza; dentro del mismo turno solo se vuelve a
    lanzar lo que fallo, con su espera y hasta agotar los reintentos.
    """
    if str(record.get("window") or "") != window:
        return "first"
    if str(record.get("outcome") or "") != "failed":
        return ""
    attempts = int(record.get("attempts") or 0)
    if attempts > int(settings.get("retry_attempts", 3)):
        return "exhausted"
    last_attempt = parse_timestamp(record.get("last_attempt_at"))
    delay = timedelta(minutes=int(settings.get("retry_delay_minutes", 10)))
    if last_attempt is not None and now - last_attempt < delay:
        return ""
    return "retry"
