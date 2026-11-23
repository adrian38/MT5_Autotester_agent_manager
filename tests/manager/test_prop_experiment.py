"""El laboratorio prop mide aprobación y distingue seeds de candidatas."""

from __future__ import annotations

import unittest

from mt5_manager.experiment.experiment_lab import LabAxis, LabStrategy
from mt5_manager.prop.prop_experiment_engine import (
    _eligible_additions,
    credible_pool,
    Evaluation,
    SearchResult,
    result_payload,
    executable_pool,
    search,
    simulate_trial,
)
from mt5_manager.prop.prop_experiment_reporting import attempt_analysis
from mt5_manager.prop.prop_experiment_periods import windowed_pool
from mt5_manager.prop.prop_experiment_validation import _validation_groups
from mt5_manager.prop.prop_profiles import PropPhase, PropProfile, profile


def _profile(phase: PropPhase) -> PropProfile:
    return PropProfile("test", "Test", "Test", (phase,), "daily", "https://example.test")


def _strategy(key: str, valley: float, series: tuple[float, ...]) -> LabStrategy:
    return LabStrategy(
        key=key, origin="IC", origin_node="ic", set_id=key, symbol="EURUSD",
        symbol_key="EURUSD", timeframe="H1", set_path=f"{key}.set", series=series,
        net=sum(series), valley_dd=valley, active_days=len(series),
        margin_per_unit=0.0, portable=True, portability="measured",
    )


