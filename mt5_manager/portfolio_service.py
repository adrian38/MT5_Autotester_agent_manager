from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
import zlib
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    CandidateFunnel,
    optimizer_overrides,
    SearchLimits,
    SearchPlan,
    ACCOUNT_LEVERAGE_CHOICES,
    DEFAULT_ACCOUNT_LEVERAGE,
    MIN_RECENT_EQUITY_RECOVERY,
    BootstrapDrawdownAnalysis,
    OptimizationDecision,
    PortfolioResult,
    PortfolioType,
    PortfolioCalculationCancelled,
    StrategyAllocation,
    UnusedSetInfo,
    bootstrap_valley_drawdown,
    filter_eligible_sets,
    load_max_product_leverage,
    load_symbol_notional,
    load_symbol_notional_from_specs,
    load_symbol_specs,
    load_unmeasured_symbols,
    margin_model_for_profile,
    normalize_margin_profile,
    evaluate_portfolio,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    optimize_portfolio,
    portfolio_display_symbol,
    portfolio_group_key,
    portfolio_group_summary,
    portfolio_symbol_key,
    set_portfolio_cancellation_check,
    slice_strategy_sets_to_month,
    summarize_robust_rows,
    validate_strict_monthly_portfolio,
)
from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.mt5_report import StrategyReport, parse_report

from . import candidate_verdict, dev_branch, portfolio_import
from .common import load_json, safe_float, safe_int, save_json, utc_now
from .portfolio_scope import PORTFOLIO_SCOPES, SCOPE_LABELS, normalize_portfolio_scope
# Reexportados a proposito: el esquema salio a su propio modulo, pero los
# llamantes (y los tests) lo siguen importando desde aqui.
from .portfolio_schema import (  # noqa: F401
    PORTFOLIO_SCHEMA,
    _ensure_column,
    _has_column,
    _table_exists,
    ensure_portfolio_schema,
)
from .portfolio_identity import (  # noqa: F401
    IMPROVEMENT_PRIORITY_LABELS,
    LOCKED_VARIANTS,
    PORTFOLIO_TYPES,
    TYPE_LABELS,
    _is_bundle_portfolio,
    _normalize_memory_row,
    _normalized_improvement_lineage,
    _portable_portfolio_uid,
    _resolve_source_path,
    _stored_path_name,
    _valid_portfolio_uid,
    normalize_portfolio_alias,
)
from .portfolio_antifiller import (  # noqa: F401
    STANDARD_ANTIFILLER_REFILL_PASSES,
    _optimize_without_recent_fillers,
    _underrepresented_recent_allocation_ids,
)
from .portfolio_generation_search import (  # noqa: F401
    EXPERIMENTAL_WARNING_PREFIXES,
    _LockedComposition,
    _annotate_locked_proposals,
    _base_optimizer,
    _locked_composition,
    _locked_full_proposals,
    _locked_sets_from,
    _locked_variant_proposal,
    _normal_proposals,
    _optimizer_kwargs,
    _optimizer_limits,
    _reserve_pct,
    _seasonal_coverage,
)
from .portfolio_generation import (  # noqa: F401
    _bundle_proposals,
    _eligible_generation_rows,
    _loaded_generation_sets,
    _require_eligible_sets,
    build_margin_model,
    filter_rows_by_disabled_symbols,
    generate_proposals,
)
from .portfolio_completion import (  # noqa: F401
    _completion_filtered_rows,
    _completion_optimizer_kwargs,
    _completion_proposal,
    _completion_required_sets,
    generate_completion_proposal,
)
from .portfolio_proposals import (  # noqa: F401
    LEGACY_ALLOCATION_RISK_FIELDS,
    LEGACY_RESULT_RISK_FIELDS,
    _proposal_metrics,
    _saved_request_portfolio_id,
    _supported_dataclass_values,
    deserialize_portfolio_proposals,
    legacy_compatible_portfolio_save_payload,
    proposal_diff,
    replace_saved_proposal,
    save_portfolio_payload,
    serialize_portfolio_proposals,
)
from .portfolio_valley_floor import (  # noqa: F401
    MAX_VALLEY_FLOOR_ATTEMPTS,
    _adjusted_valley_pcts,
    _proposals_are_empty,
    _with_executable_valley_floor,
    describe_eligibility,
    eligibility_counts,
    strategy_unit_risk,
)
from .portfolio_saved import (  # noqa: F401
    SAVED_INPUT_FALLBACKS,
    _allocation_source_rows,
    _annotate_improvement_lineage,
    _blank_recalculated_portfolio,
    _migrated_asset_groups,
    _recalculated_metrics,
    _saved_portfolio_row,
    _saved_portfolio_type,
    _saved_risk_targets,
    _stored_metrics,
    _update_recalculated_row,
)
from .portfolio_source_connection import (  # noqa: F401
    BROKER_ACCOUNT_TYPES,
    REMOTE_SNAPSHOT_LOCK,
    WAL_UNSUPPORTED_FILESYSTEMS,
    PortfolioSourceConnectionMixin,
    _linux_path_needs_snapshot,
)
from .portfolio_coordinator_saved import PortfolioCoordinatorSavedMixin
from .portfolio_source import PortfolioSource
from .portfolio_source_inventory import PortfolioSourceInventoryMixin
from .portfolio_source_quarantine import PortfolioSourceQuarantineMixin
from .portfolio_source_saved import PortfolioSourceSavedMixin
from .portfolio_source_reports import PortfolioSourceReportsMixin
from .portfolio_report_cache import cached_report  # noqa: F401
from .portfolio_import_build import (  # noqa: F401
    _ImportContext,
    _imported_target_month,
    _resolve_import_members,
    _variant_proposal,
    build_import_proposals,
)
from .portfolio_persistence import (  # noqa: F401
    RUNTIME_ONLY_INPUT_KEYS,
    _insert_allocation,
    _insert_decisions,
    _result_metrics,
    result_payload,
    save_proposal,
    settings_inputs,
)
from .portfolio_settings import (  # noqa: F401
    ASSET_GROUPS,
    BOOLEAN_SETTINGS,
    COMMON_DEFAULTS,
    CORRELATION_KEYS,
    MONTHLY_DEFAULTS,
    _optional_corr,
    _optional_int,
    normalize_settings,
)
from .portfolio_transfer import (  # noqa: F401
    _sql_when,
    _copy_exported_sets,
    _export_folder,
    _export_summary_lines,
    _import_candidate_sql,
    _imported_candidate,
)
from .portfolio_full_experimental import optimize_experimental_full_portfolio
from .stage_reports import recover_robustness_report


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








