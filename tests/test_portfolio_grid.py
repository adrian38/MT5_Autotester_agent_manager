from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.portfolio_grid_service import _adjusted_grid_valley_pcts, normalize_grid_settings
from mt5_manager.portfolio_scope import normalize_portfolio_scope
from portfolio_manager.grid_portfolio import _grid_evaluation, _prune_to_grid_valley
from portfolio_manager.grid_risk import (
    GridExposureModel,
    open_exposure_overlap,
    peak_margin_summary,
    portfolio_peak_lots,
    prune_overlapping_sets,
    strategy_grid_exposure,
)
from portfolio_manager.grid_set import filter_rows_grid_on, set_file_grid_enabled_value
from portfolio_manager.ubs_portfolio import ClosedTrade


def strategy(set_id: str, profit: float, floating_dd: float) -> SimpleNamespace:
    return SimpleNamespace(
        set_id=set_id,
        symbol="EURUSD",
        curve_2020_2026_001=[0.0, profit],
        curve_points_2020_2026_001=[(datetime(2025, 1, 1), profit)],
        net_profit_2020_2026_001=profit,
        max_balance_dd_001=10.0,
        max_equity_dd_001=10.0 + floating_dd,
        max_floating_dd_001=10.0 + floating_dd,
    )


def losing_trade(day: int, loss: float, volume: float = 0.01, days_open: int = 1) -> ClosedTrade:
    return ClosedTrade(
        open_time=datetime(2025, 1, day),
        close_time=datetime(2025, 1, day + days_open - 1, 23),
        symbol="EURUSD",
        volume=volume,
        profit=-abs(loss),
    )


def grid_strategy(set_id: str, trades: list[ClosedTrade], *, profit: float = 100.0,
                  declared_floating: float = 0.0) -> SimpleNamespace:
    item = strategy(set_id, profit, declared_floating)
    item.closed_trades_2020_2026 = trades
    return item


class GridSetTests(unittest.TestCase):
    def test_grid_on_requires_explicit_enable_grid_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            enabled = root / "enabled.set"
            disabled = root / "disabled.set"
            missing = root / "missing.set"
            enabled.write_text("EnableGrid=true||false||0||true||N\nGridLossUSD=0", encoding="utf-8")
            disabled.write_text("EnableGrid=false", encoding="utf-8")
            missing.write_text("GridLossUSD=0", encoding="utf-8")

            rows, warnings = filter_rows_grid_on([
                {"set_path": str(enabled)},
                {"set_path": str(disabled)},
                {"set_path": str(missing)},
            ])

            self.assertEqual([row["set_path"] for row in rows], [str(enabled)])
            self.assertTrue(any("EnableGrid=false" in warning for warning in warnings))
            self.assertTrue(any("no existe" in warning for warning in warnings))
            self.assertIs(set_file_grid_enabled_value(enabled), True)
            self.assertIs(set_file_grid_enabled_value(disabled), False)
            self.assertIsNone(set_file_grid_enabled_value(missing))


