"""El proceso del manager: configuracion, coordinadores y planificador.

Se arma por pasos con nombre en vez de un `__init__` de 123 lineas. El hilo de
la auditoria en vivo **vigila siempre y lanza solo si el interruptor esta
puesto**: ver el comentario de `_start_live_audit_scheduler`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import live_audit_schedule, live_audit_watch, manager_tls
from .common import load_json, safe_int, save_json
from .experiment_service import ExperimentCoordinator
from .live_audit_archive import LiveAuditArchive
from .live_audit_notifications import LiveAuditNotificationStore
from .live_audit_settings import LiveAuditSettingsStore
from .live_audit_watch import LiveAuditRunState
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


_LEGACY_SCHEDULER_CONFIG_KEYS = (
    "live_audit_scheduler_enabled", "live_audit_scheduler_interval_days",
)


def _scheduler_defaults(config: dict[str, Any], legacy_environment: bool) -> dict[str, Any]:
    """Conserva la cadencia en días de despliegues anteriores al cron semanal."""
    legacy = legacy_environment or any(key in config for key in _LEGACY_SCHEDULER_CONFIG_KEYS)
    return {
        "enabled": _truthy(config.get("live_audit_scheduler_enabled")),
        "interval_days": safe_int(
            config.get("live_audit_scheduler_interval_days"), 30,
            minimum=1, maximum=3650,
        ),
        **({"mode": "interval"} if legacy else {}),
    }


def _scheduler_settings(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    """Carga el JSON y reconoce el contrato antiguo `{enabled, interval_days}`."""
    if not path.is_file():
        return normalize_live_audit_scheduler_settings({}, defaults)
    stored = load_json(path)
    if "mode" not in stored and "interval_days" in stored:
        stored = {**stored, "mode": "interval"}
    return normalize_live_audit_scheduler_settings(stored, defaults)


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
        # La campana y la memoria del cron viven al lado de la configuracion:
        # un aviso del sabado por la noche tiene que seguir ahi el lunes.
        self.live_audit_notifications = LiveAuditNotificationStore(
            live_audit_settings_path.with_name("live_audit_notifications.json"),
        )
        self.live_audit_runs = LiveAuditRunState(
            live_audit_settings_path.with_name("live_audit_schedule_state.json"),
        )
        # El agente solo conserva la ultima ejecucion de cada uso: sin este
        # diario, cada auditoria borraba la anterior y no habia serie.
        self.live_audit_archive = LiveAuditArchive(
            live_audit_settings_path.with_name("live_audit_history"),
        )
        scheduler_file = str(config.get("live_audit_scheduler_settings_file") or "").strip()
        self.live_audit_scheduler_path = (
            Path(scheduler_file).expanduser().resolve()
            if scheduler_file else live_audit_settings_path.with_name("live_audit_scheduler.json")
        )
        raw_scheduler_environment = os.environ.get("MT5_MANAGER_LIVE_AUDIT_SCHEDULER")
        self.live_audit_scheduler_environment = (
            str(raw_scheduler_environment).strip()
            if raw_scheduler_environment is not None and str(raw_scheduler_environment).strip()
            else None
        )
        defaults = _scheduler_defaults(
            config,
            self.live_audit_scheduler_environment is not None
            and not self.live_audit_scheduler_path.is_file(),
        )
        try:
            self.live_audit_scheduler_settings = _scheduler_settings(
                self.live_audit_scheduler_path, defaults,
            )
        except ValueError as exc:
            print(f"[live-audit-scheduler] configuración ignorada: {exc}", flush=True)
            self.live_audit_scheduler_settings = normalize_live_audit_scheduler_settings(
                {}, defaults,
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
        # El hilo hace dos cosas distintas y solo una toca al agente:
        #
        # - **Vigilar** es leer el estado de los usos para poder avisar en la
        #   campana de que uno terminó o falló. Solo son GET y corre siempre,
        #   porque un aviso que necesita un navegador abierto no sirve para
        #   contar lo que pasó el sábado por la noche.
        # - **Lanzar** sigue detrás del interruptor. Una auditoría pausa el
        #   pipeline del agente, corre y lo reanuda: el 2026-08-21 una
        #   ejecución desatendida dejó un terminal sin cuenta y dos días de
        #   discovery a cero. Se arma con el botón «Auditoría programada», con
        #   `live_audit_scheduler_enabled: true` en manager.json o con
        #   MT5_MANAGER_LIVE_AUDIT_SCHEDULER=1.
        self.live_audit_scheduler_enabled = (
            _truthy(self.live_audit_scheduler_environment)
            if self.live_audit_scheduler_environment is not None
            else bool(self.live_audit_scheduler_settings["enabled"])
        )
        self.live_audit_stop = threading.Event()
        self.live_audit_wakeup = threading.Event()
        print(
            "[live-audit-scheduler] "
            + (
                f"activo: {live_audit_schedule.schedule_label(self.live_audit_scheduler_settings)}"
                if self.live_audit_scheduler_enabled
                else "sin armar: solo vigila para la campana; nada se lanza solo."
            ),
            flush=True,
        )
        self.live_audit_thread: threading.Thread | None = threading.Thread(
            target=self._live_audit_schedule_loop,
            daemon=True,
            name="live-audit-scheduler",
        )
        self.live_audit_thread.start()

    def _configure_tls(self, config: dict[str, Any]) -> None:
        """El manager sirve https **solo** si encuentra su certificado.

        Es lo que convierte la pagina en contexto seguro cuando se abre desde
        otro equipo por la IP de la red local, que es el unico sitio donde el
        navegador niega los avisos de escritorio. Sin certificado, todo sigue
        exactamente como antes. Lo genera `tools.make_local_cert`.
        """
        configured = str(config.get("config_dir") or "").strip()
        found = manager_tls.paths(config, Path(configured) if configured else None)
        self.tls_context = manager_tls.context(*found) if found else None
        self.tls_certificate = found[0] if found else None
        self.scheme = "https" if self.tls_context else "http"
        if found:
            print(f"[manager] TLS activo con {found[0]}", flush=True)

    def get_request(self) -> tuple[Any, Any]:
        connection, address = super().get_request()
        if self.tls_context is None:
            return connection, address
        try:
            return manager_tls.wrap(connection, self.tls_context), address
        except OSError:
            # Un saludo TLS fallido -o una peticion http contra el puerto
            # https- cierra esa conexion y nada mas: `socketserver` ignora el
            # OSError de `get_request` y sigue atendiendo.
            connection.close()
            raise

    def __init__(self, address: tuple[str, int], config: dict[str, Any]) -> None:
        self._configure_nodes(config)
        self._configure_preferences(config)
        self._configure_live_audit(config)
        self._configure_coordinators(config)
        self._configure_restart(config)
        self._configure_tls(config)
        super().__init__(address, ManagerHandler)
        self._start_live_audit_scheduler()

    def server_close(self) -> None:
        self.live_audit_stop.set()
        self.live_audit_wakeup.set()
        super().server_close()

    def live_audit_scheduler_state(self) -> dict[str, Any]:
        """Contrato público y explícito del «cron» interno."""
        settings = dict(self.live_audit_scheduler_settings)
        _zone, timezone_warning = live_audit_schedule.resolve_zone(settings.get("timezone"))
        now = datetime.now(timezone.utc)
        return {
            **settings,
            "effective_enabled": bool(self.live_audit_scheduler_enabled),
            "source": "environment" if self.live_audit_scheduler_environment is not None else "saved",
            "environment_override": self.live_audit_scheduler_environment is not None,
            "schedule_label": live_audit_schedule.schedule_label(settings),
            "next_run_at": (
                live_audit_schedule.next_occurrence(now, settings).isoformat()
                if settings.get("mode") == "weekly" else ""
            ),
            "catch_up_missed_runs": True,
            "timezone_warning": timezone_warning,
            **live_audit_watch.targets(self),
            "description": live_audit_schedule.schedule_description(settings),
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
        if self.live_audit_thread is None:
            self.live_audit_thread = threading.Thread(
                target=self._live_audit_schedule_loop,
                daemon=True,
                name="live-audit-scheduler",
            )
            self.live_audit_thread.start()
        else:
            self.live_audit_wakeup.set()
        return self.live_audit_scheduler_state()

    def _live_audit_schedule_loop(self) -> None:
        # La espera inicial y el sondeo son detalles internos; el usuario
        # configura la cita semanal o la cadencia en días, no este pulso.
        if self.live_audit_stop.wait(_LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS):
            return
        while not self.live_audit_stop.is_set():
            try:
                self._run_due_live_audits()
            except Exception as exc:
                sys.stderr.write(f"[live-audit-scheduler] {exc}\n")
            self.live_audit_wakeup.wait(_LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS)
            self.live_audit_wakeup.clear()

    def _run_due_live_audits(self) -> None:
        """Una vuelta del vigilante: avisos siempre, lanzamientos si toca.

        El reparto esta en `live_audit_watch`; aqui queda el nombre que usan el
        bucle y las pruebas. El segundo candado sigue dentro: sin el
        interruptor, `tick` lee los nodos y no les pide nada.
        """
        live_audit_watch.tick(self)

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
