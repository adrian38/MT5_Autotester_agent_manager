"""Que se esta ejecutando ahora mismo y cuanto le queda por terminar.

El cron lanza un uso por agente y solo sigue con el siguiente cuando ese
termina, asi que entre el clic y el aviso de «terminada» pueden pasar horas en
las que la pantalla no dice nada. Aqui se juntan las dos mitades de la
respuesta: lo que cada agente tiene en marcha -que es suyo y hay que
preguntarselo- y la tanda de recuperacion, que si es del manager.

El porcentaje no se inventa: es el de `PROGRESS`, el mismo que ya pinta la
pantalla de la auditoria, para que las dos no puedan contar cosas distintas.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import live_account_watch, live_audit_watch, manual_check_watch
from .common import utc_now
from .live_audit_core import PROGRESS, RUNNING_STATUSES, STATUS_LABELS
from .live_audit_executions import audit_label

CATCH_UP_DONE = frozenset({"success", "not_applicable", "exhausted"})
"""Lo que ya no cuenta como pendiente en una tanda de recuperacion."""


def _running(node: dict[str, Any], node_id: str, state: dict[str, Any],
             audits: dict[str, Any]) -> list[dict[str, Any]]:
    """Los usos que ese agente tiene en marcha, con su fase y su porcentaje."""
    profiles = state.get("profiles") or {}
    rows: list[dict[str, Any]] = []
    for audit_id, audit in sorted(audits.items()):
        if not isinstance(audit, dict):
            continue
        status = str(audit.get("status") or "idle")
        if status not in RUNNING_STATUSES:
            continue
        rows.append({
            "node_id": node_id, "node_name": str(node.get("name") or node_id),
            "audit_id": str(audit_id),
            "label": audit_label(dict(profiles.get(audit_id) or {}), str(audit_id)),
            "status": status,
            "status_label": STATUS_LABELS.get(status, status.upper()),
            "percent": PROGRESS.get(status, ("idle", 0))[1],
            "progress_text": str(audit.get("progress_text") or ""),
            "started_at": str(audit.get("started_at") or ""),
        })
    return rows


def _node_activity(
    server: Any, node: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Una lectura de un agente: lo que corre, o por que no se pudo saber."""
    node_id = str(node.get("id") or "")
    audits, code, message = live_audit_watch.node_live_audits(node, node_id)
    if audits is None:
        return [], {
            "node_id": node_id, "node_name": str(node.get("name") or node_id),
            "code": code, "message": message,
        }
    try:
        state = server.live_audit_settings.state(node_id)
    except (OSError, TypeError, ValueError):
        state = {}
    return _running(node, node_id, state, audits), {}


def recovery(server: Any) -> dict[str, Any]:
    """La tanda de recuperacion mas reciente: cuantos usos y cuantos van.

    Se identifica por el corte, que es el mismo para todos los usos que
    entraron con el mismo clic. Las marcas **no** se borran al terminar cada
    uno: son el denominador, y sin ellas «2 de 5» no se puede decir.
    """
    counts: dict[str, dict[str, int]] = {}
    for record in server.live_audit_runs.state().values():
        target = str(record.get("catch_up_until") or "")
        if not target:
            continue
        tally = counts.setdefault(target, {"total": 0, "done": 0})
        tally["total"] += 1
        if str(record.get("catch_up_status") or "") in CATCH_UP_DONE:
            tally["done"] += 1
    if not counts:
        return {}
    target = max(counts)
    tally = counts[target]
    return {
        "target_date": target, "total": tally["total"], "done": tally["done"],
        "pending": tally["total"] - tally["done"],
    }


def snapshot(server: Any) -> dict[str, Any]:
    """Lo que hay en marcha ahora, con una sola lectura por agente.

    La comprobación manual y el sondeo de cuentas entran aquí y no en una chapa
    propia: es la misma espera larga en el mismo agente, y tres indicadores en
    la cabecera se taparían unos a otros.
    """
    running: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    nodes = list(server.nodes)
    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as executor:
        for rows, issue in executor.map(
            lambda node: _node_activity(server, node), nodes,
        ):
            running.extend(rows)
            if issue:
                issues.append(issue)
        for row in executor.map(manual_check_watch.running_row, nodes):
            if row:
                checks.append(row)
        for row in executor.map(live_account_watch.running_row, nodes):
            if row:
                snapshots.append(row)
    pending = recovery(server)
    return {
        "running": running,
        "manual_checks": checks,
        "account_snapshots": snapshots,
        "recovery": pending,
        "issues": issues,
        "active": bool(running) or bool(checks) or bool(snapshots)
        or bool(pending.get("pending")),
        "observed_at": utc_now(),
    }