class GridRiskTests(unittest.TestCase):
    def test_grid_valley_is_max_of_floating_and_combined_closed_dd(self) -> None:
        strategies = [strategy("a", 10.0, 30.0), strategy("b", 20.0, 40.0)]

        evaluation, floating_max, valley = _grid_evaluation(
            strategies, {"a": 1, "b": 1}, 100.0, 100.0,
        )

        self.assertEqual(evaluation.closed_valley_dd, 0.0)
        self.assertEqual(floating_max, 40.0)
        self.assertEqual(valley, 40.0)

    def test_floating_max_removes_only_the_strategy_that_exceeds_valley(self) -> None:
        strategies = [strategy("a", 10.0, 30.0), strategy("b", 20.0, 40.0)]

        allocations, _evaluation, floating_max, decisions, removed = _prune_to_grid_valley(
            strategies, {"a": 1, "b": 1}, 35.0, 50.0,
        )

        self.assertEqual(allocations, {"a": 1})
        self.assertEqual(floating_max, 30.0)
        self.assertEqual(removed, ["b"])
        self.assertEqual(len(decisions), 1)

    def test_grid_valley_reduces_units_without_forcing_binary_allocations(self) -> None:
        strategies = [strategy("a", 10.0, 30.0)]

        allocations, evaluation, floating_max, decisions, removed = _prune_to_grid_valley(
            strategies, {"a": 3}, 65.0, 100.0,
        )

        self.assertEqual(allocations, {"a": 2})
        self.assertEqual(evaluation.total_net_profit, 20.0)
        self.assertEqual(floating_max, 60.0)
        self.assertEqual(removed, [])
        self.assertEqual(decisions[0].action, "reduce_for_grid_valley")

    def test_infeasible_request_gets_the_next_executable_valley_floor(self) -> None:
        candidate = strategy("a", 10.0, 31.08)
        candidate.robustness_status = "accepted"
        candidate.already_used = False
        candidate.trades_2020_2026 = 10
        candidate.valley_dd_2020_2026_001 = 13.01

        adjusted = _adjusted_grid_valley_pcts(
            [candidate], capital=1000.0, reserve_pct=10.0,
            requested_pct=3.0, min_trades=1,
        )

        self.assertEqual(len(adjusted), 1)
        self.assertAlmostEqual(adjusted[0], 31.08 / 1000.0 * 100.0 / 0.9, places=5)


class GridOpenExposureTests(unittest.TestCase):
    def test_portfolio_floating_adds_up_the_strategies_underwater_the_same_day(self) -> None:
        # El max() entre estrategias da por hecho que solo una esta bajo el agua.
        # Cuando coinciden, el flotante de la cartera es la suma de ese dia.
        first = grid_strategy("a", [losing_trade(10, 60.0)], declared_floating=60.0)
        second = grid_strategy("b", [losing_trade(10, 40.0)], declared_floating=40.0)
        model = GridExposureModel([first, second])

        self.assertEqual(model.declared_floating({"a": 1, "b": 1}), 60.0)
        self.assertEqual(model.floating({"a": 1, "b": 1}), 100.0)
        audit = model.audit({"a": 1, "b": 1})
        self.assertEqual(audit["worst_day"], "2025-01-10")
        self.assertEqual(audit["coincident_sets"], 2)

    def test_strategies_underwater_on_different_days_keep_the_previous_valley(self) -> None:
        # Sin coincidencia no hay nada que sumar: el resultado no puede empeorar
        # respecto al max() de siempre.
        first = grid_strategy("a", [losing_trade(10, 60.0)], declared_floating=60.0)
        second = grid_strategy("b", [losing_trade(20, 40.0)], declared_floating=40.0)
        model = GridExposureModel([first, second])

        self.assertEqual(model.floating({"a": 1, "b": 1}), 60.0)

    def test_declared_floating_is_the_floor_when_the_trades_are_missing(self) -> None:
        # Un set sin operaciones legibles mide cero; conservar el declarado evita
        # que la medida nueva rebaje el riesgo que ya se reconocia.
        model = GridExposureModel([strategy("a", 10.0, 30.0)])

        self.assertEqual(model.floating({"a": 1}), 30.0)
        self.assertEqual(model.audit({"a": 1})["measured_open_exposure"], 0.0)

    def test_units_scale_the_open_exposure(self) -> None:
        model = GridExposureModel([grid_strategy("a", [losing_trade(10, 25.0)])])

        self.assertEqual(model.floating({"a": 3}), 75.0)

    def test_removing_one_unit_matches_a_full_recalculation(self) -> None:
        # La poda usa el atajo incremental; tiene que dar lo mismo que rehacerlo.
        first = grid_strategy("a", [losing_trade(10, 60.0), losing_trade(12, 20.0)])
        second = grid_strategy("b", [losing_trade(10, 40.0)])
        model = GridExposureModel([first, second])
        allocations = {"a": 2, "b": 1}
        total = model.total_vector(allocations)

        self.assertEqual(
            model.floating_without_one_unit(total, allocations, "a"),
            model.floating({"a": 1, "b": 1}),
        )
        self.assertEqual(
            model.floating_without_one_unit(total, allocations, "b"),
            model.floating({"a": 2}),
        )

    def test_valley_still_takes_the_maximum_against_the_closed_drawdown(self) -> None:
        # La regla de dominio no cambia: max(flotante, cerrado).
        first = grid_strategy("a", [losing_trade(10, 5.0)], declared_floating=5.0)
        strategies = [first]
        _evaluation, floating, valley = _grid_evaluation(strategies, {"a": 1}, 100.0, 100.0)

        self.assertEqual(floating, 5.0)
        self.assertEqual(valley, max(floating, 0.0))


