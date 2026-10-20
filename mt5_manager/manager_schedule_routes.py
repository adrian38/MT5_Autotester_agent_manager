"""Las rutas del cron de auditoria, de la campana y de la serie historica.

Mismo patron que `manager_live_audit_routes`: funciones que reciben el handler.
Las tres cosas van juntas porque son el mismo material: el cron produce casi
todos los avisos de la campana, y cada auditoria que termina es una entrada mas
de la serie.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
from typing import Any

from . import live_audit_activity, live_audit_executions, live_audit_history
from . import live_audit_lineage, live_audit_watch
from . import manager_http
from . import manager_live_audit_routes
from .common import safe_int

NOTIFICATIONS_PATH = "/api/live-audit-notifications"

SCHEDULER_PATH = "/api/live-audit-scheduler-config"
CATCH_UP_PATH = "/api/live-audit-scheduler-catch-up"
ACTIVITY_PATH = "/api/live-audit-activity"


def _limit(parsed: Any) -> int:
    query = urllib.parse.parse_qs(getattr(parsed, "query", "") or "")
    return safe_int(query.get("limit", [50])[0], 50, minimum=1, maximum=300)


def _audit_history(handler, node_id: str, audit_key: str) -> dict[str, Any]:
    """La serie completa de un uso, con la ejecución vigente ya dentro.

    Se archiva aquí también, y no sólo en el vigilante, para que la pantalla
    nunca enseñe una serie a la que le falta justo la auditoría que se está
    mirando. Archivar es idempotente por ejecución.
    """
    node = handler._node(node_id)
    status, value = manager_http.node_request(
        node, "GET",
        f"/api/v1/live-audits/{urllib.parse.quote(audit_key, safe='')}", timeout=10,
    )
    if status != 200:
        raise ValueError(str((value or {}).get("error") if isinstance(value, dict) else value))
    analysed = manager_live_audit_routes.analysed_live_audit(
        handler, node_id, audit_key, value, node,
    )
    state = analysed.get("audit") if isinstance(analysed, dict) and isinstance(
        analysed.get("audit"), dict,
    ) else analysed
    profile = live_audit_lineage.audit_profile(handler.server, node_id, audit_key)
    live_audit_executions.archive_execution(
        handler.server, node, node_id, audit_key, dict(state or {}), profile,
    )
    result = (state or {}).get("last_result")
    lineage = live_audit_lineage.audit_lineage(handler.server, node_id, profile)
    return {
        "node": {"id": node_id, "name": node.get("name") or node_id},
        "audit_key": audit_key,
        **live_audit_history.series(
            handler.server.live_audit_archive, node_id, audit_key,
            (result or {}).get("comparison_detail") or {},
            lineage.ancestors,
        ),
    }


def _handle_history(handler, parts: list[str]) -> bool:
    if not (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "history"
    ):
        return False
    try:
        handler._send_json(200, _audit_history(
            handler, urllib.parse.unquote(parts[2]), urllib.parse.unquote(parts[4]),
        ))
    except (KeyError, ValueError, OSError) as exc:
        handler._send_json(400, {"error": str(exc)})
    except (urllib.error.URLError, TimeoutError) as exc:
        handler._send_json(502, {"error": str(exc)})
    return True


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    if _handle_history(handler, parts):
        return True
    if parsed.path == ACTIVITY_PATH:
        handler._send_json(200, live_audit_activity.snapshot(handler.server))
        return True
    if parsed.path == SCHEDULER_PATH:
        handler._send_json(200, handler.server.live_audit_scheduler_state())
        return True
    if parsed.path == NOTIFICATIONS_PATH:
        handler._send_json(
            200, handler.server.live_audit_notifications.state(_limit(parsed)),
        )
        return True
    return False


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path == CATCH_UP_PATH:
        try:
            result = live_audit_watch.request_catch_up(handler.server)
            handler.server.live_audit_wakeup.set()
            handler._send_json(202, result)
        except (ValueError, OSError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == SCHEDULER_PATH:
        try:
            handler._send_json(
                200, handler.server.update_live_audit_scheduler(handler._body()),
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path in {f"{NOTIFICATIONS_PATH}/read", f"{NOTIFICATIONS_PATH}/clear"}:
        try:
            body = handler._body()
            notifications = handler.server.live_audit_notifications
            handler._send_json(200, (
                notifications.clear(_limit(parsed))
                if parsed.path.endswith("/clear")
                else notifications.mark_read(body.get("ids"), _limit(parsed))
            ))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    return False
