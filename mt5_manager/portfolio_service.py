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


BROKER_ACCOUNT_TYPES = {"ROBOFOREX": ("ECN", "PRO"), "ICTRADING": ("STANDARD",), "AXI": ("STANDARD", "PREMIUM")}
REMOTE_SNAPSHOT_LOCK = threading.RLock()


#: Sistemas de ficheros que no pueden respaldar el indice en memoria compartida
#: (``-shm``) que SQLite necesita para leer una base en modo WAL. Incluye tanto
#: recursos de red como los bind mounts de Docker Desktop (9p, virtiofs,
#: gRPC-FUSE): en todos ellos un ``?mode=ro`` sobre una base con WAL falla con
#: "disk I/O error", asi que hay que copiarla a un disco que si lo soporte.
WAL_UNSUPPORTED_FILESYSTEMS = frozenset(
    {"cifs", "smb3", "nfs", "nfs4", "9p", "virtiofs", "fuse", "fuse.grpcfuse", "fuseblk"}
)


def _linux_path_needs_snapshot(path: Path, mounts_text: str) -> bool:
    """True si hay que copiar la base a otro disco antes de leerla.

    El criterio no es "esta en red", es "este sistema de ficheros no soporta el
    ``-shm`` del modo WAL". Confundir ambas cosas costo caro: los bind mounts de
    Docker (9p) se daban por locales, se leian con ``immutable=1`` y eso ignora
    el ``-wal`` entero, con lo que el manager seguia viendo filas que el nodo ya
    habia borrado.
    """
    target = str(path).replace("\\", "/")
    matched: tuple[int, str] | None = None
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mountpoint = fields[1].replace("\\040", " ").replace("\\134", "\\")
        prefix = mountpoint.rstrip("/") + "/"
        if target == mountpoint or target.startswith(prefix):
            candidate = (len(mountpoint), fields[2].lower())
            if matched is None or candidate[0] > matched[0]:
                matched = candidate
    if not matched:
        return False
    fstype = matched[1]
    return fstype in WAL_UNSUPPORTED_FILESYSTEMS or fstype.startswith("fuse.")


def _pool_symbol(row: dict[str, Any]) -> Any:
    """El simbolo con el que el inventario cuenta esta fila."""
    return row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol")


def _inventory_visible_rows(
    rows: list[dict[str, Any]],
    symbol_of: Callable[[dict[str, Any]], Any],
    settings: dict[str, Any],
    universe: Any,
) -> list[dict[str, Any]]:
    """Los mismos descartes que aplica ``inventory`` a la fila de la que se abre.

    Sin esto la ventana ensenaba 84 sets de DE40 frente a los 61 de la fila: el
    pool crudo no sabe nada de ``grid_off`` ni de ``allowed_asset_groups``.
    """
    allowed_groups = set(settings.get("allowed_asset_groups") or ASSET_GROUPS)
    kept = [
        row for row in rows
        if portfolio_group_key(
            str(symbol_of(row) or ""), universe_files=[universe]
        ) in allowed_groups
    ]
    if bool(settings.get("grid_off")):
        kept, _ = filter_rows_grid_off(kept)
    return kept


def _symbol_set_row(
    row: dict[str, Any],
    path: str,
    display_symbol: str,
    quarantined: dict[str, Any] | None,
    used: bool,
) -> dict[str, Any]:
    """Fila del inventario de una familia, con su estado ya resuelto.

    ``candidate_rows`` ya exige las cuatro etapas aceptadas: aqui el estado solo
    depende de la cuarentena y de si el set esta asignado.
    """
    if quarantined:
        state, state_label = "excluded", str(quarantined.get("reason_label") or "Excluido")
    elif used:
        state, state_label = "used", "Usado en portafolio"
    else:
        state, state_label = "available", "Disponible"
    return {
        "candidate_id": row.get("candidate_id"),
        "set_path": path,
        "set_name": Path(path).name,
        "symbol": display_symbol,
        "timeframe": row.get("period") or "",
        "family": row.get("family") or "",
        "account": row.get("account_type") or "",
        "state": state,
        "state_label": state_label,
        "quarantine_key": str(quarantined.get("quarantine_key") or "") if quarantined else "",
        "reason_code": (
            candidate_verdict.normalize_reason_code(quarantined.get("reason_code"))
            if quarantined else ""
        ),
        "exists": Path(path).is_file(),
    }


def _quarantined_set_row(
    quarantined: dict[str, Any], path: str, display_symbol: str,
) -> dict[str, Any]:
    """Fila de un set en cuarentena que ya no aparece en el inventario vivo."""
    return {
        "candidate_id": quarantined.get("candidate_id"),
        "set_path": path,
        "set_name": Path(path).name,
        "symbol": display_symbol,
        "timeframe": quarantined.get("timeframe") or "",
        "family": "",
        "account": (
            quarantined.get("source_account") or quarantined.get("account_type") or ""
        ),
        "state": "excluded",
        "state_label": str(quarantined.get("reason_label") or "Excluido"),
        "quarantine_key": str(quarantined.get("quarantine_key") or ""),
        "reason_code": candidate_verdict.normalize_reason_code(quarantined.get("reason_code")),
        "exists": Path(path).is_file(),
    }


"""Con que se reconstruye una cartera guardada antes de que existiera
``metrics.inputs``. No son los defaults del formulario: `COMMON_DEFAULTS` puede
cambiar con el producto y esto tiene que seguir describiendo el calculo de
entonces."""


def _accepted_candidate_sql(conn: sqlite3.Connection) -> str | None:
    """El pool elegible: las cuatro etapas aceptadas, o ``None`` si no aplica.

    Elegibilidad = haber superado el Final Tick 6M. El estado del final tick
    corto se acepta tambien como 'pending_ohlc_trades': es terminal para esa
    etapa (la probe OHLC de 1 mes no genero operaciones, no es un rechazo de la
    estrategia) y el propio pipeline lo trata como paso valido hacia 6M
    (node.py: probe_ft.status in ('accepted','pending_ohlc_trades')). Exigir
    'accepted' aqui dejaba fuera candidatos ya aceptados en 6M junto con sus
    simbolos completos. Esas filas llegan sin full_history_report_path, que es
    opcional en ubs_portfolio (require_full_history nunca se activa desde el
    manager): entran apoyadas en IS + OOS + 6M, sin el tramo continuo.
    """
    candidate_tables = {
        "candidates", "candidate_robustness",
        "candidate_final_tick", "candidate_final_tick_6m",
    }
    if not all(_table_exists(conn, table) for table in candidate_tables):
        # A manager-owned Grid memory stores portfolios only. It is
        # intentionally part of memory_sources for used-set and correlation
        # lookups, never as a candidate source.
        return None
    final_tick_metrics_sql = _sql_when(
        _has_column(conn, "candidate_final_tick_6m", "real_tick_metrics_json"),
        "ft6.real_tick_metrics_json",
        "null",
    )
    return f"""
        select ? as account_type, ? || ':' || c.id as candidate_id,
               c.id as source_candidate_id, c.set_path, c.symbol, c.target_symbol,
               c.period, c.family, c.report_path as is_report_path,
               cr.report_path as oos_report_path,
               ft.real_tick_report_path as full_history_report_path,
               ft6.ohlc_report_path as final_ohlc_report_path,
               ft6.real_tick_report_path as final_tick_report_path,
               ft6.from_date as final_tick_from_date, ft6.to_date as final_tick_to_date,
               {final_tick_metrics_sql} as final_tick_metrics_json
        from candidates c join candidate_robustness cr on cr.candidate_id=c.id
        join candidate_final_tick ft on ft.candidate_id=c.id
        join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
        where c.status='accepted' and cr.status='accepted'
        and ft.status in ('accepted','pending_ohlc_trades')
        and ft6.status='accepted'
        order by c.id
        """


@dataclass(frozen=True)
class _InventoryKeys:
    """Claves de ruta y de simbolo que restan disponibilidad en el inventario."""

    quarantined: set[str]
    used: set[str]
    disabled: set[str]


