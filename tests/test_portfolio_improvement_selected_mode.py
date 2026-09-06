import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from mt5_manager import portfolio_improvement_service as full
from mt5_manager.portfolio_service import (
    PortfolioCoordinator, PortfolioSource, normalize_settings, save_portfolio_payload,
)
from portfolio_manager.ubs_portfolio import PortfolioResult, StrategyAllocation


def proposal(mode="balanced", source_id=1, gain=30):
    allocations = [
        StrategyAllocation(name, "1", symbol, 1, .01, 65, 5, 5,
                           set_path=name, has_recent_performance=True,
                           recent_net_profit_001=10)
        for name, symbol in ((str(Path("old.set").resolve()), "EURUSD"),
                             (str(Path("new.set").resolve()), "USDJPY"))
    ]
    result = PortfolioResult(allocations, [0, 130], 130, 10, 10, 100, 100,
                             10, 10, .02, 2, 2, "ok", [], [])
    result.seasonal_validation = {"portfolio_improvement": {"efficiency_gain_pct": gain}}
    inputs = normalize_settings("full_history", {"portfolio_type": mode})
    inputs["improvement_source_portfolio_id"] = source_id
    return {"key": mode, "label": full.TYPE_LABELS[mode], "reserve_pct": 0,
            "inputs": inputs, "result": result}


