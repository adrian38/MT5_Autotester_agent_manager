"""Lo que el nodo sabe de su memoria: conteos, runs y trabajo pendiente.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Cambiar una regla aqui no
cambia nada para ellos: ver `AGENTS.md`, seccion «El nodo NO ejecuta este
repositorio».
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .node_settings import memory_path, read_settings, setting, setting_bool


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone()
    return row is not None

def _counts(conn: sqlite3.Connection, table: str, run_id: int) -> dict[str, int]:
    if not _table_exists(conn, table):
        return {}
    rows = conn.execute(f"select status, count(*) total from {table} where run_id=? group by status", (run_id,))
    return {str(row[0] or "unknown"): int(row[1]) for row in rows}

def _execution_failure_counts(conn: sqlite3.Connection, table: str, run_id: int) -> dict[str, int]:
    if not _table_exists(conn, table):
        return {}
    columns = {row[1] for row in conn.execute(f"pragma table_info({table})")}
    if "metrics_json" not in columns:
        return {}
    counts: dict[str, int] = {}
    audit_column = ", degradation_json" if "degradation_json" in columns else ""
    for row in conn.execute(f"select metrics_json{audit_column} from {table} where run_id=? and status='rejected'", (run_id,)):
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            continue
        reason = payload.get("failure_type") if isinstance(payload, dict) else None
        if not reason and audit_column:
            try:
                audit = json.loads(row[1] or "{}")
                reason = audit.get("failure_type") if isinstance(audit, dict) else None
            except (TypeError, ValueError):
                pass
        if reason in {"invalid_stops", "incompatible_volume"}:
            counts[reason] = counts.get(reason, 0) + 1
    return counts

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
                "execution_failures": {
                    "generation": _execution_failure_counts(conn, "candidates", run_id),
                    "robustness": _execution_failure_counts(conn, "candidate_robustness", run_id),
                },
            }
    except (sqlite3.Error, OSError) as exc:
        return {**empty, "error": str(exc)}

def completed_runs_snapshot(path: Path, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "runs") or not _table_exists(conn, "candidates"):
                return []
            rows = conn.execute(
                "select * from runs where coalesce(hidden,0)=0 order by id desc limit ? offset ?",
                (max(1, int(limit)), max(0, int(offset))),
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

REGRESSION_RETRYABLE_STATUSES = {
    "no_report", "parse_error", "report_mismatch", "date_mismatch", "no_history",
}

FINAL_TICK_STAGES = {"final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"}


def _result_pending(conn: sqlite3.Connection, project: Path, run_id: int) -> int:
    rows = conn.execute(
        "select set_path from candidates "
        "where run_id=? and status in ('report_mismatch','no_report')",
        (run_id,),
    ).fetchall()
    return sum(1 for row in rows if _workspace_path_exists(row["set_path"], project))


def _robustness_pending(conn: sqlite3.Connection, project: Path, run_id: int) -> int:
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


def _regression_pending(conn: sqlite3.Connection, project: Path, run_id: int) -> int:
    if not _table_exists(conn, "candidate_final_tick_6m"):
        return 0
    regression_join = (
        "left join candidate_regression rg on rg.candidate_id=c.id"
        if _table_exists(conn, "candidate_regression") else ""
    )
    status_expr = "rg.status" if regression_join else "null"
    rows = conn.execute(
        f"select c.set_path,{status_expr} stage_status from candidates c "
        "join candidate_final_tick_6m ft6 "
        "on ft6.candidate_id=c.id and ft6.status='accepted' "
        f"{regression_join} where c.run_id=? and c.status='accepted'",
        (run_id,),
    ).fetchall()
    return sum(
        1 for row in rows
        if _workspace_path_exists(row["set_path"], project)
        and (
            not str(row["stage_status"] or "").strip()
            or str(row["stage_status"] or "").strip() in REGRESSION_RETRYABLE_STATUSES
        )
    )


def _final_tick_rows(conn: sqlite3.Connection, run_id: int, *, six_month: bool) -> list[sqlite3.Row]:
    """Los candidatos de la etapa Final Tick con su estado y sus fechas."""
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
    return conn.execute(
        f"select c.set_path,{status_expr} stage_status,{from_expr} stage_from,{to_expr} stage_to "
        "from candidates c "
        "join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted' "
        f"{probe_join} {stage_join} "
        "where c.run_id=? and c.status='accepted'",
        (run_id,),
    ).fetchall()


def _final_tick_pending(
    conn: sqlite3.Connection, cfg: Any, project: Path, stage: str, run_id: int,
) -> int:
    six_month = stage in {"final_tick_6m", "final_tick_6m_quality"}
    quality_only = stage in {"final_tick_quality", "final_tick_6m_quality"}
    if not _table_exists(conn, "candidate_robustness"):
        return 0
    if six_month and not _table_exists(conn, "candidate_final_tick"):
        return 0
    rows = _final_tick_rows(conn, run_id, six_month=six_month)
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
            return _result_pending(conn, project, run_id)
        if stage == "robustness":
            return _robustness_pending(conn, project, run_id)
        if stage == "regression":
            return _regression_pending(conn, project, run_id)
        if stage not in FINAL_TICK_STAGES:
            raise ValueError(f"Etapa de pipeline desconocida: {stage}")
        return _final_tick_pending(conn, cfg, project, stage, run_id)
