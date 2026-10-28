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
    live_account_dashboard, live_account_expectation, live_account_ingest,
    live_account_inventory, live_account_report, live_account_watch,
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


class FakeSource:
    """Lo mínimo que `_rebuild` le pide a la memoria del agente."""

    project = ""

    def saved_portfolio_detail(self, portfolio_id, scope):
        return {"portfolio": {"id": portfolio_id, "members": []}}


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

    def test_an_account_just_added_waits_for_the_next_appointment(self) -> None:
        """2026-10-24: un portafolio nuevo en ICTrading lanzó el sondeo en
        sábado, fuera de lunes-viernes, porque el nodo no tenía ninguna cuenta
        hasta entonces y la cita del viernes parecía incumplida desde
        siempre. La cuenta nació el sábado por la mañana (`audit-62-mvm3z7k0-1`
        = 2026-10-24T08:00:00Z); el sábado a las 23:07 de Madrid no debe
        lanzar nada, porque la cita vigente para una cuenta tan nueva es la
        del lunes siguiente, no la del viernes anterior.
        """
        state = {
            "configured_audit_ids": ["audit-62-mvm3z7k0-1"],
            "profiles": {
                "audit-62-mvm3z7k0-1": profile(
                    portfolio_id=62, deployment_name="Nuevo", source_login="99999",
                ),
            },
            "credential_state": {"audit-62-mvm3z7k0-1": {"source_password_saved": True}},
            "restore_account": {"configured": True},
        }
        server = FakeServer(Path(self.folder.name), state, enabled=True)
        saturday_night = datetime(2026, 10, 24, 23, 7, tzinfo=ZoneInfo("Europe/Madrid"))
        with self._request(snapshot_state("idle", accounts={})), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ):
            live_account_watch.tick(server, saturday_night)
        self.assertEqual([row for row in self.requests if row[0] == "POST"], [])

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
                normalization_version=live_account_report.series_version({}),
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
            "mt5_manager.live_account_report.account_from_html",
            return_value=(
                [(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
                [("2026-10-20", 1_000.0)],
            ),
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
            "mt5_manager.live_account_report.account_from_html",
            return_value=(
                [(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
                [("2026-10-20", 1_000.0)],
            ),
        ):
            live_account_watch.tick(server, NOW)
        event = server.events()[0]
        self.assertEqual(event["kind"], "account_snapshot_collected")
        self.assertEqual(event["level"], "warning")
        self.assertIn("1 cuenta(s) y 1 cierre(s)", event["message"])
        self.assertIn("no quedó conectada", event["message"])

    def test_the_bell_says_the_snapshot_finished_and_not_only_that_it_happened(self) -> None:
        # Es el «ya está»: entre el clic y esto el agente lleva un rato dentro
        # de las terminales y no hay serie nueva que mirar. El usuario lo pidió
        # el 2026-10-24, después de lanzar un sondeo y no enterarse de nada.
        server = self.server()
        with self._request(snapshot_state()), mock.patch(
            "mt5_manager.live_account_ingest.refresh_expectations", return_value=0,
        ), mock.patch(
            "mt5_manager.live_account_report.account_from_html",
            return_value=(
                [(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
                [("2026-10-20", 1_000.0)],
            ),
        ):
            live_account_watch.tick(server, NOW)
        self.assertIn("Sondeo de cuentas terminado", server.events()[0]["title"])

    def test_a_scale_change_is_not_a_failure_and_does_not_burn_the_retries(self) -> None:
        """La captura llegó entera; lo que ya no sirve es su escala.

        Contarlo como fallo gastaba un reintento en una lectura que nunca se
        iba a poder usar, y con tres de tres la serie se quedaba sin reparar
        hasta la cita siguiente. Lo que hace falta es **otra** lectura.
        """
        self.assertEqual(live_account_ingest._outcome(0, [], ["a: escala"]), "stale")
        self.assertEqual(live_account_ingest._outcome(2, [], ["a: escala"]), "stale")
        self.assertEqual(live_account_ingest._outcome(0, ["a: roto"], []), "failed")
        self.assertEqual(live_account_ingest._outcome(2, ["a: roto"], []), "partial")
        self.assertEqual(live_account_ingest._outcome(2, [], []), "success")
        self.assertEqual(live_account_ingest._outcome(0, [], []), "failed")
        # Sigue dejando la cita sin cumplir: hace falta otra lectura.
        self.assertIn("stale", live_account_watch.UNFINISHED_OUTCOMES)
        self.assertEqual(
            live_account_watch.retry_record({"outcome": "stale"})["outcome"], "failed",
        )
        self.assertEqual(live_account_watch.LEVELS_BY_OUTCOME["stale"], "info")

    def test_a_new_criterion_is_measured_once_and_not_every_turn(self) -> None:
        """El atajo de la firma sólo vale dentro del mismo criterio.

        La firma resume las **entradas**; con un criterio nuevo las mismas
        entradas dan otro número. Reescribir lo guardado le devolvía su
        criterio viejo, así que `_needs_rebuild` volvía a pedirlo en la vuelta
        siguiente y la memoria UBS del agente se abría cada minuto. Medido el
        2026-10-24: ocho portafolios se quedaron en el criterio anterior
        después de subirlo.
        """
        server = self.server()
        stored = {
            "signature": "firma", "criterion": "criterio-anterior",
            "months": {"10": {"profit": 1.0, "drawdown": 0.0, "years": [2020]}},
        }
        server.live_accounts.write_expectation("axi", 105, stored)
        calls = []
        with mock.patch(
            "mt5_manager.live_account_expectation.signature", return_value="firma",
        ), mock.patch(
            "mt5_manager.live_account_expectation.build",
            side_effect=lambda *args, **kwargs: calls.append(1) or {
                "signature": "firma",
                "criterion": live_account_expectation.CRITERION_VERSION,
                "months": {},
            },
        ):
            live_account_ingest._rebuild(
                server, FakeSource(), "axi",
                {"account_key": "x", "real_strategy_lots": {}},
                {"portfolio_id": 105, "portfolio_type": "", "audit_id": "audit-105"},
                NOW,
            )
        self.assertEqual(calls, [1])
        self.assertEqual(
            server.live_accounts.read_expectation("axi", 105)["criterion"],
            live_account_expectation.CRITERION_VERSION,
        )

    def test_every_refresh_keeps_both_saved_portfolio_valleys(self) -> None:
        class ValleySource(FakeSource):
            def saved_portfolio_detail(self, portfolio_id, scope):
                return {"portfolio": {
                    "id": portfolio_id, "members": [], "capital": 1_000,
                    "target_valley_dd": 63, "actual_valley_dd": 57.06,
                }}

        server = self.server()
        with mock.patch(
            "mt5_manager.live_account_expectation.build",
            return_value={
                "signature": "x", "criterion": live_account_expectation.CRITERION_VERSION,
                "months": {},
            },
        ):
            live_account_ingest._rebuild(
                server, ValleySource(), "axi",
                {"account_key": "x", "real_strategy_lots": {}},
                {"portfolio_id": 105, "portfolio_type": "balanced", "audit_id": "audit-105"},
                NOW,
            )
        stored = server.live_accounts.read_expectation("axi", 105)
        self.assertEqual(stored["portfolio_valley_limit"], 63.0)
        self.assertEqual(stored["portfolio_historical_valley"], 57.06)

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
            "mt5_manager.live_account_report.account_from_html",
            return_value=(
                [(datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 7.5)],
                [("2026-10-20", 1_000.0)],
            ),
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


if __name__ == "__main__":
    unittest.main()
