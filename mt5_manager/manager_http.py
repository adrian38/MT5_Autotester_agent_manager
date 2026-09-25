"""Como habla el manager con un nodo: peticiones, artefactos y envios largos.

Todo el reenvio pasa por aqui, asi que `manager_http.node_request` es el unico
punto que hay que interceptar para simular un nodo. Por eso los llamantes lo
invocan como atributo del modulo y no con `from ... import node_request`: asi
un doble puesto aqui los alcanza a todos.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import dev_branch
from . import guided_batches
# Se importa a si mismo a proposito: los envios largos llaman a
# `node_request` por el modulo, no por el nombre, para que un doble
# puesto en `manager_http.node_request` los intercepte tambien a ellos.
from . import manager_http as _module
from .common import json_bytes


# Detener, pausar y reanudar no son consultas: matan la etapa en curso y esperan
# hasta 8 segundos a que el proceso muera, más lo que tarde el nodo en atender la
# petición si su pipeline está descartando etapas sin candidatos pendientes. Con
# el timeout general de 5 segundos el POST expiraba siempre, la pantalla decía
# que el botón había fallado y el trabajo seguía corriendo aunque el nodo sí lo
# hubiera aplicado. Como las demás mutaciones, no se reintenta.
NODE_CONTROL_ACTIONS = frozenset({"stop", "pause", "resume"})

NODE_CONTROL_TIMEOUT = 30

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

def submit_guided_to_node(node: dict[str, Any], submission: dict[str, Any]) -> tuple[int, Any]:
    package, launch_options = guided_batches.unpack_submission(submission)
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
    status, state = _module.node_request(node, 'GET', '/api/v1/status', timeout=15)
    if status!=200 or not (state.get('capabilities') or {}).get('guided_batches_v1'):
        raise ValueError('El nodo todavía no soporta lotes guiados; actualizar su runtime')
    if launch_options is not None and not (state.get('capabilities') or {}).get('guided_launch_options_v1'):
        raise ValueError('El nodo todavía no soporta terminales/reparación en lotes guiados; actualizar su runtime')
    identity = state.get('node') or {}
    if identity.get('broker')!=broker or identity.get('account_type')!=account:
        raise ValueError('La identidad del nodo no coincide con el destino')
    if normalize_path(identity.get('project_dir'))!=normalize_path(remote_project):
        raise ValueError('El proyecto anunciado por el nodo no coincide con el configurado')
    forwarded = {'package': package, 'launch_options': launch_options} if launch_options is not None else package
    return _module.node_request(node, 'POST', '/api/v1/guided-batches', forwarded, timeout=60)

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
        status, value = _module.node_request(
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
