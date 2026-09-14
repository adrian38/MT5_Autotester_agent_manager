from __future__ import annotations

import copy
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from .common import load_json, save_json


CONTAINER_PROJECTS = {
    "ICTRADING": "/data/ic",
    "AXI": "/data/axi",
    "ROBOFOREX": "/data/roboforex",
}

PROXY_BROKERS_ENV = "MT5_MANAGER_PROXY_BROKERS"


def _container_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return text
    port = f":{parsed.port}" if parsed.port else ""
    return urllib.parse.urlunsplit(
        (parsed.scheme, f"host.docker.internal{port}", parsed.path, parsed.query, parsed.fragment)
    )


def _container_project_path(value: Any, project: str) -> str:
    text = str(value or "").replace("\\", "/")
    lowered = text.lower()
    marker = "/outputs/"
    if marker in lowered:
        return project + text[lowered.index(marker):]
    return text


def docker_config(source: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(source)
    config["host"] = "0.0.0.0"
    config["export_mode"] = "download"
    config["preferences_file"] = "/app/runtime/launch_preferences.json"
    config["portfolio_settings_file"] = "/app/runtime/portfolio_settings.json"
    proxy_brokers = {
        item.strip().upper()
        for item in os.environ.get(PROXY_BROKERS_ENV, "").split(",")
        if item.strip()
    }
    for node in config.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node["url"] = _container_url(node.get("url"))
        broker = str(node.get("portfolio_broker") or "").strip().upper()
        if broker in proxy_brokers:
            # Docker Desktop cannot bind a Windows mapped network drive.  When
            # the agent only exists on such a drive, leave the project unset so
            # portfolio reads and writes use the node HTTP API instead.
            node.pop("portfolio_project_dir", None)
            node.pop("portfolio_memory_path", None)
            node.pop("portfolio_memory_paths", None)
            continue
        project = CONTAINER_PROJECTS.get(broker)
        if not project:
            continue
        # Keep the node's Windows identity separate from the manager's bind mount.
        node.setdefault("node_project_dir", node.get("portfolio_project_dir"))
        node["portfolio_project_dir"] = project
        if node.get("portfolio_memory_path"):
            node["portfolio_memory_path"] = _container_project_path(
                node["portfolio_memory_path"], project
            )
        for item in node.get("portfolio_memory_paths") or []:
            if isinstance(item, dict) and item.get("path"):
                item["path"] = _container_project_path(item["path"], project)
    return config


def main() -> int:
    source_path = Path(os.environ.get("MT5_MANAGER_CONFIG", "/app/config/manager.json"))
    target_path = Path("/app/runtime/manager.docker.json")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(target_path, docker_config(load_json(source_path)))
    os.execv(
        sys.executable,
        [sys.executable, "-m", "mt5_manager.manager", "--config", str(target_path), "--no-browser"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
