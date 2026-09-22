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


class PortfolioWorkflowTests(unittest.TestCase):
    def test_schema_versions_undo_delete_and_export_are_managed_centrally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            set_file = project / "sample.set"
            set_file.write_text("Risk=1\n", encoding="utf-8")
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            node = {"id": "ic", "portfolio_project_dir": str(project), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"}
            source = PortfolioSource(node)
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
            summary_text = Path(exported["summary"]).read_text(encoding="utf-8")
            self.assertIn("Miembros JSON:", summary_text)
            self.assertIn('"candidate_id":"ICTRADING/STANDARD:1"', summary_text)
            self.assertEqual((Path(exported["folder"]) / set_file.name).read_text(encoding="utf-8"), "Risk=1\n")
            archive = PortfolioCoordinator([node], project / "settings.json").export_archive(
                "ic", "full_history", portfolio_id
            )
            self.assertEqual(archive["exported"], 1)
            with zipfile.ZipFile(io.BytesIO(archive["content"])) as zipped:
                names = zipped.namelist()
                self.assertTrue(any(name.endswith("/sample.set") for name in names))
                self.assertTrue(any(name.endswith(f"/PORTAFOLIO_{portfolio_id}_resumen.txt") for name in names))
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
                        create table candidate_final_tick(candidate_id integer,real_tick_report_path text,from_date text,to_date text,status text);
                        create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text);
                        """
                    )
                    conn.execute("insert into candidates values(1,?,?,?,?,?,?,?)", (f"sets/{account}.set", "EURUSD", "EURUSD", "H1", "f", "report.html", "accepted"))
                    conn.execute("insert into candidate_robustness values(1,'oos.html','accepted')")
                    conn.execute("insert into candidate_final_tick values(1,'full.html','2020.01.01','2026.06.30','accepted')")
                    conn.execute("insert into candidate_final_tick_6m values(1,'','','2026.01.01','2026.06.30','accepted')")
                    conn.commit()
            source = PortfolioSource({"portfolio_project_dir": str(project), "portfolio_broker": "AXI", "portfolio_account_type": "STANDARD"})
            rows = source.candidate_rows(include_quarantined=False)
            self.assertEqual({row["candidate_id"] for row in rows}, {"AXI/STANDARD:1", "AXI/PREMIUM:1"})

    def test_monthly_strict_validation_retries_when_first_proposals_fail_post_validation(self) -> None:
        strategy = SimpleNamespace(
            set_id="a.set", symbol="EURUSD", robustness_status="accepted",
            already_used=False, curve_2020_2026_001=[0.0, 100.0],
            trades_2020_2026=20, net_profit_2020_2026_001=100.0,
            has_recent_performance=False, recent_net_profit_001=0.0,
            recent_equity_dd_001=0.0,
        )
        allocation = SimpleNamespace(set_id="a.set", units=1)
        first = SimpleNamespace(allocations=[allocation], target_valley_dd=100, target_point_dd=100,
                                seasonal_validation={}, warnings=[])
        second = SimpleNamespace(allocations=[allocation], target_valley_dd=100, target_point_dd=100,
                                 seasonal_validation={}, warnings=[])

        class Source:
            universe = Path("assets.ini")
            def candidate_rows(self, *, include_quarantined):
                if include_quarantined:
                    raise AssertionError("mensual no debe cargar estrategias en cuarentena")
                return [{"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"}]
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
        with patch("mt5_manager.portfolio_monthly_service.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_monthly_service.slice_strategy_sets_to_month", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_monthly_service.summarize_robust_rows", return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_monthly_service._monthly_proposals", side_effect=[proposal_one, proposal_two]) as optimizer, \
             patch("mt5_manager.portfolio_monthly_service._strict_monthly_candidate_pool", return_value=([strategy], ["retry"])), \
             patch("mt5_manager.portfolio_monthly_service.validate_strict_monthly_portfolio", side_effect=[failed, passed]):
            _availability, proposals = generate_monthly_proposals(Source(), inputs)
        self.assertEqual(optimizer.call_count, 2)
        self.assertIs(proposals[0]["result"], second)
        self.assertTrue(second.seasonal_validation["passed"])

    def test_monthly_eligibility_counts_explain_each_filter_stage(self) -> None:
        base = dict(
            robustness_status="accepted", already_used=False,
            curve_2020_2026_001=[0.0, 1.0], has_recent_performance=False,
            recent_net_profit_001=0.0, recent_equity_dd_001=0.0,
        )
        strategies = [
            SimpleNamespace(**base, trades_2020_2026=0, net_profit_2020_2026_001=0.0),
            SimpleNamespace(**base, trades_2020_2026=10, net_profit_2020_2026_001=50.0),
            SimpleNamespace(**base, trades_2020_2026=20, net_profit_2020_2026_001=-5.0),
            SimpleNamespace(**base, trades_2020_2026=20, net_profit_2020_2026_001=80.0),
        ]

        counts = monthly_eligibility_counts(strategies, 15)

        self.assertEqual(counts, {
            "total": 4, "accepted": 4, "not_used": 4, "with_curve": 4,
            "with_trades": 3, "enough_trades": 2,
            "positive": 1, "recent_recovery": 1, "eligible": 1,
        })

    def test_eligibility_funnel_matches_the_shared_filter_in_the_three_scopes(self) -> None:
        # El embudo solo sirve si su ultima etapa es exactamente lo que hace
        # `filter_eligible_sets`: si divergen, el error nombraria una etapa que
        # no es la que decide.
        base = dict(curve_2020_2026_001=[0.0, 1.0], has_recent_performance=False,
                    recent_net_profit_001=0.0, recent_equity_dd_001=0.0)
        strategies = [
            SimpleNamespace(**base, robustness_status="rejected", already_used=False,
                            trades_2020_2026=200, net_profit_2020_2026_001=90.0),
            SimpleNamespace(**base, robustness_status="accepted", already_used=True,
                            trades_2020_2026=200, net_profit_2020_2026_001=90.0),
            SimpleNamespace(**base, robustness_status="accepted", already_used=False,
                            trades_2020_2026=5, net_profit_2020_2026_001=90.0),
            SimpleNamespace(**base, robustness_status="accepted", already_used=False,
                            trades_2020_2026=200, net_profit_2020_2026_001=-1.0),
            SimpleNamespace(robustness_status="accepted", already_used=False,
                            curve_2020_2026_001=[0.0, 1.0], has_recent_performance=True,
                            recent_net_profit_001=1.0, recent_equity_dd_001=100.0,
                            trades_2020_2026=200, net_profit_2020_2026_001=90.0),
            SimpleNamespace(**base, robustness_status="accepted", already_used=False,
                            trades_2020_2026=200, net_profit_2020_2026_001=90.0),
        ]

        counts = eligibility_counts(strategies, 100)

        self.assertEqual(counts["eligible"], len(filter_eligible_sets(strategies, 100)))
        self.assertEqual(counts, {
            "total": 6, "accepted": 5, "not_used": 4, "with_curve": 4,
            "with_trades": 4, "enough_trades": 3, "positive": 2,
            "recent_recovery": 1, "eligible": 1,
        })

    def test_grid_eligibility_funnel_ignores_the_recent_recovery_rule(self) -> None:
        # Grid apaga `has_recent_performance` en el optimizador; contar esa
        # etapa daria un numero de elegibles que Grid no va a usar.
        strategy = SimpleNamespace(
            robustness_status="accepted", already_used=False,
            curve_2020_2026_001=[0.0, 1.0], has_recent_performance=True,
            recent_net_profit_001=1.0, recent_equity_dd_001=100.0,
            trades_2020_2026=200, net_profit_2020_2026_001=90.0,
        )

        self.assertEqual(eligibility_counts([strategy], 100)["eligible"], 0)
        self.assertEqual(
            eligibility_counts([strategy], 100, apply_recent_recovery=False)["eligible"], 1
        )

    def test_experimental_monthly_search_is_opt_in_and_persisted(self) -> None:
        defaults = normalize_settings(
            "monthly",
            {"allowed_asset_groups": ["Forex"]},
            "ICTRADING",
        )
        enabled = normalize_settings(
            "monthly",
            {
                "allowed_asset_groups": ["Forex"],
                "experimental_monthly_search": True,
            },
            "ICTRADING",
        )

        self.assertFalse(defaults["experimental_monthly_search"])
        self.assertTrue(enabled["experimental_monthly_search"])

    def test_disabling_correlation_preserves_limits_but_optimizer_ignores_them(self) -> None:
        configured = {
            "max_pair_corr": 0.31,
            "max_downside_corr": 0.21,
            "max_dd_overlap": 0.41,
            "max_portfolio_corr": 0.51,
        }
        settings = normalize_settings(
            "monthly",
            {
                "allowed_asset_groups": ["Forex"],
                "use_correlation": False,
                **configured,
            },
            "ICTRADING",
        )

        for key, value in configured.items():
            self.assertEqual(settings[key], value)
        optimizer = _optimizer_kwargs(
            settings,
            PortfolioType.BALANCED,
            [],
            15.0,
        )
        for key in configured:
            self.assertIsNone(getattr(optimizer["limits"], key))

    def test_reenabling_legacy_empty_correlation_limits_restores_defaults(self) -> None:
        empty_limits = {
            "max_pair_corr": None,
            "max_downside_corr": None,
            "max_dd_overlap": None,
            "max_portfolio_corr": None,
        }

        for scope in ("full_history", "monthly"):
            with self.subTest(scope=scope):
                settings = normalize_settings(
                    scope,
                    {
                        "allowed_asset_groups": ["Forex"],
                        "use_correlation": True,
                        **empty_limits,
                    },
                    "ICTRADING",
                )
                self.assertEqual(settings["max_pair_corr"], 0.35)
                self.assertEqual(settings["max_downside_corr"], 0.25)
                self.assertEqual(settings["max_dd_overlap"], 0.35)
                self.assertEqual(settings["max_portfolio_corr"], 0.50)

    def test_failed_monthly_job_keeps_the_last_reached_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            log_path = project / "monthly.log"
            log_path.touch()
            node = {
                "id": "ic", "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            key = coordinator._key("ic", "monthly")
            coordinator.jobs[key] = {
                "id": "job", "status": "running", "stage": 0,
                "log_path": str(log_path),
            }

            def fail_after_optimization(_source, _operation, _portfolio_id, _settings, progress):
                progress("5/6 · Optimizando propuesta 1/3")
                raise ValueError("sin propuesta")

            with patch(
                "mt5_manager.portfolio_monthly_service.run_monthly_operation",
                side_effect=fail_after_optimization,
            ):
                coordinator._worker("ic", "monthly", {}, "generate", None)

            self.assertEqual(coordinator.jobs[key]["status"], "failed")
            self.assertEqual(coordinator.jobs[key]["stage"], 5)

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
                {"key": "aggressive", "label": "Agresivo", "reserve_pct": 10,
                 "auto_adjusted_valley": True, "requested_valley_dd_pct": 3.0,
                 "adjusted_valley_dd_pct": 3.4533334, "inputs": settings, "result": result(2)},
                {"key": "balanced", "label": "Moderado", "reserve_pct": 15, "inputs": settings, "result": result(5)},
            ]
            coordinator.jobs[key] = {"status": "completed", "previous_members": [
                {"variant_key": "aggressive", "set_path": "same.set", "units": 1, "lot": .01, "symbol": "EURUSD"},
                {"variant_key": "balanced", "set_path": "same.set", "units": 3, "lot": .03, "symbol": "EURUSD"},
            ]}
            with patch.object(PortfolioSource, "inventory", return_value={}):
                state = coordinator.state("ic", "full_history")
            self.assertEqual(state["proposals"][0]["diff"][0]["old_units"], 1)
            self.assertTrue(state["proposals"][0]["auto_adjusted_valley"])
            self.assertEqual(state["proposals"][0]["requested_valley_dd_pct"], 3.0)
            self.assertAlmostEqual(state["proposals"][0]["adjusted_valley_dd_pct"], 3.4533334)
            self.assertEqual(state["proposals"][1]["diff"][0]["old_units"], 3)
            self.assertEqual(state["proposals"][1]["result"]["changed_allocations"], 1)
            self.assertEqual(state["proposals"][1]["result"]["nominal_valley_margin"], 980)

            coordinator.jobs[key] = {"status": "completed", "previous_members": []}
            with patch.object(PortfolioSource, "inventory", return_value={}):
                generated = coordinator.state("ic", "full_history")
            self.assertEqual(generated["proposals"][0]["diff"][0]["state"], "NUEVA")
