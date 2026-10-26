"""El cron general de auditoría y su campana.

Tres cosas que no se pueden comprobar leyendo el código: que «cada sábado a
las 23:00» sea la hora española y no la del contenedor, que un fallo consuma
reintentos en vez de relanzar sin parar, y que vigilar no lance nada.

Lo que hace el vigilante en cada vuelta vive en `test_live_audit_cron_watch.py`;
los dobles que comparten los dos, en `tests/live_audit/live_audit_cron_base.py`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_schedule
from mt5_manager.live_audit_notifications import MAXIMUM_EVENTS, LiveAuditNotificationStore
from mt5_manager.live_audit_schedule import DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS
from mt5_manager.manager_server import _scheduler_defaults, _scheduler_settings
from tests.live_audit.live_audit_cron_base import published_audit, settings


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
        self.assertIn("sólo con la terminal principal", live)
        self.assertIn(
            "Reparte el tester entre las terminales",
            live_audit_schedule.schedule_description(settings(use_multiterminal=True)),
        )


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


class DefaultsTests(unittest.TestCase):
    def test_the_cron_arrives_disarmed(self) -> None:
        # Una auditoría pausa el pipeline del agente: el interruptor lo pone
        # una persona, nunca un despliegue.
        self.assertFalse(DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS["enabled"])

    def test_the_last_weekly_turn_is_the_monthly_full_audit(self) -> None:
        current = settings(monthly_full_audit=True)
        for window in (
            "2026-10-31T22:00:00+00:00", "2026-11-28T22:00:00+00:00",
            "2026-12-26T22:00:00+00:00", "2027-01-30T22:00:00+00:00",
        ):
            with self.subTest(window=window):
                self.assertTrue(live_audit_schedule.is_monthly_full_audit(window, current))
        self.assertFalse(live_audit_schedule.is_monthly_full_audit(
            "2026-10-24T21:00:00+00:00", current,
        ))

    def test_the_discarded_week_selector_migrates_out(self) -> None:
        self.assertNotIn(
            "monthly_full_audit_week",
            live_audit_schedule.normalize_live_audit_scheduler_settings(
                {"monthly_full_audit_week": 4},
            ),
        )


if __name__ == "__main__":
    unittest.main()
