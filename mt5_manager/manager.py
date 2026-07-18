from __future__ import annotations

import argparse
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .common import json_bytes, load_json, safe_int, save_json, utc_now
from .portfolio_service import PortfolioCoordinator, legacy_compatible_portfolio_save_payload


STATIC_DIR = Path(__file__).resolve().parent / "static"
FOLDER_PICKER_LOCK = threading.Lock()


def choose_directory(initial_directory: str | None = None) -> str | None:
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
                title="Selecciona la carpeta para exportar los sets",
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
                    if isinstance(value, dict):
                        value["manager_node"] = {"id": node_id, "name": node.get("name") or node_id, "url": node.get("url")}
                        value["launch_preferences"] = self.server.preferences_for(node_id)
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
                    results[node_id] = value
                except Exception as exc:
                    results[node_id] = {
                        "manager_node": {"id": node_id, "name": node.get("name") or node_id, "url": node.get("url")},
                        "offline": True, "error": str(exc), "observed_at": utc_now(),
                    }
        return [results[str(node.get("id"))] for node in self.server.nodes]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if parsed.path == "/api/nodes":
            self._send_json(200, {"nodes": self._all_status(), "observed_at": utc_now()})
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
                limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=500)
                status, value = node_request(node, "GET", f"/api/v1/runs?limit={limit}")
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
        if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-manager":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                query = urllib.parse.parse_qs(parsed.query)
                scope = "monthly" if query.get("scope", ["full_history"])[0] == "monthly" else "full_history"
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
                scope = "monthly" if query.get("scope", ["full_history"])[0] == "monthly" else "full_history"
                self._send_json(200, self.server.portfolios.task_state(node_id, scope))
            except (KeyError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if len(parts) in {4, 5} and parts[:2] == ["api", "nodes"] and parts[3] == "portfolios":
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                scope = "monthly" if query.get("scope", ["full_history"])[0] == "monthly" else "full_history"
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
            "portfolios.html", "portfolios.js",
            "portfolios_monthly.html", "portfolios_monthly.js",
        }:
            self._send_file(STATIC_DIR / relative)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
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
        if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-manager":
            try:
                node_id = urllib.parse.unquote(parts[2])
                node = self._node(node_id)
                body = self._body()
                scope = "monthly" if str(body.pop("scope", "full_history")) == "monthly" else "full_history"
                action = parts[4]
                if action == "settings":
                    self._send_json(200, {"settings": self.server.portfolios.update_settings(node_id, scope, body)})
                elif action == "generate":
                    self._send_json(202, {"job": self.server.portfolios.start(node_id, scope, body)})
                elif action == "save":
                    save_payload = self.server.portfolios.prepare_save(
                        node_id, scope, str(body.get("proposal_key") or "")
                    )
                    status, value = node_request(
                        node, "POST", "/api/v1/portfolios/save", save_payload, timeout=120
                    )
                    error_text = str(value.get("error") if isinstance(value, dict) else value or "")
                    if status >= 400 and "unexpected keyword argument" in error_text:
                        status, value = node_request(
                            node,
                            "POST",
                            "/api/v1/portfolios/save",
                            legacy_compatible_portfolio_save_payload(save_payload),
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
                    if portfolio_id <= 0 or request_id != str(save_payload["request_id"]):
                        raise ValueError("El nodo no confirmó correctamente el guardado")
                    self.server.portfolios.confirm_save(node_id, scope, request_id, portfolio_id)
                    self._send_json(201, {"portfolio_id": portfolio_id})
                elif action in {"reoptimize", "complete"}:
                    portfolio_id = safe_int(body.pop("portfolio_id", 0), 0, minimum=1)
                    self._send_json(202, {"job": self.server.portfolios.start_saved_operation(
                        node_id, scope, portfolio_id, action, body or None
                    )})
                elif action == "exclude":
                    if body.get("set_paths") is not None:
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
                        if not value.get("deleted") or safe_int(value.get("portfolio_id"), 0) != portfolio_id:
                            raise ValueError("El nodo no confirmó correctamente la exclusión múltiple")
                        self.server.portfolios.invalidate_after_exclusion(node_id)
                        self._send_json(201, value)
                    else:
                        quarantine_result = self.server.portfolios.exclude(node_id, scope, body)
                        self._send_json(201, {"quarantine_id": quarantine_result})
                elif action == "release":
                    self.server.portfolios.release(node_id, str(body.get("quarantine_id") or ""))
                    self._send_json(200, {"released": True})
                elif action == "undo":
                    version = self.server.portfolios.undo(node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1))
                    self._send_json(200, {"restored_version": version})
                elif action == "delete":
                    task = self.server.portfolios.delete(
                        node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1)
                    )
                    self._send_json(202, {"task": task})
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
            "start", "stop", "repair", "regression", "universe",
        }:
            self._send_json(404, {"error": "Ruta no encontrada"})
            return
        try:
            node = self._node(urllib.parse.unquote(parts[2]))
            targets = {
                "start": "/api/v1/jobs/generation",
                "stop": "/api/v1/jobs/stop",
                "repair": "/api/v1/jobs/repair",
                "regression": "/api/v1/jobs/regression",
                "universe": "/api/v1/universe/symbols",
            }
            target = targets[parts[3]]
            status, value = node_request(node, "POST", target, self._body())
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
        portfolio_settings_file = str(config.get("portfolio_settings_file") or "").strip()
        portfolio_settings_path = (
            Path(portfolio_settings_file).expanduser().resolve()
            if portfolio_settings_file
            else Path.cwd() / "runtime" / "portfolio_settings.json"
        )
        self.portfolios = PortfolioCoordinator(nodes, portfolio_settings_path)
        super().__init__(address, ManagerHandler)

    def preferences_for(self, node_id: str) -> dict[str, Any]:
        with self.preferences_lock:
            return dict(self.preferences.get(node_id) or {})

    def update_preferences(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "cycles", "generation_mode", "max_workers", "repair_attempts", "repair_after_generation",
            "run_robustness", "run_final_tick", "run_final_tick_6m", "run_regression",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Preferencias desconocidas: {', '.join(sorted(unknown))}")
        normalized: dict[str, Any] = {}
        if "cycles" in changes:
            normalized["cycles"] = safe_int(changes["cycles"], 1, minimum=1, maximum=100)
        if "generation_mode" in changes:
            mode = str(changes["generation_mode"] or "").strip().lower()
            if mode not in {"production", "discovery"}:
                raise ValueError("generation_mode debe ser production o discovery")
            normalized["generation_mode"] = mode
        if "max_workers" in changes:
            normalized["max_workers"] = safe_int(changes["max_workers"], 1, minimum=1, maximum=64)
        if "repair_attempts" in changes:
            normalized["repair_attempts"] = safe_int(changes["repair_attempts"], 1, minimum=1, maximum=20)
        for key in ("run_robustness", "run_final_tick", "run_final_tick_6m", "run_regression", "repair_after_generation"):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Panel central de MT5 Autotester")
    parser.add_argument("--config", default="manager.json")
    parser.add_argument("--port", type=int, help="Sobrescribe temporalmente el puerto del archivo de configuración")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    config = load_json(args.config)
    config.setdefault(
        "preferences_file",
        str(Path(args.config).expanduser().resolve().parent / "runtime" / "launch_preferences.json"),
    )
    config.setdefault(
        "portfolio_settings_file",
        str(Path(args.config).expanduser().resolve().parent / "runtime" / "portfolio_settings.json"),
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
