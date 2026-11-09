"""HTTP aislado de Experimenta Prop."""

from __future__ import annotations

import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from .common import safe_int


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"prop_experiment.html", "prop_experiment.css", "prop_experiment.js"}
API_PREFIX = "/api/prop-experiment/"


def handle_get(handler: Any, parsed: urllib.parse.ParseResult) -> bool:
    relative = parsed.path.lstrip("/")
    if relative in STATIC_FILES:
        handler._send_file(STATIC_DIR / relative)
        return True
    if not parsed.path.startswith(API_PREFIX):
        return False
    action = parsed.path[len(API_PREFIX):]
    coordinator = handler.server.prop_experiments
    try:
        if action == "config":
            handler._send_json(200, coordinator.config())
            return True
        if action == "state":
            handler._send_json(200, coordinator.state())
            return True
        if action == "log":
            query = urllib.parse.parse_qs(parsed.query)
            lines = safe_int(query.get("lines", [400])[0], 400, minimum=1, maximum=4000)
            handler._send_json(200, coordinator.log(lines))
            return True
    except (ValueError, OSError, sqlite3.Error) as exc:
        handler._send_json(400, {"error": str(exc)})
        return True
    return False


def handle_post(handler: Any, parsed: urllib.parse.ParseResult) -> bool:
    if not parsed.path.startswith(API_PREFIX):
        return False
    action = parsed.path[len(API_PREFIX):]
    if action not in {"settings", "run", "stop"}:
        return False
    coordinator = handler.server.prop_experiments
    try:
        body = handler._body() if action != "stop" else {}
        if action == "settings":
            handler._send_json(200, {"settings": coordinator.update_settings(body)})
        elif action == "run":
            handler._send_json(200, coordinator.start(body))
        else:
            handler._send_json(200, coordinator.stop())
    except (ValueError, OSError, sqlite3.Error) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True
