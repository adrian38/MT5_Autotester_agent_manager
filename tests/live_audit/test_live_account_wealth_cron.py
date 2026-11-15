"""La cita que relee la vida entera de cada cuenta, y su cola.

Es un cron distinto del sondeo diario, y lo es por lo que pide: el diario
relee los últimos días —con esa ventana los ingresos y las retiradas de hace
medio año no entran nunca— y éste pide el informe **completo**.

Y espera. El 2026-11-15 el sondeo entró mientras Vantage y RoboForex estaban
con pruebas y tres cuentas volvieron sin leer: «El terminal no tiene el
historial de la cuenta». Petición del usuario ese mismo día: la cita se encola
y entra cuando el agente queda libre, sin tocar lo que esté corriendo.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import (
    live_account_inventory, live_account_watch, live_account_wealth_watch,
)
from mt5_manager.live_account.live_account_archive import LiveAccountArchive
from mt5_manager.live_account.live_account_ingest import SNAPSHOT_KEY
from mt5_manager.live_account.live_account_wealth_schedule import (
    normalize_wealth_scheduler_settings, next_occurrence, schedule_label,
)
from mt5_manager.live_audit.live_audit_run_state import LiveAuditRunState

NODE = {"id": "axi", "name": "AXI Standard", "url": "http://127.0.0.1:1"}
KEY = "60090762@Axi-US50-Live"
# Sábado 14 de noviembre de 2026, 09:00 UTC: la cita de las 07:00 ya venció.
SATURDAY = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)


class Notifications:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, level: str, title: str, message: str, **extra: Any) -> None:
        self.rows.append({"level": level, "title": title, "message": message, **extra})


class WealthCronBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.archive = LiveAccountArchive(root / "live_accounts")
        self.runs = LiveAuditRunState(root / "state.json")
        self.notifications = Notifications()
        self.server = SimpleNamespace(
            nodes=[NODE], live_accounts=self.archive, live_account_runs=self.runs,
            live_audit_notifications=self.notifications,
            live_account_stop=SimpleNamespace(is_set=lambda: False),
            live_account_wakeup=SimpleNamespace(set=lambda: None),
        )
        live_account_wealth_watch.configure(self.server, root / "live_audit_settings.json")
        self.server.wealth_scheduler_settings = normalize_wealth_scheduler_settings(
            {"enabled": True, "weekday": 5, "hour": 7, "minute": 0, "timezone": "UTC"},
        )
        self.accounts = [{
            "account_key": KEY, "login": "60090762", "server": "Axi-US50-Live",
            "portfolios": [], "audit_ids": ["1"], "autolot": False, "block_reason": "",
        }]

    def tick(self, now: datetime = SATURDAY, job: str = "idle") -> list[Any]:
        """Una vuelta con el agente en ese estado, anotando lo que se lanzó."""
        launched: list[Any] = []

        def fake_launch(server, node, node_id, accounts, window, attempts, now_, **extra):
            launched.append({"node_id": node_id, "window": window, **extra})
            self.runs.update(self.runs.key(node_id, SNAPSHOT_KEY), outcome="running")

        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=self.accounts,
        ), mock.patch.object(
            live_account_wealth_watch.manager_http, "node_request",
            return_value=(200, {"job": {"status": job, "job_type": "generation"}}),
        ), mock.patch.object(live_account_watch, "launch", fake_launch):
            live_account_wealth_watch.tick(self.server, now)
        return launched

    def record(self) -> dict[str, Any]:
        return self.runs.record(self.runs.key("axi", live_account_wealth_watch.WEALTH_KEY))


class ScheduleTests(unittest.TestCase):
    def test_the_weekly_appointment_is_said_the_way_someone_would_say_it(self) -> None:
        settings = normalize_wealth_scheduler_settings(
            {"weekday": 5, "hour": 7, "minute": 0, "timezone": "UTC"},
        )
        self.assertEqual(schedule_label(settings), "cada sábado a las 07:00 (UTC)")
        self.assertEqual(
            next_occurrence(datetime(2026, 11, 13, 9, tzinfo=timezone.utc), settings).weekday(), 5,
        )

    def test_an_unknown_field_is_refused_instead_of_ignored(self) -> None:
        """Un campo mal escrito que se ignora es una cita que no se guarda."""
        with self.assertRaisesRegex(ValueError, "Campos desconocidos"):
            normalize_wealth_scheduler_settings({"weekdays": [1]})

    def test_the_wait_has_a_ceiling_and_a_floor(self) -> None:
        with self.assertRaises(ValueError):
            normalize_wealth_scheduler_settings({"wait_hours": 0})
        self.assertEqual(
            normalize_wealth_scheduler_settings({"wait_hours": 36})["wait_hours"], 36,
        )


class QueueTests(WealthCronBase):
    def test_a_busy_agent_leaves_the_appointment_waiting_instead_of_launching(self) -> None:
        launched = self.tick(job="running")
        self.assertEqual(launched, [])
        record = self.record()
        self.assertEqual(record["status"], "waiting")
        self.assertIn("ocupado", record["waiting_reason"])
        self.assertTrue(record["waiting_since"])

    def test_it_enters_as_soon_as_the_agent_is_free(self) -> None:
        self.tick(job="running")
        launched = self.tick(SATURDAY + timedelta(minutes=5), job="idle")
        self.assertEqual(len(launched), 1)
        # Toda la vida: sin fecha de inicio, que es como se pide el informe
        # completo, y con su propio motivo para la campana.
        self.assertEqual(launched[0]["period"][0], "")
        self.assertIn("patrimonio", launched[0]["reason"])
        self.assertEqual(self.record()["status"], "launched")

    def test_a_launched_appointment_is_not_launched_again_while_it_runs(self) -> None:
        self.tick()
        self.assertEqual(self.tick(SATURDAY + timedelta(minutes=5)), [])

    def test_the_appointment_closes_when_the_snapshot_it_asked_for_finishes(self) -> None:
        self.tick()
        self.runs.update(
            self.runs.key("axi", SNAPSHOT_KEY), outcome="completed",
            completed_at="2026-11-14T09:30:00+00:00",
        )
        self.tick(SATURDAY + timedelta(minutes=40))
        record = self.record()
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["completed_at"], "2026-11-14T09:30:00+00:00")
        # Y no se vuelve a lanzar en el mismo turno.
        self.assertEqual(self.tick(SATURDAY + timedelta(hours=1)), [])

    def test_the_wait_gives_up_and_says_so(self) -> None:
        """Una cita que no caduca acabaría entrando el lunes a media mañana."""
        self.server.wealth_scheduler_settings["wait_hours"] = 2
        self.tick(job="running")
        self.tick(SATURDAY + timedelta(hours=3), job="running")
        self.assertEqual(self.record()["status"], "expired")
        self.assertEqual(self.notifications.rows[-1]["level"], "warning")
        self.assertIn("no quedó libre", self.notifications.rows[-1]["message"])

    def test_a_snapshot_already_running_also_counts_as_busy(self) -> None:
        self.runs.update(self.runs.key("axi", SNAPSHOT_KEY), outcome="running")
        self.assertEqual(self.tick(), [])
        self.assertIn("sondeo", self.record()["waiting_reason"])

    def test_with_the_switch_off_nothing_is_asked_of_any_agent(self) -> None:
        self.server.wealth_scheduler_settings["enabled"] = False
        with mock.patch.object(
            live_account_wealth_watch.manager_http, "node_request",
            side_effect=AssertionError("la cita apagada no habla con el agente"),
        ):
            live_account_wealth_watch.tick(self.server, SATURDAY)
        self.assertEqual(self.record(), {})


class ScopeTests(WealthCronBase):
    def test_only_the_accounts_that_add_up_are_reread(self) -> None:
        """Una relectura cuesta una terminal y nueve megas por cuenta.

        Lo que no cuenta en el patrimonio no justifica ese precio.
        """
        self.accounts.append({
            "account_key": "10010194@Axi-US50-Demo", "login": "10010194",
            "server": "Axi-US50-Demo", "portfolios": [], "audit_ids": ["2"],
            "autolot": False, "block_reason": "",
        })
        self.archive.update_total_selection({
            "account": "axi::10010194@Axi-US50-Demo", "included": False,
        })
        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=self.accounts,
        ):
            found = live_account_wealth_watch.summing_accounts(self.server, "axi")
        self.assertEqual([row["account_key"] for row in found], [KEY])

    def test_a_node_without_accounts_that_add_up_is_left_alone(self) -> None:
        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=[],
        ), mock.patch.object(
            live_account_wealth_watch.manager_http, "node_request",
            side_effect=AssertionError("sin cuentas no se pregunta nada"),
        ):
            live_account_wealth_watch.tick(self.server, SATURDAY)
        self.assertEqual(self.record(), {})

    def test_the_manual_run_respects_the_queue_too(self) -> None:
        """Pedirlo una persona no cambia por qué no se entra en mitad de unas pruebas."""
        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=self.accounts,
        ), mock.patch.object(
            live_account_wealth_watch.manager_http, "node_request",
            return_value=(200, {"job": {"status": "running", "job_type": "generation"}}),
        ):
            answer = live_account_wealth_watch.run_now(self.server)
        self.assertEqual(answer["launched"], [])
        self.assertIn("ocupado", answer["skipped"][0]["reason"])

    def test_the_state_says_what_each_agent_is_doing_with_the_appointment(self) -> None:
        self.tick(job="running")
        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=self.accounts,
        ):
            payload = live_account_wealth_watch.state(self.server)
        self.assertEqual(payload["schedule_label"], "cada sábado a las 07:00 (UTC)")
        self.assertEqual(payload["nodes"][0]["status"], "waiting")
        self.assertEqual(payload["nodes"][0]["accounts"], 1)
        self.assertIn("informe", payload["description"])


if __name__ == "__main__":
    unittest.main()
