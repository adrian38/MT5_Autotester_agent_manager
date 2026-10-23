"""El cron de cuentas de punta a punta, sin tocar ningún agente.

Lo que se comprueba aquí es lo que falló en el cron de auditoría y no puede
volver a fallar: que el alcance sale del **perfil** y no de las tarjetas
abiertas, que la recogida es idempotente por sondeo —el nodo guarda uno y el
siguiente lo sustituye—, que sin el interruptor no se le pide nada al agente,
y que un agente sin portar se distingue de un agente caído.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import (
    live_account_dashboard, live_account_inventory, live_account_report,
    live_account_watch,
)
from mt5_manager.live_account_archive import LiveAccountArchive, account_key
from mt5_manager.live_account_schedule import normalize_live_account_scheduler_settings
from mt5_manager.live_audit_notifications import LiveAuditNotificationStore
from mt5_manager.live_audit_watch import LiveAuditRunState

REPORT = b"<html>informe</html>"
SHA = hashlib.sha256(REPORT).hexdigest()
NOW = datetime(2026, 10, 21, 23, 30, tzinfo=ZoneInfo("Europe/Madrid"))


def profile(**changes: object) -> dict[str, object]:
    row = {
        "portfolio_id": 105, "portfolio_type": "aggressive",
        "deployment_name": "Port_107_AXI", "live_trading": True,
        "source_login": "60082017", "source_server": "Axi-US50-Live",
        "real_strategy_lots": {"AXI/STANDARD:1437": 0.01},
        "autolot": {"enabled": False},
    }
    row.update(changes)
    return row


def node_state(**changes: object) -> dict[str, object]:
    row = {
        # Una sola tarjeta abierta y dos usos reales: es el caso que dejó al
        # cron semanal vigilando uno de cuatro.
        "configured_audit_ids": ["audit-105"],
        "profiles": {
            "audit-105": profile(),
            "audit-109": profile(
                portfolio_id=109, deployment_name="Christmas_Axi",
                source_login="60082303", autolot={"enabled": True},
            ),
            "audit-64": profile(portfolio_id=64, live_trading=False, source_login="1"),
        },
        "credential_state": {
            "audit-105": {"source_password_saved": True},
            "audit-109": {"source_password_saved": True},
            "audit-64": {"source_password_saved": True},
        },
        "restore_account": {"configured": True},
    }
    row.update(changes)
    return row


class FakeSettingsStore:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    def state(self, _node_id: str) -> dict[str, object]:
        return self._state

    def credentials(self, _node_id: str, _audit_id: str) -> dict[str, str]:
        return {"source_password": "secreto"}

    def restore_credentials(self, _node_id: str) -> dict[str, str]:
        return {
            "restore_login": "60268704", "restore_server": "Axi-US51-Live",
            "restore_password": "final",
        }


class FakeServer:
    def __init__(self, root: Path, state: dict[str, object], **changes: object) -> None:
        self.nodes = [{"id": "axi", "name": "AXI Standard", "url": "http://127.0.0.1:1"}]
        self.live_audit_settings = FakeSettingsStore(state)
        self.live_accounts = LiveAccountArchive(root / "live_accounts")
        self.live_account_runs = LiveAuditRunState(root / "cron.json")
        self.live_audit_notifications = LiveAuditNotificationStore(root / "avisos.json")
        self.live_account_scheduler_settings = normalize_live_account_scheduler_settings(changes)
        self.live_account_scheduler_enabled = bool(changes.get("enabled"))
        self.live_account_stop = threading.Event()
        self.live_account_wakeup = threading.Event()

    def events(self) -> list[dict[str, object]]:
        return self.live_audit_notifications.state(50)["events"]


def snapshot_state(status: str = "completed", **changes: object) -> dict[str, object]:
    row = {
        "snapshot_id": "20261021_230500_000001", "status": status,
        "finished_at": "2026-10-21T21:10:00+00:00",
        "period_start": "2026-10-17T00:00:00+00:00",
        "period_end": "2026-10-21T21:00:00+00:00",
        "accounts": {
            "60082017@Axi-US50-Live": {
                "login": "60082017", "server": "Axi-US50-Live", "currency": "EUR",
                "balance": 1200.0, "equity": 1210.0, "status": "completed",
                "report": {"filename": "account_60082017.html", "sha256": SHA},
            },
            "60082303@Axi-US50-Live": {
                "login": "60082303", "server": "Axi-US50-Live", "currency": "USD",
                "status": "failed", "error": "la cuenta no quedó conectada",
            },
        },
    }
    row.update(changes)
    return row


def complete_snapshot_state(**changes: object) -> dict[str, object]:
    """El mismo sondeo, pero con las dos cuentas leídas.

    Es lo que separa un turno cumplido de uno que hay que reintentar, así que
    no vale con mirar el `finished_at`: las dos formas lo traen.
    """
    row = snapshot_state()
    accounts = dict(row["accounts"])
    accounts["60082303@Axi-US50-Live"] = {
        "login": "60082303", "server": "Axi-US50-Live", "currency": "USD",
        "balance": 800.0, "equity": 805.0, "status": "completed",
        "report": {"filename": "account_60082303.html", "sha256": SHA},
    }
    row["accounts"] = accounts
    row.update(changes)
    return row


class InventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name), node_state())

    def test_the_scope_is_the_profile_and_not_the_open_card(self) -> None:
        accounts = live_account_inventory.node_accounts(self.server, "axi")
        self.assertEqual(
            [account["login"] for account in accounts], ["60082017", "60082303"],
        )

    def test_a_use_that_is_not_live_is_not_an_account(self) -> None:
        logins = [
            account["login"]
            for account in live_account_inventory.node_accounts(self.server, "axi")
        ]
        self.assertNotIn("1", logins)

    def test_two_uses_on_one_login_are_a_single_card(self) -> None:
        state = node_state()
        state["profiles"]["audit-109"] = profile(portfolio_id=109)
        server = FakeServer(Path(self.folder.name), state)
        accounts = live_account_inventory.node_accounts(server, "axi")
        self.assertEqual(len(accounts), 1)
        self.assertEqual(
            sorted(row["portfolio_id"] for row in accounts[0]["portfolios"]), [105, 109],
        )

    def test_one_complete_use_is_enough_to_read_a_shared_account(self) -> None:
        state = node_state()
        state["profiles"]["audit-109"] = profile(portfolio_id=109)
        state["credential_state"]["audit-109"] = {"source_password_saved": False}
        server = FakeServer(Path(self.folder.name), state)
        self.assertEqual(
            live_account_inventory.node_accounts(server, "axi")[0]["block_reason"], "",
        )

    def test_a_missing_password_blocks_the_card_and_says_why(self) -> None:
        state = node_state()
        state["credential_state"]["audit-105"] = {"source_password_saved": False}
        server = FakeServer(Path(self.folder.name), state)
        blocked = live_account_inventory.node_accounts(server, "axi")[0]
        self.assertIn("contraseña", blocked["block_reason"])
        self.assertEqual(live_account_inventory.readable_accounts(server, "axi"), [
            account for account in live_account_inventory.node_accounts(server, "axi")
            if account["login"] == "60082303"
        ])

    def test_what_travels_to_the_node_is_the_account_and_its_password(self) -> None:
        payload = live_account_inventory.snapshot_accounts(self.server, "axi")
        self.assertEqual(payload[0], {
            "account_key": "60082017@Axi-US50-Live", "login": "60082017",
            "server": "Axi-US50-Live", "password": "secreto",
        })


class WatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.requests: list[tuple[str, str, object]] = []

    def server(self, **changes: object) -> FakeServer:
        return FakeServer(Path(self.folder.name), node_state(), **changes)

    def _request(self, state: dict[str, object], capability: bool = True):
        def node_request(node, method, path, payload=None, timeout=10):
            self.requests.append((method, path, payload))
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_account_snapshot": capability}}
            if path == "/api/v1/live-accounts":
                return 200, {"snapshot": state}
            if path == "/api/v1/live-accounts/snapshot":
                return 202, {"snapshot": {"status": "queued"}}
            raise AssertionError(f"ruta inesperada {path}")

        def artifact(node, path, timeout=10):
            self.requests.append(("GET", path, None))
            return 200, REPORT, "text/html"

        return mock.patch.multiple(
            "mt5_manager.manager_http",
            node_request=node_request, node_artifact_request=artifact,
        )

    def test_without_the_switch_nothing_is_asked_of_the_agent(self) -> None:
        server = self.server()
        with self._request(snapshot_state("idle")), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ):
            live_account_watch.tick(server, NOW)
        self.assertTrue(self.requests)
        self.assertTrue(all(method == "GET" for method, _path, _body in self.requests))

    def test_with_the_switch_the_due_snapshot_is_launched(self) -> None:
        server = self.server(enabled=True)
        with self._request(snapshot_state("idle")), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ):
            live_account_watch.tick(server, NOW)
        posts = [row for row in self.requests if row[0] == "POST"]
        self.assertEqual(len(posts), 1)
        body = posts[0][2]
        self.assertEqual(len(body["accounts"]), 2)
        self.assertEqual(body["restore_login"], "60268704")
        # Ninguna cuenta tiene serie todavía: se pide toda la vida.
        self.assertEqual(body["period_start_date"], "")
        self.assertEqual(body["period_end_date"], "2026-10-21")

    def test_a_second_turn_asks_only_for_the_last_days(self) -> None:
        server = self.server(enabled=True)
        # Con la version del criterio puesta: una serie guardada con otro
        # criterio se vuelve a leer entera, y entonces no habria segundo turno
        # corto que comprobar.
        for key, login, currency, day, amount in (
            ("60082017@Axi-US50-Live", "60082017", "EUR", 20, 5.0),
            ("60082303@Axi-US50-Live", "60082303", "USD", 19, 1.0),
        ):
            server.live_accounts.merge(
                "axi", key, login=login, server="Axi-US50-Live", currency=currency,
                movements=[(datetime(2026, 10, day, 8, tzinfo=timezone.utc), amount)],
                period_start="", period_end=f"2026-10-{day}",
                normalization_version=live_account_report.normalization_version(key),
            )
        with self._request(snapshot_state("idle")), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ):
            live_account_watch.tick(server, NOW)
        body = [row for row in self.requests if row[0] == "POST"][0][2]
        # Manda la cuenta que va más atrás (19) menos el solape de tres días.
        self.assertEqual(body["period_start_date"], "2026-10-16")

    def test_a_finished_snapshot_enters_the_series_once(self) -> None:
        server = self.server()
        with self._request(snapshot_state()), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ), mock.patch(
            "mt5_manager.live_account_report.movements_from_html",
            return_value=[(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
        ) as parsed:
            live_account_watch.tick(server, NOW)
            live_account_watch.tick(server, NOW)
        self.assertEqual(parsed.call_count, 1)
        stored = server.live_accounts.read("axi", "60082017@Axi-US50-Live")
        self.assertEqual(len(stored["movements"]), 1)
        self.assertAlmostEqual(stored["balance"], 1200.0)
        self.assertEqual(stored["currency"], "EUR")

    def test_the_bell_says_what_entered_and_what_did_not(self) -> None:
        server = self.server()
        with self._request(snapshot_state()), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ), mock.patch(
            "mt5_manager.live_account_report.movements_from_html",
            return_value=[(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
        ):
            live_account_watch.tick(server, NOW)
        event = server.events()[0]
        self.assertEqual(event["kind"], "account_snapshot_collected")
        self.assertEqual(event["level"], "warning")
        self.assertIn("1 cuenta(s) y 1 cierre(s)", event["message"])
        self.assertIn("no quedó conectada", event["message"])

    def test_a_report_that_does_not_match_its_hash_is_refused(self) -> None:
        server = self.server()
        def artifact(node, path, timeout=10):
            return 200, b"<html>otro</html>", "text/html"
        with self._request(snapshot_state()), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ), mock.patch("mt5_manager.manager_http.node_artifact_request", artifact):
            live_account_watch.tick(server, NOW)
        self.assertEqual(server.live_accounts.read("axi", "60082017@Axi-US50-Live")["movements"], [])
        self.assertIn("no coincide", server.events()[0]["message"])

    def test_an_agent_without_the_port_is_not_an_agent_that_is_down(self) -> None:
        server = self.server(enabled=True)
        with self._request(snapshot_state(), capability=False):
            live_account_watch.tick(server, NOW)
        event = server.events()[0]
        self.assertEqual(event["kind"], "account_snapshot_node_unavailable")
        self.assertEqual(event["level"], "warning")
        self.assertIn("falta portar", event["message"])
        self.assertFalse([row for row in self.requests if row[0] == "POST"])

    def test_a_single_timeout_is_not_a_disconnection(self) -> None:
        server = self.server(enabled=True)

        def node_request(node, method, path, payload=None, timeout=10):
            raise TimeoutError("se agotó el tiempo")

        with mock.patch("mt5_manager.manager_http.node_request", node_request):
            live_account_watch.tick(server, NOW)
            live_account_watch.tick(server, NOW)
            self.assertEqual(server.events(), [])
            live_account_watch.tick(server, NOW)
        self.assertEqual(server.events()[0]["kind"], "account_snapshot_node_unavailable")

    def test_a_running_snapshot_is_never_duplicated(self) -> None:
        server = self.server(enabled=True)
        with self._request(snapshot_state("extracting")), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ):
            live_account_watch.tick(server, NOW)
        self.assertFalse([row for row in self.requests if row[0] == "POST"])

    def test_the_turn_is_not_launched_twice_in_the_same_night(self) -> None:
        server = self.server(enabled=True)
        with self._request(snapshot_state("idle")), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ):
            live_account_watch.tick(server, NOW)
            live_account_watch.tick(server, NOW)
        self.assertEqual(len([row for row in self.requests if row[0] == "POST"]), 1)

    # --- Reintentos del lado del agente ------------------------------------
    #
    # Hasta el 2026-10-23 la política de reintentos sólo cubría los fallos al
    # **lanzar**. Un sondeo que el agente aceptaba y devolvía con cuentas sin
    # leer escribía su `completed_at`, cerraba la cita y no se volvía a
    # intentar: el 2026-10-22 AXI volvió con una cuenta sin leer y el turno se
    # dio por bueno.

    def _collected(self):
        return mock.patch(
            "mt5_manager.live_account_report.movements_from_html",
            return_value=[(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
        )

    def _no_expectations(self):
        return mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        )

    def _launch_the_turn(self, server: FakeServer) -> None:
        """La cita de la noche, lanzada y aceptada por el agente."""
        with self._request(snapshot_state("idle")), self._no_expectations():
            live_account_watch.tick(server, NOW)
        # `launch` sella la hora con el reloj de pared, no con el `now` del
        # vigilante: sin esto la espera entre reintentos mide contra la fecha
        # real de quien ejecuta la prueba.
        server.live_account_runs.update(
            server.live_account_runs.key("axi", live_account_watch.SNAPSHOT_KEY),
            last_attempt_at=NOW.astimezone(timezone.utc).isoformat(),
        )

    def _come_back(self, server: FakeServer, state, moment) -> None:
        with self._request(state), self._no_expectations(), self._collected():
            live_account_watch.tick(server, moment)

    def test_a_snapshot_that_comes_back_incomplete_is_retried(self) -> None:
        server = self.server(enabled=True)
        self._launch_the_turn(server)
        self._come_back(server, snapshot_state(), NOW + timedelta(minutes=15))
        posts = [row for row in self.requests if row[0] == "POST"]
        self.assertEqual(len(posts), 2)
        record = server.live_account_runs.record(
            server.live_account_runs.key("axi", live_account_watch.SNAPSHOT_KEY),
        )
        self.assertEqual(record["attempts"], 2)

    def test_the_wait_between_retries_is_respected(self) -> None:
        server = self.server(enabled=True)
        self._launch_the_turn(server)
        self._come_back(server, snapshot_state(), NOW + timedelta(minutes=5))
        self.assertEqual(len([row for row in self.requests if row[0] == "POST"]), 1)

    def test_a_complete_snapshot_closes_the_turn(self) -> None:
        server = self.server(enabled=True)
        self._launch_the_turn(server)
        self._come_back(server, complete_snapshot_state(), NOW + timedelta(minutes=15))
        self.assertEqual(len([row for row in self.requests if row[0] == "POST"]), 1)

    def test_the_incomplete_turn_stops_at_the_last_retry(self) -> None:
        server = self.server(enabled=True, retry_attempts=1)
        self._launch_the_turn(server)
        self._come_back(server, snapshot_state(), NOW + timedelta(minutes=15))
        # El reintento trae su propio sondeo, que vuelve igual de incompleto.
        self._come_back(
            server, snapshot_state(snapshot_id="20261021_234500_000002"),
            NOW + timedelta(minutes=30),
        )
        self._come_back(
            server, snapshot_state(snapshot_id="20261021_234500_000002"),
            NOW + timedelta(minutes=45),
        )
        self.assertEqual(len([row for row in self.requests if row[0] == "POST"]), 2)
        exhausted = [
            event for event in server.events()
            if event["kind"] == "account_snapshot_exhausted"
        ]
        self.assertEqual(len(exhausted), 1)
        self.assertIn("sin leer", exhausted[0]["message"])

    def test_the_blocked_accounts_are_visible_before_arming_it(self) -> None:
        state = node_state()
        state["credential_state"]["audit-105"] = {"source_password_saved": False}
        server = FakeServer(Path(self.folder.name), state)
        targets = live_account_watch.targets(server)
        self.assertEqual([row["login"] for row in targets["blocked"]], ["60082017"])
        self.assertEqual([row["login"] for row in targets["accounts"]], ["60082303"])


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name), node_state())
        self.key = account_key("60082017", "Axi-US50-Live")
        self.server.live_accounts.merge(
            "axi", self.key, login="60082017", server="Axi-US50-Live", currency="EUR",
            movements=[
                (datetime(2026, 9, 15, 8, tzinfo=timezone.utc), 120.0),
                (datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 40.0),
                (datetime(2026, 10, 21, 9, tzinfo=timezone.utc), 30.0),
                (datetime(2026, 10, 21, 12, tzinfo=timezone.utc), -10.0),
            ],
            period_start="", period_end="2026-10-21", balance=1200.0,
            taken_at="2026-10-21T21:10:00+00:00",
        )
        self.server.live_accounts.write_expectation("axi", 105, {
            "signature": "abc",
            "months": {
                "10": {"profit": 600.0, "drawdown": 150.0, "years": [2020, 2021, 2022]},
                "09": {"profit": 300.0, "drawdown": 100.0, "years": [2020, 2021]},
            },
        })

    def payload(self) -> dict[str, object]:
        return live_account_dashboard.snapshot(self.server, NOW)

    def card(self) -> dict[str, object]:
        return next(
            row for row in self.payload()["accounts"] if row["login"] == "60082017"
        )

    def test_the_daily_row_is_the_day_and_carries_no_comparison(self) -> None:
        card = self.card()
        self.assertEqual(card["daily"]["day"], "2026-10-21")
        self.assertAlmostEqual(card["daily"]["profit"], 20.0)
        self.assertAlmostEqual(card["daily"]["drawdown"], 10.0)
        self.assertNotIn("profit_tone", card["daily"])

    def test_the_monthly_row_is_compared_with_that_month_in_the_backtest(self) -> None:
        card = self.card()
        self.assertAlmostEqual(card["monthly"]["profit"], 60.0)
        self.assertAlmostEqual(card["expected_month"]["profit"], 600.0)
        self.assertEqual(card["expected_month"]["years"], [2020, 2021, 2022])
        # El día 21 de 31: la media prorrateada son 406,45 y 60 va por debajo.
        self.assertAlmostEqual(card["expected_month"]["profit_prorated"], 600.0 * 21 / 31)
        self.assertEqual(card["monthly"]["profit_tone"], "bad")

    def test_a_drawdown_under_the_mean_is_good_news(self) -> None:
        card = self.card()
        self.assertAlmostEqual(card["monthly"]["drawdown"], 10.0)
        self.assertEqual(card["monthly"]["drawdown_tone"], "good")

    def test_the_previous_month_is_shown_and_compared(self) -> None:
        card = self.card()
        self.assertEqual(card["previous_month"]["label"], "2026-09")
        self.assertAlmostEqual(card["previous_month"]["profit"], 120.0)
        # 60 este mes contra 120 el pasado: peor, y la diferencia a la vista.
        self.assertEqual(card["previous_month"]["profit_tone"], "bad")
        self.assertAlmostEqual(card["previous_month"]["profit_delta"], -60.0)
        self.assertAlmostEqual(card["expected_previous"]["profit"], 300.0)

    def test_the_comparison_with_the_previous_month_is_about_this_one(self) -> None:
        """El tono habla de **este** mes, no del anterior.

        Pintado sobre el importe de septiembre, un verde se leia como
        «septiembre fue bueno» cuando lo que decia era que octubre va mejor.
        La pantalla lo enseña como nota; aqui se fija que el numero que lo
        decide es la diferencia entre los dos meses.
        """
        card = self.card()
        self.assertAlmostEqual(
            card["previous_month"]["profit_delta"],
            card["monthly"]["profit"] - card["previous_month"]["profit"],
        )
        self.assertAlmostEqual(
            card["previous_month"]["drawdown_delta"],
            card["monthly"]["drawdown"] - card["previous_month"]["drawdown"],
        )

    def test_without_a_stored_mean_there_is_no_colour(self) -> None:
        self.server.live_accounts.write_expectation("axi", 105, {"months": {}})
        card = self.card()
        self.assertIsNone(card["expected_month"]["profit"])
        self.assertEqual(card["monthly"]["profit_tone"], "")

    def test_the_totals_convert_to_one_currency_and_say_what_they_cannot(self) -> None:
        self.server.live_accounts.merge(
            "axi", account_key("60082303", "Axi-US50-Live"), login="60082303",
            server="Axi-US50-Live", currency="GBP",
            movements=[(datetime(2026, 10, 21, 9, tzinfo=timezone.utc), 50.0)],
            period_start="", period_end="2026-10-21",
        )
        totals = self.payload()["totals"]
        self.assertAlmostEqual(totals["daily"], 20.0)
        self.assertAlmostEqual(totals["previous_month"], 120.0)
        self.assertEqual(totals["currency"], "EUR")
        self.assertEqual(totals["missing_rates"], ["GBP"])

    def test_the_payload_carries_the_selection_shared_between_browsers(self) -> None:
        selection_key = f"axi::{self.key}"
        self.server.live_accounts.update_total_selection({
            "account": selection_key, "included": False,
        })
        self.assertEqual(self.payload()["total_selection"], {
            "initialized": True, "excluded_accounts": [selection_key],
        })

    def test_a_dollar_account_is_converted_with_its_stored_rate(self) -> None:
        self.server.live_accounts.merge(
            "axi", account_key("60082303", "Axi-US50-Live"), login="60082303",
            server="Axi-US50-Live", currency="USD",
            movements=[
                (datetime(2026, 9, 21, 9, tzinfo=timezone.utc), 100.0),
                (datetime(2026, 10, 21, 9, tzinfo=timezone.utc), 100.0),
            ],
            period_start="", period_end="2026-10-21",
        )
        totals = self.payload()["totals"]
        self.assertAlmostEqual(totals["daily"], 20.0 + 92.0)
        self.assertAlmostEqual(totals["previous_month"], 120.0 + 92.0)
        self.assertEqual(totals["missing_rates"], [])

    def test_an_account_never_read_says_so_instead_of_showing_zeros(self) -> None:
        card = next(
            row for row in self.payload()["accounts"] if row["login"] == "60082303"
        )
        self.assertEqual(card["state"], "never_read")
        self.assertIn("Sondear ahora", card["message"])

    def test_a_series_older_than_two_days_is_marked_stale(self) -> None:
        self.server.live_accounts.merge(
            "axi", self.key, login="60082017", server="Axi-US50-Live", currency="EUR",
            movements=[], period_start="2026-10-21", period_end="2026-10-21",
            taken_at="2026-10-18T21:10:00+00:00",
        )
        self.assertEqual(self.card()["state"], "stale")

    def test_the_card_names_the_portfolios_the_account_serves(self) -> None:
        card = self.card()
        self.assertEqual(
            [row["name"] for row in card["portfolios"]], ["Port_107_AXI"],
        )
        self.assertAlmostEqual(card["balance"], 1200.0)


if __name__ == "__main__":
    unittest.main()
