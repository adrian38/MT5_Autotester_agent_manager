from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.live_audit_watch import LiveAuditRunState
from mt5_manager.manager_live_audit_routes import (
    analysed_live_audit, dashboard_node_state, dashboard_nodes, run_payload,
)
from tests.test_live_audit_autolot import STRATEGY, audit_payload
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
    def test_dashboard_lists_nodes_without_polling_them(self) -> None:
        endpoint = SimpleNamespace(server=SimpleNamespace(nodes=[
            {"id": "ic", "name": "ICTrading", "url": "http://slow"},
        ]))
        self.assertEqual(
            dashboard_nodes(endpoint), {"nodes": [{"id": "ic", "name": "ICTrading"}]},
        )

    def test_dashboard_reads_all_live_results_in_one_node_request(self) -> None:
        configured = {**profile(), "live_trading": True}
        settings = SimpleNamespace(state=lambda _node_id: {
            "live_audit_ids": ["audit-148"], "profiles": {"audit-148": configured},
        })
        endpoint = SimpleNamespace(server=SimpleNamespace(live_audit_settings=settings))
        raw = {"audits": {"audit-148": {"last_payload": payload()}}}
        with patch("mt5_manager.manager_http.node_request", return_value=(200, raw)) as request:
            state = dashboard_node_state(endpoint, "ic", {"id": "ic", "name": "ICTrading"})
        request.assert_called_once_with(
            {"id": "ic", "name": "ICTrading"}, "GET", "/api/v1/live-audits", timeout=10,
        )
        self.assertEqual(state["entries"][0]["audit"]["last_result"]["status"], "completed")
        self.assertNotIn("last_payload", state["entries"][0]["audit"])

    def test_the_dashboard_carries_the_degradation_the_watcher_measured(self) -> None:
        # La tarjeta no puede abrir la memoria UBS del agente en cada sondeo:
        # el veredicto «contra el histórico» viaja ya medido en el payload.
        configured = {**profile(), "live_trading": True}
        settings = SimpleNamespace(state=lambda _node_id: {
            "live_audit_ids": ["audit-148"], "profiles": {"audit-148": configured},
        })
        with tempfile.TemporaryDirectory() as root:
            runs = LiveAuditRunState(Path(root) / "cron.json")
            runs.update(
                runs.key("ic", "audit-148"), degradation_execution="run-1",
                degradation=[{"strategy": "X:1", "symbol": "EURUSD", "verdict": "degraded"}],
                degradation_watching=2, degradation_compared=5,
                degradation_checked_at="2026-10-20T00:00:00+00:00",
            )
            endpoint = SimpleNamespace(server=SimpleNamespace(
                live_audit_settings=settings, live_audit_runs=runs,
            ))
            raw = {"audits": {"audit-148": {"last_payload": payload()}}}
            with patch("mt5_manager.manager_http.node_request", return_value=(200, raw)):
                state = dashboard_node_state(endpoint, "ic", {"id": "ic", "name": "ICTrading"})
        degradation = state["entries"][0]["degradation"]
        self.assertEqual(degradation["strategies"][0]["symbol"], "EURUSD")
        self.assertEqual(degradation["watching"], 2)
        self.assertEqual(degradation["compared"], 5)

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

    def test_autolot_history_is_added_by_the_manager_before_analysis(self) -> None:
        configured = profile(autolot={
            "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
            "rules": {STRATEGY: {"dd_001": 10.0, "risk_pct": 10.0}},
        })
        history = [
            {"time": "2026-09-22T09:00:00+00:00", "balance": 1000.0},
            {"time": "2026-09-22T10:00:00+00:00", "balance": 2000.0},
        ]
        with patch(
            "mt5_manager.live_audit_executions.balance_history",
            return_value=history,
        ):
            analysed = analysed_live_audit(
                handler({"audit-148": configured}), "nodo", "audit-148",
                {"audit": {"last_payload": audit_payload()}}, {},
            )
        rows = analysed["audit"]["last_result"]["comparison_detail"]["operation_comparisons"]
        self.assertEqual([row["limits"]["volume_expected_real"] for row in rows], [0.1, 0.2])

    def test_manager_only_configuration_never_leaks_into_the_node_request(self) -> None:
        configured = {**profile(), "portfolio_id": 148, "live_trading": True, "autolot": {
            "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
            "rules": {STRATEGY: {"dd_001": 10.0, "risk_pct": 10.0}},
        }, "view_lot_overrides": {"$mode": "autolot"},
            "reviewed_strategies": {STRATEGY: {"verdict": "AVISO", "at": ""}}}
        settings = SimpleNamespace(
            state=lambda _node_id: {
                "configured_audit_ids": ["audit-148"], "profiles": {"audit-148": configured},
            },
            credentials=lambda *_args: {"source_password": "real", "tester_password": "test"},
            restore_credentials=lambda _node_id: {
                "restore_login": "1", "restore_server": "Broker", "restore_password": "secret",
            },
        )
        endpoint = SimpleNamespace(server=SimpleNamespace(live_audit_settings=settings))
        with patch(
            "mt5_manager.manager_http.node_request",
            return_value=(200, {"capabilities": {"live_audit_restore_account": True}}),
        ):
            request = run_payload(endpoint, "nodo", {}, "audit-148")
        # `view_lot_overrides` es escalado de la vista, `reviewed_strategies`
        # es qué ha mirado ya el usuario y `manual_pairs` qué real le tocaba a
        # qué operación: el nodo audita con los lotes configurados y no tiene
        # por qué saber cómo se dibuja después, qué filas siguen pidiendo
        # atención en la portada ni qué parejas corrigió el usuario.
        for manager_only in (
            "autolot", "real_attribution", "live_trading",
            "view_lot_overrides", "reviewed_strategies", "reviewed_open_positions",
            "manual_pairs",
        ):
            self.assertNotIn(manager_only, request)


if __name__ == "__main__":
    unittest.main()
