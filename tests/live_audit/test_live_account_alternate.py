"""La cuenta que se sondea en una tarjeta cuando no es la suya.

Petición del usuario (2026-11-17): el icono de configuración de cada tarjeta
guarda otra cuenta y el agente con el que leerla; desde ahí el sondeo de esa
tarjeta —y la relectura del patrimonio, que pide el mismo informe— entran en
esa cuenta. Sin nada guardado no cambia nada.

Lo que se comprueba aquí es justo lo que puede salir mal en silencio:

- que la tarjeta desviada **deje** el sondeo de su agente y **entre** en el
  del agente elegido, porque entrar en una cuenta de otro bróker necesita un
  terminal de ese bróker;
- que lo leído se archive bajo la tarjeta y no bajo quien lo leyó, porque si
  no la portada y el patrimonio no verían nada y nada lo diría;
- que la contraseña no vuelva nunca a la pantalla;
- y que la auditoría no se entere: el perfil no se toca.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import (
    live_account_alternate, live_account_dashboard, live_account_ingest,
    live_account_wealth_watch,
)
from mt5_manager.live_account.live_account_alternate import LiveAccountAlternateStore
from mt5_manager.live_account.live_account_archive import LiveAccountArchive
from mt5_manager.live_audit.live_audit_notifications import LiveAuditNotificationStore
from mt5_manager.live_audit.live_audit_watch import LiveAuditRunState
from mt5_manager.manager import manager_account_routes

from .live_account_fixtures import REPORT, SHA, _test_cipher, profile

NOW = datetime(2026, 11, 17, 22, tzinfo=timezone.utc)
AXI_KEY = "60082017@Axi-US50-Live"
NODES = [
    {"id": "axi", "name": "AXI Standard", "portfolio_broker": "AXI"},
    {"id": "robo", "name": "RoboForex ECN", "portfolio_broker": "ROBOFOREX"},
]


class Settings:
    """Lo mínimo del almacén del auditor: perfiles y contraseñas por nodo."""

    def __init__(self, states: dict[str, dict[str, Any]]) -> None:
        self.states = states

    def state(self, node_id: str) -> dict[str, Any]:
        return self.states.get(node_id) or {"profiles": {}, "credential_state": {}}

    def credentials(self, node_id: str, audit_id: str) -> dict[str, str]:
        saved = (self.state(node_id).get("credential_state") or {}).get(audit_id) or {}
        return {"source_password": "perfil"} if saved.get("source_password_saved") else {}

    def restore_credentials(self, _node_id: str) -> dict[str, str]:
        return {
            "restore_login": "9", "restore_server": "Axi-US51-Live",
            "restore_password": "final",
        }


def node_state(login: str, password: bool = True) -> dict[str, Any]:
    return {
        "profiles": {"audit-105": profile(source_login=login)},
        "credential_state": {"audit-105": {"source_password_saved": password}},
    }


class Server:
    """Dos agentes con una cuenta real cada uno, y nada más."""

    def __init__(self, root: Path, states: dict[str, dict[str, Any]] | None = None) -> None:
        self.nodes = list(NODES)
        self.live_audit_settings = Settings(states or {
            "axi": node_state("60082017"), "robo": node_state("77028839"),
        })
        self.live_accounts = LiveAccountArchive(root / "live_accounts")
        self.live_account_runs = LiveAuditRunState(root / "cron.json")
        self.live_audit_notifications = LiveAuditNotificationStore(root / "avisos.json")
        self.live_account_alternates = LiveAccountAlternateStore(
            root / "alternates.json", _test_cipher,
        )
        self.live_account_scheduler_settings = {"overlap_days": 3}


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.server = Server(self.root)
        self.store = self.server.live_account_alternates

    def configure(self, **changes: Any) -> dict[str, Any]:
        return self.store.save("axi", AXI_KEY, {
            "node_id": "robo", "login": "77050001",
            "server": "RoboForex-ECN", "password": "otra", **changes,
        })


class AlternateStoreTests(StoreTestCase):
    def test_what_the_screen_gets_back_never_includes_the_password(self) -> None:
        saved = self.configure()
        self.assertEqual(saved["login"], "77050001")
        self.assertTrue(saved["password_saved"])
        self.assertNotIn("password", saved)

    def test_the_password_is_stored_encrypted_and_comes_back_in_the_clear(self) -> None:
        self.configure()
        stored = (self.root / "alternates.json").read_text(encoding="utf-8")
        self.assertNotIn("otra", stored)
        self.assertEqual(
            self.store.credentials("axi", AXI_KEY)["password"], "otra",
        )

    def test_an_empty_password_keeps_the_one_already_saved(self) -> None:
        """Es lo que permite cambiar de agente sin volver a teclearla."""
        self.configure()
        self.configure(node_id="axi", password="")
        credentials = self.store.credentials("axi", AXI_KEY)
        self.assertEqual(credentials["password"], "otra")
        self.assertEqual(credentials["node_id"], "axi")

    def test_the_first_save_demands_a_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "contraseña"):
            self.configure(password="")

    def test_a_field_left_empty_is_refused_instead_of_guessed(self) -> None:
        for field in ("node_id", "login", "server"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.configure(**{field: "  "})

    def test_removing_it_leaves_the_card_as_it_was(self) -> None:
        self.configure()
        self.assertFalse(self.store.remove("axi", AXI_KEY)["configured"])
        self.assertEqual(self.store.credentials("axi", AXI_KEY), {})

    def test_a_card_key_without_its_node_is_not_a_card(self) -> None:
        with self.assertRaises(ValueError):
            live_account_alternate.split_key("60082017@Axi-US50-Live")
        self.assertEqual(
            live_account_alternate.split_key("axi::60082017@Axi-US50-Live"),
            ("axi", "60082017@Axi-US50-Live"),
        )


class ReadingTests(StoreTestCase):
    def logins(self, node_id: str) -> list[str]:
        return [
            reading.login
            for reading in live_account_alternate.readings(self.server, node_id)
        ]

    def test_without_anything_configured_each_agent_reads_its_own(self) -> None:
        self.assertEqual(self.logins("axi"), ["60082017"])
        self.assertEqual(self.logins("robo"), ["77028839"])

    def test_the_card_leaves_its_agent_and_enters_the_chosen_one(self) -> None:
        self.configure()
        self.assertEqual(self.logins("axi"), [])
        self.assertEqual(sorted(self.logins("robo")), ["77028839", "77050001"])

    def test_the_reading_keeps_the_card_it_belongs_to(self) -> None:
        self.configure()
        found = [
            reading for reading in live_account_alternate.readings(self.server, "robo")
            if reading.alternate
        ]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].owner_node_id, "axi")
        self.assertEqual(found[0].account_key, AXI_KEY)

    def test_what_travels_is_the_card_key_and_the_other_password(self) -> None:
        """La clave es la de la tarjeta: es con la que vuelve el informe."""
        self.configure()
        payload = live_account_alternate.payload(
            live_account_alternate.readings(self.server, "robo"),
        )
        self.assertIn({
            "account_key": AXI_KEY, "login": "77050001",
            "server": "RoboForex-ECN", "password": "otra",
        }, payload)

    def test_the_same_agent_is_a_valid_choice(self) -> None:
        self.configure(node_id="axi")
        self.assertEqual(self.logins("axi"), ["77050001"])
        self.assertEqual(self.logins("robo"), ["77028839"])

    def test_a_card_without_its_profile_password_can_still_be_read(self) -> None:
        """Una cuenta bloqueada es justo de las que se arreglan guardando otra."""
        server = Server(self.root, {
            "axi": node_state("60082017", password=False),
            "robo": node_state("77028839"),
        })
        server.live_account_alternates = self.store
        self.assertEqual(
            [reading.login for reading in live_account_alternate.readings(server, "axi")],
            [],
        )
        self.configure(node_id="axi")
        self.assertEqual(
            [reading.login for reading in live_account_alternate.readings(server, "axi")],
            ["77050001"],
        )

    def test_a_password_that_no_longer_decrypts_reads_nothing(self) -> None:
        """Antes eso que leer la cuenta que la tarjeta ya no usa.

        La tarjeta se queda sin sondear, que se ve en la portada; un número de
        otra cuenta no se vería.
        """
        self.configure()
        self.store.records[f"axi::{AXI_KEY}"]["password"] = "roto"
        self.assertEqual(self.logins("robo"), ["77028839"])
        self.assertEqual(self.logins("axi"), [])


class CollectTests(StoreTestCase):
    def snapshot(self) -> dict[str, Any]:
        return {
            "snapshot_id": "20261117_220000", "status": "completed",
            "finished_at": "2026-11-17T22:10:00+00:00",
            "period_start": "2026-11-14T00:00:00+00:00",
            "period_end": "2026-11-17T22:00:00+00:00",
            "accounts": {
                AXI_KEY: {
                    "login": "77050001", "server": "RoboForex-ECN", "currency": "EUR",
                    "balance": 1_200.0, "equity": 1_210.0, "status": "completed",
                    "report": {"filename": "cuenta.html", "sha256": SHA},
                },
            },
        }

    def collect(self) -> dict[str, Any] | None:
        with mock.patch.object(
            live_account_ingest.manager_http, "node_artifact_request",
            return_value=(200, REPORT, "text/html"),
        ), mock.patch.object(
            live_account_ingest.live_account_report, "account_from_html",
            return_value=(
                [(datetime(2026, 11, 16, 9, tzinfo=timezone.utc), 7.5)],
                [(datetime(2026, 11, 16, 9, tzinfo=timezone.utc), 8.0)],
                [("2026-11-16", 1_200.0)], [],
            ),
        ):
            return live_account_ingest.collect(
                self.server, NODES[1], "robo", self.snapshot(),
            )

    def test_what_the_other_agent_read_is_archived_under_the_card(self) -> None:
        self.configure()
        collected = self.collect()
        self.assertEqual(collected["accounts"], 1)
        self.assertEqual(len(self.server.live_accounts.read("axi", AXI_KEY)["movements"]), 1)
        self.assertEqual(self.server.live_accounts.read("robo", AXI_KEY)["movements"], [])

    def test_without_a_detour_it_is_archived_where_it_was_read(self) -> None:
        collected = self.collect()
        self.assertEqual(collected["accounts"], 1)
        self.assertEqual(len(self.server.live_accounts.read("robo", AXI_KEY)["movements"]), 1)

    def test_the_card_is_fed_by_the_configured_account_on_every_snapshot(self) -> None:
        """Lo que el usuario pidió, de punta a punta (2026-11-17).

        El sondeo entra en la cuenta guardada, lo leído se archiva bajo la
        tarjeta, y la tarjeta enseña **esos** números: el saldo y el beneficio
        de ayer son los de la cuenta configurada, no los del perfil. Es la
        vuelta entera, y es lo único que demuestra que las dos mitades —a
        quién se lee y dónde se guarda— encajan.
        """
        from mt5_manager.live_account import live_account_inventory

        self.configure()
        self.collect()
        account = live_account_inventory.node_accounts(self.server, "axi")[0]
        card = live_account_dashboard.account_card(
            self.server, NODES[0], "axi", account,
            {"display_currency": "EUR", "exchange_rates": {}}, NOW,
        )
        self.assertEqual(card["balance"], 1_200.0)
        # 8,0 y no 7,5: la tarjeta enseña la columna Profit de MT5, que es lo
        # que MT5 pone en grande; 7,5 es el neto con comisión y swap.
        self.assertEqual(card["daily"]["profit"], 8.0)
        self.assertEqual(card["state"], "")
        # Y sigue siendo la tarjeta de siempre, con su aviso de dónde salen.
        self.assertEqual(card["login"], "60082017")
        self.assertEqual(card["alternate"]["login"], "77050001")


class WealthTests(StoreTestCase):
    def test_the_full_reread_follows_the_card_to_its_agent(self) -> None:
        self.configure()
        self.assertEqual(
            [
                reading.login
                for reading in live_account_wealth_watch.summing_readings(self.server, "axi")
            ],
            [],
        )
        self.assertIn("77050001", [
            reading.login
            for reading in live_account_wealth_watch.summing_readings(self.server, "robo")
        ])

    def test_a_card_that_does_not_add_up_is_not_reread_either(self) -> None:
        self.configure()
        self.server.live_accounts.update_total_selection(
            {"account": f"axi::{AXI_KEY}", "included": False},
        )
        self.assertEqual(
            [
                reading.login
                for reading in live_account_wealth_watch.summing_readings(self.server, "robo")
            ],
            ["77028839"],
        )


class DashboardTests(StoreTestCase):
    def card(self, server: Any = None) -> dict[str, Any]:
        target = server or self.server
        from mt5_manager.live_account import live_account_inventory

        account = live_account_inventory.node_accounts(target, "axi")[0]
        return live_account_dashboard.account_card(
            target, NODES[0], "axi", account,
            {"display_currency": "EUR", "exchange_rates": {}}, NOW,
        )

    def test_the_card_says_which_account_it_really_reads(self) -> None:
        self.configure()
        alternate = self.card()["alternate"]
        self.assertTrue(alternate["configured"])
        self.assertEqual(alternate["login"], "77050001")
        self.assertEqual(alternate["node_id"], "robo")

    def test_the_card_keeps_its_own_identity(self) -> None:
        """El perfil no se toca: el título sigue siendo la cuenta de siempre."""
        self.configure()
        card = self.card()
        self.assertEqual(card["login"], "60082017")
        self.assertEqual(card["account_key"], AXI_KEY)

    def test_a_configured_card_is_no_longer_blocked(self) -> None:
        server = Server(self.root, {
            "axi": node_state("60082017", password=False),
            "robo": node_state("77028839"),
        })
        server.live_account_alternates = self.store
        self.assertEqual(self.card(server)["state"], "blocked")
        self.configure()
        self.assertEqual(self.card(server)["state"], "never_read")


class RouteTests(StoreTestCase):
    """La ruta que guarda y quita, con la misma clave que usa «Sumar»."""

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        answer: dict[str, Any] = {}

        def node(node_id: str) -> dict[str, Any]:
            found = [row for row in NODES if row["id"] == node_id]
            if not found:
                raise KeyError(f"Nodo desconocido: {node_id}")
            return found[0]

        handler = SimpleNamespace(
            server=self.server, _body=lambda: body, _node=node,
            _send_json=lambda status, payload: answer.update(
                status=status, body=payload,
            ),
        )
        self.assertTrue(manager_account_routes.handle_post(
            handler, SimpleNamespace(path="/api/live-account-alternate"), [],
        ))
        return answer

    def test_saving_answers_with_what_the_screen_may_see(self) -> None:
        answer = self.post({
            "account": f"axi::{AXI_KEY}", "node_id": "robo",
            "login": "77050001", "server": "RoboForex-ECN", "password": "otra",
        })
        self.assertEqual(answer["status"], 200)
        self.assertTrue(answer["body"]["configured"])
        self.assertNotIn("password", answer["body"])

    def test_clearing_puts_the_card_back_on_its_own_account(self) -> None:
        self.configure()
        answer = self.post({"account": f"axi::{AXI_KEY}", "clear": True})
        self.assertEqual(answer["status"], 200)
        self.assertFalse(answer["body"]["configured"])

    def test_an_agent_that_does_not_exist_is_a_404(self) -> None:
        answer = self.post({
            "account": f"axi::{AXI_KEY}", "node_id": "inventado",
            "login": "1", "server": "s", "password": "p",
        })
        self.assertEqual(answer["status"], 404)
        self.assertFalse(self.store.public("axi", AXI_KEY)["configured"])

    def test_a_card_key_that_is_not_one_is_a_400(self) -> None:
        self.assertEqual(self.post({"account": "sin-nodo"})["status"], 400)


if __name__ == "__main__":
    unittest.main()
