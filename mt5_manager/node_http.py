"""El servidor HTTP del nodo y su arranque por linea de ordenes.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`, embebida en `app_ui.py`. Ver
`AGENTS.md`, seccion «El nodo NO ejecuta este repositorio».
"""
from __future__ import annotations

import argparse
import hmac
import sqlite3
import json
import mimetypes
import os
import platform
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import dev_branch
from . import guided_batches
from . import node_portfolio_api
from .common import json_bytes, load_json, safe_int, utc_now
from .node_jobs import JobController
from .node_settings import _load_universe_rows, _universe_paths, read_settings, setting
from .node_snapshots import completed_runs_snapshot, database_snapshot


class NodeHandler(BaseHTTPRequestHandler):
    server: "NodeServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[node-http] " + (fmt % args) + "\n")

    def _authorized(self) -> bool:
        expected = str(self.server.controller.config.get("token") or "")
        supplied = self.headers.get("Authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())

    def _send(self, status: int, value: Any) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_artifact(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, maximum: int = 1_000_000) -> dict[str, Any]:
        length = safe_int(self.headers.get("Content-Length"), 0, minimum=0, maximum=maximum)
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON")
        return value

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v1/health":
            self._send(200, {"ok": True, "node_id": self.server.controller.config.get("node_id"), "time": utc_now()})
        elif parsed.path.startswith("/api/v1/guided-batches/"):
            try:
                self._send(200, self.server.controller.guided_status(parsed.path.rsplit("/", 1)[-1]))
            except (ValueError, OSError, sqlite3.Error) as exc:
                self._send(400, {"error": str(exc)})
        elif parsed.path == "/api/v1/status":
            self._send(200, self.server.controller.status())
        elif parsed.path == "/api/v1/logs":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.log_tail(safe_int(query.get("lines", [200])[0], 200)))
        elif parsed.path == "/api/v1/runs":
            query = urllib.parse.parse_qs(parsed.query)
            limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=100)
            offset = safe_int(query.get("offset", [0])[0], 0, minimum=0)
            self._send(200, self.server.controller.runs(limit, offset))
        elif parsed.path == "/api/v1/universe":
            self._send(200, self.server.controller.universe())
        elif parsed.path == "/api/v1/live-audits":
            self._send(200, {"audits": self.server.controller.live_audits.all_states(), "observed_at": utc_now()})
        elif (
            len(parsed.path.strip("/").split("/")) == 7
            and parsed.path.strip("/").split("/")[:3] == ["api", "v1", "live-audits"]
            and parsed.path.strip("/").split("/")[4] == "artifacts"
        ):
            parts = parsed.path.strip("/").split("/")
            try:
                path = self.server.controller.live_audits.artifact_path(
                    urllib.parse.unquote(parts[3]),
                    urllib.parse.unquote(parts[5]),
                    urllib.parse.unquote(parts[6]),
                )
                self._send_artifact(path)
            except (ValueError, FileNotFoundError):
                self._send(404, {"error": "Reporte de auditoría no encontrado"})
        elif parsed.path.startswith("/api/v1/live-audits/"):
            audit_key = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            self._send(200, {"audit": self.server.controller.live_audits.state(audit_key), "observed_at": utc_now()})
        elif parsed.path == "/api/v1/portfolios":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, node_portfolio_api.portfolios(self.server.controller, query.get("scope", ["full_history"])[0]))
        elif parsed.path.startswith("/api/v1/portfolios/"):
            query = urllib.parse.parse_qs(parsed.query)
            portfolio_id = safe_int(parsed.path.rsplit("/", 1)[-1], 0, minimum=1)
            self._send(200, node_portfolio_api.portfolio_detail(self.server.controller, portfolio_id, query.get("scope", ["full_history"])[0]))
        else:
            self._send(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        try:
            if self.path == "/api/v1/application/restart":
                self._send(202, self.server.request_application_restart())
            elif self.path == "/api/v1/guided-batches":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= guided_batches.MAX_BODY:
                    raise ValueError("Lote demasiado grande o vacío")
                self._send(202, self.server.controller.submit_guided(self._body(guided_batches.MAX_BODY)))
            elif self.path == "/api/v1/jobs/generation":
                self._send(202, self.server.controller.start(self._body()))
            elif self.path == "/api/v1/jobs/repair":
                self._send(202, self.server.controller.start_repair(self._body()))
            elif self.path == "/api/v1/jobs/regression":
                self._send(202, self.server.controller.start_regression(self._body()))
            elif self.path == "/api/v1/jobs/cleanup":
                self._send(202, self.server.controller.start_cleanup())
            elif self.path == "/api/v1/jobs/stop":
                self._send(202, self.server.controller.stop())
            elif self.path == "/api/v1/jobs/pause":
                self._send(202, self.server.controller.pause())
            elif self.path == "/api/v1/jobs/resume":
                self._send(202, self.server.controller.resume())
            elif self.path == "/api/v1/jobs/queue/cancel":
                self._send(200, self.server.controller.cancel_queued(str(self._body().get("task_id") or "")))
            elif self.path.startswith("/api/v1/live-audits/") and self.path.endswith("/run"):
                portfolio_id = safe_int(self.path.strip("/").split("/")[-2], 0, minimum=1)
                body = self._body()
                body["portfolio_id"] = portfolio_id
                self._send(202, {"audit": self.server.controller.live_audits.start(body)})
            elif self.path == "/api/v1/universe/symbols":
                self._send(200, self.server.controller.update_universe(self._body()))
            elif self.path == "/api/v1/portfolios/save":
                self._send(201, node_portfolio_api.save_portfolio(self.server.controller, self._body(50_000_000)))
            elif self.path == "/api/v1/portfolios/alias":
                self._send(200, node_portfolio_api.set_portfolio_alias(self.server.controller, self._body()))
            elif self.path == "/api/v1/portfolios/exclude":
                self._send(200, node_portfolio_api.exclude_portfolio_members(self.server.controller, self._body()))
            elif self.path == "/api/v1/portfolios/requalify":
                self._send(200, node_portfolio_api.requalify_portfolio_member(self.server.controller, self._body()))
            elif self.path == "/api/v1/portfolios/delete":
                self._send(200, node_portfolio_api.delete_portfolio(self.server.controller, self._body()))
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": str(exc)})

class NodeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        controller: JobController,
        restart_callback: Callable[[], None] | None = None,
    ) -> None:
        self.controller = controller
        self.restart_callback = restart_callback
        self.controller.application_restart_available = restart_callback is not None
        super().__init__(address, NodeHandler)

    def request_application_restart(self) -> dict[str, Any]:
        callback = self.restart_callback
        if callback is None:
            raise RuntimeError("El reinicio remoto solo esta disponible en la aplicacion integrada")
        with self.controller.lock:
            process = self.controller.process
            process_running = process is not None and process.poll() is None
            status = str(self.controller.state.get("status") or "")
            restartable = status in {"idle", "completed", "failed", "stopped", "paused", "interrupted"}
            if process_running or self.controller.live_audits.is_running() or self.controller.queue or not restartable:
                raise RuntimeError(
                    "No se puede reiniciar la aplicacion con una ejecucion activa o tareas pendientes"
                )
        callback()
        return {"status": "restarting", "message": "Reinicio de la aplicacion solicitado"}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nodo remoto para MT5 Autotester Manager")
    parser.add_argument("--config", default="node.json")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    config = dev_branch.apply_node_config(load_json(config_path))
    for key in ("node_id", "project_dir", "token"):
        if not str(config.get(key) or "").strip():
            parser.error(f"Falta {key} en {config_path}")
    host = str(config.get("host") or "0.0.0.0")
    port = safe_int(config.get("port"), 8761, minimum=1, maximum=65535)
    server = NodeServer((host, port), JobController(config, config_path))
    print(f"Nodo {config['node_id']} escuchando en http://{host}:{port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