def _run_portfolio_operation(
    source: PortfolioSource,
    scope: str,
    operation: str,
    portfolio_id: int | None,
    settings: dict[str, Any],
    progress: Callable[[str], None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """El motor que corresponde al ambito y a la operacion pedida.

    Los imports son locales a proposito: cada ambito arrastra su modulo y no
    tiene por que cargarse para atender a los otros dos.
    """
    if scope == "monthly":
        from .portfolio_monthly_service import run_monthly_operation

        return run_monthly_operation(source, operation, portfolio_id, settings, progress)
    if scope == "grid":
        from .portfolio_grid_service import run_grid_operation

        return run_grid_operation(source, operation, portfolio_id, settings, progress)
    if operation == "improve":
        if portfolio_id is None:
            raise ValueError("Falta el portafolio cuya base se quiere mejorar")
        # Dos motores en dos ficheros: base y cadena. La decision la toma la
        # genealogia del destino, no el nombre ni esta rama.
        from .portfolio_improvement_dispatch import run_full_history_improvement

        return run_full_history_improvement(source, portfolio_id, settings, progress)
    if operation == "complete":
        if portfolio_id is None:
            raise ValueError("Falta el portafolio que se quiere completar")
        return generate_completion_proposal(
            source, portfolio_id, scope, settings, progress,
        )
    return generate_proposals(
        source, settings, progress,
        exclude_portfolio_id=portfolio_id if operation == "reoptimize" else None,
        lock_portfolio_type=_reoptimize_locked_type(source, scope, operation, portfolio_id, settings),
    )


def _reoptimize_locked_type(
    source: PortfolioSource,
    scope: str,
    operation: str,
    portfolio_id: int | None,
    settings: dict[str, Any],
) -> PortfolioType | None:
    """Reoptimizar una fila de una sola variante fija su tipo; un paquete no."""
    if operation != "reoptimize" or portfolio_id is None:
        return None
    saved = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
    if _is_bundle_portfolio(saved):
        return None
    return PORTFOLIO_TYPES[str(settings["portfolio_type"])]


class PortfolioCoordinator(PortfolioCoordinatorSavedMixin):
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
        settings = self.update_settings(node_id, scope, changes)
        return self._start_job(node_id, scope, settings, "generate", None, [])

    def _start_job(
        self,
        node_id: str,
        scope: str,
        settings: dict[str, Any],
        operation: str,
        portfolio_id: int | None,
        previous_members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        key = self._key(node_id, scope)
        with self.lock:
            if (self.jobs.get(key) or {}).get("status") in {"running", "stopping"}:
                raise ValueError("Ya hay un cálculo de portafolio en curso")
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
        if operation not in {"reoptimize", "complete", "improve"}:
            raise ValueError("Operación guardada desconocida")
        source = self._persistence_source(node_id, scope)
        detail = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        settings = source.saved_inputs(portfolio_id, scope)
        if changes:
            settings.update(changes)
            settings = normalize_settings(scope, settings, source.broker)
        return self._start_job(node_id, scope, settings, operation, portfolio_id, list(detail.get("members") or []))

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

    def _worker(
        self, node_id: str, scope: str, settings: dict[str, Any], operation: str = "generate", portfolio_id: int | None = None
    ) -> None:
        key = self._key(node_id, scope)
        with self.lock:
            cancellation_event = self.cancellation_events.get(key)

        def cancellation_requested() -> bool:
            return bool(cancellation_event and cancellation_event.is_set())

        def raise_if_cancelled() -> None:
            if cancellation_requested():
                raise PortfolioCalculationCancelled("Cálculo de portafolio detenido por el usuario")

        previous_cancellation_check = None
        if scope == "full_history":
            previous_cancellation_check = set_portfolio_cancellation_check(cancellation_requested)

        try:
            raise_if_cancelled()
            source = self._calculation_source(node_id, scope)
            log_path = self._job_log_path(key, source, scope, operation)

            def logged_progress(message: str) -> None:
                raise_if_cancelled()
                self._record_job_progress(key, message)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{datetime.now().isoformat(timespec='seconds')} | {message}\n")

            availability, proposals = _run_portfolio_operation(
                source, scope, operation, portfolio_id, settings, logged_progress,
            )
            raise_if_cancelled()
            self._mark_job_completed(key, availability, proposals)
            source.notify(
                f"Portfolio Builder {operation} listo en {source.broker}/{source.account}: "
                f"{len(proposals)} propuesta(s)" + (f" para portafolio #{portfolio_id}" if portfolio_id else "")
            )
        except PortfolioCalculationCancelled:
            self._mark_job_stopped(key)
        except Exception as exc:
            if cancellation_requested():
                # El motor no siempre alcanza a lanzar la cancelacion antes de
                # fallar por lo que la cancelacion misma provoco.
                self._mark_job_stopped(key)
                return
            self._mark_job_failed(key, exc, node_id, operation, portfolio_id)
        finally:
            if scope == "full_history":
                set_portfolio_cancellation_check(previous_cancellation_check)
            with self.lock:
                if self.cancellation_events.get(key) is cancellation_event:
                    self.cancellation_events.pop(key, None)

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


    def _save_imported_ubs_proposals(
        self,
        node_id: str,
        scope: str,
        proposals: list[dict[str, Any]],
        selected_key: str,
    ) -> int:
        """Guarda una importacion UBS en la memoria local de su nodo."""
        if normalize_portfolio_scope(scope) != "full_history":
            raise ValueError("El guardado importado por nodo solo pertenece a Portafolio UBS")
        request_id = str(uuid.uuid4())
        save_payload = {
            "scope": scope,
            "selected_key": selected_key,
            "operation": "generate",
            "portfolio_id": None,
            "request_id": request_id,
            "proposals": serialize_portfolio_proposals(proposals, request_id),
        }
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            value = save_portfolio_payload(PortfolioSource(node), save_payload)
        else:
            status, value = self._post_to_node(
                node, "/api/v1/portfolios/save", save_payload, timeout=120
            )
            error_text = str(value.get("error") if isinstance(value, dict) else value or "")
            if status >= 400 and "unexpected keyword argument" in error_text:
                status, value = self._post_to_node(
                    node,
                    "/api/v1/portfolios/save",
                    legacy_compatible_portfolio_save_payload(save_payload),
                    timeout=120,
                )
            if status == 404:
                raise ValueError(
                    "El nodo todavía no admite guardado local de portafolios; "
                    "actualiza su código y reinícialo."
                )
            if status >= 400 or not isinstance(value, dict):
                error = value.get("error") if isinstance(value, dict) else value
                raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        if not isinstance(value, dict):
            raise ValueError("El guardado del portafolio importado devolvió una respuesta inválida")
        portfolio_id = safe_int(value.get("portfolio_id"), 0)
        if portfolio_id <= 0 or str(value.get("request_id") or "") != request_id:
            raise ValueError("El nodo no confirmó correctamente el portafolio importado")
        return portfolio_id

    def import_portfolio(self, node_id: str, scope: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Recrea un portafolio guardado desde una carpeta o ZIP de exportación.

        En Portafolio UBS el manager reconstruye las propuestas y las persiste
        con el endpoint de guardado del nodo: es el proceso que tiene la memoria
        WAL en local. Los demás ámbitos conservan sin cambios su camino previo.
        """
        scope = normalize_portfolio_scope(scope)
        source_path = str(payload.get("folder") or payload.get("path") or "").strip()
        archive = payload.get("archive")
        if archive:
            header, members, set_files = self._read_uploaded_export(archive, str(payload.get("filename") or ""))
        elif source_path:
            header, members, set_files = portfolio_import.read_export(source_path)
        else:
            raise ValueError("Falta la carpeta o el ZIP del portafolio exportado")
        calculation = self._calculation_source(node_id, scope)
        proposals, selected_key, report = build_import_proposals(calculation, scope, header, members)
        if scope == "full_history":
            portfolio_id = self._save_imported_ubs_proposals(
                node_id, scope, proposals, selected_key
            )
        else:
            portfolio_id = save_proposal(
                self._persistence_source(node_id, scope), proposals, selected_key, scope
            )
        missing_files = sorted({member.set_name for member in members} - set(set_files))
        self.invalidate_after_exclusion(node_id)
        return {
            "portfolio_id": portfolio_id,
            "scope": scope,
            "name": str(header.get("name") or ""),
            "missing_set_files": missing_files,
            **report,
        }

    @staticmethod
    def _read_uploaded_export(archive: Any, filename: str) -> tuple[dict[str, Any], list[Any], list[str]]:
        """El ZIP llega en base64 cuando el manager no puede abrir un diálogo local.

        Es el reflejo de la exportación: con `export_mode=folder` el manager abre
        el selector nativo, y con `download` el navegador se descarga el ZIP. La
        importación tiene que aceptar las dos formas o queda inservible en uno de
        los dos despliegues.
        """
        import base64

        try:
            content = base64.b64decode(str(archive), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("El archivo subido no es un ZIP válido") from exc
        suffix = ".zip" if not filename.lower().endswith(".zip") else Path(filename).suffix
        with tempfile.TemporaryDirectory(prefix="mt5-portfolio-upload-") as temp_dir:
            target = Path(temp_dir) / (Path(filename).name or f"portafolio{suffix}")
            target.write_bytes(content)
            return portfolio_import.read_export(target)

    def export(self, node_id: str, scope: str, portfolio_id: int, destination: str | None) -> dict[str, Any]:
        return self._persistence_source(node_id, scope).export_portfolio(portfolio_id, scope, destination)

    def export_archive(self, node_id: str, scope: str, portfolio_id: int) -> dict[str, Any]:
        source = self._persistence_source(node_id, scope)
        with tempfile.TemporaryDirectory(prefix="mt5-portfolio-export-") as temp_dir:
            result = source.export_portfolio(portfolio_id, scope, temp_dir)
            output = Path(str(result["folder"]))
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(output.rglob("*")):
                    if path.is_file():
                        archive.write(path, Path(output.name) / path.relative_to(output))
            return {
                "filename": f"{output.name}.zip",
                "content": buffer.getvalue(),
                "exported": int(result.get("exported") or 0),
                "missing": list(result.get("missing") or []),
            }

    def symbol_sets(self, node_id: str, scope: str, symbol: str) -> dict[str, Any]:
        # Los ajustes son los que dibujaron la fila del inventario desde la que
        # se abre la ventana: sin ellos la tabla no cuadra con su propio total.
        return self._calculation_source(node_id, scope).symbol_sets(
            symbol, scope, self.settings_for(node_id, scope),
        )

    def export_symbol(
        self, node_id: str, scope: str, symbol: str, selected_paths: Any, destination: str | None
    ) -> dict[str, Any]:
        if normalize_portfolio_scope(scope) != "full_history":
            raise ValueError("La exportación por símbolo solo está disponible en Portafolio UBS")
        return self._calculation_source(node_id, scope).export_symbol_sets(
            symbol, selected_paths, destination, self.settings_for(node_id, scope),
        )

    def export_symbol_archive(
        self, node_id: str, scope: str, symbol: str, selected_paths: Any
    ) -> dict[str, Any]:
        source = self._calculation_source(node_id, scope)
        with tempfile.TemporaryDirectory(prefix="mt5-symbol-export-") as temp_dir:
            result = source.export_symbol_sets(
                symbol, selected_paths, temp_dir, self.settings_for(node_id, scope),
            )
            output = Path(str(result["folder"]))
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(output.iterdir()):
                    if path.is_file():
                        archive.write(path, Path(output.name) / path.name)
            return {
                "filename": f"{output.name}.zip",
                "content": buffer.getvalue(),
                "exported": int(result.get("exported") or 0),
                "missing": list(result.get("missing") or []),
            }

    def open_report(self, node_id: str, scope: str, portfolio_id: int, set_path: str) -> dict[str, Any]:
        return self._persistence_source(node_id, scope).open_member_report(portfolio_id, scope, set_path)

    def export_member_reports_archive(
        self, node_id: str, scope: str, portfolio_id: int, set_path: str
    ) -> dict[str, Any]:
        return self._persistence_source(node_id, scope).export_member_reports_archive(
            portfolio_id, scope, set_path
        )

    def log(self, node_id: str, scope: str, lines: int = 500) -> dict[str, Any]:
        key = self._key(node_id, scope)
        with self.lock:
            job = dict(self.jobs.get(key) or {})
        raw_path = str(job.get("log_path") or job.get("last_log_path") or "")
        if not raw_path:
            raise ValueError("Todavía no hay un log de cálculo para este constructor")
        source = PortfolioSource(self._node(node_id))
        log_root = (source.project / "portfolio_logs").resolve()
        path = Path(raw_path).resolve()
        try:
            path.relative_to(log_root)
        except ValueError as exc:
            raise ValueError("La ruta del log no pertenece al proyecto") from exc
        if not path.is_file():
            raise ValueError("El archivo de log ya no existe")
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        limit = min(max(int(lines), 1), 5000)
        return {"path": str(path), "lines": content[-limit:]}
