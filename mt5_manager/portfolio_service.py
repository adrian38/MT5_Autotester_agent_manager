from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zlib
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    BootstrapDrawdownAnalysis,
    OptimizationDecision,
    PortfolioResult,
    PortfolioType,
    StrategyAllocation,
    UnusedSetInfo,
    bootstrap_valley_drawdown,
    evaluate_portfolio,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
    portfolio_display_symbol,
    portfolio_group_key,
    portfolio_group_summary,
    portfolio_symbol_key,
    slice_strategy_sets_to_month,
    summarize_robust_rows,
    validate_strict_monthly_portfolio,
)
from portfolio_manager.mt5_report import StrategyReport, parse_report

from .common import load_json, safe_float, safe_int, save_json, utc_now


ASSET_GROUPS = ("Forex", "Metals", "Indices", "Energies", "Crypto", "Stocks", "Bonds", "Softs")
BROKER_ACCOUNT_TYPES = {"ROBOFOREX": ("ECN", "PRO"), "ICTRADING": ("STANDARD",), "AXI": ("STANDARD", "PREMIUM")}
REMOTE_SNAPSHOT_LOCK = threading.RLock()
PORTFOLIO_TYPES = {
    "aggressive": PortfolioType.AGGRESSIVE,
    "balanced": PortfolioType.BALANCED,
    "conservative": PortfolioType.CONSERVATIVE,
}
TYPE_LABELS = {"aggressive": "Agresivo", "balanced": "Moderado", "conservative": "Conservador"}
LOCKED_VARIANTS = (
    ("aggressive", "Agresivo", PortfolioType.AGGRESSIVE),
    ("balanced", "Moderado", PortfolioType.BALANCED),
    ("conservative", "Conservador", PortfolioType.CONSERVATIVE),
)

COMMON_DEFAULTS: dict[str, Any] = {
    "capital": 10000.0,
    "valley_dd_pct": 10.0,
    "point_dd_pct": 10.0,
    "portfolio_type": "balanced",
    "top_k_per_symbol": 3,
    "max_total_candidates": 30,
    "min_trades_2020_2026": 100,
    "max_units_per_set": None,
    "max_total_units": None,
    "max_units_per_symbol": None,
    "max_sets_per_symbol": 1,
    "run_local_search": True,
    "deep_optimization": True,
    "use_correlation": True,
    "require_3_positive_months_6m": False,
    "grid_off": False,
    "exclude_used_sets": True,
    "min_strategy_recent_contribution_pct": 5.0,
    "dd_reserve_pct": 10.0,
    "search_restarts": 4,
    "max_pair_corr": 0.35,
    "max_downside_corr": 0.25,
    "max_dd_overlap": 0.35,
    "max_portfolio_corr": 0.50,
    "allowed_asset_groups": list(ASSET_GROUPS),
    "margin_profile": "ictrading",
    "max_margin_pct": 100.0,
    "validate_margin": True,
    "enforce_point_dd": False,
}

MONTHLY_DEFAULTS: dict[str, Any] = {
    **COMMON_DEFAULTS,
    "portfolio_scope": "monthly",
    "target_month": 1,
    "min_trades_2020_2026": 15,
    "deep_optimization": False,
    "max_daily_dd": 150.0,
    "daily_dd_full_history": False,
    "exclude_monthly_used": False,
    "corr_with_monthly_portfolios": False,
    "strict_yearly_month_validation": False,
}

_REPORT_CACHE: dict[str, tuple[int, int, StrategyReport]] = {}
_REPORT_CACHE_LOCK = threading.RLock()


def cached_report(path: Path) -> StrategyReport:
    resolved = path.resolve()
    stat = resolved.stat()
    key = str(resolved).casefold()
    with _REPORT_CACHE_LOCK:
        cached = _REPORT_CACHE.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
    parsed = parse_report(resolved)
    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE[key] = (stat.st_mtime_ns, stat.st_size, parsed)
    return parsed


def _optional_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    parsed = safe_int(value, -1)
    if parsed < 1:
        raise ValueError(f"{label} debe ser un entero mayor que 0")
    return parsed


