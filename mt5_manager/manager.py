"""Punto de entrada del manager y fachada de sus modulos.

El servidor entero eran 1.585 lineas en este fichero. Ahora estan repartidas en
orden de dependencia (`manager_config`, `manager_http`, `manager_pulse`, las
rutas por area, `manager_handler`, `manager_server`) y aqui solo queda el
arranque y la reexportacion, para que los llamantes de siempre sigan
importando de `mt5_manager.manager`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import dev_branch
from . import guided_batches  # noqa: F401
from . import manager_config  # noqa: F401
from . import manager_http  # noqa: F401
from .common import load_json, safe_int
from .manager_config import (  # noqa: F401
    BOOL_PREFERENCE_KEYS,
    DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS,
    FOLDER_PICKER_LOCK,
    LAUNCH_DEFAULT_OVERRIDE_KEYS,
    LAUNCH_PREFERENCE_KEYS,
    _truthy,
    choose_directory,
    normalize_live_audit_scheduler_settings,
)
from .manager_handler import (  # noqa: F401
    NODE_ACTION_TARGETS,
    STATIC_DIR,
    STATIC_FILES,
    ManagerHandler,
)
from .manager_http import (  # noqa: F401
    NODE_CONTROL_ACTIONS,
    NODE_CONTROL_TIMEOUT,
    live_log_progress,
    node_artifact_request,
    node_request,
    submit_guided_to_node,
    submit_repair_request,
)
from .manager_pulse import (  # noqa: F401
    PULSE_JOB_KEYS,
    PULSE_PORTFOLIO_JOB_KEYS,
    PULSE_PORTFOLIO_TASK_KEYS,
)
from .manager_server import ManagerServer  # noqa: F401


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
