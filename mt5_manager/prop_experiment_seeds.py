"""Carga de seeds aceptadas sin cambiar el embudo UBS de candidatos."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import RobustStrategySet
from portfolio_manager.ubs_portfolio.reports import period_report_from_strategy_report

from .portfolio_identity import _resolve_source_path


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,),
    ).fetchone()
    return row is not None


def accepted_seed_rows(source: Any) -> list[dict[str, Any]]:
    """Seeds activas que superaron su backtest propio en el agente."""
    with source.connect(write=False) as connection:
        if not _table_exists(connection, "seed_scores"):
            return []
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            select id, seed_path, symbol, period, family, report_path, score,
                   metrics_json, status, active
              from seed_scores
             where active=1 and accepted=1 and status='accepted'
             order by score desc, id desc
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _seed_strategy(row: dict[str, Any], source: Any, parse: Callable[[Path], Any]) -> RobustStrategySet:
    report_path = _resolve_source_path(row.get("report_path"), source.project)
    set_path = _resolve_source_path(row.get("seed_path"), source.project)
    if not report_path or not Path(report_path).is_file():
        raise ValueError(f"informe seed ausente: {row.get('report_path')}")
    report = parse(Path(report_path))
    period = period_report_from_strategy_report(report, "seed")
    set_id = f"seed-{row.get('id')}-{Path(set_path).stem}"
    return RobustStrategySet(
        set_id=set_id,
        candidate_id=set_id,
        symbol=report.symbol or str(row.get("symbol") or ""),
        timeframe=report.timeframe or str(row.get("period") or ""),
        strategy_family=str(row.get("family") or "seed"),
        robustness_status="seed_accepted",
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
        full_history_report_path=report_path,
        curve_points_2020_2026_001=list(period.pnl_points_001),
        max_balance_dd_001=period.balance_dd_metric_001,
        max_equity_dd_001=period.equity_dd_metric_001,
        closed_trades_2020_2026=list(period.closed_trades),
    )


def load_seed_sets(
    source: Any,
    *,
    parse: Callable[[Path], Any],
    limit: int,
) -> tuple[list[RobustStrategySet], list[str], int]:
    rows = accepted_seed_rows(source)
    total = len(rows)
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
