"""Rutas de «Experimenta Seeds», aisladas del despachador del manager.

Mismo contrato que `experiment_routes`: dos líneas en `do_GET`/`do_POST` y un
booleano que significa «esta petición ya está contestada». Lo que cambia es el
prefijo (`/api/seed-experiment/`) y el coordinador al que se delega.

Su pantalla sirve **un solo** estático propio: `seed_experiment.html` carga
`/experiment.js` y `/experiment.css`, que ya sirve el laboratorio hermano. Las
dos páginas son el mismo laboratorio con otro pool, y el JS sabe a qué API
hablar porque la página lo dice en `data-api`.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from typing import Any

from ..common import safe_int
from ..static_files import static_path


STATIC_FILES = frozenset({"seed_experiment.html"})
API_PREFIX = "/api/seed-experiment/"


def handle_get(handler: Any, parsed: urllib.parse.ParseResult) -> bool:
    relative = parsed.path.lstrip("/")
    if relative in STATIC_FILES:
        handler._send_file(static_path(relative))
        return True
    if not parsed.path.startswith(API_PREFIX):
        return False
    action = parsed.path[len(API_PREFIX):]
    experiments = handler.server.seed_experiments
    try:
        if action == "config":
            handler._send_json(200, experiments.config())
            return True
        if action == "state":
            handler._send_json(200, experiments.state())
            return True
        if action == "log":
            query = urllib.parse.parse_qs(parsed.query)
            lines = safe_int(query.get("lines", [400])[0], 400, minimum=1, maximum=4000)
            handler._send_json(200, experiments.log(lines))
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
    experiments = handler.server.seed_experiments
    try:
        body = handler._body() if action != "stop" else {}
        if action == "settings":
            handler._send_json(200, {"settings": experiments.update_settings(body)})
            return True
        if action == "run":
            handler._send_json(200, experiments.start(body))
            return True
        handler._send_json(200, experiments.stop())
        return True
    except (ValueError, OSError, sqlite3.Error) as exc:
        handler._send_json(400, {"error": str(exc)})
        return True
