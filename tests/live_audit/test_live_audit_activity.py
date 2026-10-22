"""Qué se está ejecutando ahora y cuánto le queda.

La petición del usuario (2026-10-20): el cron lanza un uso por agente y sigue
solo, así que entre el clic y el aviso de «terminada» la barra superior estaba
igual de quieta que cuando no pasa nada.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_activity, live_audit_watch, manager_schedule_routes
from mt5_manager.live_audit_watch import LiveAuditRunState
from tests.live_audit.live_audit_cron_base import FakeServer, audit_state


def answers(audits: dict[str, object], broken: bool = False):
    def request(_node, _method, path, _payload=None, timeout=0):
        if broken:
            raise TimeoutError("sin red")
        if path == "/api/v1/status":
            return 200, {"capabilities": {"live_audit_restore_account": True}}
        return 200, {"audits": audits}
    return request


class ActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def _snapshot(self, audits: dict[str, object], broken: bool = False) -> dict[str, object]:
        with mock.patch(
            "mt5_manager.manager_http.node_request", side_effect=answers(audits, broken),
        ):
            return live_audit_activity.snapshot(self.server)

    def test_nothing_running_and_nothing_queued_shows_nothing(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        state = self._snapshot({"11": {"status": "idle"}})
        self.assertFalse(state["active"])
        self.assertEqual(state["running"], [])
        self.assertEqual(state["recovery"], {})

    def test_a_running_use_carries_its_phase_and_percentage(self) -> None:
        # El porcentaje sale de `PROGRESS`, que es el mismo que pinta la
        # pantalla de la auditoría: dos cuentas distintas no pueden coexistir.
        self.server = FakeServer(self.root, audit_state())
        state = self._snapshot({"11": {
            "status": "testing", "progress_text": "Probando 3 de 4",
            "started_at": "2026-10-20T11:00:00+00:00",
        }})
        self.assertTrue(state["active"])
        row = state["running"][0]
        self.assertEqual(
            (row["status"], row["status_label"], row["percent"]), ("testing", "PROBANDO", 55),
        )
        self.assertEqual((row["node_name"], row["progress_text"]), ("Nodo N", "Probando 3 de 4"))

    def test_a_finished_use_is_not_activity(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        self.assertEqual(self._snapshot({"11": {"status": "completed"}})["running"], [])

    def test_the_recovery_counts_how_many_are_left_of_the_batch(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        runs = self.server.live_audit_runs
        for audit_id, status in (("11", "success"), ("22", "pending"), ("33", "pending")):
            runs.update(
                LiveAuditRunState.key("n", audit_id),
                catch_up_until="2026-10-17", catch_up_status=status,
            )
        state = self._snapshot({"11": {"status": "idle"}})
        self.assertEqual(state["recovery"], {
            "target_date": "2026-10-17", "total": 3, "done": 1, "pending": 2,
        })
        # Nada corre todavía, pero quedan dos por lanzar: eso es actividad.
        self.assertTrue(state["active"])

    def test_an_older_batch_does_not_contaminate_the_current_one(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        runs = self.server.live_audit_runs
        runs.update(LiveAuditRunState.key("n", "11"),
                    catch_up_until="2026-10-10", catch_up_status="success")
        runs.update(LiveAuditRunState.key("n", "22"),
                    catch_up_until="2026-10-17", catch_up_status="pending")
        state = self._snapshot({"11": {"status": "idle"}})
        self.assertEqual(
            (state["recovery"]["target_date"], state["recovery"]["total"]), ("2026-10-17", 1),
        )

    def test_a_finished_batch_stops_being_activity(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        self.server.live_audit_runs.update(
            LiveAuditRunState.key("n", "11"),
            catch_up_until="2026-10-17", catch_up_status="success",
        )
        state = self._snapshot({"11": {"status": "idle"}})
        self.assertEqual(state["recovery"]["pending"], 0)
        self.assertFalse(state["active"])

    def test_an_agent_that_does_not_answer_is_reported_not_crashed(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        state = self._snapshot({}, broken=True)
        self.assertEqual(state["running"], [])
        self.assertEqual(state["issues"][0]["node_id"], "n")
        self.assertIn("No se pudo consultar el agente", state["issues"][0]["message"])

    def test_the_route_answers_the_snapshot(self) -> None:
        self.server = FakeServer(self.root, audit_state())
        handler = ActivityHandler(self.server)
        parsed = type("Parsed", (), {"path": manager_schedule_routes.ACTIVITY_PATH})()
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=answers({"11": {"status": "comparing"}}),
        ):
            handled = manager_schedule_routes.handle_get(handler, parsed, [])
        self.assertTrue(handled)
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.payload["running"][0]["percent"], 85)


class CatchUpProgressTests(unittest.TestCase):
    """La cola y la barra tienen que contar lo mismo."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_the_click_resets_the_batch_so_the_bar_starts_at_zero(self) -> None:
        server = FakeServer(self.root, audit_state())
        server.live_audit_runs.update(
            LiveAuditRunState.key("n", "11"),
            catch_up_until="2026-10-10", catch_up_status="success",
        )
        live_audit_watch.request_catch_up(
            server, datetime(2026, 10, 20, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(live_audit_activity.recovery(server), {
            "target_date": "2026-10-17", "total": 1, "done": 0, "pending": 1,
        })


class ActivityHandler:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.status = 0
        self.payload: dict[str, object] = {}

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        self.status, self.payload = status, payload


if __name__ == "__main__":
    unittest.main()
