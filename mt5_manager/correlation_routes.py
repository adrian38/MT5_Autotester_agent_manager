"""Lecturas aisladas para comparar portafolios de los tres brokers.

En ``dev`` AXI y RoboForex no están disponibles bajo las rutas operativas
``/data/*``. El override de Compose ya monta sus proyectos bajo
``/experiment-data/*`` en solo lectura para Experimenta. Estas rutas GET
reutilizan exactamente ese origen para Correlación sin cambiar la configuración
normal del nodo, sin ampliar las rutas permitidas de escritura y sin exponer un
solo verbo mutador.

Grid es distinto: sus paquetes pertenecen al manager y viven en
``runtime/grid_portfolios``. Se leen directamente si la base ya existe; una
consulta no crea una base vacía.
"""

from __future__ import annotations

import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from .common import safe_int, utc_now
from .portfolio_scope import normalize_portfolio_scope


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = frozenset({"correlation.html", "correlation.js", "correlation.css"})
API_PARTS = ("api", "correlation", "nodes")


def _empty_listing(source: Any, scope: str) -> dict[str, Any]:
    return {
        "node": {
            "id": source.node.get("id"),
            "name": source.node.get("name") or source.node.get("id"),
            "broker": source.broker,
            "account_type": source.account,
        },
        "scope": scope,
        "portfolios": [],
        "summary": {"total": 0, "strategies": 0, "latest_id": None},
        "observed_at": utc_now(),
    }


def _read_saved(server: Any, node_id: str, scope: str, portfolio_id: int | None) -> dict[str, Any]:
    """Lee una cartera sin pasar por las rutas operativas de ``/data/*``."""
    source = server.experiments.readonly_source(node_id)
    if scope == "grid":
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", node_id) + ".sqlite"
        memory = server.portfolios.settings_path.parent / "grid_portfolios" / filename
        if not memory.is_file():
            if portfolio_id is not None:
                raise ValueError(f"No existe el portafolio Grid #{portfolio_id}")
            return _empty_listing(source, scope)
        source.memory = memory
        source.memory_sources = [(f"{source.broker}/{source.account}/GRID", memory)]
    if portfolio_id is None:
        return source.saved_portfolios(scope)
    return source.saved_portfolio_detail(portfolio_id, scope)


def handle_get(handler: Any, parsed: urllib.parse.ParseResult) -> bool:
    relative = parsed.path.lstrip("/")
    if relative in STATIC_FILES:
        handler._send_file(STATIC_DIR / relative)
        return True
    parts = tuple(parsed.path.strip("/").split("/"))
    if len(parts) not in {5, 6} or parts[:3] != API_PARTS or parts[4] != "portfolios":
        return False
    try:
        node_id = urllib.parse.unquote(parts[3])
        query = urllib.parse.parse_qs(parsed.query)
        scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
        portfolio_id = safe_int(parts[5], 0, minimum=1) if len(parts) == 6 else None
        handler._send_json(200, _read_saved(handler.server, node_id, scope, portfolio_id))
    except (ValueError, OSError, sqlite3.Error) as exc:
        handler._send_json(502, {"error": str(exc)})
    return True
