import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from mt5_manager import portfolio_improvement_attempt as base_attempt
from mt5_manager import portfolio_improvement_chain_attempt as chain_attempt
from mt5_manager import portfolio_improvement_service as improvement
from mt5_manager.portfolio_improvement_common import (
    ImprovementOptions,
    validate_and_attach_improvement_audit,
)
from mt5_manager.portfolio_service import normalize_settings


class ImprovementInheritedSettingsTests(unittest.TestCase):
    def test_every_dialog_override_replaces_the_saved_value_in_both_engines(self) -> None:
        saved = normalize_settings("full_history", {"portfolio_type": "balanced"})
        overrides = {
            "capital": 9000,
            "valley_dd_pct": 7,
            "top_k_per_symbol": 13,
            "max_total_candidates": 44,
            "min_trades_2020_2026": 77,
            "min_strategy_recent_contribution_pct": 2.5,
            "max_units_per_set": 9,
            "max_total_units": 30,
            "max_units_per_symbol": 12,
            "max_sets_per_symbol": 3,
            "dd_reserve_pct": 4,
            "search_restarts": 6,
            "margin_profile": "axi",
            "account_leverage": 100,
            "max_margin_pct": 65,
            "max_pair_corr": 0.4,
            "max_downside_corr": 0.3,
            "max_dd_overlap": 0.2,
            "max_portfolio_corr": 0.45,
            "disabled_symbols": ["NFLX"],
            "allowed_asset_groups": ["Forex", "Metals"],
            "run_local_search": False,
            "deep_optimization": True,
            "use_correlation": False,
            "validate_margin": False,
            "require_3_positive_months_6m": False,
            "grid_off": True,
        }
        request = {"portfolio_type": "balanced", "improvement_inherited_overrides": overrides}
        source = NS(broker="ICTRADING")

        for engine in (base_attempt, chain_attempt):
            with self.subTest(engine=engine.__name__), \
                    patch.object(engine, "build_margin_model", return_value=None):
                merged = engine._merged_attempt_inputs(source, request, saved, "balanced")

            for key, value in overrides.items():
                self.assertEqual(merged[key], value, key)
            self.assertNotIn("improvement_inherited_overrides", merged)

    def test_local_search_and_restarts_reach_selection_in_both_engines(self) -> None:
        inputs = normalize_settings("full_history", {
            "portfolio_type": "balanced",
            "run_local_search": True,
            "search_restarts": 6,
        })
        context = NS(
            inputs=inputs,
            options=NS(allow_same_symbol=False),
            base_type=improvement.PORTFOLIO_TYPES["balanced"],
            existing=[],
            reserve=3.0,
            original_ids=["old.set"],
            minimum_target=2,
            maximum_target=3,
        )

        for engine in (base_attempt, chain_attempt):
            with self.subTest(engine=engine.__name__):
                search = engine._selection_kwargs(context)["search"]
                self.assertTrue(search.run_local_search)
                self.assertEqual(search.search_restarts, 6)

    def test_an_unknown_inherited_setting_is_rejected(self) -> None:
        request = {
            "portfolio_type": "balanced",
            "improvement_inherited_overrides": {"experimental_full_search": True},
        }
        with self.assertRaisesRegex(ValueError, "Característica heredada desconocida"):
            base_attempt._merged_attempt_inputs(
                NS(broker="ICTRADING"),
                request,
                normalize_settings("full_history", {"portfolio_type": "balanced"}),
                "balanced",
            )

    def test_disabling_correlation_reaches_the_audit_in_both_engines(self) -> None:
        context = NS(
            inputs={
                "use_correlation": False,
                "margin_profile": "ictrading",
                "account_leverage": 1000,
                "grid_off": False,
                "min_strategy_recent_contribution_pct": 5,
                "_improvement_portfolio_uid": "11111111-1111-4111-8111-111111111111",
            },
            original_ids=["old.set"],
            options=NS(min_efficiency_gain_pct=3),
            lineage={
                "improvement_parent_uid": "",
                "improvement_root_portfolio_id": 1,
                "improvement_root_uid": "",
                "improvement_depth": 1,
                "improvement_lineage": [],
            },
            base_type=improvement.PORTFOLIO_TYPES["balanced"],
            portfolio_id=1,
            target="balanced",
        )

        for engine in (base_attempt, chain_attempt):
            with self.subTest(engine=engine.__name__), \
                    patch.object(
                        engine,
                        "validate_and_attach_improvement_audit",
                        return_value={"added_count": 1},
                    ) as validate, \
                    patch.object(engine, "_source_snapshot", return_value={}):
                engine._attach_attempt_audit(context, NS(), NS(), [], 1, [])

            audit_inputs = validate.call_args.kwargs["inputs"]
            self.assertFalse(audit_inputs["_improvement_enforce_general_correlation"])


