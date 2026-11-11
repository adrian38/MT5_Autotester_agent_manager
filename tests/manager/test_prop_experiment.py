"""El laboratorio prop mide aprobación y distingue seeds de candidatas."""

from __future__ import annotations

import unittest

from mt5_manager.experiment.experiment_lab import LabAxis, LabStrategy
from mt5_manager.prop.prop_experiment_engine import risk_normalized_pool, simulate_trial
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
        normalized = risk_normalized_pool(pool, {"seed"}, 10000.0, 1.0)
        self.assertEqual([item.source_kind for item in normalized], ["candidate", "seed"])
        self.assertEqual(normalized[0].series, (10.0,))
        self.assertEqual(normalized[1].series, (10.0,))

    def test_daily_loss_uses_each_days_opening_balance(self) -> None:
        rules = _profile(PropPhase("Evaluation", 50.0, 3.0, 50.0))
        result = simulate_trial([100.0, -301.0], 0, rules, 10000.0, 3)
        self.assertTrue(result.passed is False)
        self.assertEqual(result.reason, "daily_loss")


if __name__ == "__main__":
    unittest.main()