class PortfolioSource(PortfolioSourceReportsMixin):
    def __init__(self, node: dict[str, Any]) -> None:
        self.node = node
        project_value = str(node.get("portfolio_project_dir") or "").strip()
        if not project_value:
            raise ValueError("El nodo no tiene portfolio_project_dir configurado en manager.json")
        # Preserve mapped drive letters on Windows. Resolving X:/Y: to UNC
        # breaks SQLite's read-only URI handling and can also make SMB locking
        # unnecessarily expensive while a remote agent is writing the DB.
        self.project = Path(project_value).expanduser().absolute()
        if not self.project.is_dir():
            raise ValueError(f"No existe el proyecto de portafolio: {self.project}")
        self.broker = str(node.get("portfolio_broker") or "ICTRADING").strip().upper()
        self.account = str(node.get("portfolio_account_type") or "STANDARD").strip().upper()
        memory_value = str(node.get("portfolio_memory_path") or "").strip()
        self.memory = Path(memory_value).expanduser().absolute() if memory_value else (
            self.project / "outputs" / f"ubs_memory_{self.broker}_{self.account}.sqlite"
        )
        configured_memories = self.node.get("portfolio_memory_paths")
        memory_sources: list[tuple[str, Path]] = []
        if isinstance(configured_memories, list):
            for item in configured_memories:
                if isinstance(item, dict):
                    account = str(item.get("account_type") or "").strip().upper()
                    path_value = str(item.get("path") or "").strip()
                    if account and path_value:
                        path = Path(path_value).expanduser().absolute()
                        if path.is_file():
                            memory_sources.append((f"{self.broker}/{account}", path))
        if not memory_sources:
            for account in BROKER_ACCOUNT_TYPES.get(self.broker, (self.account,)):
                path = self.project / "outputs" / f"ubs_memory_{self.broker}_{account}.sqlite"
                if path.is_file():
                    memory_sources.append((f"{self.broker}/{account}", path.absolute()))
        active_label = f"{self.broker}/{self.account}"
        memory_sources = [(label, path) for label, path in memory_sources if path != self.memory]
        self.memory_sources = [(active_label, self.memory)] + memory_sources
        self.universe = self.project / "assets" / f"{self.broker.lower()}_assets.ini"
        # Especificaciones medidas en MT5 (lote minimo, contrato, tick value)
        # colapsadas en un factor por simbolo. El margen las invierte para conocer
        # el nocional real de una posicion. Puede no existir: el modelo cae
        # entonces en la estimacion por precio de reporte.
        self.normalization = self.project / "assets" / f"{self.broker.lower()}_normalization.json"
        # Volcado directo del terminal: margen por posicion minima, lote minimo y
        # tamano de contrato reales. Es la fuente buena del margen.
        self.symbol_specs = self.project / "assets" / f"{self.broker.lower()}_symbol_specs.json"
        # Topes de apalancamiento publicados por el broker, para simular una
        # cuenta con otro apalancamiento sin pasarse del maximo del producto.
        self.product_leverage = self.project / "assets" / f"{self.broker.lower()}_max_product_leverage.json"
        if not self.memory.is_file():
            raise ValueError(f"No existe la memoria UBS: {self.memory}")

    @contextlib.contextmanager
    def connect(self, *, write: bool = False):
        with self.connect_memory(self.memory, write=write) as conn:
            yield conn

    @staticmethod
    def _needs_snapshot_read(memory: Path) -> bool:
        """True si leer esta base exige copiarla antes a un disco con WAL."""
        if os.name != "nt":
            try:
                return _linux_path_needs_snapshot(memory, Path("/proc/mounts").read_text(encoding="utf-8"))
            except OSError:
                return False
        if not memory.drive:
            return str(memory).startswith("\\\\")
        try:
            import ctypes

            return ctypes.windll.kernel32.GetDriveTypeW(f"{memory.drive}\\") == 4  # DRIVE_REMOTE
        except (AttributeError, OSError):
            return False

    @classmethod
    def write_needs_node(cls, memory: Path) -> bool:
        """True si esta memoria solo puede escribirla el nodo del agente.

        El criterio es el mismo que decide copiar para leer, y no por casualidad:
        un sistema de ficheros que no soporta el `-shm` del modo WAL no sirve ni
        para leer ni para escribir. La diferencia es que leer tiene salida —una
        copia en otro disco— y escribir no: la escritura tiene que ir al original
        o no vale para nada. La unica salida es ejecutarla donde la base es local,
        es decir en el nodo del agente, igual que ya hacen la exclusion y el
        borrado (`node.py::exclude_portfolio_members`, `_delete_on_node`).
        """
        return cls._needs_snapshot_read(memory)

    @classmethod
    def _snapshot_root(cls) -> Path:
        """Directorio donde dejar las copias, en un disco que soporte WAL.

        ``runtime/`` es lo preferible porque persiste entre reinicios y se ve
        desde fuera, pero en el contenedor es otro bind mount 9p: copiar ahi
        reproduciria el mismo fallo que se intenta evitar. Cuando pasa eso se cae
        al temporal del contenedor, que vive en el overlay y si soporta el
        ``-shm``.
        """
        runtime = Path(__file__).resolve().parents[1] / "runtime" / "portfolio_snapshots"
        if not cls._needs_snapshot_read(runtime):
            return runtime
        return Path(tempfile.gettempdir()) / "mt5_manager_portfolio_snapshots"

    def _snapshot_path(self, memory: Path) -> Path:
        node_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(self.node.get("id") or self.broker))
        root = self._snapshot_root() / node_id
        root.mkdir(parents=True, exist_ok=True)
        return root / memory.name

    def _remote_read_snapshot(self, memory: Path) -> Path:
        target = self._snapshot_path(memory)
        metadata_path = target.with_name(target.name + ".snapshot.json")
        source_wal = Path(str(memory) + "-wal")
        target_wal = Path(str(target) + "-wal")
        target_shm = Path(str(target) + "-shm")

        source_stat = memory.stat()
        wal_stat = source_wal.stat() if source_wal.is_file() else None
        signature = {
            "source_size": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "wal_size": wal_stat.st_size if wal_stat else 0,
            "wal_mtime_ns": wal_stat.st_mtime_ns if wal_stat else 0,
        }
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                loaded = load_json(metadata_path)
                metadata = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError, json.JSONDecodeError):
                metadata = {}
        copied_at = safe_float(metadata.get("copied_at"), 0.0)
        if target.is_file() and (
            all(metadata.get(key) == value for key, value in signature.items())
            or time.time() - copied_at < 30.0
        ):
            return target

        suffix = f".tmp-{os.getpid()}-{threading.get_ident()}"
        temp = target.with_name(target.name + suffix)
        temp_wal = Path(str(temp) + "-wal")
        try:
            shutil.copy2(memory, temp)
            if source_wal.is_file():
                last_error: OSError | None = None
                for _attempt in range(3):
                    try:
                        shutil.copy2(source_wal, temp_wal)
                        last_error = None
                        break
                    except OSError as exc:
                        last_error = exc
                        time.sleep(0.1)
                if last_error is not None:
                    raise last_error
            target_shm.unlink(missing_ok=True)
            target_wal.unlink(missing_ok=True)
            os.replace(temp, target)
            if temp_wal.is_file():
                os.replace(temp_wal, target_wal)
            save_json(metadata_path, {**signature, "copied_at": time.time(), "source": str(memory)})
        finally:
            temp.unlink(missing_ok=True)
            temp_wal.unlink(missing_ok=True)
        return target

    def _invalidate_remote_snapshot(self, memory: Path) -> None:
        metadata_path = self._snapshot_path(memory).with_name(memory.name + ".snapshot.json")
        metadata_path.unlink(missing_ok=True)

    @contextlib.contextmanager
    def connect_memory(self, memory: Path, *, write: bool = False):
        snapshot = self._needs_snapshot_read(memory)
        source_memory = memory
        remote_lock = False
        conn: sqlite3.Connection | None = None
        try:
            if write:
                # Unico punto de escritura en la memoria de un agente: aqui se
                # aplica el limite de la rama de pruebas.
                dev_branch.assert_writable(memory, "memoria UBS")
                try:
                    conn = sqlite3.connect(memory, timeout=10 if snapshot else 30)
                    ensure_portfolio_schema(conn)
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        raise ValueError(
                            f"No se pudo guardar: {memory.name} está bloqueada por otro proceso "
                            "(por ejemplo, una generación activa). La propuesta sigue disponible; "
                            "inténtalo de nuevo cuando termine."
                        ) from exc
                    if snapshot:
                        # La lectura tiene salida —copiar la base a otro disco— y la
                        # escritura no: el original es el unico sitio valido. Sobre un
                        # recurso de red o un bind mount de Docker, abrir en modo WAL
                        # falla con "disk I/O error" porque no hay `-shm` que respalde
                        # el indice compartido. Un error crudo de SQLite no dice nada
                        # de eso; este si, y nombra la unica salida real: que escriba
                        # el nodo, que tiene la base en local.
                        raise ValueError(
                            f"No se pudo escribir en {memory.name}: está en un sistema de ficheros "
                            "que no soporta el índice en memoria compartida del modo WAL (recurso "
                            "de red o bind mount de Docker), así que esta memoria solo puede "
                            "escribirla el nodo del agente. Si la operación no pasa por el nodo, "
                            "hay que portarla a manager_node_runtime/."
                        ) from exc
                    raise
            elif snapshot:
                # Copiar base y WAL a un disco que soporte el ``-shm`` y leer alli.
                # Es el unico modo de ver lo que el nodo acaba de escribir: un
                # ``immutable=1`` sobre el original ignora el ``-wal`` entero, y con
                # el se perdian borrados y altas que aun no habian pasado a
                # checkpoint. Un portafolio borrado en el nodo seguia apareciendo en
                # la pantalla y cada reintento fallaba con "no existe".
                REMOTE_SNAPSHOT_LOCK.acquire()
                remote_lock = True
                memory = self._remote_read_snapshot(memory)
                conn = sqlite3.connect(memory.as_uri() + "?mode=ro", uri=True, timeout=5)
            else:
                conn = sqlite3.connect(memory.as_uri() + "?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if conn is not None:
                conn.close()
            if write and snapshot and conn is not None:
                self._invalidate_remote_snapshot(source_memory)
            if remote_lock:
                REMOTE_SNAPSHOT_LOCK.release()

    def candidate_rows(self, *, include_quarantined: bool) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                sql = _accepted_candidate_sql(conn)
                if sql is None:
                    continue
                rows = conn.execute(sql, (account_label, account_label)).fetchall()
            result.extend(
                _normalize_memory_row(dict(db_row), memory, self.project) for db_row in rows
            )
        if include_quarantined:
            return result
        return self._without_quarantined(result)

    def _without_quarantined(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Quita las estrategias en cuarentena comparando por clave de ruta.

        La tabla de cuarentena guarda el set_path *resuelto*, mientras que
        candidates.set_path trae el valor crudo del nodo Windows que lo genero
        (su letra de unidad, sus separadores). Compararlos en SQL no casaba
        nunca, asi que las estrategias excluidas reaparecian en cada generacion.
        Se filtra en Python con la misma normalizacion que usa `inventory`.
        """
        quarantined = {self._path_key(row.get("set_path")) for row in self.quarantine_rows()}
        if not quarantined:
            return rows
        return [row for row in rows if self._path_key(row.get("set_path")) not in quarantined]

    def import_candidate_rows(
        self,
        set_names: Iterable[str] | None = None,
        *,
        include_without_robustness: bool = False,
    ) -> list[dict[str, Any]]:
        """Devuelve candidatos reconstruibles sin volver a filtrar su veredicto.

        Un cálculo nuevo solo puede usar el pool que superó las cuatro etapas,
        y para eso existe :meth:`candidate_rows`. Una importación tiene otro
        contrato: el ZIP ya fija la composición que el usuario guardó. Si una
        reparación posterior cambió el veredicto de robustez o Final Tick, se
        conserva como información, pero no puede borrar una estrategia del
        paquete restaurado.

        Siguen siendo imprescindibles el candidato y sus informes base/OOS;
        ``load_robust_sets_from_rows`` nombrará cualquier informe ausente o
        ilegible en vez de inventar métricas.

        ``set_names`` acota el inventario a los ficheros que el ZIP realmente
        necesita. Sin él hay que preparar la memoria entera —70.065 candidatos
        en RoboForex— para resolver las 18 líneas de un resumen: cada fila sin
        robustez vigente cuesta además hasta dos ``is_file()`` buscando su
        informe histórico, y son decenas de miles contra el disco del agente.

        Una fila sin robustez vigente entra igual —de ahí el ``left join``—
        porque el agente puede borrar esa fila al degradar el veredicto y el
        informe sigue en ``reports/``. ``include_without_robustness`` amplía eso
        un paso más: admite una memoria que aún no tiene siquiera la tabla
        ``candidate_robustness``, lo que necesita la ventana de familia para
        listar sets que no han llegado a la etapa. Nunca se usa esa ampliación
        para reconstruir un portafolio.
        """
        wanted = (
            {_stored_path_name(name) for name in set_names if str(name or "").strip()}
            if set_names is not None else None
        )
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                sql = _import_candidate_sql(
                    conn, include_without_robustness=include_without_robustness
                )
                if sql is None:
                    continue
                rows = conn.execute(sql, (account_label, account_label)).fetchall()
            for db_row in rows:
                if wanted is not None and _stored_path_name(db_row["set_path"]) not in wanted:
                    continue
                result.append(_imported_candidate(dict(db_row), self.project, memory))
        return result

    @staticmethod
    def _path_key(value: Any) -> str:
        return str(Path(str(value or "")).expanduser()).replace("/", "\\").casefold()

    def _match_key(self, value: Any) -> str:
        """Normalise a stored path to the current project before comparing.

        Saved portfolios can hold set paths rooted at a *previous* deployment:
        a Docker container's ``/data/...``, another PC's ``C:\\Users\\...`` or a
        mapped drive ``X:\\...``. Comparing a freshly resolved request against a
        raw stored member/allocation path then never matched, so lookups raised
        "no se encontró la estrategia" and the exclusion/delete aborted while the
        portfolio stayed on screen. Resolving BOTH sides through
        ``_resolve_source_path`` collapses every historical root onto the
        manager's current project so the keys line up again.
        """
        return self._path_key(_resolve_source_path(value, self.project))

    def quarantine_rows(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolio_quarantine"):
                    continue
                rows = conn.execute("select * from portfolio_quarantine order by quarantined_at desc,id desc").fetchall()
            for row in rows:
                item = dict(row)
                item["quarantine_key"] = f"{account_label}|{item['id']}"
                item["source_account"] = account_label
                item["set_path"] = _resolve_source_path(item.get("set_path"), self.project)
                item["set_name"] = Path(str(item.get("set_path") or "")).name
                # El respaldo de etapas puede ocupar decenas de KB por fila y la
                # interfaz solo necesita saber si existe.
                item["restorable"] = bool(str(item.pop("restore_json", "") or "").strip())
                item["reason_code"] = candidate_verdict.normalize_reason_code(item.get("reason_code"))
                item["reason_label"] = candidate_verdict.REASON_LABELS[item["reason_code"]]
                result.append(item)
        return sorted(result, key=lambda item: (str(item.get("quarantined_at") or ""), int(item.get("id") or 0)), reverse=True)

    def _inventory_keys(
        self, monthly: bool, settings: dict[str, Any], quarantine: list[dict[str, Any]],
    ) -> _InventoryKeys:
        """Las tres razones por las que un set no cuenta como disponible."""
        used_paths: list[str] = []
        if monthly and settings.get("exclude_monthly_used"):
            used_paths = self.used_set_paths("monthly")
        elif not monthly and settings.get("exclude_used_sets", True):
            used_paths = self.used_set_paths("full_history")
        return _InventoryKeys(
            quarantined={self._path_key(row.get("set_path")) for row in quarantine},
            used={self._path_key(path) for path in used_paths},
            # El control de simbolos deshabilitados pertenece solo a UBS normal.
            disabled={
                portfolio_symbol_key(
                    portfolio_display_symbol(str(symbol), universe_files=[self.universe])
                )
                for symbol in settings.get("disabled_symbols") or []
            } if not monthly else set(),
        )

    def _symbol_inventory_counts(
        self, rows: list[dict[str, Any]], keys: _InventoryKeys, monthly: bool,
    ) -> list[dict[str, Any]]:
        """Por simbolo visible: cuantos hay, cuantos estorban y cuantos quedan."""
        by_symbol: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = portfolio_display_symbol(
                str(row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol") or ""),
                universe_files=[self.universe],
            )
            symbol_key = portfolio_symbol_key(symbol)
            counts = by_symbol.setdefault(symbol_key, {
                "symbol": symbol,
                "total": 0,
                "quarantined": 0,
                "used": 0,
                "available": 0,
                **({"disabled": symbol_key in keys.disabled} if not monthly else {}),
            })
            counts["total"] += 1
            key = self._path_key(row.get("set_path"))
            is_quarantined = key in keys.quarantined
            is_used = key in keys.used
            if is_quarantined:
                counts["quarantined"] += 1
            if is_used:
                counts["used"] += 1
            if not is_quarantined and not is_used and symbol_key not in keys.disabled:
                counts["available"] += 1
        return sorted(by_symbol.values(), key=lambda item: str(item["symbol"]).upper())

    def inventory(self, scope: str, settings: dict[str, Any]) -> dict[str, Any]:
        monthly = scope == "monthly"
        allowed = set(settings.get("allowed_asset_groups") or ASSET_GROUPS)
        # No se reutiliza `_inventory_visible_rows`: ese descarta los avisos de
        # `filter_rows_grid_off` y aqui viajan en la respuesta.
        rows = [
            row for row in self.candidate_rows(include_quarantined=True)
            if portfolio_group_key(
                str(row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol") or ""),
                universe_files=[self.universe],
            ) in allowed
        ]
        warnings: list[str] = []
        if settings.get("grid_off"):
            rows, warnings = filter_rows_grid_off(rows)
        quarantine = self.quarantine_rows()
        symbol_rows = self._symbol_inventory_counts(
            rows, self._inventory_keys(monthly, settings, quarantine), monthly
        )
        return {
            "scope": "monthly" if monthly else "full_history",
            "total": sum(row["total"] for row in symbol_rows),
            "quarantined": sum(row["quarantined"] for row in symbol_rows),
            "used": sum(row["used"] for row in symbol_rows),
            "available": sum(row["available"] for row in symbol_rows),
            "symbols": len(symbol_rows),
            "by_symbol": symbol_rows,
            "quarantine": quarantine,
            "quarantine_excludes": True,
            "warnings": warnings,
        }

    def symbol_sets(
        self,
        symbol: str,
        scope: str = "full_history",
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Lista los sets que el inventario cuenta para la familia, y su cuarentena.

        La ventana se abre desde una fila de «Sets disponibles por simbolo», asi
        que ensena lo que esa fila cuenta: el pool de las cuatro etapas
        aceptadas. Las excluidas que ya no estan en el pool tambien entran,
        porque esta tabla es desde donde se reintegran. Ver
        `ai_context/symbol_sync_cards.md` para el caso que lo fijo.
        """
        if normalize_portfolio_scope(scope) != "full_history":
            raise ValueError("La gestión por símbolo solo está disponible en Portafolio UBS")
        requested = portfolio_display_symbol(str(symbol or "").strip(), universe_files=[self.universe])
        requested_key = portfolio_symbol_key(requested)
        if not requested_key:
            raise ValueError("Falta el símbolo que se quiere gestionar")

        def belongs_to_family(value: Any) -> bool:
            display = portfolio_display_symbol(
                str(value or ""), universe_files=[self.universe]
            )
            return portfolio_symbol_key(display) == requested_key

        def inventory_filters(
            rows: list[dict[str, Any]], symbol_of: Callable[[dict[str, Any]], Any]
        ) -> list[dict[str, Any]]:
            return _inventory_visible_rows(rows, symbol_of, settings or {}, self.universe)

        quarantine = {
            self._path_key(row.get("set_path")): row
            for row in self.quarantine_rows()
        }
        used = {self._path_key(path) for path in self.used_set_paths("full_history")}
        pool_rows = [
            row for row in self.candidate_rows(include_quarantined=True)
            if belongs_to_family(_pool_symbol(row))
        ]
        result = self._family_pool_rows(
            inventory_filters(pool_rows, _pool_symbol), quarantine, used,
        )
        seen = {self._path_key(str(item["set_path"])) for item in result}
        family_quarantine = [
            row for key, row in quarantine.items()
            if key and key not in seen and belongs_to_family(row.get("symbol"))
        ]
        result.extend(self._family_quarantine_rows(
            inventory_filters(family_quarantine, lambda row: row.get("symbol")),
        ))
        if not result:
            raise ValueError(f"No se encontraron sets de la familia {requested}")
        result.sort(key=lambda item: (str(item["set_name"]).casefold(), str(item["account"]).casefold()))
        return {"symbol": requested, "sets": result, "total": len(result)}

    def _family_pool_rows(
        self,
        rows: list[dict[str, Any]],
        quarantine: dict[str, Any],
        used: set[str],
    ) -> list[dict[str, Any]]:
        """Filas del pool vivo de la familia, sin repetir el mismo .set."""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            path = str(row.get("set_path") or "")
            if not path.strip():
                continue
            path_key = self._path_key(path)
            if not path_key or path_key in seen:
                continue
            seen.add(path_key)
            result.append(_symbol_set_row(
                row,
                path,
                portfolio_display_symbol(
                    str(_pool_symbol(row) or ""), universe_files=[self.universe],
                ),
                quarantine.get(path_key),
                path_key in used,
            ))
        return result

    def _family_quarantine_rows(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Excluidas que ya no figuran en el pool; desde aqui se reintegran."""
        result: list[dict[str, Any]] = []
        for quarantined in rows:
            path = str(quarantined.get("set_path") or "")
            if not path.strip():
                continue
            result.append(_quarantined_set_row(
                quarantined,
                path,
                portfolio_display_symbol(
                    str(quarantined.get("symbol") or ""), universe_files=[self.universe],
                ),
            ))
        return result

    def export_symbol_sets(
        self,
        symbol: str,
        selected_paths: Any,
        destination: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Copia la selección validada de una familia de símbolo.

        ``settings`` tiene que ser el mismo con el que se listó la familia: la
        validación se hace contra esa lista, así que filtrarla de otra forma
        rechazaría una fila que la ventana sí ofrecía.
        """
        if not isinstance(selected_paths, list) or any(not isinstance(path, str) for path in selected_paths):
            raise ValueError("La selección de sets no es válida")
        family = self.symbol_sets(symbol, settings=settings)
        allowed = {self._path_key(row["set_path"]): row for row in family["sets"]}
        selected_keys = {self._path_key(path) for path in selected_paths if str(path).strip()}
        if not selected_keys:
            raise ValueError("Selecciona al menos un set para exportar")
        if selected_keys - set(allowed):
            raise ValueError("La selección contiene sets que no pertenecen a este símbolo")

        safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", family["symbol"]).strip("._") or "SIMBOLO"
        root = Path(destination).expanduser() if destination else self.project / "exports"
        output = root.resolve() / f"SETS_{safe_symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        dev_branch.assert_export_destination(output, self.project)
        output.mkdir(parents=True, exist_ok=True)
        exported: list[str] = []
        missing: list[str] = []
        destination_names: set[str] = set()
        for key in sorted(selected_keys):
            row = allowed[key]
            source_path = Path(str(row["set_path"]))
            if not source_path.is_file():
                missing.append(source_path.name)
                continue
            name = source_path.name
            if name.casefold() in destination_names:
                stem, suffix = source_path.stem, source_path.suffix
                account = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row.get("account") or "cuenta")).strip("._")
                name = f"{stem}_{account}{suffix}"
                index = 2
                while name.casefold() in destination_names:
                    name = f"{stem}_{account}_{index}{suffix}"
                    index += 1
            shutil.copy2(source_path, output / name)
            destination_names.add(name.casefold())
            exported.append(name)
        if not exported:
            raise ValueError("Ninguno de los sets seleccionados existe en disco")
        return {
            "folder": str(output), "symbol": family["symbol"],
            "exported": len(exported), "sets": exported, "missing": missing,
        }

    def _candidate_to_exclude(self, requested: str) -> dict[str, Any]:
        """La fila del candidato que se quiere excluir, o el motivo de no hallarla.

        Se acepta el nombre del fichero como respaldo solo si identifica a UNO:
        con dos candidatos del mismo nombre, adivinar excluiria al que no es.
        """
        candidates = self.candidate_rows(include_quarantined=True)
        requested_key = self._path_key(_resolve_source_path(requested, self.project))
        matches = [row for row in candidates if self._path_key(row.get("set_path")) == requested_key]
        if not matches:
            by_name = [row for row in candidates if Path(str(row.get("set_path") or "")).name.casefold() == Path(requested).name.casefold()]
            if len(by_name) == 1:
                matches = by_name
        if not matches:
            raise ValueError("El set no pertenece a los candidatos Final Tick 6M accepted")
        return matches[0]

    def _stage_restore_snapshot(
        self, candidate_memory: Path, candidate_id: Any, reason_code: str,
    ) -> str | None:
        """El respaldo de etapas que permitira reintegrar, leido antes de escribir.

        Si el veredicto fallase despues, la fila de cuarentena ya guardada
        describe el estado actual y «Reintegrar» sigue siendo correcto.
        """
        if reason_code == candidate_verdict.MANUAL:
            return None
        # `write=True` aunque aqui solo se lea: es el unico modo de abrir el
        # fichero real. Una lectura normal sobre una memoria remota devuelve la
        # copia, que puede ir por detras, y el respaldo saldria de un estado que
        # ya no es el que se va a rechazar.
        with self.connect_memory(candidate_memory, write=True) as read_conn:
            return candidate_verdict.dumps_snapshot(
                candidate_verdict.snapshot_candidate_stages(read_conn, candidate_id)
            )

    def _write_quarantine_row(
        self,
        source_memory: Path,
        row: dict[str, Any],
        account_label: str,
        candidate_id: Any,
        reason_code: str,
        payload: dict[str, Any],
        restore_json: str | None,
    ) -> Any:
        with self.connect_memory(source_memory, write=True) as conn:
            candidate_verdict.ensure_quarantine_schema(conn)
            conn.execute(
                """
                insert into portfolio_quarantine(account_type,candidate_id,set_path,symbol,timeframe,reason,source_portfolio_id,quarantined_at,reason_code,restore_json)
                values(?,?,?,?,?,?,?,?,?,?)
                on conflict(set_path) do update set account_type=excluded.account_type,candidate_id=excluded.candidate_id,
                    symbol=excluded.symbol,timeframe=excluded.timeframe,reason=excluded.reason,
                    source_portfolio_id=excluded.source_portfolio_id,quarantined_at=excluded.quarantined_at,
                    reason_code=excluded.reason_code,restore_json=excluded.restore_json
                """,
                (
                    account_label, candidate_id, row.get("set_path"),
                    portfolio_display_symbol(str(row.get("target_symbol") or row.get("symbol") or "")), row.get("period"),
                    candidate_verdict.reason_text(reason_code, payload.get("reason")),
                    safe_int(payload.get("portfolio_id"), 0) or None,
                    datetime.now().isoformat(timespec="seconds"),
                    reason_code, restore_json,
                ),
            )
            saved = conn.execute("select id from portfolio_quarantine where set_path=?", (row.get("set_path"),)).fetchone()
            conn.commit()
        return saved

    def exclude_strategy(self, payload: dict[str, Any], *, memory: Path | None = None) -> int:
        """Quarantine a candidate.

        ``memory`` overrides where the quarantine row is written. By default it
        lands in the memory that owns the candidate (the broker's), which is the
        global UBS quarantine. The Grid scope passes its manager-owned database
        instead, so a Grid exclusion is written where the Grid packages live.

        ``reason_code`` decide además si la exclusión escribe un veredicto de
        etapa en la memoria del candidato (`mt5_manager/candidate_verdict.py`).
        El veredicto va siempre a la memoria que **posee** al candidato, aunque
        la cuarentena se escriba en otra: en Grid la fila vive en la base del
        manager, pero los estados, el score y los pesos son del agente.
        """
        requested = str(payload.get("set_path") or payload.get("set_id") or "").strip()
        if not requested:
            raise ValueError("Falta identificar el set que se quiere excluir")
        row = self._candidate_to_exclude(requested)
        candidate_memory = Path(str(row.get("source_memory_path") or self.memory)).absolute()
        source_memory = Path(memory or candidate_memory).absolute()
        account_label = str(row.get("account_type") or f"{self.broker}/{self.account}")
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        candidate_id = row.get("source_candidate_id")
        restore_json = self._stage_restore_snapshot(candidate_memory, candidate_id, reason_code)
        saved = self._write_quarantine_row(
            source_memory, row, account_label, candidate_id, reason_code, payload, restore_json,
        )
        self._apply_candidate_verdict(candidate_memory, candidate_id, reason_code)
        return int(saved[0])

    def _apply_candidate_verdict(self, memory: Path, candidate_id: Any, reason_code: str) -> None:
        """Escribe el veredicto de etapa en la memoria que posee al candidato.

        REGLA DUPLICADA: el agente hace lo mismo desde
        `manager_node_runtime/portfolio_save.py` llamando a `ubs.manual_status`.
        """
        if candidate_verdict.normalize_reason_code(reason_code) == candidate_verdict.MANUAL:
            return
        with self.connect_memory(Path(memory).absolute(), write=True) as conn:
            candidate_verdict.apply_verdict(conn, candidate_id, reason_code)
            conn.commit()

    def _quarantine_memory(self, quarantine_key: str | int) -> tuple[Path, int]:
        raw = str(quarantine_key)
        if "|" in raw:
            account_label, raw_id = raw.rsplit("|", 1)
            memory = next((path for label, path in self.memory_sources if label == account_label), None)
            if memory is None:
                raise ValueError("La memoria de la cuarentena ya no está disponible")
            quarantine_id = safe_int(raw_id, 0)
        else:
            memory = self.memory
            quarantine_id = safe_int(raw, 0)
        if quarantine_id < 1:
            raise ValueError("Identificador de cuarentena inválido")
        return Path(memory).absolute(), quarantine_id

    def _quarantine_verdict_row(self, memory: Path, quarantine_id: int) -> tuple[Any, str]:
        """La fila de cuarentena y el veredicto que tiene puesto ahora mismo."""
        with self.connect_memory(memory, write=True) as conn:
            if not _table_exists(conn, "portfolio_quarantine"):
                raise ValueError("No existe la cuarentena")
            candidate_verdict.ensure_quarantine_schema(conn)
            row = conn.execute(
                "select account_type,candidate_id,reason,reason_code,restore_json"
                " from portfolio_quarantine where id=?", (quarantine_id,)
            ).fetchone()
            if row is None:
                raise ValueError("La estrategia excluida ya no existe")
            current = candidate_verdict.normalize_reason_code(row["reason_code"])
            conn.commit()
        return row, current

    def _reapply_candidate_verdict(
        self, candidate_memory: Path, row: Any, target: str,
    ) -> str | None:
        """Deshace el veredicto vigente y aplica el nuevo, en ese orden.

        Sin deshacer primero, el «estado anterior» que se guardaria seria una
        memoria a la que ya le faltan Final Tick y 6M, y el candidato no volveria
        nunca al pool.
        """
        restore_json: str | None = None
        with self.connect_memory(candidate_memory, write=True) as conn:
            # 1. Deshacer el veredicto vigente, si lo hubiera.
            candidate_verdict.restore_candidate_stages(conn, row["restore_json"])
            # 2. Fotografiar el estado ya restaurado, que es el que habrá que
            #    devolver la próxima vez.
            snapshot = candidate_verdict.snapshot_candidate_stages(conn, row["candidate_id"])
            if target not in {"pool", candidate_verdict.MANUAL}:
                if not snapshot:
                    raise ValueError(
                        "El candidato ya no tiene etapas en la memoria del agente: "
                        "no se puede aplicar el veredicto"
                    )
                restore_json = candidate_verdict.dumps_snapshot(snapshot)
                candidate_verdict.apply_verdict(conn, row["candidate_id"], target)
            conn.commit()
        return restore_json

    def _store_requalified(
        self, memory: Path, quarantine_id: int, target: str, row: Any, restore_json: str | None,
    ) -> None:
        """Borra la fila si vuelve al pool; si no, la reetiqueta."""
        with self.connect_memory(memory, write=True) as conn:
            if target == "pool":
                conn.execute("delete from portfolio_quarantine where id=?", (quarantine_id,))
            else:
                conn.execute(
                    "update portfolio_quarantine set reason_code=?,reason=?,restore_json=?,quarantined_at=?"
                    " where id=?",
                    (
                        target,
                        candidate_verdict.reason_text(target, candidate_verdict.origin_text(row["reason"])),
                        restore_json,
                        datetime.now().isoformat(timespec="seconds"),
                        quarantine_id,
                    ),
                )
            conn.commit()

    def requalify_strategy(self, quarantine_key: str | int, reason_code: str) -> str:
        """Mueve una estrategia excluida entre los cuatro estados posibles.

        Los tres motivos de exclusión y el pool son estados de una misma cosa, no
        operaciones independientes: reclasificar es **deshacer el veredicto
        actual y aplicar el nuevo**, nunca aplicar uno encima de otro.

        No pasa por `candidate_rows`: un candidato con veredicto ya no está ahí.
        Todo lo que hace falta está en la fila de cuarentena.

        REGLA DUPLICADA: sobre una memoria que el manager ve por red o por un bind
        mount, esto no se puede ejecutar aquí y `PortfolioCoordinator.requalify` lo
        manda al nodo, que reimplementa el mismo orden en
        `manager_node_runtime/portfolio_save.py::requalify_portfolio_member_payload`.
        Cambiar el orden solo aquí no tiene efecto para esos nodos.
        """
        target = candidate_verdict.normalize_reason_code(reason_code) if str(reason_code) != "pool" else "pool"
        memory, quarantine_id = self._quarantine_memory(quarantine_key)
        row, current = self._quarantine_verdict_row(memory, quarantine_id)
        if target == current:
            return current
        candidate_memory = next(
            (path for label, path in self.memory_sources if label == str(row["account_type"] or "")),
            memory,
        )
        restore_json = self._reapply_candidate_verdict(candidate_memory, row, target)
        self._store_requalified(memory, quarantine_id, target, row, restore_json)
        return target

    def release_strategy(self, quarantine_key: str | int) -> None:
        """Devuelve la estrategia al pool: es reclasificarla al estado `pool`.

        Delega en `requalify_strategy` para que reintegrar y reclasificar no
        puedan divergir: las dos operaciones tienen que deshacer el veredicto
        vigente antes de nada.
        """
        self.requalify_strategy(quarantine_key, "pool")

    def used_set_paths(
        self,
        scope: str,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_type: PortfolioType | None = None,
    ) -> list[str]:
        paths: set[str] = set()
        for _account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolios"):
                    continue
                selects: list[str] = []
                type_filter = ""
                if scope == "full_history" and portfolio_type is not None:
                    type_expression = "lower(coalesce(nullif(p.portfolio_type,''),nullif(p.type,''),''))"
                    type_filter = (
                        f" and {type_expression}='aggressive'"
                        if portfolio_type == PortfolioType.AGGRESSIVE
                        else f" and {type_expression}<>'aggressive'"
                    )
                if _table_exists(conn, "portfolio_allocations"):
                    selects.append(
                        "select pa.set_path from portfolio_allocations pa join portfolios p on p.id=pa.portfolio_id "
                        "where pa.set_path is not null and pa.set_path<>'' and coalesce(nullif(p.portfolio_scope,''),'full_history')=? "
                        f"and (? is null or p.id<>?){type_filter}"
                    )
                if _table_exists(conn, "portfolio_members"):
                    selects.append(
                        "select pm.set_path from portfolio_members pm join portfolios p on p.id=pm.portfolio_id "
                        "where pm.set_path is not null and pm.set_path<>'' and coalesce(nullif(p.portfolio_scope,''),'full_history')=? "
                        f"and (? is null or p.id<>?){type_filter}"
                    )
                params: list[Any] = []
                exclusion_memories = {self.memory}
                scope_memory = getattr(self, "scope_memory", None)
                if scope_memory is not None:
                    exclusion_memories.add(scope_memory)
                for _ in selects:
                    excluded = exclude_portfolio_id if memory in exclusion_memories else None
                    params.extend((scope, excluded, excluded))
                if selects:
                    paths.update(_resolve_source_path(row[0], self.project) for row in conn.execute(" union ".join(selects), params) if row[0])
        return sorted(paths)

    def saved_curves(
        self,
        *,
        monthly: bool,
        scope: str | None = None,
        portfolio_type: PortfolioType | None = None,
        exclude_portfolio_id: int | None = None,
    ) -> list[list[float]]:
        portfolio_scope = normalize_portfolio_scope(scope) if scope is not None else ("monthly" if monthly else "full_history")
        curves: list[list[float]] = []
        rows: list[sqlite3.Row] = []
        for _account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolios"):
                    continue
                excluded = exclude_portfolio_id if memory in {
                    self.memory, getattr(self, "scope_memory", None)
                } else None
                rows.extend(conn.execute(
                    "select id,portfolio_type,type,metrics_json from portfolios where metrics_json is not null "
                    "and metrics_json<>'' and coalesce(nullif(portfolio_scope,''),'full_history')=? and (? is null or id<>?)",
                    (portfolio_scope, excluded, excluded),
                ).fetchall())
        for row in rows:
            type_key = str(row["portfolio_type"] or row["type"] or "").lower()
            if not monthly and portfolio_type is not None:
                if portfolio_type == PortfolioType.AGGRESSIVE and type_key not in {"aggressive", "bundle", "grid_bundle"}:
                    continue
                if portfolio_type != PortfolioType.AGGRESSIVE and type_key == "aggressive":
                    continue
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(metrics, dict) and metrics.get("portfolio_bundle") and isinstance(metrics.get("variants"), dict):
                keys = ("aggressive",) if portfolio_type == PortfolioType.AGGRESSIVE else ("balanced", "conservative")
                for key in keys:
                    payload = metrics["variants"].get(key)
                    curve = payload.get("equity_curve_2020_2026") if isinstance(payload, dict) else None
                    if isinstance(curve, list) and len(curve) > 1:
                        curves.append([float(value) for value in curve])
                continue
            curve = metrics.get("equity_curve_2020_2026") if isinstance(metrics, dict) else None
            if isinstance(curve, list) and len(curve) > 1:
                curves.append([float(value) for value in curve])
        return curves

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
        return row[key] if key in row.keys() else default

    def saved_portfolios(self, scope: str) -> dict[str, Any]:
        portfolio_scope = normalize_portfolio_scope(scope)
        with self.connect() as conn:
            rows = conn.execute(
                "select * from portfolios where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
                (portfolio_scope,),
            ).fetchall() if _table_exists(conn, "portfolios") else []
        value = self._row_value
        portfolios = [_saved_portfolio_row(row, value, portfolio_scope) for row in rows]
        if portfolio_scope == "full_history":
            _annotate_improvement_lineage(portfolios, rows, value)
        return {
            "node": {"id": self.node.get("id"), "name": self.node.get("name") or self.node.get("id"), "broker": self.broker, "account_type": self.account},
            "scope": portfolio_scope,
            "portfolios": portfolios,
            "summary": {"total": len(portfolios), "strategies": sum(item["active_strategies"] for item in portfolios), "latest_id": portfolios[0]["id"] if portfolios else None},
            "observed_at": utc_now(),
        }

    def saved_portfolio_detail(self, portfolio_id: int, scope: str) -> dict[str, Any]:
        listing = self.saved_portfolios(scope)
        selected = next((item for item in listing["portfolios"] if item["id"] == portfolio_id), None)
        if selected is None:
            raise ValueError(f"No existe el portafolio #{portfolio_id} en este ambito")
        with self.connect() as conn:
            row = conn.execute("select metrics_json from portfolios where id=?", (portfolio_id,)).fetchone()
            try:
                parsed = json.loads(row["metrics_json"] or "{}") if row else {}
                metrics = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                metrics = {}
            members = [dict(item) for item in conn.execute(
                "select * from portfolio_allocations where portfolio_id=? order by variant_key,set_id,units desc",
                (portfolio_id,),
            ).fetchall()] if _table_exists(conn, "portfolio_allocations") else []
            if not members and _table_exists(conn, "portfolio_members"):
                for item in conn.execute("select * from portfolio_members where portfolio_id=? order by lot desc", (portfolio_id,)).fetchall():
                    raw = dict(item)
                    members.append({"variant_key": raw.get("variant_key") or "", "variant_label": raw.get("variant_label") or "", "set_id": raw.get("set_path") or "", "candidate_id": raw.get("candidate_id") or "", "symbol": raw.get("symbol") or "", "timeframe": raw.get("period") or "", "units": int(round(float(raw.get("lot") or 0) / .01)), "lot": float(raw.get("lot") or 0), "lot_size_step": float(raw.get("lot_size_step") or .01), "net_profit_contribution": float(raw.get("combined_net_profit") or 0), "standalone_valley_dd": float(raw.get("standalone_dd") or 0), "standalone_point_dd": 0.0, "set_path": raw.get("set_path") or "", "margin_required": 0.0, "margin_pct": 0.0})
        selected["metrics"] = metrics
        selected["members"] = [{
            "variant_key": str(raw.get("variant_key") or ""), "variant_label": str(raw.get("variant_label") or ""),
            "set_id": str(raw.get("set_id") or ""), "set_name": Path(str(raw.get("set_path") or raw.get("set_id") or "")).name,
            "set_path": str(raw.get("set_path") or raw.get("set_id") or ""),
            "candidate_id": str(raw.get("candidate_id") or ""), "symbol": str(raw.get("symbol") or ""), "timeframe": str(raw.get("timeframe") or ""),
            "units": int(raw.get("units") or 0), "lot": float(raw.get("lot") or 0), "lot_size_step": float(raw.get("lot_size_step") or 0),
            "net_profit_contribution": float(raw.get("net_profit_contribution") or 0), "standalone_valley_dd": float(raw.get("standalone_valley_dd") or 0),
            "standalone_point_dd": float(raw.get("standalone_point_dd") or 0), "margin_required": float(raw.get("margin_required") or 0), "margin_pct": float(raw.get("margin_pct") or 0),
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
            "seasonal": (metrics.get("seasonal_coverage") or {}).get(str(raw.get("set_id") or ""), {}),
        } for raw in members]
        with self.connect() as conn:
            versions = [dict(item) for item in conn.execute(
                "select id,version_no,created_at,reason from portfolio_versions where portfolio_id=? order by version_no desc",
                (portfolio_id,),
            ).fetchall()] if _table_exists(conn, "portfolio_versions") else []
            decisions = [dict(item) for item in conn.execute(
                "select * from portfolio_decision_log where portfolio_id=? order by step,id",
                (portfolio_id,),
            ).fetchall()] if _table_exists(conn, "portfolio_decision_log") else []
        selected["versions"] = versions
        selected["decisions"] = decisions
        return {"node": listing["node"], "scope": listing["scope"], "portfolio": selected, "observed_at": utc_now()}

    def set_portfolio_alias(self, portfolio_id: int, scope: str, alias: Any) -> str:
        """Persist an optional display alias inside metrics.inputs."""
        portfolio_scope = normalize_portfolio_scope(scope)
        if portfolio_scope != "full_history":
            raise ValueError("El alias solo está disponible en Portafolio UBS")
        normalized = normalize_portfolio_alias(alias)
        with self.connect(write=True) as conn:
            row = conn.execute(
                "select metrics_json from portfolios where id=? and "
                "coalesce(nullif(portfolio_scope,''),'full_history')=?",
                (portfolio_id, portfolio_scope),
            ).fetchone()
            if row is None:
                raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
            try:
                parsed = json.loads(row["metrics_json"] or "{}")
                metrics = parsed if isinstance(parsed, dict) else {}
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            inputs = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
            metrics["inputs"] = inputs
            if normalized:
                inputs["portfolio_alias"] = normalized
            else:
                inputs.pop("portfolio_alias", None)
            conn.execute(
                "update portfolios set metrics_json=? where id=?",
                (json.dumps(metrics, ensure_ascii=True, separators=(",", ":")), portfolio_id),
            )
            conn.commit()
        return normalized

    def saved_inputs(self, portfolio_id: int, scope: str) -> dict[str, Any]:
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        return self._saved_inputs_from_detail(detail, scope)

    def _saved_inputs_from_detail(self, detail: dict[str, Any], scope: str) -> dict[str, Any]:
        """Rebuild saved constraints, including rows created before metrics.inputs existed."""
        metrics = detail.get("metrics") if isinstance(detail.get("metrics"), dict) else {}
        stored = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        capital, valley_pct, point_pct = _saved_risk_targets(detail)
        saved_row_type, portfolio_type = _saved_portfolio_type(detail, metrics, stored)
        values: dict[str, Any] = {
            "capital": capital,
            "valley_dd_pct": valley_pct,
            "point_dd_pct": point_pct,
            "portfolio_type": portfolio_type,
            "min_trades_2020_2026": 15 if scope == "monthly" else 100,
            **SAVED_INPUT_FALLBACKS,
            "margin_profile": self.broker.lower(),
            "portfolio_scope": scope,
        }
        if scope == "monthly":
            values.update({
                "target_month": int(detail.get("target_month") or 0),
                "max_daily_dd": float(metrics.get("target_daily_dd") or MONTHLY_DEFAULTS["max_daily_dd"]),
            })
        values.update(stored)
        values["capital"] = values.get("capital") or capital
        values["valley_dd_pct"] = values.get("valley_dd_pct") or valley_pct
        values["point_dd_pct"] = values.get("point_dd_pct") or point_pct
        values["portfolio_scope"] = scope
        if saved_row_type in {"bundle", "grid_bundle"}:
            values["portfolio_type"] = portfolio_type
        if scope == "monthly":
            values["target_month"] = values.get("target_month") or detail.get("target_month")
        migrated_groups = _migrated_asset_groups(values)
        if migrated_groups:
            values["allowed_asset_groups"] = migrated_groups
        return normalize_settings(scope, values, self.broker)

    def _save_version(self, conn: sqlite3.Connection, portfolio_id: int, reason: str) -> int:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe")
        payload: dict[str, Any] = {"portfolio": dict(portfolio)}
        for key, table in (
            ("allocations", "portfolio_allocations"),
            ("members", "portfolio_members"),
            ("decisions", "portfolio_decision_log"),
        ):
            payload[key] = [dict(row) for row in conn.execute(
                f"select * from {table} where portfolio_id=? order by id", (portfolio_id,)
            )] if _table_exists(conn, table) else []
        version_no = int(conn.execute(
            "select coalesce(max(version_no),0)+1 from portfolio_versions where portfolio_id=?",
            (portfolio_id,),
        ).fetchone()[0])
        snapshot = zlib.compress(json.dumps(payload, ensure_ascii=True).encode("utf-8"), level=6)
        conn.execute(
            "insert into portfolio_versions(portfolio_id,version_no,created_at,reason,snapshot_json) values(?,?,?,?,?)",
            (portfolio_id, version_no, datetime.now().isoformat(timespec="seconds"), reason, snapshot),
        )
        return version_no

    @staticmethod
    def _restore_version(conn: sqlite3.Connection, portfolio_id: int, snapshot: bytes) -> None:
        payload = json.loads(zlib.decompress(snapshot).decode("utf-8"))
        portfolio = dict(payload["portfolio"])
        portfolio.pop("id", None)
        columns = list(portfolio)
        conn.execute(
            f"update portfolios set {', '.join(f'{column}=?' for column in columns)} where id=?",
            [portfolio[column] for column in columns] + [portfolio_id],
        )
        for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members"):
            conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
        for key, table in (
            ("allocations", "portfolio_allocations"),
            ("members", "portfolio_members"),
            ("decisions", "portfolio_decision_log"),
        ):
            for raw in payload.get(key) or []:
                row = dict(raw)
                row.pop("id", None)
                row["portfolio_id"] = portfolio_id
                row_columns = list(row)
                conn.execute(
                    f"insert into {table} ({', '.join(row_columns)}) values ({', '.join('?' for _ in row_columns)})",
                    [row[column] for column in row_columns],
                )

    def undo_latest(self, portfolio_id: int, scope: str) -> int:
        self.saved_portfolio_detail(portfolio_id, scope)
        with self.connect(write=True) as conn:
            version = conn.execute(
                "select id,version_no,snapshot_json from portfolio_versions where portfolio_id=? order by version_no desc limit 1",
                (portfolio_id,),
            ).fetchone()
            if version is None:
                raise ValueError("No hay una versión anterior guardada")
            try:
                self._restore_version(conn, portfolio_id, version["snapshot_json"])
                conn.execute("delete from portfolio_versions where id=?", (version["id"],))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(version["version_no"])

    def delete_portfolio(self, portfolio_id: int, scope: str) -> None:
        self.saved_portfolio_detail(portfolio_id, scope)
        with self.connect(write=True) as conn:
            for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members", "portfolio_versions"):
                conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
            deleted = conn.execute("delete from portfolios where id=?", (portfolio_id,))
            if deleted.rowcount != 1:
                raise ValueError("El portafolio ya no existe")
            conn.commit()

    def _recalculate_saved(self, conn: sqlite3.Connection, portfolio_id: int) -> None:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe")
        rows = [dict(row) for row in conn.execute(
            "select * from portfolio_allocations where portfolio_id=? order by id", (portfolio_id,)
        ).fetchall()]
        metrics = _stored_metrics(portfolio)
        if not rows:
            _blank_recalculated_portfolio(conn, portfolio, portfolio_id, metrics)
            return
        strategies, warnings = load_robust_sets_from_rows(
            _allocation_source_rows(rows), [], parse=cached_report,
        )
        if len(strategies) != len(rows):
            raise ValueError("No se pudieron reconstruir todas las curvas restantes")
        full_strategies = list(strategies)
        scope = str(portfolio["portfolio_scope"] or "full_history")
        detail = dict(portfolio)
        detail["metrics"] = metrics
        inputs = self._saved_inputs_from_detail(detail, scope)
        if scope == "monthly":
            strategies, scoped_warnings = slice_strategy_sets_to_month(strategies, int(inputs["target_month"]))
            warnings.extend(scoped_warnings)
        units = {str(row.get("set_path") or row.get("set_id")): int(row.get("units") or 0) for row in rows}
        evaluation = evaluate_portfolio(
            strategies, units, float(portfolio["target_valley_dd"] or 0), float(portfolio["target_point_dd"] or 0),
            inputs.get("max_daily_dd"), bool(inputs.get("enforce_point_dd", False)), bool(inputs.get("daily_dd_full_history", False)),
        )
        _recalculated_metrics(metrics, evaluation, strategies, units, portfolio)
        if inputs.get("strict_yearly_month_validation"):
            metrics["seasonal_validation"] = validate_strict_monthly_portfolio(
                full_strategies, units, target_month=int(inputs["target_month"]),
                target_valley_dd=float(portfolio["target_valley_dd"] or 0),
                target_point_dd=float(portfolio["target_point_dd"] or 0), enforce_point_dd=False, lookback_years=5,
            )
        if warnings:
            metrics.setdefault("warnings", []).extend(warnings)
        _update_recalculated_row(conn, portfolio_id, metrics, evaluation, strategies, units)

    def remove_member_to_quarantine(self, payload: dict[str, Any], scope: str) -> int:
        """Excluye un miembro y decide si el portafolio se borra o se recalcula.

        REGLA DUPLICADA. El agente no ejecuta esto: reimplementa la misma regla en
        `manager_node_runtime/portfolio_save.py::exclude_portfolio_members_payload`.
        Cambiar solo aquí no tiene efecto para el usuario. Portar el cambio y
        comprobarlo con `tests/test_node_runtime_fork_parity.py`.
        """
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        if portfolio_id < 1:
            return self.exclude_strategy(payload)
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        is_bundle = _is_bundle_portfolio(detail)
        requested = self._match_key(payload.get("set_path") or payload.get("set_id"))
        member = next((item for item in detail.get("members") or [] if self._match_key(item.get("set_path")) == requested), None)
        if member is None:
            raise ValueError("No se encontró la estrategia dentro del portafolio")
        return self._quarantine_member(member, portfolio_id, payload, is_bundle, scope)

    def _quarantine_member(
        self, member: dict[str, Any], portfolio_id: int, payload: dict[str, Any],
        is_bundle: bool, scope: str,
    ) -> int:
        """Pone en cuarentena un miembro guardado, sin tocar el portafolio.

        EL PORTAFOLIO GUARDADO NO SE MODIFICA. Antes, excluir un miembro borraba
        el A/M/C o el mes entero, y en un `full_history` de objetivo único
        quitaba la asignación y recalculaba las métricas. Las dos cosas
        destruían un resultado guardado como efecto colateral de una decisión
        sobre el pool. La exclusión afecta ahora a lo que decide: el pool y, si
        hay veredicto, los estados del agente.

        No pasa por `candidate_rows` a propósito: un candidato con veredicto ya
        no aparece ahí (`exclude_strategy` lo exige y fallaría), y aun así tiene
        que poder excluirse desde el portafolio que lo contiene. Los datos salen
        del miembro guardado, que es donde están.
        """
        candidate_text = str(member.get("candidate_id") or "")
        candidate_id = safe_int(candidate_text.rsplit(":", 1)[-1], 0) or None
        account_label = candidate_text.rsplit(":", 1)[0] if ":" in candidate_text else f"{self.broker}/{self.account}"
        source_memory = next((path for label, path in self.memory_sources if label == account_label), self.memory)
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        default_reason = (
            "Excluida manualmente desde un portafolio A/M/C guardado" if is_bundle
            else "Excluida manualmente desde un Portafolio UBS mensual guardado" if scope == "monthly"
            else "Retirada manualmente de un portafolio guardado"
        )
        with self.connect_memory(source_memory, write=True) as source_conn:
            candidate_verdict.ensure_quarantine_schema(source_conn)
            restore_json = candidate_verdict.dumps_snapshot(
                candidate_verdict.snapshot_candidate_stages(source_conn, candidate_id)
            ) if reason_code != candidate_verdict.MANUAL else None
            source_conn.execute(
                """insert into portfolio_quarantine(account_type,candidate_id,set_path,symbol,timeframe,reason,source_portfolio_id,quarantined_at,reason_code,restore_json)
                   values(?,?,?,?,?,?,?,?,?,?) on conflict(set_path) do update set account_type=excluded.account_type,
                   candidate_id=excluded.candidate_id,symbol=excluded.symbol,timeframe=excluded.timeframe,
                   reason=excluded.reason,source_portfolio_id=excluded.source_portfolio_id,quarantined_at=excluded.quarantined_at,
                   reason_code=excluded.reason_code,restore_json=excluded.restore_json""",
                (account_label, candidate_id, set_path, str(member.get("symbol") or ""), str(member.get("timeframe") or ""),
                 candidate_verdict.reason_text(reason_code, payload.get("reason") or default_reason),
                 portfolio_id, datetime.now().isoformat(timespec="seconds"), reason_code, restore_json),
            )
            quarantine_id = int(source_conn.execute("select id from portfolio_quarantine where set_path=?", (set_path,)).fetchone()[0])
            candidate_verdict.apply_verdict(source_conn, candidate_id, reason_code)
            source_conn.commit()
        return quarantine_id

    def remove_members_to_quarantine(self, payload: dict[str, Any], scope: str) -> list[int]:
        """Excluye varios miembros. El portafolio guardado no se toca.

        REGLA DUPLICADA. Igual que `remove_member_to_quarantine`: el agente la
        reimplementa en `manager_node_runtime/portfolio_save.py`, y es la copia del
        agente la que se ejecuta cuando el usuario pulsa el botón. Portar allí todo
        cambio de criterio; `tests/test_node_runtime_fork_parity.py` lo verifica.
        """
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        if portfolio_id < 1:
            raise ValueError("Falta el portafolio que contiene las estrategias")
        requested_paths = payload.get("set_paths")
        if not isinstance(requested_paths, list) or not requested_paths:
            raise ValueError("Selecciona al menos una estrategia")
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        is_bundle = _is_bundle_portfolio(detail)
        # Se admite en bundles A/M/C y mensuales, que es donde la interfaz ofrece
        # las casillas de selección. Ya no hay ninguna asimetría de borrado
        # detrás: ningún ámbito borra ni modifica el portafolio guardado.
        if not (is_bundle or scope == "monthly"):
            raise ValueError("La exclusión múltiple solo está disponible para portafolios A/M/C y mensuales")
        members_by_path = {
            self._match_key(item.get("set_path")): item for item in detail.get("members") or []
        }
        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in requested_paths:
            key = self._match_key(value)
            if key in seen:
                continue
            member = members_by_path.get(key)
            if member is None:
                raise ValueError("Una de las estrategias seleccionadas ya no pertenece al portafolio")
            seen.add(key)
            members.append(member)
        return [
            self._quarantine_member(member, portfolio_id, payload, is_bundle, scope)
            for member in members
        ]


    def notify(self, message: str) -> None:
        settings = self.project / "ui_settings.ini"
        enabled = False
        if settings.is_file():
            for line in settings.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if line.strip().lower().startswith("telegram_enabled="):
                    enabled = line.split("=", 1)[1].strip().lower() in {"1", "true", "yes", "on", "si", "sí"}
                    break
        if not enabled or not (self.project / "telegram_notify.py").is_file():
            return
        env = os.environ.copy()
        env["MT5_MANAGER_TELEGRAM_MESSAGE"] = str(message)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [sys.executable, "-c", "import os,telegram_notify; telegram_notify.send_message(os.environ.get('MT5_MANAGER_TELEGRAM_MESSAGE',''))"],
            cwd=str(self.project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )


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


class PortfolioCoordinator:
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

    def prepare_save(self, node_id: str, scope: str, selected_key: str) -> dict[str, Any]:
        self._node(node_id)
        key = self._key(node_id, scope)
        with self.lock:
            proposals = list(self.proposals.get(key) or [])
            job = dict(self.jobs.get(key) or {})
        if not proposals:
            raise ValueError("Genera una propuesta antes de guardar")
        if not any(str(proposal.get("key") or "") == selected_key for proposal in proposals):
            raise ValueError("La propuesta seleccionada ya no está disponible")
        operation = str(job.get("operation") or "generate")
        standalone_improvement = operation == "improve" and normalize_portfolio_scope(scope) == "full_history"
        if standalone_improvement and (len(proposals) != 1 or selected_key not in PORTFOLIO_TYPES):
            raise ValueError("Recalcula la mejora para una sola variante antes de guardar")
        # Un paquete A/M/C incompleto se muestra para poder mirarlo, pero no se
        # guarda: la fila guardada representa las tres variantes de una misma
        # composicion y media fila no es reoptimizable ni comparable.
        keys = {str(proposal.get("key") or "") for proposal in proposals}
        locked_keys = {key for key, _label, _type in LOCKED_VARIANTS}
        # Solo UBS full y Grid nombran sus variantes A/M/C; el mensual usa
        # profit/balanced/margin y comparte el nombre «balanced» por accidente.
        bundle_scope = normalize_portfolio_scope(scope) in {"full_history", "grid"}
        if bundle_scope and keys and keys < locked_keys and not standalone_improvement:
            raise ValueError(
                f"El paquete A/M/C esta incompleto ({len(keys)}/3 variantes viables: "
                f"{', '.join(sorted(keys))}). Ajusta los limites y recalcula antes de guardar."
            )
        operation = str(job.get("operation") or "generate")
        target_id = safe_int(job.get("portfolio_id"), 0)
        if operation in {"reoptimize", "complete", "improve"} and target_id <= 0:
            raise ValueError("Falta el portafolio que se quiere actualizar")
        request_id = str(job.get("save_request_id") or "")
        if not request_id or str(job.get("save_selected_key") or "") != selected_key:
            request_id = str(uuid.uuid4())
        with self.lock:
            if key not in self.jobs:
                self.jobs[key] = job
            self.jobs[key]["save_request_id"] = request_id
            self.jobs[key]["save_selected_key"] = selected_key
        # UBS normal guarda la mejora como un portafolio nuevo de un solo modo.
        # El mensual conserva su protocolo anterior mientras siga congelado.
        wire_operation = "generate" if standalone_improvement else "complete" if operation == "improve" else operation
        return {
            "scope": scope,
            "selected_key": selected_key,
            "operation": wire_operation,
            "manager_operation": operation,
            "portfolio_id": None if standalone_improvement else target_id or None,
            "request_id": request_id,
            "proposals": serialize_portfolio_proposals(proposals, request_id),
        }

    def confirm_save(self, node_id: str, scope: str, request_id: str, portfolio_id: int) -> None:
        key = self._key(node_id, scope)
        with self.lock:
            job = dict(self.jobs.get(key) or {})
            if str(job.get("save_request_id") or "") != str(request_id):
                raise ValueError("La confirmación no corresponde a la propuesta pendiente")
            self.proposals.pop(key, None)
            self.jobs[key] = {"status": "idle", "operation": "generate", "last_saved_id": portfolio_id,
                              "last_log_path": job.get("log_path") or job.get("last_log_path")}
        # El nodo acaba de escribir la fila en su memoria; la copia que lee el
        # manager sigue siendo la anterior y la lista se repinta justo despues
        # del guardado, asi que sin esto el portafolio recien confirmado no
        # aparece. Igual que en _delete_on_node y en exclude.
        self._invalidate_node_snapshots(node_id)

    def saved(self, node_id: str, scope: str, portfolio_id: int | None = None) -> dict[str, Any]:
        source = self._persistence_source(node_id, scope)
        return source.saved_portfolio_detail(portfolio_id, scope) if portfolio_id is not None else source.saved_portfolios(scope)

    def set_alias(self, node_id: str, scope: str, portfolio_id: int, alias: Any) -> str:
        """Write the alias where the portfolio DB is locally owned."""
        scope = normalize_portfolio_scope(scope)
        if scope != "full_history":
            raise ValueError("El alias solo está disponible en Portafolio UBS")
        normalized = normalize_portfolio_alias(alias)
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if base_url.startswith(("http://", "https://")):
            status, value = self._post_to_node(
                node,
                "/api/v1/portfolios/alias",
                {"scope": scope, "portfolio_id": portfolio_id, "alias": normalized},
            )
            if status == 404:
                raise ValueError(
                    "El nodo todavía no admite alias de portafolio; actualiza su código y reinícialo."
                )
            if status >= 400 or not isinstance(value, dict):
                error = value.get("error") if isinstance(value, dict) else value
                raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
            if safe_int(value.get("portfolio_id"), 0) != portfolio_id or value.get("alias") != normalized:
                raise ValueError("El nodo no confirmó correctamente el alias del portafolio")
        else:
            normalized = PortfolioSource(node).set_portfolio_alias(portfolio_id, scope, normalized)
        self._invalidate_node_snapshots(node_id)
        return normalized

    def _quarantine_grid_set(self, node_id: str, set_path: str, reason: str, reason_code: str = "manual") -> int:
        """Quarantine one set in the manager's own Grid database.

        The node endpoint cannot be used here: it requires a ``portfolio_id``
        that exists in the broker memory ("Falta el portafolio que contiene las
        estrategias"), and a Grid package only exists in this manager. Writing
        the quarantine next to the packages keeps the whole Grid scope
        manager-owned, and ``candidate_rows`` already filters by the quarantine
        of every memory source, so the exclusion holds on the next generation.
        A Grid exclusion is therefore Grid-only; the broker quarantine written
        from the UBS screens keeps applying to Grid as well.

        El veredicto de etapa es la excepción a esa asimetría, y a propósito: los
        estados, el score y los pesos son del agente, no de Grid, así que
        `exclude_strategy` los escribe en la memoria del broker aunque la fila de
        cuarentena se quede aquí. Una estrategia rechazada por degradación deja
        de ser candidata en los tres ámbitos, que es lo que significa el rechazo.
        """
        source = self._calculation_source(node_id, "grid")
        grid_memory = self._persistence_source(node_id, "grid").memory
        return source.exclude_strategy(
            {"set_path": set_path, "reason": reason, "reason_code": reason_code}, memory=grid_memory
        )

    def exclude_grid(self, node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Quarantine Grid strategies. The saved package is left untouched.

        Antes se borraba el paquete A/M/C entero, igual que en UBS. Ya no: la
        exclusión decide sobre el pool y, si hay veredicto, sobre los estados del
        agente; el resultado guardado no es un efecto colateral de eso.
        """
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        raw_paths = payload.get("set_paths")
        if raw_paths is None:
            raw_paths = [payload.get("set_path") or payload.get("set_id")]
        elif not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError("Selecciona al menos una estrategia")
        paths: list[str] = []
        for value in raw_paths:
            text = str(value or "").strip()
            if text and text not in paths:
                paths.append(text)
        if not paths:
            raise ValueError("Falta identificar el set que se quiere excluir")
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        reason = str(payload.get("reason") or "").strip() or (
            "Excluida manualmente desde un paquete Grid A/M/C guardado" if portfolio_id
            else "Excluida manualmente desde el manager Grid"
        )
        source = self._persistence_source(node_id, "grid")
        if portfolio_id:
            detail = source.saved_portfolio_detail(portfolio_id, "grid")["portfolio"]
            members = {source._match_key(item.get("set_path")) for item in detail.get("members") or []}
            for path in paths:
                if source._match_key(path) not in members:
                    raise ValueError("Una de las estrategias seleccionadas ya no pertenece al portafolio Grid")
        quarantine_ids = [self._quarantine_grid_set(node_id, path, reason, reason_code) for path in paths]
        # El veredicto se escribe en la memoria del broker, que el manager lee por
        # copia: sin invalidar la firma seguiría enseñando al candidato aceptado.
        self.invalidate_after_exclusion(node_id)
        return {
            "quarantine_id": quarantine_ids[0],
            "quarantine_ids": quarantine_ids,
            "deleted": False,
            "portfolio_id": portfolio_id or None,
            "scope": "grid",
        }

    def _drop_cached_proposals(self, node_id: str) -> None:
        """Invalidate every scope: the quarantine is shared by all of them."""
        with self.lock:
            for scope in PORTFOLIO_SCOPES:
                self.proposals.pop(self._key(node_id, scope), None)

    def exclude(self, node_id: str, scope: str, payload: dict[str, Any]) -> int:
        if payload.get("set_paths") is not None:
            raise ValueError("La exclusión múltiple debe ejecutarse mediante la API del nodo")
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if base_url.startswith(("http://", "https://")):
            # Run the write on the node, which owns the DB, then refresh the
            # manager snapshot -- exactly like _delete_on_node. The manager only
            # reads the remote memory through a read-only snapshot; writing to it
            # directly over CIFS is unreliable (SQLite WAL is not coherent across
            # a network share), so a manager-side quarantine/delete silently
            # failed to appear and the excluded portfolio kept showing up.
            status, value = self._post_to_node(node, "/api/v1/portfolios/exclude", {**payload, "scope": scope})
            if status == 404:
                raise ValueError("El nodo todavía no admite exclusión individual local; actualiza su código y reinícialo.")
            if status >= 400 or not isinstance(value, dict):
                error = value.get("error") if isinstance(value, dict) else value
                raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
            quarantine_id = safe_int(value.get("quarantine_id"), 0)
            # Only when this manager reads the node's memory locally (via a
            # snapshot) is there a cache to refresh; without portfolio_project_dir
            # it proxies reads to the node too, so there is nothing to invalidate.
            if str(node.get("portfolio_project_dir") or "").strip():
                source = PortfolioSource(node)
                for _account, memory in source.memory_sources:
                    source._invalidate_remote_snapshot(memory)
            self._assert_node_applied_verdict(payload, value)
        else:
            source = PortfolioSource(node)
            quarantine_id = source.remove_member_to_quarantine(payload, scope) if safe_int(payload.get("portfolio_id"), 0) else source.exclude_strategy(payload)
        self._drop_cached_proposals(node_id)
        return quarantine_id

    @staticmethod
    def _assert_node_applied_verdict(payload: dict[str, Any], value: dict[str, Any]) -> None:
        """Un nodo sin portar acepta el motivo y no escribe el veredicto.

        La copia de `manager_node_runtime/` es distinta en cada agente y se porta
        a mano, así que un nodo antiguo devuelve 200 tras poner la estrategia en
        cuarentena y descarta `reason_code` en silencio: el usuario creería que
        se actualizaron estados, score y pesos cuando no se tocó nada. El nodo
        portado confirma con `verdict_applied`; sin esa confirmación, esto falla
        y dice exactamente qué ha pasado y qué queda por hacer.
        """
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        if reason_code == candidate_verdict.MANUAL or value.get("verdict_applied"):
            return
        raise ValueError(
            "La estrategia quedó en cuarentena, pero el nodo no escribió el veredicto "
            f"«{candidate_verdict.REASON_LABELS[reason_code]}»: estados, score y pesos siguen "
            "igual en la memoria del agente. Ese nodo aún no tiene portado el cambio en "
            "manager_node_runtime/portfolio_save.py; actualízalo y reinícialo. "
            "Puedes reintegrar la estrategia desde la tabla de excluidas."
        )

    def _invalidate_node_snapshots(self, node_id: str) -> None:
        """Obliga a recopiar la memoria del nodo en la proxima lectura.

        La copia se reutiliza mientras el tamano y la mtime del original parezcan
        iguales, y sobre un bind mount esos atributos van por detras del contenido
        real. Tras una escritura hecha por el nodo hay que borrar la firma a mano o
        el manager sigue sirviendo la copia vieja.

        No propaga errores: se llama despues de que la escritura del nodo ya se ha
        confirmado, y fallar aqui convertiria una operacion correcta en un error.
        """
        node = self._node(node_id)
        # Sin portfolio_project_dir el manager no lee la memoria, la proxifica al
        # nodo: no hay copia que invalidar.
        if not str(node.get("portfolio_project_dir") or "").strip():
            return
        try:
            source = PortfolioSource(node)
            for _account, memory in source.memory_sources:
                source._invalidate_remote_snapshot(memory)
        except (ValueError, OSError):
            return

    def invalidate_after_exclusion(self, node_id: str) -> None:
        source = PortfolioSource(self._node(node_id))
        for _account, memory in source.memory_sources:
            source._invalidate_remote_snapshot(memory)
        self._drop_cached_proposals(node_id)

    def release(self, node_id: str, scope: str, quarantine_id: str | int) -> None:
        # La clave de cuarentena lleva la etiqueta de la memoria que la guarda.
        # En Grid esa memoria es la base del manager, que solo aparece en las
        # fuentes de cálculo de ese ámbito.
        self.requalify(node_id, scope, quarantine_id, "pool")

    def requalify(self, node_id: str, scope: str, quarantine_id: str | int, reason_code: str) -> str:
        """Mueve una estrategia excluida entre los tres motivos y el pool.

        Quién ejecuta la escritura lo decide la memoria, no el ámbito. Cuando el
        manager la ve por un recurso de red o un bind mount de Docker —el caso de
        cualquier nodo que no sea local— no puede escribirla: abrir en modo WAL
        falla con "disk I/O error" porque ese sistema de ficheros no respalda el
        `-shm`. Ahí la operación va al nodo, que la tiene en local, exactamente
        como la exclusión y el borrado. Con la memoria en local la escribe el
        manager, que es el único caso en el que esto funcionaba antes.
        """
        # La clave de cuarentena lleva la etiqueta de la memoria que la guarda.
        # En Grid esa memoria es la base del manager, que solo aparece en las
        # fuentes de cálculo de ese ámbito.
        source = self._calculation_source(node_id, scope)
        memory, _quarantine_row_id = source._quarantine_memory(quarantine_id)
        if PortfolioSource.write_needs_node(memory):
            target = self._requalify_on_node(node_id, scope, quarantine_id, reason_code)
        else:
            target = source.requalify_strategy(quarantine_id, reason_code)
        # Reclasificar devuelve y vuelve a escribir filas de etapa en la memoria
        # del broker: hay que tirar la copia como en cualquier escritura.
        self.invalidate_after_exclusion(node_id)
        return target

    def _requalify_on_node(self, node_id: str, scope: str, quarantine_id: str | int, reason_code: str) -> str:
        """Pide al nodo que reclasifique, porque la memoria no es escribible aquí.

        Mismo patrón que `exclude` y `_delete_on_node`: la copia de
        `manager_node_runtime/` es distinta en cada agente y se porta a mano, así
        que un nodo sin portar devuelve 404 y hay que decir qué falta en vez de
        propagar un «Ruta no encontrada» que no explica nada.
        """
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                "Esta memoria solo puede escribirla el nodo del agente (el manager la ve por "
                "red o por un bind mount), y este nodo no tiene URL HTTP configurada en "
                "manager.json."
            )
        status, value = self._post_to_node(
            node,
            "/api/v1/portfolios/requalify",
            {"scope": normalize_portfolio_scope(scope), "quarantine_id": str(quarantine_id), "reason_code": reason_code},
        )
        if status == 404:
            raise ValueError(
                "El nodo todavía no admite cambiar el estado de una estrategia excluida: "
                "falta portar /api/v1/portfolios/requalify a su manager_node_runtime/ "
                "(node.py y portfolio_save.py) y reiniciar la aplicación del agente. "
                "La estrategia sigue excluida como estaba."
            )
        if status >= 400 or not isinstance(value, dict):
            error = value.get("error") if isinstance(value, dict) else value
            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        if not value.get("requalified"):
            raise ValueError(
                "El nodo respondió sin confirmar el cambio de estado: la estrategia sigue "
                "excluida como estaba. Comprueba el port de manager_node_runtime/."
            )
        applied = str(value.get("reason_code") or "")
        return "pool" if applied == "pool" else candidate_verdict.normalize_reason_code(applied)

    def undo(self, node_id: str, scope: str, portfolio_id: int) -> int:
        return self._persistence_source(node_id, scope).undo_latest(portfolio_id, scope)

    def delete(self, node_id: str, scope: str, portfolio_id: int) -> dict[str, Any]:
        self._node(node_id)
        key = self._key(node_id, scope)
        task = {
            "id": str(uuid.uuid4()),
            "status": "pending",
            "operation": "delete",
            "portfolio_id": portfolio_id,
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "progress": f"Borrado del portafolio #{portfolio_id} pendiente",
            "error": None,
        }
        with self.lock:
            queue = self.tasks.setdefault(key, [])
            queue.append(task)
            if len(queue) > 20:
                del queue[:-20]
            start_worker = key not in self.task_workers
            if start_worker:
                self.task_workers.add(key)
        if start_worker:
            threading.Thread(target=self._task_worker, args=(node_id, scope), daemon=True).start()
        return dict(task)

    def _task_worker(self, node_id: str, scope: str) -> None:
        key = self._key(node_id, scope)
        while True:
            with self.lock:
                task = next(
                    (item for item in self.tasks.get(key, []) if item.get("status") == "pending"),
                    None,
                )
                if task is None:
                    self.task_workers.discard(key)
                    return
                if (self.jobs.get(key) or {}).get("status") == "running":
                    task["progress"] = "En cola hasta que termine el cálculo actual"
                    wait_for_calculation = True
                else:
                    task.update({
                        "status": "running",
                        "started_at": utc_now(),
                        "progress": f"Borrando portafolio #{task['portfolio_id']}",
                    })
                    wait_for_calculation = False
            if wait_for_calculation:
                time.sleep(0.25)
                continue
            try:
                self._delete_on_node(node_id, scope, int(task["portfolio_id"]))
                with self.lock:
                    task.update({
                        "status": "completed",
                        "finished_at": utc_now(),
                        "progress": f"Portafolio #{task['portfolio_id']} borrado",
                    })
            except Exception as exc:
                with self.lock:
                    task.update({
                        "status": "failed",
                        "finished_at": utc_now(),
                        "progress": "Error al borrar el portafolio",
                        "error": str(exc),
                    })

    def _post_to_node(self, node: dict[str, Any], path: str, payload: dict[str, Any], timeout: int = 60) -> tuple[int, Any]:
        """POST to a node's HTTP API and return (status, parsed_body)."""
        base_url = str(node.get("url") or "").rstrip("/")
        request = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {node.get('token', '')}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, (json.loads(raw) if raw else {"error": str(exc)})
            except json.JSONDecodeError:
                return exc.code, {"error": raw.decode("utf-8", errors="replace") or str(exc)}

    def _delete_on_node(self, node_id: str, scope: str, portfolio_id: int) -> None:
        if normalize_portfolio_scope(scope) == "grid":
            self._persistence_source(node_id, scope).delete_portfolio(portfolio_id, scope)
            return
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            PortfolioSource(node).delete_portfolio(portfolio_id, scope)
            return
        status, payload = self._post_to_node(
            node, "/api/v1/portfolios/delete", {"scope": scope, "portfolio_id": portfolio_id}
        )
        if status == 404:
            raise ValueError("El nodo todavía no admite borrado local; actualiza y reinicia el nodo")
        if status >= 400 or not isinstance(payload, dict):
            error = payload.get("error") if isinstance(payload, dict) else payload
            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        if not payload.get("deleted") or int(payload.get("portfolio_id") or 0) != portfolio_id:
            raise ValueError("El nodo no confirmó el borrado del portafolio")
        source = PortfolioSource(node)
        source._invalidate_remote_snapshot(source.memory)

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
