"""Las rutas de la portada de cuentas y de su cron.

Mismo patrón que `manager_schedule_routes`: funciones que reciben el handler.
Va en su propio módulo y no ahí porque aquél ya ronda el techo de tamaño, y
porque son dos crones distintos: mezclarlos invitaría a compartir el registro,
que es justo lo que no comparten.

`/api/live-accounts` no consulta a ningún agente: lee la serie guardada y la
media que dejó el sondeo nocturno. La portada se repinta cada minuto y no puede
abrir la memoria del agente en cada vuelta.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import live_account_dashboard, live_account_inventory, live_account_watch

ACCOUNTS_PATH = "/api/live-accounts"
SCHEDULER_PATH = "/api/live-account-scheduler-config"
SNAPSHOT_NOW_PATH = "/api/live-account-snapshot-now"
TOTAL_SELECTION_PATH = "/api/live-account-total-selection"


def _snapshot_now(handler) -> dict[str, Any]:
    """Lanza el sondeo a mano, en todos los nodos que tengan cuentas.

    Funciona con el interruptor apagado a propósito: es una orden del usuario,
    con alguien delante, que es la diferencia con una cita de madrugada. El
    agente sigue siendo quien decide si puede —`is_running` mira auditorías y
    sondeos—, así que esto nunca ocupa un terminal ya ocupado.
    """
    now = datetime.now(timezone.utc)
    server = handler.server
    launched: list[str] = []
    skipped: list[dict[str, str]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        accounts = live_account_inventory.node_accounts(server, node_id) if node_id else []
        if not accounts:
            continue
        state, issue, message = live_account_watch.node_snapshot(node, node_id)
        if state is None:
            skipped.append({"node_id": node_id, "reason": message or issue})
            continue
        if str(state.get("status") or "") in {"queued", "pausing", "extracting", "finalizing", "resuming"}:
            skipped.append({"node_id": node_id, "reason": "Ya hay un sondeo en marcha."})
            continue
        runs = server.live_account_runs
        record = runs.record(runs.key(node_id, live_account_watch.SNAPSHOT_KEY))
        live_account_watch.launch(
            server, node, node_id, accounts,
            str(record.get("window") or now.isoformat()),
            int(record.get("attempts") or 0) + 1, now,
        )
        launched.append(node_id)
    server.live_account_wakeup.set()
    return {"launched": launched, "skipped": skipped}


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path == ACCOUNTS_PATH:
        handler._send_json(200, live_account_dashboard.snapshot(handler.server))
        return True
    if parsed.path == SCHEDULER_PATH:
        handler._send_json(200, handler.server.live_account_scheduler_state())
        return True
    return False


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path == TOTAL_SELECTION_PATH:
        try:
            state = handler.server.live_accounts.update_total_selection(handler._body())
            handler._send_json(200, state)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == SCHEDULER_PATH:
        try:
            handler._send_json(
                200, handler.server.update_live_account_scheduler(handler._body()),
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == SNAPSHOT_NOW_PATH:
        try:
            handler._send_json(202, _snapshot_now(handler))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    return False