def _optional_corr(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    parsed = safe_float(value, -1.0)
    if not 0 <= parsed <= 1:
        raise ValueError(f"{label} debe estar entre 0 y 1")
    return parsed


def normalize_settings(scope: str, raw: dict[str, Any], broker: str = "ICTRADING") -> dict[str, Any]:
    monthly = scope == "monthly"
    values = dict(MONTHLY_DEFAULTS if monthly else COMMON_DEFAULTS)
    values["margin_profile"] = str(broker or "ICTRADING").strip().lower()
    values.update(raw)
    values["portfolio_scope"] = "monthly" if monthly else "full_history"
    values["capital"] = safe_float(values.get("capital"), 0)
    values["valley_dd_pct"] = safe_float(values.get("valley_dd_pct"), 0)
    values["point_dd_pct"] = values["valley_dd_pct"]
    values["enforce_point_dd"] = False
    if values["capital"] <= 0 or values["valley_dd_pct"] <= 0:
        raise ValueError("Capital y DD valle deben ser mayores que 0")
    type_key = str(values.get("portfolio_type") or "balanced").strip().lower()
    if type_key not in PORTFOLIO_TYPES:
        raise ValueError("portfolio_type debe ser aggressive, balanced o conservative")
    values["portfolio_type"] = type_key
    for key, minimum in (("top_k_per_symbol", 1), ("max_total_candidates", 1), ("min_trades_2020_2026", 0), ("max_sets_per_symbol", 1), ("search_restarts", 0)):
        values[key] = safe_int(values.get(key), -1)
        if values[key] < minimum:
            raise ValueError(f"{key} debe ser >= {minimum}")
    for key in ("max_units_per_set", "max_total_units", "max_units_per_symbol"):
        values[key] = _optional_int(values.get(key), key)
    values["dd_reserve_pct"] = safe_float(values.get("dd_reserve_pct"), -1)
    if not 0 <= values["dd_reserve_pct"] < 100:
        raise ValueError("dd_reserve_pct debe estar entre 0 y menos de 100")
    values["min_strategy_recent_contribution_pct"] = safe_float(
        values.get("min_strategy_recent_contribution_pct"), -1
    )
    if not 0 <= values["min_strategy_recent_contribution_pct"] <= 100:
        raise ValueError("min_strategy_recent_contribution_pct debe estar entre 0 y 100")
    values["max_margin_pct"] = safe_float(values.get("max_margin_pct"), 0)
    if values["max_margin_pct"] <= 0:
        raise ValueError("max_margin_pct debe ser mayor que 0")
    values["margin_profile"] = str(values.get("margin_profile") or broker).strip().lower()
    for key in ("max_pair_corr", "max_downside_corr", "max_dd_overlap", "max_portfolio_corr"):
        values[key] = _optional_corr(values.get(key), key)
    boolean_keys = (
        "run_local_search", "deep_optimization", "use_correlation",
        "require_3_positive_months_6m", "grid_off", "exclude_used_sets",
        "validate_margin", "daily_dd_full_history", "exclude_monthly_used",
        "corr_with_monthly_portfolios", "strict_yearly_month_validation",
    )
    for key in boolean_keys:
        values[key] = bool(values.get(key))
    if not values["use_correlation"]:
        for key in ("max_pair_corr", "max_downside_corr", "max_dd_overlap", "max_portfolio_corr"):
            values[key] = None
    groups = [str(value) for value in values.get("allowed_asset_groups") or [] if str(value) in ASSET_GROUPS]
    if not groups:
        raise ValueError("Selecciona al menos un grupo de activos")
    values["allowed_asset_groups"] = sorted(set(groups))
    if monthly:
        values["target_month"] = safe_int(values.get("target_month"), 0)
        if not 1 <= values["target_month"] <= 12:
            raise ValueError("target_month debe estar entre 1 y 12")
        values["max_daily_dd"] = safe_float(values.get("max_daily_dd"), 0)
        if values["max_daily_dd"] <= 0:
            raise ValueError("max_daily_dd debe ser mayor que 0")
    return values


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")


def ensure_portfolio_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate the same persistence surface used by the desktop portfolio UI."""
    conn.execute(
        """
        create table if not exists portfolios (
            id integer primary key autoincrement, created_at text not null,
            name text not null default '', type text not null default '',
            portfolio_type text not null default 'balanced', num_symbols integer not null default 0,
            account_capital real not null default 0, capital real not null default 0,
            target_valley_dd_pct real not null default 0, target_point_dd_pct real not null default 0,
            target_valley_dd real not null default 0, target_point_dd real not null default 0,
            actual_valley_dd real not null default 0, actual_point_dd real not null default 0,
            actual_closed_valley_dd real not null default 0, floating_dd_buffer real not null default 0,
            valley_usage_pct real not null default 0, point_usage_pct real not null default 0,
            total_net_profit real not null default 0, total_lot real not null default 0,
            total_units integer not null default 0, active_strategies integer not null default 0,
            target_strategies integer not null default 0, stop_reason text not null default '',
            scale_factor real, binding_constraint text,
            portfolio_scope text not null default 'full_history', target_month integer, metrics_json text
        )
        """
    )
    portfolio_columns = (
        ("name", "text not null default ''"), ("type", "text not null default ''"),
        ("portfolio_type", "text not null default 'balanced'"), ("num_symbols", "integer not null default 0"),
        ("account_capital", "real not null default 0"), ("capital", "real not null default 0"),
        ("target_valley_dd_pct", "real not null default 0"), ("target_point_dd_pct", "real not null default 0"),
        ("target_valley_dd", "real not null default 0"), ("target_point_dd", "real not null default 0"),
        ("actual_valley_dd", "real not null default 0"), ("actual_point_dd", "real not null default 0"),
        ("actual_closed_valley_dd", "real not null default 0"), ("floating_dd_buffer", "real not null default 0"),
        ("valley_usage_pct", "real not null default 0"), ("point_usage_pct", "real not null default 0"),
        ("total_net_profit", "real not null default 0"), ("total_lot", "real not null default 0"),
        ("total_units", "integer not null default 0"), ("active_strategies", "integer not null default 0"),
        ("target_strategies", "integer not null default 0"), ("stop_reason", "text not null default ''"),
        ("scale_factor", "real"), ("binding_constraint", "text"),
        ("portfolio_scope", "text not null default 'full_history'"), ("target_month", "integer"),
        ("metrics_json", "text"),
    )
    for column, definition in portfolio_columns:
        _ensure_column(conn, "portfolios", column, definition)
    conn.execute(
        """
        create table if not exists portfolio_allocations (
            id integer primary key autoincrement, portfolio_id integer not null,
            variant_key text not null default '', variant_label text not null default '',
            set_id text not null, candidate_id text not null, symbol text not null,
            units integer not null, lot real not null, net_profit_contribution real not null,
            standalone_valley_dd real not null, standalone_point_dd real not null,
            set_path text, timeframe text, lot_size_step real,
            margin_required real not null default 0, margin_pct real not null default 0,
            margin_leverage real not null default 0, margin_contract_size real not null default 0,
            margin_price real not null default 0, is_report_path text, oos_report_path text,
            final_tick_report_path text, full_history_report_path text
            , max_balance_dd_001 real not null default 0
            , max_equity_dd_001 real not null default 0
            , floating_dd_source text not null default ''
            , standalone_floating_dd real not null default 0
            , recent_net_profit_001 real not null default 0
            , recent_equity_dd_001 real not null default 0
            , has_recent_performance integer not null default 0
        )
        """
    )
    for column, definition in (
        ("variant_key", "text not null default ''"), ("variant_label", "text not null default ''"),
        ("margin_required", "real not null default 0"), ("margin_pct", "real not null default 0"),
        ("margin_leverage", "real not null default 0"), ("margin_contract_size", "real not null default 0"),
        ("margin_price", "real not null default 0"),
        ("final_tick_report_path", "text"),
        ("full_history_report_path", "text"),
        ("max_balance_dd_001", "real not null default 0"),
        ("max_equity_dd_001", "real not null default 0"),
        ("floating_dd_source", "text not null default ''"),
        ("standalone_floating_dd", "real not null default 0"),
        ("recent_net_profit_001", "real not null default 0"),
        ("recent_equity_dd_001", "real not null default 0"),
        ("has_recent_performance", "integer not null default 0"),
    ):
        _ensure_column(conn, "portfolio_allocations", column, definition)
    conn.execute(
        """
        create table if not exists portfolio_decision_log (
            id integer primary key autoincrement, portfolio_id integer not null,
            step integer not null, action text not null, set_id text, from_set_id text, to_set_id text,
            gain real not null, valley_cost real not null, point_cost real not null, score real not null,
            portfolio_net_profit_after real not null, portfolio_valley_dd_after real not null,
            portfolio_point_dd_after real not null, reason text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists portfolio_members (
            id integer primary key autoincrement, portfolio_id integer not null,
            variant_key text not null default '', variant_label text not null default '',
            candidate_id integer, set_path text not null, symbol text, period text,
            lot_multiplier real, lot real, lot_size_step real, standalone_dd real,
            quality_score real, combined_net_profit real, is_report_path text, oos_report_path text
        )
        """
    )
    for column, definition in (("variant_key", "text not null default ''"), ("variant_label", "text not null default ''")):
        _ensure_column(conn, "portfolio_members", column, definition)
    conn.execute(
        """
        create table if not exists portfolio_quarantine (
            id integer primary key autoincrement, account_type text not null, candidate_id integer,
            set_path text not null unique, symbol text, timeframe text, reason text not null default '',
            source_portfolio_id integer, quarantined_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists portfolio_versions (
            id integer primary key autoincrement, portfolio_id integer not null,
            version_no integer not null, created_at text not null, reason text not null,
            snapshot_json blob not null, unique(portfolio_id, version_no)
        )
        """
    )
    conn.commit()


def _resolve_source_path(value: Any, project: Path) -> str:
    path = Path(str(value or "")).expanduser()
    if path.exists():
        return str(path.resolve())
    parts = path.parts
    lowered = [part.lower() for part in parts]
    for root in ("outputs", "sets", "reports", "configs", "assets"):
        if root in lowered:
            candidate = project.joinpath(*parts[lowered.index(root):])
            # A DB produced on another PC stores that PC's drive letter. Once
            # a known project root is found, relocate it deterministically;
            # checking hundreds of individual paths over SMB makes inventory
            # refreshes needlessly slow and does not improve the mapping.
            return str(candidate.absolute())
    if not path.is_absolute():
        candidate = project / path
        return str(candidate.absolute())
    return str(path)


class PortfolioSource:
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
        if not self.memory.is_file():
            raise ValueError(f"No existe la memoria UBS: {self.memory}")

    @contextlib.contextmanager
    def connect(self, *, write: bool = False):
        with self.connect_memory(self.memory, write=write) as conn:
            yield conn

    @staticmethod
    def _is_remote_memory(memory: Path) -> bool:
        if os.name != "nt":
            return False
        if not memory.drive:
            return str(memory).startswith("\\\\")
        try:
            import ctypes

            return ctypes.windll.kernel32.GetDriveTypeW(f"{memory.drive}\\") == 4  # DRIVE_REMOTE
        except (AttributeError, OSError):
            return False

    def _snapshot_path(self, memory: Path) -> Path:
        node_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(self.node.get("id") or self.broker))
        root = Path(__file__).resolve().parents[1] / "runtime" / "portfolio_snapshots" / node_id
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
        remote = self._is_remote_memory(memory)
        source_memory = memory
        remote_lock = False
        conn: sqlite3.Connection | None = None
        try:
            if write:
                try:
                    conn = sqlite3.connect(memory, timeout=10 if remote else 30)
                    ensure_portfolio_schema(conn)
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        raise ValueError(
                            f"No se pudo guardar: {memory.name} está bloqueada por otro proceso "
                            "(por ejemplo, una generación activa). La propuesta sigue disponible; "
                            "inténtalo de nuevo cuando termine."
                        ) from exc
                    raise
            else:
                if remote:
                    REMOTE_SNAPSHOT_LOCK.acquire()
                    remote_lock = True
                    memory = self._remote_read_snapshot(memory)
                conn = sqlite3.connect(memory.as_uri() + "?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if conn is not None:
                conn.close()
            if write and remote and conn is not None:
                self._invalidate_remote_snapshot(source_memory)
            if remote_lock:
                REMOTE_SNAPSHOT_LOCK.release()

    def candidate_rows(self, *, include_quarantined: bool) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                quarantine = _table_exists(conn, "portfolio_quarantine")
                exclusion = "" if include_quarantined or not quarantine else (
                    "and not exists (select 1 from portfolio_quarantine pq where pq.set_path=c.set_path)"
                )
                rows = conn.execute(
                    f"""
                    select ? as account_type, ? || ':' || c.id as candidate_id,
                           c.id as source_candidate_id, c.set_path, c.symbol, c.target_symbol,
                           c.period, c.family, c.report_path as is_report_path,
                           cr.report_path as oos_report_path,
                           ft6.ohlc_report_path as final_ohlc_report_path,
                           ft6.real_tick_report_path as final_tick_report_path,
                           ft6.from_date as final_tick_from_date, ft6.to_date as final_tick_to_date
                    from candidates c join candidate_robustness cr on cr.candidate_id=c.id
                    join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
                    where c.status='accepted' and cr.status='accepted' and ft6.status='accepted'
                    {exclusion} order by c.id
                    """, (account_label, account_label),
                ).fetchall()
            for db_row in rows:
                item = dict(db_row)
                item["source_memory_path"] = str(memory)
                result.append(item)
        for row in result:
            for key in (
                "set_path", "is_report_path", "oos_report_path", "full_history_report_path",
                "final_ohlc_report_path", "final_tick_report_path",
            ):
                row[key] = _resolve_source_path(row.get(key), self.project)
        return result

    @staticmethod
    def _path_key(value: Any) -> str:
        return str(Path(str(value or "")).expanduser()).replace("/", "\\").casefold()

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
                result.append(item)
        return sorted(result, key=lambda item: (str(item.get("quarantined_at") or ""), int(item.get("id") or 0)), reverse=True)

    def inventory(self, scope: str, settings: dict[str, Any]) -> dict[str, Any]:
        monthly = scope == "monthly"
        rows = self.candidate_rows(include_quarantined=True)
        allowed = set(settings.get("allowed_asset_groups") or ASSET_GROUPS)
        rows = [
            row for row in rows
            if portfolio_group_key(str(row.get("target_symbol") or row.get("symbol") or ""), universe_files=[self.universe]) in allowed
        ]
        warnings: list[str] = []
        if settings.get("grid_off"):
            rows, warnings = filter_rows_grid_off(rows)
        quarantine = self.quarantine_rows()
        quarantined = {self._path_key(row.get("set_path")) for row in quarantine}
        used_paths: list[str] = []
        if monthly and settings.get("exclude_monthly_used"):
            used_paths = self.used_set_paths("monthly")
        elif not monthly and settings.get("exclude_used_sets", True):
            used_paths = self.used_set_paths("full_history")
        used = {self._path_key(path) for path in used_paths}
        by_symbol: dict[str, dict[str, int]] = {}
        for row in rows:
            symbol = portfolio_display_symbol(str(row.get("target_symbol") or row.get("symbol") or ""))
            counts = by_symbol.setdefault(symbol, {"total": 0, "quarantined": 0, "used": 0, "available": 0})
            counts["total"] += 1
            key = self._path_key(row.get("set_path"))
            is_quarantined = key in quarantined
            is_used = key in used
            if is_quarantined:
                counts["quarantined"] += 1
            if is_used:
                counts["used"] += 1
            if (monthly or not is_quarantined) and not is_used:
                counts["available"] += 1
        symbol_rows = [{"symbol": symbol, **counts} for symbol, counts in sorted(by_symbol.items())]
        return {
            "scope": "monthly" if monthly else "full_history",
            "total": sum(row["total"] for row in symbol_rows),
            "quarantined": sum(row["quarantined"] for row in symbol_rows),
            "used": sum(row["used"] for row in symbol_rows),
            "available": sum(row["available"] for row in symbol_rows),
            "symbols": len(symbol_rows),
            "by_symbol": symbol_rows,
            "quarantine": quarantine,
            "quarantine_excludes": not monthly,
            "warnings": warnings,
        }

    def exclude_strategy(self, payload: dict[str, Any]) -> int:
        requested = str(payload.get("set_path") or payload.get("set_id") or "").strip()
        if not requested:
            raise ValueError("Falta identificar el set que se quiere excluir")
        candidates = self.candidate_rows(include_quarantined=True)
        requested_key = self._path_key(_resolve_source_path(requested, self.project))
        matches = [row for row in candidates if self._path_key(row.get("set_path")) == requested_key]
        if not matches:
            by_name = [row for row in candidates if Path(str(row.get("set_path") or "")).name.casefold() == Path(requested).name.casefold()]
            if len(by_name) == 1:
                matches = by_name
        if not matches:
            raise ValueError("El set no pertenece a los candidatos Final Tick 6M accepted")
        row = matches[0]
        source_memory = Path(str(row.get("source_memory_path") or self.memory)).absolute()
        account_label = str(row.get("account_type") or f"{self.broker}/{self.account}")
        with self.connect_memory(source_memory, write=True) as conn:
            conn.execute(
                """
                create table if not exists portfolio_quarantine (
                    id integer primary key autoincrement,account_type text not null,candidate_id,
                    set_path text not null unique,symbol text,timeframe text,reason text not null default '',
                    source_portfolio_id integer,quarantined_at text not null
                )
                """
            )
            conn.execute(
                """
                insert into portfolio_quarantine(account_type,candidate_id,set_path,symbol,timeframe,reason,source_portfolio_id,quarantined_at)
                values(?,?,?,?,?,?,?,?)
                on conflict(set_path) do update set account_type=excluded.account_type,candidate_id=excluded.candidate_id,
                    symbol=excluded.symbol,timeframe=excluded.timeframe,reason=excluded.reason,
                    source_portfolio_id=excluded.source_portfolio_id,quarantined_at=excluded.quarantined_at
                """,
                (
                    account_label, row.get("source_candidate_id"), row.get("set_path"),
                    portfolio_display_symbol(str(row.get("target_symbol") or row.get("symbol") or "")), row.get("period"),
                    str(payload.get("reason") or "Excluida manualmente desde el manager"),
                    safe_int(payload.get("portfolio_id"), 0) or None,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            saved = conn.execute("select id from portfolio_quarantine where set_path=?", (row.get("set_path"),)).fetchone()
            conn.commit()
        return int(saved[0])

    def release_strategy(self, quarantine_key: str | int) -> None:
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
        with self.connect_memory(memory, write=True) as conn:
            if not _table_exists(conn, "portfolio_quarantine"):
                raise ValueError("No existe la cuarentena")
            deleted = conn.execute("delete from portfolio_quarantine where id=?", (quarantine_id,))
            if deleted.rowcount != 1:
                raise ValueError("La estrategia excluida ya no existe")
            conn.commit()

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
                for _ in selects:
                    params.extend((scope, exclude_portfolio_id if memory == self.memory else None, exclude_portfolio_id if memory == self.memory else None))
                if selects:
                    paths.update(_resolve_source_path(row[0], self.project) for row in conn.execute(" union ".join(selects), params) if row[0])
        return sorted(paths)

    def saved_curves(
        self,
        *,
        monthly: bool,
        portfolio_type: PortfolioType | None = None,
        exclude_portfolio_id: int | None = None,
    ) -> list[list[float]]:
        curves: list[list[float]] = []
        rows: list[sqlite3.Row] = []
        for _account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolios"):
                    continue
                excluded = exclude_portfolio_id if memory == self.memory else None
                rows.extend(conn.execute(
                    "select id,portfolio_type,type,metrics_json from portfolios where metrics_json is not null "
                    "and metrics_json<>'' and coalesce(nullif(portfolio_scope,''),'full_history')=? and (? is null or id<>?)",
                    ("monthly" if monthly else "full_history", excluded, excluded),
                ).fetchall())
        for row in rows:
            type_key = str(row["portfolio_type"] or row["type"] or "").lower()
            if not monthly and portfolio_type is not None:
                if portfolio_type == PortfolioType.AGGRESSIVE and type_key not in {"aggressive", "bundle"}:
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
        portfolio_scope = "monthly" if scope == "monthly" else "full_history"
        with self.connect() as conn:
            rows = conn.execute(
                "select * from portfolios where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
                (portfolio_scope,),
            ).fetchall() if _table_exists(conn, "portfolios") else []
        value = self._row_value
        portfolios = [{
            "id": int(value(row, "id", 0) or 0),
            "created_at": str(value(row, "created_at", "") or ""),
            "name": str(value(row, "name", "") or ""),
            "portfolio_type": str(value(row, "portfolio_type", value(row, "type", "")) or ""),
            "portfolio_scope": portfolio_scope,
            "target_month": int(value(row, "target_month", 0) or 0) or None,
            "capital": float(value(row, "capital", value(row, "account_capital", 0)) or 0),
            "total_net_profit": float(value(row, "total_net_profit", 0) or 0),
            "actual_valley_dd": float(value(row, "actual_valley_dd", 0) or 0),
            "actual_closed_valley_dd": float(value(row, "actual_closed_valley_dd", 0) or 0),
            "floating_dd_buffer": float(value(row, "floating_dd_buffer", 0) or 0),
            "target_valley_dd": float(value(row, "target_valley_dd", 0) or 0),
            "target_valley_dd_pct": float(value(row, "target_valley_dd_pct", 0) or 0),
            "valley_usage_pct": float(value(row, "valley_usage_pct", 0) or 0),
            "actual_point_dd": float(value(row, "actual_point_dd", 0) or 0),
            "target_point_dd": float(value(row, "target_point_dd", 0) or 0),
            "target_point_dd_pct": float(value(row, "target_point_dd_pct", 0) or 0),
            "point_usage_pct": float(value(row, "point_usage_pct", 0) or 0),
            "total_lot": float(value(row, "total_lot", 0) or 0),
            "total_units": int(value(row, "total_units", 0) or 0),
            "active_strategies": int(value(row, "active_strategies", 0) or 0),
            "target_strategies": int(value(row, "target_strategies", 0) or 0),
            "stop_reason": str(value(row, "stop_reason", "") or ""),
            "binding_constraint": str(value(row, "binding_constraint", "") or ""),
        } for row in rows]
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

    def saved_inputs(self, portfolio_id: int, scope: str) -> dict[str, Any]:
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        return self._saved_inputs_from_detail(detail, scope)

    def _saved_inputs_from_detail(self, detail: dict[str, Any], scope: str) -> dict[str, Any]:
        """Rebuild saved constraints, including rows created before metrics.inputs existed."""
        metrics = detail.get("metrics") if isinstance(detail.get("metrics"), dict) else {}
        stored = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        capital = float(detail.get("capital") or detail.get("account_capital") or 0)
        valley_pct = float(detail.get("target_valley_dd_pct") or 0)
        if valley_pct <= 0 and capital > 0:
            valley_pct = float(detail.get("target_valley_dd") or 0) * 100.0 / capital
        point_pct = float(detail.get("target_point_dd_pct") or 0) or valley_pct
        saved_row_type = str(detail.get("portfolio_type") or detail.get("type") or "balanced").lower()
        portfolio_type = saved_row_type
        if saved_row_type == "bundle":
            portfolio_type = str(
                stored.get("composition_portfolio_type")
                or metrics.get("composition_portfolio_type")
                or stored.get("portfolio_type")
                or "balanced"
            ).lower()
        values: dict[str, Any] = {
            "capital": capital,
            "valley_dd_pct": valley_pct,
            "point_dd_pct": point_pct,
            "portfolio_type": portfolio_type,
            "top_k_per_symbol": 3,
            "max_total_candidates": 30,
            "min_trades_2020_2026": 15 if scope == "monthly" else 100,
            "max_units_per_set": None,
            "max_total_units": None,
            "max_units_per_symbol": None,
            "max_sets_per_symbol": 1,
            "run_local_search": True,
            "deep_optimization": False,
            "use_correlation": True,
            "require_3_positive_months_6m": False,
            "grid_off": False,
            "exclude_used_sets": True,
            "min_strategy_recent_contribution_pct": COMMON_DEFAULTS["min_strategy_recent_contribution_pct"],
            "exclude_monthly_used": False,
            "corr_with_monthly_portfolios": False,
            "strict_yearly_month_validation": False,
            "daily_dd_full_history": False,
            "dd_reserve_pct": 0.0,
            "search_restarts": 0,
            "max_pair_corr": 0.35,
            "max_downside_corr": 0.25,
            "max_dd_overlap": 0.35,
            "max_portfolio_corr": 0.50,
            "allowed_asset_groups": list(ASSET_GROUPS),
            "margin_profile": self.broker.lower(),
            "max_margin_pct": 100.0,
            "validate_margin": True,
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
        if saved_row_type == "bundle":
            values["portfolio_type"] = portfolio_type
        if scope == "monthly":
            values["target_month"] = values.get("target_month") or detail.get("target_month")

        # Match the desktop migration for portfolios saved before the eight-group universe.
        stored_groups = set(values.get("allowed_asset_groups") or [])
        legacy_groups = not stored_groups.intersection({"Indices", "Energies", "Crypto", "Bonds", "Softs"})
        if "IndicesEnergies" in stored_groups:
            stored_groups.remove("IndicesEnergies")
            stored_groups.update(("Indices", "Energies"))
        if legacy_groups:
            stored_groups.update(("Crypto", "Bonds", "Softs"))
        if stored_groups:
            values["allowed_asset_groups"] = sorted(stored_groups)
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
        try:
            metrics = json.loads(portfolio["metrics_json"] or "{}")
            if not isinstance(metrics, dict):
                metrics = {}
        except (json.JSONDecodeError, TypeError):
            metrics = {}
        if not rows:
            metrics.update({"equity_curve_2020_2026": [0.0], "group_summary": {}, "seasonal_coverage": {}, "seasonal_validation": {}})
            metrics["stress_bootstrap"] = asdict(bootstrap_valley_drawdown(
                [0.0],
                nominal_valley_dd_limit=float(portfolio["capital"] or portfolio["account_capital"] or 0) * float(portfolio["target_valley_dd_pct"] or 0) / 100.0,
                effective_valley_dd_limit=float(portfolio["target_valley_dd"] or 0),
            ))
            conn.execute(
                "update portfolios set num_symbols=0,actual_valley_dd=0,actual_point_dd=0,actual_closed_valley_dd=0,floating_dd_buffer=0,valley_usage_pct=0,point_usage_pct=0,total_net_profit=0,total_lot=0,total_units=0,active_strategies=0,metrics_json=? where id=?",
                (json.dumps(metrics, ensure_ascii=True), portfolio_id),
            )
            return
        source_rows = [{
            "candidate_id": row.get("candidate_id"), "set_path": row.get("set_path") or row.get("set_id"),
            "symbol": row.get("symbol"), "target_symbol": row.get("symbol"), "period": row.get("timeframe"),
            "family": "", "is_report_path": row.get("is_report_path"), "oos_report_path": row.get("oos_report_path"),
            "max_balance_dd_001": row.get("max_balance_dd_001"),
            "max_equity_dd_001": row.get("max_equity_dd_001"),
            "floating_dd_source": row.get("floating_dd_source"),
            "recent_net_profit_001": row.get("recent_net_profit_001"),
            "recent_equity_dd_001": row.get("recent_equity_dd_001"),
            "has_recent_performance": row.get("has_recent_performance"),
            "final_tick_report_path": row.get("final_tick_report_path"),
            "full_history_report_path": row.get("full_history_report_path"),
        } for row in rows]
        strategies, warnings = load_robust_sets_from_rows(source_rows, [], parse=cached_report)
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
        metrics.update({
            "equity_curve_2020_2026": evaluation.equity_curve_2020_2026,
            "group_summary": portfolio_group_summary(strategies, units),
            "actual_closed_valley_dd": evaluation.closed_valley_dd,
            "floating_dd_buffer": evaluation.floating_dd_buffer,
            "seasonal_coverage": {
                strategy.set_id: {"target_month": strategy.target_month, "years": list(strategy.month_years),
                    "positive_years": list(strategy.positive_month_years), "year_count": len(strategy.month_years),
                    "positive_year_count": len(strategy.positive_month_years), "trades": strategy.trades_2020_2026}
                for strategy in strategies if strategy.target_month is not None and units.get(strategy.set_id, 0) > 0
            },
            "stress_bootstrap": asdict(bootstrap_valley_drawdown(
                evaluation.equity_curve_2020_2026,
                nominal_valley_dd_limit=float(portfolio["capital"] or portfolio["account_capital"] or 0) * float(portfolio["target_valley_dd_pct"] or 0) / 100.0,
                effective_valley_dd_limit=float(portfolio["target_valley_dd"] or 0),
            )),
        })
        if inputs.get("strict_yearly_month_validation"):
            metrics["seasonal_validation"] = validate_strict_monthly_portfolio(
                full_strategies, units, target_month=int(inputs["target_month"]),
                target_valley_dd=float(portfolio["target_valley_dd"] or 0),
                target_point_dd=float(portfolio["target_point_dd"] or 0), enforce_point_dd=False, lookback_years=5,
            )
        if warnings:
            metrics.setdefault("warnings", []).extend(warnings)
        conn.execute(
            """update portfolios set num_symbols=?,actual_valley_dd=?,actual_point_dd=?,actual_closed_valley_dd=?,floating_dd_buffer=?,valley_usage_pct=?,point_usage_pct=?,
               total_net_profit=?,total_lot=?,total_units=?,active_strategies=?,metrics_json=? where id=?""",
            (len({portfolio_symbol_key(item.symbol) for item in strategies if units.get(item.set_id, 0) > 0}),
             evaluation.valley_dd, evaluation.point_dd, evaluation.closed_valley_dd, evaluation.floating_dd_buffer,
             evaluation.valley_usage_pct, evaluation.point_usage_pct,
             evaluation.total_net_profit, evaluation.total_lot, evaluation.total_units, evaluation.active_strategies,
             json.dumps(metrics, ensure_ascii=True), portfolio_id),
        )

    def remove_member_to_quarantine(self, payload: dict[str, Any], scope: str) -> int:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        if portfolio_id < 1:
            return self.exclude_strategy(payload)
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        is_bundle = scope == "full_history" and (
            str(detail.get("portfolio_type") or "").lower() == "bundle" or detail.get("metrics", {}).get("portfolio_bundle")
        )
        requested = self._path_key(_resolve_source_path(payload.get("set_path") or payload.get("set_id"), self.project))
        member = next((item for item in detail.get("members") or [] if self._path_key(item.get("set_path")) == requested), None)
        if member is None:
            raise ValueError("No se encontró la estrategia dentro del portafolio")
        if is_bundle:
            quarantine_id = self.exclude_strategy({
                **payload,
                "set_path": member.get("set_path") or member.get("set_id"),
                "portfolio_id": portfolio_id,
                "reason": payload.get("reason") or "Excluida manualmente de un portafolio A/M/C eliminado",
            })
            self.delete_portfolio(portfolio_id, scope)
            return quarantine_id
        candidate_text = str(member.get("candidate_id") or "")
        candidate_id = safe_int(candidate_text.rsplit(":", 1)[-1], 0) or None
        account_label = candidate_text.rsplit(":", 1)[0] if ":" in candidate_text else f"{self.broker}/{self.account}"
        source_memory = next((path for label, path in self.memory_sources if label == account_label), self.memory)
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        with self.connect_memory(source_memory, write=True) as source_conn:
            source_conn.execute(
                """insert into portfolio_quarantine(account_type,candidate_id,set_path,symbol,timeframe,reason,source_portfolio_id,quarantined_at)
                   values(?,?,?,?,?,?,?,?) on conflict(set_path) do update set account_type=excluded.account_type,
                   candidate_id=excluded.candidate_id,symbol=excluded.symbol,timeframe=excluded.timeframe,
                   reason=excluded.reason,source_portfolio_id=excluded.source_portfolio_id,quarantined_at=excluded.quarantined_at""",
                (account_label, candidate_id, set_path, str(member.get("symbol") or ""), str(member.get("timeframe") or ""),
                 str(payload.get("reason") or "Retirada manualmente de un portafolio guardado"), portfolio_id,
                 datetime.now().isoformat(timespec="seconds")),
            )
            quarantine_id = int(source_conn.execute("select id from portfolio_quarantine where set_path=?", (set_path,)).fetchone()[0])
            source_conn.commit()
        with self.connect(write=True) as conn:
            row = conn.execute("select active_strategies,target_strategies from portfolios where id=?", (portfolio_id,)).fetchone()
            if row is None:
                raise ValueError("El portafolio ya no existe")
            target = max(int(row[0] or 0), int(row[1] or 0))
            conn.execute("update portfolios set target_strategies=? where id=?", (target, portfolio_id))
            allocations = conn.execute("select id,set_path from portfolio_allocations where portfolio_id=?", (portfolio_id,)).fetchall()
            ids = [int(row["id"]) for row in allocations if self._path_key(row["set_path"]) == requested]
            if not ids:
                raise ValueError("No se encontró la asignación dentro del portafolio")
            conn.executemany("delete from portfolio_allocations where id=?", [(value,) for value in ids])
            members = conn.execute("select id,set_path from portfolio_members where portfolio_id=?", (portfolio_id,)).fetchall()
            member_ids = [int(row["id"]) for row in members if self._path_key(row["set_path"]) == requested]
            conn.executemany("delete from portfolio_members where id=?", [(value,) for value in member_ids])
            self._recalculate_saved(conn, portfolio_id)
            conn.commit()
        return quarantine_id

    def open_member_report(self, portfolio_id: int, scope: str, set_path: str) -> str:
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        requested = self._path_key(set_path)
        member = next((item for item in detail["members"] if self._path_key(item.get("set_path")) == requested), None)
        if member is None:
            raise ValueError("La estrategia no pertenece al portafolio")
        report = str(member.get("oos_report_path") or member.get("is_report_path") or "")
        report = _resolve_source_path(report, self.project)
        if not report or not Path(report).is_file():
            raise ValueError("La estrategia no tiene un reporte disponible")
        if hasattr(os, "startfile"):
            os.startfile(report)  # type: ignore[attr-defined]
        return report

    def export_portfolio(self, portfolio_id: int, scope: str, destination: str | None = None) -> dict[str, Any]:
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        members = detail.get("members") or []
        if not members:
            raise ValueError("El portafolio no tiene estrategias para exportar")
        root = Path(destination).expanduser() if destination else self.project / "exports"
        created = str(detail.get("created_at") or "").replace("T", "_").replace(":", "").replace("-", "")
        label = "A_M_C" if str(detail.get("portfolio_type") or "").lower() == "bundle" else str(detail.get("portfolio_type") or "Portfolio")
        folder_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"PORTAFOLIO_{portfolio_id}_{label}_{created[:15]}").strip("._")
        output = root.resolve() / (folder_name or f"PORTAFOLIO_{portfolio_id}")
        output.mkdir(parents=True, exist_ok=True)
        copied: set[str] = set()
        exported: list[dict[str, Any]] = []
        missing: list[str] = []
        for member in members:
            source_path = Path(_resolve_source_path(member.get("set_path") or member.get("set_id"), self.project))
            if not source_path.is_file():
                missing.append(source_path.name)
                continue
            destination_path = output / source_path.name
            key = str(source_path.resolve()).casefold()
            if key not in copied:
                shutil.copy2(source_path, destination_path)
                copied.add(key)
            exported.append({
                "variant": member.get("variant_label") or member.get("variant_key") or "",
                "account": str(member.get("candidate_id") or "").split(":", 1)[0] or self.account,
                "symbol": member.get("symbol") or "", "timeframe": member.get("timeframe") or "",
                "units": int(member.get("units") or 0), "lot": float(member.get("lot") or 0), "set": source_path.name,
            })
        lines = [
            f"Portafolio: {detail.get('name') or portfolio_id}",
            f"Tipo: {detail.get('portfolio_type') or ''}   Capital: {float(detail.get('capital') or 0):,.0f}",
            f"DD valle objetivo: {float(detail.get('target_valley_dd') or 0):,.2f}",
            f"DD puntual objetivo: {float(detail.get('target_point_dd') or 0):,.2f}",
            f"DD valle usado: {float(detail.get('actual_valley_dd') or 0):,.2f}",
            f"DD puntual usado: {float(detail.get('actual_point_dd') or 0):,.2f}",
            f"Net profit total 2020-2026: {float(detail.get('total_net_profit') or 0):,.2f}", "",
            "Sets exportados: copia exacta del .set original probado.",
            "No se modifica Risk, LotPerBalance_step, grid ni ningún otro parámetro del EA.",
            "UNID. y LOTE son la asignación informativa calculada por el portafolio.", "",
            f"{'PERFIL':12s} {'CUENTA':12s} {'SIMBOLO':12s} {'TF':5s} {'UNID.':>7s} {'LOTE':>7s}   SET",
        ]
        for item in exported:
            lines.append(f"{str(item['variant'])[:12]:12s} {str(item['account'])[:12]:12s} {str(item['symbol']):12s} {str(item['timeframe']):5s} {item['units']:7d} {item['lot']:7.2f}   {item['set']}")
        if missing:
            lines.extend(("", "OMITIDOS (set no encontrado): " + ", ".join(missing)))
        summary = output / f"PORTAFOLIO_{portfolio_id}_resumen.txt"
        summary.write_text("\n".join(lines), encoding="utf-8")
        return {"folder": str(output), "summary": str(summary), "exported": len(exported), "missing": missing}

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


def _reserve_pct(configured: float, portfolio_type: PortfolioType) -> float:
    if portfolio_type == PortfolioType.CONSERVATIVE:
        return max(configured, 25.0)
    if portfolio_type == PortfolioType.BALANCED:
        return max(configured, 15.0)
    return configured


def _optimizer_kwargs(
    inputs: dict[str, Any],
    objective_type: PortfolioType,
    existing_curves: list[list[float]],
    reserve: float,
) -> dict[str, Any]:
    use_corr = bool(inputs.get("use_correlation", True))
    validate_margin = bool(inputs.get("validate_margin", True))
    return {
        "capital": float(inputs["capital"]),
        "valley_dd_pct": float(inputs["valley_dd_pct"]),
        "point_dd_pct": float(inputs["point_dd_pct"]),
        "portfolio_type": objective_type,
        "min_trades_2020_2026": int(inputs["min_trades_2020_2026"]),
        "top_k_per_symbol": int(inputs["top_k_per_symbol"]),
        "max_total_candidates": int(inputs["max_total_candidates"]),
        "max_units_per_set": inputs.get("max_units_per_set"),
        "max_total_units": inputs.get("max_total_units"),
        "max_units_per_symbol": inputs.get("max_units_per_symbol"),
        "max_sets_per_symbol": inputs.get("max_sets_per_symbol"),
        "run_local_search": bool(inputs.get("run_local_search", True)),
        "max_pair_corr": inputs.get("max_pair_corr") if use_corr else None,
        "max_downside_corr": inputs.get("max_downside_corr") if use_corr else None,
        "max_dd_overlap": inputs.get("max_dd_overlap") if use_corr else None,
        "existing_portfolio_curves": existing_curves,
        "max_portfolio_corr": inputs.get("max_portfolio_corr") if use_corr else None,
        "dd_reserve_pct": reserve,
        "search_restarts": int(inputs.get("search_restarts") or 0),
        "margin_balance": float(inputs["capital"]) if validate_margin else None,
        "max_margin_pct": float(inputs.get("max_margin_pct") or 100.0) if validate_margin else None,
        "margin_profile": str(inputs.get("margin_profile") or "ictrading"),
        "stock_leverage": 20.0,
        "default_leverage": 500.0,
        "stock_contract_size": 100.0,
        "default_contract_size": 1.0,
        "max_daily_dd": inputs.get("max_daily_dd"),
        "enforce_point_dd": bool(inputs.get("enforce_point_dd", False)),
        "daily_dd_full_history": bool(inputs.get("daily_dd_full_history", False)),
    }


def _seasonal_coverage(result: PortfolioResult, strategies: list[Any]) -> None:
    by_id = {strategy.set_id: strategy for strategy in strategies}
    result.seasonal_coverage = {
        allocation.set_id: {
            "target_month": by_id[allocation.set_id].target_month,
            "years": list(by_id[allocation.set_id].month_years),
            "positive_years": list(by_id[allocation.set_id].positive_month_years),
            "year_count": len(by_id[allocation.set_id].month_years),
            "positive_year_count": len(by_id[allocation.set_id].positive_month_years),
            "trades": by_id[allocation.set_id].trades_2020_2026,
        }
        for allocation in result.allocations
        if allocation.set_id in by_id and by_id[allocation.set_id].target_month is not None
    }


def _underrepresented_recent_allocation_ids(
    result: PortfolioResult,
    minimum_pct: float,
) -> set[str]:
    """Return active sets whose final lot does not contribute enough recent profit."""
    threshold = max(float(minimum_pct), 0.0) / 100.0
    if threshold <= 0 or not result.allocations:
        return set()
    contributions = {
        allocation.set_id: (
            max(float(allocation.recent_net_profit_001), 0.0) * int(allocation.units)
            if allocation.has_recent_performance
            else 0.0
        )
        for allocation in result.allocations
        if allocation.units > 0
    }
    total = sum(contributions.values())
    if total <= 0:
        return set()
    return {
        set_id
        for set_id, contribution in contributions.items()
        if contribution + 1e-9 < total * threshold
    }


def _optimize_without_recent_fillers(
    raw_sets: list[Any],
    minimum_pct: float,
    optimize: Callable[[list[Any]], PortfolioResult],
) -> tuple[PortfolioResult, set[str]]:
    """Remove strategies whose leave-one-out frontier contribution is immaterial."""
    pool = list(raw_sets)
    removed: set[str] = set()
    while True:
        result = optimize(pool)
        active_ids = {allocation.set_id for allocation in result.allocations if allocation.units > 0}
        threshold = max(float(minimum_pct), 0.0)
        if threshold <= 0 or len(active_ids) <= 1:
            if removed:
                result.warnings.insert(
                    0,
                    "Prueba antirrelleno marginal: "
                    f"{len(removed)} estrategia(s) eliminada(s) y portafolio reoptimizado.",
                )
            return result, removed

        base_net = float(result.total_net_profit)
        denominator = max(abs(base_net), 1.0)
        underrepresented_recent = _underrepresented_recent_allocation_ids(result, threshold)
        removal_candidates: list[
            tuple[float, int, float, float, str, list[Any]]
        ] = []
        for set_id in sorted(active_ids):
            trial_pool = [strategy for strategy in pool if strategy.set_id != set_id]
            if not trial_pool:
                continue
            trial = optimize(trial_pool)
            marginal_gain = base_net - float(trial.total_net_profit)
            marginal_pct = max(marginal_gain, 0.0) / denominator * 100.0
            protects_valley = (
                float(result.actual_valley_dd) + 1e-9
                < float(trial.actual_valley_dd)
            )
            if marginal_pct + 1e-9 >= threshold or protects_valley:
                continue
            removal_candidates.append((
                marginal_pct,
                0 if set_id in underrepresented_recent else 1,
                -float(trial.total_net_profit),
                float(trial.actual_valley_dd),
                set_id,
                trial_pool,
            ))

        if not removal_candidates:
            if removed:
                result.warnings.insert(
                    0,
                    "Prueba antirrelleno marginal: "
                    f"{len(removed)} estrategia(s) eliminada(s) y portafolio reoptimizado.",
                )
            return result, removed

        _pct, _recent_rank, _trial_net, _trial_dd, remove_id, pool = min(removal_candidates)
        removed.add(remove_id)


def _normal_proposals(
    raw_sets: list[Any],
    inputs: dict[str, Any],
    existing_curves: list[list[float]],
    *,
    full_sets: list[Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured = float(inputs.get("dd_reserve_pct") or 0)
    specs = (
        ("profit", "Maximo beneficio", base_type, configured),
        ("balanced", "Equilibrada", PortfolioType.BALANCED, max(configured, 15.0)),
        ("margin", "Maximo margen DD", PortfolioType.CONSERVATIVE, max(configured, 25.0)),
    )
    proposals: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, (key, label, objective_type, reserve) in enumerate(specs, 1):
        if progress:
            progress(f"Calculando propuesta {index}/3: {label}")
        proposal_inputs = dict(inputs)
        proposal_inputs.update({
            "optimization_profile": key,
            "optimization_profile_label": label,
            "portfolio_type": objective_type.value,
            "portfolio_type_label": TYPE_LABELS[objective_type.value],
            "dd_reserve_pct": reserve,
        })
        kwargs = _optimizer_kwargs(inputs, objective_type, existing_curves, reserve)
        try:
            def optimize(candidate_sets: list[Any]) -> PortfolioResult:
                if inputs.get("portfolio_scope") == "monthly" and inputs.get("strict_yearly_month_validation"):
                    return optimize_strict_monthly_portfolio(
                        monthly_sets=candidate_sets,
                        full_sets=full_sets or [],
                        target_month=int(inputs["target_month"]),
                        use_deep_refinement=bool(inputs.get("deep_optimization")),
                        **kwargs,
                    )
                return optimize_portfolio(
                    raw_sets=candidate_sets,
                    use_deep_refinement=bool(inputs.get("deep_optimization")),
                    **kwargs,
                )

            result, _removed = _optimize_without_recent_fillers(
                raw_sets,
                float(inputs.get("min_strategy_recent_contribution_pct") or 0.0),
                optimize,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        _seasonal_coverage(result, raw_sets)
        proposals.append({"key": key, "label": label, "reserve_pct": reserve, "inputs": proposal_inputs, "result": result})
    if not proposals:
        raise ValueError("Ninguna propuesta fue viable. " + " | ".join(errors))
    return proposals


def _locked_full_proposals(
    raw_sets: list[Any],
    inputs: dict[str, Any],
    existing_by_type: dict[PortfolioType, list[list[float]]],
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    base_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    configured = float(inputs.get("dd_reserve_pct") or 0)
    base_reserve = max(_reserve_pct(configured, portfolio_type) for _key, _label, portfolio_type in LOCKED_VARIANTS)
    base_inputs = dict(inputs)
    base_inputs["dd_reserve_pct"] = base_reserve
    minimum_recent_pct = float(inputs.get("min_strategy_recent_contribution_pct") or 0.0)
    if progress:
        progress(f"Seleccionando composicion base {TYPE_LABELS[base_type.value]}")
    base_kwargs = _optimizer_kwargs(
        base_inputs,
        base_type,
        existing_by_type.get(base_type, []),
        base_reserve,
    )
    base, removed_ids = _optimize_without_recent_fillers(
        raw_sets,
        minimum_recent_pct,
        lambda candidate_sets: optimize_portfolio(
            raw_sets=candidate_sets,
            use_deep_refinement=bool(base_inputs.get("deep_optimization")),
            **base_kwargs,
        ),
    )
    locked_ids = [allocation.set_id for allocation in base.allocations if allocation.units > 0]
    if not locked_ids:
        raise ValueError("La composicion base no produjo ningun set activo")
    raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
    missing = [set_id for set_id in locked_ids if set_id not in raw_by_id]
    if missing:
        raise ValueError("Faltan sets de la composicion base: " + ", ".join(Path(value).name for value in missing))
    locked_sets = [raw_by_id[set_id] for set_id in locked_ids]
    while True:
        locked_count = len(locked_sets)
        if inputs.get("max_total_units") is not None and int(inputs["max_total_units"]) < locked_count:
            raise ValueError("Max unidades es menor que la composicion comun")
        proposals: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, (key, label, portfolio_type) in enumerate(LOCKED_VARIANTS, 1):
            if progress:
                progress(f"Calculando variante {index}/3: {label}")
            reserve = _reserve_pct(configured, portfolio_type)
            proposal_inputs = dict(inputs)
            proposal_inputs.update({
                "optimization_profile": key,
                "optimization_profile_label": label,
                "portfolio_type": portfolio_type.value,
                "portfolio_type_label": TYPE_LABELS[portfolio_type.value],
                "composition_portfolio_type": base_type.value,
                "composition_portfolio_type_label": TYPE_LABELS[base_type.value],
                "dd_reserve_pct": reserve,
            })
            kwargs = _optimizer_kwargs(inputs, portfolio_type, existing_by_type.get(portfolio_type, []), reserve)
            kwargs.update({
                "top_k_per_symbol": max(int(inputs["top_k_per_symbol"]), locked_count),
                "max_total_candidates": None,
                "max_sets_per_group": locked_count,
                "group_unit_cap_bootstrap": max(locked_count, 1),
                "minimum_active_strategies": locked_count,
                "maximum_active_strategies": locked_count,
                "search_restarts": 0,
            })
            try:
                result = optimize_portfolio(
                    raw_sets=locked_sets,
                    use_deep_refinement=bool(inputs.get("deep_optimization")),
                    **kwargs,
                )
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                continue
            if {allocation.set_id for allocation in result.allocations if allocation.units > 0} != set(locked_ids):
                errors.append(f"{label}: no mantuvo todos los sets comunes")
                continue
            _seasonal_coverage(result, locked_sets)
            proposals.append({"key": key, "label": label, "reserve_pct": reserve, "inputs": proposal_inputs, "result": result})
        if len(proposals) != 3:
            raise ValueError("No se pudieron calcular las tres variantes bloqueadas. " + " | ".join(errors))

        for proposal in proposals:
            result = proposal["result"]
            result.warnings.insert(
                0,
                f"Composicion comun A/M/C: {locked_count} sets; reserva base {base_reserve:.1f}%",
            )
            if removed_ids:
                result.warnings.insert(
                    1,
                    "Prueba antirrelleno marginal: "
                    f"{len(removed_ids)} estrategia(s) eliminada(s) antes de fijar la composicion A/M/C.",
                )
        return proposals


def result_payload(result: PortfolioResult) -> dict[str, Any]:
    return {
        "total_net_profit": result.total_net_profit,
        "actual_valley_dd": result.actual_valley_dd,
        "actual_closed_valley_dd": result.actual_closed_valley_dd,
        "floating_dd_buffer": result.floating_dd_buffer,
        "actual_point_dd": result.actual_point_dd,
        "target_valley_dd": result.target_valley_dd,
        "target_point_dd": result.target_point_dd,
        "valley_usage_pct": result.valley_usage_pct,
        "point_usage_pct": result.point_usage_pct,
        "total_lot": result.total_lot,
        "total_units": result.total_units,
        "active_strategies": result.active_strategies,
        "stop_reason": result.stop_reason,
        "warnings": list(result.warnings),
        "group_summary": result.group_summary,
        "equity_curve_2020_2026": result.equity_curve_2020_2026,
        "unused_sets": [asdict(item) for item in result.unused_sets],
        "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
        "seasonal_coverage": result.seasonal_coverage,
        "seasonal_validation": result.seasonal_validation,
        "margin_summary": result.margin_summary,
        "daily_dd_summary": result.daily_dd_summary,
        "max_daily_dd": result.max_daily_dd,
        "target_daily_dd": result.target_daily_dd,
        "daily_dd_full_history": result.daily_dd_full_history,
        "enforce_point_dd": result.enforce_point_dd,
        "allocations": [asdict(allocation) for allocation in result.allocations],
        "decision_log": [asdict(decision) for decision in result.decision_log],
    }


def _strict_monthly_candidate_pool(
    raw_sets: list[Any],
    inputs: dict[str, Any],
) -> tuple[list[Any], list[str]]:
    """Match the desktop monthly fallback: keep sets whose best 5Y month is the target."""
    target_month = int(inputs["target_month"])
    selected: list[Any] = []
    for strategy in raw_sets:
        validation = validate_strict_monthly_portfolio(
            [strategy],
            {strategy.set_id: 1},
            target_month=target_month,
            target_valley_dd=1_000_000_000.0,
            target_point_dd=1_000_000_000.0,
            lookback_years=5,
        )
        if (
            int(validation.get("best_month") or 0) == target_month
            and float(validation.get("target_month_net") or 0.0) > 0
        ):
            selected.append(strategy)
    return selected, [
        "Validacion estricta: reintento con "
        f"{len(selected)}/{len(raw_sets)} candidato(s) cuyo mejor mes individual 5A es el objetivo."
    ]


def generate_proposals(
    source: PortfolioSource,
    inputs: dict[str, Any],
    progress: Callable[[str], None] | None = None,
    *,
    exclude_portfolio_id: int | None = None,
    lock_portfolio_type: PortfolioType | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    monthly = inputs["portfolio_scope"] == "monthly"
    if progress:
        progress("Leyendo candidatos Final Tick 6M accepted")
    rows = source.candidate_rows(include_quarantined=monthly)
    if not rows:
        raise ValueError("No hay candidatos con Final Tick 6M accepted")
    warnings: list[str] = []
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows, min_positive_months=3, window_months=6, parse=cached_report,
        )
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(inputs["allowed_asset_groups"])
    group_counts: dict[str, int] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        group = portfolio_group_key(str(row.get("target_symbol") or row.get("symbol") or ""), universe_files=[source.universe])
        group_counts[group] = group_counts.get(group, 0) + 1
        if group in allowed:
            filtered.append(row)
    rows = filtered
    if not rows:
        raise ValueError("No quedan candidatos tras aplicar los grupos permitidos")
    used: list[str] = []
    if monthly and inputs.get("exclude_monthly_used"):
        used = source.used_set_paths("monthly", exclude_portfolio_id=exclude_portfolio_id)
    elif not monthly and inputs.get("exclude_used_sets", True):
        used = source.used_set_paths(
            "full_history",
            exclude_portfolio_id=exclude_portfolio_id,
            portfolio_type=lock_portfolio_type,
        )
    availability = asdict(summarize_robust_rows(rows, used))
    if progress:
        progress(f"Cargando reportes de {len(rows)} candidatos")
    raw_sets, load_warnings = load_robust_sets_from_rows(rows, used, parse=cached_report, progress=progress)
    warnings.extend(load_warnings)
    raw_sets = [strategy for strategy in raw_sets if portfolio_group_key(strategy.symbol, universe_files=[source.universe]) in allowed]
    if not raw_sets:
        raise ValueError("No quedan sets cargados después de los filtros")
    if monthly:
        monthly_sets, slice_warnings = slice_strategy_sets_to_month(raw_sets, int(inputs["target_month"]))
        warnings.extend(slice_warnings)
        if not monthly_sets:
            raise ValueError("Ningun candidato tiene trades para el mes objetivo")
        existing = source.saved_curves(monthly=True, exclude_portfolio_id=exclude_portfolio_id) if inputs.get("corr_with_monthly_portfolios") else []
        strict_retry_warnings: list[str] = []

        def validate_strict(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not inputs.get("strict_yearly_month_validation"):
                return items
            valid: list[dict[str, Any]] = []
            rejected: list[str] = []
            full_by_id = {strategy.set_id: strategy for strategy in raw_sets}
            for proposal in items:
                result = proposal["result"]
                units = {allocation.set_id: allocation.units for allocation in result.allocations if allocation.units > 0}
                validation = validate_strict_monthly_portfolio(
                    [full_by_id[set_id] for set_id in units if set_id in full_by_id], units,
                    target_month=int(inputs["target_month"]),
                    target_valley_dd=result.target_valley_dd,
                    target_point_dd=result.target_point_dd,
                    enforce_point_dd=False,
                    lookback_years=5,
                )
                result.seasonal_validation = validation
                if validation.get("passed"):
                    valid.append(proposal)
                else:
                    rejected.append(f"{proposal['label']}: {'; '.join(str(x) for x in (validation.get('reasons') or [])[:3])}")
            if not valid:
                raise ValueError("Ninguna propuesta pasó la validación mensual estricta. " + " | ".join(rejected))
            return valid

        try:
            proposals = _normal_proposals(monthly_sets, inputs, existing, full_sets=raw_sets, progress=progress)
            proposals = validate_strict(proposals)
        except ValueError:
            if not inputs.get("strict_yearly_month_validation"):
                raise
            if progress:
                progress("Reintentando con el pool mensual estricto")
            strict_raw_sets, strict_retry_warnings = _strict_monthly_candidate_pool(raw_sets, inputs)
            if not strict_raw_sets:
                raise
            strict_monthly_sets, strict_slice_warnings = slice_strategy_sets_to_month(
                strict_raw_sets, int(inputs["target_month"])
            )
            warnings.extend(strict_retry_warnings)
            warnings.extend(strict_slice_warnings)
            if not strict_monthly_sets:
                raise
            proposals = _normal_proposals(
                strict_monthly_sets,
                inputs,
                existing,
                full_sets=raw_sets,
                progress=progress,
            )
            proposals = validate_strict(proposals)
    else:
        existing_by_type = {
            kind: source.saved_curves(monthly=False, portfolio_type=kind, exclude_portfolio_id=exclude_portfolio_id)
            for kind in PORTFOLIO_TYPES.values()
        }
        proposals = _locked_full_proposals(raw_sets, inputs, existing_by_type, progress)
    for proposal in proposals:
        proposal["result"].warnings[:0] = warnings
    availability.update({"loaded_sets": len(raw_sets), "group_counts": group_counts, "warnings": warnings})
    return availability, proposals


def generate_completion_proposal(
    source: PortfolioSource,
    portfolio_id: int,
    scope: str,
    inputs: dict[str, Any],
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    detail = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
    if scope == "full_history" and (
        str(detail.get("portfolio_type") or "").lower() == "bundle" or detail.get("metrics", {}).get("portfolio_bundle")
    ):
        raise ValueError("El portafolio A/M/C debe reoptimizarse completo; no admite completar una sola variante")
    members = list(detail.get("members") or [])
    target = max(int(detail.get("target_strategies") or 0), int(detail.get("active_strategies") or 0))
    if target <= len(members):
        raise ValueError("El portafolio ya tiene todas sus estrategias")
    monthly = scope == "monthly"
    if progress:
        progress(f"Reconstruyendo {len(members)} estrategias que deben conservarse")
    required_rows = [{
        "candidate_id": item.get("candidate_id"), "set_path": item.get("set_path") or item.get("set_id"),
        "symbol": item.get("symbol"), "target_symbol": item.get("symbol"), "period": item.get("timeframe"),
        "family": "", "is_report_path": item.get("is_report_path"), "oos_report_path": item.get("oos_report_path"),
        "final_tick_report_path": item.get("final_tick_report_path"),
        "full_history_report_path": item.get("full_history_report_path"),
        "max_balance_dd_001": item.get("max_balance_dd_001"),
        "max_equity_dd_001": item.get("max_equity_dd_001"),
        "floating_dd_source": item.get("floating_dd_source"),
        "recent_net_profit_001": item.get("recent_net_profit_001"),
        "recent_equity_dd_001": item.get("recent_equity_dd_001"),
        "has_recent_performance": item.get("has_recent_performance"),
    } for item in members]
    required_sets, required_warnings = load_robust_sets_from_rows(required_rows, [], parse=cached_report)
    if len(required_sets) != len(required_rows):
        raise ValueError("No se pudieron reconstruir todas las estrategias que deben conservarse")
    rows = source.candidate_rows(include_quarantined=monthly)
    warnings = list(required_warnings)
    if inputs.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(rows, min_positive_months=3, window_months=6, parse=cached_report)
        warnings.extend(found)
    if inputs.get("grid_off"):
        rows, found = filter_rows_grid_off(rows)
        warnings.extend(found)
    allowed = set(inputs.get("allowed_asset_groups") or ASSET_GROUPS)
    rows = [row for row in rows if portfolio_group_key(
        str(row.get("target_symbol") or row.get("symbol") or ""), universe_files=[source.universe]
    ) in allowed]
    used = [] if monthly else (
        source.used_set_paths(
            scope,
            exclude_portfolio_id=portfolio_id,
            portfolio_type=PORTFOLIO_TYPES[str(inputs["portfolio_type"])],
        )
        if inputs.get("exclude_used_sets", True) else []
    )
    candidate_sets, load_warnings = load_robust_sets_from_rows(rows, used, parse=cached_report, progress=progress)
    warnings.extend(load_warnings)
    full_sets = list(required_sets) + list(candidate_sets)
    if monthly:
        required_sets, found = slice_strategy_sets_to_month(required_sets, int(inputs["target_month"]))
        warnings.extend(found)
        candidate_sets, found = slice_strategy_sets_to_month(candidate_sets, int(inputs["target_month"]))
        warnings.extend(found)
    by_id = {strategy.set_id: strategy for strategy in candidate_sets}
    by_id.update({strategy.set_id: strategy for strategy in required_sets})
    raw_sets = list(by_id.values())
    required_ids = [strategy.set_id for strategy in required_sets]
    saved_units = {str(item.get("set_path") or item.get("set_id") or ""): int(item.get("units") or 0) for item in members}
    initial = {strategy.set_id: saved_units.get(strategy.set_id, 0) for strategy in required_sets}
    portfolio_type = PORTFOLIO_TYPES[str(inputs["portfolio_type"])]
    reserve = float(inputs.get("dd_reserve_pct") or 0)
    existing = source.saved_curves(
        monthly=monthly, portfolio_type=None if monthly else portfolio_type, exclude_portfolio_id=portfolio_id
    ) if (not monthly or inputs.get("corr_with_monthly_portfolios")) else []
    kwargs = _optimizer_kwargs(inputs, portfolio_type, existing, reserve)
    kwargs.update({
        "required_set_ids": required_ids,
        "minimum_active_strategies": target,
        "maximum_active_strategies": target,
        "required_initial_allocations": initial,
        "preserve_required_allocations": True,
    })
    if progress:
        progress(f"Buscando sustituta para completar {len(members)}/{target}")
    result = optimize_portfolio(raw_sets=raw_sets, use_deep_refinement=bool(inputs.get("deep_optimization")), **kwargs)
    _seasonal_coverage(result, raw_sets)
    if inputs.get("strict_yearly_month_validation"):
        full_by_id = {strategy.set_id: strategy for strategy in full_sets}
        units = {item.set_id: item.units for item in result.allocations if item.units > 0}
        validation = validate_strict_monthly_portfolio(
            [full_by_id[set_id] for set_id in units if set_id in full_by_id], units,
            target_month=int(inputs["target_month"]), target_valley_dd=result.target_valley_dd,
            target_point_dd=result.target_point_dd, enforce_point_dd=False, lookback_years=5,
        )
        result.seasonal_validation = validation
        if not validation.get("passed"):
            raise ValueError("La sustitución no pasó la validación mensual estricta: " + "; ".join(str(x) for x in (validation.get("reasons") or [])[:3]))
    result.warnings[:0] = warnings
    if result.active_strategies < target:
        raise ValueError(f"No existe una sustituta compatible: quedaron {result.active_strategies}/{target} estrategias")
    proposal_inputs = dict(inputs)
    proposal_inputs.update({"optimization_profile": "complete", "optimization_profile_label": "Completar portafolio"})
    availability = asdict(summarize_robust_rows(rows, used))
    availability.update({"loaded_sets": len(raw_sets), "warnings": warnings})
    return availability, [{"key": "complete", "label": "Completar portafolio", "reserve_pct": reserve,
                           "inputs": proposal_inputs, "result": result}]


def _result_metrics(inputs: dict[str, Any], result: PortfolioResult) -> dict[str, Any]:
    return {
        "inputs": inputs,
        "warnings": result.warnings,
        "group_summary": result.group_summary,
        "equity_curve_2020_2026": result.equity_curve_2020_2026,
        "unused_sets": [asdict(item) for item in result.unused_sets],
        "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
        "seasonal_coverage": result.seasonal_coverage,
        "seasonal_validation": result.seasonal_validation,
        "margin_summary": result.margin_summary,
        "daily_dd_summary": result.daily_dd_summary,
        "max_daily_dd": result.max_daily_dd,
        "target_daily_dd": result.target_daily_dd,
        "daily_dd_full_history": result.daily_dd_full_history,
        "enforce_point_dd": result.enforce_point_dd,
        "actual_closed_valley_dd": result.actual_closed_valley_dd,
        "floating_dd_buffer": result.floating_dd_buffer,
    }


def serialize_portfolio_proposals(
    proposals: list[dict[str, Any]], request_id: str
) -> list[dict[str, Any]]:
    """Convert in-memory optimizer results into an authenticated node payload."""
    if not request_id:
        raise ValueError("Falta el identificador de la solicitud de guardado")
    payload: list[dict[str, Any]] = []
    for proposal in proposals:
        result = proposal.get("result")
        inputs = proposal.get("inputs")
        if not isinstance(result, PortfolioResult) or not isinstance(inputs, dict):
            raise ValueError("La propuesta calculada no tiene un formato guardable")
        payload.append({
            "key": str(proposal.get("key") or ""),
            "label": str(proposal.get("label") or ""),
            "reserve_pct": float(proposal.get("reserve_pct") or 0),
            "inputs": {**inputs, "_manager_save_request_id": request_id},
            "result": asdict(result),
        })
    return payload


LEGACY_ALLOCATION_RISK_FIELDS = {
    "max_balance_dd_001",
    "max_equity_dd_001",
    "floating_dd_source",
    "standalone_floating_dd",
    "recent_net_profit_001",
    "recent_equity_dd_001",
    "has_recent_performance",
    "final_tick_report_path",
    "full_history_report_path",
}
LEGACY_RESULT_RISK_FIELDS = {"actual_closed_valley_dd", "floating_dd_buffer"}


def legacy_compatible_portfolio_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Downgrade only the wire shape for nodes from before equity-risk fields.

    Core safety values remain in ``actual_valley_dd`` and
    ``standalone_valley_dd``; only the new audit breakdown is omitted.
    """
    compatible = dict(payload)
    compatible_proposals: list[dict[str, Any]] = []
    for raw_proposal in payload.get("proposals") or []:
        if not isinstance(raw_proposal, dict):
            continue
        proposal = dict(raw_proposal)
        raw_result = proposal.get("result")
        if isinstance(raw_result, dict):
            result = {
                key: value for key, value in raw_result.items()
                if key not in LEGACY_RESULT_RISK_FIELDS
            }
            result["allocations"] = [
                {
                    key: value for key, value in allocation.items()
                    if key not in LEGACY_ALLOCATION_RISK_FIELDS
                }
                for allocation in raw_result.get("allocations") or []
                if isinstance(allocation, dict)
            ]
            proposal["result"] = result
        compatible_proposals.append(proposal)
    compatible["proposals"] = compatible_proposals
    return compatible


def _supported_dataclass_values(dataclass_type: type, raw: dict[str, Any]) -> dict[str, Any]:
    supported = {item.name for item in fields(dataclass_type)}
    return {key: value for key, value in raw.items() if key in supported}


def deserialize_portfolio_proposals(
    payload: object, scope: str, broker: str
) -> list[dict[str, Any]]:
    """Validate and rebuild optimizer dataclasses inside the node process."""
    if not isinstance(payload, list) or not payload:
        raise ValueError("No se recibieron propuestas para guardar")
    proposals: list[dict[str, Any]] = []
    for raw_proposal in payload:
        if not isinstance(raw_proposal, dict):
            raise ValueError("Propuesta remota inválida")
        raw_result = raw_proposal.get("result")
        raw_inputs = raw_proposal.get("inputs")
        if not isinstance(raw_result, dict) or not isinstance(raw_inputs, dict):
            raise ValueError("La propuesta remota no contiene inputs y resultado")
        result_values = dict(raw_result)
        result_values["allocations"] = [
            StrategyAllocation(**_supported_dataclass_values(StrategyAllocation, item))
            for item in result_values.get("allocations") or []
            if isinstance(item, dict)
        ]
        result_values["decision_log"] = [
            OptimizationDecision(**_supported_dataclass_values(OptimizationDecision, item))
            for item in result_values.get("decision_log") or []
            if isinstance(item, dict)
        ]
        result_values["unused_sets"] = [
            UnusedSetInfo(**_supported_dataclass_values(UnusedSetInfo, item))
            for item in result_values.get("unused_sets") or []
            if isinstance(item, dict)
        ]
        stress = result_values.get("stress_bootstrap")
        result_values["stress_bootstrap"] = (
            BootstrapDrawdownAnalysis(**_supported_dataclass_values(BootstrapDrawdownAnalysis, stress))
            if isinstance(stress, dict) else None
        )
        try:
            result = PortfolioResult(**_supported_dataclass_values(PortfolioResult, result_values))
        except TypeError as exc:
            raise ValueError(f"Resultado de propuesta incompatible: {exc}") from exc
        proposals.append({
            "key": str(raw_proposal.get("key") or ""),
            "label": str(raw_proposal.get("label") or ""),
            "reserve_pct": float(raw_proposal.get("reserve_pct") or 0),
            "inputs": normalize_settings(scope, raw_inputs, broker),
            "result": result,
        })
    return proposals


def _saved_request_portfolio_id(source: PortfolioSource, request_id: str, scope: str) -> int | None:
    portfolio_scope = "monthly" if scope == "monthly" else "full_history"
    with source.connect() as conn:
        if not _table_exists(conn, "portfolios"):
            return None
        rows = conn.execute(
            "select id,metrics_json from portfolios "
            "where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
            (portfolio_scope,),
        ).fetchall()
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except json.JSONDecodeError:
            continue
        inputs = metrics.get("inputs") if isinstance(metrics, dict) else None
        if isinstance(inputs, dict) and inputs.get("_manager_save_request_id") == request_id:
            return int(row["id"])
    return None


def save_portfolio_payload(source: PortfolioSource, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a manager proposal locally on its owning node, with retry deduplication."""
    scope = "monthly" if str(payload.get("scope")) == "monthly" else "full_history"
    request_id = str(payload.get("request_id") or "").strip()
    selected_key = str(payload.get("selected_key") or "").strip()
    operation = str(payload.get("operation") or "generate")
    if not request_id or not selected_key:
        raise ValueError("Solicitud de guardado incompleta")
    if operation not in {"generate", "reoptimize", "complete"}:
        raise ValueError("Operación de guardado desconocida")
    existing_id = _saved_request_portfolio_id(source, request_id, scope)
    if existing_id is not None:
        return {"portfolio_id": existing_id, "request_id": request_id, "deduplicated": True}
    proposals = deserialize_portfolio_proposals(payload.get("proposals"), scope, source.broker)
    if operation in {"reoptimize", "complete"}:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
        saved_id = replace_saved_proposal(
            source, proposals, selected_key, scope, portfolio_id,
            "Antes de reoptimizar" if operation == "reoptimize" else "Antes de completar portafolio",
        )
    else:
        saved_id = save_proposal(source, proposals, selected_key, scope)
    detail = source.saved_portfolio_detail(saved_id, scope)["portfolio"]
    if not detail.get("members"):
        raise ValueError(f"El portafolio #{saved_id} se escribió sin estrategias")
    source.notify(
        f"Portfolio Builder guardado: #{saved_id}, net {float(detail.get('total_net_profit') or 0):,.2f}, "
        f"lote {float(detail.get('total_lot') or 0):.2f}, {int(detail.get('active_strategies') or 0)} estrategias"
    )
    return {"portfolio_id": saved_id, "request_id": request_id, "deduplicated": False}


def _insert_allocation(
    conn: sqlite3.Connection,
    portfolio_id: int,
    allocation: Any,
    variant_key: str,
    variant_label: str,
) -> None:
    conn.execute(
        """
        insert into portfolio_allocations (
            portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
            net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe,
            lot_size_step,margin_required,margin_pct,margin_leverage,margin_contract_size,
            margin_price,is_report_path,oos_report_path,final_tick_report_path,full_history_report_path,
            max_balance_dd_001,max_equity_dd_001,
            floating_dd_source,standalone_floating_dd,recent_net_profit_001,recent_equity_dd_001,
            has_recent_performance
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            portfolio_id, variant_key, variant_label, allocation.set_id, allocation.candidate_id,
            allocation.symbol, allocation.units, allocation.lot, allocation.net_profit_contribution,
            allocation.standalone_valley_dd, allocation.standalone_point_dd,
            allocation.set_path or allocation.set_id, allocation.timeframe or "", allocation.lot_size_step,
            allocation.margin_required, allocation.margin_pct, allocation.margin_leverage,
            allocation.margin_contract_size, allocation.margin_price,
            allocation.is_report_path, allocation.oos_report_path,
            allocation.final_tick_report_path,
            allocation.full_history_report_path,
            allocation.max_balance_dd_001, allocation.max_equity_dd_001,
            allocation.floating_dd_source, allocation.standalone_floating_dd,
            allocation.recent_net_profit_001, allocation.recent_equity_dd_001,
            int(allocation.has_recent_performance),
        ),
    )
    candidate_text = str(allocation.candidate_id)
    candidate_suffix = candidate_text.rsplit(":", 1)[-1]
    candidate_value = int(candidate_suffix) if candidate_suffix.isdigit() else None
    conn.execute(
        """
        insert into portfolio_members (
            portfolio_id,variant_key,variant_label,candidate_id,set_path,symbol,period,
            lot_multiplier,lot,lot_size_step,standalone_dd,quality_score,combined_net_profit,
            is_report_path,oos_report_path
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            portfolio_id, variant_key, variant_label, candidate_value,
            allocation.set_path or allocation.set_id, allocation.symbol, allocation.timeframe or "",
            allocation.units, allocation.lot, allocation.lot_size_step, allocation.standalone_valley_dd,
            0.0, allocation.net_profit_contribution, allocation.is_report_path, allocation.oos_report_path,
        ),
    )


def _insert_decisions(
    conn: sqlite3.Connection,
    portfolio_id: int,
    result: PortfolioResult,
    prefix: str = "",
) -> None:
    for decision in result.decision_log:
        reason = f"{prefix}: {decision.reason}" if prefix else decision.reason
        conn.execute(
            """
            insert into portfolio_decision_log (
                portfolio_id,step,action,set_id,from_set_id,to_set_id,gain,valley_cost,point_cost,
                score,portfolio_net_profit_after,portfolio_valley_dd_after,portfolio_point_dd_after,reason
            ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                portfolio_id, decision.step, decision.action, decision.set_id,
                decision.from_set_id, decision.to_set_id, decision.gain, decision.valley_cost,
                decision.point_cost, decision.score, decision.portfolio_net_profit_after,
                decision.portfolio_valley_dd_after, decision.portfolio_point_dd_after, reason,
            ),
        )


def save_proposal(
    source: PortfolioSource,
    proposals: list[dict[str, Any]],
    selected_key: str,
    scope: str,
) -> int:
    selected = next((proposal for proposal in proposals if str(proposal["key"]) == selected_key), None)
    if selected is None:
        raise ValueError("La propuesta seleccionada ya no está disponible")
    selected_result: PortfolioResult = selected["result"]
    selected_inputs: dict[str, Any] = selected["inputs"]
    if not selected_result.allocations:
        raise ValueError("La propuesta no tiene asignaciones")
    if scope == "monthly" and selected_inputs.get("strict_yearly_month_validation") and not selected_result.seasonal_validation.get("passed"):
        raise ValueError("La propuesta mensual no pasó la validación estricta")
    created_at = datetime.now().isoformat(timespec="seconds")
    target_month = int(selected_inputs.get("target_month") or 0) or None
    bundle = scope == "full_history"
    if bundle:
        common = [allocation.set_id for allocation in selected_result.allocations if allocation.units > 0]
        common_set = set(common)
        if any({allocation.set_id for allocation in proposal["result"].allocations if allocation.units > 0} != common_set for proposal in proposals):
            raise ValueError("Las variantes A/M/C no comparten la misma composición")
        variants: dict[str, Any] = {}
        for proposal in proposals:
            result: PortfolioResult = proposal["result"]
            payload = _result_metrics(proposal["inputs"], result)
            payload.update({
                "label": proposal["label"],
                "summary": result_payload(result),
                "allocations": [asdict(allocation) for allocation in result.allocations],
            })
            variants[str(proposal["key"])] = payload
        metrics = _result_metrics(selected_inputs, selected_result)
        metrics.update({
            "portfolio_bundle": True,
            "bundle_display": "A/M/C",
            "selected_variant": selected_key,
            "variant_order": [str(proposal["key"]) for proposal in proposals],
            "variants": variants,
            "common_set_ids": common,
        })
        row_type = "bundle"
        name = f"A/M/C | Base {TYPE_LABELS.get(str(selected_inputs.get('composition_portfolio_type')), 'Moderado')} | {len(common)} sets | {datetime.now():%d.%m.%Y %H:%M}"
    else:
        metrics = _result_metrics(selected_inputs, selected_result)
        row_type = str(selected_inputs["portfolio_type"])
        name = f"{TYPE_LABELS.get(row_type, row_type)} | Mes {target_month:02d} | {selected_result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
    active_symbols = len({portfolio_symbol_key(allocation.symbol) for allocation in selected_result.allocations if allocation.units > 0})
    with source.connect(write=True) as conn:
        try:
            cur = conn.execute(
                """
                insert into portfolios (
                    created_at,name,type,portfolio_type,num_symbols,account_capital,capital,
                    target_valley_dd_pct,target_point_dd_pct,target_valley_dd,target_point_dd,
                    actual_valley_dd,actual_point_dd,valley_usage_pct,point_usage_pct,total_net_profit,
                    actual_closed_valley_dd,floating_dd_buffer,
                    total_lot,total_units,active_strategies,target_strategies,stop_reason,binding_constraint,
                    portfolio_scope,target_month,metrics_json
                ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    created_at, name, row_type, row_type, active_symbols, float(selected_inputs["capital"]),
                    float(selected_inputs["capital"]), float(selected_inputs["valley_dd_pct"]),
                    float(selected_inputs["point_dd_pct"]), selected_result.target_valley_dd,
                    selected_result.target_point_dd, selected_result.actual_valley_dd,
                    selected_result.actual_point_dd, selected_result.valley_usage_pct,
                    selected_result.point_usage_pct, selected_result.total_net_profit,
                    selected_result.actual_closed_valley_dd, selected_result.floating_dd_buffer,
                    selected_result.total_lot, selected_result.total_units, selected_result.active_strategies,
                    selected_result.active_strategies, selected_result.stop_reason,
                    "valley", scope, target_month, json.dumps(metrics, ensure_ascii=True),
                ),
            )
            portfolio_id = int(cur.lastrowid)
            rows_to_save = proposals if bundle else [selected]
            for proposal in rows_to_save:
                result = proposal["result"]
                key = str(proposal["key"]) if bundle else ""
                label = str(proposal["label"]) if bundle else ""
                for allocation in result.allocations:
                    _insert_allocation(conn, portfolio_id, allocation, key, label)
                _insert_decisions(conn, portfolio_id, result, label)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return portfolio_id


def _proposal_metrics(
    proposals: list[dict[str, Any]], selected: dict[str, Any], selected_key: str, scope: str
) -> tuple[dict[str, Any], str, str]:
    result: PortfolioResult = selected["result"]
    inputs: dict[str, Any] = selected["inputs"]
    if scope == "full_history":
        common = [allocation.set_id for allocation in result.allocations if allocation.units > 0]
        common_set = set(common)
        if any({allocation.set_id for allocation in item["result"].allocations if allocation.units > 0} != common_set for item in proposals):
            raise ValueError("Las variantes A/M/C no comparten la misma composición")
        variants: dict[str, Any] = {}
        for item in proposals:
            variant_result: PortfolioResult = item["result"]
            payload = _result_metrics(item["inputs"], variant_result)
            payload.update({"label": item["label"], "summary": result_payload(variant_result), "allocations": [asdict(value) for value in variant_result.allocations]})
            variants[str(item["key"])] = payload
        metrics = _result_metrics(inputs, result)
        metrics.update({"portfolio_bundle": True, "bundle_display": "A/M/C", "selected_variant": selected_key,
                        "variant_order": [str(item["key"]) for item in proposals], "variants": variants, "common_set_ids": common})
        row_type = "bundle"
        name = f"A/M/C | Base {TYPE_LABELS.get(str(inputs.get('composition_portfolio_type')), 'Moderado')} | {len(common)} sets | {datetime.now():%d.%m.%Y %H:%M}"
    else:
        metrics = _result_metrics(inputs, result)
        row_type = str(inputs["portfolio_type"])
        name = f"{TYPE_LABELS.get(row_type, row_type)} | Mes {int(inputs.get('target_month') or 0):02d} | {result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
    return metrics, row_type, name


def replace_saved_proposal(
    source: PortfolioSource,
    proposals: list[dict[str, Any]],
    selected_key: str,
    scope: str,
    portfolio_id: int,
    reason: str,
) -> int:
    selected = next((proposal for proposal in proposals if str(proposal["key"]) == selected_key), None)
    if selected is None:
        raise ValueError("La propuesta seleccionada ya no está disponible")
    result: PortfolioResult = selected["result"]
    inputs: dict[str, Any] = selected["inputs"]
    if not result.allocations:
        raise ValueError("La propuesta no tiene asignaciones")
    metrics, row_type, name = _proposal_metrics(proposals, selected, selected_key, scope)
    active_symbols = len({portfolio_symbol_key(item.symbol) for item in result.allocations if item.units > 0})
    target_month = int(inputs.get("target_month") or 0) or None
    with source.connect(write=True) as conn:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe")
        target_strategies = max(int(portfolio["target_strategies"] or 0), result.active_strategies)
        try:
            source._save_version(conn, portfolio_id, reason)
            conn.execute(
                """update portfolios set name=?,type=?,portfolio_type=?,num_symbols=?,account_capital=?,capital=?,
                   target_valley_dd_pct=?,target_point_dd_pct=?,target_valley_dd=?,target_point_dd=?,actual_valley_dd=?,
                   actual_point_dd=?,valley_usage_pct=?,point_usage_pct=?,total_net_profit=?,total_lot=?,total_units=?,
                   actual_closed_valley_dd=?,floating_dd_buffer=?,
                   active_strategies=?,target_strategies=?,stop_reason=?,binding_constraint=?,portfolio_scope=?,target_month=?,metrics_json=?
                   where id=?""",
                (name, row_type, row_type, active_symbols, float(inputs["capital"]), float(inputs["capital"]),
                 float(inputs["valley_dd_pct"]), float(inputs["point_dd_pct"]), result.target_valley_dd,
                 result.target_point_dd, result.actual_valley_dd, result.actual_point_dd, result.valley_usage_pct,
                 result.point_usage_pct, result.total_net_profit, result.total_lot, result.total_units,
                 result.actual_closed_valley_dd, result.floating_dd_buffer,
                 result.active_strategies, target_strategies, result.stop_reason, "valley", scope, target_month,
                 json.dumps(metrics, ensure_ascii=True), portfolio_id),
            )
            for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members"):
                conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
            rows_to_save = proposals if scope == "full_history" else [selected]
            for proposal in rows_to_save:
                variant_result: PortfolioResult = proposal["result"]
                variant_key = str(proposal["key"]) if scope == "full_history" else ""
                variant_label = str(proposal["label"]) if scope == "full_history" else ""
                for allocation in variant_result.allocations:
                    _insert_allocation(conn, portfolio_id, allocation, variant_key, variant_label)
                _insert_decisions(conn, portfolio_id, variant_result, variant_label)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return portfolio_id


def proposal_diff(previous_members: list[dict[str, Any]], result: PortfolioResult) -> list[dict[str, Any]]:
    before = {str(item.get("set_path") or item.get("set_id") or ""): item for item in previous_members}
    after = {str(item.set_path or item.set_id): item for item in result.allocations}
    rows: list[dict[str, Any]] = []
    for set_path in sorted(set(before) | set(after), key=lambda value: Path(value).name.casefold()):
        old, new = before.get(set_path), after.get(set_path)
        old_units = int(old.get("units") or 0) if old else 0
        new_units = int(new.units) if new else 0
        state = "NUEVA" if old is None else "RETIRADA" if new is None else "AJUSTADA" if old_units != new_units else "SIN CAMBIO"
        rows.append({
            "set_path": set_path, "set_name": Path(set_path).name,
            "symbol": str(new.symbol if new else old.get("symbol") or ""),
            "old_units": old_units, "new_units": new_units, "delta_units": new_units - old_units,
            "old_lot": float(old.get("lot") or 0) if old else 0.0,
            "new_lot": float(new.lot) if new else 0.0,
            "state": state,
        })
    return rows


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
            if (self.jobs.get(key) or {}).get("status") == "running":
                raise ValueError("Ya hay un cálculo de portafolio en curso")
            if any(task.get("status") in {"pending", "running"} for task in self.tasks.get(key, [])):
                raise ValueError("Hay una tarea de portafolio pendiente o en ejecución")
            job = {
                "id": time.strftime("%Y%m%d_%H%M%S"), "status": "running",
                "started_at": utc_now(), "finished_at": None, "progress": "Preparando cálculo",
                "error": None, "availability": None, "operation": operation,
                "portfolio_id": portfolio_id, "previous_members": previous_members,
            }
            self.jobs[key] = job
            self.proposals.pop(key, None)
        threading.Thread(target=self._worker, args=(node_id, scope, settings, operation, portfolio_id), daemon=True).start()
        return dict(job)

    def start_saved_operation(
        self, node_id: str, scope: str, portfolio_id: int, operation: str, changes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if operation not in {"reoptimize", "complete"}:
            raise ValueError("Operación guardada desconocida")
        source = PortfolioSource(self._node(node_id))
        detail = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        settings = source.saved_inputs(portfolio_id, scope)
        if changes:
            settings.update(changes)
            settings = normalize_settings(scope, settings, source.broker)
        return self._start_job(node_id, scope, settings, operation, portfolio_id, list(detail.get("members") or []))

    def _worker(
        self, node_id: str, scope: str, settings: dict[str, Any], operation: str = "generate", portfolio_id: int | None = None
    ) -> None:
        key = self._key(node_id, scope)

        def progress(message: str) -> None:
            with self.lock:
                if key in self.jobs:
                    self.jobs[key]["progress"] = str(message)

        try:
            source = PortfolioSource(self._node(node_id))
            log_dir = source.project / "portfolio_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"manager_{scope}_{operation}_{time.strftime('%Y%m%d_%H%M%S')}.log"
            with self.lock:
                self.jobs[key]["log_path"] = str(log_path)

            def logged_progress(message: str) -> None:
                progress(message)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{datetime.now().isoformat(timespec='seconds')} | {message}\n")

            if operation == "complete":
                if portfolio_id is None:
                    raise ValueError("Falta el portafolio que se quiere completar")
                availability, proposals = generate_completion_proposal(source, portfolio_id, scope, settings, logged_progress)
            else:
                lock_portfolio_type: PortfolioType | None = None
                if operation == "reoptimize" and portfolio_id is not None:
                    saved = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
                    is_bundle = (
                        str(saved.get("portfolio_type") or "").lower() == "bundle"
                        or bool((saved.get("metrics") or {}).get("portfolio_bundle"))
                    )
                    if not is_bundle:
                        lock_portfolio_type = PORTFOLIO_TYPES[str(settings["portfolio_type"])]
                availability, proposals = generate_proposals(
                    source, settings, logged_progress,
                    exclude_portfolio_id=portfolio_id if operation == "reoptimize" else None,
                    lock_portfolio_type=lock_portfolio_type,
                )
            with self.lock:
                self.proposals[key] = proposals
                self.jobs[key].update({
                    "status": "completed", "finished_at": utc_now(), "progress": "Propuestas listas",
                    "availability": availability, "proposal_count": len(proposals),
                })
            source.notify(
                f"Portfolio Builder {operation} listo en {source.broker}/{source.account}: "
                f"{len(proposals)} propuesta(s)" + (f" para portafolio #{portfolio_id}" if portfolio_id else "")
            )
        except Exception as exc:
            with self.lock:
                self.jobs[key].update({"status": "failed", "finished_at": utc_now(), "error": str(exc), "progress": "Error"})
            try:
                PortfolioSource(self._node(node_id)).notify(
                    f"Portfolio Builder {operation} fallido" + (f" para #{portfolio_id}" if portfolio_id else "") + f": {exc}"
                )
            except Exception:
                pass

    def state(self, node_id: str, scope: str) -> dict[str, Any]:
        key = self._key(node_id, scope)
        with self.lock:
            job = dict(self.jobs.get(key) or {"status": "idle"})
            proposals = list(self.proposals.get(key) or [])
            tasks = [dict(task) for task in self.tasks.get(key, [])]
            active_task = next(
                (task for task in tasks if task.get("status") in {"pending", "running"}),
                tasks[-1] if tasks else {"status": "idle"},
            )
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
                "result": result_data, "diff": diff,
            })
        return {
            "settings": settings,
            "job": job,
            "task": active_task,
            "tasks": tasks[-10:],
            "inventory": PortfolioSource(self._node(node_id)).inventory(scope, settings),
            "proposals": proposal_payloads,
        }

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
        target_id = safe_int(job.get("portfolio_id"), 0)
        if operation in {"reoptimize", "complete"} and target_id <= 0:
            raise ValueError("Falta el portafolio que se quiere actualizar")
        request_id = str(job.get("save_request_id") or "")
        if not request_id or str(job.get("save_selected_key") or "") != selected_key:
            request_id = str(uuid.uuid4())
        with self.lock:
            if key not in self.jobs:
                self.jobs[key] = job
            self.jobs[key]["save_request_id"] = request_id
            self.jobs[key]["save_selected_key"] = selected_key
        return {
            "scope": scope,
            "selected_key": selected_key,
            "operation": operation,
            "portfolio_id": target_id or None,
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

    def saved(self, node_id: str, scope: str, portfolio_id: int | None = None) -> dict[str, Any]:
        source = PortfolioSource(self._node(node_id))
        return source.saved_portfolio_detail(portfolio_id, scope) if portfolio_id is not None else source.saved_portfolios(scope)

    def exclude(self, node_id: str, scope: str, payload: dict[str, Any]) -> int:
        source = PortfolioSource(self._node(node_id))
        quarantine_id = source.remove_member_to_quarantine(payload, scope) if safe_int(payload.get("portfolio_id"), 0) else source.exclude_strategy(payload)
        with self.lock:
            self.proposals.pop(self._key(node_id, "full_history"), None)
        return quarantine_id

    def release(self, node_id: str, quarantine_id: str | int) -> None:
        PortfolioSource(self._node(node_id)).release_strategy(quarantine_id)
        with self.lock:
            self.proposals.pop(self._key(node_id, "full_history"), None)

    def undo(self, node_id: str, scope: str, portfolio_id: int) -> int:
        return PortfolioSource(self._node(node_id)).undo_latest(portfolio_id, scope)

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
                PortfolioSource(self._node(node_id)).delete_portfolio(int(task["portfolio_id"]), scope)
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

    def export(self, node_id: str, scope: str, portfolio_id: int, destination: str | None) -> dict[str, Any]:
        return PortfolioSource(self._node(node_id)).export_portfolio(portfolio_id, scope, destination)

    def open_report(self, node_id: str, scope: str, portfolio_id: int, set_path: str) -> str:
        return PortfolioSource(self._node(node_id)).open_member_report(portfolio_id, scope, set_path)

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
