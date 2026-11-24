"""Carga de seeds aceptadas sin cambiar el embudo UBS de candidatos.

Vivía en `prop/prop_experiment_seeds.py` y lo movió aquí el laboratorio de
seeds: lo usan los dos, y `prop` ya importaba de `experiment` —el motor del
laboratorio y su coordinador—, así que la dependencia baja en vez de invertirse.
Al revés habría sido `experiment` → `prop` → `experiment`, que hoy resuelve de
milagro y revienta con el primer import que se añada en el otro sentido.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Collection

from portfolio_manager.ubs_portfolio import RobustStrategySet
from portfolio_manager.ubs_portfolio.reports import period_report_from_strategy_report

from ..portfolio.portfolio_identity import _resolve_source_path


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,),
    ).fetchone()
    return row is not None


def accepted_seed_rows(
    source: Any, *, require_final_tick_6m: bool = False,
) -> list[dict[str, Any]]:
    """Seeds activas que superaron su backtest propio en el agente."""
    with source.connect(write=False) as connection:
        if not _table_exists(connection, "seed_scores"):
            return []
        if require_final_tick_6m and not all(
            _table_exists(connection, table)
            for table in ("seed_final_tick", "seed_final_tick_6m")
        ):
            return []
        connection.row_factory = sqlite3.Row
        joins = """
            join seed_final_tick probe on probe.seed_id = seed_scores.id
            join seed_final_tick_6m six on six.seed_id = seed_scores.id
             and six.seed_mtime = probe.seed_mtime
             and six.seed_size = probe.seed_size
        """ if require_final_tick_6m else ""
        extra = """
             and probe.status in ('accepted', 'pending_ohlc_trades')
             and six.status = 'accepted' and six.accepted = 1
        """ if require_final_tick_6m else ""
        rows = connection.execute(f"""
            select seed_scores.id, seed_scores.seed_path, seed_scores.symbol,
                   seed_scores.period, seed_scores.family, seed_scores.report_path,
                   seed_scores.score, seed_scores.metrics_json,
                   seed_scores.status, seed_scores.active
              from seed_scores {joins}
             where seed_scores.active=1 and seed_scores.accepted=1
               and seed_scores.status='accepted'
                   {extra}
             order by seed_scores.score desc, seed_scores.id desc
        """).fetchall()
    return [dict(row) for row in rows]


def _seed_strategy(row: dict[str, Any], source: Any, parse: Callable[[Path], Any]) -> RobustStrategySet:
    report_path = _resolve_source_path(row.get("report_path"), source.project)
    set_path = _resolve_source_path(row.get("seed_path"), source.project)
    if not report_path or not Path(report_path).is_file():
        raise ValueError(f"informe seed ausente: {row.get('report_path')}")
    report = parse(Path(report_path))
    set_id = f"seed-{row.get('id')}-{Path(set_path).stem}"
    return single_report_strategy(report, row, set_path, set_id, "seed_accepted", report_path=report_path)


def single_report_strategy(
    report: Any, row: dict[str, Any], set_path: str, set_id: str, status: str,
    *, report_path: str,
) -> RobustStrategySet:
    period = period_report_from_strategy_report(report, "seed" if status == "seed_accepted" else "OHLC")
    return RobustStrategySet(
        set_id=set_id,
        candidate_id=str(row.get("candidate_id") or set_id),
        symbol=report.symbol or str(row.get("symbol") or ""),
        timeframe=report.timeframe or str(row.get("period") or ""),
        strategy_family=str(row.get("family") or ("seed" if status == "seed_accepted" else "")),
        robustness_status=status,
        already_used=False,
        report_2020_2024=period,
        report_2025_2026=period,
        curve_2020_2026_001=list(period.pnl_curve_001),
        net_profit_2020_2026_001=period.net_profit_001,
        valley_dd_2020_2026_001=period.valley_dd_001,
        point_dd_2020_2026_001=period.point_dd_001,
        profit_factor_2020_2026=period.profit_factor,
        return_dd_2020_2026=period.return_dd_ratio,
        trades_2020_2026=period.trades,
        set_path=set_path,
        full_history_report_path=str(report_path),
        curve_points_2020_2026_001=list(period.pnl_points_001),
        max_balance_dd_001=period.balance_dd_metric_001,
        max_equity_dd_001=period.equity_dd_metric_001,
        closed_trades_2020_2026=list(period.closed_trades),
        report_coverage=((period.backtest_start, period.backtest_end),),
    )


def load_seed_sets(
    source: Any,
    *,
    parse: Callable[[Path], Any],
    limit: int,
    require_final_tick_6m: bool = False,
    allowed_set_names: Collection[str] | None = None,
    include_row: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[list[RobustStrategySet], list[str], int]:
    rows = accepted_seed_rows(source, require_final_tick_6m=require_final_tick_6m)
    if allowed_set_names is not None:
        allowed = {str(name).casefold() for name in allowed_set_names}
        rows = [row for row in rows if Path(str(row.get("seed_path") or "")).name.casefold() in allowed]
    total = len(rows)
    if include_row is not None:
        rows = [row for row in rows if include_row(row)]
    if limit > 0:
        rows = rows[:limit]
    loaded: list[RobustStrategySet] = []
    warnings: list[str] = []
    for row in rows:
        try:
            loaded.append(_seed_strategy(row, source, parse))
        except (OSError, ValueError) as exc:
            warnings.append(f"{source.broker} seed {row.get('id')}: {exc}")
    return loaded, warnings, total
