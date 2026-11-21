"""Contrato del motor independiente The5ers Hyper Growth."""

from __future__ import annotations

import unittest

from mt5_manager.prop.the5ers_hyper_growth_engine import (
    GrowthDay,
    simulate_campaign,
    simulate_evaluation,
)
from mt5_manager.prop.the5ers_hyper_growth_rules import HYPER_GROWTH_5K


class HyperGrowthEngineTests(unittest.TestCase):
    def test_rules_are_the_verified_five_thousand_account(self) -> None:
        rules = HYPER_GROWTH_5K
        self.assertEqual(rules.target_balance, 5500.0)
        self.assertEqual(rules.stop_out_level, 4700.0)
        self.assertEqual(rules.minimum_days, 0)
        self.assertFalse(rules.payload()["daily_pause_is_failure"])

    def test_the_target_can_be_reached_in_one_day(self) -> None:
        result = simulate_evaluation([500.0])
        self.assertTrue(result.passed)
        self.assertEqual(result.days, 1)

    def test_daily_pause_closes_the_day_without_failing_the_account(self) -> None:
        result = simulate_evaluation([-200.0, 650.0])
        self.assertTrue(result.passed)
        self.assertEqual(result.daily_pauses, 1)
        self.assertEqual(result.final_balance, 5500.0)

    def test_midnight_equity_sets_the_daily_pause_base(self) -> None:
        result = simulate_evaluation([
            GrowthDay(-200.0, midnight_equity=5100.0),
            553.0,
        ])
        self.assertTrue(result.passed)
        self.assertEqual(result.daily_pauses, 1)

    def test_repeated_daily_pauses_eventually_reach_the_static_stop_out(self) -> None:
        result = simulate_evaluation([-200.0, -200.0, -200.0])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "stop_out")
        self.assertEqual(result.days, 3)
        self.assertEqual(result.daily_pauses, 2)

    def test_campaign_restarts_after_completion_without_reusing_days(self) -> None:
        campaign = simulate_campaign([-200.0, -200.0, -200.0, 500.0])
        self.assertEqual(campaign.completed, 2)
        self.assertEqual(campaign.passed, 1)
        self.assertEqual(campaign.stop_outs, 1)
        self.assertEqual(campaign.attempts[1].start_index, 3)
        self.assertEqual(campaign.pass_rate, 50.0)

    def test_unresolved_history_is_not_counted_as_a_failed_attempt(self) -> None:
        campaign = simulate_campaign([10.0, 10.0])
        self.assertEqual(campaign.completed, 0)
        self.assertEqual(campaign.unresolved, 1)
        self.assertEqual(campaign.pass_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
