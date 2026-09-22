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


class PortfolioServiceTests(unittest.TestCase):
    def test_portfolio_alias_preserves_identity_and_other_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = root / "memory.sqlite"
            memory.touch()
            source = PortfolioSource({
                "id": "local",
                "portfolio_project_dir": str(root),
                "portfolio_memory_path": str(memory),
                "portfolio_broker": "TEST",
                "portfolio_account_type": "DEMO",
            })
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) "
                    "values(?,?,?,?,?,?)",
                    ("2026-09-13", "A/M/C original", "bundle", "bundle", "full_history",
                     json.dumps({"inputs": {"capital": 10000}, "audit": {"ok": True}})),
                ).lastrowid)
                conn.commit()

            self.assertEqual(
                source.set_portfolio_alias(portfolio_id, "full_history", "  Londres   estable "),
                "Londres estable",
            )
            detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
            self.assertEqual(detail["name"], "A/M/C original")
            self.assertEqual(detail["alias"], "Londres estable")
            self.assertEqual(detail["metrics"]["inputs"]["capital"], 10000)
            self.assertTrue(detail["metrics"]["audit"]["ok"])

            self.assertEqual(source.set_portfolio_alias(portfolio_id, "full_history", ""), "")
            self.assertEqual(
                source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]["alias"], ""
            )

    def test_decision_audit_converts_non_finite_scores_before_sqlite(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            ensure_portfolio_schema(conn)
            result = SimpleNamespace(decision_log=[SimpleNamespace(
                step=1, action="reject", set_id="a.set", from_set_id=None, to_set_id=None,
                gain=0.0, valley_cost=0.0, point_cost=0.0, score=None,
                portfolio_net_profit_after=100.0,
                portfolio_valley_dd_after=50.0,
                portfolio_point_dd_after=20.0,
                reason="descartada",
            )])

            _insert_decisions(conn, 1, result)

            score = conn.execute("select score from portfolio_decision_log").fetchone()[0]
            self.assertEqual(score, 0.0)

    def test_bind_mounts_and_shares_both_need_a_snapshot_to_see_the_wal(self) -> None:
        # El criterio no es "esta en red" sino "este sistema de ficheros no puede
        # respaldar el -shm del modo WAL". Dar 9p por local hacia que se leyera
        # con immutable=1, que ignora el -wal: un portafolio borrado por el nodo
        # seguia apareciendo en la pantalla del manager.
        mounts = (
            "//192.168.1.152/G /data/roboforex cifs rw,relatime 0 0\n"
            "C:\\040drive /data/ic 9p rw,relatime 0 0\n"
            "host /data/axi virtiofs rw,relatime 0 0\n"
            "grpcfuse /data/legacy fuse.grpcfuse rw,relatime 0 0\n"
            "overlay / overlay rw,relatime 0 0\n"
        )

        for path in (
            "/data/roboforex/TRADING/project/outputs/memory.sqlite",
            "/data/ic/outputs/memory.sqlite",
            "/data/axi/outputs/memory.sqlite",
            "/data/legacy/outputs/memory.sqlite",
        ):
            self.assertTrue(_linux_path_needs_snapshot(Path(path), mounts), path)

        # El disco propio del contenedor sí soporta WAL: se lee en el sitio.
        self.assertFalse(_linux_path_needs_snapshot(Path("/tmp/memory.sqlite"), mounts))

    def test_grid_filter_reads_set_files_in_parallel_without_changing_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            enabled = root / "enabled.set"
            disabled = root / "disabled.set"
            enabled.write_text("EnableGrid=true\n", encoding="utf-8")
            disabled.write_text("EnableGrid=false\n", encoding="utf-8")
            rows = [{"set_path": str(enabled)}, {"set_path": str(disabled)}]

            filtered, warnings = filter_rows_grid_off(rows)

            self.assertEqual(filtered, [rows[1]])
            self.assertEqual(len(warnings), 1)

    def test_loader_rejects_short_final_tick_as_continuous_history(self) -> None:
        def report(
            name: str, start: str, end: str, trade: ClosedTrade
        ) -> PeriodReport:
            return PeriodReport(
                period_name=name, start_year=trade.close_time.year,
                end_year=trade.close_time.year, symbol="EURUSD", timeframe="H1",
                pnl_curve_001=[0.0, trade.net_profit],
                net_profit_001=trade.net_profit, valley_dd_001=0.0,
                point_dd_001=0.0, profit_factor=2.0,
                return_dd_ratio=trade.net_profit, trades=1,
                closed_trades=[trade], start_date=start, end_date=end,
            )

        base_trade = ClosedTrade(
            datetime(2024, 7, 1), datetime(2024, 7, 2), "EURUSD", 0.01, 30.0
        )
        oos_trade = ClosedTrade(
            datetime(2025, 7, 1), datetime(2025, 7, 2), "EURUSD", 0.01, 40.0
        )
        short_trade = ClosedTrade(
            datetime(2026, 5, 1), datetime(2026, 5, 2), "EURUSD", 0.01, 5.0
        )
        periods = {
            "is.html": report("is", "06.01.2020", "30.12.2024", base_trade),
            "oos.html": report("oos", "06.01.2025", "29.05.2026", oos_trade),
            "short.html": report("short", "06.05.2026", "29.05.2026", short_trade),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in periods:
                (root / name).touch()
            row = {
                "set_path": str(root / "strategy.set"), "candidate_id": 1,
                "target_symbol": "EURUSD", "period": "H1",
                "is_report_path": str(root / "is.html"),
                "oos_report_path": str(root / "oos.html"),
                "full_history_report_path": str(root / "short.html"),
                "final_tick_to_date": "2026.06.30",
            }

            with patch(
                # El consumidor es load_robust_sets_from_rows, en el modulo
                # selection: parchear el paquete no alcanza su referencia.
                "portfolio_manager.ubs_portfolio.selection.period_report_from_strategy_report",
                side_effect=lambda parsed, _name: periods[str(parsed)],
            ):
                loaded, warnings = load_robust_sets_from_rows(
                    [row], [], parse=lambda path: path.name
                )

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].closed_trades_2020_2026, [base_trade, oos_trade])
        self.assertEqual(loaded[0].full_history_report_path, "")
        self.assertTrue(any("no eran continuos" in warning for warning in warnings))

    def test_windows_source_paths_are_relocated_for_a_container_project(self) -> None:
        project = Path("/data/roboforex/TRADING/MT5_Autotester_agent")
        resolved = _resolve_source_path(
            r"C:\Users\Adrian\project\outputs\ubs_agent\run\strategy.set", project
        )

        self.assertEqual(
            Path(resolved),
            (project / "outputs" / "ubs_agent" / "run" / "strategy.set").absolute(),
        )

    def test_source_path_relocation_is_idempotent_even_when_the_target_exists(self) -> None:
        # Regression: on a mapped network drive (X:\ -> \\host\share) Path.resolve()
        # rewrites the drive letter to its UNC target, so resolving a set once as a
        # raw node path (relocated, keeps the drive letter) and again as its already
        # relocated path (exists -> resolve -> UNC) produced two different strings.
        # Quarantine matching compares candidate paths (resolved once) against stored
        # quarantine paths (resolved again), so the mismatch let excluded strategies
        # reappear on every generation while the portfolio delete (keyed by id) still
        # worked -- exactly "portfolio deleted but strategy not excluded". Relocation
        # under a known project root must win over the existence check so the mapping
        # is idempotent no matter how many times it runs.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            set_file = project / "outputs" / "sets" / "strategy.set"
            set_file.parent.mkdir(parents=True)
            set_file.touch()  # the relocated target now exists locally
            node_raw = r"C:\Users\node\other_project\outputs\sets\strategy.set"

            once = _resolve_source_path(node_raw, project)
            twice = _resolve_source_path(once, project)

            self.assertEqual(Path(once), set_file.absolute())
            self.assertEqual(once, twice)
            self.assertEqual(
                PortfolioSource._path_key(once), PortfolioSource._path_key(twice)
            )

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
            allocations = []
            if any(item.set_id == "core.set" for item in pool):
                allocations.append(StrategyAllocation(
                    "core.set", "1", "EURUSD", 2, .02, 200, 20, 10,
                    recent_net_profit_001=100, has_recent_performance=True,
                ))
            if any(item.set_id == "filler.set" for item in pool):
                allocations.append(StrategyAllocation(
                    "filler.set", "2", "USDJPY", 1, .01, 10, 2, 1,
                    recent_net_profit_001=4, has_recent_performance=True,
                ))
            return self._recent_result(allocations)

        result, removed = _optimize_without_recent_fillers([core, filler], 5.0, optimize)

        self.assertEqual(removed, {"filler.set"})
        self.assertEqual(
            seen_pools,
            [["core.set", "filler.set"], ["core.set"]],
        )
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

    def test_recent_fillers_do_not_reopen_hundreds_of_inactive_candidates(self) -> None:
        pool = [SimpleNamespace(set_id="core")] + [
            SimpleNamespace(set_id=f"filler-{i}") for i in range(482)
        ]
        calls = []
        messages = []

        def optimize(candidates):
            calls.append([item.set_id for item in candidates])
            active = [candidates[0]] + candidates[1:2]
            return self._recent_result([
                StrategyAllocation(
                    item.set_id, "1", "EURUSD", 1, .01, 100, 10, 5,
                    recent_net_profit_001=100 if item.set_id == "core" else 1,
                    has_recent_performance=True,
                ) for item in active
            ])

        result, removed = _optimize_without_recent_fillers(pool, 5, optimize, progress=messages.append)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], ["core"])
        self.assertEqual(removed, {"filler-0"})
        self.assertFalse(_underrepresented_recent_allocation_ids(result, 5))
        self.assertIn("sin reabrir el pool global", messages[0])

    def test_recent_refinement_rechecks_contributions_after_lot_changes(self) -> None:
        pool = [SimpleNamespace(set_id=key) for key in ("a", "b", "c", "unused")]
        calls = []

        def optimize(candidates):
            calls.append([item.set_id for item in candidates])
            profits = {"a": 100, "b": 10 if len(calls) == 1 else 1, "c": 1}
            return self._recent_result([
                StrategyAllocation(
                    item.set_id, "1", "EURUSD", 1, .01, 100, 10, 5,
                    recent_net_profit_001=profits[item.set_id], has_recent_performance=True,
                ) for item in candidates if item.set_id in profits
            ])

        result, removed = _optimize_without_recent_fillers(pool, 5, optimize)
        self.assertEqual(calls, [["a", "b", "c", "unused"], ["a", "b"], ["a"]])
        self.assertEqual(removed, {"b", "c"})
        self.assertFalse(_underrepresented_recent_allocation_ids(result, 5))

    def test_full_and_monthly_refine_only_selected_sets_with_same_risk_settings(self) -> None:
        pool = [SimpleNamespace(set_id=key, target_month=None) for key in ("core", "filler", "unused")]
        for scope in ("full_history", "monthly"):
            with self.subTest(scope=scope):
                calls = []
                messages = []
                settings = normalize_settings(scope, {"allowed_asset_groups": ["Forex"]})

                def optimize(*, raw_sets, **kwargs):
                    calls.append(([item.set_id for item in raw_sets], kwargs))
                    return self._recent_result([
                        StrategyAllocation(
                            item.set_id, "1", "EURUSD", 1, .01, 100, 10, 5,
                            recent_net_profit_001=100 if item.set_id == "core" else 1,
                            has_recent_performance=True,
                        ) for item in raw_sets if item.set_id != "unused"
                    ])

                if scope == "full_history":
                    settings["experimental_full_search"] = True
                    with patch("mt5_manager.portfolio_generation_search.optimize_experimental_full_portfolio", side_effect=optimize), patch(
                        "mt5_manager.portfolio_generation_search.optimize_portfolio", side_effect=optimize,
                    ):
                        proposals = _locked_full_proposals(pool, settings, {}, messages.append)
                else:
                    with patch("mt5_manager.portfolio_monthly_service.optimize_portfolio", side_effect=optimize):
                        proposals = _monthly_proposals(pool, pool, settings, [], messages.append)
                self.assertEqual(len(proposals), 3)
                self.assertEqual(calls[0][0], ["core", "filler", "unused"])
                self.assertEqual(calls[1][0], ["core"])
                self.assertEqual(calls[0][1], calls[1][1])
                self.assertTrue(any("sin reabrir el pool global" in message for message in messages))
                for proposal in proposals:
                    self.assertFalse(_underrepresented_recent_allocation_ids(proposal["result"], 5))

    def test_recent_fillers_are_replaced_from_the_pool_when_refill_is_on(self) -> None:
        pool = [SimpleNamespace(set_id=key) for key in ("core", "filler", "spare")]
        calls: list[list[str]] = []
        messages: list[str] = []

        def optimize(candidates):
            available = [item.set_id for item in candidates]
            calls.append(available)
            # `spare` solo puede entrar si el hueco del relleno sigue abierto:
            # es la reposicion que la ruta estandar nunca hacia.
            chosen = ["core", "filler"] if "filler" in available else available
            return self._recent_result([
                StrategyAllocation(
                    set_id, "1", "EURUSD", 1, .01, 100, 10, 5,
                    recent_net_profit_001=1 if set_id == "filler" else 100,
                    has_recent_performance=True,
                ) for set_id in chosen
            ])

        result, removed = _optimize_without_recent_fillers(
            pool, 5, optimize, progress=messages.append, refill_from_pool=True,
        )

        self.assertEqual(calls, [["core", "filler", "spare"], ["core", "spare"]])
        self.assertEqual(removed, {"filler"})
        self.assertEqual({item.set_id for item in result.allocations}, {"core", "spare"})
        self.assertIn("reponiendo sobre 2 candidato(s)", messages[0])
        self.assertIn("reponiendo desde el pool", result.warnings[0])

    def test_refill_is_bounded_and_the_shrink_refinement_still_closes_it(self) -> None:
        pool = [SimpleNamespace(set_id="core")] + [
            SimpleNamespace(set_id=f"filler-{index}") for index in range(30)
        ]
        calls: list[list[str]] = []

        def optimize(candidates):
            available = [item.set_id for item in candidates]
            calls.append(available)
            return self._recent_result([
                StrategyAllocation(
                    set_id, "1", "EURUSD", 1, .01, 100, 10, 5,
                    recent_net_profit_001=100 if set_id == "core" else 1,
                    has_recent_performance=True,
                ) for set_id in available[:2]
            ])

        result, removed = _optimize_without_recent_fillers(
            pool, 5, optimize, refill_from_pool=True,
        )

        # Un relleno nuevo por pasada no puede reabrir el pool indefinidamente:
        # agotado el presupuesto cierra el refinamiento por supervivientes.
        self.assertEqual(len(calls), STANDARD_ANTIFILLER_REFILL_PASSES + 2)
        self.assertEqual(len(removed), STANDARD_ANTIFILLER_REFILL_PASSES + 1)
        self.assertEqual(calls[-1], ["core"])
        self.assertFalse(_underrepresented_recent_allocation_ids(result, 5))

    def test_the_standard_bundle_reopens_the_pool_and_the_experimental_one_does_not(self) -> None:
        cases = (
            (False, ["core", "spare"], "eliminada(s) y repuestas desde el pool antes"),
            (True, ["core"], "eliminada(s) antes de fijar"),
        )
        for experimental, refined_pool, warning_text in cases:
            with self.subTest(experimental=experimental):
                pool = [
                    SimpleNamespace(set_id=key, target_month=None)
                    for key in ("core", "filler", "spare")
                ]
                calls: list[list[str]] = []
                settings = normalize_settings("full_history", {"allowed_asset_groups": ["Forex"]})
                settings["experimental_full_search"] = experimental

                def optimize(*, raw_sets, **kwargs):
                    available = [item.set_id for item in raw_sets]
                    calls.append(available)
                    chosen = ["core", "filler"] if "filler" in available else available
                    return self._recent_result([
                        StrategyAllocation(
                            set_id, "1", "EURUSD", 1, .01, 100, 10, 5,
                            recent_net_profit_001=1 if set_id == "filler" else 100,
                            has_recent_performance=True,
                        ) for set_id in chosen
                    ])

                with patch(
                    "mt5_manager.portfolio_generation_search.optimize_experimental_full_portfolio",
                    side_effect=optimize,
                ), patch(
                    "mt5_manager.portfolio_generation_search.optimize_portfolio", side_effect=optimize,
                ):
                    proposals = _locked_full_proposals(pool, settings, {}, None)

                self.assertEqual(calls[0], ["core", "filler", "spare"])
                self.assertEqual(calls[1], refined_pool)
                self.assertEqual(len(proposals), 3)
                for proposal in proposals:
                    self.assertFalse(
                        _underrepresented_recent_allocation_ids(proposal["result"], 5)
                    )
                    self.assertTrue(
                        any(warning_text in warning for warning in proposal["result"].warnings),
                        proposal["result"].warnings,
                    )
