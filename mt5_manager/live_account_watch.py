"""La vuelta del sondeo de cuentas: recoger siempre, lanzar sólo con el interruptor.

Mismo reparto y mismas dos cautelas que `live_audit_watch`, por la misma razón:

1. **Recoger** es leer el estado del agente y meter en el archivo el sondeo que
   ya terminó. Son sólo GET y se hace siempre, porque el nodo guarda **un**
   sondeo y el siguiente lo sustituye: lo que no se recoja se pierde.
2. **Lanzar** sigue detrás del interruptor. Un sondeo entra en las cuentas
   reales, cambia la cuenta guardada de un terminal y pausa el pipeline: el
   2026-08-21 una ejecución desatendida dejó un terminal sin cuenta y dos días
   de discovery a cero.

Un agente por vuelta como mucho, y nunca dos sondeos a la vez en el mismo nodo:
el candado de verdad está en el agente (`is_running` mira auditorías y sondeos),
pero pedirlo igualmente evita contar como fallo un rechazo que ya sabíamos.
"""
from __future__ import annotations

import sys
import urllib.error
from datetime import datetime, timezone
from typing import Any

from . import live_account_ingest, live_account_inventory, live_account_schedule
from . import manager_http
from .common import utc_now
from .live_account_ingest import SNAPSHOT_KEY
from .live_account_snapshot import RUNNING_SNAPSHOT_STATUSES

_NODE_UNAVAILABLE_CONFIRMATION_POLLS = 3
"""Lo mismo que en el cron de auditoría: un timeout no es una desconexión."""


