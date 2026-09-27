import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_service import (
    STANDARD_ANTIFILLER_REFILL_PASSES,
    PortfolioCoordinator,
    PortfolioSource,
    _linux_path_needs_snapshot,
    _insert_decisions,
    _optimizer_kwargs,
    _optimize_without_recent_fillers,
    _resolve_source_path,
    _underrepresented_recent_allocation_ids,
    _adjusted_valley_pcts,
    _locked_full_proposals,
    describe_eligibility,
    eligibility_counts,
    generate_proposals,
    ensure_portfolio_schema,
    normalize_settings,
    save_portfolio_payload,
    scope_stage_count,
)
from mt5_manager.portfolio_monthly_service import (
    _monthly_proposals,
    generate_monthly_proposals,
    monthly_eligibility_counts,
)
from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    PortfolioAvailability,
    PortfolioCalculationCancelled,
    PortfolioResult,
    PortfolioType,
    StrategyAllocation,
    filter_eligible_sets,
    filter_rows_grid_off,
    evaluate_portfolio,
    load_robust_sets_from_rows,
    set_portfolio_cancellation_check,
)
from tests.helpers import ASYNC_TIMEOUT, assert_event, assert_until


class PortfolioInventoryTests(unittest.TestCase):
    @staticmethod
    def _create_axi_inventory_source(project: Path) -> PortfolioSource:
        (project / "outputs").mkdir()
        (project / "assets").mkdir()
        (project / "assets" / "axi_assets.ini").write_text(
            "[Crypto]\nsymbols=BTCUSD.sa,ETHUSD.sa\n"
            "[Indices]\nsymbols=NAS100.fs,USTECH.sa\n",
            encoding="utf-8",
        )
        memory = project / "outputs" / "ubs_memory_AXI_STANDARD.sqlite"
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            conn.executescript(
                """
                    create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,period text,family text,report_path text,status text);
                    create table candidate_robustness(candidate_id integer,report_path text,status text);
                    create table candidate_final_tick(candidate_id integer,real_tick_report_path text,from_date text,to_date text,status text);
                    create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text,real_tick_metrics_json text);
                    insert into candidates values(1,'sets/legacy.set','BTCUSD','ETHUSD','H1','f','reports/legacy.html','accepted');
                    insert into candidates values(2,'sets/current.set','BTCUSD.sa','ETHUSD.sa','H1','f','reports/current.html','accepted');
                    insert into candidates values(3,'sets/ustec.set','USTEC','USTEC','H1','f','reports/ustec.html','accepted');
                    insert into candidates values(4,'sets/nas100.set','NAS100.fs','NAS100.fs','H1','f','reports/nas100.html','accepted');
                    insert into candidates values(5,'sets/ustech.set','USTECH.sa','USTECH.sa','H1','f','reports/ustech.html','accepted');
                    insert into candidate_robustness values(1,'reports/legacy_oos.html','accepted');
                    insert into candidate_robustness values(2,'reports/current_oos.html','accepted');
                    insert into candidate_robustness values(3,'reports/ustec_oos.html','accepted');
                    insert into candidate_robustness values(4,'reports/nas100_oos.html','accepted');
                    insert into candidate_robustness values(5,'reports/ustech_oos.html','accepted');
                    insert into candidate_final_tick values(1,'reports/legacy_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick values(2,'reports/current_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick values(3,'reports/ustec_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick values(4,'reports/nas100_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick values(5,'reports/ustech_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick_6m values(1,'','','2026.01.01','2026.06.30','accepted','{"symbol":"ETHUSD.sa"}');
                    insert into candidate_final_tick_6m values(2,'','','2026.01.01','2026.06.30','accepted','{"symbol":"ETHUSD.sa"}');
                    insert into candidate_final_tick_6m values(3,'','','2026.01.01','2026.06.30','accepted','{"symbol":"USTECH.sa"}');
                    insert into candidate_final_tick_6m values(4,'','','2026.01.01','2026.06.30','accepted','{"symbol":"NAS100.fs"}');
                    insert into candidate_final_tick_6m values(5,'','','2026.01.01','2026.06.30','accepted','{"symbol":"USTECH.sa"}');
                """
            )
            conn.commit()
        return PortfolioSource({
            "portfolio_project_dir": str(project),
            "portfolio_broker": "AXI",
            "portfolio_account_type": "STANDARD",
        })

    @staticmethod
    def _expected_axi_inventory() -> list[dict[str, object]]:
        return [
            {
                "symbol": "ETHUSD.sa", "total": 2, "quarantined": 0,
                "used": 0, "available": 2, "disabled": False,
            },
            {
                "symbol": "NAS100.fs", "total": 1, "quarantined": 0,
                "used": 0, "available": 1, "disabled": False,
            },
            {
                "symbol": "USTECH.sa", "total": 2, "quarantined": 0,
                "used": 0, "available": 2, "disabled": False,
            },
        ]

    def test_axi_inventory_groups_legacy_symbols_under_the_executable_broker_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._create_axi_inventory_source(Path(temp_dir))
            expected = self._expected_axi_inventory()
            groups = {"allowed_asset_groups": ["Crypto", "Indices"]}
            full_history = source.inventory(
                "full_history", normalize_settings("full_history", groups, "AXI")
            )
            monthly = source.inventory(
                "monthly", normalize_settings("monthly", groups, "AXI")
            )

        self.assertEqual(full_history["by_symbol"], expected)
        self.assertEqual(full_history["symbols"], 3)
        self.assertEqual(
            monthly["by_symbol"],
            [{key: value for key, value in row.items() if key != "disabled"} for row in expected],
        )
        self.assertEqual(monthly["symbols"], 3)

    def test_portfolio_source_accepts_pending_ohlc_trades_on_the_short_final_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.executescript(
                    """
                    create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,period text,family text,report_path text,status text);
                    create table candidate_robustness(candidate_id integer,report_path text,status text);
                    create table candidate_final_tick(candidate_id integer,real_tick_report_path text,from_date text,to_date text,status text);
                    create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text);
                    insert into candidates values(1,'sets/a.set','EURUSD','EURUSD','H1','f','reports/a.html','accepted');
                    insert into candidates values(2,'sets/b.set','GBPUSD','GBPUSD','H1','f','reports/b.html','accepted');
                    insert into candidates values(3,'sets/c.set','USDJPY','USDJPY','H1','f','reports/c.html','accepted');
                    insert into candidate_robustness values(1,'reports/a_oos.html','accepted');
                    insert into candidate_robustness values(2,'reports/b_oos.html','accepted');
                    insert into candidate_robustness values(3,'reports/c_oos.html','accepted');
                    insert into candidate_final_tick values(1,'','2026.05.01','2026.05.31','pending_ohlc_trades');
                    insert into candidate_final_tick values(2,'','2026.05.01','2026.05.31','rejected');
                    insert into candidate_final_tick values(3,'','2026.05.01','2026.05.31','pending_ohlc_trades');
                    insert into candidate_final_tick_6m values(1,'','','2026.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick_6m values(2,'','','2026.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick_6m values(3,'','','2026.01.01','2026.06.30','rejected');
                    """
                )
                conn.commit()
            source = PortfolioSource(
                {
                    "portfolio_project_dir": str(project),
                    "portfolio_broker": "ICTRADING",
                    "portfolio_account_type": "STANDARD",
                }
            )
            rows = source.candidate_rows(include_quarantined=False)
            # 1 pasa: 6M accepted aunque el tick corto quedara en pending_ohlc_trades.
            # 2 no (tick corto rechazado), 3 tampoco (6M rechazado).
            self.assertEqual([row["source_candidate_id"] for row in rows], [1])
            # Sin reporte de tick corto la fila entra sin tramo continuo 2020-hoy.
            self.assertEqual(rows[0]["full_history_report_path"], "")
            settings = normalize_settings("full_history", {"allowed_asset_groups": ["Forex"]}, "ICTRADING")
            self.assertEqual(source.inventory("full_history", settings)["available"], 1)

    def test_saved_portfolios_are_read_directly_from_the_broker_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.executescript(
                    """
                    create table portfolios(
                        id integer primary key,created_at text,name text,portfolio_type text,type text,
                        portfolio_scope text,target_month integer,capital real,total_net_profit real,
                        actual_valley_dd real,target_valley_dd real,valley_usage_pct real,
                        actual_point_dd real,target_point_dd real,point_usage_pct real,total_lot real,
                        total_units integer,active_strategies integer,target_strategies integer,
                        stop_reason text,binding_constraint text,metrics_json text
                    );
                    create table portfolio_allocations(
                        portfolio_id integer,variant_key text,variant_label text,set_id text,candidate_id text,
                        symbol text,timeframe text,units integer,lot real,lot_size_step real,
                        net_profit_contribution real,standalone_valley_dd real,standalone_point_dd real,
                        set_path text,margin_required real,margin_pct real
                    );
                    """
                )
                conn.execute(
                    "insert into portfolios values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (7, "2026-07-13", "Enero", "balanced", "balanced", "monthly", 1, 10000, 450,
                     80, 1000, 8, 40, 1000, 4, .02, 2, 1, 1, "ok", "", json.dumps({"inputs": {"target_month": 1}})),
                )
                conn.execute(
                    "insert into portfolio_allocations values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (7, "", "", "sets/a.set", "STANDARD:1", "EURUSD", "H1", 2, .02, .01, 450, 80, 40, "sets/a.set", 10, .1),
                )
                conn.commit()
            source = PortfolioSource({"id": "ic", "name": "IC", "portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"})
            listing = source.saved_portfolios("monthly")
            detail = source.saved_portfolio_detail(7, "monthly")
            self.assertEqual(listing["summary"]["total"], 1)
            self.assertEqual(detail["portfolio"]["members"][0]["symbol"], "EURUSD")

    def test_legacy_saved_inputs_use_nominal_percentages_and_desktop_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({"portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"})
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    """insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,target_month,
                       capital,account_capital,target_valley_dd_pct,target_point_dd_pct,target_valley_dd,
                       target_point_dd,metrics_json) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("2026-07-13", "Legacy", "balanced", "balanced", "monthly", 7,
                     10000, 10000, 7.5, 6.0, 600, 500, "{}"),
                ).lastrowid)
                conn.commit()
            settings = source.saved_inputs(portfolio_id, "monthly")
            self.assertEqual(settings["valley_dd_pct"], 7.5)
            self.assertEqual(settings["target_month"], 7)
            self.assertEqual(settings["dd_reserve_pct"], 0.0)
            self.assertEqual(settings["search_restarts"], 0)
            self.assertFalse(settings["deep_optimization"])
            self.assertEqual(settings["account_leverage"], 1000.0)
            self.assertEqual(set(settings["allowed_asset_groups"]), {
                "Forex", "Metals", "Indices", "Energies", "Crypto", "Stocks", "Bonds", "Softs",
            })

    def test_bundle_saved_inputs_keep_composition_base_instead_of_selected_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({"portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"})
            metrics = {"composition_portfolio_type": "balanced", "inputs": {
                "portfolio_type": "conservative", "composition_portfolio_type": "balanced",
                "capital": 5000, "valley_dd_pct": 6, "allowed_asset_groups": list({
                    "Forex", "Metals", "Indices", "Energies", "Crypto", "Stocks", "Bonds", "Softs",
                }),
            }}
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    """insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,capital,account_capital,
                       target_valley_dd_pct,target_point_dd_pct,target_valley_dd,target_point_dd,metrics_json)
                       values(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("2026-07-13", "A/M/C", "bundle", "bundle", "full_history", 5000, 5000,
                     6, 6, 225, 225, json.dumps(metrics)),
                ).lastrowid)
                conn.commit()
            settings = source.saved_inputs(portfolio_id, "full_history")
            self.assertEqual(settings["portfolio_type"], "balanced")

    def test_full_history_used_locks_keep_aggressive_separate_for_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({"portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"})
            with source.connect(write=True) as conn:
                rows = (
                    ("aggressive", "full_history", "aggressive.set"),
                    ("balanced", "full_history", "balanced.set"),
                    ("balanced", "monthly", "monthly.set"),
                )
                for index, (kind, scope, set_name) in enumerate(rows, 1):
                    portfolio_id = int(conn.execute(
                        "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                        ("2026-07-13", kind, kind, kind, scope, "{}"),
                    ).lastrowid)
                    conn.execute(
                        """insert into portfolio_allocations(
                           portfolio_id,set_id,candidate_id,symbol,set_path,units,lot,
                           net_profit_contribution,standalone_valley_dd,standalone_point_dd
                           ) values(?,?,?,?,?,?,?,?,?,?)""",
                        (portfolio_id, set_name, f"candidate:{index}", "EURUSD", set_name, 1, .01, 1, 1, 1),
                    )
                conn.commit()
            aggressive = {Path(path).name for path in source.used_set_paths("full_history", portfolio_type=PortfolioType.AGGRESSIVE)}
            balanced = {Path(path).name for path in source.used_set_paths("full_history", portfolio_type=PortfolioType.BALANCED)}
            all_profiles = {Path(path).name for path in source.used_set_paths("full_history")}
            self.assertEqual(aggressive, {"aggressive.set"})
            self.assertEqual(balanced, {"balanced.set"})
            self.assertEqual(all_profiles, {"aggressive.set", "balanced.set"})
