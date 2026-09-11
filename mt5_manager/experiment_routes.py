"""Rutas del laboratorio «Experimenta», aisladas del despachador del manager.

`manager.py` solo delega: dos líneas en `do_GET` y `do_POST`. Todo lo que la
pantalla nueva necesita —sus ficheros estáticos y sus siete endpoints— vive
aquí, así que un cambio del experimento no puede romper ninguna ruta de
generación, portafolio o auditoría.

El contrato con el despachador es un booleano: `True` significa «esta petición
ya está contestada». Cualquier ruta desconocida devuelve `False` y sigue su
camino normal, incluido el 404 final.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from .common import safe_int


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = frozenset({"experiment.html", "experiment.js", "experiment.css"})
API_PREFIX = "/api/experiment/"


def handle_get(handler: Any, parsed: urllib.parse.ParseResult) -> bool:
    relative = parsed.path.lstrip("/")
    if relative in STATIC_FILES:
        handler._send_file(STATIC_DIR / relative)
        return True
    if not parsed.path.startswith(API_PREFIX):
        return False
    action = parsed.path[len(API_PREFIX):]
    experiments = handler.server.experiments
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
    experiments = handler.server.experiments
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
