"""Recuperacion explicita de huecos entre auditorias programadas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from . import live_audit_lineage, live_audit_schedule
from .live_audit_core import RUNNING_STATUSES
from .live_audit_executions import audit_label


AuditIds = Callable[[dict[str, Any], dict[str, Any]], list[str]]
CreatedAt = Callable[[str], datetime | None]
Launch = Callable[[Any, dict[str, Any], str, str, dict[str, Any], dict[str, Any]], None]


def request(server: Any, audit_ids: AuditIds, created_at: CreatedAt,
            now: datetime | None = None) -> dict[str, Any]:
    """Encola los usos reales hasta la ultima cita que ya vencio."""
    now = now or datetime.now(timezone.utc)
    settings = server.live_audit_scheduler_settings
    cutoff = live_audit_schedule.previous_occurrence(now, settings) if (
        settings.get("mode") == "weekly"
    ) else now
    target = cutoff.astimezone(timezone.utc).date().isoformat()
    queued: list[dict[str, Any]] = []
    forced = {**settings, "only_live_trading": True}
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        state = server.live_audit_settings.state(node_id)
        if not (state.get("restore_account") or {}).get("configured"):
            continue
        profiles = state.get("profiles") or {}
        for audit_id in audit_ids(state, forced):
            profile = dict(profiles.get(audit_id) or {})
            use_created = created_at(audit_id)
            born_after = _born_after(server, node_id, profile, cutoff) or (
                use_created is not None and use_created > cutoff
            )
            server.live_audit_runs.update(
                server.live_audit_runs.key(node_id, audit_id),
                catch_up_until=target,
                catch_up_cutoff=cutoff.astimezone(timezone.utc).isoformat(),
                catch_up_status="not_applicable" if born_after else "pending",
                catch_up_requested_at=now.isoformat(),
                window="", attempts=0, outcome="", last_attempt_at="",
            )
            if born_after:
                continue
            queued.append({
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "audit_id": audit_id, "label": audit_label(profile, audit_id),
            })
    return {"target_date": target, "queued": queued, "queued_count": len(queued)}


def _period(server: Any, node_id: str, profile: dict[str, Any],
            audit: dict[str, Any], target: datetime) -> dict[str, str]:
    inherited, created = live_audit_lineage.audit_period_bounds(server, node_id, profile)
    forced = {**server.live_audit_scheduler_settings, "chain_from_last_audit": True}
    return live_audit_schedule.chained_period(forced, audit, target, inherited, created)


def _born_after(server: Any, node_id: str, profile: dict[str, Any],
                target: datetime) -> bool:
    """Si el portafolio nacio despues del corte no hay hueco que recuperar."""
    _inherited, created = live_audit_lineage.audit_period_bounds(server, node_id, profile)
    return created is not None and created > target


def restore_legacy_markers(records: dict[str, dict[str, Any]]) -> bool:
    """Recupera la marca que la primera version borraba al terminar la tanda."""
    changed = False
    for record in records.values():
        if not record.get("catch_up_requested_at") or not record.get("catch_up_status"):
            continue
        if record.get("catch_up_until"):
            continue
        cutoff = live_audit_schedule.parse_timestamp(record.get("window"))
        if cutoff is None:
            continue
        record["catch_up_until"] = cutoff.astimezone(timezone.utc).date().isoformat()
        record.setdefault("catch_up_cutoff", cutoff.astimezone(timezone.utc).isoformat())
        changed = True
    return changed


def _already_covered(audit: dict[str, Any], target: datetime) -> bool:
    previous = live_audit_schedule.previous_period_end(audit)
    return previous is not None and (
        previous.astimezone(timezone.utc).date() >= target.date()
    )


def launch_pending(server: Any, node: dict[str, Any], node_id: str,
                   state: dict[str, Any], audits: dict[str, Any],
                   audit_ids: AuditIds, created_at: CreatedAt,
                   launch: Launch) -> bool:
    """Continua la cola persistida, como maximo con un uso por nodo."""
    runs, profiles = server.live_audit_runs, state.get("profiles") or {}
    forced = {**server.live_audit_scheduler_settings, "only_live_trading": True}
    for audit_id in audit_ids(state, forced):
        key, audit = runs.key(node_id, audit_id), dict(audits.get(audit_id) or {})
        record = runs.record(key)
        target_text = str(record.get("catch_up_until") or "")
        if not target_text or str(record.get("catch_up_status") or "") != "pending":
            continue
        target = live_audit_schedule.parse_timestamp(record.get("catch_up_cutoff"))
        if target is None:  # Compatibilidad con una cola creada antes de guardar el instante.
            target = datetime.fromisoformat(f"{target_text}T23:59:59+00:00")
        if _already_covered(audit, target):
            runs.update(key, catch_up_status="success")
            continue
        created, profile = created_at(audit_id), dict(profiles.get(audit_id) or {})
        if _born_after(server, node_id, profile, target) or (
            live_audit_schedule.previous_period_end(audit) is None
            and created is not None and created > target
        ):
            runs.update(key, catch_up_status="not_applicable")
            continue
        if any(str((row or {}).get("status") or "") in RUNNING_STATUSES
               for row in audits.values() if isinstance(row, dict)):
            return True
        window = f"catch-up:{target_text}"
        decision = live_audit_schedule.due_decision(
            server.live_audit_scheduler_settings, record, window,
            datetime.now(timezone.utc),
        )
        if decision == "exhausted":
            runs.update(key, catch_up_status="exhausted")
            continue
        if not decision:
            return True
        launch(server, node, node_id, audit_id, profile, {
            "window": window, "audit": audit, "now": target,
            "attempts": int(record.get("attempts") or 0) + 1 if decision == "retry" else 1,
            "period": _period(server, node_id, profile, audit, target),
            "catch_up": True,
        })
        return True
    return False
