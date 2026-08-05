import unittest

from mt5_manager.portfolio_service import (
    deserialize_portfolio_proposals,
    legacy_compatible_portfolio_save_payload,
    serialize_portfolio_proposals,
)
from portfolio_manager.ubs_portfolio import (
    OptimizationDecision,
    PortfolioResult,
    StrategyAllocation,
)


def proposal_result() -> PortfolioResult:
    allocation = StrategyAllocation(
        set_id="a.set",
        candidate_id="ROBOFOREX/ECN:1",
        symbol="EURUSD",
        units=2,
        lot=0.02,
        net_profit_contribution=100.0,
        standalone_valley_dd=80.0,
        standalone_point_dd=20.0,
        max_balance_dd_001=10.0,
        max_equity_dd_001=25.0,
        floating_dd_source="2020-2024",
        standalone_floating_dd=30.0,
        recent_net_profit_001=12.0,
        recent_equity_dd_001=8.0,
        has_recent_performance=True,
    )
    return PortfolioResult(
        allocations=[allocation],
        equity_curve_2020_2026=[0.0, 100.0],
        total_net_profit=100.0,
        actual_valley_dd=80.0,
        actual_point_dd=20.0,
        target_valley_dd=225.0,
        target_point_dd=225.0,
        valley_usage_pct=35.5,
        point_usage_pct=8.8,
        total_lot=0.02,
        total_units=2,
        active_strategies=1,
        stop_reason="ok",
        warnings=[],
        decision_log=[],
        actual_closed_valley_dd=50.0,
        floating_dd_buffer=30.0,
    )


class PortfolioWireCompatibilityTests(unittest.TestCase):
    def test_wire_payload_replaces_non_finite_decision_scores_for_old_nodes(self) -> None:
        result = proposal_result()
        result.decision_log = [OptimizationDecision(
            step=1, action="reject", set_id="a.set", from_set_id=None, to_set_id=None,
            gain=0.0, valley_cost=0.0, point_cost=0.0, score=float("-inf"),
            portfolio_net_profit_after=100.0,
            portfolio_valley_dd_after=80.0,
            portfolio_point_dd_after=20.0,
            reason="sin mejora",
        )]

        proposals = serialize_portfolio_proposals(
            [{"key": "aggressive", "label": "Agresivo Grid", "reserve_pct": 10,
              "inputs": {"capital": 1000, "valley_dd_pct": 30}, "result": result}],
            "finite-wire",
        )

        self.assertEqual(proposals[0]["result"]["decision_log"][0]["score"], 0.0)

    def test_legacy_payload_keeps_enforced_risk_and_drops_only_new_audit_fields(self) -> None:
        proposals = serialize_portfolio_proposals(
            [{"key": "balanced", "label": "Moderado", "reserve_pct": 15,
              "inputs": {"capital": 5000, "valley_dd_pct": 6}, "result": proposal_result()}],
            "request-1",
        )
        payload = {"scope": "full_history", "request_id": "request-1", "proposals": proposals}

        legacy = legacy_compatible_portfolio_save_payload(payload)
        result = legacy["proposals"][0]["result"]
        allocation = result["allocations"][0]

        self.assertEqual(result["actual_valley_dd"], 80.0)
        self.assertNotIn("actual_closed_valley_dd", result)
        self.assertEqual(allocation["standalone_valley_dd"], 80.0)
        self.assertNotIn("max_balance_dd_001", allocation)

    def test_deserializer_ignores_fields_from_a_newer_manager(self) -> None:
        proposals = serialize_portfolio_proposals(
            [{"key": "balanced", "label": "Moderado", "reserve_pct": 15,
              "inputs": {"capital": 5000, "valley_dd_pct": 6}, "result": proposal_result()}],
            "request-2",
        )
        proposals[0]["result"]["future_result_field"] = "ignored"
        proposals[0]["result"]["allocations"][0]["future_allocation_field"] = "ignored"

        restored = deserialize_portfolio_proposals(proposals, "full_history", "ROBOFOREX")

        self.assertEqual(restored[0]["result"].actual_valley_dd, 80.0)
        self.assertEqual(restored[0]["result"].allocations[0].max_equity_dd_001, 25.0)

    def test_grid_minimum_executable_valley_survives_the_wire_round_trip(self) -> None:
        proposals = serialize_portfolio_proposals(
            [{
                "key": "aggressive", "label": "Agresivo Grid", "reserve_pct": 10,
                "auto_adjusted_valley": True,
                "requested_valley_dd_pct": 3.0,
                "adjusted_valley_dd_pct": 3.4533334,
                "inputs": {"capital": 1000, "valley_dd_pct": 3.4533334},
                "result": proposal_result(),
            }],
            "request-grid",
        )

        restored = deserialize_portfolio_proposals(proposals, "grid", "ROBOFOREX")

        self.assertTrue(restored[0]["auto_adjusted_valley"])
        self.assertEqual(restored[0]["requested_valley_dd_pct"], 3.0)
        self.assertAlmostEqual(restored[0]["adjusted_valley_dd_pct"], 3.4533334)
        self.assertAlmostEqual(restored[0]["inputs"]["valley_dd_pct"], 3.4533334)


if __name__ == "__main__":
    unittest.main()
