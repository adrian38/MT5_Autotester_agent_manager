"""State, settings and job lifecycle for the portfolio coordinator."""

from __future__ import annotations

import contextlib
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from portfolio_manager.ubs_portfolio import PortfolioResult

from .common import load_json, save_json, utc_now
from .portfolio_job_notices import job_notice, notice_link
from .portfolio_persistence import result_payload
from .portfolio_proposals import proposal_diff, save_portfolio_payload
from .portfolio_schema import ensure_portfolio_schema
from .portfolio_scope import PORTFOLIO_SCOPES, SCOPE_LABELS, normalize_portfolio_scope
from .portfolio_settings import normalize_settings
from .portfolio_source import PortfolioSource


def scope_stage_count(scope: str, operation: str) -> int:
    """Number of numbered stages the worker of this scope/operation emits."""
    scope = normalize_portfolio_scope(scope)
    if scope == "monthly":
        return 6
    if scope == "grid":
        return 4
    return 3 if operation == "complete" else 5


def prepare_scope_log(
    source: PortfolioSource,
    scope: str,
    operation: str,
    job_id: str,
    first_line: str,
) -> Path:
    """Create the calculation log before the worker thread starts.

    La pantalla habilita «Ver log» en cuanto el trabajo pasa a running; si el
    fichero aún no existe, el botón abre un diálogo vacío justo cuando más
    interesa mirar. Crearlo antes del hilo era una ventaja que solo tenía el
    mensual y no depende de nada estacional.
    """
    log_dir = source.project / "portfolio_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"manager_{normalize_portfolio_scope(scope)}_{operation}_{job_id}.log"
    path.write_text(
        f"{datetime.now().isoformat(timespec='seconds')} | {first_line}\n",
        encoding="utf-8",
    )
    return path


