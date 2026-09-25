"""El proceso del manager: configuracion, coordinadores y planificador.

Se arma por pasos con nombre en vez de un `__init__` de 123 lineas. El
planificador de auditoria en vivo arranca **apagado** a proposito: ver el
comentario de `_start_live_audit_scheduler`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import manager_http
from .common import load_json, safe_int, save_json
from .experiment_service import ExperimentCoordinator
from .live_audit_settings import LiveAuditSettingsStore
from .manager_config import (
    BOOL_PREFERENCE_KEYS,
    LAUNCH_PREFERENCE_KEYS,
    _LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS,
    _LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS,
    _truthy,
    normalize_live_audit_scheduler_settings,
)
from .manager_handler import ManagerHandler
from .manager_restart import ManagerRestartController
from .portfolio_service import PortfolioCoordinator


class ManagerServer(ThreadingHTTPServer):
    daemon_threads = True

    def _configure_nodes(self, config: dict[str, Any]) -> None:
        nodes = config.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("manager.json debe contener una lista nodes no vacia")
        self.nodes = nodes
        self.node_status_lock = threading.Lock()
        self.node_status_cache: dict[str, dict[str, Any]] = {}
        export_mode = str(
            os.environ.get("MT5_MANAGER_EXPORT_MODE") or config.get("export_mode") or "folder"
        ).strip().lower()
        if export_mode not in {"folder", "download"}:
            raise ValueError("export_mode debe ser folder o download")
        self.export_mode = export_mode

    def _configure_preferences(self, config: dict[str, Any]) -> None:
        """Las preferencias por nodo. Un fichero ilegible se ignora, no aborta."""
        preferences_file = str(config.get("preferences_file") or "").strip()
        self.preferences_path = Path(preferences_file).expanduser().resolve() if preferences_file else None
        self.preferences_lock = threading.RLock()
        self.preferences: dict[str, dict[str, Any]] = {}
        if self.preferences_path and self.preferences_path.is_file():
            try:
                stored = load_json(self.preferences_path)
                self.preferences = {
                    str(key): dict(value) for key, value in stored.items() if isinstance(value, dict)
                }
            except ValueError:
                self.preferences = {}

    def _configure_live_audit(self, config: dict[str, Any]) -> None:
        """El almacen de la auditoria y los ajustes de su planificador."""
        live_audit_settings_file = str(config.get("live_audit_settings_file") or "").strip()
        live_audit_settings_path = (
            Path(live_audit_settings_file).expanduser().resolve()
            if live_audit_settings_file
            else Path.cwd() / "runtime" / "live_audit_settings.json"
        )
        self.live_audit_settings = LiveAuditSettingsStore(live_audit_settings_path)
        scheduler_file = str(config.get("live_audit_scheduler_settings_file") or "").strip()
        self.live_audit_scheduler_path = (
            Path(scheduler_file).expanduser().resolve()
            if scheduler_file else live_audit_settings_path.with_name("live_audit_scheduler.json")
        )
        scheduler_defaults = {
            "enabled": _truthy(config.get("live_audit_scheduler_enabled")),
            "interval_days": safe_int(
                config.get("live_audit_scheduler_interval_days"), 30,
                minimum=1, maximum=3650,
            ),
        }
        self.live_audit_scheduler_settings = dict(scheduler_defaults)
        if self.live_audit_scheduler_path.is_file():
            try:
                self.live_audit_scheduler_settings = normalize_live_audit_scheduler_settings(
                    load_json(self.live_audit_scheduler_path), scheduler_defaults,
                )
            except ValueError as exc:
                print(f"[live-audit-scheduler] configuración ignorada: {exc}", flush=True)
        raw_scheduler_environment = os.environ.get("MT5_MANAGER_LIVE_AUDIT_SCHEDULER")
        self.live_audit_scheduler_environment = (
            str(raw_scheduler_environment).strip()
            if raw_scheduler_environment is not None and str(raw_scheduler_environment).strip()
            else None
        )

    def _configure_coordinators(self, config: dict[str, Any]) -> None:
        portfolio_settings_file = str(config.get("portfolio_settings_file") or "").strip()
        portfolio_settings_path = (
            Path(portfolio_settings_file).expanduser().resolve()
            if portfolio_settings_file
            else Path.cwd() / "runtime" / "portfolio_settings.json"
        )
        self.portfolios = PortfolioCoordinator(self.nodes, portfolio_settings_path)
        # Laboratorio «Experimenta»: vive aparte del coordinador de portafolios
        # a propósito, porque no guarda nada en la memoria de ningún agente.
        self.experiments = ExperimentCoordinator(
            self.nodes, portfolio_settings_path.with_name("experiment_settings.json"),
        )

    def _configure_restart(self, config: dict[str, Any]) -> None:
        repo_dir = str(
            os.environ.get("MT5_MANAGER_RESTART_REPO")
            or config.get("manager_repo_dir")
            or Path(__file__).resolve().parents[1]
        )
        restart_state_file = str(
            os.environ.get("MT5_MANAGER_RESTART_STATE")
            or config.get("manager_restart_state_file")
            or Path.cwd() / "runtime" / "manager_restart.json"
        )
        restart_log_file = str(
            os.environ.get("MT5_MANAGER_RESTART_LOG")
            or config.get("manager_restart_log_file")
            or Path.cwd() / "runtime" / "manager_restart.log"
        )
        self.manager_restart = ManagerRestartController(
            repo_dir,
            restart_state_file,
            restart_log_file,
            container_name=str(
                os.environ.get("MT5_MANAGER_CONTAINER_NAME")
                or config.get("manager_container_name")
                or "mt5-autotester-manager"
            ),
        )

    def _start_live_audit_scheduler(self) -> None:
        # La auditoría en vivo se dispara **solo a mano** mientras el MVP no esté
        # cerrado. Automática pausaba el pipeline del agente, corría sola y lo
        # reanudaba sin nadie delante; el 2026-08-21 una ejecución desatendida
        # dejó un terminal sin cuenta y dos días de discovery a cero. Para
        # rearmarla: `live_audit_scheduler_enabled: true` en manager.json, o
        # MT5_MANAGER_LIVE_AUDIT_SCHEDULER=1.
        self.live_audit_scheduler_enabled = (
            _truthy(self.live_audit_scheduler_environment)
            if self.live_audit_scheduler_environment is not None
            else bool(self.live_audit_scheduler_settings["enabled"])
        )
        self.live_audit_stop = threading.Event()
        self.live_audit_wakeup = threading.Event()
        self.live_audit_thread: threading.Thread | None = None
        if not self.live_audit_scheduler_enabled:
            print(
                "[live-audit-scheduler] desactivado: la auditoría solo se lanza a mano. "
                "Para rearmarlo, live_audit_scheduler_enabled=true en manager.json.",
                flush=True,
            )
            return
        self.live_audit_thread = threading.Thread(
            target=self._live_audit_schedule_loop,
            daemon=True,
            name="live-audit-scheduler",
        )
        self.live_audit_thread.start()

    def __init__(self, address: tuple[str, int], config: dict[str, Any]) -> None:
        self._configure_nodes(config)
        self._configure_preferences(config)
        self._configure_live_audit(config)
        self._configure_coordinators(config)
        self._configure_restart(config)
        super().__init__(address, ManagerHandler)
        self._start_live_audit_scheduler()

    def server_close(self) -> None:
        self.live_audit_stop.set()
        self.live_audit_wakeup.set()
        super().server_close()

    def live_audit_scheduler_state(self) -> dict[str, Any]:
        """Contrato público y explícito del antiguo «cron» interno."""
        return {
            **dict(self.live_audit_scheduler_settings),
            "effective_enabled": bool(self.live_audit_scheduler_enabled),
            "source": "environment" if self.live_audit_scheduler_environment is not None else "saved",
            "environment_override": self.live_audit_scheduler_environment is not None,
            "description": (
                "El manager ejecuta las auditorías configuradas cada X días. "
                "Nunca vuelve a iniciar una que ya esté ejecutándose."
            ),
        }

    def update_live_audit_scheduler(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Persiste y aplica el programador sin exigir reiniciar el manager."""
        normalized = normalize_live_audit_scheduler_settings(
            changes, self.live_audit_scheduler_settings,
        )
        save_json(self.live_audit_scheduler_path, normalized)
        self.live_audit_scheduler_settings = normalized
        self.live_audit_scheduler_enabled = (
            _truthy(self.live_audit_scheduler_environment)
            if self.live_audit_scheduler_environment is not None
            else bool(normalized["enabled"])
        )
        if self.live_audit_scheduler_enabled and self.live_audit_thread is None:
            self.live_audit_thread = threading.Thread(
                target=self._live_audit_schedule_loop,
                daemon=True,
                name="live-audit-scheduler",
            )
            self.live_audit_thread.start()
        else:
            self.live_audit_wakeup.set()
        return self.live_audit_scheduler_state()

    @staticmethod
    def _audit_timestamp(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _live_audit_schedule_loop(self) -> None:
        # La espera inicial y el sondeo son detalles internos; el usuario solo
        # configura la cadencia real en días.
        if self.live_audit_stop.wait(_LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS):
            return
        while not self.live_audit_stop.is_set():
            try:
                self._run_due_live_audits()
            except Exception as exc:
                sys.stderr.write(f"[live-audit-scheduler] {exc}\n")
            self.live_audit_wakeup.wait(_LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS)
            self.live_audit_wakeup.clear()

    def _node_live_audits(self, node: dict[str, Any], node_id: str) -> dict[str, Any] | None:
        """Los usos que el nodo declara, o ``None`` si no se le puede preguntar.

        Un agente con el auditor anterior no cuenta como error: se anota y se
        salta, porque lo que le falta es un reinicio, no configuracion.
        """
        try:
            status_status, node_state = manager_http.node_request(node, "GET", "/api/v1/status", timeout=10)
            capabilities = (
                node_state.get("capabilities")
                if status_status == 200 and isinstance(node_state, dict) else {}
            )
            if not isinstance(capabilities, dict) or not capabilities.get("live_audit_restore_account"):
                sys.stderr.write(
                    f"[live-audit-scheduler] {node_id}: auditor antiguo; pendiente reiniciar el agente\n"
                )
                return None
            status, value = manager_http.node_request(node, "GET", "/api/v1/live-audits", timeout=10)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            sys.stderr.write(f"[live-audit-scheduler] {node_id}: no se pudo consultar el nodo: {exc}\n")
            return None
        if status != 200 or not isinstance(value, dict):
            sys.stderr.write(f"[live-audit-scheduler] {node_id}: GET devolvió HTTP {status}: {value}\n")
            return None
        return value.get("audits") if isinstance(value.get("audits"), dict) else {}

    def _live_audit_is_due(self, audit: dict[str, Any], now: datetime) -> bool:
        """Falso si el uso esta corriendo o si aun no ha cumplido el intervalo."""
        if str(audit.get("status") or "") in {
            "queued", "pausing", "extracting", "testing", "comparing", "finalizing", "resuming",
        }:
            return False
        result = audit.get("last_result") if isinstance(audit.get("last_result"), dict) else {}
        previous = self._audit_timestamp(result.get("completed_at") or audit.get("finished_at"))
        if previous is None:
            return True
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        interval = int(self.live_audit_scheduler_settings["interval_days"])
        return now - previous >= timedelta(days=interval)

    def _start_due_audit(
        self, node: dict[str, Any], node_id: str, audit_id: str, profile: dict[str, Any],
    ) -> None:
        portfolio_id = safe_int(profile.get("portfolio_id"), 0, minimum=1)
        payload = {
            **profile,
            **self.live_audit_settings.credentials(node_id, audit_id),
            **self.live_audit_settings.restore_credentials(node_id),
            "audit_key": audit_id,
            "portfolio_id": portfolio_id,
        }
        if not payload.get("restore_password"):
            sys.stderr.write(
                f"[live-audit-scheduler] {node_id}/{audit_id}: "
                "falta la cuenta de restauración de terminales\n"
            )
            return
        sys.stderr.write(
            f"[live-audit-scheduler] iniciando {node_id}/{audit_id}: "
            f"portafolio #{portfolio_id}, variante {profile.get('portfolio_type')}, "
            f"cuenta {profile.get('source_login')}\n"
        )
        start_status, response = manager_http.node_request(
            node, "POST", f"/api/v1/live-audits/{portfolio_id}/run", payload, timeout=30
        )
        if start_status >= 400 and start_status != 409:
            sys.stderr.write(
                f"[live-audit-scheduler] {node_id}/{audit_id}/#{portfolio_id}: HTTP {start_status} {response}\n"
            )

    def _run_due_live_audits(self) -> None:
        # Segundo candado: aunque alguien arranque el bucle, sin el interruptor
        # no se lanza ninguna auditoría.
        if not self.live_audit_scheduler_enabled:
            return
        now = datetime.now(timezone.utc)
        for node in self.nodes:
            if not self.live_audit_scheduler_enabled or self.live_audit_stop.is_set():
                return
            node_id = str(node.get("id") or "")
            state = self.live_audit_settings.state(node_id)
            configured = list(state.get("configured_audit_ids") or [])
            if not configured:
                continue
            audits = self._node_live_audits(node, node_id)
            if audits is None:
                continue
            for audit_id in configured:
                if not self.live_audit_scheduler_enabled or self.live_audit_stop.is_set():
                    return
                if not self._live_audit_is_due(dict(audits.get(str(audit_id)) or {}), now):
                    continue
                self._start_due_audit(
                    node, node_id, audit_id,
                    dict((state.get("profiles") or {}).get(str(audit_id)) or {}),
                )

    def preferences_for(self, node_id: str) -> dict[str, Any]:
        with self.preferences_lock:
            return dict(self.preferences.get(node_id) or {})

    def update_preferences(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(LAUNCH_PREFERENCE_KEYS)
        if unknown:
            raise ValueError(f"Preferencias desconocidas: {', '.join(sorted(unknown))}")
        normalized: dict[str, Any] = {}
        if "cycles" in changes:
            normalized["cycles"] = safe_int(changes["cycles"], 1, minimum=1, maximum=100)
        if "generations" in changes:
            normalized["generations"] = safe_int(changes["generations"], 1, minimum=1, maximum=1000)
        if "variants_per_seed" in changes:
            normalized["variants_per_seed"] = safe_int(
                changes["variants_per_seed"], 10, minimum=1, maximum=10000
            )
        if "max_seeds" in changes:
            normalized["max_seeds"] = safe_int(changes["max_seeds"], 30, minimum=0, maximum=100000)
        if "generation_mode" in changes:
            mode = str(changes["generation_mode"] or "").strip().lower()
            if mode not in {"production", "discovery"}:
                raise ValueError("generation_mode debe ser production o discovery")
            normalized["generation_mode"] = mode
        if "random_seed" in changes:
            value = changes["random_seed"]
            if value is None or str(value).strip() == "":
                normalized["random_seed"] = None
            else:
                try:
                    normalized["random_seed"] = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("random_seed debe ser un entero o null") from exc
        for key in ("max_workers", "repair_max_workers", "regression_max_workers"):
            if key in changes:
                normalized[key] = safe_int(changes[key], 1, minimum=1, maximum=64)
        if "repair_attempts" in changes:
            normalized["repair_attempts"] = safe_int(changes["repair_attempts"], 1, minimum=1, maximum=20)
        for key in BOOL_PREFERENCE_KEYS:
            if key in changes:
                if not isinstance(changes[key], bool):
                    raise ValueError(f"{key} debe ser booleano")
                normalized[key] = changes[key]
        with self.preferences_lock:
            current = dict(self.preferences.get(node_id) or {})
            current.update(normalized)
            self.preferences[node_id] = current
            if self.preferences_path:
                save_json(self.preferences_path, self.preferences)
            return dict(current)

    def remember_launch_request(self, node_id: str, payload: dict[str, Any]) -> None:
        """Guarda como preferencia cada campo con el que se lanzó una generación.

        Solo aplica al arranque de generación: en reparación y prueba regresiva
        ``max_workers`` significa las terminales de esa etapa, no las de generación.
        """
        changes = {
            key: bool(payload[key]) if key in BOOL_PREFERENCE_KEYS else payload[key]
            for key in LAUNCH_PREFERENCE_KEYS
            if key in payload
        }
        if not changes:
            return
        try:
            self.update_preferences(node_id, changes)
        except (ValueError, OSError) as exc:
            # Nunca hacer fallar un lanzamiento aceptado por no poder recordarlo.
            print(f"[manager] No se pudo recordar la configuración de {node_id}: {exc}", flush=True)
