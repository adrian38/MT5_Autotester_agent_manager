"""Como se normaliza y se lanza cada tipo de trabajo del nodo.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Ver `AGENTS.md`, seccion
«El nodo NO ejecuta este repositorio».

Funciones de modulo que reciben el controlador, como las rutas del manager: la
clase se quedaba en mil lineas y estas diez son un bloque con vida propia.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from . import dev_branch
from . import node_job_runtime
from .common import safe_int, utc_now
from . import node_commands
from .node_settings import (
    CLEANUP_STAGES,
    build_historical_cleanup_command,
    cleanup_after_run_enabled,
    historical_cleanup_scripts,
    memory_path,
    read_settings,
    setting,
)


def _normalize_generation(handler, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    random_seed = payload.get("random_seed")
    if random_seed is None or str(random_seed).strip() == "":
        payload["random_seed"] = None
    else:
        try:
            payload["random_seed"] = int(random_seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("random_seed debe ser un entero o null") from exc
    cycles = safe_int(payload.get("cycles"), 1, minimum=1, maximum=100)
    payload["cycles"] = cycles
    run_robustness = bool(payload.get("run_robustness", False))
    run_final_tick = bool(payload.get("run_final_tick", False))
    run_final_tick_6m = bool(payload.get("run_final_tick_6m", False))
    if run_final_tick_6m:
        run_final_tick = True
        run_robustness = True
    elif run_final_tick:
        run_robustness = True
    payload["run_robustness"] = run_robustness
    payload["run_final_tick"] = run_final_tick
    payload["run_final_tick_6m"] = run_final_tick_6m
    repair_after_generation = bool(payload.get("repair_after_generation", False))
    repair_max_workers = safe_int(
        payload.get("repair_max_workers"),
        safe_int(payload.get("max_workers"), 1, minimum=1, maximum=64),
        minimum=1,
        maximum=64,
    )
    repair_attempts = safe_int(payload.get("repair_attempts"), 1, minimum=1, maximum=20)
    payload["repair_after_generation"] = repair_after_generation
    payload["repair_max_workers"] = repair_max_workers
    payload["repair_phase2_max_workers"] = safe_int(
        payload.get("repair_phase2_max_workers"), 1, minimum=1, maximum=64
    )
    payload["repair_attempts"] = repair_attempts
    payload["cleanup_after_run"] = cleanup_after_run_enabled(handler.config, payload)
    return payload


def _generation_pipeline(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Las etapas de cada ciclo, en el orden en que se van a ejecutar."""
    repair_phase_workers = (
        payload["repair_max_workers"], payload["repair_phase2_max_workers"],
    )
    pipeline: list[dict[str, Any]] = []
    for cycle in range(1, payload["cycles"] + 1):
        pipeline.append({"action": "generation", "cycle": cycle, "run_id": None})
        # Complete the ordinary run once with its own worker limit. Repair is
        # a later pass over the finished run; it must never replace or split
        # these stages.
        for flag, action in (
            ("run_robustness", "robustness"),
            ("run_final_tick", "final_tick"),
            ("run_final_tick_6m", "final_tick_6m"),
        ):
            if payload[flag]:
                pipeline.append({"action": action, "cycle": cycle, "run_id": None})
        if payload["repair_after_generation"]:
            pipeline.extend(_repair_pass(payload, cycle, repair_phase_workers))
        if payload["cleanup_after_run"]:
            pipeline.extend(
                {"action": action, "cycle": cycle, "run_id": None}
                for action in CLEANUP_STAGES
            )
    return pipeline


def _repair_pass(
    payload: dict[str, Any], cycle: int, repair_phase_workers: tuple[Any, Any],
) -> list[dict[str, Any]]:
    """Los intentos de reparacion de un ciclo, cada uno en sus dos fases.

    Todas las etapas son «pending-only», asi que la segunda fase solo trabaja
    lo que la primera dejo pendiente y se omite sin lanzar proceso cuando no
    queda nada.
    """
    repair_actions = ["result"]
    if payload["run_robustness"]:
        repair_actions.append("robustness")
    if payload["run_final_tick"]:
        repair_actions.extend(["final_tick", "final_tick_quality"])
    if payload["run_final_tick_6m"]:
        repair_actions.extend(["final_tick_6m", "final_tick_6m_quality"])
    return [
        {
            "action": action, "cycle": cycle, "run_id": None,
            "attempt": attempt, "phase": phase, "max_workers": workers,
        }
        for attempt in range(1, payload["repair_attempts"] + 1)
        for phase, workers in enumerate(repair_phase_workers, start=1)
        for action in repair_actions
    ]