class PortfolioCoordinatorCoreMixin:
    def __init__(self, nodes: list[dict[str, Any]], settings_path: Path) -> None:
        self.nodes = {str(node.get("id")): node for node in nodes}
        self.settings_path = settings_path
        self.lock = threading.RLock()
        self.settings: dict[str, dict[str, dict[str, Any]]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.proposals: dict[str, list[dict[str, Any]]] = {}
        self.tasks: dict[str, list[dict[str, Any]]] = {}
        self.task_workers: set[str] = set()
        self.cancellation_events: dict[str, threading.Event] = {}
        # La campana del manager, que `ManagerServer` le presta al construirlo.
        # Sin ella el coordinador funciona igual y no avisa de nada: los tests
        # que lo montan sueltos no tienen que saber de avisos.
        self.notifications: Any = None
        # El servidor le presta también el almacén de auditorías. Un borrado
        # confirmado debe retirar sus usos para que no sigan apareciendo como
        # portafolios activos ni entren en el cron.
        self.live_audit_settings: Any = None
        if settings_path.is_file():
            try:
                loaded = load_json(settings_path)
                self.settings = {
                    str(node_id): {str(scope): dict(values) for scope, values in scopes.items() if isinstance(values, dict)}
                    for node_id, scopes in loaded.items() if isinstance(scopes, dict)
                }
            except ValueError:
                self.settings = {}

    @staticmethod
    def _key(node_id: str, scope: str) -> str:
        return f"{node_id}:{scope}"

    def _node(self, node_id: str) -> dict[str, Any]:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise ValueError(f"Nodo desconocido: {node_id}") from exc

    def _persistence_source(self, node_id: str, scope: str) -> PortfolioSource:
        """Use a manager-owned database for Grid packages.

        Broker nodes deployed before Grid normalize the new scope to
        ``full_history``. Keeping Grid in its own manager database prevents
        those writes from contaminating UBS while preserving the broker files
        as the read-only source for calculation and export.
        """
        source = PortfolioSource(self._node(node_id))
        if normalize_portfolio_scope(scope) != "grid":
            return source
        root = self.settings_path.parent / "grid_portfolios"
        root.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", node_id) + ".sqlite"
        memory = root / filename
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            ensure_portfolio_schema(conn)
            conn.commit()
        source.memory = memory
        source.memory_sources = [(f"{source.broker}/{source.account}/GRID", memory)]
        return source

    def _calculation_source(self, node_id: str, scope: str) -> PortfolioSource:
        """Read candidates from the broker and saved Grid packages from manager storage."""
        source = PortfolioSource(self._node(node_id))
        if normalize_portfolio_scope(scope) != "grid":
            return source
        grid_source = self._persistence_source(node_id, "grid")
        source.scope_memory = grid_source.memory
        source.memory_sources.append(
            (f"{source.broker}/{source.account}/GRID", grid_source.memory)
        )
        return source

    def save_grid_package(self, node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if normalize_portfolio_scope(payload.get("scope")) != "grid":
            raise ValueError("El paquete no pertenece al ámbito Grid")
        keys = {
            str(proposal.get("key") or "")
            for proposal in payload.get("proposals") or []
            if isinstance(proposal, dict)
        }
        if keys != {"aggressive", "balanced", "conservative"}:
            raise ValueError("El paquete Grid debe contener las tres variantes A/M/C")
        return save_portfolio_payload(self._persistence_source(node_id, "grid"), payload)

    def settings_for(self, node_id: str, scope: str) -> dict[str, Any]:
        node = self._node(node_id)
        broker = str(node.get("portfolio_broker") or "ICTRADING")
        with self.lock:
            stored = dict((self.settings.get(node_id) or {}).get(scope) or {})
        return normalize_settings(scope, stored, broker)

    def update_settings(self, node_id: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.settings_for(node_id, scope)
        current.update(changes)
        normalized = normalize_settings(scope, current, str(self._node(node_id).get("portfolio_broker") or "ICTRADING"))
        with self.lock:
            self.settings.setdefault(node_id, {})[scope] = normalized
            save_json(self.settings_path, self.settings)
        return normalized

    def inventory(self, node_id: str, scope: str, settings: dict[str, Any]) -> dict[str, Any]:
        """Sets disponibles por símbolo con los filtros de `settings` aplicados."""
        source = self._calculation_source(node_id, scope)
        if normalize_portfolio_scope(scope) == "grid":
            from .portfolio_grid_service import grid_inventory

            return grid_inventory(source, settings)
        return source.inventory(scope, settings)

    def apply_settings(self, node_id: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Guarda el formulario y devuelve el inventario que esos ajustes filtran.

        Los grupos permitidos, `grid_off` y la exclusión de sets ya usados deciden
        qué filas cuenta el inventario, así que la tabla «Sets disponibles por
        símbolo» quedaba obsoleta en cuanto se marcaba una casilla y solo se
        repintaba al pulsar Guardar o Refrescar. Viaja con el guardado para que la
        pantalla no tenga que pedir el estado entero, que rehidrataría el
        formulario que el usuario sigue tocando.

        La lectura va a la memoria del agente y puede fallar (proyecto no montado,
        memoria inexistente). Los ajustes ya están guardados en ese punto: la
        respuesta sale sin inventario y la pantalla conserva el que tenía, en vez
        de convertir un guardado correcto en un error de guardado.
        """
        settings = self.update_settings(node_id, scope, changes)
        try:
            inventory = self.inventory(node_id, scope, settings)
        except (ValueError, OSError, sqlite3.Error):
            return {"settings": settings}
        return {"settings": settings, "inventory": inventory}

    def start(self, node_id: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        automatic_save = bool(changes.pop("automatic_save", False))
        automatic_save_key = str(changes.pop("automatic_save_key", "") or "")
        automatic_manual_check = changes.pop("automatic_manual_check", None)
        if not automatic_save:
            automatic_manual_check = None
        settings = self.update_settings(node_id, scope, changes)
        return self._start_job(
            node_id, scope, settings, "generate", None, [], automatic_manual_check,
            automatic_save, automatic_save_key,
        )

    def _active_calculation(self, node_id: str) -> dict[str, Any] | None:
        for candidate_scope in PORTFOLIO_SCOPES:
            candidate = self.jobs.get(self._key(node_id, candidate_scope)) or {}
            if candidate.get("status") in {"running", "stopping"}:
                return candidate
        return None

    def _start_job(
        self,
        node_id: str,
        scope: str,
        settings: dict[str, Any],
        operation: str,
        portfolio_id: int | None,
        previous_members: list[dict[str, Any]],
        automatic_manual_check: dict[str, Any] | None = None,
        automatic_save: bool = False,
        automatic_save_key: str = "",
    ) -> dict[str, Any]:
        key = self._key(node_id, scope)
        with self.lock:
            if self._active_calculation(node_id):
                raise ValueError(
                    "El generador de portafolios está ocupado con otro cálculo; "
                    "inténtalo de nuevo cuando termine"
                )
            if any(task.get("status") in {"pending", "running"} for task in self.tasks.get(key, [])):
                raise ValueError("Hay una tarea de portafolio pendiente o en ejecución")
            stage_total = scope_stage_count(scope, operation)
            prelude = (
                f"0/{stage_total} · Preparando cálculo "
                f"{SCOPE_LABELS[normalize_portfolio_scope(scope)]}"
            )
            job = {
                "id": time.strftime("%Y%m%d_%H%M%S"), "status": "running",
                "started_at": utc_now(), "finished_at": None,
                "progress": prelude,
                "error": None, "availability": None, "operation": operation,
                "portfolio_id": portfolio_id, "previous_members": previous_members,
                "automatic_manual_check": automatic_manual_check,
                "automatic_save": automatic_save,
                "automatic_save_key": automatic_save_key,
                "stage": 0, "stage_total": stage_total,
            }
            self.jobs[key] = job
            self.cancellation_events[key] = threading.Event()
            self.proposals.pop(key, None)
        # El log se crea antes del hilo en los tres ámbitos: así «Ver log» ya
        # tiene contenido en cuanto la pantalla ve el trabajo en marcha.
        try:
            source = PortfolioSource(self._node(node_id))
            log_path = prepare_scope_log(source, scope, operation, str(job["id"]), prelude)
            with self.lock:
                self.jobs[key]["log_path"] = str(log_path)
                job["log_path"] = str(log_path)
        except Exception as exc:
            with self.lock:
                self.jobs[key].update({
                    "status": "failed",
                    "finished_at": utc_now(),
                    "progress": "Error preparando el log del cálculo",
                    "error": str(exc),
                })
                self.cancellation_events.pop(key, None)
            raise
        threading.Thread(target=self._worker, args=(node_id, scope, settings, operation, portfolio_id), daemon=True).start()
        return dict(job)

    def stop(self, node_id: str, scope: str) -> dict[str, Any]:
        scope = normalize_portfolio_scope(scope)
        if scope != "full_history":
            raise ValueError("Detener solo está disponible para Portafolio UBS")
        key = self._key(node_id, scope)
        with self.lock:
            job = self.jobs.get(key) or {}
            if job.get("status") == "stopping":
                return dict(job)
            if job.get("status") != "running":
                raise ValueError("No hay un cálculo UBS en curso")
            event = self.cancellation_events.get(key)
            if event is None:
                raise ValueError("El cálculo en curso no admite detención")
            event.set()
            job.update({"status": "stopping", "progress": "Deteniendo cálculo…"})
            return dict(job)

    def start_saved_operation(
        self, node_id: str, scope: str, portfolio_id: int, operation: str, changes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if operation not in {"reoptimize", "complete", "improve", "repair"}:
            raise ValueError("Operación guardada desconocida")
        source = self._persistence_source(node_id, scope)
        detail = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        settings = source.saved_inputs(portfolio_id, scope)
        if changes:
            automatic_save = bool(changes.pop("automatic_save", False))
            automatic_manual_check = changes.pop("automatic_manual_check", None)
            if not automatic_save:
                automatic_manual_check = None
            settings.update(changes)
            settings = normalize_settings(scope, settings, source.broker)
        else:
            automatic_save = False
            automatic_manual_check = None
        return self._start_job(
            node_id, scope, settings, operation, portfolio_id,
            list(detail.get("members") or []), automatic_manual_check,
            automatic_save,
        )

    def _record_job_progress(self, key: str, message: str) -> None:
        """Anota el avance del job y, si numera etapas, su posicion.

        Los tres ambitos numeran sus etapas «N/M». El mensual era el unico
        que lo leia y por eso el unico con monitor.
        """
        with self.lock:
            if key not in self.jobs:
                return
            self.jobs[key]["progress"] = str(message)
            match = re.match(r"^\s*(\d+)/(\d+)\b", str(message))
            if not match:
                return
            self.jobs[key]["stage"] = max(
                int(self.jobs[key].get("stage") or 0), int(match.group(1)),
            )
            self.jobs[key]["stage_total"] = int(match.group(2))

    def _append_job_log(self, log_path: str, line: str) -> None:
        """Anade una linea al log del job. Si no se puede, no es motivo de fallo."""
        if not log_path:
            return
        try:
            with Path(log_path).open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} | {line}\n")
        except OSError:
            pass

    def _announce_job(self, key: str) -> None:
        """Deja en la campana del manager lo que acaba de pasar con el cálculo.

        Generar, arreglar o mejorar dura horas y acaba sin nadie delante: el
        aviso es lo que convierte «ya está» en algo de lo que el usuario se
        entera, en la campana y en el escritorio.

        Nunca puede tumbar el trabajo que acaba de terminar. Un fallo al
        escribir el diario dentro de `_mark_job_completed` saldría por el
        `except` del worker y marcaría como fallido un cálculo correcto, así
        que aquí no se escapa nada.
        """
        store = self.notifications
        if store is None:
            return
        node_id, _, scope = key.rpartition(":")
        with self.lock:
            job = dict(self.jobs.get(key) or {})
        try:
            notice = job_notice(scope, job)
            if notice is None:
                return
            store.add(
                notice["level"], notice["title"], notice["message"],
                node_id=node_id,
                node_name=str((self.nodes.get(node_id) or {}).get("name") or node_id),
                portfolio_id=notice["portfolio_id"], kind=notice["kind"],
                link=notice_link(scope, node_id), link_text=notice["link_text"],
            )
        except Exception:
            pass

    def _mark_job_stopped(self, key: str) -> None:
        """Deja el job como detenido por el usuario y lo anota en su log."""
        with self.lock:
            job = self.jobs.get(key) or {}
            job.update({
                "status": "stopped", "finished_at": utc_now(), "error": None,
                "progress": "Cálculo detenido por el usuario",
            })
            log_path = str(job.get("log_path") or "")
        self._append_job_log(log_path, "DETENIDO por el usuario")
        self._announce_job(key)

    def _mark_job_failed(
        self,
        key: str,
        exc: Exception,
        node_id: str,
        operation: str,
        portfolio_id: int | None,
    ) -> None:
        """Deja el job como fallido, lo anota y avisa al nodo."""
        with self.lock:
            self.jobs[key].update({
                "status": "failed", "finished_at": utc_now(),
                "error": str(exc), "progress": "Error",
            })
            log_path = str(self.jobs[key].get("log_path") or "")
        self._append_job_log(log_path, f"ERROR · {exc}")
        try:
            PortfolioSource(self._node(node_id)).notify(
                f"Portfolio Builder {operation} fallido"
                + (f" para #{portfolio_id}" if portfolio_id else "")
                + f": {exc}"
            )
        except Exception:
            pass
        self._announce_job(key)

    def _job_log_path(self, key: str, source: Any, scope: str, operation: str) -> Path:
        """El log del trabajo: el que dejo preparado quien lo encolo, o uno nuevo."""
        with self.lock:
            prepared_log_path = str(self.jobs[key].get("log_path") or "")
        if prepared_log_path:
            return Path(prepared_log_path)
        log_dir = source.project / "portfolio_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"manager_{scope}_{operation}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        with self.lock:
            self.jobs[key]["log_path"] = str(log_path)
        return log_path

    def _mark_job_completed(
        self, key: str, availability: dict[str, Any], proposals: list[dict[str, Any]],
    ) -> None:
        with self.lock:
            self.proposals[key] = proposals
            stage_total = int(self.jobs[key].get("stage_total") or 0)
            self.jobs[key].update({
                "status": "completed", "finished_at": utc_now(), "progress": "Propuestas listas",
                "availability": availability, "proposal_count": len(proposals),
                "stage": stage_total or self.jobs[key].get("stage", 0),
            })
        self._announce_job(key)

    def state(self, node_id: str, scope: str) -> dict[str, Any]:
        status = self.task_state(node_id, scope)
        key = self._key(node_id, scope)
        job = status["job"]
        active_task = status["task"]
        tasks = status["tasks"]
        with self.lock:
            proposals = list(self.proposals.get(key) or [])
        previous_members = list(job.get("previous_members") or [])
        settings = self.settings_for(node_id, scope)
        proposal_payloads: list[dict[str, Any]] = []
        for proposal in proposals:
            result: PortfolioResult = proposal["result"]
            proposal_inputs = proposal.get("inputs") if isinstance(proposal.get("inputs"), dict) else settings
            variant_members = [
                member for member in previous_members
                if str(member.get("variant_key") or "") == str(proposal.get("key") or "")
            ]
            before = variant_members if variant_members else previous_members
            diff = proposal_diff(before, result)
            result_data = result_payload(result)
            nominal_valley = float(proposal_inputs.get("capital") or 0) * float(proposal_inputs.get("valley_dd_pct") or 0) / 100.0
            nominal_margin = max(nominal_valley - result.actual_valley_dd, 0.0)
            result_data.update({
                "nominal_valley_dd": nominal_valley,
                "nominal_valley_margin": nominal_margin,
                "nominal_valley_margin_pct": nominal_margin / max(nominal_valley, 1e-9) * 100.0,
                "changed_allocations": sum(row["state"] != "SIN CAMBIO" for row in diff),
            })
            proposal_payloads.append({
                "key": proposal["key"], "label": proposal["label"], "reserve_pct": proposal["reserve_pct"],
                "auto_adjusted_valley": bool(proposal.get("auto_adjusted_valley", False)),
                "requested_valley_dd_pct": float(
                    proposal.get("requested_valley_dd_pct")
                    or settings.get("valley_dd_pct")
                    or 0
                ),
                "adjusted_valley_dd_pct": float(
                    proposal.get("adjusted_valley_dd_pct")
                    or proposal_inputs.get("valley_dd_pct")
                    or 0
                ),
                "result": result_data, "diff": diff,
            })
        inventory = self.inventory(node_id, scope, settings)
        return {
            "settings": settings,
            "job": job,
            "task": active_task,
            "tasks": tasks[-10:],
            "inventory": inventory,
            "proposals": proposal_payloads,
        }

    def task_state(self, node_id: str, scope: str) -> dict[str, Any]:
        self._node(node_id)
        key = self._key(node_id, scope)
        with self.lock:
            job = dict(self.jobs.get(key) or {"status": "idle"})
            tasks = [dict(task) for task in self.tasks.get(key, [])]
        active_task = next(
            (task for task in tasks if task.get("status") in {"pending", "running"}),
            tasks[-1] if tasks else {"status": "idle"},
        )
        return {"job": job, "task": active_task, "tasks": tasks[-10:]}
