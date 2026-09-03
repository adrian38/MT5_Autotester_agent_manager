from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import dev_branch
from . import guided_batches
from .common import json_bytes, load_json, safe_int, save_json, utc_now
from .live_audit_settings import LiveAuditSettingsStore
from .manager_restart import ManagerRestartController, RestartAlreadyRunning
from .portfolio_service import (
    PortfolioCoordinator,
    legacy_compatible_portfolio_save_payload,
)
from .portfolio_scope import PORTFOLIO_SCOPES, normalize_portfolio_scope


STATIC_DIR = Path(__file__).resolve().parent / "static"
FOLDER_PICKER_LOCK = threading.Lock()
BOOL_PREFERENCE_KEYS = (
    "run_robustness", "run_final_tick", "run_final_tick_6m", "run_regression",
    # `repair_run_regression` es la casilla del diálogo de Reparar, independiente de
    # `run_regression` (nueva ejecución) como `repair_max_workers` lo es de `max_workers`.
    "repair_run_regression",
    "repair_after_generation", "execute_backtests", "cleanup_after_run", "dry_run",
)
# Cada campo del diálogo de generación se recuerda por nodo a partir del propio
# lanzamiento, sin depender de que el navegador lo reenvíe a /preferences.
LAUNCH_PREFERENCE_KEYS = (
    "cycles", "generations", "variants_per_seed", "max_seeds", "generation_mode", "random_seed",
    "max_workers", "repair_max_workers", "repair_phase2_max_workers",
    "regression_max_workers", "repair_attempts",
    *BOOL_PREFERENCE_KEYS,
)
# Preferencias que el diálogo relee desde launch_defaults en lugar de launch_preferences.
LAUNCH_DEFAULT_OVERRIDE_KEYS = ("generations", "variants_per_seed", "max_seeds")
# Lo que un vigilante externo necesita para detectar que algo terminó o falló.
# `/api/nodes` ronda los 400 KB porque lleva el comando completo, el pipeline y
# el snapshot de la base de cada nodo; sondear eso cada medio minuto desde un
# móvil no es razonable, así que `/api/pulse` proyecta solo estos campos.
PULSE_JOB_KEYS = (
    "job_id", "job_type", "status", "current_stage", "return_code",
    "started_at", "finished_at", "error",
)
PULSE_PORTFOLIO_JOB_KEYS = ("status", "operation", "portfolio_id", "error")
PULSE_PORTFOLIO_TASK_KEYS = ("id", "status", "operation", "portfolio_id", "error")
DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS = {
    "enabled": False,
    "interval_days": 30,
}
_LEGACY_LIVE_AUDIT_SCHEDULER_KEYS = frozenset({
    "check_interval_minutes", "startup_delay_seconds",
})
_LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS = 30
_LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS = 300


def _truthy(*values: Any) -> bool:
    """Primer valor no vacío interpretado como interruptor; ausencia es «no».

    Un interruptor que arranca procesos desatendidos no puede activarse por un
    valor mal escrito: solo cuenta como sí lo que se reconoce explícitamente.
    """
    for value in values:
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"1", "true", "yes", "on", "si", "sí"}
    return False


