"""La cola persistente de trabajos del nodo.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Ver `AGENTS.md`, seccion
«El nodo NO ejecuta este repositorio».
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from . import node_job_starts
from .common import safe_int, save_json, utc_now
from .node_statuses import RESUMABLE_STATUSES


def _persist_queue(handler) -> None:
    save_json(handler.queue_path, handler.queue)


def _queue_snapshot(handler) -> dict[str, Any]:
    return {
        "count": len(handler.queue),
        "items": [
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or "generation"),
                "created_at": item.get("created_at"),
                "summary": str(item.get("summary") or ""),
                "position": index,
            }
            for index, item in enumerate(handler.queue, 1)
        ],
    }


def _busy(handler) -> bool:
    # Keep the node reserved until the watcher has recorded the process exit.
    # Un pipeline en pausa tambien reserva el nodo: si no, la cola arrancaria
    # el siguiente trabajo encima del que el usuario dejo a medias y ya no
    # habria forma de reanudarlo.
    return handler.process is not None or _is_resumable(handler) or handler.live_audits.is_running()


def _is_resumable(handler) -> bool:
    pipeline = list(handler.state.get("pipeline") or [])
    step_index = safe_int(handler.state.get("current_step_index"), -1)
    return (
        str(handler.state.get("status") or "") in RESUMABLE_STATUSES
        and 0 <= step_index < len(pipeline)
        and bool(str(handler.state.get("log_path") or "").strip())
    )


def _enqueue(handler, task_type: str, payload: dict[str, Any], summary: str) -> dict[str, Any]:
    if len(handler.queue) >= 100:
        raise RuntimeError("La cola de este nodo alcanzo el limite de 100 tareas")
    task_id = f"{int(time.time() * 1000)}_{time.time_ns() % 1_000_000:06d}"
    item = {
        "id": task_id,
        "type": task_type,
        "payload": payload,
        "created_at": utc_now(),
        "summary": summary,
    }
    handler.queue.append(item)
    _persist_queue(handler)
    return {
        **dict(handler.state),
        "queued": True,
        "queue_item": {**_queue_snapshot(handler)["items"][-1]},
        "task_queue": _queue_snapshot(handler),
    }


def _schedule_queue_drain(handler) -> None:
    timer = threading.Timer(0.05, lambda: _drain_queue(handler))
    timer.daemon = True
    timer.start()


def _drain_queue(handler) -> None:
    with handler.lock:
        if _busy(handler) or not handler.queue:
            return
        item = handler.queue.pop(0)
        _persist_queue(handler)
        try:
            payload = dict(item.get("payload") or {})
            if item.get("type") == "repair":
                node_job_starts._start_repair(handler, payload)
            elif item.get("type") == "regression":
                node_job_starts._start_regression(handler, payload)
            elif item.get("type") == "cleanup":
                node_job_starts._start_cleanup(handler)
            else:
                node_job_starts._start_generation(handler, payload)
        except Exception as exc:
            handler.state = {
                "job_id": item.get("id"), "job_type": item.get("type"),
                "status": "failed", "pid": None, "started_at": utc_now(),
                "finished_at": utc_now(), "return_code": 1, "request": item.get("payload"),
                "command": None, "log_path": None, "error": str(exc), "pipeline": [],
                "current_stage": None, "completed_stages": [], "stage_return_codes": {},
            }
            handler._persist()
            if handler.queue:
                _schedule_queue_drain(handler)


def cancel_queued(handler, task_id: str) -> dict[str, Any]:
    with handler.lock:
        task_id = str(task_id or "").strip()
        if not task_id:
            raise ValueError("Falta el id de la tarea")
        before = len(handler.queue)
        handler.queue = [item for item in handler.queue if str(item.get("id")) != task_id]
        if len(handler.queue) == before:
            raise ValueError("La tarea ya no esta en la cola")
        _persist_queue(handler)
        return {"cancelled": task_id, "task_queue": _queue_snapshot(handler)}
