import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from mt5_manager import portfolio_improvement_service as full
from mt5_manager.portfolio_service import (
    PortfolioCoordinator, PortfolioSource, normalize_settings, save_portfolio_payload,
)
from portfolio_manager.ubs_portfolio import (
    BootstrapDrawdownAnalysis,
    PortfolioResult,
    StrategyAllocation,
)


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


def stress(*, p95, probability):
    return BootstrapDrawdownAnalysis(
        method="moving_block_bootstrap",
        simulations=1000,
        seed=17,
        observations=24,
        block_size=4,
        valley_dd_p50=p95 / 2,
        valley_dd_p95=p95,
        nominal_valley_dd_limit=100,
        effective_valley_dd_limit=90,
        probability_exceed_nominal_pct=max(probability - 2, 0),
        probability_exceed_effective_pct=probability,
        alert=probability > 10,
    )


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
                self.assertEqual(audit["source_snapshot"]["id"], 42)
                self.assertEqual(audit["source_snapshot"]["total_net_profit"], 100)
                self.assertEqual(len(audit["source_snapshot"]["members"]), 1)
                self.assertEqual(audit["source_snapshot"]["members"][0]["variant_key"], mode)

    def test_minimum_two_can_select_three_and_never_tries_one(self):
        def attempt(_source, _id, inputs, _progress):
            count = inputs["improvement_additions"]
            return {"improvement": {"actual_additions": count}}, [proposal(gain=20 if count == 3 else 4)]
        with patch.object(full, "_generate_full_history_improvement_attempt", side_effect=attempt) as search:
            availability, result = full.generate_full_history_improvement(object(), 1, {"improvement_min_additions": 2})
        self.assertEqual([call.args[2]["improvement_additions"] for call in search.call_args_list], [2, 3, 4, 5])
        self.assertEqual(availability["improvement"]["actual_additions"], 3)
        self.assertEqual(availability["improvement"]["minimum_additions"], 2)
        self.assertEqual(result[0]["inputs"]["improvement_min_additions"], 2)
        self.assertEqual(result[0]["result"].seasonal_validation["portfolio_improvement"]["minimum_additions"], 2)
        self.assertEqual(result[0]["result"].seasonal_validation["portfolio_improvement"]["efficiency_gain_pct"], 20)

    def test_invalid_minimum_is_rejected_before_searching(self):
        for value in (0, -1, 6, 2.5, None, True, "bad"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "mínimo.*entero entre 1 y 5"):
                full.generate_full_history_improvement(object(), 1, {"improvement_min_additions": value})

    def test_selector_cannot_return_one_when_minimum_is_two(self):
        output = proposal()
        old_id = output["result"].allocations[0].set_id
        detail = {"portfolio_type": "balanced", "members": [{"set_path": old_id, "units": 1}]}
        source = NS(project=Path.cwd(), saved_portfolio_detail=Mock(return_value={"portfolio": detail}), saved_curves=Mock(return_value=[]))
        sets = [NS(set_id=a.set_id) for a in output["result"].allocations] + [NS(set_id="another.set")]
        with patch.object(full, "_load_full_history_improvement_pool", return_value=(sets[:1], sets, [], [], [])), patch.object(full, "build_margin_model", return_value=None), patch.object(full, "optimize_portfolio", return_value=output["result"]) as optimize, patch.object(full, "evaluate_portfolio") as baseline:
            with self.assertRaisesRegex(ValueError, "al menos 2.*selector añadió 1"):
                full.generate_full_history_improvement(source, 1, {**output["inputs"], "improvement_min_additions": 2})
        self.assertEqual(optimize.call_count, 1)
        baseline.assert_not_called()

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

    def test_invalid_selection_priority_is_rejected_before_searching(self):
        with self.assertRaisesRegex(ValueError, "prioridad de mejora"):
            full.generate_full_history_improvement(
                object(), 1, {"improvement_selection_priority": "hidden-limit"}
            )

    def test_stress_comparison_is_informative_and_keeps_acceptance(self):
        output = proposal()
        result = output["result"]
        result.stress_bootstrap = stress(p95=85, probability=32)
        audit = result.seasonal_validation["portfolio_improvement"]
        audit["verdict"] = "ACEPTADA"
        baseline = NS(equity_curve_2020_2026=[0, 5, 2, 8])

        with patch.object(
            full, "bootstrap_valley_drawdown", return_value=stress(p95=66, probability=7)
        ):
            full._attach_stress_comparison(
                result=result, baseline=baseline, priority="balanced"
            )

        comparison = audit["stress_comparison"]
        self.assertEqual(comparison["direction"], "higher")
        self.assertEqual(comparison["valley_dd_p95_delta"], 19)
        self.assertEqual(comparison["probability_exceed_effective_delta_pp"], 25)
        self.assertEqual(audit["verdict"], "ACEPTADA")
        self.assertIn("Dato informativo", result.warnings[-1])
        self.assertIn("validez sigue determinada por los límites declarados", result.warnings[-1])

    def test_selection_priorities_rank_valid_proposals_without_new_rejections(self):
        calm = proposal(gain=8)
        efficient = proposal(gain=25)
        for item, probability, delta, p95 in (
            (calm, 6, -1, 60),
            (efficient, 30, 23, 85),
        ):
            item["result"].seasonal_validation["portfolio_improvement"]["stress_comparison"] = {
                "status": "completed",
                "improved": {
                    "probability_exceed_effective_pct": probability,
                    "valley_dd_p95": p95,
                },
                "probability_exceed_effective_delta_pp": delta,
            }

        self.assertGreater(
            full._improvement_rank(calm, 2, "balanced"),
            full._improvement_rank(efficient, 2, "balanced"),
        )
        self.assertGreater(
            full._improvement_rank(calm, 2, "stress"),
            full._improvement_rank(efficient, 2, "stress"),
        )
        self.assertGreater(
            full._improvement_rank(efficient, 2, "efficiency"),
            full._improvement_rank(calm, 2, "efficiency"),
        )

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
            # Simulate an older node that retained provenance but generated a
            # generic name. Reading recovers identity without changing SQLite.
            with source.connect(write=True) as conn:
                conn.execute("update portfolios set name='A/M/C antiguo' where id=?", (saved["portfolio_id"],))
                conn.commit()
            new = source.saved_portfolio_detail(saved["portfolio_id"], "full_history")["portfolio"]
            self.assertIn(f"Mejora del portafolio #{original_id} | modo Conservador", new["name"])
            self.assertEqual(new["improvement_origin"], {"source_id": original_id, "mode": "conservative"})
            self.assertEqual(new["portfolio_type"], "conservative")
            self.assertFalse(new["metrics"].get("portfolio_bundle", False))
            self.assertEqual(new["metrics"]["inputs"]["improvement_source_portfolio_id"], original_id)
            with source.connect() as conn:
                self.assertEqual(conn.execute("select name from portfolios where id=?", (saved["portfolio_id"],)).fetchone()[0], "A/M/C antiguo")
