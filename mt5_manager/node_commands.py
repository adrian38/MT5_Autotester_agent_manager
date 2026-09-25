"""Las ordenes que el nodo lanza: generacion y cada etapa del pipeline.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Cambiar una regla aqui no
cambia nada para ellos: ver `AGENTS.md`, seccion «El nodo NO ejecuta este
repositorio».
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import guided_batches
from .common import safe_int
from .node_settings import (
    SCORE_OPTIONS,
    VALUE_OPTIONS,
    _add,
    filter_supported_options,
    memory_path,
    read_settings,
    setting,
    setting_bool,
)
from .node_snapshots import _table_exists


def _generation_paths(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    """Proyecto, script y fichero de ajustes, ya comprobados."""
    project = Path(str(config["project_dir"])).expanduser().resolve()
    script = project / "ubs_agent.py"
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    if not script.is_file():
        raise ValueError(f"No existe {script}")
    if not settings_path.is_file():
        raise ValueError(f"No existe {settings_path}")
    return project, script, settings_path




def build_generation_command(config: dict[str, Any], payload: dict[str, Any]) -> tuple[list[str], Path]:
    project, script, settings_path = _generation_paths(config)
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
    _add(args, "--random-seed", payload.get("random_seed", defaults.get("random_seed")))
    if payload.get("guided_batch_id"):
        prepared = guided_batches.batch_dir(project, payload["guided_batch_id"]) / "batch.json"
        _add(args, "--prepared-manifest", prepared)
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
        _terminal_options(args, config, cfg, payload, settings_path, broker)
    return filter_supported_options(args, script), project

def _terminal_options(
    args: list[str],
    config: dict[str, Any],
    cfg: Any,
    payload: dict[str, Any],
    settings_path: Path,
    broker: str,
) -> None:
    """Multiterminal o terminal unico, y el mapa de simbolos del broker.

    Lo comparten la generacion y cada etapa del pipeline: era el mismo bloque
    escrito dos veces.
    """
    if setting_bool(cfg, "Multiterminal", "enabled"):
        args.extend(["--multi-terminal", "--terminals-config", str(settings_path)])
        workers = safe_int(
            payload.get("max_workers", setting(cfg, "Multiterminal", "workers", "1")),
            1, minimum=1, maximum=64,
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
        _add(args, "--symbol-map", setting(cfg, "General", f"symbol_map_{broker_key}") or setting(cfg, "General", "symbol_map"))
    if setting_bool(cfg, "General", "symbol_suffix_enabled"):
        _add(args, "--symbol-suffix", setting(cfg, "General", "symbol_suffix"))
        _add(args, "--symbol-futures-suffix", setting(cfg, "General", "symbol_futures_suffix"))
        _add(args, "--symbol-shares-suffix", setting(cfg, "General", "symbol_shares_suffix"))


def _run_base_dates(config: dict[str, Any], cfg: Any, run_id: int) -> tuple[str, str]:
    """Las fechas con las que corrio el run, leidas de su propio config_json.

    Reintentar con las fechas de hoy compararia contra otro periodo y el
    veredicto no significaria nada, asi que sin ellas se cancela.
    """
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
    return from_date, to_date


ROBUST_STAGE_OPTIONS = {
    "ubs_robust_pass_min_net_profit": "--min-net-profit",
    "ubs_robust_pass_min_profit_factor": "--min-profit-factor",
    "ubs_robust_pass_min_trades": "--min-trades",
    "ubs_robust_pass_max_drawdown_pct": "--max-drawdown-pct",
    "ubs_robust_pass_min_recovery_factor": "--min-recovery-factor",
    "ubs_long_tf_min_trades_w1": "--min-trades-w1",
    "ubs_long_tf_min_trades_mn": "--min-trades-mn",
}

FINAL_TICK_STAGE_OPTIONS = {
    "ubs_final_tick_min_history_quality": "--final-tick-min-history-quality",
    "ubs_final_tick_min_ohlc_trades": "--final-tick-min-ohlc-trades",
    "ubs_final_tick_min_trades_w1": "--final-tick-min-trades-w1",
    "ubs_final_tick_min_trades_mn": "--final-tick-min-trades-mn",
    "ubs_final_tick_max_net_delta_pct": "--final-tick-max-net-delta-pct",
    "ubs_final_tick_max_pf_delta_pct": "--final-tick-max-pf-delta-pct",
    "ubs_final_tick_max_dd_delta_pct": "--final-tick-max-dd-delta-pct",
    "ubs_final_tick_max_trades_delta_pct": "--final-tick-max-trades-delta-pct",
}

REGRESSION_STAGE_OPTIONS = {
    "ubs_regression_from_date": "--regression-from-date",
    "ubs_regression_to_date": "--regression-to-date",
    "ubs_regression_min_net_profit": "--regression-min-net-profit",
    "ubs_regression_min_profit_factor": "--regression-min-profit-factor",
    "ubs_regression_min_trades": "--regression-min-trades",
    "ubs_regression_min_trades_w1": "--regression-min-trades-w1",
    "ubs_regression_min_trades_mn": "--regression-min-trades-mn",
    "ubs_regression_max_drawdown_pct": "--regression-max-drawdown-pct",
    "ubs_regression_min_recovery_factor": "--regression-min-recovery-factor",
    "ubs_regression_min_positive_month_ratio": "--regression-min-positive-month-ratio",
    "ubs_regression_min_pf_efficiency": "--regression-min-pf-efficiency",
    "ubs_regression_max_dd_ratio": "--regression-max-dd-ratio",
    "ubs_regression_positive_points": "--regression-positive-points",
    "ubs_regression_negative_points": "--regression-negative-points",
}


def _stage_options(
    args: list[str], config: dict[str, Any], cfg: Any, stage: str, run_id: int,
) -> None:
    """Lo que cada etapa del pipeline anade a la orden."""
    if stage == "result":
        from_date, to_date = _run_base_dates(config, cfg, run_id)
        args.append("--retry-mismatch-run")
        _add(args, "--retry-run-id", run_id)
        _add(args, "--from-date", from_date)
        _add(args, "--to-date", to_date)
        options = SCORE_OPTIONS
    elif stage == "robustness":
        args.extend(["--evaluate-robustness", "--robust-pending-only"])
        _add(args, "--robust-run-id", run_id)
        _add(args, "--robust-positive-bonus", setting(cfg, "General", "ubs_robust_positive_bonus", "70"))
        _add(args, "--robust-negative-bonus", setting(cfg, "General", "ubs_robust_negative_bonus", "-70"))
        _add(args, "--from-date", setting(cfg, "General", "ubs_robust_from_date"))
        _add(args, "--to-date", setting(cfg, "General", "ubs_robust_to_date"))
        options = ROBUST_STAGE_OPTIONS
    elif stage in {"final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"}:
        six_month = stage in {"final_tick_6m", "final_tick_6m_quality"}
        prefix = "ubs_final_tick_6m" if six_month else "ubs_final_tick"
        args.extend(["--evaluate-final-tick", "--final-tick-pending-only"])
        if stage in {"final_tick_quality", "final_tick_6m_quality"}:
            args.extend(["--final-tick-retry-pending-quality", "--final-tick-skip-ohlc"])
        _add(args, "--final-tick-run-id", run_id)
        _add(args, "--final-tick-stage", "six_month" if six_month else "probe")
        _add(args, "--from-date", setting(cfg, "General", f"{prefix}_from_date"))
        _add(args, "--to-date", setting(cfg, "General", f"{prefix}_to_date"))
        _add(args, "--final-tick-ohlc-from-date", setting(cfg, "General", f"{prefix}_ohlc_from_date"))
        _add(args, "--final-tick-ohlc-to-date", setting(cfg, "General", f"{prefix}_ohlc_to_date"))
        options = FINAL_TICK_STAGE_OPTIONS
    elif stage == "regression":
        args.extend(["--evaluate-regression", "--regression-pending-only"])
        _add(args, "--regression-run-id", run_id)
        options = REGRESSION_STAGE_OPTIONS
    else:
        raise ValueError(f"Etapa de pipeline desconocida: {stage}")
    for key, option in options.items():
        _add(args, option, setting(cfg, "General", key))


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
    _stage_options(args, config, cfg, stage, run_id)
    if bool(payload.get("dry_run", False)):
        args.append("--dry-run")
    _terminal_options(args, config, cfg, payload, settings_path, broker)
    return filter_supported_options(args, script), project
