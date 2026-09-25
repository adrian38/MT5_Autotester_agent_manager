"""El servidor HTTP del manager: que ruta atiende que, y como responde.

Las rutas de cada area viven en su modulo (`manager_portfolio_routes`,
`manager_live_audit_routes`, `correlation_routes`, `experiment_routes`); aqui
quedan el despacho, las respuestas y lo que se reenvia al nodo tal cual.
"""
from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from . import correlation_routes
from . import dev_branch
from . import experiment_routes
from . import guided_batches
from . import manager_http
from . import manager_live_audit_routes
from . import manager_portfolio_routes
from . import manager_pulse
from .common import json_bytes, safe_int, utc_now
from .manager_http import NODE_CONTROL_ACTIONS, NODE_CONTROL_TIMEOUT, submit_guided_to_node, submit_repair_request
from .manager_restart import RestartAlreadyRunning
from .portfolio_scope import normalize_portfolio_scope


STATIC_DIR = Path(__file__).resolve().parent / "static"

# Lo que un vigilante externo necesita para detectar que algo terminó o falló.
# `/api/nodes` ronda los 400 KB porque lleva el comando completo, el pipeline y
# el snapshot de la base de cada nodo; sondear eso cada medio minuto desde un
# móvil no es razonable, así que `/api/pulse` proyecta solo estos campos.
STATIC_FILES = {
    "app.js", "styles.css", "universe.html", "universe.js",
    "live_audit.html", "live_audit.js", "live_audit.css",
    "live_audit_result.html", "live_audit_result.js", "live_audit_result.css",
    "portfolios.html", "portfolios.js",
    "portfolios_monthly.html", "portfolios_monthly.js",
    "portfolio_improvement.js", "portfolio_monthly_improvement.js",
    "portfolio_comparison.js",
    "portfolios_grid.html", "portfolios_grid.js",
    # Primitiva compartida por los tres ambitos: el dialogo del motivo de
    # exclusion y las etiquetas de sus tres codigos. La interfaz de cada
    # ambito sigue siendo suya; lo que no puede divergir es el codigo que
    # viaja al nodo y decide que se escribe en la memoria del agente.
    "exclusion_reason.js",
    # Importar es el reflejo de exportar y hereda su transporte: la lectura del
    # ZIP y el resumen del resultado no pueden divergir entre pantallas.
    "portfolio_transfer.js",
}

NODE_ACTION_TARGETS = {
    "start": "/api/v1/jobs/generation",
    "stop": "/api/v1/jobs/stop",
    "pause": "/api/v1/jobs/pause",
    "resume": "/api/v1/jobs/resume",
    "restart": "/api/v1/application/restart",
    "repair": "/api/v1/jobs/repair",
    "regression": "/api/v1/jobs/regression",
    "cleanup": "/api/v1/jobs/cleanup",
    "universe": "/api/v1/universe/symbols",
    "universe-sync": "/api/v1/universe/sync",
    "universe-history-preview": "/api/v1/universe/history-preview",
    "universe-history": "/api/v1/jobs/universe-history",
    "universe-disable-preview": "/api/v1/universe/disable-preview",
    "universe-disable-no-history": "/api/v1/universe/disable-no-history",
    "universe-trade-disabled-preview": "/api/v1/universe/trade-disabled-preview",
    "universe-disable-trade-disabled": "/api/v1/universe/disable-trade-disabled",
}