class SameSymbolCorrelationTests(unittest.TestCase):
    @staticmethod
    def strategy(set_id: str, symbol: str) -> NS:
        return NS(
            set_id=set_id,
            symbol=symbol,
            has_recent_performance=True,
            recent_net_profit_001=10.0,
        )

    @staticmethod
    def result() -> NS:
        return NS(
            allocations=[NS(set_id=value, units=1) for value in ("old.set", "new.set")],
            total_net_profit=130.0,
            actual_valley_dd=10.0,
            target_valley_dd=20.0,
            seasonal_validation={},
            warnings=[],
        )

    def audit(self, new_symbol: str) -> dict:
        pair = NS(pearson_corr=0.90, downside_corr=0.80, dd_overlap=0.70)
        inputs = {
            "max_pair_corr": 0.35,
            "max_downside_corr": 0.25,
            "max_dd_overlap": 0.35,
            "_improvement_enforce_general_correlation": False,
        }
        with patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            return validate_and_attach_improvement_audit(
                result=self.result(),
                baseline=NS(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", new_symbol)],
                original_ids=["old.set"],
                options=ImprovementOptions(min_efficiency_gain_pct=1.0),
                inputs=inputs,
                scope="full_history",
            )

    def test_general_correlation_can_be_disabled_for_a_different_symbol(self) -> None:
        audit = self.audit("USDJPY")
        self.assertFalse(audit["candidates"][0]["same_symbol_as_original"])

    def test_same_symbol_still_requires_low_correlation_and_justification(self) -> None:
        with self.assertRaisesRegex(ValueError, "no justifica su diversificación"):
            self.audit("EURUSD")


class ImprovementInheritedSettingsScreenTests(unittest.TestCase):
    def test_dialog_edits_every_real_setting_and_keeps_improvement_criteria(self) -> None:
        script = (Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolio_improvement.js").read_text(encoding="utf-8")
        for name in (
            "capital", "valley_dd_pct", "top_k_per_symbol", "max_total_candidates",
            "min_trades_2020_2026", "improvement_min_recent_contribution_pct",
            "max_units_per_set", "max_total_units", "max_units_per_symbol",
            "max_sets_per_symbol", "dd_reserve_pct", "search_restarts",
            "improvement_margin_profile", "improvement_account_leverage",
            "max_margin_pct", "max_pair_corr", "max_downside_corr",
            "max_dd_overlap", "max_portfolio_corr", "improvement_disabled_symbols",
            "run_local_search", "deep_optimization", "use_correlation",
            "validate_margin", "require_3_positive_months_6m",
            "improvement_grid_off", "improvement_exclude_used_sets",
            "improvement_allow_same_symbol", "improvement_allowed_asset_groups",
            "improvement_portfolio_type", "improvement_min_additions",
            "improvement_min_efficiency_gain_pct", "improvement_selection_priority",
        ):
            self.assertIn(name, script)
        self.assertIn("improvement_inherited_overrides: inheritedOverrides(chosenGroups)", script)
        self.assertIn("currentDetail?.inherited_inputs", script)
        self.assertNotIn("experimental_full_search", script)
        self.assertIn('name="quick_composition"', script)
        self.assertIn("if (fields.quick_composition.checked)", script)
        self.assertIn("values.run_local_search = false", script)
        self.assertIn("values.search_restarts = 0", script)


if __name__ == "__main__":
    unittest.main()
