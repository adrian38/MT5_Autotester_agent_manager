from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mt5_manager.portfolio_improvement_common import (
    ImprovementOptions,
    allocation_units,
    improvement_options,
    member_rows,
    unique_original_members,
    validate_and_attach_improvement_audit,
)
from mt5_manager.portfolio_service import PortfolioCoordinator
from mt5_manager import portfolio_improvement_service as full_improvement
from mt5_manager import portfolio_monthly_improvement_service as monthly_improvement


class ImprovementOptionsTests(unittest.TestCase):
    def test_excluding_sets_used_by_other_portfolios_is_on_by_default(self) -> None:
        options = improvement_options({})

        self.assertTrue(options.exclude_used_sets)
        self.assertTrue(options.allow_same_symbol)
        self.assertEqual(options.max_additions, 2)
        self.assertEqual(options.min_efficiency_gain_pct, 3.0)

    def test_a_single_run_cannot_grow_the_base_without_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "entre 1 y 5"):
            improvement_options({"improvement_additions": 6})

    def test_efficiency_threshold_rejects_overfit_seeking_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "entre 0 y 25"):
            improvement_options({"improvement_min_efficiency_gain_pct": 30})

    def test_bundle_reconstruction_prefers_the_saved_base_variant(self) -> None:
        detail = {
            "members": [
                {"set_path": "one.set", "variant_key": "aggressive", "units": 7},
                {"set_path": "one.set", "variant_key": "balanced", "units": 3},
                {"set_path": "two.set", "variant_key": "balanced", "units": 2},
            ]
        }

        members = unique_original_members(detail, "balanced")

        self.assertEqual({row["set_path"] for row in members}, {"one.set", "two.set"})
        self.assertEqual(next(row for row in members if row["set_path"] == "one.set")["units"], 3)

    def test_saved_member_paths_are_relocated_before_reconstruction(self) -> None:
        member = {
            "set_path": r"C:\old-agent\outputs\run_1\one.set",
            "is_report_path": r"C:\old-agent\reports\one.htm",
            "oos_report_path": r"C:\old-agent\reports\robust_one.htm",
        }

        rows = member_rows(
            [member],
            resolve_path=lambda value: str(value).replace(
                r"C:\old-agent", "/data/agent"
            ).replace("\\", "/"),
        )

        self.assertEqual(rows[0]["set_path"], "/data/agent/outputs/run_1/one.set")
        self.assertEqual(rows[0]["is_report_path"], "/data/agent/reports/one.htm")
        self.assertEqual(
            rows[0]["oos_report_path"], "/data/agent/reports/robust_one.htm"
        )

    def test_saved_allocation_keys_use_the_same_relocated_ids_as_curves(self) -> None:
        detail = {
            "members": [
                {
                    "variant_key": "balanced",
                    "set_path": r"C:\old-agent\outputs\run_1\one.set",
                    "units": 4,
                }
            ]
        }

        units = allocation_units(
            detail,
            "balanced",
            resolve_path=lambda value: str(value).replace(
                r"C:\old-agent", "/data/agent"
            ).replace("\\", "/"),
        )

        self.assertEqual(units, {"/data/agent/outputs/run_1/one.set": 4})
        self.assertNotIn(r"C:\old-agent\outputs\run_1\one.set", units)


class ImprovementAuditTests(unittest.TestCase):
    @staticmethod
    def strategy(set_id: str, symbol: str, recent: float = 10.0) -> SimpleNamespace:
        return SimpleNamespace(
            set_id=set_id,
            symbol=symbol,
            has_recent_performance=True,
            recent_net_profit_001=recent,
        )

    @staticmethod
    def result(ids: list[str], net: float = 130.0, dd: float = 10.0) -> SimpleNamespace:
        return SimpleNamespace(
            allocations=[SimpleNamespace(set_id=set_id, units=1) for set_id in ids],
            total_net_profit=net,
            actual_valley_dd=dd,
            target_valley_dd=20.0,
            seasonal_validation={},
            warnings=[],
        )

    def test_originals_are_never_removed(self) -> None:
        with self.assertRaisesRegex(ValueError, "retirar estrategias originales"):
            validate_and_attach_improvement_audit(
                result=self.result(["new.set"]),
                baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "USDJPY")],
                original_ids=["old.set"],
                options=ImprovementOptions(),
                inputs={},
                scope="full_history",
            )

    def test_same_symbol_is_accepted_only_with_recorded_low_dependence(self) -> None:
        pair = SimpleNamespace(pearson_corr=0.10, downside_corr=0.08, dd_overlap=0.12)
        result = self.result(["old.set", "new.set"])

        with mock.patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            audit = validate_and_attach_improvement_audit(
                result=result,
                baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "EURUSD")],
                original_ids=["old.set"],
                options=ImprovementOptions(min_efficiency_gain_pct=1.0),
                inputs={"max_pair_corr": 0.35, "max_downside_corr": 0.25, "max_dd_overlap": 0.35},
                scope="full_history",
            )

        self.assertEqual(audit["removed_original_ids"], [])
        self.assertTrue(audit["candidates"][0]["same_symbol_as_original"])
        self.assertIn("baja dependencia", audit["candidates"][0]["justification"])
        self.assertIs(result.seasonal_validation["portfolio_improvement"], audit)

    def test_historical_growth_without_better_profit_dd_is_rejected(self) -> None:
        pair = SimpleNamespace(pearson_corr=0.0, downside_corr=0.0, dd_overlap=0.0)
        with mock.patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            with self.assertRaisesRegex(ValueError, "no mejora suficientemente"):
                validate_and_attach_improvement_audit(
                    result=self.result(["old.set", "new.set"], net=105.0, dd=12.0),
                    baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                    all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "USDJPY")],
                    original_ids=["old.set"],
                    options=ImprovementOptions(min_efficiency_gain_pct=1.0),
                    inputs={},
                    scope="full_history",
                )

    def test_requested_additions_are_a_maximum_not_an_exact_quota(self) -> None:
        pair = SimpleNamespace(pearson_corr=0.0, downside_corr=0.0, dd_overlap=0.0)
        result = self.result(["old.set", "new.set"])
        with mock.patch(
            "mt5_manager.portfolio_improvement_common.strategy_correlation_pair",
            return_value=pair,
        ):
            audit = validate_and_attach_improvement_audit(
                result=result,
                baseline=SimpleNamespace(total_net_profit=100.0, valley_dd=10.0),
                all_sets=[self.strategy("old.set", "EURUSD"), self.strategy("new.set", "USDJPY")],
                original_ids=["old.set"],
                options=ImprovementOptions(max_additions=2, min_efficiency_gain_pct=3.0),
                inputs={},
                scope="full_history",
            )

        self.assertEqual(audit["added_count"], 1)
        self.assertEqual(audit["maximum_additions"], 2)


