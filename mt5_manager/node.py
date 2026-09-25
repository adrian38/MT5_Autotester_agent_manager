"""Nodo remoto del manager. ATENCIÓN: NO es el nodo que corre en los brokers.

Los equipos broker ejecutan una copia bifurcada y renombrada en
`manager_node_runtime/` del proyecto del agente, embebida en `app_ui.py` vía
`manager_node_lifecycle.py`. Este módulo y `run_node.bat` solo sirven para
ejecutar un nodo desde este repositorio.

Consecuencia: cambiar aquí una regla de comportamiento del nodo (guardado,
exclusión, escritura en la memoria UBS) **no tiene ningún efecto** sobre los
agentes. La copia del agente reimplementa esas reglas en
`manager_node_runtime/portfolio_save.py` con otros nombres de función, así que no
la encuentra ni el grafo ni una búsqueda por símbolo: hay que buscarla por el
texto del mensaje al usuario. Ver `ai_context/node_runtime_is_forked_per_agent.md`
y `tests/test_node_runtime_fork_parity.py`, que falla si las copias divergen.
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import hmac
import json
import mimetypes
import os
import platform
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import candidate_verdict, dev_branch
from . import node_commands
from . import node_job_runtime
from . import node_job_starts
# Reexportados: el nodo se parte en modulos pero sus llamantes, sus pruebas y
# la guarda de paridad con la copia de cada agente siguen mirando aqui.
from .node_settings import (  # noqa: F401
    CLEANUP_STAGES,
    SCORE_OPTIONS,
    VALUE_OPTIONS,
    _load_universe_rows,
    _universe_paths,
    build_historical_cleanup_command,
    cleanup_after_run_enabled,
    historical_cleanup_scripts,
    memory_path,
    read_settings,
    setting,
    setting_bool,
)
from .node_commands import (  # noqa: F401
    build_generation_command,
    build_pipeline_stage_command,
)
from .node_snapshots import (  # noqa: F401
    _table_exists,
    completed_runs_snapshot,
    database_snapshot,
    pipeline_stage_pending_count,
)
from .guided_controller import GuidedControllerMixin
from . import guided_batches

from .common import json_bytes, load_json, safe_int, save_json, utc_now
from .live_audit_engine import LiveAuditController
from .portfolio_service import PortfolioSource, normalize_portfolio_alias, save_portfolio_payload
from .portfolio_scope import normalize_portfolio_scope


#: Estados desde los que un pipeline puede continuar donde lo dejo. ``failed``
#: tambien es retomable cuando la etapa relanzada fallo antes de avanzar el
#: pipeline; ``_is_resumable`` exige que conserve posicion y log validos.
RESUMABLE_STATUSES = frozenset({"paused", "interrupted", "failed"})
# Estados en los que el pipeline sigue avanzando aunque no haya proceso vivo: se
# esta descartando etapas sin pendientes entre una y la siguiente.
ACTIVE_STATUSES = frozenset({"running", "stopping", "pausing"})
# Lo que detener o pausar esperan por el bloqueo antes de limitarse a dejar la
# peticion puesta. El bucle del pipeline la atiende entre etapa y etapa.
CONTROL_LOCK_TIMEOUT = 3


class JobController(GuidedControllerMixin):
    def submit_guided(self, package):
        dev_branch.assert_writable(self.config['project_dir'], 'Lote guiado del nodo')
        return super().submit_guided(package)

    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.runtime_dir = config_path.parent / "runtime" / str(config.get("node_id") or "node")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.queue_path = self.runtime_dir / "queue.json"
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.queue: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {
            "job_id": None, "status": "idle", "pid": None, "started_at": None,
            "finished_at": None, "return_code": None, "request": None, "command": None,
            "log_path": None, "error": None, "pipeline": [], "current_stage": None,
            "completed_stages": [], "stage_return_codes": {}, "current_step_index": None,
        }
        self.pause_requested = False
        # Se leen y escriben FUERA de `self.lock` a proposito: durante una
        # reparacion masiva el bloqueo se queda retenido minutos enteros en
        # `_launch_next_runnable` descartando etapas sin pendientes, y detener o
        # pausar no puede depender de conseguirlo.
        self.stop_requested = False
        if self.state_path.is_file():
            try:
                old = load_json(self.state_path)
                self.state.update(old)
                if self.state.get("status") in {"running", "stopping"}:
                    # El proceso murio con el agente. Si sabemos en que paso iba,
                    # queda como interrumpido y se puede reanudar; si no, no hay
                    # nada que retomar y se deja como antes.
                    resumable = (
                        self.state.get("current_step_index") is not None
                        and bool(self.state.get("pipeline"))
                    )
                    self.state["status"] = "interrupted" if resumable else "unknown_after_restart"
                    self.state["pid"] = None
            except ValueError:
                pass
        if self.queue_path.is_file():
            try:
                stored_queue = load_json(self.queue_path)
                if isinstance(stored_queue, list):
                    self.queue = [dict(item) for item in stored_queue if isinstance(item, dict)]
            except ValueError:
                pass
        self.live_audits = LiveAuditController(self, self.runtime_dir)
        if self.queue:
            self._schedule_queue_drain()

    def _persist(self) -> None:
        save_json(self.state_path, self.state)

    def _persist_queue(self) -> None:
        save_json(self.queue_path, self.queue)

    def _queue_snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self.queue),
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "type": str(item.get("type") or "generation"),
                    "created_at": item.get("created_at"),
                    "summary": str(item.get("summary") or ""),
                    "position": index,
                }
                for index, item in enumerate(self.queue, 1)
            ],
        }

    def _busy(self) -> bool:
        # Keep the node reserved until the watcher has recorded the process exit.
        # Un pipeline en pausa tambien reserva el nodo: si no, la cola arrancaria
        # el siguiente trabajo encima del que el usuario dejo a medias y ya no
        # habria forma de reanudarlo.
        return self.process is not None or self._is_resumable() or self.live_audits.is_running()

    def _is_resumable(self) -> bool:
        pipeline = list(self.state.get("pipeline") or [])
        step_index = safe_int(self.state.get("current_step_index"), -1)
        return (
            str(self.state.get("status") or "") in RESUMABLE_STATUSES
            and 0 <= step_index < len(pipeline)
            and bool(str(self.state.get("log_path") or "").strip())
        )

    def _enqueue(self, task_type: str, payload: dict[str, Any], summary: str) -> dict[str, Any]:
        if len(self.queue) >= 100:
            raise RuntimeError("La cola de este nodo alcanzo el limite de 100 tareas")
        task_id = f"{int(time.time() * 1000)}_{time.time_ns() % 1_000_000:06d}"
        item = {
            "id": task_id,
            "type": task_type,
            "payload": payload,
            "created_at": utc_now(),
            "summary": summary,
        }
        self.queue.append(item)
        self._persist_queue()
        return {
            **dict(self.state),
            "queued": True,
            "queue_item": {**self._queue_snapshot()["items"][-1]},
            "task_queue": self._queue_snapshot(),
        }

    def _schedule_queue_drain(self) -> None:
        timer = threading.Timer(0.05, self._drain_queue)
        timer.daemon = True
        timer.start()

    def _drain_queue(self) -> None:
        with self.lock:
            if self._busy() or not self.queue:
                return
            item = self.queue.pop(0)
            self._persist_queue()
            try:
                payload = dict(item.get("payload") or {})
                if item.get("type") == "repair":
                    node_job_starts._start_repair(self, payload)
                elif item.get("type") == "regression":
                    node_job_starts._start_regression(self, payload)
                elif item.get("type") == "cleanup":
                    node_job_starts._start_cleanup(self)
                else:
                    node_job_starts._start_generation(self, payload)
            except Exception as exc:
                self.state = {
                    "job_id": item.get("id"), "job_type": item.get("type"),
                    "status": "failed", "pid": None, "started_at": utc_now(),
                    "finished_at": utc_now(), "return_code": 1, "request": item.get("payload"),
                    "command": None, "log_path": None, "error": str(exc), "pipeline": [],
                    "current_stage": None, "completed_stages": [], "stage_return_codes": {},
                }
                self._persist()
                if self.queue:
                    self._schedule_queue_drain()

    def cancel_queued(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            task_id = str(task_id or "").strip()
            if not task_id:
                raise ValueError("Falta el id de la tarea")
            before = len(self.queue)
            self.queue = [item for item in self.queue if str(item.get("id")) != task_id]
            if len(self.queue) == before:
                raise ValueError("La tarea ya no esta en la cola")
            self._persist_queue()
            return {"cancelled": task_id, "task_queue": self._queue_snapshot()}

    def _normalize_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Se queda como metodo: `guided_controller.py` tiene que ser identico
        byte a byte al del runtime de IC, asi que no puede llamar al modulo."""
        return node_job_starts._normalize_generation(self, payload)

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "guided_batch_id" in payload or "prepared_manifest" in payload:
            raise ValueError("Usar la entrada autenticada de lotes preparados")
        with self.lock:
            normalized = node_job_starts._normalize_generation(self, payload)
            # Validate paths and options before accepting a queued task.
            node_commands.build_generation_command(self.config, normalized)
            if self._busy() or self.queue:
                cycles = normalized["cycles"]
                mode = normalized.get("generation_mode", "production")
                return self._enqueue("generation", normalized, f"{cycles} ciclo(s) · {mode}")
            return node_job_starts._start_generation(self, normalized)

    def start_cleanup(self) -> dict[str, Any]:
        with self.lock:
            historical_cleanup_scripts(self.config)
            if self._busy() or self.queue:
                return self._enqueue(
                    "cleanup", {}, "Cierra MT5 y elimina tester/bases/history",
                )
            return node_job_starts._start_cleanup(self)

    def start_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = node_job_starts._normalize_repair(self, payload)
            if self._busy() or self.queue:
                run_ids = normalized["run_ids"]
                attempts = normalized["repair_attempts"]
                return self._enqueue(
                    "repair", normalized,
                    f"Run(s) {', '.join(str(value) for value in run_ids)} · {attempts} intento(s)",
                )
            return node_job_starts._start_repair(self, normalized)

    def start_regression(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = node_job_starts._normalize_regression(self, payload)
            if self._busy() or self.queue:
                run_ids = normalized["run_ids"]
                return self._enqueue(
                    "regression", normalized,
                    f"Run(s) {', '.join(str(value) for value in run_ids)} · solo regresiva",
                )
            return node_job_starts._start_regression(self, normalized)

    @staticmethod
    def _step_label(step: dict[str, Any]) -> str:
        cycle = step.get("cycle")
        stage = str(step["action"])
        if cycle is None and stage in CLEANUP_STAGES:
            run_id = safe_int(step.get("run_id"), 0, minimum=0)
            return f"run_{run_id}_{stage}" if run_id > 0 else stage
        # La reparacion recorre las mismas etapas una vez por fase. Sin la fase en
        # la clave, la segunda pasada pisaria el codigo de retorno, el comando y el
        # recuento de pendientes de la primera.
        phase = step.get("phase")
        phase_part = f"phase_{phase}_" if phase is not None else ""
        if cycle is not None:
            if step.get("attempt") is not None:
                return f"cycle_{cycle}_attempt_{step.get('attempt')}_{phase_part}{stage}"
            return f"cycle_{cycle}_{stage}"
        attempt = step.get("attempt")
        return f"run_{step.get('run_id')}_attempt_{attempt}_{phase_part}{stage}"

    def stop(self) -> dict[str, Any]:
        # La bandera se pone ANTES de pedir el bloqueo. Si el pipeline lo tiene
        # retenido descartando etapas vacias -minutos enteros en una reparacion de
        # cien runs-, `_launch_next_runnable` atiende la peticion por su cuenta y
        # esta llamada devuelve enseguida, en vez de agotar el POST del manager y
        # dejar el trabajo corriendo como si nadie hubiera pulsado nada.
        self.stop_requested = True
        if not self.lock.acquire(timeout=CONTROL_LOCK_TIMEOUT):
            return {**dict(self.state), "status": "stopping"}
        try:
            process = self.process
            if process is None or process.poll() is not None:
                # Un pipeline en pausa o interrumpido no tiene proceso vivo, pero
                # si reserva el nodo: pararlo es descartarlo para liberar la cola.
                if self._is_resumable():
                    self.stop_requested = False
                    self.state["status"] = "stopped"
                    self.state["current_step_index"] = None
                    self.state["finished_at"] = utc_now()
                    self._persist()
                    if self.queue:
                        self._schedule_queue_drain()
                    return dict(self.state)
                if str(self.state.get("status") or "") not in ACTIVE_STATUSES:
                    self.stop_requested = False
                    raise RuntimeError("No hay ninguna generacion activa")
                # En marcha y sin proceso: esta entre etapas. La bandera ya esta
                # puesta y el pipeline la atendera en la siguiente.
                self.state["status"] = "stopping"
                self._persist()
                return dict(self.state)
            self.pause_requested = False
            node_job_runtime._terminate_current(self, process)
            self.state["status"] = "stopping"
            self._persist()
            return dict(self.state)
        finally:
            self.lock.release()

    def pause(self) -> dict[str, Any]:
        """Corta la etapa en curso conservando la posicion del pipeline."""
        self.pause_requested = True
        if not self.lock.acquire(timeout=CONTROL_LOCK_TIMEOUT):
            return {**dict(self.state), "status": "pausing"}
        try:
            process = self.process
            if process is None or process.poll() is not None:
                if self._is_resumable():
                    self.pause_requested = False
                    raise RuntimeError("El pipeline ya esta pausado")
                if str(self.state.get("status") or "") not in ACTIVE_STATUSES:
                    self.pause_requested = False
                    raise RuntimeError("No hay ninguna generacion activa que pausar")
                self.state["status"] = "pausing"
                self._persist()
                return dict(self.state)
            if self.state.get("current_step_index") is None:
                self.pause_requested = False
                raise RuntimeError("Este trabajo no registra su posicion; no se puede pausar")
            self.state["status"] = "pausing"
            self._persist()
            node_job_runtime._terminate_current(self, process)
            return dict(self.state)
        finally:
            self.lock.release()

    def resume(self) -> dict[str, Any]:
        """Relanza el pipeline desde la etapa en la que se quedo."""
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Ya hay una etapa en marcha")
            if not self._is_resumable():
                raise RuntimeError("No hay ningun pipeline pausado, interrumpido o fallido que reanudar")
            step_index = safe_int(self.state.get("current_step_index"), -1)
            pipeline = list(self.state.get("pipeline") or [])
            if not 0 <= step_index < len(pipeline):
                raise RuntimeError("La posicion guardada del pipeline no es valida")
            stored_log = str(self.state.get("log_path") or "").strip()
            if not stored_log:
                raise RuntimeError("No se conserva el log del trabajo; no se puede reanudar")
            log_path = Path(stored_log)
            self.state.pop("paused_at", None)
            self.state["resumed_at"] = utc_now()
            self.state["error"] = None
            try:
                # ``first=False`` para no truncar el log de lo ya ejecutado.
                if not node_job_runtime._launch_next_runnable(self, step_index, log_path):
                    # Nada pendiente desde aqui: el pipeline estaba de hecho acabado.
                    node_job_runtime._complete(self, 0)
            except Exception as exc:
                self.state["error"] = str(exc)
                self.state["status"] = "failed"
                self._persist()
                raise
            self._persist()
            return dict(self.state)

    def status(self) -> dict[str, Any]:
        with self.lock:
            result = dict(self.state)
            task_queue = self._queue_snapshot()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        try:
            cfg = read_settings(settings_path)
            db = database_snapshot(memory_path(self.config, cfg))
            defaults = self.config.get("defaults") if isinstance(self.config.get("defaults"), dict) else {}
            launch_defaults = {
                "cycles": safe_int(defaults.get("cycles", 1), 1, minimum=1, maximum=100),
                "generations": safe_int(defaults.get("generations", setting(cfg, "General", "ubs_generation_count", "1")), 1, minimum=1),
                "variants_per_seed": safe_int(defaults.get("variants_per_seed", setting(cfg, "General", "ubs_variants_per_seed", "10")), 10, minimum=1),
                "max_seeds": safe_int(defaults.get("max_seeds", setting(cfg, "General", "ubs_max_seeds", "30")), 30, minimum=0),
                "generation_mode": str(defaults.get("generation_mode", setting(cfg, "General", "ubs_generation_mode", "production"))),
                "random_seed": defaults.get("random_seed"),
                "max_workers": safe_int(setting(cfg, "Multiterminal", "workers", "1"), 1, minimum=1, maximum=64),
                "run_robustness": setting_bool(cfg, "General", "ubs_robust_auto", False),
                "run_final_tick": setting_bool(cfg, "General", "ubs_final_tick_auto", False),
                "run_final_tick_6m": setting_bool(cfg, "General", "ubs_final_tick_6m_auto", False),
                "cleanup_after_run": bool(historical_cleanup_scripts(self.config, required=False)),
            }
        except Exception as exc:
            db = {"available": False, "error": str(exc)}
            launch_defaults = {}
        return {
            "node": {
                "id": self.config.get("node_id"),
                "name": self.config.get("display_name") or self.config.get("node_id"),
                "broker": self.config.get("broker"),
                "account_type": self.config.get("account_type"),
                "machine": os.environ.get("COMPUTERNAME") or platform.node(),
                "user": os.environ.get("USERNAME") or os.environ.get("USER"),
                "project_dir": str(project),
            },
            "job": result,
            "task_queue": task_queue,
            "database": db,
            "launch_defaults": launch_defaults,
            "capabilities": {
                "guided_batches_v1": True,
                "guided_launch_options_v1": True,
                "worker_override": True,
                "pipeline_controls": True,
                "failed_resume": True,
                "cycles": True,
                "repair_runs": True,
                "universe_management": True,
                "portfolio_views": True,
                "task_queue": True,
                "application_restart": bool(getattr(self, "application_restart_available", False)),
                "historical_cleanup": bool(historical_cleanup_scripts(self.config, required=False)),
                "live_account_audit": True,
                "live_audit_restore_account": True,
            },
            "observed_at": utc_now(),
        }

    def universe(self) -> dict[str, Any]:
        with self.lock:
            rows, disabled, seed_enabled = _load_universe_rows(self.config)
        generation_enabled = sum(1 for row in rows if row["generation_enabled"])
        seed_only = sum(1 for row in rows if not row["generation_enabled"] and row["seeds_enabled"])
        return {
            "node": {
                "id": self.config.get("node_id"),
                "name": self.config.get("display_name") or self.config.get("node_id"),
                "broker": self.config.get("broker"),
                "account_type": self.config.get("account_type"),
            },
            "symbols": rows,
            "summary": {
                "total": len(rows),
                "generation_enabled": generation_enabled,
                "generation_disabled": len(rows) - generation_enabled,
                "seed_only": seed_only,
            },
            "observed_at": utc_now(),
        }

    def update_universe(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = payload.get("symbols")
        if not isinstance(values, list) or not values:
            raise ValueError("symbols debe ser una lista no vacía")
        requested = {str(value).strip().upper() for value in values if str(value).strip()}
        generation = payload.get("generation_enabled")
        seeds = payload.get("seeds_enabled")
        if generation is None and seeds is None:
            raise ValueError("Indica generation_enabled o seeds_enabled")
        if generation is not None and not isinstance(generation, bool):
            raise ValueError("generation_enabled debe ser booleano")
        if seeds is not None and not isinstance(seeds, bool):
            raise ValueError("seeds_enabled debe ser booleano")
        with self.lock:
            rows, disabled, seed_enabled = _load_universe_rows(self.config)
            available = {str(row["symbol"]).upper() for row in rows}
            unknown = requested - available
            if unknown:
                raise ValueError(f"Símbolos desconocidos: {', '.join(sorted(unknown))}")
            if generation is True:
                disabled.difference_update(requested)
                seed_enabled.difference_update(requested)
            elif generation is False:
                disabled.update(requested)
                seed_enabled.difference_update(requested)
            if seeds is not None:
                eligible = requested & disabled
                if seeds:
                    seed_enabled.update(eligible)
                else:
                    seed_enabled.difference_update(eligible)
            _, policy_path = _universe_paths(self.config)
            save_json(policy_path, {
                "disabled": sorted(disabled),
                "seed_enabled_when_disabled": sorted(seed_enabled & disabled),
            })
        return self.universe()

    def _portfolio_source(self) -> PortfolioSource:
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        db_path = memory_path(self.config, read_settings(settings_path))
        return PortfolioSource({
            "id": self.config.get("node_id"),
            "name": self.config.get("display_name") or self.config.get("node_id"),
            "portfolio_project_dir": str(project),
            "portfolio_broker": self.config.get("broker"),
            "portfolio_account_type": self.config.get("account_type"),
            "portfolio_memory_path": str(db_path),
        })

    def save_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        return save_portfolio_payload(self._portfolio_source(), payload)

    def set_portfolio_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
        scope = normalize_portfolio_scope(payload.get("scope"))
        alias = self._portfolio_source().set_portfolio_alias(
            portfolio_id, scope, normalize_portfolio_alias(payload.get("alias"))
        )
        return {"portfolio_id": portfolio_id, "scope": scope, "alias": alias}

    def exclude_portfolio_members(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = normalize_portfolio_scope(payload.get("scope"))
        source = self._portfolio_source()
        # `verdict_applied` es la confirmación que el manager exige cuando el
        # motivo no es manual. Un nodo sin portar no devuelve esta clave y el
        # manager avisa en vez de dar por escrito un veredicto que no existe.
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        verdict_applied = reason_code != candidate_verdict.MANUAL
        if payload.get("set_paths") is not None:
            portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
            quarantine_ids = source.remove_members_to_quarantine(payload, scope)
            return {
                "quarantine_ids": quarantine_ids,
                "deleted": True,
                "portfolio_id": portfolio_id,
                "scope": scope,
                "reason_code": reason_code,
                "verdict_applied": verdict_applied,
            }
        # Single exclusion (from a saved portfolio, or straight from the inventory
        # when no portfolio_id is sent). This MUST run on the node: the manager
        # only reads the node's memory through a read-only snapshot, and writing
        # to it directly over CIFS is unreliable because SQLite's WAL is not
        # coherent across a network share, so a manager-side quarantine/delete
        # silently failed to appear (the portfolio kept showing up after a
        # "successful" exclusion). remove_member_to_quarantine already falls back
        # to exclude_strategy when there is no portfolio_id.
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        quarantine_id = source.remove_member_to_quarantine(payload, scope)
        return {
            "quarantine_id": quarantine_id,
            "portfolio_id": portfolio_id or None,
            "scope": scope,
            "reason_code": reason_code,
            "verdict_applied": verdict_applied,
        }

    def requalify_portfolio_member(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Mueve una estrategia excluida entre los tres motivos y el pool.

        Corre en el nodo por lo mismo que la exclusión individual: el manager
        solo lee esta memoria por una copia de lectura, y escribirla
        directamente por CIFS o por un bind mount de Docker no falla en silencio
        sino con "disk I/O error", porque el modo WAL necesita un `-shm` que esos
        sistemas de ficheros no respaldan. Aquí la base es local.

        La confirmación es `requalified`: un nodo sin portar no tiene esta ruta y
        devuelve 404, que el manager traduce a qué hay que portar.
        """
        scope = normalize_portfolio_scope(payload.get("scope"))
        quarantine_key = str(payload.get("quarantine_id") or "").strip()
        if not quarantine_key:
            raise ValueError("Falta la estrategia excluida que se quiere reclasificar")
        source = self._portfolio_source()
        target = source.requalify_strategy(quarantine_key, str(payload.get("reason_code") or "pool"))
        return {
            "requalified": True,
            "quarantine_id": quarantine_key,
            "reason_code": target,
            "scope": scope,
        }

    def delete_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
        scope = normalize_portfolio_scope(payload.get("scope"))
        source = self._portfolio_source()
        source.delete_portfolio(portfolio_id, scope)
        if any(int(row["id"]) == portfolio_id for row in source.saved_portfolios(scope)["portfolios"]):
            raise RuntimeError(f"El portafolio #{portfolio_id} sigue presente después del borrado")
        return {"deleted": True, "portfolio_id": portfolio_id, "scope": scope}

    def portfolios(self, scope: str = "full_history") -> dict[str, Any]:
        portfolio_scope = normalize_portfolio_scope(scope)
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        db_path = memory_path(self.config, read_settings(settings_path))
        if not db_path.is_file():
            raise ValueError(f"No existe la memoria UBS: {db_path}")
        with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "select * from portfolios where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
                (portfolio_scope,),
            ).fetchall() if _table_exists(conn, "portfolios") else []

        def value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
            return row[key] if key in row.keys() else default

        portfolios = [{
            "id": int(value(row, "id", 0) or 0), "created_at": str(value(row, "created_at", "") or ""),
            "name": str(value(row, "name", "") or ""),
            "portfolio_type": str(value(row, "portfolio_type", value(row, "type", "")) or ""),
            "portfolio_scope": portfolio_scope, "target_month": int(value(row, "target_month", 0) or 0) or None,
            "capital": float(value(row, "capital", value(row, "account_capital", 0)) or 0),
            "total_net_profit": float(value(row, "total_net_profit", 0) or 0),
            "actual_valley_dd": float(value(row, "actual_valley_dd", 0) or 0),
            "target_valley_dd": float(value(row, "target_valley_dd", 0) or 0),
            "valley_usage_pct": float(value(row, "valley_usage_pct", 0) or 0),
            "actual_point_dd": float(value(row, "actual_point_dd", 0) or 0),
            "target_point_dd": float(value(row, "target_point_dd", 0) or 0),
            "point_usage_pct": float(value(row, "point_usage_pct", 0) or 0),
            "total_lot": float(value(row, "total_lot", 0) or 0), "total_units": int(value(row, "total_units", 0) or 0),
            "active_strategies": int(value(row, "active_strategies", 0) or 0),
            "target_strategies": int(value(row, "target_strategies", 0) or 0),
            "stop_reason": str(value(row, "stop_reason", "") or ""),
            "binding_constraint": str(value(row, "binding_constraint", "") or ""),
        } for row in rows]
        if portfolio_scope == "full_history":
            for portfolio, row in zip(portfolios, rows):
                try:
                    metrics = json.loads(value(row, "metrics_json", "{}") or "{}")
                    inputs = metrics.get("inputs") if isinstance(metrics, dict) else {}
                    portfolio["alias"] = normalize_portfolio_alias(
                        inputs.get("portfolio_alias") if isinstance(inputs, dict) else ""
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    portfolio["alias"] = ""
        return {
            "node": {"id": self.config.get("node_id"), "name": self.config.get("display_name") or self.config.get("node_id"), "broker": self.config.get("broker"), "account_type": self.config.get("account_type")},
            "scope": portfolio_scope, "portfolios": portfolios,
            "summary": {"total": len(portfolios), "strategies": sum(item["active_strategies"] for item in portfolios), "latest_id": portfolios[0]["id"] if portfolios else None},
            "observed_at": utc_now(),
        }

    def portfolio_detail(self, portfolio_id: int, scope: str = "full_history") -> dict[str, Any]:
        listing = self.portfolios(scope)
        selected = next((item for item in listing["portfolios"] if item["id"] == portfolio_id), None)
        if selected is None:
            raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute(): settings_path = project / settings_path
        db_path = memory_path(self.config, read_settings(settings_path))
        with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("select metrics_json from portfolios where id=?", (portfolio_id,)).fetchone()
            try:
                parsed = json.loads(row["metrics_json"] or "{}") if row else {}
                metrics = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                metrics = {}
            members: list[dict[str, Any]] = []
            if _table_exists(conn, "portfolio_allocations"):
                members = [dict(item) for item in conn.execute("select * from portfolio_allocations where portfolio_id=? order by variant_key,set_id,units desc", (portfolio_id,)).fetchall()]
            if not members and _table_exists(conn, "portfolio_members"):
                for item in conn.execute("select * from portfolio_members where portfolio_id=? order by lot desc", (portfolio_id,)).fetchall():
                    raw = dict(item)
                    members.append({"variant_key":raw.get("variant_key") or "","variant_label":raw.get("variant_label") or "","set_id":raw.get("set_path") or "","candidate_id":raw.get("candidate_id") or "","symbol":raw.get("symbol") or "","timeframe":raw.get("period") or "","units":int(round(float(raw.get("lot") or 0)/0.01)),"lot":float(raw.get("lot") or 0),"lot_size_step":float(raw.get("lot_size_step") or .01),"net_profit_contribution":float(raw.get("combined_net_profit") or 0),"standalone_valley_dd":float(raw.get("standalone_dd") or 0),"standalone_point_dd":0.0,"set_path":raw.get("set_path") or "","margin_required":0.0,"margin_pct":0.0})
        selected["metrics"] = {"inputs": metrics.get("inputs") if isinstance(metrics.get("inputs"),dict) else {}, "stress_bootstrap": metrics.get("stress_bootstrap") if isinstance(metrics.get("stress_bootstrap"),dict) else {}, "common_set_ids": metrics.get("common_set_ids") if isinstance(metrics.get("common_set_ids"),list) else [], "variant_order": metrics.get("variant_order") if isinstance(metrics.get("variant_order"),list) else []}
        selected["members"] = [{
            "variant_key": str(raw.get("variant_key") or ""), "variant_label": str(raw.get("variant_label") or ""),
            "set_id": str(raw.get("set_id") or ""), "set_name": Path(str(raw.get("set_path") or raw.get("set_id") or "")).name,
            "set_path": str(raw.get("set_path") or raw.get("set_id") or ""),
            "candidate_id": str(raw.get("candidate_id") or ""), "symbol": str(raw.get("symbol") or ""),
            "timeframe": str(raw.get("timeframe") or ""), "units": int(raw.get("units") or 0),
            "lot": float(raw.get("lot") or 0), "lot_size_step": float(raw.get("lot_size_step") or 0),
            "net_profit_contribution": float(raw.get("net_profit_contribution") or 0),
            "standalone_valley_dd": float(raw.get("standalone_valley_dd") or 0),
            "standalone_point_dd": float(raw.get("standalone_point_dd") or 0),
            "margin_required": float(raw.get("margin_required") or 0), "margin_pct": float(raw.get("margin_pct") or 0),
            "max_balance_dd_001": float(raw.get("max_balance_dd_001") or 0),
            "max_equity_dd_001": float(raw.get("max_equity_dd_001") or 0),
            "floating_dd_source": str(raw.get("floating_dd_source") or ""),
            "standalone_floating_dd": float(raw.get("standalone_floating_dd") or 0),
            "recent_net_profit_001": float(raw.get("recent_net_profit_001") or 0),
            "recent_equity_dd_001": float(raw.get("recent_equity_dd_001") or 0),
            "has_recent_performance": bool(raw.get("has_recent_performance") or False),
            "margin_leverage": float(raw.get("margin_leverage") or 0),
            "margin_contract_size": float(raw.get("margin_contract_size") or 0),
            "margin_price": float(raw.get("margin_price") or 0),
            "is_report_path": str(raw.get("is_report_path") or ""),
            "oos_report_path": str(raw.get("oos_report_path") or ""),
            "final_tick_report_path": str(raw.get("final_tick_report_path") or ""),
            "full_history_report_path": str(raw.get("full_history_report_path") or ""),
        } for raw in members]
        return {"node": listing["node"], "scope": listing["scope"], "portfolio": selected, "observed_at": utc_now()}

    def runs(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        cfg = read_settings(settings_path)
        path = memory_path(self.config, cfg)
        page_limit = max(1, min(int(limit), 100))
        page_offset = max(0, int(offset))
        page = completed_runs_snapshot(path, page_limit + 1, page_offset)
        has_more = len(page) > page_limit
        runs = page[:page_limit]
        return {
            "runs": runs,
            "pagination": {
                "limit": page_limit,
                "offset": page_offset,
                "has_more": has_more,
                "next_offset": page_offset + len(runs) if has_more else None,
            },
            "memory_path": str(path),
            "observed_at": utc_now(),
        }

    def log_tail(self, lines: int = 200) -> dict[str, Any]:
        with self.lock:
            path_text = self.state.get("log_path")
        if not path_text or not Path(path_text).is_file():
            return {"lines": [], "log_path": path_text}
        content = Path(path_text).read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": content[-max(1, min(lines, 2000)):], "log_path": path_text}


class NodeHandler(BaseHTTPRequestHandler):
    server: "NodeServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[node-http] " + (fmt % args) + "\n")

    def _authorized(self) -> bool:
        expected = str(self.server.controller.config.get("token") or "")
        supplied = self.headers.get("Authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())

    def _send(self, status: int, value: Any) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_artifact(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, maximum: int = 1_000_000) -> dict[str, Any]:
        length = safe_int(self.headers.get("Content-Length"), 0, minimum=0, maximum=maximum)
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON")
        return value

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v1/health":
            self._send(200, {"ok": True, "node_id": self.server.controller.config.get("node_id"), "time": utc_now()})
        elif parsed.path.startswith("/api/v1/guided-batches/"):
            try:
                self._send(200, self.server.controller.guided_status(parsed.path.rsplit("/", 1)[-1]))
            except (ValueError, OSError, sqlite3.Error) as exc:
                self._send(400, {"error": str(exc)})
        elif parsed.path == "/api/v1/status":
            self._send(200, self.server.controller.status())
        elif parsed.path == "/api/v1/logs":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.log_tail(safe_int(query.get("lines", [200])[0], 200)))
        elif parsed.path == "/api/v1/runs":
            query = urllib.parse.parse_qs(parsed.query)
            limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=100)
            offset = safe_int(query.get("offset", [0])[0], 0, minimum=0)
            self._send(200, self.server.controller.runs(limit, offset))
        elif parsed.path == "/api/v1/universe":
            self._send(200, self.server.controller.universe())
        elif parsed.path == "/api/v1/live-audits":
            self._send(200, {"audits": self.server.controller.live_audits.all_states(), "observed_at": utc_now()})
        elif (
            len(parsed.path.strip("/").split("/")) == 7
            and parsed.path.strip("/").split("/")[:3] == ["api", "v1", "live-audits"]
            and parsed.path.strip("/").split("/")[4] == "artifacts"
        ):
            parts = parsed.path.strip("/").split("/")
            try:
                path = self.server.controller.live_audits.artifact_path(
                    urllib.parse.unquote(parts[3]),
                    urllib.parse.unquote(parts[5]),
                    urllib.parse.unquote(parts[6]),
                )
                self._send_artifact(path)
            except (ValueError, FileNotFoundError):
                self._send(404, {"error": "Reporte de auditoría no encontrado"})
        elif parsed.path.startswith("/api/v1/live-audits/"):
            audit_key = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            self._send(200, {"audit": self.server.controller.live_audits.state(audit_key), "observed_at": utc_now()})
        elif parsed.path == "/api/v1/portfolios":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.portfolios(query.get("scope", ["full_history"])[0]))
        elif parsed.path.startswith("/api/v1/portfolios/"):
            query = urllib.parse.parse_qs(parsed.query)
            portfolio_id = safe_int(parsed.path.rsplit("/", 1)[-1], 0, minimum=1)
            self._send(200, self.server.controller.portfolio_detail(portfolio_id, query.get("scope", ["full_history"])[0]))
        else:
            self._send(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        try:
            if self.path == "/api/v1/application/restart":
                self._send(202, self.server.request_application_restart())
            elif self.path == "/api/v1/guided-batches":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= guided_batches.MAX_BODY:
                    raise ValueError("Lote demasiado grande o vacío")
                self._send(202, self.server.controller.submit_guided(self._body(guided_batches.MAX_BODY)))
            elif self.path == "/api/v1/jobs/generation":
                self._send(202, self.server.controller.start(self._body()))
            elif self.path == "/api/v1/jobs/repair":
                self._send(202, self.server.controller.start_repair(self._body()))
            elif self.path == "/api/v1/jobs/regression":
                self._send(202, self.server.controller.start_regression(self._body()))
            elif self.path == "/api/v1/jobs/cleanup":
                self._send(202, self.server.controller.start_cleanup())
            elif self.path == "/api/v1/jobs/stop":
                self._send(202, self.server.controller.stop())
            elif self.path == "/api/v1/jobs/pause":
                self._send(202, self.server.controller.pause())
            elif self.path == "/api/v1/jobs/resume":
                self._send(202, self.server.controller.resume())
            elif self.path == "/api/v1/jobs/queue/cancel":
                self._send(200, self.server.controller.cancel_queued(str(self._body().get("task_id") or "")))
            elif self.path.startswith("/api/v1/live-audits/") and self.path.endswith("/run"):
                portfolio_id = safe_int(self.path.strip("/").split("/")[-2], 0, minimum=1)
                body = self._body()
                body["portfolio_id"] = portfolio_id
                self._send(202, {"audit": self.server.controller.live_audits.start(body)})
            elif self.path == "/api/v1/universe/symbols":
                self._send(200, self.server.controller.update_universe(self._body()))
            elif self.path == "/api/v1/portfolios/save":
                self._send(201, self.server.controller.save_portfolio(self._body(50_000_000)))
            elif self.path == "/api/v1/portfolios/alias":
                self._send(200, self.server.controller.set_portfolio_alias(self._body()))
            elif self.path == "/api/v1/portfolios/exclude":
                self._send(200, self.server.controller.exclude_portfolio_members(self._body()))
            elif self.path == "/api/v1/portfolios/requalify":
                self._send(200, self.server.controller.requalify_portfolio_member(self._body()))
            elif self.path == "/api/v1/portfolios/delete":
                self._send(200, self.server.controller.delete_portfolio(self._body()))
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": str(exc)})


class NodeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        controller: JobController,
        restart_callback: Callable[[], None] | None = None,
    ) -> None:
        self.controller = controller
        self.restart_callback = restart_callback
        self.controller.application_restart_available = restart_callback is not None
        super().__init__(address, NodeHandler)

    def request_application_restart(self) -> dict[str, Any]:
        callback = self.restart_callback
        if callback is None:
            raise RuntimeError("El reinicio remoto solo esta disponible en la aplicacion integrada")
        with self.controller.lock:
            process = self.controller.process
            process_running = process is not None and process.poll() is None
            status = str(self.controller.state.get("status") or "")
            restartable = status in {"idle", "completed", "failed", "stopped", "paused", "interrupted"}
            if process_running or self.controller.live_audits.is_running() or self.controller.queue or not restartable:
                raise RuntimeError(
                    "No se puede reiniciar la aplicacion con una ejecucion activa o tareas pendientes"
                )
        callback()
        return {"status": "restarting", "message": "Reinicio de la aplicacion solicitado"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nodo remoto para MT5 Autotester Manager")
    parser.add_argument("--config", default="node.json")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    config = dev_branch.apply_node_config(load_json(config_path))
    for key in ("node_id", "project_dir", "token"):
        if not str(config.get(key) or "").strip():
            parser.error(f"Falta {key} en {config_path}")
    host = str(config.get("host") or "0.0.0.0")
    port = safe_int(config.get("port"), 8761, minimum=1, maximum=65535)
    server = NodeServer((host, port), JobController(config, config_path))
    print(f"Nodo {config['node_id']} escuchando en http://{host}:{port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
