from __future__ import annotations

import argparse
import configparser
import contextlib
import hmac
import json
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
from typing import Any

from .common import json_bytes, load_json, safe_int, save_json, utc_now
from .portfolio_service import PortfolioSource, save_portfolio_payload


SCORE_OPTIONS = {
    "ubs_pass_min_net_profit": "--min-net-profit",
    "ubs_pass_min_profit_factor": "--min-profit-factor",
    "ubs_pass_min_trades": "--min-trades",
    "ubs_pass_max_drawdown_pct": "--max-drawdown-pct",
    "ubs_pass_min_recovery_factor": "--min-recovery-factor",
    "ubs_long_tf_min_trades_w1": "--min-trades-w1",
    "ubs_long_tf_min_trades_mn": "--min-trades-mn",
}

VALUE_OPTIONS = {
    "--source-dir", "--output-dir", "--memory", "--broker", "--account-type", "--template",
    "--generations", "--variants-per-seed", "--max-seeds", "--delay", "--generation-mode",
    "--from-date", "--to-date", "--min-net-profit", "--min-profit-factor", "--min-trades",
    "--max-drawdown-pct", "--min-recovery-factor", "--min-trades-w1", "--min-trades-mn",
    "--terminals-config", "--max-workers", "--expert", "--mt5-path", "--data-dir", "--symbol-map",
    "--symbol-suffix", "--symbol-futures-suffix", "--symbol-shares-suffix",
    "--robust-run-id", "--robust-positive-bonus", "--robust-negative-bonus",
    "--final-tick-run-id", "--final-tick-stage", "--final-tick-min-history-quality",
    "--final-tick-min-ohlc-trades", "--final-tick-min-trades-w1", "--final-tick-min-trades-mn",
    "--final-tick-max-net-delta-pct", "--final-tick-max-pf-delta-pct",
    "--final-tick-max-dd-delta-pct", "--final-tick-max-trades-delta-pct",
    "--final-tick-ohlc-from-date", "--final-tick-ohlc-to-date",
}


