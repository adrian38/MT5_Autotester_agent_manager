"""El cron general de auditoría y su campana.

Tres cosas que no se pueden comprobar leyendo el código: que «cada sábado a
las 23:00» sea la hora española y no la del contenedor, que un fallo consuma
reintentos en vez de relanzar sin parar, y que vigilar no lance nada.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_audit_schedule, live_audit_watch
from mt5_manager.live_audit_notifications import MAXIMUM_EVENTS, LiveAuditNotificationStore
from mt5_manager.live_audit_schedule import (
    DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS,
    normalize_live_audit_scheduler_settings,
)
from mt5_manager.live_audit_watch import LiveAuditRunState
from mt5_manager.manager_server import _scheduler_defaults, _scheduler_settings


def settings(**changes: object) -> dict[str, object]:
    return normalize_live_audit_scheduler_settings(changes)


class FakeSettingsStore:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    def state(self, _node_id: str) -> dict[str, object]:
        return self._state

    def credentials(self, _node_id: str, _audit_id: str) -> dict[str, str]:
        return {"source_password": "s", "tester_password": "t"}

    def restore_credentials(self, _node_id: str) -> dict[str, str]:
        return {
            "restore_login": "9", "restore_server": "Broker-Test", "restore_password": "r",
        }


class FakeServer:
    def __init__(self, root: Path, state: dict[str, object],
                 portfolios: object = None, **changes: object) -> None:
        self.nodes = [{"id": "n", "name": "Nodo N", "url": "http://127.0.0.1:1", "token": "t"}]
        self.portfolios = portfolios
        self.live_audit_settings = FakeSettingsStore(state)
        self.live_audit_scheduler_settings = settings(**changes)
        self.live_audit_scheduler_enabled = bool(changes.get("enabled"))
        self.live_audit_notifications = LiveAuditNotificationStore(root / "avisos.json")
        self.live_audit_runs = LiveAuditRunState(root / "cron.json")
        self.live_audit_stop = threading.Event()

    def events(self) -> list[dict[str, object]]:
        return self.live_audit_notifications.state(50)["events"]


def audit_state(**changes: object) -> dict[str, object]:
    return {
        "configured_audit_ids": ["11", "22"],
        "live_audit_ids": ["11"],
        "restore_account": {"configured": True},
        "profiles": {
            "11": {
                "portfolio_id": 11, "deployment_name": "Cartera viva",
                "live_trading": True, "period_mode": "rolling_days", "period_days": 7,
                "real_attribution": {"rules": [{"ticket": 1}]},
                "manual_pairs": {"1": "2"}, "reviewed_strategies": {"a": True},
            },
            "22": {"portfolio_id": 22, "deployment_name": "Cartera parada"},
        },
        **changes,
    }


def published_audit(**changes: object) -> dict[str, object]:
    """Un uso terminado con la forma que publica un agente de hoy.

    Desde que la comparación se movió al manager, el nodo sólo publica
    `last_payload`: `last_result` llega **siempre** a `None`. Alimentar
    `{"last_result": {...}}` modelaba un agente que ya no existe, y por eso el
    suite no vio que el periodo nunca se encadenaba.
    """
    payload: dict[str, object] = {
        "audit_id": "run-1", "audit_key": "11",
        "completed_at": "2026-01-02T03:00:00+00:00",
        "period_start": "2026-01-01T00:00:00+00:00",
        "period_end": "2026-01-01T23:59:59+00:00",
        "request": {
            "audit_key": "11", "portfolio_id": 11, "portfolio_type": "balanced",
            "period_mode": "fixed_dates", "period_days": 7,
            "period_start_date": "2025-12-26", "period_end_date": "2026-01-01",
        },
    }
    payload.update(changes)
    return {
        "status": "completed", "audit_id": payload["audit_id"],
        "finished_at": "2026-01-02T03:00:00+00:00",
        "last_result": None, "last_payload": payload,
    }


class WeeklyCadenceTests(unittest.TestCase):
    def test_eleven_at_night_is_spanish_time_not_the_container_clock(self) -> None:
        # El contenedor del manager corre en UTC. Sin zona, «las 23:00» serían
        # las 00:00 o la 01:00 en España, que es justo la noche equivocada.
        summer = live_audit_schedule.previous_occurrence(
            datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc), settings(),
        )
        winter = live_audit_schedule.previous_occurrence(
            datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc), settings(),
        )
        self.assertEqual(summer.astimezone(timezone.utc).isoformat(), "2026-07-18T21:00:00+00:00")
        self.assertEqual(winter.astimezone(timezone.utc).isoformat(), "2026-12-19T22:00:00+00:00")
        for occurrence in (summer, winter):
            self.assertEqual(occurrence.weekday(), 5)
            self.assertEqual((occurrence.hour, occurrence.minute), (23, 0))

    def test_the_next_run_is_a_calendar_week_later_across_the_dst_change(self) -> None:
        # Sumar 168 horas al instante absoluto desplazaría la cita una hora el
        # fin de semana del cambio de horario.
        now = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
        following = live_audit_schedule.next_occurrence(now, settings())
        self.assertEqual((following.hour, following.minute), (23, 0))
        self.assertEqual(following.astimezone(timezone.utc).isoformat(), "2026-10-31T22:00:00+00:00")

    def test_an_audit_already_run_after_the_appointment_is_not_pending(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        occurrence = live_audit_schedule.previous_occurrence(now, settings())
        window, pending = live_audit_schedule.pending_window(
            settings(), occurrence + timedelta(hours=2), now,
        )
        self.assertFalse(pending)
        _window, pending_before = live_audit_schedule.pending_window(
            settings(), occurrence - timedelta(hours=2), now,
        )
        self.assertTrue(pending_before)
        self.assertEqual(window, occurrence.isoformat())

    def test_a_use_created_after_the_appointment_does_not_inherit_it(self) -> None:
        now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        occurrence = live_audit_schedule.previous_occurrence(now, settings())
        _window, pending = live_audit_schedule.pending_window(
            settings(), None, now, occurrence + timedelta(minutes=1),
        )
        self.assertFalse(pending)
        _window, older_pending = live_audit_schedule.pending_window(
            settings(), None, now, occurrence - timedelta(minutes=1),
        )
        self.assertTrue(older_pending)

    def test_the_cadence_in_days_keeps_its_own_window(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        interval = settings(mode="interval", interval_days=7)
        _window, pending = live_audit_schedule.pending_window(
            interval, now - timedelta(days=2), now,
        )
        self.assertFalse(pending)
        _window, overdue = live_audit_schedule.pending_window(
            interval, now - timedelta(days=9), now,
        )
        self.assertTrue(overdue)
        self.assertEqual(
            live_audit_schedule.pending_window(interval, None, now), ("sin-ejecutar", True),
        )
        created = now - timedelta(days=2)
        _window, new_pending = live_audit_schedule.pending_window(
            interval, None, now, created,
        )
        self.assertFalse(new_pending)

    def test_the_description_matches_the_real_scope_period_and_catch_up(self) -> None:
        live = live_audit_schedule.schedule_description(settings())
        self.assertIn("marcados «operando en real»", live)
        self.assertIn("donde terminó la anterior", live)
        self.assertIn("la recupera", live)
        self.assertIn("ya existían en esa cita", live)
        every = live_audit_schedule.schedule_description(settings(
            only_live_trading=False, chain_from_last_audit=False,
        ))
        self.assertIn("todos los usos con una auditoría configurada", every)
        self.assertIn("periodo guardado en su perfil", every)


class SchedulerMigrationTests(unittest.TestCase):
    def test_an_old_saved_interval_does_not_silently_become_weekly(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "scheduler.json"
            path.write_text(json.dumps({"enabled": True, "interval_days": 17}), encoding="utf-8")
            migrated = _scheduler_settings(path, settings())
        self.assertEqual((migrated["mode"], migrated["interval_days"]), ("interval", 17))

    def test_the_old_manager_switch_and_environment_keep_interval_semantics(self) -> None:
        for config, environment in (
            ({"live_audit_scheduler_enabled": True}, False),
            ({}, True),
        ):
            defaults = _scheduler_defaults(config, environment)
            self.assertEqual(settings(**defaults)["mode"], "interval")


class RetryPolicyTests(unittest.TestCase):
    def test_three_retries_every_ten_minutes_and_then_it_stops(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        current = settings()
        self.assertEqual(
            "first", live_audit_schedule.due_decision(current, {}, "turno", now),
        )
        failed = {"window": "turno", "outcome": "failed", "attempts": 1,
                  "last_attempt_at": (now - timedelta(minutes=4)).isoformat()}
        self.assertEqual("", live_audit_schedule.due_decision(current, failed, "turno", now))
        failed["last_attempt_at"] = (now - timedelta(minutes=11)).isoformat()
        self.assertEqual("retry", live_audit_schedule.due_decision(current, failed, "turno", now))
        for attempts, expected in ((2, "retry"), (3, "retry"), (4, "exhausted")):
            failed["attempts"] = attempts
            self.assertEqual(
                expected, live_audit_schedule.due_decision(current, failed, "turno", now),
                msg=f"con {attempts} intentos",
            )

    def test_a_finished_turn_is_not_launched_again(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        for outcome in ("success", "running", "exhausted", "stopped"):
            self.assertEqual("", live_audit_schedule.due_decision(
                settings(), {"window": "turno", "outcome": outcome}, "turno", now,
            ), msg=outcome)


class ChainedPeriodTests(unittest.TestCase):
    NOW = datetime(2026, 10, 24, 21, 30, tzinfo=timezone.utc)

    def test_the_next_audit_starts_where_the_last_one_ended(self) -> None:
        # Las operaciones reales se filtran por hora de cierre: arrancar donde
        # terminó la anterior recoge las que entonces seguían abiertas. El
        # último día se repite a propósito —la auditoría anterior se ejecutó a
        # media noche de ese día y lo que cerró después no estaba todavía en el
        # historial del terminal—, así que no queda hueco entre semanas.
        period = live_audit_schedule.chained_period(
            settings(), published_audit(period_end="2026-10-17T23:59:59+00:00"), self.NOW,
        )
        self.assertEqual(period, {
            "period_mode": "fixed_dates",
            "period_start_date": "2026-10-17",
            "period_end_date": "2026-10-24",
        })

    def test_an_agent_that_still_publishes_its_own_verdict_is_read_the_same(self) -> None:
        # Un nodo sin el reparto nuevo sí trae `last_result`: no se puede
        # perder ese camino al añadir el de la materia prima.
        period = live_audit_schedule.chained_period(
            settings(), {"last_result": {"period_end": "2026-10-17T23:59:59+00:00"}}, self.NOW,
        )
        self.assertEqual(period["period_start_date"], "2026-10-17")

    def test_a_payload_without_its_period_closes_with_the_end_of_the_run(self) -> None:
        audit = published_audit()
        audit["last_payload"].pop("period_end")
        audit["finished_at"] = "2026-10-18T02:30:00+00:00"
        period = live_audit_schedule.chained_period(settings(), audit, self.NOW)
        self.assertEqual(period["period_start_date"], "2026-10-18")

    def test_without_a_previous_audit_the_profile_decides(self) -> None:
        self.assertEqual({}, live_audit_schedule.chained_period(settings(), {}, self.NOW))
        self.assertEqual({}, live_audit_schedule.chained_period(
            settings(), {"status": "idle", "last_result": None, "last_payload": None},
            self.NOW,
        ))
        self.assertEqual({}, live_audit_schedule.chained_period(
            settings(chain_from_last_audit=False),
            published_audit(period_end="2026-10-17T23:59:59+00:00"), self.NOW,
        ))


class NotificationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_unread_count_marking_and_survival_across_restarts(self) -> None:
        store = LiveAuditNotificationStore(self.root / "avisos.json")
        first = store.add("error", "Auditoría fallida", "MT5 no abrió", node_id="n")
        store.add("success", "Auditoría completada")
        self.assertEqual(store.state(50)["unread"], 2)
        self.assertEqual([event["title"] for event in store.state(50)["events"]][0],
                         "Auditoría completada")  # el más reciente primero
        store.mark_read([first["id"]])
        self.assertEqual(store.state(50)["unread"], 1)
        # Un reinicio del manager no puede perder el aviso del sábado.
        reopened = LiveAuditNotificationStore(self.root / "avisos.json")
        self.assertEqual(reopened.state(50)["unread"], 1)
        self.assertEqual(reopened.state(50)["total"], 2)
        reopened.mark_read()
        self.assertEqual(reopened.state(50)["unread"], 0)

    def test_the_diary_does_not_grow_without_end(self) -> None:
        store = LiveAuditNotificationStore(self.root / "avisos.json")
        for number in range(MAXIMUM_EVENTS + 5):
            store.add("info", f"Aviso {number}")
        self.assertEqual(store.state(1)["total"], MAXIMUM_EVENTS)
        self.assertEqual(store.state(1)["events"][0]["title"], f"Aviso {MAXIMUM_EVENTS + 4}")


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
        # Y el perfil guardado conserva lo que no viajó.
        profile = server.live_audit_settings.state("n")["profiles"]["11"]
        self.assertEqual(profile["manual_pairs"], {"1": "2"})

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


class DefaultsTests(unittest.TestCase):
    def test_the_cron_arrives_disarmed(self) -> None:
        # Una auditoría pausa el pipeline del agente: el interruptor lo pone
        # una persona, nunca un despliegue.
        self.assertFalse(DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS["enabled"])


if __name__ == "__main__":
    unittest.main()
