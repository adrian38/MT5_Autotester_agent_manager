"""Recuperacion manual de huecos del cron de auditorias."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_audit_watch, manager_schedule_routes
from mt5_manager.live_audit_watch import LiveAuditRunState
from tests.test_live_audit_cron import FakeServer, audit_state, published_audit


class CatchUpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def test_the_button_persists_the_last_expired_weekly_cutoff(self) -> None:
        server = FakeServer(self.root, audit_state())
        result = live_audit_watch.request_catch_up(server, self.now)
        self.assertEqual((result["target_date"], result["queued_count"]), ("2026-10-17", 1))
        key = LiveAuditRunState.key("n", "11")
        reopened = LiveAuditRunState(self.root / "cron.json")
        self.assertEqual(reopened.record(key)["catch_up_until"], "2026-10-17")

    def test_the_http_route_queues_and_wakes_the_watcher(self) -> None:
        server = FakeServer(self.root, audit_state())
        server.live_audit_wakeup = threading.Event()
        handler = CatchUpHandler(server)
        parsed = type("Parsed", (), {"path": manager_schedule_routes.CATCH_UP_PATH})()
        with mock.patch("mt5_manager.live_audit_catch_up.datetime") as clock:
            clock.now.return_value = self.now
            clock.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            handled = manager_schedule_routes.handle_post(handler, parsed, [])
        self.assertTrue(handled)
        self.assertEqual((handler.status, handler.payload["target_date"]), (202, "2026-10-17"))
        self.assertTrue(server.live_audit_wakeup.is_set())

    def test_a_use_created_after_the_cutoff_without_history_is_not_backfilled(self) -> None:
        audit_id = "audit-170-mvdscovr-2"  # creado el 18 de octubre
        state = audit_state(
            configured_audit_ids=[audit_id], live_audit_ids=[audit_id],
            profiles={audit_id: {
                "portfolio_id": 170, "deployment_name": "Nueva",
                "live_trading": True,
            }},
        )
        server = FakeServer(self.root, state)
        live_audit_watch.request_catch_up(server, self.now)
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=live_audit_watch_test_answers({audit_id: {"status": "idle"}}),
        ):
            live_audit_watch.tick(server)
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", audit_id))
        self.assertEqual(record["catch_up_status"], "not_applicable")

    def test_recovery_runs_with_the_switch_off_and_closes_the_real_gap(self) -> None:
        server = FakeServer(self.root, audit_state())
        live_audit_watch.request_catch_up(server, self.now)
        posted: list[object] = []
        old = published_audit(period_end="2026-10-04T23:59:59+00:00")
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=live_audit_watch_test_answers({"11": old}, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual(len(posted), 1)
        path, payload = posted[0]
        self.assertEqual(path, "/api/v1/live-audits/11/run")
        self.assertEqual(payload["period_start_date"], "2026-10-04")
        self.assertEqual(payload["period_end_date"], "2026-10-17")
        self.assertIn("Recuperación lanzada", server.events()[0]["title"])

    def test_a_completed_recovery_is_removed_from_the_queue(self) -> None:
        server = FakeServer(self.root, audit_state())
        live_audit_watch.request_catch_up(server, self.now)
        covered = published_audit(period_end="2026-10-17T23:59:59+00:00")
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=live_audit_watch_test_answers({"11": covered}),
        ):
            live_audit_watch.tick(server)
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", "11"))
        self.assertEqual((record["catch_up_until"], record["catch_up_status"]), ("", "success"))


def live_audit_watch_test_answers(audits: dict[str, object],
                                  posted: list[object] | None = None):
    def request(_node, method, path, payload=None, timeout=0):
        if path == "/api/v1/status":
            return 200, {"capabilities": {"live_audit_restore_account": True}}
        if method == "GET":
            return 200, {"audits": audits}
        if posted is not None:
            posted.append((path, payload))
        return 202, {"audit": {"status": "queued"}}
    return request


class CatchUpHandler:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.status = 0
        self.payload: dict[str, object] = {}

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        self.status, self.payload = status, payload


if __name__ == "__main__":
    unittest.main()