def read_settings(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def setting(parser: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    return parser.get(section, key, fallback=default).strip()


def setting_bool(parser: configparser.ConfigParser, section: str, key: str, default: bool = False) -> bool:
    try:
        return parser.getboolean(section, key, fallback=default)
    except ValueError:
        return default


def _universe_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    broker = str(config.get("broker") or "ROBOFOREX").strip().upper()
    account = str(config.get("account_type") or "ECN").strip().upper()
    return (
        project / "assets" / f"{broker.lower()}_assets.ini",
        project / "outputs" / f"ubs_disabled_symbols_{broker}_{account}.json",
    )


def _load_universe_rows(config: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    assets_path, policy_path = _universe_paths(config)
    if not assets_path.is_file():
        raise ValueError(f"No existe el universo de activos: {assets_path}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(assets_path, encoding="utf-8-sig")
    aliases = {
        str(alias).strip().upper(): str(target).strip().upper()
        for alias, target in (parser["CommonAliases"].items() if parser.has_section("CommonAliases") else [])
        if str(alias).strip() and str(target).strip()
    }
    reverse_aliases: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        reverse_aliases.setdefault(target, []).append(alias)
    policy: dict[str, Any] = {}
    if policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
            policy = loaded if isinstance(loaded, dict) else {"disabled": loaded if isinstance(loaded, list) else []}
        except (OSError, json.JSONDecodeError):
            policy = {}
    disabled = {str(value).strip().upper() for value in policy.get("disabled") or [] if str(value).strip()}
    seed_enabled = {
        str(value).strip().upper()
        for value in policy.get("seed_enabled_when_disabled") or []
        if str(value).strip()
    } & disabled
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in parser.sections():
        if section == "CommonAliases":
            continue
        for raw in parser[section].get("symbols", "").split(","):
            symbol = raw.strip().upper()
            canonical = aliases.get(symbol, symbol)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            generation_enabled = canonical not in disabled
            rows.append({
                "symbol": canonical,
                "group": section,
                "aliases": sorted(reverse_aliases.get(canonical, [])),
                "generation_enabled": generation_enabled,
                "seeds_enabled": generation_enabled or canonical in seed_enabled,
            })
    rows.sort(key=lambda item: (str(item["group"]).casefold(), str(item["symbol"]).casefold()))
    return rows, disabled, seed_enabled


def memory_path(config: dict[str, Any], parser: configparser.ConfigParser) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    explicit = str(config.get("memory_path") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_absolute() else project / path
    broker = str(config.get("broker") or setting(parser, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(parser, "General", "ubs_account_type", "ECN")).upper()
    scoped = project / "outputs" / f"ubs_memory_{broker}_{account}.sqlite"
    legacy = project / "outputs" / "ubs_memory.sqlite"
    script = project / "ubs_agent.py"
    try:
        supports_broker = '"--broker"' in script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        supports_broker = True
    return scoped if supports_broker else legacy


def _add(args: list[str], option: str, value: Any) -> None:
    text = str(value).strip()
    if text:
        args.extend([option, text])


def filter_supported_options(command: list[str], script: Path) -> list[str]:
    """Remove manager options that an older broker branch does not expose."""
    try:
        source = script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return command
    supported = set(re.findall(r"[\"'](--[a-z0-9-]+)[\"']", source, flags=re.IGNORECASE))
    # A custom wrapper may not define argparse options in its own source.
    if "--generations" not in supported:
        return command
    prefix, options = command[:3], command[3:]
    filtered: list[str] = []
    index = 0
    while index < len(options):
        token = options[index]
        if token.startswith("--") and token not in supported:
            index += 2 if token in VALUE_OPTIONS and index + 1 < len(options) else 1
            continue
        filtered.append(token)
        index += 1
    return prefix + filtered


def build_generation_command(config: dict[str, Any], payload: dict[str, Any]) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    script = project / "ubs_agent.py"
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    if not script.is_file():
        raise ValueError(f"No existe {script}")
    if not settings_path.is_file():
        raise ValueError(f"No existe {settings_path}")
    cfg = read_settings(settings_path)
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}

    def pick(name: str, settings_key: str, fallback: Any) -> Any:
        if name in payload:
            return payload[name]
        if name in defaults:
            return defaults[name]
        return setting(cfg, "General", settings_key, str(fallback))

    broker = str(config.get("broker") or setting(cfg, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(cfg, "General", "ubs_account_type", "ECN")).upper()
    source = str(config.get("source_dir") or setting(cfg, "Paths", "set_files_root"))
    output = str(config.get("output_dir") or setting(cfg, "Paths", "ubs_generation_output"))
    template = str(config.get("template") or setting(cfg, "Paths", "template_path", str(project / "tester_template.ini")))
    python = str(config.get("python_executable") or sys.executable)
    generations = safe_int(pick("generations", "ubs_generation_count", 1), 1, minimum=1, maximum=1000)
    variants = safe_int(pick("variants_per_seed", "ubs_variants_per_seed", 10), 10, minimum=1, maximum=10000)
    max_seeds = safe_int(pick("max_seeds", "ubs_max_seeds", 30), 30, minimum=0, maximum=100000)
    generation_mode = str(pick("generation_mode", "ubs_generation_mode", "production")).lower()
    if generation_mode not in {"production", "discovery"}:
        raise ValueError("generation_mode debe ser production o discovery")
    execute = payload.get("execute_backtests", defaults.get("execute_backtests", setting_bool(cfg, "General", "ubs_agent_execute", True)))

    args = [python, "-u", str(script)]
    _add(args, "--source-dir", source)
    _add(args, "--output-dir", output)
    _add(args, "--memory", memory_path(config, cfg))
    _add(args, "--broker", broker)
    _add(args, "--account-type", account)
    _add(args, "--template", template)
    _add(args, "--generations", generations)
    _add(args, "--variants-per-seed", variants)
    _add(args, "--max-seeds", max_seeds)
    _add(args, "--delay", pick("delay", "delay", 5))
    _add(args, "--generation-mode", generation_mode)
    _add(args, "--from-date", payload.get("from_date", defaults.get("from_date", setting(cfg, "General", "ubs_agent_from_date"))))
    _add(args, "--to-date", payload.get("to_date", defaults.get("to_date", setting(cfg, "General", "ubs_agent_to_date"))))

    for key, option in SCORE_OPTIONS.items():
        _add(args, option, setting(cfg, "General", key))
    if setting_bool(cfg, "General", "ubs_experimental_long_timeframes"):
        args.append("--experimental-long-timeframes")
    if bool(payload.get("continue_last", False)):
        args.append("--continue-last-run")
    if bool(payload.get("dry_run", False)):
        args.append("--dry-run")
    if execute:
        args.append("--execute-backtests")
        if setting_bool(cfg, "Multiterminal", "enabled"):
            args.extend(["--multi-terminal", "--terminals-config", str(settings_path)])
            workers = safe_int(
                payload.get("max_workers", setting(cfg, "Multiterminal", "workers", "1")),
                1,
                minimum=1,
                maximum=64,
            )
            _add(args, "--max-workers", workers)
        else:
            expert = str(config.get("expert") or setting(cfg, "Paths", "ubs_ex5_file"))
            if not expert:
                raise ValueError("Falta Paths.ubs_ex5_file y no hay multiterminal habilitado")
            _add(args, "--expert", expert)
            _add(args, "--mt5-path", setting(cfg, "Paths", "mt5_path"))
            _add(args, "--data-dir", setting(cfg, "Paths", "mt5_data_root"))
        broker_key = broker.lower().replace(" ", "")
        if setting_bool(cfg, "General", "symbol_map_enabled"):
            symbol_map = setting(cfg, "General", f"symbol_map_{broker_key}") or setting(cfg, "General", "symbol_map")
            _add(args, "--symbol-map", symbol_map)
        if setting_bool(cfg, "General", "symbol_suffix_enabled"):
            _add(args, "--symbol-suffix", setting(cfg, "General", "symbol_suffix"))
            _add(args, "--symbol-futures-suffix", setting(cfg, "General", "symbol_futures_suffix"))
            _add(args, "--symbol-shares-suffix", setting(cfg, "General", "symbol_shares_suffix"))
    return filter_supported_options(args, script), project


def build_pipeline_stage_command(
    config: dict[str, Any],
    payload: dict[str, Any],
    stage: str,
    run_id: int,
) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    script = project / "ubs_agent.py"
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    cfg = read_settings(settings_path)
    broker = str(config.get("broker") or setting(cfg, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(cfg, "General", "ubs_account_type", "ECN")).upper()
    python = str(config.get("python_executable") or sys.executable)
    args = [python, "-u", str(script)]
    _add(args, "--source-dir", config.get("source_dir") or setting(cfg, "Paths", "set_files_root"))
    _add(args, "--output-dir", config.get("output_dir") or setting(cfg, "Paths", "ubs_generation_output"))
    _add(args, "--memory", memory_path(config, cfg))
    _add(args, "--broker", broker)
    _add(args, "--account-type", account)
    _add(args, "--template", config.get("template") or setting(cfg, "Paths", "template_path", project / "tester_template.ini"))
    _add(args, "--delay", payload.get("delay", setting(cfg, "General", "delay", "5")))

    if stage == "result":
        db_path = memory_path(config, cfg)
        if not db_path.is_file():
            raise ValueError(f"No existe la memoria SQLite: {db_path}")
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "runs"):
                raise ValueError("La memoria SQLite no contiene la tabla runs")
            run = conn.execute("select config_json from runs where id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"No existe el run #{run_id} en memoria")
        try:
            run_config = json.loads(str(run["config_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"El run #{run_id} tiene config_json invalido") from exc
        run_config = run_config if isinstance(run_config, dict) else {}
        execution = run_config.get("execution") if isinstance(run_config.get("execution"), dict) else {}
        run_args = run_config.get("args") if isinstance(run_config.get("args"), dict) else {}
        from_date = str(execution.get("from_date") or run_args.get("from_date") or "").strip()
        to_date = str(execution.get("to_date") or run_args.get("to_date") or "").strip()
        if not from_date or not to_date:
            raise ValueError(
                f"El run #{run_id} no guarda sus fechas base; se cancela para no usar fechas actuales"
            )
        args.append("--retry-mismatch-run")
        _add(args, "--retry-run-id", run_id)
        _add(args, "--from-date", from_date)
        _add(args, "--to-date", to_date)
        for key, option in SCORE_OPTIONS.items():
            _add(args, option, setting(cfg, "General", key))
    elif stage == "robustness":
        args.extend(["--evaluate-robustness", "--robust-pending-only"])
        _add(args, "--robust-run-id", run_id)
        _add(args, "--robust-positive-bonus", setting(cfg, "General", "ubs_robust_positive_bonus", "70"))
        _add(args, "--robust-negative-bonus", setting(cfg, "General", "ubs_robust_negative_bonus", "-70"))
        _add(args, "--from-date", setting(cfg, "General", "ubs_robust_from_date"))
        _add(args, "--to-date", setting(cfg, "General", "ubs_robust_to_date"))
        robust_score_options = {
            "ubs_robust_pass_min_net_profit": "--min-net-profit",
            "ubs_robust_pass_min_profit_factor": "--min-profit-factor",
            "ubs_robust_pass_min_trades": "--min-trades",
            "ubs_robust_pass_max_drawdown_pct": "--max-drawdown-pct",
            "ubs_robust_pass_min_recovery_factor": "--min-recovery-factor",
            "ubs_long_tf_min_trades_w1": "--min-trades-w1",
            "ubs_long_tf_min_trades_mn": "--min-trades-mn",
        }
        for key, option in robust_score_options.items():
            _add(args, option, setting(cfg, "General", key))
    elif stage in {"final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"}:
        six_month = stage in {"final_tick_6m", "final_tick_6m_quality"}
        retry_quality = stage in {"final_tick_quality", "final_tick_6m_quality"}
        prefix = "ubs_final_tick_6m" if six_month else "ubs_final_tick"
        args.extend(["--evaluate-final-tick", "--final-tick-pending-only"])
        if retry_quality:
            args.extend(["--final-tick-retry-pending-quality", "--final-tick-skip-ohlc"])
        _add(args, "--final-tick-run-id", run_id)
        _add(args, "--final-tick-stage", "six_month" if six_month else "probe")
        _add(args, "--from-date", setting(cfg, "General", f"{prefix}_from_date"))
        _add(args, "--to-date", setting(cfg, "General", f"{prefix}_to_date"))
        _add(args, "--final-tick-ohlc-from-date", setting(cfg, "General", f"{prefix}_ohlc_from_date"))
        _add(args, "--final-tick-ohlc-to-date", setting(cfg, "General", f"{prefix}_ohlc_to_date"))
        final_options = {
            "ubs_final_tick_min_history_quality": "--final-tick-min-history-quality",
            "ubs_final_tick_min_ohlc_trades": "--final-tick-min-ohlc-trades",
            "ubs_final_tick_min_trades_w1": "--final-tick-min-trades-w1",
            "ubs_final_tick_min_trades_mn": "--final-tick-min-trades-mn",
            "ubs_final_tick_max_net_delta_pct": "--final-tick-max-net-delta-pct",
            "ubs_final_tick_max_pf_delta_pct": "--final-tick-max-pf-delta-pct",
            "ubs_final_tick_max_dd_delta_pct": "--final-tick-max-dd-delta-pct",
            "ubs_final_tick_max_trades_delta_pct": "--final-tick-max-trades-delta-pct",
        }
        for key, option in final_options.items():
            _add(args, option, setting(cfg, "General", key))
    else:
        raise ValueError(f"Etapa de pipeline desconocida: {stage}")

    if bool(payload.get("dry_run", False)):
        args.append("--dry-run")
    if setting_bool(cfg, "Multiterminal", "enabled"):
        args.extend(["--multi-terminal", "--terminals-config", str(settings_path)])
        workers = safe_int(payload.get("max_workers", setting(cfg, "Multiterminal", "workers", "1")), 1, minimum=1, maximum=64)
        _add(args, "--max-workers", workers)
    else:
        expert = str(config.get("expert") or setting(cfg, "Paths", "ubs_ex5_file"))
        if not expert:
            raise ValueError("Falta Paths.ubs_ex5_file y no hay multiterminal habilitado")
        _add(args, "--expert", expert)
        _add(args, "--mt5-path", setting(cfg, "Paths", "mt5_path"))
        _add(args, "--data-dir", setting(cfg, "Paths", "mt5_data_root"))
    broker_key = broker.lower().replace(" ", "")
    if setting_bool(cfg, "General", "symbol_map_enabled"):
        _add(args, "--symbol-map", setting(cfg, "General", f"symbol_map_{broker_key}") or setting(cfg, "General", "symbol_map"))
    if setting_bool(cfg, "General", "symbol_suffix_enabled"):
        _add(args, "--symbol-suffix", setting(cfg, "General", "symbol_suffix"))
        _add(args, "--symbol-futures-suffix", setting(cfg, "General", "symbol_futures_suffix"))
        _add(args, "--symbol-shares-suffix", setting(cfg, "General", "symbol_shares_suffix"))
    return filter_supported_options(args, script), project


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone()
    return row is not None


def _counts(conn: sqlite3.Connection, table: str, run_id: int) -> dict[str, int]:
    if not _table_exists(conn, table):
        return {}
    rows = conn.execute(f"select status, count(*) total from {table} where run_id=? group by status", (run_id,))
    return {str(row[0] or "unknown"): int(row[1]) for row in rows}


def database_snapshot(path: Path) -> dict[str, Any]:
    empty = {"available": False, "path": str(path), "latest_run": None, "stages": {}}
    if not path.is_file():
        return empty
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "runs"):
                return empty
            run = conn.execute("select * from runs where coalesce(hidden,0)=0 order by id desc limit 1").fetchone()
            if run is None:
                return {**empty, "available": True}
            run_dict = dict(run)
            run_id = int(run_dict["id"])
            max_generation = conn.execute("select coalesce(max(generation),0) from candidates where run_id=?", (run_id,)).fetchone()[0]
            stages = {
                "generation": _counts(conn, "candidates", run_id),
                "robustness": _counts(conn, "candidate_robustness", run_id),
                "final_tick": _counts(conn, "candidate_final_tick", run_id),
                "final_tick_6m": _counts(conn, "candidate_final_tick_6m", run_id),
            }
            return {
                "available": True,
                "path": str(path),
                "latest_run": run_dict,
                "max_generation": int(max_generation or 0),
                "stages": stages,
            }
    except (sqlite3.Error, OSError) as exc:
        return {**empty, "error": str(exc)}


def completed_runs_snapshot(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "runs") or not _table_exists(conn, "candidates"):
                return []
            rows = conn.execute(
                "select * from runs where coalesce(hidden,0)=0 order by id desc limit ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            result: list[dict[str, Any]] = []
            non_terminal = {"generated", "pending", "running"}
            for row in rows:
                run = dict(row)
                run_id = int(run["id"])
                candidate_counts = _counts(conn, "candidates", run_id)
                max_generation = int(conn.execute(
                    "select coalesce(max(generation),0) from candidates where run_id=?", (run_id,)
                ).fetchone()[0] or 0)
                generations = int(run.get("generations") or 0)
                completed = bool(candidate_counts) and max_generation >= generations and not any(
                    candidate_counts.get(status, 0) for status in non_terminal
                )
                result.append({
                    "id": run_id,
                    "created_at": run.get("created_at"),
                    "generations": generations,
                    "max_generation": max_generation,
                    "completed": completed,
                    "candidate_counts": candidate_counts,
                    "stages": {
                        "robustness": _counts(conn, "candidate_robustness", run_id),
                        "final_tick": _counts(conn, "candidate_final_tick", run_id),
                        "final_tick_6m": _counts(conn, "candidate_final_tick_6m", run_id),
                    },
                })
            return result
    except (sqlite3.Error, OSError, ValueError):
        return []


ROBUST_RETRYABLE_STATUSES = {"pending", "no_report", "parse_error", "report_mismatch", "no_trades"}
FINAL_TICK_RETRYABLE_STATUSES = {"pending", "no_report", "parse_error", "report_mismatch"}


def _workspace_path_exists(value: object, project: Path) -> bool:
    path = Path(str(value or "")).expanduser()
    if path.exists():
        return True
    if not path.is_absolute() and (project / path).exists():
        return True
    parts = path.parts
    lowered = [part.lower() for part in parts]
    for root_name in ("outputs", "sets", "reports", "configs", "assets"):
        if root_name not in lowered:
            continue
        candidate = project.joinpath(*parts[lowered.index(root_name):])
        if candidate.exists():
            return True
    return False


def pipeline_stage_pending_count(
    config: dict[str, Any], payload: dict[str, Any], stage: str, run_id: int
) -> int:
    """Return the candidates the agent would actually consider for a pending pipeline stage."""
    project = Path(str(config["project_dir"])).expanduser().resolve()
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    cfg = read_settings(settings_path)
    db_path = memory_path(config, cfg)
    if not db_path.is_file():
        raise ValueError(f"No existe la memoria SQLite: {db_path}")

    uri = db_path.resolve().as_uri() + "?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "candidates"):
            raise ValueError("La memoria SQLite no contiene la tabla candidates")

        if stage == "result":
            rows = conn.execute(
                "select set_path from candidates "
                "where run_id=? and status in ('report_mismatch','no_report')",
                (run_id,),
            ).fetchall()
            return sum(1 for row in rows if _workspace_path_exists(row["set_path"], project))

        if stage == "robustness":
            robust_join = (
                "left join candidate_robustness cr on cr.candidate_id=c.id"
                if _table_exists(conn, "candidate_robustness")
                else ""
            )
            robust_status = "cr.status" if robust_join else "null"
            rows = conn.execute(
                f"select c.set_path,{robust_status} stage_status from candidates c "
                f"{robust_join} where c.run_id=? and c.status='accepted'",
                (run_id,),
            ).fetchall()
            return sum(
                1 for row in rows
                if _workspace_path_exists(row["set_path"], project)
                and (not str(row["stage_status"] or "").strip()
                     or str(row["stage_status"] or "").strip() in ROBUST_RETRYABLE_STATUSES)
            )

        if stage not in {"final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"}:
            raise ValueError(f"Etapa de pipeline desconocida: {stage}")

        six_month = stage in {"final_tick_6m", "final_tick_6m_quality"}
        quality_only = stage in {"final_tick_quality", "final_tick_6m_quality"}
        if not _table_exists(conn, "candidate_robustness"):
            return 0
        if six_month and not _table_exists(conn, "candidate_final_tick"):
            return 0
        table = "candidate_final_tick_6m" if six_month else "candidate_final_tick"
        has_table = _table_exists(conn, table)
        stage_join = f"left join {table} ft on ft.candidate_id=c.id" if has_table else ""
        status_expr = "ft.status" if has_table else "null"
        from_expr = "ft.from_date" if has_table else "null"
        to_expr = "ft.to_date" if has_table else "null"
        probe_join = (
            "join candidate_final_tick probe_ft on probe_ft.candidate_id=c.id "
            "and probe_ft.status in ('accepted','pending_ohlc_trades')"
            if six_month else ""
        )
        rows = conn.execute(
            f"select c.set_path,{status_expr} stage_status,{from_expr} stage_from,{to_expr} stage_to "
            "from candidates c "
            "join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted' "
            f"{probe_join} {stage_join} "
            "where c.run_id=? and c.status='accepted'",
            (run_id,),
        ).fetchall()
        prefix = "ubs_final_tick_6m" if six_month else "ubs_final_tick"
        main_dates = (
            setting(cfg, "General", f"{prefix}_from_date"),
            setting(cfg, "General", f"{prefix}_to_date"),
        )
        retry_dates = (
            setting(cfg, "General", f"{prefix}_ohlc_from_date"),
            setting(cfg, "General", f"{prefix}_ohlc_to_date"),
        )

        def pending(row: sqlite3.Row) -> bool:
            if not _workspace_path_exists(row["set_path"], project):
                return False
            status = str(row["stage_status"] or "").strip()
            if quality_only:
                return status == "pending_history_quality"
            if not status or status in FINAL_TICK_RETRYABLE_STATUSES:
                return True
            if not six_month or status not in {"pending_history_quality", "pending_ohlc_trades"}:
                return False
            dates = retry_dates if status == "pending_ohlc_trades" and all(retry_dates) else main_dates
            stored = (str(row["stage_from"] or "").strip(), str(row["stage_to"] or "").strip())
            return stored != dates

        return sum(1 for row in rows if pending(row))


class JobController:
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
            "completed_stages": [], "stage_return_codes": {},
        }
        if self.state_path.is_file():
            try:
                old = load_json(self.state_path)
                self.state.update(old)
                if self.state.get("status") == "running":
                    self.state["status"] = "unknown_after_restart"
            except ValueError:
                pass
        if self.queue_path.is_file():
            try:
                stored_queue = load_json(self.queue_path)
                if isinstance(stored_queue, list):
                    self.queue = [dict(item) for item in stored_queue if isinstance(item, dict)]
            except ValueError:
                pass
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
        return self.process is not None

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
                    self._start_repair(payload)
                else:
                    self._start_generation(payload)
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

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = self._normalize_generation(payload)
            # Validate paths and options before accepting a queued task.
            build_generation_command(self.config, normalized)
            if self._busy() or self.queue:
                cycles = normalized["cycles"]
                mode = normalized.get("generation_mode", "production")
                return self._enqueue("generation", normalized, f"{cycles} ciclo(s) · {mode}")
            return self._start_generation(normalized)

    def _normalize_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        cycles = safe_int(payload.get("cycles"), 1, minimum=1, maximum=100)
        payload["cycles"] = cycles
        run_robustness = bool(payload.get("run_robustness", False))
        run_final_tick = bool(payload.get("run_final_tick", False))
        run_final_tick_6m = bool(payload.get("run_final_tick_6m", False))
        if run_final_tick_6m:
            run_final_tick = True
            run_robustness = True
        elif run_final_tick:
            run_robustness = True
        payload["run_robustness"] = run_robustness
        payload["run_final_tick"] = run_final_tick
        payload["run_final_tick_6m"] = run_final_tick_6m
        repair_after_generation = bool(payload.get("repair_after_generation", False))
        repair_attempts = safe_int(payload.get("repair_attempts"), 1, minimum=1, maximum=20)
        payload["repair_after_generation"] = repair_after_generation
        payload["repair_attempts"] = repair_attempts
        return payload

    def _start_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_generation(payload)
        cycles = payload["cycles"]
        run_robustness = payload["run_robustness"]
        run_final_tick = payload["run_final_tick"]
        run_final_tick_6m = payload["run_final_tick_6m"]
        repair_after_generation = payload["repair_after_generation"]
        repair_attempts = payload["repair_attempts"]
        pipeline: list[dict[str, Any]] = []
        for cycle in range(1, cycles + 1):
            pipeline.append({"action": "generation", "cycle": cycle, "run_id": None})
            if repair_after_generation:
                repair_actions = ["result"]
                if run_robustness:
                    repair_actions.append("robustness")
                if run_final_tick:
                    repair_actions.extend(["final_tick", "final_tick_quality"])
                if run_final_tick_6m:
                    repair_actions.extend(["final_tick_6m", "final_tick_6m_quality"])
                pipeline.extend(
                    {
                        "action": action, "cycle": cycle, "run_id": None,
                        "attempt": attempt, "max_workers": 1,
                    }
                    for attempt in range(1, repair_attempts + 1)
                    for action in repair_actions
                )
            else:
                if run_robustness:
                    pipeline.append({"action": "robustness", "cycle": cycle, "run_id": None})
                if run_final_tick:
                    pipeline.append({"action": "final_tick", "cycle": cycle, "run_id": None})
                if run_final_tick_6m:
                    pipeline.append({"action": "final_tick_6m", "cycle": cycle, "run_id": None})
        command, cwd = build_generation_command(self.config, payload)
        job_id = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        log_path = self.runtime_dir / f"generation_{job_id}.log"
        self.state = {
            "job_id": job_id, "status": "running", "pid": None,
            "started_at": utc_now(), "finished_at": None, "return_code": None,
            "request": payload, "command": command, "log_path": str(log_path), "error": None,
            "job_type": "generation", "pipeline": pipeline, "current_stage": "generation",
            "current_cycle": 1, "current_run_id": None, "completed_stages": [],
            "stage_return_codes": {}, "commands": {"cycle_1_generation": command},
            "cycle_run_ids": {}, "skipped_stages": [], "stage_pending_counts": {},
        }
        self._launch_step(0, command, cwd, log_path, first=True)
        return {**dict(self.state), "queued": False, "task_queue": self._queue_snapshot()}

    def start_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = self._normalize_repair(payload)
            if self._busy() or self.queue:
                run_ids = normalized["run_ids"]
                attempts = normalized["repair_attempts"]
                return self._enqueue(
                    "repair", normalized,
                    f"Run(s) {', '.join(str(value) for value in run_ids)} · {attempts} intento(s)",
                )
            return self._start_repair(normalized)

    def _normalize_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = payload.get("run_ids")
        if not isinstance(requested, list):
            raise ValueError("run_ids debe ser una lista")
        run_ids = list(dict.fromkeys(safe_int(value, 0, minimum=0) for value in requested))
        run_ids = [value for value in run_ids if value > 0]
        if not run_ids:
            raise ValueError("Selecciona al menos un run terminado")
        payload = dict(payload)
        payload["run_ids"] = run_ids
        payload["max_workers"] = 1
        payload["execute_backtests"] = True
        repair_attempts = safe_int(payload.get("repair_attempts"), 1, minimum=1, maximum=20)
        payload["repair_attempts"] = repair_attempts
        retry_low_quality = bool(payload.get("retry_low_quality", True))
        payload["retry_low_quality"] = retry_low_quality
        return payload

    def _start_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_repair(payload)
        run_ids = payload["run_ids"]
        repair_attempts = payload["repair_attempts"]
        retry_low_quality = payload["retry_low_quality"]
        actions = ["result", "robustness", "final_tick"]
        if retry_low_quality:
            actions.append("final_tick_quality")
        actions.append("final_tick_6m")
        if retry_low_quality:
            actions.append("final_tick_6m_quality")
        pipeline = [
            {"action": action, "cycle": None, "run_id": run_id, "attempt": attempt}
            for run_id in run_ids
            for attempt in range(1, repair_attempts + 1)
            for action in actions
        ]
        job_id = "repair_" + time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        log_path = self.runtime_dir / f"{job_id}.log"
        self.state = {
            "job_id": job_id, "job_type": "repair", "status": "running", "pid": None,
            "started_at": utc_now(), "finished_at": None, "return_code": None,
            "request": payload, "command": None, "log_path": str(log_path), "error": None,
            "pipeline": pipeline, "current_stage": None, "current_cycle": None,
            "current_run_id": None, "current_attempt": None,
            "completed_stages": [], "skipped_stages": [],
            "stage_return_codes": {}, "stage_pending_counts": {}, "commands": {}, "cycle_run_ids": {},
        }
        try:
            launched = self._launch_next_runnable(0, log_path, first=True)
        except Exception as exc:
            self.state["error"] = str(exc)
            self.state["return_code"] = 1
            self.state["finished_at"] = utc_now()
            self.state["status"] = "failed"
            self._persist()
            raise
        if not launched:
            self._complete(0)
        return {**dict(self.state), "queued": False, "task_queue": self._queue_snapshot()}

    @staticmethod
    def _step_label(step: dict[str, Any]) -> str:
        cycle = step.get("cycle")
        stage = str(step["action"])
        if cycle is not None:
            if step.get("attempt") is not None:
                return f"cycle_{cycle}_attempt_{step.get('attempt')}_{stage}"
            return f"cycle_{cycle}_{stage}"
        attempt = step.get("attempt")
        return f"run_{step.get('run_id')}_attempt_{attempt}_{stage}"

    def _append_skip_log(self, log_path: Path, label: str) -> None:
        with log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[manager-node] Etapa omitida: {label}; no hay candidatos pendientes.\n")

    def _launch_next_runnable(self, step_index: int, log_path: Path, *, first: bool = False) -> bool:
        pipeline = list(self.state.get("pipeline") or [])
        request = dict(self.state.get("request") or {})
        while step_index < len(pipeline):
            step = pipeline[step_index]
            stage = str(step["action"])
            label = self._step_label(step)
            step_request = dict(request)
            if step.get("max_workers") is not None:
                step_request["max_workers"] = step["max_workers"]
            if stage == "generation":
                command, cwd = build_generation_command(self.config, step_request)
            else:
                run_id = safe_int(step.get("run_id"), 0, minimum=0)
                if run_id <= 0:
                    raise ValueError("No se encontro el run para continuar el pipeline")
                pending_count = pipeline_stage_pending_count(self.config, step_request, stage, run_id)
                self.state.setdefault("stage_pending_counts", {})[label] = pending_count
                if pending_count == 0:
                    self.state.setdefault("skipped_stages", []).append(label)
                    self.state.setdefault("stage_return_codes", {})[label] = None
                    self._append_skip_log(log_path, label)
                    self._persist()
                    step_index += 1
                    first = False
                    continue
                command, cwd = build_pipeline_stage_command(self.config, step_request, stage, run_id)
            self.state.setdefault("commands", {})[label] = command
            self._launch_step(step_index, command, cwd, log_path, first=first)
            return True
        return False

    def _complete(self, return_code: int) -> None:
        self.state["return_code"] = return_code
        self.state["finished_at"] = utc_now()
        self.state["status"] = "completed" if return_code == 0 else "failed"
        self.state["pid"] = None
        self.process = None
        self._persist()
        if self.queue:
            self._schedule_queue_drain()

    def _launch_step(self, step_index: int, command: list[str], cwd: Path, log_path: Path, *, first: bool = False) -> None:
        step = list(self.state.get("pipeline") or [])[step_index]
        stage = str(step["action"])
        mode = "w" if first else "a"
        self.log_handle = log_path.open(mode, encoding="utf-8", errors="replace", buffering=1)
        if not first:
            self.log_handle.write(f"\n[manager-node] Iniciando etapa: {stage}\n")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command, cwd=cwd, stdout=self.log_handle, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
        )
        self.process = process
        self.state["pid"] = process.pid
        self.state["current_stage"] = stage
        self.state["current_cycle"] = step.get("cycle")
        self.state["current_run_id"] = step.get("run_id")
        self.state["current_attempt"] = step.get("attempt")
        self.state["command"] = command
        self._persist()
        threading.Thread(target=self._watch, args=(process, step_index), daemon=True).start()

    def _watch(self, process: subprocess.Popen[str], step_index: int) -> None:
        return_code = process.wait()
        with self.lock:
            if process is not self.process:
                return
            if self.log_handle:
                self.log_handle.close()
                self.log_handle = None
            pipeline = list(self.state.get("pipeline") or [])
            step = pipeline[step_index]
            stage = str(step["action"])
            cycle = step.get("cycle")
            run_id = step.get("run_id")
            label = self._step_label(step)
            self.state.setdefault("stage_return_codes", {})[label] = return_code
            if return_code == 0:
                self.state.setdefault("completed_stages", []).append(label)
            has_downstream_for_cycle = any(
                pending.get("cycle") == cycle and pending.get("action") != "generation"
                for pending in pipeline[step_index + 1:]
            )
            if return_code == 0 and stage == "generation" and has_downstream_for_cycle:
                try:
                    settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
                    project = Path(str(self.config["project_dir"])).expanduser().resolve()
                    if not settings_path.is_absolute():
                        settings_path = project / settings_path
                    cfg = read_settings(settings_path)
                    snapshot = database_snapshot(memory_path(self.config, cfg))
                    generated_run = safe_int((snapshot.get("latest_run") or {}).get("id"), 0, minimum=0)
                    if generated_run <= 0:
                        raise ValueError("No se encontro el run generado")
                    self.state.setdefault("cycle_run_ids", {})[str(cycle)] = generated_run
                    for pending_step in pipeline:
                        if pending_step.get("cycle") == cycle:
                            pending_step["run_id"] = generated_run
                    self.state["pipeline"] = pipeline
                except Exception as exc:
                    self.state["error"] = str(exc)
                    return_code = 1
            next_index = step_index + 1
            if return_code == 0 and next_index < len(pipeline):
                try:
                    if self._launch_next_runnable(next_index, Path(str(self.state["log_path"]))):
                        return
                except Exception as exc:
                    self.state["error"] = str(exc)
                    return_code = 1
            self._complete(return_code)

    def stop(self) -> dict[str, Any]:
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                raise RuntimeError("No hay ninguna generacion activa")
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGTERM)
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
            self.state["status"] = "stopping"
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
                "max_workers": safe_int(setting(cfg, "Multiterminal", "workers", "1"), 1, minimum=1, maximum=64),
                "run_robustness": setting_bool(cfg, "General", "ubs_robust_auto", False),
                "run_final_tick": setting_bool(cfg, "General", "ubs_final_tick_auto", False),
                "run_final_tick_6m": setting_bool(cfg, "General", "ubs_final_tick_6m_auto", False),
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
                "worker_override": True,
                "pipeline_controls": True,
                "cycles": True,
                "repair_runs": True,
                "universe_management": True,
                "portfolio_views": True,
                "task_queue": True,
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

    def portfolios(self, scope: str = "full_history") -> dict[str, Any]:
        portfolio_scope = "monthly" if str(scope).strip().lower() == "monthly" else "full_history"
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
        selected["members"] = [{"variant_key":str(raw.get("variant_key") or ""),"variant_label":str(raw.get("variant_label") or ""),"set_id":str(raw.get("set_id") or ""),"set_name":Path(str(raw.get("set_path") or raw.get("set_id") or "")).name,"candidate_id":str(raw.get("candidate_id") or ""),"symbol":str(raw.get("symbol") or ""),"timeframe":str(raw.get("timeframe") or ""),"units":int(raw.get("units") or 0),"lot":float(raw.get("lot") or 0),"lot_size_step":float(raw.get("lot_size_step") or 0),"net_profit_contribution":float(raw.get("net_profit_contribution") or 0),"standalone_valley_dd":float(raw.get("standalone_valley_dd") or 0),"standalone_point_dd":float(raw.get("standalone_point_dd") or 0),"margin_required":float(raw.get("margin_required") or 0),"margin_pct":float(raw.get("margin_pct") or 0)} for raw in members]
        return {"node": listing["node"], "scope": listing["scope"], "portfolio": selected, "observed_at": utc_now()}

    def runs(self, limit: int = 100) -> dict[str, Any]:
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        cfg = read_settings(settings_path)
        path = memory_path(self.config, cfg)
        runs = completed_runs_snapshot(path, limit)
        return {"runs": runs, "memory_path": str(path), "observed_at": utc_now()}

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
        elif parsed.path == "/api/v1/status":
            self._send(200, self.server.controller.status())
        elif parsed.path == "/api/v1/logs":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.log_tail(safe_int(query.get("lines", [200])[0], 200)))
        elif parsed.path == "/api/v1/runs":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.runs(safe_int(query.get("limit", [100])[0], 100)))
        elif parsed.path == "/api/v1/universe":
            self._send(200, self.server.controller.universe())
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
            if self.path == "/api/v1/jobs/generation":
                self._send(202, self.server.controller.start(self._body()))
            elif self.path == "/api/v1/jobs/repair":
                self._send(202, self.server.controller.start_repair(self._body()))
            elif self.path == "/api/v1/jobs/stop":
                self._send(202, self.server.controller.stop())
            elif self.path == "/api/v1/jobs/queue/cancel":
                self._send(200, self.server.controller.cancel_queued(str(self._body().get("task_id") or "")))
            elif self.path == "/api/v1/universe/symbols":
                self._send(200, self.server.controller.update_universe(self._body()))
            elif self.path == "/api/v1/portfolios/save":
                self._send(201, self.server.controller.save_portfolio(self._body(50_000_000)))
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": str(exc)})


class NodeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: JobController) -> None:
        self.controller = controller
        super().__init__(address, NodeHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nodo remoto para MT5 Autotester Manager")
    parser.add_argument("--config", default="node.json")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    config = load_json(config_path)
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