class ManagerHandler(BaseHTTPRequestHandler):
    server: "ManagerServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[manager-http] " + (fmt % args) + "\n")

    def _send_json(self, status: int, value: Any) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_download(self, value: dict[str, Any]) -> None:
        body = bytes(value.get("content") or b"")
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value.get("filename") or "portafolio.zip"))
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Exported-Sets", str(safe_int(value.get("exported"), 0, minimum=0)))
        self.send_header("X-Exported-Files", str(safe_int(value.get("exported"), 0, minimum=0)))
        self.send_header("X-Missing-Sets", str(len(value.get("missing") or [])))
        self.end_headers()
        self.wfile.write(body)

    def _send_inline_content(self, value: dict[str, Any]) -> None:
        body = bytes(value.get("content") or b"")
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value.get("filename") or "reporte.html"))
        self.send_response(200)
        self.send_header("Content-Type", str(value.get("content_type") or "application/octet-stream"))
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file() or STATIC_DIR not in path.resolve().parents:
            self.send_error(404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_artifact(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if (content_type or "").casefold().startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
            )
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = safe_int(self.headers.get("Content-Length"), 0, minimum=0, maximum=1_000_000)
        value = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        if not isinstance(value, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON")
        return value

    def _node(self, node_id: str) -> dict[str, Any]:
        for node in self.server.nodes:
            if str(node.get("id")) == node_id:
                return node
        raise KeyError(f"Nodo desconocido: {node_id}")

    def _all_status(self) -> Any:
        return manager_pulse.all_status(self)

    def _pulse(self) -> Any:
        return manager_pulse.pulse(self)

    def _get_manager_routes(self, parsed: Any, parts: list[str]) -> bool:
        """Estado del manager entero: reinicio, planificador, nodos y pulso."""
        if parsed.path == "/api/manager/restart":
            query = urllib.parse.parse_qs(parsed.query)
            lines = safe_int(query.get("lines", [120])[0], 120, minimum=1, maximum=1000)
            self._send_json(200, self.server.manager_restart.status(log_lines=lines))
            return True
        if parsed.path == "/api/live-audit-scheduler-config":
            self._send_json(200, self.server.live_audit_scheduler_state())
            return True
        if parsed.path == "/api/nodes":
            self._send_json(200, {"nodes": self._all_status(), "observed_at": utc_now()})
            return True
        if parsed.path == "/api/pulse":
            self._send_json(200, self._pulse())
            return True
        return False

    def _get_node_proxy_routes(self, parsed: Any, parts: list[str]) -> bool:
        """Lo que se le pregunta al nodo tal cual: lotes, logs, runs y universo."""
        if len(parts)==5 and parts[:2]==["api","nodes"] and parts[3]=="guided-batches":
            try:
                batch_id = parts[4]
                if not re.fullmatch("[a-f0-9]{64}", batch_id):
                    raise ValueError("Identificador de lote inválido")
                status, value = manager_http.node_request(self._node(urllib.parse.unquote(parts[2])), "GET", "/api/v1/guided-batches/"+batch_id, timeout=30)
                self._send_json(status, value)
            except (KeyError, ValueError, OSError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(400, {"error": str(exc)})
            return True
        if parsed.path.startswith("/api/nodes/") and parsed.path.endswith("/logs"):
            parts = parsed.path.strip("/").split("/")
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                lines = safe_int(query.get("lines", [200])[0], 200, minimum=1, maximum=2000)
                status, value = manager_http.node_request(node, "GET", f"/api/v1/logs?lines={lines}")
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return True
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "runs":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=100)
                offset = safe_int(query.get("offset", [0])[0], 0, minimum=0)
                status, value = manager_http.node_request(
                    node, "GET", f"/api/v1/runs?limit={limit}&offset={offset}", timeout=120
                )
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return True
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "universe":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                status, value = manager_http.node_request(node, "GET", "/api/v1/universe")
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return True
        return False

    def _get_portfolio_routes(self, parsed: Any, parts: list[str]) -> bool:
        return manager_portfolio_routes.handle_get(self, parsed, parts)

    def _get_live_audit_routes(self, parsed: Any, parts: list[str]) -> bool:
        return manager_live_audit_routes.handle_get(self, parsed, parts)

    def _post_live_audit_routes(self, parsed: Any, parts: list[str]) -> bool:
        return manager_live_audit_routes.handle_post(self, parsed, parts)

    def _get_static_routes(self, parsed: Any, parts: list[str]) -> bool:
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return True
        relative = parsed.path.lstrip("/")
        if relative in STATIC_FILES:
            self._send_file(STATIC_DIR / relative)
            return True
        return False

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        # Comparador de correlación: GET de solo lectura, aislado de las rutas
        # operativas y capaz de usar los mounts especiales de dev.
        if correlation_routes.handle_get(self, parsed):
            return
        # Laboratorio «Experimenta»: pantalla y endpoints propios, en su módulo.
        if experiment_routes.handle_get(self, parsed):
            return
        for route in (
            self._get_node_proxy_routes,
            self._get_manager_routes,
            self._get_live_audit_routes,
            self._get_portfolio_routes,
            self._get_static_routes,
        ):
            if route(parsed, parts):
                return
        self.send_error(404)

    def _post_manager_routes(self, parsed: Any, parts: list[str]) -> bool:
        """Lotes guiados, reinicio, planificador, cola y preferencias."""
        if len(parts)==4 and parts[:2]==["api","nodes"] and parts[3]=="guided-batches":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= guided_batches.MAX_BODY:
                    raise ValueError("Lote demasiado grande o vacío")
                package = json.loads(self.rfile.read(length).decode("utf-8"))
                status, value = submit_guided_to_node(self._node(urllib.parse.unquote(parts[2])), package)
                self._send_json(status, value)
            except (KeyError, ValueError, OSError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(400, {"error": str(exc)})
            return True
        if parsed.path == "/api/manager/restart":
            try:
                self._send_json(202, self.server.manager_restart.start())
            except RestartAlreadyRunning as exc:
                self._send_json(409, {"error": str(exc)})
            except (ValueError, OSError, RuntimeError) as exc:
                self._send_json(503, {"error": str(exc)})
            return True
        if parsed.path == "/api/live-audit-scheduler-config":
            try:
                self._send_json(200, self.server.update_live_audit_scheduler(self._body()))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            return True
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3:] == ["queue", "cancel"]:
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                status, value = manager_http.node_request(node, "POST", "/api/v1/jobs/queue/cancel", self._body())
                self._send_json(status, value)
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except (urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return True
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "preferences":
            try:
                node_id = urllib.parse.unquote(parts[2])
                self._node(node_id)
                saved = self.server.update_preferences(node_id, self._body())
                self._send_json(200, {"preferences": saved})
            except (KeyError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            return True
        return False

    def _submit_repair_async(self, node: dict[str, Any], body: Any) -> None:
        """La reparacion se acepta y se envia en segundo plano: el nodo tarda."""
        worker = threading.Thread(
            target=submit_repair_request,
            args=(node, body),
            daemon=True,
            name=f"repair-submit-{node.get('id')}",
        )
        worker.start()
        self._send_json(202, {
            "job_type": "repair",
            "status": "submitting",
            "queued": False,
            "request": body,
        })

    def _post_node_action(self, parts: list[str]) -> None:
        """Las acciones que el manager reenvia al nodo con su propio tiempo."""
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = self._node(node_id)
            target = NODE_ACTION_TARGETS[parts[3]]
            body = self._body()
            if parts[3] == "repair":
                self._submit_repair_async(node, body)
                return
            if parts[3].startswith("universe-"):
                project = node.get("portfolio_project_dir")
                if dev_branch.is_active() and not project:
                    raise ValueError("Falta portfolio_project_dir para verificar el destino en dev")
                if project:
                    dev_branch.assert_writable(project, "sincronización de símbolos")
                # MT5 initialization can exceed the normal status timeout. Never
                # retry a mutation: the node may already have applied it.
                status, value = manager_http.node_request(node, "POST", target, body, timeout=120)
            elif parts[3] in NODE_CONTROL_ACTIONS:
                status, value = manager_http.node_request(
                    node, "POST", target, body, timeout=NODE_CONTROL_TIMEOUT,
                )
            else:
                status, value = manager_http.node_request(node, "POST", target, body)
            if parts[3] == "start" and status < 400:
                self.server.remember_launch_request(node_id, body)
            self._send_json(status, value)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except (urllib.error.URLError, TimeoutError) as exc:
            self._send_json(502, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if experiment_routes.handle_post(self, parsed):
            return
        for route in (self._post_manager_routes, self._post_live_audit_routes):
            if route(parsed, parts):
                return
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-manager":
            manager_portfolio_routes.handle_post(self, parts)
            return
        if len(parts) != 4 or parts[:2] != ["api", "nodes"] or parts[3] not in NODE_ACTION_TARGETS:
            self._send_json(404, {"error": "Ruta no encontrada"})
            return
        self._post_node_action(parts)
