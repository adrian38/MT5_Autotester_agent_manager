from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.portfolio_grid_service import _adjusted_grid_valley_pcts, normalize_grid_settings
from mt5_manager.portfolio_scope import normalize_portfolio_scope
from portfolio_manager.grid_portfolio import _grid_evaluation, _prune_to_grid_valley
from portfolio_manager.grid_set import filter_rows_grid_on, set_file_grid_enabled_value


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
