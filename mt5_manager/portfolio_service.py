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
from .portfolio_coordinator_core import (
    PortfolioCoordinatorCoreMixin,
    prepare_scope_log,
    scope_stage_count,
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


class PortfolioCoordinator(PortfolioCoordinatorCoreMixin, PortfolioCoordinatorSavedMixin):

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
