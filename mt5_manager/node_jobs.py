"""El controlador de trabajos del nodo: cola, ciclo de vida y estado.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Ver `AGENTS.md`, seccion
«El nodo NO ejecuta este repositorio».
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
from . import node_job_queue
from . import node_job_starts
from . import node_portfolio_api
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
from .node_statuses import (  # noqa: F401
    ACTIVE_STATUSES,
    CONTROL_LOCK_TIMEOUT,
    RESUMABLE_STATUSES,
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

    def _normalize_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Se queda como metodo: `guided_controller.py` tiene que ser identico
        byte a byte al del runtime de IC, asi que no puede llamar al modulo."""
        return node_job_starts._normalize_generation(self, payload)

    # La cola vive en `node_job_queue`. Estos metodos se quedan porque
    # `guided_controller.py` tiene que ser identico byte a byte al runtime de
    # IC y llama a `self._enqueue` y `self._schedule_queue_drain`.
    def _persist_queue(self) -> None:
        node_job_queue._persist_queue(self)

    def _queue_snapshot(self) -> dict[str, Any]:
        return node_job_queue._queue_snapshot(self)

    def _busy(self) -> bool:
        return node_job_queue._busy(self)

    def _is_resumable(self) -> bool:
        return node_job_queue._is_resumable(self)

    def _enqueue(self, job_type: str, payload: dict[str, Any], label: str) -> dict[str, Any]:
        return node_job_queue._enqueue(self, job_type, payload, label)

    def _schedule_queue_drain(self) -> None:
        node_job_queue._schedule_queue_drain(self)

    def _drain_queue(self) -> None:
        node_job_queue._drain_queue(self)

    def cancel_queued(self, payload: dict[str, Any]) -> dict[str, Any]:
        return node_job_queue.cancel_queued(self, payload)

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
