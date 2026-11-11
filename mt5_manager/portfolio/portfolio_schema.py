"""El esquema sqlite del portafolio y los sondeos que lo mantienen.

Esta abajo del todo en la pila de `portfolio_service`: no importa nada del
paquete, solo sqlite3. Los llamantes lo siguen viendo reexportado desde
`portfolio_service`.
"""
from __future__ import annotations

import sqlite3


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if not _has_column(conn, table, column):
        conn.execute(f"alter table {table} add column {column} {definition}")


_PORTFOLIOS_COLUMNS = (
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

_ALLOCATIONS_COLUMNS = (
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
)

_MEMBERS_COLUMNS = (
    ("variant_key", "text not null default ''"),
    ("variant_label", "text not null default ''"),
)

PORTFOLIO_SCHEMA: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "portfolios",
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
        """,
        _PORTFOLIOS_COLUMNS,
    ),
    (
        "portfolio_allocations",
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
        """,
        _ALLOCATIONS_COLUMNS,
    ),
    (
        "portfolio_decision_log",
        """
        create table if not exists portfolio_decision_log (
            id integer primary key autoincrement, portfolio_id integer not null,
            step integer not null, action text not null, set_id text, from_set_id text, to_set_id text,
            gain real not null, valley_cost real not null, point_cost real not null, score real not null,
            portfolio_net_profit_after real not null, portfolio_valley_dd_after real not null,
            portfolio_point_dd_after real not null, reason text not null
        )
        """,
        (),
    ),
    (
        "portfolio_members",
        """
        create table if not exists portfolio_members (
            id integer primary key autoincrement, portfolio_id integer not null,
            variant_key text not null default '', variant_label text not null default '',
            candidate_id integer, set_path text not null, symbol text, period text,
            lot_multiplier real, lot real, lot_size_step real, standalone_dd real,
            quality_score real, combined_net_profit real, is_report_path text, oos_report_path text
        )
        """,
        _MEMBERS_COLUMNS,
    ),
    (
        "portfolio_quarantine",
        """
        create table if not exists portfolio_quarantine (
            id integer primary key autoincrement, account_type text not null, candidate_id integer,
            set_path text not null unique, symbol text, timeframe text, reason text not null default '',
            source_portfolio_id integer, quarantined_at text not null
        )
        """,
        (),
    ),
    (
        "portfolio_versions",
        """
        create table if not exists portfolio_versions (
            id integer primary key autoincrement, portfolio_id integer not null,
            version_no integer not null, created_at text not null, reason text not null,
            snapshot_json blob not null, unique(portfolio_id, version_no)
        )
        """,
        (),
    ),
)
"""El esquema completo como dato: ``(tabla, create, columnas anadidas despues)``.

Las columnas repiten lo que ya dice el ``create`` a proposito: el ``create`` solo
corre en una memoria nueva, y la lista es la que migra las que ya existen.
"""


def ensure_portfolio_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate the same persistence surface used by the desktop portfolio UI."""
    for table, create_sql, added_columns in PORTFOLIO_SCHEMA:
        conn.execute(create_sql)
        for column, definition in added_columns:
            _ensure_column(conn, table, column, definition)
    conn.commit()
