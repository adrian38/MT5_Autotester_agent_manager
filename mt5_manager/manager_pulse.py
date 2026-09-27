"""El estado de todos los nodos y el pulso que refresca la pantalla.

Se pregunta a los nodos en paralelo y se guarda la ultima respuesta buena: un
nodo que no contesta sale marcado `offline` y `stale`, con sus datos anteriores,
en vez de desaparecer de la pantalla.
"""
from __future__ import annotations

import copy
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import manager_http
from .common import safe_int, utc_now
from .manager_config import LAUNCH_DEFAULT_OVERRIDE_KEYS
from .manager_http import live_log_progress
from .portfolio_scope import PORTFOLIO_SCOPES


PULSE_JOB_KEYS = (
    "job_id", "job_type", "status", "current_stage", "return_code",
    "started_at", "finished_at", "error",
)

PULSE_PORTFOLIO_JOB_KEYS = ("status", "operation", "portfolio_id", "error")

PULSE_PORTFOLIO_TASK_KEYS = ("id", "status", "operation", "portfolio_id", "error")


def all_status(handler) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(handler.server.nodes))) as executor:
        futures = {executor.submit(manager_http.node_request, node, "GET", "/api/v1/status"): node for node in handler.server.nodes}
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
                    preferences = handler.server.preferences_for(node_id)
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
                            log_status, log_value = manager_http.node_request(node, "GET", "/api/v1/logs?lines=500")
                            if log_status < 400 and isinstance(log_value, dict):
                                value["live_progress"] = live_log_progress(
                                    list(log_value.get("lines") or []),
                                    (value.get("job") or {}).get("current_stage"),
                                )
                        except (ValueError, urllib.error.URLError, TimeoutError):
                            pass
                value["last_successful_at"] = utc_now()
                with handler.server.node_status_lock:
                    handler.server.node_status_cache[node_id] = copy.deepcopy(value)
                results[node_id] = value
            except Exception as exc:
                with handler.server.node_status_lock:
                    cached = copy.deepcopy(handler.server.node_status_cache.get(node_id, {}))
                results[node_id] = {
                    **cached,
                    "manager_node": {"id": node_id, "name": node.get("name") or node_id, "url": node.get("url")},
                    "offline": True, "stale": bool(cached), "error": str(exc),
                    "last_attempt_at": utc_now(),
                }
    return [results[str(node.get("id"))] for node in handler.server.nodes]


def pulse(handler) -> dict[str, Any]:
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
    for status in handler._all_status():
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
    for node in handler.server.nodes:
        node_id = str(node.get("id"))
        if not str(node.get("portfolio_project_dir") or "").strip():
            continue
        for scope in PORTFOLIO_SCOPES:
            try:
                state = handler.server.portfolios.task_state(node_id, scope)
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
