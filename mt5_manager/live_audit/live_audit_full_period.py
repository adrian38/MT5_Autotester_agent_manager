"""Ventana acumulada de la auditoría larga mensual."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable

from . import live_audit_executions, live_audit_lineage, live_audit_schedule
from ..common import safe_int


def completion_changes(record: dict[str, Any], outcome: str) -> dict[str, Any]:
    """Avanza el corte sólo cuando la larga terminó con éxito."""
    if outcome != "success" or record.get("period_kind") != "monthly_full":
        return {}
    return {"last_full_audit_end": record.get("full_period_end")}


def oldest_active_start(
    server: Any, scheduled_ids: Callable[..., list[str]], now: datetime,
) -> datetime | None:
    """Fecha configurada más antigua entre los usos que atiende el cron."""
    starts: list[datetime] = []
    settings = server.live_audit_scheduler_settings
    for node in server.nodes:
        state = server.live_audit_settings.state(str(node.get("id") or ""))
        profiles = state.get("profiles") or {}
        for audit_id in scheduled_ids(state, settings):
            start = live_audit_schedule.configured_start(
                dict(profiles.get(audit_id) or {}), now,
            )
            if start is not None:
                starts.append(start)
    return min(starts) if starts else None


def audit_window(
    server: Any, node_id: str, profile: dict[str, Any], record: dict[str, Any],
    window: str, scheduled_ids: Callable[..., list[str]],
) -> tuple[date, date] | None:
    """Los dos extremos del tramo acumulado, sin juzgar si caben días dentro.

    Separado de `audit_period` porque el vigilante necesita distinguir dos
    cosas que el diccionario vacío confunde: que no se pueda fijar la ventana
    —y entonces manda el encadenado de siempre— y que la ventana exista pero
    esté vacía, que es una cita que no hay que lanzar.
    """
    end = live_audit_schedule.parse_timestamp(window)
    if end is None:
        return None
    start = live_audit_schedule.parse_timestamp(record.get("last_full_audit_end"))
    start = start or oldest_active_start(server, scheduled_ids, end)
    start = start or live_audit_schedule.configured_start(profile, end)
    _inherited, created_at = live_audit_lineage.audit_period_bounds(server, node_id, profile)
    if created_at is not None and (start is None or created_at > start):
        start = created_at
    if start is None:
        return None
    return start.astimezone(timezone.utc).date(), end.astimezone(timezone.utc).date()


def has_days(bounds: tuple[date, date] | None) -> bool:
    """Si el tramo acumulado contiene algún día que auditar.

    El final de una larga es siempre el día de la cita, el sábado del cron. Un
    tramo que arranca ahí mismo —porque el uso nació ese día— sólo contiene ese
    sábado, con el mercado cerrado, así que no hay nada que comparar. Pedírselo
    igual al nodo no da una auditoría vacía: MT5 ni siquiera arranca el
    Strategy Tester (`-1000012351`, «tester didn't start») y el runner lo cuenta
    como «no se encontró ningún reporte», un trabajo por estrategia. Pasó el
    2026-11-02 con el portafolio #185 de RoboForex, creado ese mismo sábado:
    ocho fallos, dos reintentos idénticos y el aviso de reintentos agotados.
    """
    return bounds is not None and bounds[0] < bounds[1]


def period_fields(bounds: tuple[date, date] | None) -> dict[str, str]:
    """El tramo, en las fechas fijas que entiende el nodo; vacío si no hay."""
    if bounds is None or not has_days(bounds):
        return {}
    start_date, end_date = bounds
    return {
        "period_mode": "fixed_dates", "period_start_date": start_date.isoformat(),
        "period_end_date": end_date.isoformat(),
    }


def audit_period(
    server: Any, node_id: str, profile: dict[str, Any], record: dict[str, Any],
    window: str, scheduled_ids: Callable[..., list[str]],
) -> dict[str, str]:
    """Desde el último corte largo; en el primero, desde el origen activo."""
    return period_fields(
        audit_window(server, node_id, profile, record, window, scheduled_ids),
    )


def close_empty_turn(
    server: Any, node: dict[str, Any], node_id: str, audit_id: str,
    profile: dict[str, Any], window: str, end: date,
) -> None:
    """Da la cita por cumplida sin pedirle nada al nodo, y lo dice.

    Vive aquí y no en el vigilante porque es la otra mitad de `has_days`, y
    porque escribe el mismo corte que `completion_changes`: la larga siguiente
    arranca donde habría terminado ésta, así que no se pierde ningún día.
    """
    runs = server.live_audit_runs
    runs.update(
        runs.key(node_id, audit_id), window=window, attempts=0, outcome="success",
        last_full_audit_end=end.isoformat(),
    )
    server.live_audit_notifications.add(
        "info",
        "Auditoría larga sin tramo: "
        f"{live_audit_executions.audit_label(profile, audit_id)}",
        f"El tramo acumulado empieza y termina el mismo día ({end.isoformat()}), "
        "así que no hay nada que auditar. No se ha lanzado y la larga siguiente "
        "arrancará ahí.",
        node_id=node_id, node_name=str(node.get("name") or node_id), audit_id=audit_id,
        portfolio_id=safe_int(profile.get("portfolio_id"), 0, minimum=0),
        kind="full_audit_empty",
    )
