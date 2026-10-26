"""Una vuelta del vigilante: avisos siempre, lanzamiento solo si toca.

Salio de `test_live_audit_cron.py` al pasar ese fichero de 600 lineas. Aqui no
hay aritmetica de citas: se mira lo que el vigilante lee del agente, lo que le
manda y lo que deja escrito en la campana.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_schedule, live_audit_watch
from mt5_manager.live_audit_watch import LiveAuditRunState
from tests.live_audit.live_audit_cron_base import FakeServer, audit_state, published_audit


class WatchTickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    @staticmethod
    def node_answers(audits: dict[str, object], posted: list[object] | None = None):
        def request(_node, method, path, payload=None, timeout=0):
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_audit_restore_account": True}}
            if method == "GET":
                return 200, {"audits": audits}
            if posted is not None:
                posted.append((path, payload))
            return 202, {"audit": {"status": "queued"}}
        return request

    def test_the_bell_only_speaks_when_a_running_audit_finishes(self) -> None:
        server = FakeServer(self.root, audit_state())
        running = {"11": {"status": "testing"}, "22": {"status": "completed"}}
        with mock.patch(
            "mt5_manager.manager_http.node_request", side_effect=self.node_answers(running),
        ):
            live_audit_watch.tick(server)
        # Primera vuelta: sólo recuerda. Un resultado de hace tres semanas no
        # es una noticia.
        self.assertEqual([], server.events())
        finished = {
            "11": {"status": "completed", "finished_at": "2026-10-24T22:10:00+00:00"},
            "22": {"status": "completed"},
        }
        with mock.patch(
            "mt5_manager.manager_http.node_request", side_effect=self.node_answers(finished),
        ):
            live_audit_watch.tick(server)
        events = server.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["level"], "success")
        self.assertIn("Cartera viva", events[0]["title"])
        self.assertEqual(events[0]["audit_id"], "11")

    def test_a_use_whose_card_is_closed_is_watched_just_the_same(self) -> None:
        # El 2026-10-19 AXI tenía una sola tarjeta abierta y cuatro usos reales:
        # los otros tres terminaron en el agente sin un aviso, y su turno se
        # quedó en «corriendo» para siempre porque nadie miraba su estado.
        server = FakeServer(self.root, audit_state(configured_audit_ids=["22"]))
        for audits in ({"11": {"status": "testing"}}, {"11": {"status": "completed"}}):
            with mock.patch(
                "mt5_manager.manager_http.node_request",
                side_effect=self.node_answers(audits),
            ):
                live_audit_watch.tick(server)
        events = server.events()
        self.assertEqual([event["audit_id"] for event in events], ["11"])
        self.assertIn("Cartera viva", events[0]["title"])
        self.assertEqual(
            "success",
            server.live_audit_runs.record(LiveAuditRunState.key("n", "11"))["outcome"],
        )

    def test_a_failure_is_an_error_with_the_reason_of_the_agent(self) -> None:
        server = FakeServer(self.root, audit_state())
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": {"status": "testing"}}),
        ):
            live_audit_watch.tick(server)
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers(
                {"11": {"status": "failed", "error": "MT5 no entregó el HTML"}},
            ),
        ):
            live_audit_watch.tick(server)
        event = server.events()[0]
        self.assertEqual(event["level"], "error")
        self.assertEqual(event["message"], "MT5 no entregó el HTML")

    def test_watching_never_launches_while_the_switch_is_off(self) -> None:
        server = FakeServer(self.root, audit_state())
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": {"status": "idle"}}, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual([], posted)

    def test_only_the_portfolios_marked_as_active_are_audited(self) -> None:
        server = FakeServer(self.root, audit_state(), enabled=True)
        posted: list[object] = []
        audits = {"11": {"status": "idle"}, "22": {"status": "idle"}}
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers(audits, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual([path for path, _payload in posted], ["/api/v1/live-audits/11/run"])

    def test_what_the_manager_decides_does_not_travel_to_the_agent(self) -> None:
        # El Excel de atribución, las parejas a mano y las estrategias ya
        # revisadas se aplican al leer: siguen en el perfil después de que el
        # cron vuelva a auditar, y por eso no se le mandan al nodo.
        server = FakeServer(self.root, audit_state(), enabled=True)
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": published_audit()}, posted),
        ):
            live_audit_watch.tick(server)
        _path, payload = posted[0]
        for key in live_audit_schedule.MANAGER_ONLY_PROFILE_KEYS:
            self.assertNotIn(key, payload)
        self.assertEqual(payload["period_mode"], "fixed_dates")
        self.assertEqual(payload["period_start_date"], "2026-01-01")
        self.assertEqual(payload["audit_key"], "11")
        self.assertEqual(payload["restore_password"], "r")
        # Y el aviso de la campana cuenta ese periodo como lo que es.
        started = [row for row in server.events() if row["kind"] == "audit_started"]
        self.assertIn("periodo 2026-01-01 a", started[0]["message"])
        self.assertIn("enganchado al final de la última auditoría", started[0]["message"])
        self.assertIn("Auditoría semanal lanzada", started[0]["title"])
        # Y el perfil guardado conserva lo que no viajó.
        profile = server.live_audit_settings.state("n")["profiles"]["11"]
        self.assertEqual(profile["manual_pairs"], {"1": "2"})

    def test_the_cron_decides_the_terminals_over_the_checkbox_of_the_use(self) -> None:
        # La casilla del perfil es la del botón «Auditar ahora», que se marca
        # con alguien delante. Una cita de madrugada es otra cosa: manda la
        # política del programador, y por defecto es la terminal principal.
        state = audit_state()
        state["profiles"]["11"]["use_multiterminal"] = True
        server = FakeServer(self.root, state, enabled=True)
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": published_audit()}, posted),
        ):
            live_audit_watch.tick(server)
        self.assertIs(posted[0][1]["use_multiterminal"], False)

    def test_the_cron_can_hand_the_night_the_whole_multiterminal(self) -> None:
        server = FakeServer(
            self.root, audit_state(), enabled=True, use_multiterminal=True,
        )
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": published_audit()}, posted),
        ):
            live_audit_watch.tick(server)
        self.assertIs(posted[0][1]["use_multiterminal"], True)

    def test_the_last_saturday_accumulates_from_the_oldest_active_start(self) -> None:
        state = audit_state()
        state["profiles"]["11"].update({
            "period_mode": "fixed_dates", "period_start_date": "2026-09-01",
            "period_end_date": "2026-10-24",
        })
        portfolios = mock.Mock()
        portfolios.saved.return_value = {
            "portfolio": {"created_at": "2026-09-09T00:07:00+00:00"},
        }
        server = FakeServer(
            self.root, state, portfolios=portfolios, enabled=True,
            monthly_full_audit=True,
        )
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": published_audit()}, posted),
        ):
            live_audit_watch.launch_due(
                server, server.nodes[0], "n", state, {"11": published_audit()},
                datetime(2026, 10, 31, 22, 1, tzinfo=timezone.utc),
            )
        _path, payload = posted[0]
        self.assertEqual(payload["period_mode"], "fixed_dates")
        self.assertEqual(payload["period_start_date"], "2026-09-09")
        self.assertEqual(payload["period_end_date"], "2026-10-31")
        started = [row for row in server.events() if row["kind"] == "audit_started"]
        self.assertIn("auditoría larga acumulada", started[0]["message"])
        self.assertIn("Auditoría larga lanzada", started[0]["title"])
        live_audit_watch.record_transitions(
            server, server.nodes[0], "n", state, {"11": published_audit()},
        )
        finished = [row for row in server.events() if row["kind"] == "audit_completed"]
        self.assertIn("Auditoría larga completada", finished[0]["title"])
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", "11"))
        self.assertEqual(record["last_full_audit_end"], "2026-10-31")
        following = live_audit_watch.live_audit_full_period.audit_period(
            server, "n", state["profiles"]["11"], record,
            "2026-11-28T22:00:00+00:00", live_audit_watch.scheduled_audit_ids,
        )
        self.assertEqual(following["period_start_date"], "2026-10-31")
        self.assertEqual(following["period_end_date"], "2026-11-28")
        portfolios.saved.return_value = {
            "portfolio": {"created_at": "2026-11-10T09:00:00+00:00"},
        }
        new_use = live_audit_watch.live_audit_full_period.audit_period(
            server, "n", state["profiles"]["11"], {},
            "2026-11-28T22:00:00+00:00", live_audit_watch.scheduled_audit_ids,
        )
        self.assertEqual(new_use["period_start_date"], "2026-11-10")

    def test_the_bell_only_claims_the_chain_when_the_period_comes_from_it(self) -> None:
        # «fixed_dates» es justo lo que trae un perfil con fechas fijas. Leerlo
        # como prueba de encadenado hacía que el aviso jurase arrancar donde
        # terminó la última auditoría mientras repetía la ventana del perfil.
        state = audit_state()
        state["profiles"]["11"].update({
            "period_mode": "fixed_dates", "period_start_date": "2026-10-01",
            "period_end_date": "2026-10-11",
        })
        server = FakeServer(self.root, state, enabled=True)
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": {"status": "idle"}}, posted),
        ):
            live_audit_watch.tick(server)
        _path, payload = posted[0]
        self.assertEqual(payload["period_start_date"], "2026-10-01")
        started = [row for row in server.events() if row["kind"] == "audit_started"]
        self.assertIn("periodo configurado en el uso", started[0]["message"])
        self.assertNotIn("enganchado", started[0]["message"])

    def test_one_audit_at_a_time_per_agent(self) -> None:
        state = audit_state(live_audit_ids=["11", "22"])
        state["profiles"]["22"]["live_trading"] = True
        server = FakeServer(self.root, state, enabled=True)
        posted: list[object] = []
        idle = {"11": {"status": "idle"}, "22": {"status": "idle"}}
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers(idle, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual([path for path, _payload in posted], ["/api/v1/live-audits/11/run"])
        # Mientras esa corre, el agente tiene su pipeline pausado: la otra
        # espera a la vuelta siguiente, aunque también le toque.
        running = {"11": {"status": "testing"}, "22": {"status": "idle"}}
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers(running, posted),
        ):
            live_audit_watch.tick(server)
        self.assertEqual(len(posted), 1)

    def test_the_modern_use_id_keeps_its_creation_time(self) -> None:
        created = live_audit_watch._audit_use_created_at("audit-170-mvdscovr-2")
        self.assertEqual(created.isoformat(), "2026-10-18T12:12:24.183000+00:00")
        self.assertIsNone(live_audit_watch._audit_use_created_at("170"))

    def test_a_409_rejected_launch_counts_as_a_failed_attempt(self) -> None:
        server = FakeServer(self.root, audit_state(), enabled=True)

        def rejecting(_node, method, path, payload=None, timeout=0):
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_audit_restore_account": True}}
            if method == "GET":
                return 200, {"audits": {"11": {"status": "idle"}}}
            return 409, {"error": "El agente está ocupado"}

        with mock.patch("mt5_manager.manager_http.node_request", side_effect=rejecting):
            live_audit_watch.tick(server)
        event = server.events()[0]
        self.assertEqual(event["level"], "error")
        self.assertEqual(event["kind"], "launch_rejected")
        record = server.live_audit_runs.record(LiveAuditRunState.key("n", "11"))
        self.assertEqual((record["outcome"], record["attempts"]), ("failed", 1))

    def test_an_unreachable_node_warns_once_and_reports_its_recovery(self) -> None:
        server = FakeServer(self.root, audit_state(), enabled=True)
        with mock.patch("mt5_manager.manager_http.node_request", side_effect=OSError("sin red")):
            live_audit_watch.tick(server)
        self.assertEqual([], server.events())
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": {"status": "testing"}}),
        ):
            live_audit_watch.tick(server)
        self.assertEqual([], server.events())
        with mock.patch("mt5_manager.manager_http.node_request", side_effect=OSError("sin red")):
            live_audit_watch.tick(server)
            live_audit_watch.tick(server)
        self.assertEqual([], server.events())
        with mock.patch("mt5_manager.manager_http.node_request", side_effect=OSError("sin red")):
            live_audit_watch.tick(server)
            live_audit_watch.tick(server)
        self.assertEqual(
            [event["kind"] for event in server.events()], ["scheduler_node_unavailable"],
        )
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=self.node_answers({"11": {"status": "testing"}}),
        ):
            live_audit_watch.tick(server)
        self.assertEqual(
            [event["kind"] for event in server.events()],
            ["scheduler_node_recovered", "scheduler_node_unavailable"],
        )

    def test_an_old_runtime_warning_does_not_wait_for_network_confirmation(self) -> None:
        server = FakeServer(self.root, audit_state(), enabled=True)
        live_audit_watch.record_node_issue(
            server, server.nodes[0], "n", "old_runtime", "Hay que reiniciar el agente.",
        )
        self.assertEqual(
            [event["kind"] for event in server.events()], ["scheduler_node_unavailable"],
        )

    def test_the_dialog_can_see_what_is_marked_but_not_configured(self) -> None:
        state = audit_state(
            configured_audit_ids=["22"], live_audit_ids=["11"],
            credential_state={"11": {
                "source_password_saved": True, "tester_password_saved": False,
            }},
        )
        state["profiles"]["11"]["tester_server"] = ""
        server = FakeServer(self.root, state)
        rows = live_audit_watch.targets(server)
        self.assertEqual([row["audit_id"] for row in rows["audits"]], [])
        self.assertEqual([row["audit_id"] for row in rows["blocked"]], ["11"])
        self.assertEqual(rows["blocked"][0]["label"], "Cartera viva")
        self.assertIn("servidor tester", rows["blocked"][0]["block_reason"])
        self.assertIn("contraseña tester", rows["blocked"][0]["block_reason"])

    def test_a_saved_live_use_does_not_need_to_stay_open_in_the_form(self) -> None:
        state = audit_state(configured_audit_ids=["22"], live_audit_ids=["11"])
        state["profiles"]["11"].update({
            "portfolio_type": "balanced", "source_login": "1",
            "source_server": "Real", "tester_login": "2", "tester_server": "Tester",
        })
        state["credential_state"] = {"11": {
            "source_password_saved": True, "tester_password_saved": True,
        }}
        server = FakeServer(self.root, state)

        self.assertEqual(live_audit_watch.scheduled_audit_ids(
            state, server.live_audit_scheduler_settings,
        ), ["11"])
        rows = live_audit_watch.targets(server)
        self.assertEqual([row["audit_id"] for row in rows["audits"]], ["11"])
        self.assertEqual(rows["blocked"], [])

    def test_a_missing_final_account_is_visible_before_the_appointment(self) -> None:
        server = FakeServer(
            self.root, audit_state(restore_account={"configured": False}),
        )
        rows = live_audit_watch.targets(server)
        self.assertEqual([row["audit_id"] for row in rows["audits"]], [])
        self.assertEqual([row["audit_id"] for row in rows["blocked"]], ["11"])
        self.assertIn("cuenta final", rows["blocked"][0]["block_reason"])


if __name__ == "__main__":
    unittest.main()