class PropExperimentEngineTests(unittest.TestCase):
    def test_the_named_profiles_include_one_and_two_phase_programs(self) -> None:
        self.assertEqual(len(profile("ftmo-1-step").phases), 1)
        self.assertEqual(len(profile("ftmo-2-step").phases), 2)
        self.assertEqual(profile("the5ers-high-stakes").firm, "The5ers")
        self.assertEqual(profile("ttp-prime-1-phase").firm, "The Trading Pit")
        growth = profile("the5ers-growth")
        self.assertEqual(growth.account_sizes, (5000.0, 10000.0, 20000.0))
        self.assertEqual(growth.account_leverage, 30.0)
        self.assertEqual(growth.phases[0].min_profitable_days, 0)

    def test_a_trial_passes_only_after_target_and_consistency(self) -> None:
        rules = _profile(PropPhase(
            "Evaluation", 10.0, 50.0, 50.0, consistency_pct=50.0,
        ))
        result = simulate_trial([600.0, 400.0, 200.0], 0, rules, 10000.0, 10)
        self.assertTrue(result.passed)
        self.assertEqual(result.days, 3)

    def test_eod_trailing_loss_is_stricter_than_static_loss(self) -> None:
        trailing = _profile(PropPhase("Evaluation", 50.0, 50.0, 10.0, "eod_trailing"))
        static = _profile(PropPhase("Evaluation", 50.0, 50.0, 10.0, "static"))
        self.assertEqual(
            simulate_trial([1000.0, -1200.0], 0, trailing, 10000.0, 2).reason,
            "max_loss",
        )
        self.assertEqual(
            simulate_trial([1000.0, -1200.0], 0, static, 10000.0, 2).reason,
            "timeout",
        )

    def test_seeds_are_added_not_substituted_for_current_candidates(self) -> None:
        pool = [_strategy("candidate", 100.0, (10.0,)), _strategy("seed", 200.0, (20.0,))]
        normalized = executable_pool(pool, {"seed"}, set())
        self.assertEqual([item.source_kind for item in normalized], ["candidate", "seed"])
        self.assertEqual(normalized[0].series, (10.0,))
        self.assertEqual(normalized[1].series, (20.0,))

    def test_manual_portfolios_remain_a_distinct_source(self) -> None:
        pool = [_strategy("candidate", 100.0, (10.0,)), _strategy("manual", 100.0, (10.0,))]
        normalized = executable_pool(pool, set(), {"manual"})
        self.assertEqual([item.source_kind for item in normalized], ["candidate", "manual"])

    def test_daily_loss_uses_each_days_opening_balance(self) -> None:
        rules = _profile(PropPhase("Evaluation", 50.0, 3.0, 50.0))
        result = simulate_trial([100.0, -301.0], 0, rules, 10000.0, 3)
        self.assertTrue(result.passed is False)
        self.assertEqual(result.reason, "daily_loss")

    def test_result_contains_the_shared_portfolio_visualization_data(self) -> None:
        normalized = executable_pool(
            [_strategy("candidate", 100.0, (10.0, -5.0, 20.0))], set(), set(),
        )
        found = SearchResult(
            {"candidate": 2}, Evaluation(2, 1, 50.0, 3.0, 80.0, {"timeout": 1}),
            tuple(normalized),
        )
        axis = LabAxis(("2026-01-01", "2026-01-02", "2026-01-03"), (0, 0, 0), 1)
        payload = result_payload(found, axis, 5000.0)
        self.assertEqual(payload["portfolio"]["net_profit"], 50.0)
        self.assertEqual(payload["portfolio"]["max_drawdown"], 10.0)
        self.assertEqual(len(payload["portfolio"]["curve"]), 3)
        self.assertEqual(payload["allocations"][0]["lot"], 0.02)

    def test_a_phase_uses_only_its_declared_dates(self) -> None:
        normalized = executable_pool(
            [_strategy("candidate", 100.0, (1.0, 2.0, 3.0))], set(), set(),
        )
        axis = LabAxis(("2024-12-31", "2025-01-01", "2026-07-01"), (0, 1, 2), 3)
        pool, window = windowed_pool(normalized, axis, "2025-01-01", "2026-06-30")
        self.assertEqual(window.days, ("2025-01-01",))
        self.assertEqual(pool[0].series, (2.0,))

    def test_repair_keeps_survivors_and_rebuilds_their_lots(self) -> None:
        normalized = executable_pool([
            _strategy("survivor", 10.0, (200.0,) * 130),
            _strategy("addition", 10.0, (100.0,) * 130),
        ], set(), set())
        axis = LabAxis(tuple(f"2025-01-{(index % 28) + 1:02d}" for index in range(130)), (0,) * 130, 1)
        found = search(
            normalized, axis,
            _profile(PropPhase("Evaluation", 1.0, 50.0, 50.0)),
            {"capital": 5000.0}, required_keys={"survivor"},
        )
        self.assertIn("survivor", found.allocations)

    def test_prop_grid_checkbox_does_not_invent_another_risk_filter(self) -> None:
        items = executable_pool([
            _strategy("Grid_bad", 20.0, (10.0, -1.0)),
            _strategy("zero", 0.0, (10.0, 1.0)),
            _strategy("valid", 10.0, (10.0, -1.0)),
        ], set(), set())
        accepted, warnings = credible_pool(items)
        self.assertEqual([item.lab.key for item in accepted], ["zero", "valid"])
        self.assertTrue(any("Grid" in warning for warning in warnings))
        accepted_grid, _warnings = credible_pool(items, include_grids=True)
        self.assertEqual(
            [item.lab.key for item in accepted_grid], ["Grid_bad", "zero", "valid"],
        )

    def test_growth_evaluation_uses_daily_pause_and_non_overlapping_attempts(self) -> None:
        normalized = executable_pool([
            _strategy("candidate", 10.0, (-200.0, -200.0, -200.0, 500.0)),
        ], set(), set())
        axis = LabAxis((
            "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07",
        ), (0,) * 4, 1)
        measured = search(
            normalized, axis, profile("the5ers-growth"), {
                "capital": 5000.0, "bankroll_eur": 1000.0,
                "evaluation_fee_eur": 52.0,
            },
        ).evaluation
        self.assertEqual(measured.trials, 2)
        self.assertEqual(measured.passed, 1)
        self.assertEqual(measured.failures, {"stop_out": 1})

    def test_monthly_bankroll_limits_attempts_and_resets(self) -> None:
        strategy = executable_pool([
            _strategy("valid", 10.0, (500.0,) * 8),
        ], set(), set())[0]
        result = SearchResult(
            {"valid": 1}, Evaluation(8, 8, 100.0, 1.0, 100.0, {}),
            (strategy,),
        )
        axis = LabAxis((
            "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07",
            "2025-02-03", "2025-02-04", "2025-02-05", "2025-02-06",
        ), (0,) * 8, 2)
        analysis = attempt_analysis(
            result, axis, profile("the5ers-growth"),
            {"capital": 5000.0, "bankroll_eur": 100.0, "evaluation_fee_eur": 60.0},
        )
        self.assertEqual(analysis["bankroll"]["max_attempts_per_month"], 1)
        self.assertEqual(analysis["totals"]["passed"], 2)
        self.assertEqual([row["started"] for row in analysis["monthly"]], [1, 1])
        self.assertEqual(
            [row["status"] for row in analysis["calendar"]],
            ["passed", "no_budget", "no_budget", "no_budget",
             "passed", "no_budget", "no_budget", "no_budget"],
        )

    def test_mt5_validation_preserves_each_selected_lot(self) -> None:
        groups = _validation_groups({"allocations": [
            {"origin_node": "ic", "lot": 0.03, "key": "a", "set_path": "a.set"},
            {"origin_node": "ic", "lot": 0.01, "key": "b", "set_path": "b.set"},
            {"origin_node": "ic", "lot": 0.03, "key": "c", "set_path": "c.set"},
        ]})
        self.assertEqual([(node, lot, len(rows)) for node, lot, rows in groups], [
            ("ic", 0.01, 1), ("ic", 0.03, 2),
        ])

    def test_an_executable_strategy_has_no_arbitrary_unit_cap(self) -> None:
        normalized = executable_pool(
            [_strategy("candidate", 1.0, (1.0,))], set(), set(),
        )
        self.assertEqual(
            _eligible_additions(normalized, {"candidate": 100}, 100.0),
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