def node_snapshot(
    node: dict[str, Any], node_id: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """El sondeo que el agente enseña y, si no se pudo leer, por qué.

    Un agente sin portar **no es un error de red**: contestó, y lo que le falta
    es el port y un reinicio. Se distingue porque el aviso es distinto y porque
    no espera tres vueltas para contarlo.
    """
    try:
        status, state = manager_http.node_request(node, "GET", "/api/v1/status", timeout=10)
        capabilities = (
            state.get("capabilities") if status == 200 and isinstance(state, dict) else {}
        )
        if not isinstance(capabilities, dict) or not capabilities.get("live_account_snapshot"):
            message = (
                "El agente no sabe sondear cuentas todavía: falta portar "
                "`/api/v1/live-accounts` a su `manager_node_runtime/` y reiniciarlo."
            )
            return None, "old_runtime", message
        status, value = manager_http.node_request(
            node, "GET", "/api/v1/live-accounts", timeout=15,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None, "unreachable", f"No se pudo consultar el agente: {exc}"
    if status != 200 or not isinstance(value, dict):
        return None, "http_error", f"La consulta del sondeo devolvió HTTP {status}: {value}"
    snapshot = value.get("snapshot")
    return (snapshot if isinstance(snapshot, dict) else {}), "", ""


def record_node_issue(
    server: Any, node: dict[str, Any], node_id: str, code: str, message: str,
) -> None:
    """Confirma la caída y avisa una vez, hasta que el agente vuelva."""
    runs = server.live_account_runs
    key = runs.key(node_id, SNAPSHOT_KEY)
    record = runs.record(key)
    previous = str(record.get("watch_issue") or "")
    failures = int(record.get("watch_failures") or 0)
    name = str(node.get("name") or node_id)
    if code:
        if previous:
            return
        failures += 1
        runs.update(key, watch_failures=failures)
        if code != "old_runtime" and failures < _NODE_UNAVAILABLE_CONFIRMATION_POLLS:
            return
        runs.update(key, watch_issue=code, watch_message=message)
        server.live_audit_notifications.add(
            "warning" if code == "old_runtime" else "error",
            f"El sondeo de cuentas no puede consultar {name}", message,
            node_id=node_id, node_name=name, kind="account_snapshot_node_unavailable",
        )
        return
    if not previous and not failures:
        return
    runs.update(key, watch_issue="", watch_message="", watch_failures=0)
    if previous:
        server.live_audit_notifications.add(
            "success", f"El sondeo de cuentas vuelve a ver {name}",
            "El agente ya responde y las citas pendientes vuelven a poder lanzarse.",
            node_id=node_id, node_name=name, kind="account_snapshot_node_recovered",
        )


def announce_collected(
    server: Any, node: dict[str, Any], node_id: str, collected: dict[str, Any],
) -> None:
    """Un aviso por sondeo recogido, con lo que entró y lo que no."""
    name = str(node.get("name") or node_id)
    failed = collected["failed"]
    level = "success" if collected["accounts"] and not failed else (
        "error" if not collected["accounts"] else "warning"
    )
    period = " a ".join(part for part in collected["period"] if part) or "toda la vida"
    detail = (
        f"{collected['accounts']} cuenta(s) y {collected['movements']} cierre(s), "
        f"periodo {period}."
    )
    if failed:
        detail += " Sin leer: " + "; ".join(failed[:4])
    server.live_audit_notifications.add(
        level, f"Sondeo de cuentas de {name}", detail,
        node_id=node_id, node_name=name, kind="account_snapshot_collected",
    )


def _period(server: Any, node_id: str, accounts: list[dict[str, Any]], now: datetime) -> tuple[str, str]:
    """El rango que se pide: el más atrasado de todas las cuentas del nodo.

    Un sondeo trae **un** periodo para todas, así que manda la que va más
    atrás. Y una cuenta sin serie lo lleva a «toda la vida», que es lo que hace
    que su media y su mes anterior existan desde la primera noche.
    """
    days = [
        server.live_accounts.last_day(node_id, account["account_key"])
        for account in accounts
    ]
    return live_account_schedule.snapshot_period(
        server.live_account_scheduler_settings, "" if not days or "" in days else min(days), now,
    )


def launch(
    server: Any, node: dict[str, Any], node_id: str, accounts: list[dict[str, Any]],
    window: str, attempts: int, now: datetime,
) -> None:
    """Pide el sondeo al agente y deja escrito cómo fue."""
    runs, settings = server.live_account_runs, server.live_account_scheduler_settings
    key = runs.key(node_id, SNAPSHOT_KEY)
    name = str(node.get("name") or node_id)
    base = {"window": window, "attempts": attempts, "last_attempt_at": utc_now()}
    common = {"node_id": node_id, "node_name": name, "kind": "account_snapshot_launch"}
    restore = server.live_audit_settings.restore_credentials(node_id)
    payload_accounts = live_account_inventory.snapshot_accounts(server, node_id)
    if not restore or not payload_accounts:
        runs.update(key, **base, outcome="failed")
        server.live_audit_notifications.add(
            "error", f"No se pudo sondear {name}",
            "Falta la cuenta final de terminales o la contraseña de las cuentas reales."
            if not restore else "Ninguna cuenta real tiene su contraseña guardada.",
            **common,
        )
        return
    start, end = _period(server, node_id, accounts, now)
    try:
        status, response = manager_http.node_request(
            node, "POST", "/api/v1/live-accounts/snapshot", {
                "snapshot_key": window, "period_start_date": start,
                "period_end_date": end, **restore, "accounts": payload_accounts,
            }, timeout=30,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        runs.update(key, **base, outcome="failed")
        server.live_audit_notifications.add(
            "error", f"No se pudo sondear {name}", str(exc), **common,
        )
        return
    if status >= 400:
        runs.update(key, **base, outcome="failed")
        server.live_audit_notifications.add(
            "error", f"El agente rechazó el sondeo de {name}",
            f"HTTP {status}: {response}", **common,
        )
        return
    runs.update(key, **base, outcome="running", last_status="queued")
    server.live_audit_notifications.add(
        "info", f"Sondeo de cuentas lanzado en {name}",
        f"{len(payload_accounts)} cuenta(s), periodo "
        f"{start or 'desde el principio'} a {end} · "
        f"{live_account_schedule.schedule_label(settings)}",
        **common,
    )


def launch_due(
    server: Any, node: dict[str, Any], node_id: str, accounts: list[dict[str, Any]],
    state: dict[str, Any], now: datetime,
) -> None:
    """Lanza el sondeo de este nodo si le toca y no hay otro corriendo."""
    runs = server.live_account_runs
    settings = server.live_account_scheduler_settings
    key = runs.key(node_id, SNAPSHOT_KEY)
    record = runs.record(key)
    # El `finished_at` del nodo sólo cuenta si **hay** un sondeo detrás: en
    # `idle` es el de una tanda que el agente ya olvidó al reiniciarse, y
    # tomarlo por cumplido dejaría la cita de hoy sin lanzar.
    published = (
        state.get("finished_at")
        if str(state.get("status") or "idle") not in {"", "idle"} else ""
    )
    completion = live_account_schedule.parse_timestamp(
        record.get("completed_at") or published,
    )
    window, pending = live_account_schedule.pending_window(settings, completion, now)
    if not pending:
        if str(record.get("window") or "") != window:
            runs.update(key, window=window, attempts=0, outcome="success")
        return
    decision = live_account_schedule.due_decision(settings, record, window, now)
    if decision == "exhausted":
        runs.update(key, window=window, outcome="exhausted")
        server.live_audit_notifications.add(
            "error", f"Reintentos agotados: sondeo de {node.get('name') or node_id}",
            "El sondeo falló en todos los intentos de este turno. No se vuelve a "
            "intentar hasta la próxima cita.",
            node_id=node_id, node_name=str(node.get("name") or node_id),
            kind="account_snapshot_exhausted",
        )
        return
    if not decision:
        return
    launch(
        server, node, node_id, accounts, window,
        int(record.get("attempts") or 0) + 1 if decision == "retry" else 1, now,
    )


def tick(server: Any, now: datetime | None = None) -> None:
    """Una vuelta: una lectura por nodo, recogida siempre, lanzamiento si toca."""
    moment = now or datetime.now(timezone.utc)
    for node in server.nodes:
        if server.live_account_stop.is_set():
            return
        node_id = str(node.get("id") or "")
        accounts = live_account_inventory.node_accounts(server, node_id)
        if not node_id or not accounts:
            continue
        state, issue, message = node_snapshot(node, node_id)
        record_node_issue(server, node, node_id, issue, message)
        if state is None:
            continue
        try:
            collected = live_account_ingest.collect(server, node, node_id, state)
        except Exception as exc:  # una recogida rota no puede parar la vuelta
            sys.stderr.write(f"[live-account-scheduler] {node_id}: {exc}\n")
            collected = None
        if collected is not None:
            announce_collected(server, node, node_id, collected)
        live_account_ingest.refresh_expectations(server, node, node_id, accounts, moment)
        if str(state.get("status") or "") in RUNNING_SNAPSHOT_STATUSES:
            continue
        if not server.live_account_scheduler_enabled or server.live_account_stop.is_set():
            continue
        launch_due(server, node, node_id, accounts, state, moment)


def targets(server: Any) -> dict[str, Any]:
    """A quién alcanza el sondeo hoy, sin preguntarle nada a ningún nodo.

    Lo enseña el diálogo antes de activarlo, igual que el del cron de
    auditoría: lo bloqueado se ve, en vez de no pasar nada el lunes a las 23:00
    sin que nadie sepa por qué.
    """
    scheduled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        record = server.live_account_runs.record(
            server.live_account_runs.key(node_id, SNAPSHOT_KEY),
        )
        if record.get("watch_issue"):
            issues.append({
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "message": str(record.get("watch_message") or "El agente no responde."),
            })
        for account in live_account_inventory.node_accounts(server, node_id):
            row = {
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "account_key": account["account_key"], "login": account["login"],
                "server": account["server"],
                "portfolios": [
                    portfolio.get("name") or f"#{portfolio.get('portfolio_id')}"
                    for portfolio in account["portfolios"]
                ],
                "block_reason": account["block_reason"],
                "last_day": server.live_accounts.last_day(node_id, account["account_key"]),
            }
            (blocked if account["block_reason"] else scheduled).append(row)
    return {"accounts": scheduled, "blocked": blocked, "scheduler_issues": issues}
