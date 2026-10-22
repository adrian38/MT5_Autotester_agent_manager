"""Recuperacion manual de huecos del cron de auditorias."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_watch, manager_schedule_routes
from mt5_manager.live_audit_watch import LiveAuditRunState
from tests.live_audit.live_audit_cron_base import FakeServer, audit_state, published_audit


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
        record = reopened.record(key)
        self.assertEqual(record["catch_up_until"], "2026-10-17")
        self.assertEqual(record["catch_up_cutoff"], "2026-10-17T21:00:00+00:00")

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

    def test_a_use_created_that_same_night_after_eleven_is_not_backfilled(self) -> None:
        # El #172 nació a las 23:23:26 de Madrid: compartir el día 17 con la
        # cita de las 23:00 no lo convierte en elegible para esa cita.
        audit_id = "audit-172-mvcwlhi2-1"
        state = audit_state(
            configured_audit_ids=[audit_id], live_audit_ids=[audit_id],
            profiles={audit_id: {
                "portfolio_id": 172, "deployment_name": "Posterior al corte",
                "live_trading": True,
            }},
        )
        server = FakeServer(self.root, state)
        result = live_audit_watch.request_catch_up(server, self.now)
        self.assertEqual(result["queued_count"], 0)
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", audit_id))
        self.assertEqual(record["catch_up_status"], "not_applicable")

    def test_the_first_recovery_marker_is_migrated_from_its_exact_window(self) -> None:
        key = LiveAuditRunState.key("n", "11")
        path = self.root / "cron.json"
        path.write_text(
            '{"audits":{"n/11":{"window":"2026-10-17T23:00:00+02:00",'
            '"catch_up_until":"","catch_up_status":"success",'
            '"catch_up_requested_at":"2026-10-20T13:36:06+00:00"}}}',
            encoding="utf-8",
        )
        reopened = LiveAuditRunState(path)
        record = reopened.record(key)
        self.assertEqual(record["catch_up_until"], "2026-10-17")
        self.assertEqual(record["catch_up_cutoff"], "2026-10-17T21:00:00+00:00")
        self.assertEqual(LiveAuditRunState(path).record(key), record)

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

    def test_a_portfolio_born_after_the_cutoff_has_no_gap_to_recover(self) -> None:
        # No se puede auditar un portafolio antes de que exista: el corte es
        # el 17 y éste nació el 18, así que no hay nada que recuperar.
        server = FakeServer(self.root, audit_state(), BornOn("2026-10-18T09:00:00"))
        result = live_audit_watch.request_catch_up(server, self.now)
        self.assertEqual(result["queued_count"], 0)
        # Queda apuntado con el corte de la tanda, no borrado: así el diálogo
        # dice por qué se quedó fuera y el contador no lo cuenta por lanzar.
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", "11"))
        self.assertEqual(
            (record["catch_up_until"], record["catch_up_status"]),
            ("2026-10-17", "not_applicable"),
        )

    def test_without_history_the_recovery_starts_the_day_it_was_created(self) -> None:
        # El perfil pide los últimos 7 días -el 10 de octubre-, pero el
        # portafolio es del 14: antes no tiene operaciones reales con las que
        # comparar al tester. Es lo que pasó con el #109 de AXI.
        server = FakeServer(self.root, audit_state(), BornOn("2026-10-14T18:00:00"))
        live_audit_watch.request_catch_up(server, self.now)
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=live_audit_watch_test_answers({"11": {"status": "idle"}}, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual(len(posted), 1)
        _path, payload = posted[0]
        self.assertEqual(payload["period_mode"], "fixed_dates")
        self.assertEqual(payload["period_start_date"], "2026-10-14")
        self.assertEqual(payload["period_end_date"], "2026-10-17")
        self.assertIn("desde que se creó el portafolio", server.events()[0]["message"])

    def test_a_completed_recovery_stops_being_pending_but_keeps_its_mark(self) -> None:
        # El corte se conserva a propósito: es el denominador del «2 de 5» que
        # enseña la barra, y borrarlo dejaba la tanda sin forma de contarse.
        server = FakeServer(self.root, audit_state())
        live_audit_watch.request_catch_up(server, self.now)
        covered = published_audit(period_end="2026-10-17T23:59:59+00:00")
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=live_audit_watch_test_answers({"11": covered}),
        ):
            live_audit_watch.tick(server)
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", "11"))
        self.assertEqual(
            (record["catch_up_until"], record["catch_up_status"]), ("2026-10-17", "success"),
        )
        # Y no se vuelve a lanzar en la vuelta siguiente.
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=live_audit_watch_test_answers({"11": covered}, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual(posted, [])


class BornOn:
    """La memoria del agente contestando cuándo nació el portafolio."""

    def __init__(self, created_at: str) -> None:
        self.created_at = created_at

    def saved(self, _node_id: str, _scope: str, _portfolio_id: int) -> dict[str, object]:
        return {"portfolio": {"created_at": self.created_at}}


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
