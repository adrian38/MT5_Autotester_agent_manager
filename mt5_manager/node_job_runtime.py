"""El curso de un trabajo ya lanzado: pasos, vigilancia y parada.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Ver `AGENTS.md`, seccion
«El nodo NO ejecuta este repositorio».

Funciones de modulo que reciben el controlador.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import guided_batches
from . import node_commands
from . import node_snapshots
from .common import safe_int, utc_now
from .node_settings import (
    CLEANUP_STAGES,
    build_historical_cleanup_command,
    cleanup_after_run_enabled,
    memory_path,
    read_settings,
)
from .node_snapshots import database_snapshot


def _append_skip_log(handler, log_path: Path, label: str) -> None:
    with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"[manager-node] Etapa omitida: {label}; no hay candidatos pendientes.\n")


def _honour_stop_request(handler, step_index: int, log_path: Path) -> bool:
    """Cierra el pipeline porque el usuario lo pidio, no porque fallara.

    Devuelve True para que quien llama no lo de por terminado con
    `_complete`, que lo marcaria como completado o fallido.
    """
    paused = handler.pause_requested and not handler.stop_requested
    handler.pause_requested = False
    handler.stop_requested = False
    handler.state["status"] = "paused" if paused else "stopped"
    # En pausa se conserva la posicion para reanudar en esta misma etapa; al
    # detener no queda nada que retomar.
    handler.state["current_step_index"] = step_index if paused else None
    handler.state["pid"] = None
    handler.state["return_code"] = None
    if paused:
        handler.state["paused_at"] = utc_now()
    else:
        handler.state["finished_at"] = utc_now()
    handler.process = None
    with contextlib.suppress(OSError):
        with log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(
                "[manager-node] "
                + ("Pipeline pausado" if paused else "Pipeline detenido")
                + " a peticion del usuario.\n"
            )
    handler._persist()
    if not paused and handler.queue:
        handler._schedule_queue_drain()
    return True


def _launch_next_runnable(handler, step_index: int, log_path: Path, *, first: bool = False) -> bool:
    pipeline = list(handler.state.get("pipeline") or [])
    request = dict(handler.state.get("request") or {})
    while step_index < len(pipeline):
        # Descartar una etapa vacia cuesta una consulta a SQLite y este bucle
        # retiene `handler.lock`: en una reparacion de cien runs encadena miles de
        # descartes y `stop()`/`pause()` se quedaban esperando el bloqueo hasta
        # que expiraba el POST del manager. Por eso la peticion se atiende aqui,
        # entre etapa y etapa, en vez de exigir el bloqueo al que la pide.
        if handler.stop_requested or handler.pause_requested:
            return _honour_stop_request(handler, step_index, log_path)
        step = pipeline[step_index]
        stage = str(step["action"])
        label = handler._step_label(step)
        step_request = dict(request)
        if step.get("max_workers") is not None:
            step_request["max_workers"] = step["max_workers"]
        if stage == "generation":
            command, cwd = node_commands.build_generation_command(handler.config, step_request)
        elif stage in CLEANUP_STAGES:
            command, cwd = build_historical_cleanup_command(handler.config, stage)
        else:
            run_id = safe_int(step.get("run_id"), 0, minimum=0)
            if run_id <= 0:
                raise ValueError("No se encontro el run para continuar el pipeline")
            pending_count = node_snapshots.pipeline_stage_pending_count(handler.config, step_request, stage, run_id)
            handler.state.setdefault("stage_pending_counts", {})[label] = pending_count
            if pending_count == 0:
                handler.state.setdefault("skipped_stages", []).append(label)
                handler.state.setdefault("stage_return_codes", {})[label] = None
                _append_skip_log(handler, log_path, label)
                handler._persist()
                step_index += 1
                first = False
                continue
            command, cwd = node_commands.build_pipeline_stage_command(handler.config, step_request, stage, run_id)
        handler.state.setdefault("commands", {})[label] = command
        _launch_step(handler, step_index, command, cwd, log_path, first=first)
        return True
    return False


def _complete(handler, return_code: int) -> None:
    # Una peticion de parada que llego cuando ya no quedaba nada que parar no
    # puede sobrevivir al trabajo: mataria el siguiente nada mas lanzarlo.
    handler.stop_requested = False
    handler.pause_requested = False
    handler.state["return_code"] = return_code
    handler.state["finished_at"] = utc_now()
    handler.state["status"] = "completed" if return_code == 0 else "failed"
    handler.guided_completed()
    handler.state["pid"] = None
    handler.process = None
    handler._persist()
    if handler.queue:
        handler._schedule_queue_drain()


def _launch_step(handler, step_index: int, command: list[str], cwd: Path, log_path: Path, *, first: bool = False) -> None:
    step = list(handler.state.get("pipeline") or [])[step_index]
    stage = str(step["action"])
    mode = "w" if first else "a"
    handler.log_handle = log_path.open(mode, encoding="utf-8", errors="replace", buffering=1)
    if not first:
        handler.log_handle.write(f"\n[manager-node] Iniciando etapa: {stage}\n")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command, cwd=cwd, stdout=handler.log_handle, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
    )
    handler.process = process
    handler.guided_stage_started()
    handler.state["pid"] = process.pid
    # Sin esto la posicion del pipeline solo vivia en los argumentos del hilo
    # vigilante, asi que un cierre del agente la perdia y no habia por donde
    # retomar.
    handler.state["current_step_index"] = step_index
    handler.state["status"] = "running"
    handler.state["current_stage"] = stage
    handler.state["current_cycle"] = step.get("cycle")
    handler.state["current_run_id"] = step.get("run_id")
    handler.state["current_attempt"] = step.get("attempt")
    handler.state["current_phase"] = step.get("phase")
    handler.state["command"] = command
    handler._persist()
    threading.Thread(target=_watch, args=(handler, process, step_index), daemon=True).start()


def _mark_paused(handler) -> None:
    """La etapa se corto a peticion del usuario, no fallo.

    Se conserva ``current_step_index`` para relanzar esta misma etapa: al
    volver, ``pipeline_stage_pending_count`` recalcula lo que quede pendiente.
    """
    handler.pause_requested = False
    handler.state["status"] = "paused"
    handler.state["pid"] = None
    handler.state["return_code"] = None
    handler.state["paused_at"] = utc_now()
    handler.process = None
    handler._persist()


def _bind_generated_run(handler, pipeline: list[dict[str, Any]], cycle: Any) -> None:
    """Fija en las etapas del ciclo el run que acaba de generar el agente.

    Con un lote preparado el run tiene que venir del propio lote: quedarse con
    el ultimo de la memoria ataria el pipeline al run de otro.
    """
    settings_path = Path(str(handler.config.get("settings_file") or "ui_settings.ini"))
    project = Path(str(handler.config["project_dir"])).expanduser().resolve()
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    cfg = read_settings(settings_path)
    snapshot = database_snapshot(memory_path(handler.config, cfg))
    prepared_id = (handler.state.get("request") or {}).get("guided_batch_id")
    prepared_run = guided_batches.read_run(project, prepared_id) if prepared_id else None
    if prepared_id and not prepared_run:
        raise ValueError("El lote preparado no publicó su run exacto")
    generated_run = safe_int((prepared_run or snapshot.get("latest_run") or {}).get("run_id" if prepared_id else "id"), 0, minimum=0)
    if generated_run <= 0:
        raise ValueError("No se encontro el run generado")
    handler.state.setdefault("cycle_run_ids", {})[str(cycle)] = generated_run
    for pending_step in pipeline:
        if pending_step.get("cycle") == cycle:
            pending_step["run_id"] = generated_run
    handler.state["pipeline"] = pipeline


def _record_step_outcome(handler, step: dict[str, Any], return_code: int) -> None:
    label = handler._step_label(step)
    handler.state.setdefault("stage_return_codes", {})[label] = return_code
    if return_code == 0:
        handler.state.setdefault("completed_stages", []).append(label)
    elif str(step["action"]) in CLEANUP_STAGES:
        handler.state["cleanup_failed"] = True


def _watch(handler, process: subprocess.Popen[str], step_index: int) -> None:
    return_code = process.wait()
    with handler.lock:
        if process is not handler.process:
            return
        if handler.log_handle:
            handler.log_handle.close()
            handler.log_handle = None
        handler.guided_stage_finished(str((handler.state.get("pipeline") or [])[step_index]["action"]))
        if handler.pause_requested:
            _mark_paused(handler)
            return
        if handler.stop_requested:
            # Detener no es un fallo de la etapa: si se dejara caer al camino
            # normal, el trabajo acabaria como «failed» cuando el usuario
            # cortase una etapa en marcha y como «stopped» cuando cortase
            # entre etapas. Mismo boton, mismo resultado.
            _honour_stop_request(handler, step_index, Path(str(handler.state["log_path"])))
            return
        pipeline = list(handler.state.get("pipeline") or [])
        step = pipeline[step_index]
        stage = str(step["action"])
        cycle = step.get("cycle")
        _record_step_outcome(handler, step, return_code)
        has_downstream_for_cycle = any(
            pending.get("cycle") == cycle and pending.get("action") != "generation"
            for pending in pipeline[step_index + 1:]
        )
        if return_code == 0 and stage == "generation" and has_downstream_for_cycle:
            try:
                _bind_generated_run(handler, pipeline, cycle)
            except Exception as exc:
                handler.state["error"] = str(exc)
                return_code = 1
        next_index = step_index + 1
        next_is_cleanup = (
            next_index < len(pipeline)
            and str(pipeline[next_index].get("action")) in CLEANUP_STAGES
        )
        cleanup_failed = bool(handler.state.get("cleanup_failed"))
        continue_pipeline = return_code == 0 and not cleanup_failed
        if stage in CLEANUP_STAGES and next_is_cleanup:
            continue_pipeline = True
        if continue_pipeline and next_index < len(pipeline):
            try:
                if _launch_next_runnable(handler, next_index, Path(str(handler.state["log_path"]))):
                    return
            except Exception as exc:
                handler.state["error"] = str(exc)
                return_code = 1
        if cleanup_failed:
            return_code = 1
        _complete(handler, return_code)


def _terminate_current(handler, process: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGTERM)
        process.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        process.terminate()
