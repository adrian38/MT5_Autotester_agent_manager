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


class ExecutableValleyFloorTests(unittest.TestCase):
    """El suelo de valle ejecutable que Grid ya tenia, ahora en UBS y mensual."""

    def strategy(self, set_id: str, floating: float, valley: float) -> SimpleNamespace:
        return SimpleNamespace(
            set_id=set_id, symbol="EURUSD", timeframe="H1", target_month=None,
            robustness_status="accepted", already_used=False,
            curve_2020_2026_001=[0.0, 100.0], trades_2020_2026=200,
            net_profit_2020_2026_001=100.0, has_recent_performance=False,
            recent_net_profit_001=0.0, recent_equity_dd_001=0.0,
            max_floating_dd_001=floating, valley_dd_2020_2026_001=valley,
        )

    def test_generation_removes_every_set_of_a_disabled_symbol_before_loading(self) -> None:
        strategy = self.strategy("eur.set", 30.0, 10.0)
        settings = normalize_settings(
            "full_history",
            {
                "capital": 1000,
                "valley_dd_pct": 10,
                "allowed_asset_groups": ["Forex"],
                "disabled_symbols": ["GBPUSD"],
            },
            "ICTRADING",
        )

        class Source:
            universe = Path("assets.ini")
            broker = "ICTRADING"

            def candidate_rows(self, *, include_quarantined):
                return [
                    {"set_path": "eur.set", "symbol": "EURUSD", "target_symbol": "EURUSD"},
                    {"set_path": "gbp.set", "symbol": "GBPUSD", "target_symbol": "GBPUSD"},
                ]

            def used_set_paths(self, *_args, **_kwargs):
                return []

            def saved_curves(self, **_kwargs):
                return []

        result = SimpleNamespace(warnings=[], active_strategies=1)
        proposal = {
            "key": "balanced", "label": "Moderado", "reserve_pct": 15,
            "inputs": settings, "result": result,
        }
        with patch("mt5_manager.portfolio_generation.build_margin_model", return_value=None), \
                patch("mt5_manager.portfolio_generation.load_robust_sets_from_rows", return_value=([strategy], [])) as loader, \
                patch("mt5_manager.portfolio_generation._locked_full_proposals", return_value=[proposal]):
            generate_proposals(Source(), settings)

        loaded_rows = loader.call_args.args[0]
        self.assertEqual([row["symbol"] for row in loaded_rows], ["EURUSD"])

    def test_floors_are_the_executable_valleys_above_the_request(self) -> None:
        # Capital 1000 y reserva 25% dejan un limite efectivo del 0,75%; con la
        # unidad mas barata en 30 el minimo ejecutable es 30/1000/0,75 = 4%.
        floors = _adjusted_valley_pcts(
            [self.strategy("a.set", 30.0, 10.0), self.strategy("b.set", 45.0, 12.0)],
            capital=1000.0, reserve_pct=25.0, requested_pct=2.0,
        )

        self.assertEqual([round(value, 4) for value in floors], [4.0, 6.0])

    def test_a_request_that_already_fits_has_no_floor_to_offer(self) -> None:
        floors = _adjusted_valley_pcts(
            [self.strategy("a.set", 30.0, 10.0), self.strategy("b.set", 45.0, 12.0)],
            capital=1000.0, reserve_pct=25.0, requested_pct=6.0,
        )

        self.assertEqual(floors, [])

    def test_full_history_retries_from_the_floor_and_marks_the_proposal(self) -> None:
        strategy = self.strategy("a.set", 30.0, 10.0)
        settings = normalize_settings("full_history", {
            "capital": 1000, "valley_dd_pct": 2, "allowed_asset_groups": ["Forex"],
        }, "ICTRADING")
        attempts: list[float] = []

        def locked(raw_sets, inputs, existing_by_type, progress=None):
            attempts.append(float(inputs["valley_dd_pct"]))
            if len(attempts) == 1:
                raise ValueError("No feasible portfolio for the requested valley DD")
            return [{"key": "aggressive", "label": "Agresivo", "reserve_pct": 25,
                     "inputs": inputs, "result": SimpleNamespace(warnings=[])}]

        class Source:
            universe = Path("assets.ini")
            broker = "ICTRADING"
            def candidate_rows(self, *, include_quarantined): return [
                {"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"},
            ]
            def used_set_paths(self, *_args, **_kwargs): return []
            def saved_curves(self, **_kwargs): return []

        with patch("mt5_manager.portfolio_generation.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_generation.summarize_robust_rows",
                   return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_generation._locked_full_proposals", side_effect=locked):
            _availability, proposals = generate_proposals(Source(), settings)

        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], 2.0)
        self.assertAlmostEqual(attempts[1], 4.0, places=4)
        self.assertTrue(proposals[0]["auto_adjusted_valley"])
        self.assertEqual(proposals[0]["requested_valley_dd_pct"], 2.0)
        self.assertAlmostEqual(proposals[0]["adjusted_valley_dd_pct"], 4.0, places=4)

    def test_an_empty_portfolio_also_triggers_the_floor(self) -> None:
        # El mensual no lanza error con un valle inalcanzable: devuelve tres
        # propuestas de cero estrategias, que es el mismo fracaso disfrazado.
        strategy = self.strategy("a.set", 30.0, 10.0)
        settings = normalize_settings("full_history", {
            "capital": 1000, "valley_dd_pct": 2, "allowed_asset_groups": ["Forex"],
        }, "ICTRADING")
        attempts: list[float] = []

        def locked(raw_sets, inputs, existing_by_type, progress=None):
            attempts.append(float(inputs["valley_dd_pct"]))
            active = 0 if len(attempts) == 1 else 1
            return [{"key": "aggressive", "label": "Agresivo", "reserve_pct": 25,
                     "inputs": inputs,
                     "result": SimpleNamespace(warnings=[], active_strategies=active)}]

        class Source:
            universe = Path("assets.ini")
            broker = "ICTRADING"
            def candidate_rows(self, *, include_quarantined): return [
                {"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"},
            ]
            def used_set_paths(self, *_args, **_kwargs): return []
            def saved_curves(self, **_kwargs): return []

        with patch("mt5_manager.portfolio_generation.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_generation.summarize_robust_rows",
                   return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_generation._locked_full_proposals", side_effect=locked):
            _availability, proposals = generate_proposals(Source(), settings)

        self.assertEqual(len(attempts), 2)
        self.assertTrue(proposals[0]["auto_adjusted_valley"])
        self.assertEqual(proposals[0]["result"].active_strategies, 1)

    def test_when_no_floor_gives_a_portfolio_the_original_result_survives(self) -> None:
        # Nunca peor que antes: si ningun suelo produce cartera, se devuelve lo
        # que este ambito devolvia, con el motivo escrito.
        strategy = self.strategy("a.set", 30.0, 10.0)
        settings = normalize_settings("full_history", {
            "capital": 1000, "valley_dd_pct": 2, "allowed_asset_groups": ["Forex"],
        }, "ICTRADING")
        warnings_seen: list[str] = []

        def locked(raw_sets, inputs, existing_by_type, progress=None):
            return [{"key": "aggressive", "label": "Agresivo", "reserve_pct": 25,
                     "inputs": inputs,
                     "result": SimpleNamespace(warnings=warnings_seen, active_strategies=0)}]

        class Source:
            universe = Path("assets.ini")
            broker = "ICTRADING"
            def candidate_rows(self, *, include_quarantined): return [
                {"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"},
            ]
            def used_set_paths(self, *_args, **_kwargs): return []
            def saved_curves(self, **_kwargs): return []

        with patch("mt5_manager.portfolio_generation.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_generation.summarize_robust_rows",
                   return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_generation._locked_full_proposals", side_effect=locked):
            _availability, proposals = generate_proposals(Source(), settings)

        self.assertEqual(len(proposals), 1)
        self.assertFalse(proposals[0]["auto_adjusted_valley"])
        self.assertIn("ninguno de los", " ".join(proposals[0]["result"].warnings))

    def test_a_feasible_request_is_not_retried_nor_marked(self) -> None:
        strategy = self.strategy("a.set", 30.0, 10.0)
        settings = normalize_settings("full_history", {
            "capital": 1000, "valley_dd_pct": 10, "allowed_asset_groups": ["Forex"],
        }, "ICTRADING")
        calls: list[float] = []

        def locked(raw_sets, inputs, existing_by_type, progress=None):
            calls.append(float(inputs["valley_dd_pct"]))
            return [{"key": "aggressive", "label": "Agresivo", "reserve_pct": 25,
                     "inputs": inputs, "result": SimpleNamespace(warnings=[])}]

        class Source:
            universe = Path("assets.ini")
            broker = "ICTRADING"
            def candidate_rows(self, *, include_quarantined): return [
                {"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"},
            ]
            def used_set_paths(self, *_args, **_kwargs): return []
            def saved_curves(self, **_kwargs): return []

        with patch("mt5_manager.portfolio_generation.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_generation.summarize_robust_rows",
                   return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_generation._locked_full_proposals", side_effect=locked):
            _availability, proposals = generate_proposals(Source(), settings)

        self.assertEqual(calls, [10.0])
        self.assertFalse(proposals[0]["auto_adjusted_valley"])


class IncompleteBundleTests(unittest.TestCase):
    """Una variante inviable ya no anula a las otras dos, pero bloquea guardar."""

    def strategy(self) -> SimpleNamespace:
        return SimpleNamespace(
            set_id="a.set", symbol="EURUSD", timeframe="H1", target_month=None,
            robustness_status="accepted", already_used=False,
            curve_2020_2026_001=[0.0, 100.0], trades_2020_2026=200,
            net_profit_2020_2026_001=100.0, has_recent_performance=False,
            recent_net_profit_001=0.0, recent_equity_dd_001=0.0,
            max_floating_dd_001=10.0, valley_dd_2020_2026_001=10.0,
        )

    def result(self) -> SimpleNamespace:
        allocation = SimpleNamespace(
            set_id="a.set", units=1, recent_net_profit_001=0.0, has_recent_performance=False,
        )
        return SimpleNamespace(allocations=[allocation], warnings=[], seasonal_validation={})

    def test_two_viable_variants_are_returned_with_the_failure_named(self) -> None:
        settings = normalize_settings("full_history", {
            "capital": 1000, "valley_dd_pct": 10, "allowed_asset_groups": ["Forex"],
        }, "ICTRADING")
        outcomes = [self.result(), self.result(), self.result(),
                    ValueError("Final portfolio violates valley DD")]

        def optimize(*_args, **_kwargs):
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with patch("mt5_manager.portfolio_generation_search.optimize_portfolio", side_effect=optimize):
            proposals = _locked_full_proposals([self.strategy()], settings, {})

        self.assertEqual([item["key"] for item in proposals], ["aggressive", "balanced"])
        combined = " ".join(proposals[0]["result"].warnings)
        self.assertIn("Paquete A/M/C incompleto: 2/3", combined)
        self.assertIn("Final portfolio violates valley DD", combined)

    def test_an_incomplete_bundle_cannot_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator(
                [{"id": "ic", "portfolio_broker": "ICTRADING"}], Path(temp_dir) / "settings.json",
            )
            key = coordinator._key("ic", "full_history")
            coordinator.jobs[key] = {"status": "completed", "operation": "generate"}
            coordinator.proposals[key] = [
                {"key": "aggressive", "label": "Agresivo"},
                {"key": "balanced", "label": "Moderado"},
            ]

            with self.assertRaises(ValueError) as error:
                coordinator.prepare_save("ic", "full_history", "aggressive")

            self.assertIn("incompleto (2/3", str(error.exception))

    def test_the_monthly_trio_is_not_mistaken_for_an_incomplete_bundle(self) -> None:
        # El mensual usa profit/balanced/margin y comparte el nombre «balanced».
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator(
                [{"id": "ic", "portfolio_broker": "ICTRADING"}], Path(temp_dir) / "settings.json",
            )
            key = coordinator._key("ic", "monthly")
            coordinator.jobs[key] = {"status": "completed", "operation": "generate"}
            coordinator.proposals[key] = [{"key": "balanced", "label": "Equilibrada",
                                           "reserve_pct": 15, "inputs": {}, "result": None}]

            with patch("mt5_manager.portfolio_coordinator_saved.serialize_portfolio_proposals", return_value=[]):
                payload = coordinator.prepare_save("ic", "monthly", "balanced")

            self.assertEqual(payload["selected_key"], "balanced")


class MonthlyMarginModelTests(unittest.TestCase):
    def test_monthly_builds_the_measured_margin_model_like_the_other_scopes(self) -> None:
        # Sin esto, la misma cuenta AXI validaba el margen con dos modelos
        # distintos segun la pantalla.
        strategy = SimpleNamespace(
            set_id="a.set", symbol="EURUSD", robustness_status="accepted",
            already_used=False, curve_2020_2026_001=[0.0, 100.0], trades_2020_2026=200,
            net_profit_2020_2026_001=100.0, has_recent_performance=False,
            recent_net_profit_001=0.0, recent_equity_dd_001=0.0,
        )
        model = object()
        seen: dict[str, object] = {}

        def proposals(monthly_sets, full_sets, inputs, existing, progress=None):
            seen["margin_model"] = inputs.get("margin_model")
            return [{"key": "profit", "label": "Maximo beneficio", "reserve_pct": 10,
                     "inputs": inputs, "result": SimpleNamespace(warnings=[])}]

        class Source:
            universe = Path("assets.ini")
            def candidate_rows(self, *, include_quarantined): return [
                {"set_path": "a.set", "symbol": "EURUSD", "target_symbol": "EURUSD"},
            ]
            def used_set_paths(self, *_args, **_kwargs): return []
            def saved_curves(self, **_kwargs): return []

        inputs = normalize_settings("monthly", {
            "target_month": 1, "allowed_asset_groups": ["Forex"], "margin_profile": "axi",
        }, "AXI")
        with patch("mt5_manager.portfolio_monthly_service.build_margin_model", return_value=model), \
             patch("mt5_manager.portfolio_monthly_service.load_robust_sets_from_rows", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_monthly_service.slice_strategy_sets_to_month", return_value=([strategy], [])), \
             patch("mt5_manager.portfolio_monthly_service.summarize_robust_rows",
                   return_value=PortfolioAvailability(1, 0, 1, 1, {"EURUSD": 1})), \
             patch("mt5_manager.portfolio_monthly_service._monthly_proposals", side_effect=proposals):
            generate_monthly_proposals(Source(), inputs)

        self.assertIs(seen["margin_model"], model)