class ImprovementWireTests(unittest.TestCase):
    def test_improvement_uses_the_compatible_transactional_node_verb(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordinator = PortfolioCoordinator(
                [{"id": "node-1"}], Path(folder) / "settings.json"
            )
            key = coordinator._key("node-1", "full_history")
            coordinator.jobs[key] = {
                "operation": "improve",
                "portfolio_id": 41,
            }
            coordinator.proposals[key] = [
                {"key": "aggressive"},
                {"key": "balanced"},
                {"key": "conservative"},
            ]
            with mock.patch(
                "mt5_manager.portfolio_service.serialize_portfolio_proposals",
                return_value=[{"serialized": True}],
            ):
                payload = coordinator.prepare_save(
                    "node-1", "full_history", "balanced"
                )

        self.assertEqual(payload["operation"], "complete")
        self.assertEqual(payload["manager_operation"], "improve")
        self.assertEqual(payload["portfolio_id"], 41)


class ImprovementMaximumFallbackTests(unittest.TestCase):
    def test_full_history_retries_with_one_when_two_do_not_pass(self) -> None:
        with mock.patch.object(
            full_improvement,
            "_generate_full_history_improvement_attempt",
            side_effect=[ValueError("dos no cumplen"), ({"improvement": {}}, [])],
        ) as attempt:
            availability, _proposals = full_improvement.generate_full_history_improvement(
                object(), 7, {"improvement_additions": 2}
            )

        tried = [call.args[2]["improvement_additions"] for call in attempt.call_args_list]
        self.assertEqual(tried, [2, 1])
        self.assertEqual(availability["improvement"]["maximum_additions"], 2)
        self.assertEqual(availability["improvement"]["actual_additions"], 1)

    def test_monthly_retries_with_one_when_two_do_not_pass(self) -> None:
        with mock.patch.object(
            monthly_improvement,
            "_generate_monthly_improvement_attempt",
            side_effect=[ValueError("dos no cumplen"), ({"improvement": {}}, [])],
        ) as attempt:
            availability, _proposals = monthly_improvement.generate_monthly_improvement(
                object(), 8, {"improvement_additions": 2}
            )

        tried = [call.args[2]["improvement_additions"] for call in attempt.call_args_list]
        self.assertEqual(tried, [2, 1])
        self.assertEqual(availability["improvement"]["maximum_additions"], 2)
        self.assertEqual(availability["improvement"]["actual_additions"], 1)


class ImprovementScreenTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1] / "mt5_manager" / "static"

    def test_full_and_monthly_keep_independent_improvement_scripts(self) -> None:
        full_page = (self.ROOT / "portfolios.html").read_text(encoding="utf-8")
        monthly_page = (self.ROOT / "portfolios_monthly.html").read_text(encoding="utf-8")

        self.assertIn('id="detail-improve"', full_page)
        self.assertIn('/portfolio_improvement.js', full_page)
        self.assertNotIn('/portfolio_monthly_improvement.js', full_page)
        self.assertIn('id="detail-improve"', monthly_page)
        self.assertIn('/portfolio_monthly_improvement.js', monthly_page)

    def test_dialog_makes_the_exclusion_explicit_and_checked(self) -> None:
        for name in ("portfolio_improvement.js", "portfolio_monthly_improvement.js"):
            script = (self.ROOT / name).read_text(encoding="utf-8")
            self.assertIn('name="improvement_exclude_used_sets" type="checkbox" checked', script)
            self.assertIn("originales quedarán bloqueadas", script)
            self.assertIn("improvement_allow_same_symbol", script)
            self.assertIn("Máximo de estrategias a añadir", script)
            self.assertIn('max="25" step="0.1" value="3"', script)

    def test_manager_serves_both_new_static_assets(self) -> None:
        manager = (self.ROOT.parent / "manager.py").read_text(encoding="utf-8")
        self.assertIn('"portfolio_improvement.js"', manager)
        self.assertIn('"portfolio_monthly_improvement.js"', manager)


if __name__ == "__main__":
    unittest.main()