def normalize_live_audit_scheduler_settings(
    value: dict[str, Any], defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normaliza la configuración persistente del programador interno."""
    if not isinstance(value, dict):
        raise ValueError("La configuración automática debe ser un objeto JSON")
    # Las claves técnicas de la primera versión se aceptan solo para migrar un
    # JSON antiguo. Ya no forman parte de la configuración pública ni se guardan.
    value = {key: item for key, item in value.items() if key not in _LEGACY_LIVE_AUDIT_SCHEDULER_KEYS}
    defaults = {
        key: item for key, item in dict(defaults or {}).items()
        if key not in _LEGACY_LIVE_AUDIT_SCHEDULER_KEYS
    }
    unknown = set(value) - set(DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = {**DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS, **defaults}
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ValueError("enabled debe ser true o false")
        normalized["enabled"] = value["enabled"]
    if "interval_days" in value:
        try:
            interval_days = int(value["interval_days"])
        except (TypeError, ValueError) as exc:
            raise ValueError("interval_days debe ser un entero") from exc
        if isinstance(value["interval_days"], bool) or not 1 <= interval_days <= 3650:
            raise ValueError("interval_days debe estar entre 1 y 3650")
        normalized["interval_days"] = interval_days
    return normalized


def choose_directory(
    initial_directory: str | None = None,
    title: str = "Selecciona la carpeta para exportar los sets",
) -> str | None:
    """Open the native desktop folder picker on the manager machine."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise ValueError("El selector de carpetas no está disponible en este equipo") from exc

    initial = Path(initial_directory).expanduser() if initial_directory else Path.home()
    if not initial.is_dir():
        initial = Path.home()
    with FOLDER_PICKER_LOCK:
        root = tk.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            root.update()
            selected = filedialog.askdirectory(
                parent=root,
                title=title,
                initialdir=str(initial),
                mustexist=True,
            )
        finally:
            root.destroy()
    return str(Path(selected).resolve()) if selected else None


def live_log_progress(lines: list[Any], current_stage: object) -> dict[str, Any]:
    text = "\n".join(str(line) for line in lines)
    stage = str(current_stage or "").strip()
    marker = f"[manager-node] Iniciando etapa: {stage}" if stage else ""
    marker_at = text.rfind(marker) if marker else -1
    segment = text[marker_at:] if marker_at >= 0 else text
    starts = re.findall(
        r"DIAG WORKER_JOB_START profile=(\S+).*?job=(\d+).*?remaining_queue=(\d+)",
        segment,
    )
    dones = re.findall(r"DIAG WORKER_JOB_DONE profile=(\S+).*?job=(\d+)", segment)
    active_by_profile: dict[str, int] = {}
    for profile, _job, _remaining in starts:
        active_by_profile[profile] = active_by_profile.get(profile, 0) + 1
    for profile, _job in dones:
        active_by_profile[profile] = max(0, active_by_profile.get(profile, 0) - 1)
    active = sum(active_by_profile.values())
    remaining = int(starts[-1][2]) if starts else None
    waits = re.findall(r"MT5 sigue activo:\s*(\d+)s", segment)
    return {
        "jobs_started": len(starts),
        "jobs_completed": len(dones),
        "active_jobs": active,
        "remaining_queue": remaining,
        "last_job": int(starts[-1][1]) if starts else None,
        "last_profile": starts[-1][0] if starts else None,
        "waiting_seconds": int(waits[-1]) if waits else None,
    }


def submit_guided_to_node(node: dict[str, Any], package: dict[str, Any]) -> tuple[int, Any]:
    project = node.get('portfolio_project_dir')
    if not project:
        raise ValueError('El nodo no tiene proyecto/broker configurado')
    dev_branch.assert_writable(project, 'Lote guiado')
    broker = str(node.get('portfolio_broker') or '').upper()
    account = str(node.get('portfolio_account_type') or '').upper()
    normalize_path = lambda value: str(value or '').replace('/', '\\').rstrip('\\').casefold()
    remote_project = node.get('node_project_dir') or project
    # Docker has no .git in /app. Inspect its existing checkout bind mount too;
    # a container must not turn dev into permission to write production nodes.
    checkout = os.environ.get('MT5_MANAGER_RESTART_REPO')
    if checkout and dev_branch.is_active(Path(checkout)):
        explicitly_allowed = {
            value.strip().upper()
            for value in os.environ.get('MT5_MANAGER_GUIDED_DEV_BROKERS', '').split(',')
            if value.strip()
        }
        if broker not in ({dev_branch.DEV_BROKER} | explicitly_allowed):
            raise ValueError('La rama dev solo permite lotes al agente IC local')
        if broker == dev_branch.DEV_BROKER and normalize_path(remote_project) != normalize_path(dev_branch.DEV_PROJECT_DIR):
            raise ValueError('La rama dev solo permite lotes al agente IC local')
    guided_batches.validate_package(package, broker, account)
    status, state = node_request(node, 'GET', '/api/v1/status', timeout=15)
    if status!=200 or not (state.get('capabilities') or {}).get('guided_batches_v1'):
        raise ValueError('El nodo todavía no soporta lotes guiados; actualizar su runtime')
    identity = state.get('node') or {}
    if identity.get('broker')!=broker or identity.get('account_type')!=account:
        raise ValueError('La identidad del nodo no coincide con el destino')
    if normalize_path(identity.get('project_dir'))!=normalize_path(remote_project):
        raise ValueError('El proyecto anunciado por el nodo no coincide con el configurado')
    return node_request(node, 'POST', '/api/v1/guided-batches', package, timeout=60)


def node_request(
    node: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None = None,
    *, timeout: float | None = None,
) -> tuple[int, Any]:
    base_url = str(node.get("url") or "").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"URL invalida para {node.get('id')}: {base_url}")
    body = json_bytes(payload) if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {node.get('token', '')}", "Content-Type": "application/json"},
    )
    try:
        request_timeout = float(timeout if timeout is not None else node.get("timeout", 5))
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            value = json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            value = {"error": raw.decode("utf-8", errors="replace") or str(exc)}
        return exc.code, value


