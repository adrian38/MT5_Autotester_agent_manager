"""El laboratorio prop mide aprobación y distingue seeds de candidatas."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime

from mt5_manager.experiment.experiment_lab import LabAxis, LabStrategy
from mt5_manager.prop.prop_experiment_engine import (
    credible_pool,
    Evaluation,
    SearchResult,
    result_payload,
    executable_pool,
    evaluate,
    search,
    simulate_trial,
)
from mt5_manager.prop.prop_experiment_reporting import attempt_analysis
from mt5_manager.prop.prop_experiment_periods import replay_window
from mt5_manager.prop.prop_experiment_validation import _validation_groups
from mt5_manager.prop.prop_profiles import PropPhase, PropProfile, profile
from mt5_manager.prop.the5ers_hyper_growth_engine import GrowthClose, GrowthDay
from mt5_manager.prop.the5ers_hyper_growth_budget import simulate_monthly_campaign
from mt5_manager.prop.the5ers_hyper_growth_rules import HYPER_GROWTH_5K
from portfolio_manager.ubs_portfolio import ClosedTrade


def _profile(phase: PropPhase) -> PropProfile:
    return PropProfile("test", "Test", "Test", (phase,), "daily", "https://example.test")


def _strategy(key: str, valley: float, series: tuple[float, ...]) -> LabStrategy:
    return LabStrategy(
        key=key, origin="IC", origin_node="ic", set_id=key, symbol="EURUSD",
        symbol_key="EURUSD", timeframe="H1", set_path=f"{key}.set", series=series,
        net=sum(series), valley_dd=valley, active_days=len(series),
        margin_per_unit=100.0, portable=True, portability="measured",
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

    def test_monthly_bankroll_pays_at_most_nineteen_completed_sequential_trials(self) -> None:
        days = tuple(f"2025-01-{day:02d}" for day in range(1, 21))
        campaign = simulate_monthly_campaign((500.0,) * 20, days, HYPER_GROWTH_5K, 1000, 52)
        self.assertEqual(campaign.passed, 19)
        self.assertEqual(campaign.spent_by_month, {"2025-01": 988.0})
        self.assertTrue(all(
            prior.result.end_index == following.result.start_index
            for prior, following in zip(campaign.attempts, campaign.attempts[1:])
        ))

    def test_next_trial_does_not_credit_trades_opened_in_previous_account(self) -> None:
        days = ("2025-01-31", "2025-02-03", "2025-02-04")
        replay = [
            GrowthDay(
                0, date=day, closes=(GrowthClose(datetime.fromisoformat(day), opened, 500),),
            )
            for day, opened in zip(days, (days[0], days[0], days[1]))
        ]
        campaign = simulate_monthly_campaign(replay, days, HYPER_GROWTH_5K, 1000, 52)
        self.assertEqual([(row.result.start_index, row.result.end_index) for row in campaign.attempts],
                         [(0, 1), (1, 3)])
        self.assertEqual(campaign.passed, 2)
        self.assertEqual(campaign.spent_by_month, {"2025-01": 52.0, "2025-02": 52.0})

    def test_search_prefers_more_sequential_passes_over_a_slower_perfect_rate(self) -> None:
        slow = Evaluation(7, 7, 100.0, 34.0, 100.0, {})
        fast = Evaluation(30, 12, 40.0, 4.0, 100.0, {"stop_out": 18})
        self.assertGreater(fast.score, slow.score)

    def test_score_breaks_equal_pass_counts_with_shorter_time_to_pass(self) -> None:
        slow = Evaluation(20, 10, 50.0, 8.0, 100.0, {"stop_out": 10})
        fast = Evaluation(20, 10, 50.0, 3.0, 100.0, {"stop_out": 10})
        self.assertGreater(fast.score, slow.score)

    def test_mt5_validation_preserves_each_selected_lot(self) -> None:
        groups = _validation_groups({"allocations": [
            {"origin_node": "ic", "lot": 0.03, "key": "a", "set_path": "a.set"},
            {"origin_node": "ic", "lot": 0.01, "key": "b", "set_path": "b.set"},
            {"origin_node": "ic", "lot": 0.03, "key": "c", "set_path": "c.set"},
        ]})
        self.assertEqual([(node, lot, len(rows)) for node, lot, rows in groups], [
            ("ic", 0.01, 1), ("ic", 0.03, 2),
        ])

    def test_search_can_jump_across_a_plateau_in_lot_size(self) -> None:
        candidate = replace(_strategy("candidate", 1.0, (4.0,)), margin_per_unit=1.0)
        found = search(
            executable_pool([candidate], set(), set()),
            LabAxis(("2025-01-02",), (0,), 1), profile("the5ers-growth"),
            {"capital": 5000.0},
        )
        self.assertEqual(found.evaluation.passed, 1)
        self.assertGreaterEqual(found.allocations["candidate"], 125)

    def test_search_finds_pair_that_outranks_best_single(self) -> None:
        candidates = [
            replace(_strategy(key, 1.0, series), margin_per_unit=2500)
            for key, series in [
                ("early", (450.0, 100.0, 450.0, 100.0)),
                ("late", (50.0, 400.0, 50.0, 400.0)),
            ]
        ]
        found = search(
            executable_pool(candidates, set(), set()),
            LabAxis(tuple(f"2025-01-0{day}" for day in range(2, 6)), (0,) * 4, 1),
            profile("the5ers-growth"), {"capital": 5000.0},
        )
        self.assertEqual(found.allocations, {"early": 1, "late": 1})
        self.assertEqual(found.evaluation.passed, 4)
        self.assertEqual(found.evaluation.median_days, 1)

    def test_report_replay_checks_coverage_lot_and_intraday_close_order(self) -> None:
        day = "2025-01-02"
        candidate = replace(_strategy("candidate", 10.0, (0.0,)), margin_per_unit=100.0)
        item = executable_pool([candidate], set(), set())[0]
        trades = tuple(
            ClosedTrade(datetime(2025, 1, 2, 8), datetime(2025, 1, 2, hour),
                        "EURUSD", 0.2, pnl)
            for hour, pnl in ((9, -400.0), (10, 1500.0))
        )
        item = replace(item, lot_per_unit=0.1, coverage=((day, day),), closed_trades=trades)
        incomplete = replace(item, coverage=())
        items, axis, warnings = replay_window([item, incomplete], LabAxis((day,), (0,), 1), day, day)
        self.assertEqual(len(items), 1)
        self.assertIn("sin cobertura", warnings[0])
        measured = evaluate(items, {"candidate": 1}, axis, profile("the5ers-growth"), 5000, 120)
        result = SearchResult({"candidate": 1}, measured, tuple(items))
        analysis = attempt_analysis(result, axis, profile("the5ers-growth"), {
            "capital": 5000, "bankroll_eur": 1000, "evaluation_fee_eur": 52,
        })
        self.assertEqual(measured.passed, 0)
        self.assertEqual(analysis["totals"]["daily_pauses"], 1)
        self.assertAlmostEqual(result_payload(result, axis, 5000)["portfolio"]["net_profit"], 550.0)
        self.assertEqual(_validation_groups(result_payload(result))[0][1], 0.1)


if __name__ == "__main__":
    unittest.main()
