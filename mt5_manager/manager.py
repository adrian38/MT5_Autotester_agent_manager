from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .common import json_bytes, load_json, safe_int, utc_now


STATIC_DIR = Path(__file__).resolve().parent / "static"


def node_request(node: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
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
        with urllib.request.urlopen(request, timeout=float(node.get("timeout", 5))) as response:
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
                    results[node_id] = value
                except Exception as exc:
                    results[node_id] = {
                        "manager_node": {"id": node_id, "name": node.get("name") or node_id, "url": node.get("url")},
                        "offline": True, "error": str(exc), "observed_at": utc_now(),
                    }
        return [results[str(node.get("id"))] for node in self.server.nodes]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
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
        if parsed.path.startswith("/api/nodes/") and parsed.path.endswith("/runs"):
            parts = parsed.path.strip("/").split("/")
            try:
                node = self._node(urllib.parse.unquote(parts[2]))
                query = urllib.parse.parse_qs(parsed.query)
                limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=500)
                status, value = node_request(node, "GET", f"/api/v1/runs?limit={limit}")
                self._send_json(status, value)
            except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                self._send_json(502, {"error": str(exc)})
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        relative = parsed.path.lstrip("/")
        if relative in {"app.js", "styles.css"}:
            self._send_file(STATIC_DIR / relative)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["api", "nodes"] or parts[3] not in {"start", "stop", "repair"}:
            self._send_json(404, {"error": "Ruta no encontrada"})
            return
        try:
            node = self._node(urllib.parse.unquote(parts[2]))
            targets = {
                "start": "/api/v1/jobs/generation",
                "stop": "/api/v1/jobs/stop",
                "repair": "/api/v1/jobs/repair",
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
        super().__init__(address, ManagerHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Panel central de MT5 Autotester")
    parser.add_argument("--config", default="manager.json")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    config = load_json(args.config)
    host = str(config.get("host") or "127.0.0.1")
    port = safe_int(config.get("port"), 8750, minimum=1, maximum=65535)
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
