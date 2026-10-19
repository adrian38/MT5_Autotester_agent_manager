"""Las rutas del cron de auditoria y de la campana, en su propio modulo.

Mismo patron que `manager_live_audit_routes`: funciones que reciben el handler.
Las dos cosas van juntas porque son la misma pantalla -el boton superior
«Auditoria programada» y su campana- y porque el cron es quien produce casi
todos los avisos.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .common import safe_int

NOTIFICATIONS_PATH = "/api/live-audit-notifications"

SCHEDULER_PATH = "/api/live-audit-scheduler-config"


def _limit(parsed: Any) -> int:
    query = urllib.parse.parse_qs(getattr(parsed, "query", "") or "")
    return safe_int(query.get("limit", [50])[0], 50, minimum=1, maximum=300)


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
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