def _start_generation(handler, payload: dict[str, Any]) -> dict[str, Any]:
    payload = _normalize_generation(handler, payload)
    pipeline = _generation_pipeline(payload)
    command, cwd = node_commands.build_generation_command(handler.config, payload)
    job_id = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
    log_path = handler.runtime_dir / f"generation_{job_id}.log"
    handler.state = {
        "job_id": job_id, "status": "running", "pid": None,
        "started_at": utc_now(), "finished_at": None, "return_code": None,
        "request": payload, "command": command, "log_path": str(log_path), "error": None,
        "job_type": "generation", "pipeline": pipeline, "current_stage": "generation",
        "current_cycle": 1, "current_run_id": None, "completed_stages": [],
        "stage_return_codes": {}, "commands": {"cycle_1_generation": command},
        "cycle_run_ids": {}, "skipped_stages": [], "stage_pending_counts": {},
        "cleanup_failed": False,
    }
    node_job_runtime._launch_step(handler, 0, command, cwd, log_path, first=True)
    return {**dict(handler.state), "queued": False, "task_queue": handler._queue_snapshot()}


def _start_cleanup(handler) -> dict[str, Any]:
    historical_cleanup_scripts(handler.config)
    pipeline = [{"action": action, "cycle": None, "run_id": None} for action in CLEANUP_STAGES]
    job_id = "cleanup_" + time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
    log_path = handler.runtime_dir / f"{job_id}.log"
    handler.state = {
        "job_id": job_id, "job_type": "cleanup", "status": "running", "pid": None,
        "started_at": utc_now(), "finished_at": None, "return_code": None,
        "request": {}, "command": None, "log_path": str(log_path), "error": None,
        "pipeline": pipeline, "current_stage": "cleanup_tester", "current_cycle": None,
        "current_run_id": None, "current_attempt": None, "current_phase": None,
        "completed_stages": [],
        "skipped_stages": [], "stage_return_codes": {}, "stage_pending_counts": {},
        "commands": {}, "cycle_run_ids": {}, "cleanup_failed": False,
    }
    node_job_runtime._launch_next_runnable(handler, 0, log_path, first=True)
    return {**dict(handler.state), "queued": False, "task_queue": handler._queue_snapshot()}


def _normalize_repair(handler, payload: dict[str, Any]) -> dict[str, Any]:
    requested = payload.get("run_ids")
    if not isinstance(requested, list):
        raise ValueError("run_ids debe ser una lista")
    run_ids = list(dict.fromkeys(safe_int(value, 0, minimum=0) for value in requested))
    run_ids = [value for value in run_ids if value > 0]
    if not run_ids:
        raise ValueError("Selecciona al menos un run terminado")
    payload = dict(payload)
    payload["run_ids"] = run_ids
    payload["max_workers"] = safe_int(
        payload.get("max_workers"), 1, minimum=1, maximum=64
    )
    payload["execute_backtests"] = True
    # `max_workers` son los terminales de la primera fase; la segunda tiene los
    # suyos y por omision es secuencial, que es el sentido de partir el intento.
    payload["repair_phase2_max_workers"] = safe_int(
        payload.get("repair_phase2_max_workers"), 1, minimum=1, maximum=64
    )
    repair_attempts = safe_int(payload.get("repair_attempts"), 1, minimum=1, maximum=20)
    payload["repair_attempts"] = repair_attempts
    retry_low_quality = bool(payload.get("retry_low_quality", True))
    payload["retry_low_quality"] = retry_low_quality
    payload["cleanup_after_run"] = cleanup_after_run_enabled(handler.config, payload)
    return payload


