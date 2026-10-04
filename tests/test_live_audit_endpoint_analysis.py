from __future__ import annotations

import unittest
from types import SimpleNamespace

from mt5_manager.manager_live_audit_routes import analysed_live_audit
from tests.test_live_audit_analysis import payload, profile


class Endpoint:
    def __init__(self, profiles: dict) -> None:
        settings = SimpleNamespace(state=lambda _node_id: {"profiles": profiles})
        self.server = SimpleNamespace(live_audit_settings=settings)

    def _analysed_live_audit(self, node_id: str, audit_id: str, value):
        return analysed_live_audit(self, node_id, audit_id, value)


def handler(profiles: dict) -> Endpoint:
    return Endpoint(profiles)


class LiveAuditEndpointAnalysisTests(unittest.TestCase):
    def test_the_manager_analyses_the_payload_with_the_current_profile(self) -> None:
        node_state = {"audit": {"audit_id": "x", "last_payload": payload()}}
        analysed = handler(
            {"audit-148": profile(real_strategy_lots={"ROBOFOREX/ECN:27672": 0.04})}
        )._analysed_live_audit("nodo", "audit-148", node_state)

        result = analysed["audit"]["last_result"]
        self.assertEqual(result["within_tolerance_trades"], 1)
        self.assertEqual(result["analysis"]["analysed_by"], "manager")

    def test_changing_a_tolerance_changes_the_verdict_without_running_again(self) -> None:
        # Es la razón de ser del reparto nuevo: mismo payload, dos criterios.
        strict = handler({"audit-148": profile()})._analysed_live_audit(
            "nodo", "audit-148", {"audit": {"last_payload": payload()}},
        )
        loose = handler(
            {"audit-148": profile(real_strategy_lots={"ROBOFOREX/ECN:27672": 0.04})}
        )._analysed_live_audit("nodo", "audit-148", {"audit": {"last_payload": payload()}})

        self.assertEqual(strict["audit"]["last_result"]["within_tolerance_trades"], 0)
        self.assertEqual(loose["audit"]["last_result"]["within_tolerance_trades"], 1)

    def test_the_raw_material_is_not_forwarded_to_the_browser(self) -> None:
        analysed = handler({"audit-148": profile()})._analysed_live_audit(
            "nodo", "audit-148", {"audit": {"last_payload": payload()}},
        )
        self.assertNotIn("last_payload", analysed["audit"])

    def test_a_node_without_the_port_keeps_showing_its_own_result(self) -> None:
        # AXI y RoboForex siguen con el reparto antiguo hasta que el usuario
        # porte el commit: su veredicto se muestra tal cual en vez de dejar la
        # pantalla en blanco.
        legacy = {"audit": {"last_result": {"audit_id": "viejo", "status": "completed"}}}
        analysed = handler({})._analysed_live_audit("nodo", "audit-148", legacy)

        self.assertEqual(analysed["audit"]["last_result"]["audit_id"], "viejo")
        self.assertNotIn("analysis_error", analysed["audit"])

    def test_unanalysable_material_reports_the_error_instead_of_a_verdict(self) -> None:
        broken = {"audit": {"last_payload": {**payload(), "period_start": "no es una fecha"}}}
        analysed = handler({"audit-148": profile()})._analysed_live_audit(
            "nodo", "audit-148", broken,
        )

        self.assertIsNone(analysed["audit"]["last_result"])
        self.assertIn("no es una fecha", analysed["audit"]["analysis_error"])

    def test_a_flat_state_without_the_audit_wrapper_is_accepted(self) -> None:
        analysed = handler({"audit-148": profile()})._analysed_live_audit(
            "nodo", "audit-148", {"last_payload": payload()},
        )
        self.assertEqual(analysed["last_result"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