class GridLadderMarginTests(unittest.TestCase):
    def test_peak_concurrency_measures_the_internal_ladder(self) -> None:
        # Un grid escalona el lote: la pierna base es 0.01 pero llegan a estar
        # abiertas tres a la vez por 0.07 lotes.
        trades = [
            losing_trade(10, 5.0, volume=0.01, days_open=3),
            losing_trade(10, 5.0, volume=0.02, days_open=3),
            losing_trade(11, 5.0, volume=0.04, days_open=2),
        ]
        exposure = strategy_grid_exposure(grid_strategy("a", trades))

        self.assertEqual(exposure.peak_legs, 3)
        self.assertAlmostEqual(exposure.peak_lots, 0.07)
        self.assertAlmostEqual(exposure.base_leg_lot, 0.01)
        self.assertAlmostEqual(exposure.peak_exposure_ratio, 7.0)

    def test_peak_margin_scales_the_nominal_margin_by_the_ladder(self) -> None:
        trades = [
            losing_trade(10, 5.0, volume=0.01, days_open=2),
            losing_trade(10, 5.0, volume=0.04, days_open=2),
        ]
        model = GridExposureModel([grid_strategy("a", trades)])
        nominal = {"by_set": {"a": {"symbol": "EURUSD", "units": 2, "margin": 10.0}}}

        summary = peak_margin_summary(nominal, model, balance=1000.0, max_margin_pct=100.0)

        self.assertEqual(summary["by_set"]["a"]["peak_exposure_ratio"], 5.0)
        self.assertEqual(summary["total"], 50.0)
        self.assertEqual(summary["usage_pct"], 5.0)
        self.assertFalse(summary["exceeds_limit"])
        self.assertAlmostEqual(portfolio_peak_lots(model, {"a": 2}), 0.1)

    def test_peak_margin_over_the_limit_is_flagged(self) -> None:
        trades = [losing_trade(10, 5.0, volume=0.01), losing_trade(10, 5.0, volume=0.99)]
        model = GridExposureModel([grid_strategy("a", trades)])
        nominal = {"by_set": {"a": {"symbol": "EURUSD", "units": 1, "margin": 10.0}}}

        summary = peak_margin_summary(nominal, model, balance=100.0, max_margin_pct=100.0)

        self.assertTrue(summary["exceeds_limit"])

    def test_pruning_reduces_units_until_the_peak_margin_fits(self) -> None:
        # El valle ya cabe; lo que aprieta es el margen de la escalera abierta.
        item = grid_strategy("a", [losing_trade(10, 1.0, volume=0.01)], profit=10.0)
        margins: list[dict[str, int]] = []

        def peak_margin_for(allocations: dict[str, int]) -> float:
            margins.append(dict(allocations))
            return 40.0 * sum(allocations.values())

        allocations, _evaluation, _floating, decisions, _removed = _prune_to_grid_valley(
            [item], {"a": 3}, 1000.0, 1000.0,
            peak_margin_for=peak_margin_for, peak_margin_limit=100.0,
        )

        self.assertEqual(allocations, {"a": 2})
        self.assertTrue(margins)
        self.assertIn("margen de pico", decisions[-1].reason)