class SelectedModeTests(unittest.TestCase):
    def test_only_selected_mode_is_loaded_optimized_and_compared(self):
        for mode in full.PORTFOLIO_TYPES:
            with self.subTest(mode=mode):
                output = proposal(mode)
                old_id = output["result"].allocations[0].set_id
                detail = {"portfolio_type": "bundle", "members": [
                    {"variant_key": key, "set_path": old_id if key == mode else "missing-other.set", "units": 7 if key == mode else 999}
                    for key in full.PORTFOLIO_TYPES
                ], "metrics": {"variants": {mode: {"inputs": {"dd_reserve_pct": 17}}}}}
                source = NS(project=Path.cwd(), saved_portfolio_detail=Mock(return_value={"portfolio": detail}), saved_curves=Mock(return_value=[]))
                sets = [NS(set_id=a.set_id, symbol=a.symbol, target_month=None,
                           has_recent_performance=True, recent_net_profit_001=10)
                        for a in output["result"].allocations]
                def pool(_source, selected, *_args):
                    self.assertEqual(len(selected["members"]), 1)
                    self.assertEqual(selected["members"][0]["variant_key"], mode)
                    return sets[:1], sets, [], [], []
                with patch.object(full, "_load_full_history_improvement_pool", side_effect=pool), patch.object(full, "build_margin_model", return_value=None), patch.object(full, "optimize_portfolio", return_value=output["result"]) as optimize, patch.object(full, "evaluate_portfolio", return_value=NS(total_net_profit=100, valley_dd=10)) as baseline, patch("mt5_manager.portfolio_improvement_common.strategy_correlation_pair", return_value=NS(pearson_corr=0, downside_corr=0, dd_overlap=0)):
                    _, proposals = full.generate_full_history_improvement(source, 42, {**output["inputs"], "portfolio_type": "balanced", "improvement_portfolio_type": mode, "improvement_additions": 1})
                self.assertEqual([p["key"] for p in proposals], [mode])
                self.assertEqual(optimize.call_count, 2)
                for call in optimize.call_args_list:
                    self.assertEqual(call.kwargs["portfolio_type"], full.PORTFOLIO_TYPES[mode])
                    self.assertEqual(call.kwargs["dd_reserve_pct"], 17)
                baseline.assert_called_once()
                self.assertEqual(baseline.call_args.args[1], {old_id: 7})
                audit = proposals[0]["result"].seasonal_validation["portfolio_improvement"]
                self.assertEqual(audit["source_portfolio_id"], 42)
                self.assertEqual(audit["minimum_efficiency_gain_pct"], 3)
                self.assertTrue(audit["save_as_new"])

    def test_selects_one_when_it_beats_two(self):
        def attempt(_source, _id, inputs, _progress):
            return {}, [proposal(gain=4 if inputs["improvement_additions"] == 2 else 20)]
        with patch.object(full, "_generate_full_history_improvement_attempt", side_effect=attempt) as search:
            availability, result = full.generate_full_history_improvement(object(), 1, {"improvement_additions": 2})
        self.assertEqual(search.call_count, 2)
        self.assertEqual(availability["improvement"]["actual_additions"], 1)
        self.assertEqual(result[0]["result"].seasonal_validation["portfolio_improvement"]["efficiency_gain_pct"], 20)

    def test_new_fillers_are_rejected_before_accepting_historical_gain(self):
        output = proposal()
        old, new = output["result"].allocations
        old.recent_net_profit_001, new.recent_net_profit_001 = 1000, 1
        detail = {"portfolio_type": "bundle", "members": [{"variant_key": "balanced", "set_path": old.set_id, "units": 1}]}
        source = NS(project=Path.cwd(), saved_portfolio_detail=Mock(return_value={"portfolio": detail}), saved_curves=Mock(return_value=[]))
        sets = [NS(set_id=a.set_id) for a in (old, new)]
        with patch.object(full, "_load_full_history_improvement_pool", return_value=(sets[:1], sets, [], [], [])), patch.object(full, "build_margin_model", return_value=None), patch.object(full, "optimize_portfolio", return_value=output["result"]), patch.object(full, "evaluate_portfolio") as baseline:
            with self.assertRaisesRegex(ValueError, "aporte mínimo Final Tick 6M"):
                full.generate_full_history_improvement(source, 1, {**output["inputs"], "improvement_additions": 1})
        baseline.assert_not_called()

    def test_zero_threshold_and_invalid_mode(self):
        self.assertEqual(full.improvement_options({"improvement_min_efficiency_gain_pct": 0}).min_efficiency_gain_pct, 0)
        with self.assertRaisesRegex(ValueError, "Elige la variante"):
            full.generate_full_history_improvement(object(), 1, {"improvement_portfolio_type": "other"})

    def test_missing_variant_never_falls_back_to_other_members(self):
        with self.assertRaisesRegex(ValueError, "No hay estrategias"):
            full._selected_variant_detail({"portfolio_type": "bundle", "members": [{"variant_key": "aggressive", "units": 1}]}, "balanced")

    def test_save_creates_an_identified_portfolio_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "outputs").mkdir()
            (root / "outputs/ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {"id": "ic", "portfolio_project_dir": str(root), "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD"}
            source = PortfolioSource(node)
            coordinator = PortfolioCoordinator([node], root / "settings.json")
            state_key = coordinator._key("ic", "full_history")
            coordinator.proposals[state_key] = [proposal(mode) for mode in full.PORTFOLIO_TYPES]
            coordinator.jobs[state_key] = {"operation": "generate"}
            original_id = save_portfolio_payload(source, coordinator.prepare_save("ic", "full_history", "balanced"))["portfolio_id"]
            before = source.saved_portfolio_detail(original_id, "full_history")["portfolio"]
            coordinator.proposals[state_key] = [proposal("conservative", original_id)]
            coordinator.jobs[state_key] = {"operation": "improve", "portfolio_id": original_id}
            payload = coordinator.prepare_save("ic", "full_history", "conservative")
            saved = save_portfolio_payload(source, payload)
            retry = save_portfolio_payload(source, payload)
            self.assertNotEqual(saved["portfolio_id"], original_id)
            self.assertTrue(retry["deduplicated"])
            self.assertEqual(saved["portfolio_id"], retry["portfolio_id"])
            self.assertEqual(before, source.saved_portfolio_detail(original_id, "full_history")["portfolio"])
            new = source.saved_portfolio_detail(saved["portfolio_id"], "full_history")["portfolio"]
            self.assertIn(f"Mejora de #{original_id} | Conservador", new["name"])
            self.assertEqual(new["portfolio_type"], "conservative")
            self.assertFalse(new["metrics"].get("portfolio_bundle", False))
            self.assertEqual(new["metrics"]["inputs"]["improvement_source_portfolio_id"], original_id)