def _start_repair(handler, payload: dict[str, Any]) -> dict[str, Any]:
    payload = _normalize_repair(handler, payload)
    run_ids = payload["run_ids"]
    repair_attempts = payload["repair_attempts"]
    retry_low_quality = payload["retry_low_quality"]
    actions = ["result", "robustness", "final_tick"]
    if retry_low_quality:
        actions.append("final_tick_quality")
    actions.append("final_tick_6m")
    if retry_low_quality:
        actions.append("final_tick_6m_quality")
    # El reintento pertenece a un run seleccionado: se termina con ese run
    # antes de pasar al siguiente. Dentro de cada reintento hay dos fases,
    # distinguidas solo por cuantos terminales usan a la vez: la fase 1 recorre
    # todas sus etapas en paralelo y la fase 2 vuelve a recorrerlas sobre lo
    # que la primera dejo pendiente.
    phase_workers = (payload["max_workers"], payload["repair_phase2_max_workers"])
    pipeline: list[dict[str, Any]] = []
    for run_id in run_ids:
        pipeline.extend(
            {
                "action": action, "cycle": None, "run_id": run_id,
                "attempt": attempt, "phase": phase, "max_workers": workers,
            }
            for attempt in range(1, repair_attempts + 1)
            for phase, workers in enumerate(phase_workers, start=1)
            for action in actions
        )
        if payload["cleanup_after_run"]:
            pipeline.extend(
                {"action": action, "cycle": None, "run_id": run_id}
                for action in CLEANUP_STAGES
            )
    job_id = "repair_" + time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
    log_path = handler.runtime_dir / f"{job_id}.log"
    handler.state = {
        "job_id": job_id, "job_type": "repair", "status": "running", "pid": None,
        "started_at": utc_now(), "finished_at": None, "return_code": None,
        "request": payload, "command": None, "log_path": str(log_path), "error": None,
        "pipeline": pipeline, "current_stage": None, "current_cycle": None,
        "current_run_id": None, "current_attempt": None, "current_phase": None,
        "completed_stages": [], "skipped_stages": [],
        "stage_return_codes": {}, "stage_pending_counts": {}, "commands": {}, "cycle_run_ids": {},
        "cleanup_failed": False,
    }
    try:
        launched = node_job_runtime._launch_next_runnable(handler, 0, log_path, first=True)
    except Exception as exc:
        handler.state["error"] = str(exc)
        handler.state["return_code"] = 1
        handler.state["finished_at"] = utc_now()
        handler.state["status"] = "failed"
        handler._persist()
        raise
    if not launched:
        node_job_runtime._complete(handler, 0)
    return {**dict(handler.state), "queued": False, "task_queue": handler._queue_snapshot()}


def _normalize_regression(handler, payload: dict[str, Any]) -> dict[str, Any]:
    requested = payload.get("run_ids")
    if not isinstance(requested, list):
        raise ValueError("run_ids debe ser una lista")
    run_ids = list(dict.fromkeys(safe_int(value, 0, minimum=0) for value in requested))
    run_ids = [value for value in run_ids if value > 0]
    if not run_ids:
        raise ValueError("Selecciona al menos un run terminado")
    payload = dict(payload)
    payload["run_ids"] = run_ids
    payload["max_workers"] = safe_int(
        payload.get("max_workers"), 1, minimum=1, maximum=64
    )
    payload["execute_backtests"] = True
    payload["cleanup_after_run"] = cleanup_after_run_enabled(handler.config, payload)
    return payload


def _start_regression(handler, payload: dict[str, Any]) -> dict[str, Any]:
    payload = _normalize_regression(handler, payload)
    pipeline: list[dict[str, Any]] = []
    for run_id in payload["run_ids"]:
        pipeline.append({
            "action": "regression", "cycle": None, "run_id": run_id, "attempt": 1,
        })
        if payload["cleanup_after_run"]:
            pipeline.extend(
                {"action": action, "cycle": None, "run_id": run_id}
                for action in CLEANUP_STAGES
            )
    job_id = (
        "regression_" + time.strftime("%Y%m%d_%H%M%S")
        + f"_{time.time_ns() % 1_000_000:06d}"
    )
    log_path = handler.runtime_dir / f"{job_id}.log"
    handler.state = {
        "job_id": job_id, "job_type": "regression", "status": "running", "pid": None,
        "started_at": utc_now(), "finished_at": None, "return_code": None,
        "request": payload, "command": None, "log_path": str(log_path), "error": None,
        "pipeline": pipeline, "current_stage": None, "current_cycle": None,
        "current_run_id": None, "current_attempt": None, "current_phase": None,
        "completed_stages": [], "skipped_stages": [],
        "stage_return_codes": {}, "stage_pending_counts": {}, "commands": {},
        "cycle_run_ids": {}, "cleanup_failed": False,
    }
    try:
        launched = node_job_runtime._launch_next_runnable(handler, 0, log_path, first=True)
    except Exception as exc:
        handler.state["error"] = str(exc)
        handler.state["return_code"] = 1
        handler.state["finished_at"] = utc_now()
        handler.state["status"] = "failed"
        handler._persist()
        raise
    if not launched:
        node_job_runtime._complete(handler, 0)
    return {**dict(handler.state), "queued": False, "task_queue": handler._queue_snapshot()}
