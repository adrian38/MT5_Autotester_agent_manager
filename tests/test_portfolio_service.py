import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_service import (
    PortfolioCoordinator,
    PortfolioSource,
    _optimize_without_recent_fillers,
    _underrepresented_recent_allocation_ids,
    generate_proposals,
    normalize_settings,
    save_portfolio_payload,
)
from portfolio_manager.ubs_portfolio import PortfolioAvailability, PortfolioResult, PortfolioType, StrategyAllocation


class PortfolioServiceTests(unittest.TestCase):
    @staticmethod
    def _recent_result(allocations: list[StrategyAllocation]) -> PortfolioResult:
        return PortfolioResult(
            allocations=allocations,
            equity_curve_2020_2026=[0.0, 1.0],
            total_net_profit=1.0,
            actual_valley_dd=1.0,
            actual_point_dd=1.0,
            target_valley_dd=100.0,
            target_point_dd=100.0,
            valley_usage_pct=1.0,
            point_usage_pct=1.0,
            total_lot=sum(item.lot for item in allocations),
            total_units=sum(item.units for item in allocations),
            active_strategies=len(allocations),
            stop_reason="ok",
            warnings=[],
            decision_log=[],
        )

    def test_recent_contribution_rule_reoptimizes_without_filler(self) -> None:
        core = SimpleNamespace(set_id="core.set")
        filler = SimpleNamespace(set_id="filler.set")
        seen_pools: list[list[str]] = []

        def optimize(pool: list[SimpleNamespace]) -> PortfolioResult:
            seen_pools.append([item.set_id for item in pool])
            allocations = [StrategyAllocation(
                "core.set", "1", "EURUSD", 2, .02, 200, 20, 10,
                recent_net_profit_001=100, has_recent_performance=True,
            )]
            if any(item.set_id == "filler.set" for item in pool):
                allocations.append(StrategyAllocation(
                    "filler.set", "2", "USDJPY", 1, .01, 10, 2, 1,
                    recent_net_profit_001=4, has_recent_performance=True,
                ))
            return self._recent_result(allocations)

        result, removed = _optimize_without_recent_fillers([core, filler], 5.0, optimize)

        self.assertEqual(removed, {"filler.set"})
        self.assertEqual(seen_pools, [["core.set", "filler.set"], ["core.set"]])
        self.assertEqual([item.set_id for item in result.allocations], ["core.set"])
        self.assertIn("Regla antirrelleno 6M", result.warnings[0])

    def test_recent_contribution_is_measured_after_final_lot(self) -> None:
        result = self._recent_result([
            StrategyAllocation(
                "large.set", "1", "EURUSD", 10, .10, 1000, 100, 50,
                recent_net_profit_001=10, has_recent_performance=True,
            ),
            StrategyAllocation(
                "small.set", "2", "USDJPY", 1, .01, 100, 10, 5,
                recent_net_profit_001=4, has_recent_performance=True,
            ),
        ])

        self.assertEqual(_underrepresented_recent_allocation_ids(result, 5.0), {"small.set"})

    def test_recent_contribution_default_is_five_percent(self) -> None:
        settings = normalize_settings("full_history", {"allowed_asset_groups": ["Forex"]})
        self.assertEqual(settings["min_strategy_recent_contribution_pct"], 5.0)

    def test_excluding_a_bundle_member_quarantines_it_and_deletes_the_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })
            set_path = str(project / "strategy.set")
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                    ("2026-07-15", "A/M/C", "bundle", "bundle", "full_history", json.dumps({"portfolio_bundle": True})),
                ).lastrowid)
                conn.execute(
                    """insert into portfolio_allocations(
                       portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
                       net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe
                       ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, "conservative", "Conservador", set_path, "ICTRADING/STANDARD:7",
                     "EURUSD", 1, .01, 100, 20, 10, set_path, "H1"),
                )
                conn.commit()
            candidate = {
                "set_path": set_path,
                "source_memory_path": str(memory),
                "account_type": "ICTRADING/STANDARD",
                "source_candidate_id": 7,
                "target_symbol": "EURUSD",
                "period": "H1",
            }

            with patch.object(source, "candidate_rows", return_value=[candidate]), patch.object(
                source, "_recalculate_saved", side_effect=AssertionError("no debe recalcular")
            ):
                quarantine_id = source.remove_member_to_quarantine(
                    {"portfolio_id": portfolio_id, "set_path": set_path}, "full_history"
                )

            self.assertGreater(quarantine_id, 0)
            with source.connect() as conn:
                self.assertIsNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())
                quarantine = conn.execute(
                    "select set_path,source_portfolio_id from portfolio_quarantine where id=?", (quarantine_id,)
                ).fetchone()
            self.assertEqual(quarantine["set_path"], set_path)
            self.assertEqual(quarantine["source_portfolio_id"], portfolio_id)

    def test_save_selected_bundle_commits_and_is_readable_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            base_inputs = normalize_settings(
                "full_history", {"capital": 5000, "valley_dd_pct": 6}, "ICTRADING"
            )

            def proposal(key: str, label: str, units: int) -> dict[str, object]:
                inputs = {
                    **base_inputs,
                    "portfolio_type": key,
                    "composition_portfolio_type": "balanced",
                }
                allocation = StrategyAllocation(
                    "same.set", "ICTRADING/STANDARD:1", "EURUSD", units, units * 0.01,
                    100 * units, 20 * units, 10 * units, "H1", "same.set", "is.html", "oos.html", 0.01,
                )
                result = PortfolioResult(
                    [allocation], [0, 100 * units], 100 * units, 20 * units, 10 * units,
                    300, 300, 10, 5, units * 0.01, units, 1, "ok", [], [],
                )
                return {"key": key, "label": label, "reserve_pct": 10, "inputs": inputs, "result": result}

            key = coordinator._key("ic", "full_history")
            coordinator.proposals[key] = [
                proposal("aggressive", "Agresivo", 3),
                proposal("balanced", "Moderado", 2),
                proposal("conservative", "Conservador", 1),
            ]
            coordinator.jobs[key] = {"status": "completed", "operation": "generate"}

            payload = coordinator.prepare_save("ic", "full_history", "balanced")
            confirmation = save_portfolio_payload(PortfolioSource(node), payload)
            portfolio_id = int(confirmation["portfolio_id"])
            retry = save_portfolio_payload(PortfolioSource(node), payload)
            self.assertEqual(retry["portfolio_id"], portfolio_id)
            self.assertTrue(retry["deduplicated"])
            coordinator.confirm_save(
                "ic", "full_history", str(confirmation["request_id"]), portfolio_id
            )
            saved = PortfolioSource(node).saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]

            self.assertEqual(saved["id"], portfolio_id)
            self.assertEqual(saved["capital"], 5000)
            self.assertEqual(saved["portfolio_type"], "bundle")
            self.assertEqual(len(saved["members"]), 3)
            self.assertEqual({row["variant_key"] for row in saved["members"]}, {
                "aggressive", "balanced", "conservative",
            })
            self.assertNotIn(key, coordinator.proposals)
            self.assertEqual(coordinator.jobs[key]["last_saved_id"], portfolio_id)

    def test_portfolio_form_settings_survive_manager_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "portfolio_settings.json"
            nodes = [{"id": "test-node", "portfolio_broker": "ICTRADING"}]
            coordinator = PortfolioCoordinator(nodes, settings_path)

            saved = coordinator.update_settings(
                "test-node",
                "full_history",
                {"capital": 5000, "exclude_used_sets": False},
            )
            reloaded = PortfolioCoordinator(nodes, settings_path).settings_for(
                "test-node", "full_history"
            )

            self.assertEqual(saved["capital"], 5000)
            self.assertFalse(saved["exclude_used_sets"])
            self.assertEqual(reloaded["capital"], 5000)
            self.assertFalse(reloaded["exclude_used_sets"])

    def test_normalize_monthly_settings_keeps_month_specific_controls(self) -> None:
        settings = normalize_settings(
            "monthly",
            {
                "target_month": 7,
                "max_daily_dd": 125,
                "strict_yearly_month_validation": True,
                "allowed_asset_groups": ["Forex", "Metals"],
            },
            "ICTRADING",
        )
        self.assertEqual(settings["portfolio_scope"], "monthly")
        self.assertEqual(settings["target_month"], 7)
        self.assertEqual(settings["max_daily_dd"], 125)
        self.assertTrue(settings["strict_yearly_month_validation"])
        self.assertFalse(settings["enforce_point_dd"])

    def test_portfolio_source_reads_only_full_pipeline_accepted_candidates(self) -> None:
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
                    create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text);
                    insert into candidates values(1,'sets/a.set','EURUSD','EURUSD','H1','f','reports/a.html','accepted');
                    insert into candidates values(2,'sets/b.set','GBPUSD','GBPUSD','H1','f','reports/b.html','accepted');
                    insert into candidate_robustness values(1,'reports/a_oos.html','accepted');
                    insert into candidate_robustness values(2,'reports/b_oos.html','rejected');
                    insert into candidate_final_tick_6m values(1,'','','2026.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick_6m values(2,'','','2026.01.01','2026.06.30','accepted');
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
            self.assertEqual([row["source_candidate_id"] for row in rows], [1])
            self.assertEqual(rows[0]["candidate_id"], "ICTRADING/STANDARD:1")
            settings = normalize_settings("full_history", {"allowed_asset_groups": ["Forex"]}, "ICTRADING")
            self.assertEqual(source.inventory("full_history", settings)["available"], 1)
            quarantine_id = source.exclude_strategy({"set_path": rows[0]["set_path"]})
            excluded = source.inventory("full_history", settings)
            monthly = source.inventory("monthly", normalize_settings("monthly", {"allowed_asset_groups": ["Forex"]}, "ICTRADING"))
            self.assertEqual(excluded["by_symbol"], [{"symbol": "EURUSD", "total": 1, "quarantined": 1, "used": 0, "available": 0}])
            self.assertEqual(monthly["available"], 1)
            self.assertFalse(monthly["quarantine_excludes"])
            source.release_strategy(quarantine_id)
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

    def test_schema_versions_undo_delete_and_export_are_managed_centrally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            set_file = project / "sample.set"
            set_file.write_text("Risk=1\n", encoding="utf-8")
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({"portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"})
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    """insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,capital,account_capital,
                       target_valley_dd,target_point_dd,total_net_profit,total_lot,total_units,active_strategies,target_strategies,metrics_json)
                       values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("2026-07-13", "Original", "balanced", "balanced", "full_history", 10000, 10000, 1000, 1000, 100, .01, 1, 1, 1, "{}"),
                ).lastrowid)
                conn.execute(
                    """insert into portfolio_allocations(portfolio_id,set_id,candidate_id,symbol,units,lot,
                       net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe,lot_size_step)
                       values(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, str(set_file), "ICTRADING/STANDARD:1", "EURUSD", 1, .01, 100, 20, 10, str(set_file), "H1", .01),
                )
                conn.commit()
                source._save_version(conn, portfolio_id, "before test")
                conn.execute("update portfolios set name='Changed' where id=?", (portfolio_id,))
                conn.commit()
            self.assertEqual(source.undo_latest(portfolio_id, "full_history"), 1)
            self.assertEqual(source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]["name"], "Original")
            exported = source.export_portfolio(portfolio_id, "full_history", str(project / "exported"))
            self.assertEqual(exported["exported"], 1)
            self.assertTrue(Path(exported["summary"]).is_file())
            self.assertEqual((Path(exported["folder"]) / set_file.name).read_text(encoding="utf-8"), "Risk=1\n")
            source.delete_portfolio(portfolio_id, "full_history")
            self.assertEqual(source.saved_portfolios("full_history")["summary"]["total"], 0)

    def test_axi_candidate_pool_combines_standard_and_premium_memories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            for index, account in enumerate(("STANDARD", "PREMIUM"), 1):
                memory = project / "outputs" / f"ubs_memory_AXI_{account}.sqlite"
                with contextlib.closing(sqlite3.connect(memory)) as conn:
                    conn.executescript(
                        """
                        create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,period text,family text,report_path text,status text);
                        create table candidate_robustness(candidate_id integer,report_path text,status text);
                        create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text);
                        """
                    )
                    conn.execute("insert into candidates values(1,?,?,?,?,?,?,?)", (f"sets/{account}.set", "EURUSD", "EURUSD", "H1", "f", "report.html", "accepted"))
                    conn.execute("insert into candidate_robustness values(1,'oos.html','accepted')")
                    conn.execute("insert into candidate_final_tick_6m values(1,'','','','','accepted')")
                    conn.commit()
            source = PortfolioSource({"portfolio_project_dir": str(project), "portfolio_broker": "AXI", "portfolio_account_type": "STANDARD"})
            rows = source.candidate_rows(include_quarantined=False)
            self.assertEqual({row["candidate_id"] for row in rows}, {"AXI/STANDARD:1", "AXI/PREMIUM:1"})

    def test_monthly_strict_validation_retries_when_first_proposals_fail_post_validation(self) -> None:
        strategy = SimpleNamespace(set_id="a.set", symbol="EURUSD")
        allocation = SimpleNamespace(set_id="a.set", units=1)
        first = SimpleNamespace(allocations=[allocation], target_valley_dd=100, target_point_dd=100,
                                seasonal_validation={}, warnings=[])
        second = SimpleNamespace(allocations=[allocation], target_valley_dd=100, target_point_dd=100,
                                 seasonal_validation={}, warnings=[])

        class Source:
            universe = Path("assets.ini")
            def candidate_rows(self, **_kwargs): return [{"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"}]
            def used_set_paths(self, *_args, **_kwargs): return []
            def saved_curves(self, **_kwargs): return []

        inputs = normalize_settings("monthly", {
            "target_month": 1, "strict_yearly_month_validation": True,
            "allowed_asset_groups": ["Forex"], "deep_optimization": False,
        }, "ICTRADING")
        proposal_one = [{"key": "profit", "label": "Primera", "reserve_pct": 10, "inputs": inputs, "result": first}]
        proposal_two = [{"key": "profit", "label": "Segunda", "reserve_pct": 10, "inputs": inputs, "result": second}]
        failed = {"passed": False, "reasons": ["primer pool no válido"]}
        passed = {"passed": True, "reasons": []}
        with patch("mt5_manager.portfolio_service.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_service.slice_strategy_sets_to_month", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_service.summarize_robust_rows", return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_service._normal_proposals", side_effect=[proposal_one, proposal_two]) as optimizer, \
             patch("mt5_manager.portfolio_service._strict_monthly_candidate_pool", return_value=([strategy], ["retry"])), \
             patch("mt5_manager.portfolio_service.validate_strict_monthly_portfolio", side_effect=[failed, passed]):
            _availability, proposals = generate_proposals(Source(), inputs)
        self.assertEqual(optimizer.call_count, 2)
        self.assertIs(proposals[0]["result"], second)
        self.assertTrue(second.seasonal_validation["passed"])

    def test_apply_reoptimization_replaces_saved_rows_and_keeps_undo_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            node = {
                "id": "ic", "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD",
            }
            source = PortfolioSource(node)
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    """insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,target_month,capital,account_capital,
                       target_valley_dd_pct,target_point_dd_pct,target_valley_dd,target_point_dd,total_net_profit,total_lot,
                       total_units,active_strategies,target_strategies,metrics_json) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("2026-07-13", "Before", "balanced", "balanced", "monthly", 1, 10000, 10000, 10, 10, 1000, 1000, 100, .01, 1, 1, 1, "{}"),
                ).lastrowid)
                conn.commit()
            allocation = StrategyAllocation("new.set", "ICTRADING/STANDARD:2", "EURUSD", 2, .02, 250, 40, 20, "H1", "new.set", "is.html", "oos.html", .01)
            result = PortfolioResult([allocation], [0, 250], 250, 40, 20, 900, 900, 4.44, 2.22, .02, 2, 1, "ok", [], [])
            inputs = normalize_settings("monthly", {"target_month": 1, "allowed_asset_groups": ["Forex"]}, "ICTRADING")
            proposal = {"key": "profit", "label": "Máximo beneficio", "reserve_pct": 10, "inputs": inputs, "result": result}
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            state_key = coordinator._key("ic", "monthly")
            coordinator.proposals[state_key] = [proposal]
            coordinator.jobs[state_key] = {
                "status": "completed", "operation": "reoptimize", "portfolio_id": portfolio_id,
            }
            payload = coordinator.prepare_save("ic", "monthly", "profit")
            confirmation = save_portfolio_payload(source, payload)
            coordinator.confirm_save(
                "ic", "monthly", str(confirmation["request_id"]), int(confirmation["portfolio_id"])
            )
            updated = source.saved_portfolio_detail(portfolio_id, "monthly")["portfolio"]
            self.assertEqual(updated["total_net_profit"], 250)
            self.assertEqual(updated["members"][0]["candidate_id"], "ICTRADING/STANDARD:2")
            self.assertEqual(len(updated["versions"]), 1)
            source.undo_latest(portfolio_id, "monthly")
            restored = source.saved_portfolio_detail(portfolio_id, "monthly")["portfolio"]
            self.assertEqual(restored["name"], "Before")
            self.assertEqual(restored["total_net_profit"], 100)

    def test_proposal_state_compares_each_bundle_variant_and_new_generation_from_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {"id": "ic", "portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"}
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            settings = normalize_settings("full_history", {"capital": 10000, "valley_dd_pct": 10}, "ICTRADING")

            def result(units: int) -> PortfolioResult:
                allocation = StrategyAllocation("same.set", "ICTRADING/STANDARD:1", "EURUSD", units, units * .01, 100, 20, 10, "H1", "same.set", "is.html", "oos.html", .01)
                return PortfolioResult([allocation], [0, 100], 100, 20, 10, 900, 900, 2.22, 1.11, units * .01, units, 1, "ok", [], [])

            key = coordinator._key("ic", "full_history")
            coordinator.proposals[key] = [
                {"key": "aggressive", "label": "Agresivo", "reserve_pct": 10, "inputs": settings, "result": result(2)},
                {"key": "balanced", "label": "Moderado", "reserve_pct": 15, "inputs": settings, "result": result(5)},
            ]
            coordinator.jobs[key] = {"status": "completed", "previous_members": [
                {"variant_key": "aggressive", "set_path": "same.set", "units": 1, "lot": .01, "symbol": "EURUSD"},
                {"variant_key": "balanced", "set_path": "same.set", "units": 3, "lot": .03, "symbol": "EURUSD"},
            ]}
            with patch.object(PortfolioSource, "inventory", return_value={}):
                state = coordinator.state("ic", "full_history")
            self.assertEqual(state["proposals"][0]["diff"][0]["old_units"], 1)
            self.assertEqual(state["proposals"][1]["diff"][0]["old_units"], 3)
            self.assertEqual(state["proposals"][1]["result"]["changed_allocations"], 1)
            self.assertEqual(state["proposals"][1]["result"]["nominal_valley_margin"], 980)

            coordinator.jobs[key] = {"status": "completed", "previous_members": []}
            with patch.object(PortfolioSource, "inventory", return_value={}):
                generated = coordinator.state("ic", "full_history")
            self.assertEqual(generated["proposals"][0]["diff"][0]["state"], "NUEVA")


if __name__ == "__main__":
    unittest.main()
