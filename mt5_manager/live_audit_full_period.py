"""Ventana acumulada de la auditoría larga mensual."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from . import live_audit_lineage, live_audit_schedule


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


def audit_period(
    server: Any, node_id: str, profile: dict[str, Any], record: dict[str, Any],
    window: str, scheduled_ids: Callable[..., list[str]],
) -> dict[str, str]:
    """Desde el último corte largo; en el primero, desde el origen activo."""
    end = live_audit_schedule.parse_timestamp(window)
    if end is None:
        return {}
    start = live_audit_schedule.parse_timestamp(record.get("last_full_audit_end"))
    start = start or oldest_active_start(server, scheduled_ids, end)
    start = start or live_audit_schedule.configured_start(profile, end)
    _inherited, created_at = live_audit_lineage.audit_period_bounds(server, node_id, profile)
    if created_at is not None and (start is None or created_at > start):
        start = created_at
    if start is None:
        return {}
    start_date = start.astimezone(timezone.utc).date()
    end_date = end.astimezone(timezone.utc).date()
    if start_date > end_date:
        return {}
    return {
        "period_mode": "fixed_dates", "period_start_date": start_date.isoformat(),
        "period_end_date": end_date.isoformat(),
    }
