"""El aviso de que la comprobación manual de un agente ha terminado.

La comprobación la ejecuta el agente y dura horas: lo que el usuario se pierde
es el final, igual que en una generación (`portfolio_job_bell_notices.md`).
Pero aquí el trabajo **no** corre en el manager, así que no hay ningún hilo
propio al que colgarle el aviso: hay que mirar al nodo.

Esto es lo que hace la vuelta de abajo, dentro del mismo vigilante que ya lee
los nodos para la campana. Un trabajo terminado se anuncia **una sola vez**:
el nodo conserva su último estado hasta que arranca otro, así que sin memoria
de lo ya anunciado cada vuelta repetiría el mismo aviso cada cinco minutos.

La memoria es un fichero del manager y no un apunte en RAM a propósito: un
reinicio del manager mientras el agente comprueba no puede convertirse en un
aviso repetido de algo que el usuario ya leyó.
"""
from __future__ import annotations

import urllib.error
from typing import Any

from . import manager_http
from .common import load_json, save_json


MAX_REMEMBERED = 200
STATUS_LEVELS = {"completed": "info", "failed": "error", "stopped": "warning"}
STATUS_TITLES = {
    "completed": "Comprobación manual terminada",
    "failed": "Comprobación manual fallida",
    "stopped": "Comprobación manual detenida",
}
MODEL_LABELS = {"ohlc": "OHLC M1", "tick": "tick a tick"}


def notices_path(server: Any):
    return server.live_audit_settings_path.with_name("manual_check_notices.json")


def _remembered(server: Any) -> list[str]:
    try:
        stored = load_json(notices_path(server))
    except (OSError, ValueError):
        return []
    announced = stored.get("announced") if isinstance(stored, dict) else None
    return [str(value) for value in announced] if isinstance(announced, list) else []


def _remember(server: Any, seen: list[str], key: str) -> None:
    seen.append(key)
    del seen[:-MAX_REMEMBERED]
    try:
        save_json(notices_path(server), {"announced": seen})
    except OSError:
        pass


def message_for(job: dict[str, Any]) -> str:
    """Lo que el aviso cuenta: qué portafolio y con qué tipos de datos."""
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    strategies = request.get("strategies") if isinstance(request.get("strategies"), list) else []
    models = [
        MODEL_LABELS.get(str(model), str(model))
        for model in (request.get("models") or [])
    ]
    portfolio = request.get("portfolio_id") or "?"
    detail = f"Portafolio #{portfolio} · {len(strategies)} estrategia(s) nueva(s)"
    if models:
        detail += f" · {' y '.join(models)}"
    error = str(job.get("error") or "").strip()
    return f"{detail}. {error}" if error else detail


def announce(server: Any, node: dict[str, Any], job: dict[str, Any]) -> None:
    node_id = str(node.get("id") or "")
    status = str(job.get("status") or "")
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    scope = str(request.get("scope") or "full_history")
    screen = "/portfolios_monthly.html" if scope == "monthly" else "/portfolios.html"
    server.live_audit_notifications.add(
        STATUS_LEVELS.get(status, "info"),
        STATUS_TITLES.get(status, "Comprobación manual"),
        message_for(job),
        node_id=node_id,
        node_name=str(node.get("name") or node_id),
        portfolio_id=int(request.get("portfolio_id") or 0),
        kind="manual_check",
        link=f"{screen}?node={node_id}",
        link_text="Ver el portafolio",
    )


def tick(server: Any) -> None:
    """Una vuelta: el último trabajo de cada nodo, anunciado si es nuevo."""
    seen = _remembered(server)
    for node in server.nodes:
        if server.live_audit_stop.is_set():
            return
        try:
            status, value = manager_http.node_request(node, "GET", "/api/v1/status")
        except (ValueError, urllib.error.URLError, TimeoutError, OSError):
            continue
        if status >= 400 or not isinstance(value, dict):
            continue
        job = value.get("job") if isinstance(value.get("job"), dict) else {}
        if str(job.get("job_type") or "") != "manual_check":
            continue
        if str(job.get("status") or "") not in STATUS_LEVELS:
            continue
        key = f"{node.get('id')}:{job.get('job_id')}:{job.get('status')}"
        if key in seen:
            continue
        _remember(server, seen, key)
        announce(server, node, job)
