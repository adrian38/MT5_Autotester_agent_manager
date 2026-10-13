from __future__ import annotations

import base64
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.live_audit_report_upload import replace_tester_reports
from mt5_manager.manager_live_audit_routes import _handle_manual_reports
from tests.test_live_audit_analysis import payload as analysis_payload, profile


SOURCE = "XAUUSD_H1_GOLD_Old_Optimizatio_c778ab20_g001_s016_v002.set"
EXPECTED = SOURCE.removesuffix(".set") + ".htm"
STRATEGY = "AXI/STANDARD:3535"


class Controller:
    def __init__(self, root: Path) -> None:
        self.lock = threading.RLock()
        self.runtime_dir = root / "live_audits"
        self.runtime_dir.mkdir()
        self.persisted = 0
        self.states = {"audit-105": {
            "audit_key": "audit-105", "audit_id": "run-1", "status": "completed",
            "last_result": {"stale": True}, "log_lines": [],
            "last_payload": {
                "audit_id": "run-1", "tester_trades": [{"strategy": STRATEGY, "profit": -1}],
                "qualities": [40.0], "strategies": {STRATEGY: 1},
                "selected_members": [{"candidate_id": STRATEGY, "timeframe": "H1"}],
                "strategy_artifacts": [{
                    "strategy": STRATEGY, "symbol": "XAUUSD", "report_symbol": "XAUUSD",
                    "source_set": SOURCE, "runtime_start_lots": 0.06,
                    "tester_trades": 1, "history_quality_pct": 40.0,
                }],
            },
        }}

    def _tester_trade_rows(self, report, strategy):
        return [{
            "strategy": strategy, "symbol": report.symbol, "side": "buy",
            "open_time": datetime(2026, 10, 2, tzinfo=timezone.utc),
            "close_time": datetime(2026, 10, 3, tzinfo=timezone.utc),
            "open_price": 1.0, "close_price": 2.0, "volume": 0.06, "profit": 3.0,
        }]

    def _persist(self):
        self.persisted += 1

    def state(self, audit_key):
        return dict(self.states[str(audit_key)])


def report(quality="100% real ticks"):
    return SimpleNamespace(
        symbol="XAUUSD", timeframe="H1", trades=[SimpleNamespace(size=0.06)],
        metrics={"History Quality": quality},
    )


def body(filename=EXPECTED):
    return {"reports": [{
        "filename": filename,
        "content_base64": base64.b64encode(b"<html>MT5</html>").decode("ascii"),
    }]}


class ManualAuditReportTests(unittest.TestCase):
    def test_a_100_pct_report_replaces_the_strategy_and_invalidates_the_old_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch(
            "mt5_manager.live_audit_report_upload.parse_report", return_value=report(),
        ):
            controller = Controller(Path(temp))
            state = replace_tester_reports(controller, "audit-105", body())

        payload = state["last_payload"]
        artifact = payload["strategy_artifacts"][0]
        self.assertEqual(artifact["history_quality_pct"], 100.0)
        self.assertEqual(artifact["report_source"], "manual_upload")
        self.assertEqual(payload["tester_trades"][0]["profit"], 3.0)
        self.assertEqual(payload["qualities"], [100.0])
        self.assertIsNone(state["last_result"])
        self.assertEqual(controller.persisted, 1)

    def test_a_prefixed_filename_is_rejected_to_avoid_guessing_the_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "no coincide con ningún set"):
                replace_tester_reports(
                    Controller(Path(temp)), "audit-105", body("cuenta_017_" + EXPECTED),
                )

    def test_a_report_below_100_pct_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch(
            "mt5_manager.live_audit_report_upload.parse_report", return_value=report("99% real ticks"),
        ):
            controller = Controller(Path(temp))
            with self.assertRaisesRegex(ValueError, "debe ser 100%"):
                replace_tester_reports(controller, "audit-105", body())
        self.assertEqual(controller.persisted, 0)

    def test_the_manager_proxies_the_upload_and_returns_the_recalculated_result(self) -> None:
        class Handler:
            def __init__(self):
                settings = SimpleNamespace(state=lambda _node: {"profiles": {"audit-105": profile()}})
                self.server = SimpleNamespace(live_audit_settings=settings)
                self.response = None

            def _node(self, _node_id):
                return {"id": "axi"}

            def _body(self):
                return body()

            def _send_json(self, status, value):
                self.response = (status, value)

        endpoint = Handler()
        with patch(
            "mt5_manager.manager_live_audit_routes.manager_http.node_request",
            return_value=(200, {"audit": {"last_payload": analysis_payload()}}),
        ) as request:
            handled = _handle_manual_reports(
                endpoint, ["api", "nodes", "axi", "live-audits", "audit-105", "reports"],
            )

        self.assertTrue(handled)
        self.assertEqual(endpoint.response[0], 200)
        self.assertEqual(endpoint.response[1]["audit"]["last_result"]["analysis"]["analysed_by"], "manager")
        self.assertEqual(request.call_args.args[2], "/api/v1/live-audits/audit-105/reports")

        with patch(
            "mt5_manager.manager_live_audit_routes.manager_http.node_request",
            return_value=(422, {"error": "reporte inválido"}),
        ):
            _handle_manual_reports(
                endpoint, ["api", "nodes", "axi", "live-audits", "audit-105", "reports"],
            )
        self.assertEqual(endpoint.response, (422, {"error": "reporte inválido"}))


if __name__ == "__main__":
    unittest.main()