def node_artifact_request(
    node: dict[str, Any], path: str, *, timeout: float = 120,
) -> tuple[int, bytes, str]:
    """Obtiene bytes de un reporte del nodo sin interpretarlos como JSON."""
    base_url = str(node.get("url") or "").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"URL invalida para {node.get('id')}: {base_url}")
    request = urllib.request.Request(
        base_url + path,
        method="GET",
        headers={"Authorization": f"Bearer {node.get('token', '')}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "application/json; charset=utf-8")


def submit_repair_request(node: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        status, value = node_request(
            node, "POST", "/api/v1/jobs/repair", payload, timeout=3600
        )
        if status >= 400:
            sys.stderr.write(
                f"[manager-repair] El nodo {node.get('id')} devolvio HTTP {status}: {value}\n"
            )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        sys.stderr.write(
            f"[manager-repair] No se pudo enviar la reparacion a {node.get('id')}: {exc}\n"
        )


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
        self.send_header("X-Missing-Sets", str(len(value.get("missing") or [])))
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

    def _live_audit_config_state(self, node: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Añade el estado operativo del agente sin exponer credenciales."""
        state = dict(state)
        state["audit_states"] = {}
        try:
            status, value = node_request(node, "GET", "/api/v1/live-audits", timeout=10)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            state["phase"] = "agent_unavailable"
            state["connection_error"] = str(exc)
            return state
        if status == 200 and isinstance(value, dict):
            state["phase"] = "connected"
            state["audit_states"] = value.get("audits") if isinstance(value.get("audits"), dict) else {}
        elif status != 404:
            state["phase"] = "agent_unavailable"
            state["connection_error"] = str(value.get("error") if isinstance(value, dict) else value)
        return state

    def _all_status(self) -> list[dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(self.server.nodes))) as executor:
            futures = {executor.submit(node_request, node, "GET", "/api/v1/status"): node for node in self.server.nodes}
            for future in as_completed(futures):
                node = futures[future]
                node_id = str(node.get("id"))
                try:
                    status, value = future.result()
                    if status >= 400:
                        raise RuntimeError(str(value.get("error") if isinstance(value, dict) else value))
                    if not isinstance(value, dict) or not isinstance(value.get("job"), dict):
                        raise ValueError("Respuesta de estado del nodo no válida")
                    if isinstance(value, dict):
                        value["manager_node"] = {"id": node_id, "name": node.get("name") or node_id, "url": node.get("url")}
                        preferences = self.server.preferences_for(node_id)
                        value["launch_preferences"] = preferences
                        defaults = value.get("launch_defaults")
                        if isinstance(defaults, dict):
                            value["launch_defaults"] = {**defaults, **{
                                key: preferences[key]
                                for key in LAUNCH_DEFAULT_OVERRIDE_KEYS
                                if key in preferences
                            }}
                        value["manager_portfolio"] = {
                            "available": bool(str(node.get("portfolio_project_dir") or "").strip()),
                            "engine": "central",
                        }
                        if str((value.get("job") or {}).get("status")) == "running":
                            try:
                                log_status, log_value = node_request(node, "GET", "/api/v1/logs?lines=500")
                                if log_status < 400 and isinstance(log_value, dict):
                                    value["live_progress"] = live_log_progress(
                                        list(log_value.get("lines") or []),
                                        (value.get("job") or {}).get("current_stage"),
                                    )
                            except (ValueError, urllib.error.URLError, TimeoutError):
                                pass
                    value["last_successful_at"] = utc_now()
                    with self.server.node_status_lock:
                        self.server.node_status_cache[node_id] = copy.deepcopy(value)
                    results[node_id] = value
                except Exception as exc:
                    with self.server.node_status_lock:
                        cached = copy.deepcopy(self.server.node_status_cache.get(node_id, {}))
                    results[node_id] = {
                        **cached,
                        "manager_node": {"id": node_id, "name": node.get("name") or node_id, "url": node.get("url")},
                        "offline": True, "stale": bool(cached), "error": str(exc),
                        "last_attempt_at": utc_now(),
                    }
        return [results[str(node.get("id"))] for node in self.server.nodes]

    def _pulse(self) -> dict[str, Any]:
        """Estado mínimo para un vigilante externo, sin el peso del panel.

        Se apoya en `_all_status` a propósito, aunque solo aproveche una parte:
        duplicar aquí la consulta a los nodos abriría la puerta a que el móvil
        y el panel no vieran lo mismo, que es justo lo que no puede pasar en un
        aviso de «terminó» o «falló».

        Los portafolios se leen del coordinador central, que los tiene en
        memoria: no cuestan red y por eso van en la misma respuesta, para que el
        vigilante cierre todo con una sola petición.
        """
        nodes = []
        for status in self._all_status():
            meta = status.get("manager_node") if isinstance(status.get("manager_node"), dict) else {}
            job = status.get("job") if isinstance(status.get("job"), dict) else {}
            # `task_queue` es el snapshot {count, items}, no una lista: contarlo
            # directamente daría el número de claves del dict.
            queue = status.get("task_queue") if isinstance(status.get("task_queue"), dict) else {}
            nodes.append({
                "id": meta.get("id"),
                "name": meta.get("name"),
                "offline": bool(status.get("offline")),
                "stale": bool(status.get("stale") or status.get("job_snapshot_stale")),
                "error": status.get("error"),
                "queued": safe_int(queue.get("count"), 0, minimum=0),
                "job": {key: job.get(key) for key in PULSE_JOB_KEYS},
            })
        portfolios = []
        for node in self.server.nodes:
            node_id = str(node.get("id"))
            if not str(node.get("portfolio_project_dir") or "").strip():
                continue
            for scope in PORTFOLIO_SCOPES:
                try:
                    state = self.server.portfolios.task_state(node_id, scope)
                except (KeyError, ValueError):
                    continue
                job = state.get("job") if isinstance(state.get("job"), dict) else {}
                task = state.get("task") if isinstance(state.get("task"), dict) else {}
                portfolios.append({
                    "node_id": node_id,
                    "scope": scope,
                    "job": {key: job.get(key) for key in PULSE_PORTFOLIO_JOB_KEYS},
                    "task": {key: task.get(key) for key in PULSE_PORTFOLIO_TASK_KEYS},
                })
        return {"nodes": nodes, "portfolios": portfolios, "observed_at": utc_now()}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts)==5 and parts[:2]==["api","nodes"] and parts[3]=="guided-batches":
            try:
                batch_id = parts[4]
                if not re.fullmatch("[a-f0-9]{64}", batch_id):
                    raise ValueError("Identificador de lote inválido")
                status, value = node_request(self._node(urllib.parse.unquote(parts[2])), "GET", "/api/v1/guided-batches/"+batch_id, timeout=30)
                self._send_json(status, value)
            except (KeyError, ValueError, OSError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/manager/restart":
            query = urllib.parse.parse_qs(parsed.query)
            lines = safe_int(query.get("lines", [120])[0], 120, minimum=1, maximum=1000)
            self._send_json(200, self.server.manager_restart.status(log_lines=lines))
            return
        if parsed.path == "/api/live-audit-scheduler-config":
            self._send_json(200, self.server.live_audit_scheduler_state())
            return
        if parsed.path == "/api/nodes":
            self._send_json(200, {"nodes": self._all_status(), "observed_at": utc_now()})
            return
        if parsed.path == "/api/pulse":
            self._send_json(200, self._pulse())
            return
        if parsed.path.startswith("/api/nodes/") and parsed.path.endswith("/logs"):
            parts = parsed.path.strip("/").split("/")
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                lines = safe_int(query.get("lines", [200])[0], 200, minimum=1, maximum=2000)
                status, value = node_request(node, "GET", f"/api/v1/logs?lines={lines}")
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "runs":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=100)
                offset = safe_int(query.get("offset", [0])[0], 0, minimum=0)
                status, value = node_request(
                    node, "GET", f"/api/v1/runs?limit={limit}&offset={offset}", timeout=120
                )
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "universe":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                status, value = node_request(node, "GET", "/api/v1/universe")
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audit-config":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                state = self._live_audit_config_state(
                    node, self.server.live_audit_settings.state(node_id)
                )
                state["node"] = {"id": node_id, "name": node.get("name") or node_id}
                self._send_json(200, state)
            except KeyError as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if (
            len(parts) == 8 and parts[:2] == ["api", "nodes"]
            and parts[3] == "live-audits" and parts[5] == "artifacts"
        ):
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                encoded = "/".join(
                    urllib.parse.quote(urllib.parse.unquote(value), safe="")
                    for value in (parts[4], parts[6], parts[7])
                )
                status, body, content_type = node_artifact_request(
                    node, f"/api/v1/live-audits/{encoded.split('/')[0]}/artifacts/"
                    f"{encoded.split('/')[1]}/{encoded.split('/')[2]}", timeout=120,
                )
                self._send_artifact(status, body, content_type)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audits":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                audit_id = urllib.parse.unquote(parts[4])
                status, value = node_request(
                    node, "GET", f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}", timeout=10
                )
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-manager":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                query = urllib.parse.parse_qs(parsed.query)
                scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
                state = self.server.portfolios.state(node_id, scope)
                state["capabilities"] = {"export_mode": self.server.export_mode}
                self._send_json(200, state)
            except (KeyError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3:] == ["portfolio-manager", "task"]:
            try:
                node_id = urllib.parse.unquote(parts[2])
                self._node(node_id)
                query = urllib.parse.parse_qs(parsed.query)
                scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
                self._send_json(200, self.server.portfolios.task_state(node_id, scope))
            except (KeyError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) in {4, 5} and parts[:2] == ["api", "nodes"] and parts[3] == "portfolios":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
                portfolio_id = safe_int(parts[4], 0, minimum=1) if len(parts) == 5 else None
                if str(node.get("portfolio_project_dir") or "").strip():
                    self._send_json(200, self.server.portfolios.saved(str(node["id"]), scope, portfolio_id))
                else:
                    suffix = f"/{portfolio_id}" if portfolio_id is not None else ""
                    status, value = node_request(node, "GET", f"/api/v1/portfolios{suffix}?scope={scope}")
                    self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        relative = parsed.path.lstrip("/")
        if relative in {
            "app.js", "styles.css", "universe.html", "universe.js",
            "live_audit.html", "live_audit.js", "live_audit.css",
            "live_audit_result.html", "live_audit_result.js", "live_audit_result.css",
            "portfolios.html", "portfolios.js",
            "portfolios_monthly.html", "portfolios_monthly.js",
            "portfolio_improvement.js", "portfolio_monthly_improvement.js",
            "portfolios_grid.html", "portfolios_grid.js",
            # Primitiva compartida por los tres ámbitos: el diálogo del motivo de
            # exclusión y las etiquetas de sus tres códigos. La interfaz de cada
            # ámbito sigue siendo suya; lo que no puede divergir es el código que
            # viaja al nodo y decide qué se escribe en la memoria del agente.
            "exclusion_reason.js",
            # Importar es el reflejo de exportar y hereda su transporte: la
            # lectura del ZIP y el resumen del resultado no pueden divergir
            # entre pantallas.
            "portfolio_transfer.js",
        }:
            self._send_file(STATIC_DIR / relative)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
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
            return
        if parsed.path == "/api/manager/restart":
            try:
                self._send_json(202, self.server.manager_restart.start())
            except RestartAlreadyRunning as exc:
                self._send_json(409, {"error": str(exc)})
            except (ValueError, OSError, RuntimeError) as exc:
                self._send_json(503, {"error": str(exc)})
            return
        if parsed.path == "/api/live-audit-scheduler-config":
            try:
                self._send_json(200, self.server.update_live_audit_scheduler(self._body()))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3:] == ["queue", "cancel"]:
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                status, value = node_request(node, "POST", "/api/v1/jobs/queue/cancel", self._body())
                self._send_json(status, value)
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except (urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "preferences":
            try:
                node_id = urllib.parse.unquote(parts[2])
                self._node(node_id)
                saved = self.server.update_preferences(node_id, self._body())
                self._send_json(200, {"preferences": saved})
            except (KeyError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audit-config":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                state = self._live_audit_config_state(
                    node, self.server.live_audit_settings.update(node_id, self._body())
                )
                state["node"] = {"id": node_id, "name": node.get("name") or node_id}
                self._send_json(200, state)
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audit-restore-account":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                state = self._live_audit_config_state(
                    node, self.server.live_audit_settings.update_restore_account(node_id, self._body())
                )
                state["node"] = {"id": node_id, "name": node.get("name") or node_id}
                self._send_json(200, state)
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if (
            len(parts) == 6 and parts[:2] == ["api", "nodes"]
            and parts[3] == "live-audits" and parts[5] == "run"
        ):
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                node_status, node_state = node_request(node, "GET", "/api/v1/status", timeout=10)
                capabilities = (
                    node_state.get("capabilities") if node_status == 200 and isinstance(node_state, dict) else {}
                )
                if not isinstance(capabilities, dict) or not capabilities.get("live_audit_restore_account"):
                    raise ValueError(
                        "El agente ICTrading aún usa el auditor anterior; reinícialo cuando termine su trabajo actual."
                    )
                audit_id = urllib.parse.unquote(parts[4])
                state = self.server.live_audit_settings.state(node_id)
                if audit_id not in state.get("configured_audit_ids", []):
                    raise ValueError(f"Guarda la configuración completa del uso {audit_id}")
                profile = dict((state.get("profiles") or {}).get(audit_id) or {})
                portfolio_id = safe_int(profile.get("portfolio_id"), 0, minimum=1)
                credentials = self.server.live_audit_settings.credentials(node_id, audit_id)
                restore = self.server.live_audit_settings.restore_credentials(node_id)
                if not restore:
                    raise ValueError("Configura y guarda la cuenta que debe quedar en los terminales")
                payload = {
                    **profile, **credentials, **restore,
                    "audit_key": audit_id, "portfolio_id": portfolio_id,
                }
                status, value = node_request(
                    node, "POST", f"/api/v1/live-audits/{portfolio_id}/run", payload, timeout=30
                )
                if status == 404:
                    raise ValueError("El agente ICTrading todavía no tiene cargado el motor de auditoría; reinícialo.")
                self._send_json(status, value)
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except (urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-manager":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                body = self._body()
                scope = normalize_portfolio_scope(body.pop("scope", "full_history"))
                action = parts[4]
                if action == "settings":
                    self._send_json(200, {"settings": self.server.portfolios.update_settings(node_id, scope, body)})
                elif action == "generate":
                    self._send_json(202, {"job": self.server.portfolios.start(node_id, scope, body)})
                elif action == "stop":
                    self._send_json(202, {"job": self.server.portfolios.stop(node_id, scope)})
                elif action == "save":
                    save_payload = self.server.portfolios.prepare_save(
                        node_id, scope, str(body.get("proposal_key") or "")
                    )
                    if scope == "grid":
                        value = self.server.portfolios.save_grid_package(node_id, save_payload)
                        portfolio_id = safe_int(value.get("portfolio_id"), 0)
                        request_id = str(value.get("request_id") or "")
                        if portfolio_id <= 0 or request_id != str(save_payload["request_id"]):
                            raise ValueError("El manager no confirmó correctamente el paquete Grid")
                        self.server.portfolios.confirm_save(
                            node_id, scope, request_id, portfolio_id
                        )
                        variant_ids = {
                            str(proposal.get("key") or ""): portfolio_id
                            for proposal in save_payload.get("proposals") or []
                            if isinstance(proposal, dict) and proposal.get("key")
                        }
                        self._send_json(201, {
                            "portfolio_id": portfolio_id,
                            "portfolio_ids": variant_ids,
                        })
                        return
                    portfolio_ids: dict[str, int] = {}
                    for variant_payload in (save_payload,):
                        status, value = node_request(
                            node, "POST", "/api/v1/portfolios/save", variant_payload, timeout=120
                        )
                        error_text = str(value.get("error") if isinstance(value, dict) else value or "")
                        if status >= 400 and "unexpected keyword argument" in error_text:
                            status, value = node_request(
                                node,
                                "POST",
                                "/api/v1/portfolios/save",
                                legacy_compatible_portfolio_save_payload(variant_payload),
                                timeout=120,
                            )
                        if status == 404:
                            raise ValueError(
                                "El nodo todavía no admite guardado local de portafolios; "
                                "actualiza su código y reinícialo."
                            )
                        if status >= 400 or not isinstance(value, dict):
                            error = value.get("error") if isinstance(value, dict) else value
                            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
                        portfolio_id = safe_int(value.get("portfolio_id"), 0)
                        request_id = str(value.get("request_id") or "")
                        if portfolio_id <= 0 or request_id != str(variant_payload["request_id"]):
                            raise ValueError("El nodo no confirmó correctamente el guardado")
                        portfolio_ids[str(variant_payload["selected_key"])] = portfolio_id
                    selected_key = str(save_payload["selected_key"])
                    selected_id = portfolio_ids.get(selected_key, 0)
                    if selected_id <= 0:
                        raise ValueError("No se guardó la variante Grid seleccionada")
                    self.server.portfolios.confirm_save(
                        node_id, scope, str(save_payload["request_id"]), selected_id
                    )
                    self._send_json(201, {
                        "portfolio_id": selected_id,
                        "portfolio_ids": portfolio_ids,
                    })
                elif action in {"reoptimize", "complete", "improve"}:
                    portfolio_id = safe_int(body.pop("portfolio_id", 0), 0, minimum=1)
                    self._send_json(202, {"job": self.server.portfolios.start_saved_operation(
                        node_id, scope, portfolio_id, action, body or None
                    )})
                elif action == "exclude":
                    if scope == "grid":
                        # El paquete Grid vive en la base del manager y la
                        # cuarentena en la memoria del nodo: el coordinador
                        # reparte cada escritura a su dueño.
                        self._send_json(201, self.server.portfolios.exclude_grid(node_id, body))
                    elif body.get("set_paths") is not None:
                        status, value = node_request(
                            node,
                            "POST",
                            "/api/v1/portfolios/exclude",
                            {**body, "scope": scope},
                            timeout=120,
                        )
                        if status == 404:
                            raise ValueError(
                                "El nodo todavía no admite exclusión múltiple local; "
                                "actualiza su código y reinícialo."
                            )
                        if status >= 400 or not isinstance(value, dict):
                            error = value.get("error") if isinstance(value, dict) else value
                            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
                        portfolio_id = safe_int(body.get("portfolio_id"), 0, minimum=1)
                        # El portafolio guardado ya no se borra, así que lo que se
                        # confirma es la cuarentena, no un borrado.
                        if not value.get("quarantine_ids") or safe_int(value.get("portfolio_id"), 0) != portfolio_id:
                            raise ValueError("El nodo no confirmó correctamente la exclusión múltiple")
                        self.server.portfolios.invalidate_after_exclusion(node_id)
                        # Misma comprobación que en la exclusión individual: un nodo
                        # sin portar acepta el motivo y no escribe el veredicto.
                        PortfolioCoordinator._assert_node_applied_verdict(body, value)
                        self._send_json(201, value)
                    else:
                        quarantine_result = self.server.portfolios.exclude(node_id, scope, body)
                        self._send_json(201, {"quarantine_id": quarantine_result})
                elif action == "release":
                    self.server.portfolios.release(node_id, scope, str(body.get("quarantine_id") or ""))
                    self._send_json(200, {"released": True})
                elif action == "requalify":
                    # Mover una estrategia excluida entre los tres motivos y el
                    # pool. Reintegrar es el caso `pool` de esta misma operación.
                    target = self.server.portfolios.requalify(
                        node_id, scope,
                        str(body.get("quarantine_id") or ""),
                        str(body.get("reason_code") or "pool"),
                    )
                    self._send_json(200, {"reason_code": target})
                elif action == "undo":
                    version = self.server.portfolios.undo(node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1))
                    self._send_json(200, {"restored_version": version})
                elif action == "delete":
                    task = self.server.portfolios.delete(
                        node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1)
                    )
                    self._send_json(202, {"task": task})
                elif action == "choose-import-folder":
                    if self.server.export_mode != "folder":
                        raise ValueError("El selector local de carpetas no está disponible en modo Docker")
                    folder = choose_directory(
                        str(body.get("initial_directory") or "").strip() or None,
                        title="Selecciona la carpeta del portafolio exportado",
                    )
                    self._send_json(200, {"folder": folder, "cancelled": folder is None})
                elif action == "import":
                    self._send_json(201, self.server.portfolios.import_portfolio(node_id, scope, body))
                elif action == "choose-export-folder":
                    if self.server.export_mode != "folder":
                        raise ValueError("El selector local de carpetas no está disponible en modo Docker")
                    folder = choose_directory(
                        str(body.get("initial_directory") or "").strip() or None
                    )
                    self._send_json(200, {"folder": folder, "cancelled": folder is None})
                elif action == "export-download":
                    result = self.server.portfolios.export_archive(
                        node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1)
                    )
                    self._send_download(result)
                elif action == "export":
                    result = self.server.portfolios.export(
                        node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1),
                        str(body.get("destination") or "").strip() or None,
                    )
                    self._send_json(200, result)
                elif action == "open-report":
                    report = self.server.portfolios.open_report(
                        node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1),
                        str(body.get("set_path") or ""),
                    )
                    self._send_json(200, {"report": report})
                elif action == "log":
                    self._send_json(200, self.server.portfolios.log(
                        node_id, scope, safe_int(body.get("lines"), 500, minimum=1, maximum=5000)
                    ))
                else:
                    self._send_json(404, {"error": "Acción de portafolio desconocida"})
            except (
                KeyError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError,
                urllib.error.URLError, TimeoutError,
            ) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) != 4 or parts[:2] != ["api", "nodes"] or parts[3] not in {
            "start", "stop", "pause", "resume", "restart", "repair", "regression", "cleanup", "universe",
            "universe-sync", "universe-history-preview", "universe-history",
            "universe-disable-preview", "universe-disable-no-history",
            "universe-trade-disabled-preview", "universe-disable-trade-disabled",
        }:
            self._send_json(404, {"error": "Ruta no encontrada"})
            return
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = self._node(node_id)
            targets = {
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
            target = targets[parts[3]]
            body = self._body()
            if parts[3] == "repair":
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
                return
            if parts[3].startswith("universe-"):
                project = node.get("portfolio_project_dir")
                if dev_branch.is_active() and not project:
                    raise ValueError("Falta portfolio_project_dir para verificar el destino en dev")
                if project:
                    dev_branch.assert_writable(project, "sincronización de símbolos")
                # MT5 initialization can exceed the normal status timeout. Never
                # retry a mutation: the node may already have applied it.
                status, value = node_request(node, "POST", target, body, timeout=120)
            else:
                status, value = node_request(node, "POST", target, body)
            if parts[3] == "start" and status < 400:
                self.server.remember_launch_request(node_id, body)
            self._send_json(status, value)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except (urllib.error.URLError, TimeoutError) as exc:
            self._send_json(502, {"error": str(exc)})


class ManagerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: dict[str, Any]) -> None:
        nodes = config.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("manager.json debe contener una lista nodes no vacia")
        self.nodes = nodes
        self.node_status_lock = threading.Lock()
        self.node_status_cache: dict[str, dict[str, Any]] = {}
        export_mode = str(
            os.environ.get("MT5_MANAGER_EXPORT_MODE") or config.get("export_mode") or "folder"
        ).strip().lower()
        if export_mode not in {"folder", "download"}:
            raise ValueError("export_mode debe ser folder o download")
        self.export_mode = export_mode
        preferences_file = str(config.get("preferences_file") or "").strip()
        self.preferences_path = Path(preferences_file).expanduser().resolve() if preferences_file else None
        self.preferences_lock = threading.RLock()
        self.preferences: dict[str, dict[str, Any]] = {}
        if self.preferences_path and self.preferences_path.is_file():
            try:
                stored = load_json(self.preferences_path)
                self.preferences = {
                    str(key): dict(value) for key, value in stored.items() if isinstance(value, dict)
                }
            except ValueError:
                self.preferences = {}
        live_audit_settings_file = str(config.get("live_audit_settings_file") or "").strip()
        live_audit_settings_path = (
            Path(live_audit_settings_file).expanduser().resolve()
            if live_audit_settings_file
            else Path.cwd() / "runtime" / "live_audit_settings.json"
        )
        self.live_audit_settings = LiveAuditSettingsStore(live_audit_settings_path)
        scheduler_file = str(config.get("live_audit_scheduler_settings_file") or "").strip()
        self.live_audit_scheduler_path = (
            Path(scheduler_file).expanduser().resolve()
            if scheduler_file else live_audit_settings_path.with_name("live_audit_scheduler.json")
        )
        scheduler_defaults = {
            "enabled": _truthy(config.get("live_audit_scheduler_enabled")),
            "interval_days": safe_int(
                config.get("live_audit_scheduler_interval_days"), 30,
                minimum=1, maximum=3650,
            ),
        }
        self.live_audit_scheduler_settings = dict(scheduler_defaults)
        if self.live_audit_scheduler_path.is_file():
            try:
                self.live_audit_scheduler_settings = normalize_live_audit_scheduler_settings(
                    load_json(self.live_audit_scheduler_path), scheduler_defaults,
                )
            except ValueError as exc:
                print(f"[live-audit-scheduler] configuración ignorada: {exc}", flush=True)
        raw_scheduler_environment = os.environ.get("MT5_MANAGER_LIVE_AUDIT_SCHEDULER")
        self.live_audit_scheduler_environment = (
            str(raw_scheduler_environment).strip()
            if raw_scheduler_environment is not None and str(raw_scheduler_environment).strip()
            else None
        )
        portfolio_settings_file = str(config.get("portfolio_settings_file") or "").strip()
        portfolio_settings_path = (
            Path(portfolio_settings_file).expanduser().resolve()
            if portfolio_settings_file
            else Path.cwd() / "runtime" / "portfolio_settings.json"
        )
        self.portfolios = PortfolioCoordinator(nodes, portfolio_settings_path)
        repo_dir = str(
            os.environ.get("MT5_MANAGER_RESTART_REPO")
            or config.get("manager_repo_dir")
            or Path(__file__).resolve().parents[1]
        )
        restart_state_file = str(
            os.environ.get("MT5_MANAGER_RESTART_STATE")
            or config.get("manager_restart_state_file")
            or Path.cwd() / "runtime" / "manager_restart.json"
        )
        restart_log_file = str(
            os.environ.get("MT5_MANAGER_RESTART_LOG")
            or config.get("manager_restart_log_file")
            or Path.cwd() / "runtime" / "manager_restart.log"
        )
        self.manager_restart = ManagerRestartController(
            repo_dir,
            restart_state_file,
            restart_log_file,
            container_name=str(
                os.environ.get("MT5_MANAGER_CONTAINER_NAME")
                or config.get("manager_container_name")
                or "mt5-autotester-manager"
            ),
        )
        super().__init__(address, ManagerHandler)
        # La auditoría en vivo se dispara **solo a mano** mientras el MVP no esté
        # cerrado. Automática pausaba el pipeline del agente, corría sola y lo
        # reanudaba sin nadie delante; el 2026-08-21 una ejecución desatendida
        # dejó un terminal sin cuenta y dos días de discovery a cero. Para
        # rearmarla: `live_audit_scheduler_enabled: true` en manager.json, o
        # MT5_MANAGER_LIVE_AUDIT_SCHEDULER=1.
        self.live_audit_scheduler_enabled = (
            _truthy(self.live_audit_scheduler_environment)
            if self.live_audit_scheduler_environment is not None
            else bool(self.live_audit_scheduler_settings["enabled"])
        )
        self.live_audit_stop = threading.Event()
        self.live_audit_wakeup = threading.Event()
        self.live_audit_thread: threading.Thread | None = None
        if not self.live_audit_scheduler_enabled:
            print(
                "[live-audit-scheduler] desactivado: la auditoría solo se lanza a mano. "
                "Para rearmarlo, live_audit_scheduler_enabled=true en manager.json.",
                flush=True,
            )
            return
        self.live_audit_thread = threading.Thread(
            target=self._live_audit_schedule_loop,
            daemon=True,
            name="live-audit-scheduler",
        )
        self.live_audit_thread.start()

    def server_close(self) -> None:
        self.live_audit_stop.set()
        self.live_audit_wakeup.set()
        super().server_close()

    def live_audit_scheduler_state(self) -> dict[str, Any]:
        """Contrato público y explícito del antiguo «cron» interno."""
        return {
            **dict(self.live_audit_scheduler_settings),
            "effective_enabled": bool(self.live_audit_scheduler_enabled),
            "source": "environment" if self.live_audit_scheduler_environment is not None else "saved",
            "environment_override": self.live_audit_scheduler_environment is not None,
            "description": (
                "El manager ejecuta las auditorías configuradas cada X días. "
                "Nunca vuelve a iniciar una que ya esté ejecutándose."
            ),
        }

    def update_live_audit_scheduler(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Persiste y aplica el programador sin exigir reiniciar el manager."""
        normalized = normalize_live_audit_scheduler_settings(
            changes, self.live_audit_scheduler_settings,
        )
        save_json(self.live_audit_scheduler_path, normalized)
        self.live_audit_scheduler_settings = normalized
        self.live_audit_scheduler_enabled = (
            _truthy(self.live_audit_scheduler_environment)
            if self.live_audit_scheduler_environment is not None
            else bool(normalized["enabled"])
        )
        if self.live_audit_scheduler_enabled and self.live_audit_thread is None:
            self.live_audit_thread = threading.Thread(
                target=self._live_audit_schedule_loop,
                daemon=True,
                name="live-audit-scheduler",
            )
            self.live_audit_thread.start()
        else:
            self.live_audit_wakeup.set()
        return self.live_audit_scheduler_state()

    @staticmethod
    def _audit_timestamp(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _live_audit_schedule_loop(self) -> None:
        # La espera inicial y el sondeo son detalles internos; el usuario solo
        # configura la cadencia real en días.
        if self.live_audit_stop.wait(_LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS):
            return
        while not self.live_audit_stop.is_set():
            try:
                self._run_due_live_audits()
            except Exception as exc:
                sys.stderr.write(f"[live-audit-scheduler] {exc}\n")
            self.live_audit_wakeup.wait(_LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS)
            self.live_audit_wakeup.clear()

    def _run_due_live_audits(self) -> None:
        # Segundo candado: aunque alguien arranque el bucle, sin el interruptor
        # no se lanza ninguna auditoría.
        if not self.live_audit_scheduler_enabled:
            return
        now = datetime.now(timezone.utc)
        for node in self.nodes:
            if not self.live_audit_scheduler_enabled or self.live_audit_stop.is_set():
                return
            node_id = str(node.get("id") or "")
            state = self.live_audit_settings.state(node_id)
            configured = list(state.get("configured_audit_ids") or [])
            if not configured:
                continue
            try:
                status_status, node_state = node_request(node, "GET", "/api/v1/status", timeout=10)
                capabilities = (
                    node_state.get("capabilities")
                    if status_status == 200 and isinstance(node_state, dict) else {}
                )
                if not isinstance(capabilities, dict) or not capabilities.get("live_audit_restore_account"):
                    sys.stderr.write(
                        f"[live-audit-scheduler] {node_id}: auditor antiguo; pendiente reiniciar el agente\n"
                    )
                    continue
                status, value = node_request(node, "GET", "/api/v1/live-audits", timeout=10)
            except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                sys.stderr.write(f"[live-audit-scheduler] {node_id}: no se pudo consultar el nodo: {exc}\n")
                continue
            if status != 200 or not isinstance(value, dict):
                sys.stderr.write(f"[live-audit-scheduler] {node_id}: GET devolvió HTTP {status}: {value}\n")
                continue
            audits = value.get("audits") if isinstance(value.get("audits"), dict) else {}
            for audit_id in configured:
                if not self.live_audit_scheduler_enabled or self.live_audit_stop.is_set():
                    return
                profile = dict((state.get("profiles") or {}).get(str(audit_id)) or {})
                portfolio_id = safe_int(profile.get("portfolio_id"), 0, minimum=1)
                audit = dict(audits.get(str(audit_id)) or {})
                if str(audit.get("status") or "") in {
                    "queued", "pausing", "extracting", "testing", "comparing", "finalizing", "resuming",
                }:
                    continue
                result = audit.get("last_result") if isinstance(audit.get("last_result"), dict) else {}
                previous = self._audit_timestamp(result.get("completed_at") or audit.get("finished_at"))
                interval = int(self.live_audit_scheduler_settings["interval_days"])
                if previous is not None:
                    if previous.tzinfo is None:
                        previous = previous.replace(tzinfo=timezone.utc)
                    if now - previous < timedelta(days=interval):
                        continue
                payload = {
                    **profile,
                    **self.live_audit_settings.credentials(node_id, audit_id),
                    **self.live_audit_settings.restore_credentials(node_id),
                    "audit_key": audit_id,
                    "portfolio_id": portfolio_id,
                }
                if not payload.get("restore_password"):
                    sys.stderr.write(
                        f"[live-audit-scheduler] {node_id}/{audit_id}: "
                        "falta la cuenta de restauración de terminales\n"
                    )
                    continue
                sys.stderr.write(
                    f"[live-audit-scheduler] iniciando {node_id}/{audit_id}: "
                    f"portafolio #{portfolio_id}, variante {profile.get('portfolio_type')}, "
                    f"cuenta {profile.get('source_login')}\n"
                )
                start_status, response = node_request(
                    node, "POST", f"/api/v1/live-audits/{portfolio_id}/run", payload, timeout=30
                )
                if start_status >= 400 and start_status != 409:
                    sys.stderr.write(
                        f"[live-audit-scheduler] {node_id}/{audit_id}/#{portfolio_id}: HTTP {start_status} {response}\n"
                    )

    def preferences_for(self, node_id: str) -> dict[str, Any]:
        with self.preferences_lock:
            return dict(self.preferences.get(node_id) or {})

    def update_preferences(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(LAUNCH_PREFERENCE_KEYS)
        if unknown:
            raise ValueError(f"Preferencias desconocidas: {', '.join(sorted(unknown))}")
        normalized: dict[str, Any] = {}
        if "cycles" in changes:
            normalized["cycles"] = safe_int(changes["cycles"], 1, minimum=1, maximum=100)
        if "generations" in changes:
            normalized["generations"] = safe_int(changes["generations"], 1, minimum=1, maximum=1000)
        if "variants_per_seed" in changes:
            normalized["variants_per_seed"] = safe_int(
                changes["variants_per_seed"], 10, minimum=1, maximum=10000
            )
        if "max_seeds" in changes:
            normalized["max_seeds"] = safe_int(changes["max_seeds"], 30, minimum=0, maximum=100000)
        if "generation_mode" in changes:
            mode = str(changes["generation_mode"] or "").strip().lower()
            if mode not in {"production", "discovery"}:
                raise ValueError("generation_mode debe ser production o discovery")
            normalized["generation_mode"] = mode
        if "random_seed" in changes:
            value = changes["random_seed"]
            if value is None or str(value).strip() == "":
                normalized["random_seed"] = None
            else:
                try:
                    normalized["random_seed"] = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("random_seed debe ser un entero o null") from exc
        for key in ("max_workers", "repair_max_workers", "regression_max_workers"):
            if key in changes:
                normalized[key] = safe_int(changes[key], 1, minimum=1, maximum=64)
        if "repair_attempts" in changes:
            normalized["repair_attempts"] = safe_int(changes["repair_attempts"], 1, minimum=1, maximum=20)
        for key in BOOL_PREFERENCE_KEYS:
            if key in changes:
                if not isinstance(changes[key], bool):
                    raise ValueError(f"{key} debe ser booleano")
                normalized[key] = changes[key]
        with self.preferences_lock:
            current = dict(self.preferences.get(node_id) or {})
            current.update(normalized)
            self.preferences[node_id] = current
            if self.preferences_path:
                save_json(self.preferences_path, self.preferences)
            return dict(current)

    def remember_launch_request(self, node_id: str, payload: dict[str, Any]) -> None:
        """Guarda como preferencia cada campo con el que se lanzó una generación.

        Solo aplica al arranque de generación: en reparación y prueba regresiva
        ``max_workers`` significa las terminales de esa etapa, no las de generación.
        """
        changes = {
            key: bool(payload[key]) if key in BOOL_PREFERENCE_KEYS else payload[key]
            for key in LAUNCH_PREFERENCE_KEYS
            if key in payload
        }
        if not changes:
            return
        try:
            self.update_preferences(node_id, changes)
        except (ValueError, OSError) as exc:
            # Nunca hacer fallar un lanzamiento aceptado por no poder recordarlo.
            print(f"[manager] No se pudo recordar la configuración de {node_id}: {exc}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Panel central de MT5 Autotester")
    parser.add_argument("--config", default="manager.json")
    parser.add_argument("--port", type=int, help="Sobrescribe temporalmente el puerto del archivo de configuración")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    config = dev_branch.apply_manager_config(load_json(args.config))
    config_dir = Path(args.config).expanduser().resolve().parent
    config.setdefault("manager_repo_dir", str(config_dir))
    config.setdefault("manager_restart_state_file", str(config_dir / "runtime" / "manager_restart.json"))
    config.setdefault("manager_restart_log_file", str(config_dir / "runtime" / "manager_restart.log"))
    config.setdefault(
        "preferences_file",
        str(Path(args.config).expanduser().resolve().parent / "runtime" / "launch_preferences.json"),
    )
    config.setdefault(
        "portfolio_settings_file",
        str(Path(args.config).expanduser().resolve().parent / "runtime" / "portfolio_settings.json"),
    )
    config.setdefault(
        "live_audit_settings_file",
        str(Path(args.config).expanduser().resolve().parent / "runtime" / "live_audit_settings.json"),
    )
    config.setdefault(
        "live_audit_scheduler_settings_file",
        str(Path(args.config).expanduser().resolve().parent / "runtime" / "live_audit_scheduler.json"),
    )
    host = str(config.get("host") or "127.0.0.1")
    port = safe_int(args.port if args.port is not None else config.get("port"), 8750, minimum=1, maximum=65535)
    server = ManagerServer((host, port), config)
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}"
    print(f"Manager disponible en {url}")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