class GridOverlapTests(unittest.TestCase):
    def test_overlap_counts_shared_days_with_open_positions(self) -> None:
        first = strategy_grid_exposure(grid_strategy("a", [losing_trade(10, 5.0, days_open=4)]))
        second = strategy_grid_exposure(grid_strategy("b", [losing_trade(12, 5.0, days_open=4)]))

        # dias a: 10-13, dias b: 12-15 -> interseccion 2, union 6
        self.assertAlmostEqual(open_exposure_overlap(first, second), 2 / 6)

    def test_pool_drops_the_less_efficient_of_two_overlapping_grids(self) -> None:
        efficient = grid_strategy("efficient", [losing_trade(10, 10.0, days_open=5)], profit=500.0)
        redundant = grid_strategy("redundant", [losing_trade(10, 10.0, days_open=5)], profit=100.0)

        kept, warnings = prune_overlapping_sets([efficient, redundant], max_open_overlap=0.6)

        self.assertEqual([item.set_id for item in kept], ["efficient"])
        self.assertTrue(any("solapar" in warning for warning in warnings))

    def test_pool_keeps_grids_that_suffer_on_different_days(self) -> None:
        first = grid_strategy("a", [losing_trade(2, 10.0, days_open=3)], profit=500.0)
        second = grid_strategy("b", [losing_trade(20, 10.0, days_open=3)], profit=100.0)

        kept, warnings = prune_overlapping_sets([first, second], max_open_overlap=0.6)

        self.assertEqual({item.set_id for item in kept}, {"a", "b"})
        self.assertEqual(warnings, [])

    def test_sets_without_readable_trades_are_never_dropped(self) -> None:
        kept, warnings = prune_overlapping_sets(
            [strategy("a", 10.0, 30.0), strategy("b", 20.0, 40.0)], max_open_overlap=0.1,
        )

        self.assertEqual(len(kept), 2)
        self.assertEqual(warnings, [])


class GridScopeTests(unittest.TestCase):
    def test_grid_scope_clears_hidden_unit_caps(self) -> None:
        values = normalize_grid_settings({
            "capital": 25_000,
            "valley_dd_pct": 12,
            "allowed_asset_groups": ["Forex"],
            "max_units_per_set": 99,
            "GridLossUSD": 0,
        }, "AXI")

        self.assertEqual(normalize_portfolio_scope("GRID"), "grid")
        self.assertEqual(values["portfolio_scope"], "grid")
        self.assertIsNone(values["max_units_per_set"])
        self.assertIsNone(values["max_total_units"])
        self.assertIsNone(values["max_units_per_symbol"])
        self.assertFalse(values["enforce_point_dd"])
        self.assertEqual(values["GridLossUSD"], 0)

    def test_unknown_scope_is_rejected_instead_of_falling_back(self) -> None:
        with self.assertRaises(ValueError):
            normalize_portfolio_scope("grdi")

    def test_broker_cards_link_the_independent_grid_screen(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app = (root / "mt5_manager" / "static" / "app.js").read_text(encoding="utf-8")
        html = (root / "mt5_manager" / "static" / "portfolios_grid.html").read_text(encoding="utf-8")
        script = (root / "mt5_manager" / "static" / "portfolios_grid.js").read_text(encoding="utf-8")

        self.assertIn("/portfolios_grid.html?node=", app)
        self.assertIn('src="/portfolios_grid.js?', html)
        self.assertIn("const scope = 'grid'", script)

    def test_grid_screen_has_progress_readable_sets_and_saved_actions(self) -> None:
        root = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"
        html = (root / "portfolios_grid.html").read_text(encoding="utf-8")
        script = (root / "portfolios_grid.js").read_text(encoding="utf-8")

        for element_id in (
            "builder-progress", "portfolio-log", "proposal-members", "portfolio-list",
            "portfolio-detail", "detail-export", "detail-reoptimize", "portfolio-members",
            "save-export-proposal",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("function progressPercent", script)
        self.assertIn("const setName", script)
        self.assertIn("downloadPortfolioExport", script)
        self.assertIn("saveSelectedProposal", script)
        self.assertIn("open-report", script)


if __name__ == "__main__":
    unittest.main()
