"""La ruta que le devuelve al ojo una contraseña ya guardada.

Es el único punto por el que un secreto vuelve al navegador, así que aquí se
fija qué nombra cada referencia y qué pasa con lo que no existe: una cuenta
sin contraseña o una referencia inventada no son un 200 con el campo vacío.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mt5_manager.live_audit_settings_schema import DEFAULT_LIVE_AUDIT_PROFILE
from mt5_manager.manager import ManagerServer


class LiveAuditPasswordRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.manager = ManagerServer(("127.0.0.1", 0), {
            "nodes": [{"id": "test-node", "name": "Test Node", "url": "http://127.0.0.1:1", "token": "x"}],
            "preferences_file": str(root / "launch_preferences.json"),
            "live_audit_settings_file": str(root / "live_audit_settings.json"),
            "portfolio_settings_file": str(root / "portfolio_settings.json"),
        })
        self.store = self.manager.live_audit_settings
        self.store.update("test-node", {
            "selected_audit_ids": ["uso-11"],
            "profiles": {"uso-11": {
                **DEFAULT_LIVE_AUDIT_PROFILE,
                "portfolio_id": 11,
                "portfolio_type": "balanced",
                "source_login": "10010194",
                "source_server": "Axi-US50-Demo",
                "tester_login": "60268704",
                "tester_server": "Axi-US51-Live",
                "source_password": "la-de-la-cuenta-real",
                "tester_password": "la-de-pruebas",
            }},
        })
        self.store.update_restore_account("test-node", {
            "login": "60268704", "server": "Axi-US51-Live", "password": "la-final-del-terminal",
        })
        self.thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.manager.server_address[1]}"

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.manager.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, reference: str) -> tuple[int, dict]:
        path = f"/api/nodes/test-node/live-audit-password/{urllib.parse.quote(reference, safe='')}"
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_every_password_of_the_screen_comes_back_by_its_public_reference(self) -> None:
        # Las mismas referencias que ya publica el catálogo de cuentas: la
        # pantalla pide con lo que tiene en la mano.
        self.assertEqual(self.request("profile:uso-11:source"), (200, {"password": "la-de-la-cuenta-real"}))
        self.assertEqual(self.request("profile:uso-11:tester"), (200, {"password": "la-de-pruebas"}))
        self.assertEqual(self.request("restore:terminal"), (200, {"password": "la-final-del-terminal"}))

    def test_the_catalog_ids_are_exactly_what_this_route_accepts(self) -> None:
        for account in self.store.state("test-node")["saved_accounts"]:
            with self.subTest(account=account["id"]):
                status, body = self.request(account["id"])
                self.assertEqual(status, 200)
                self.assertTrue(body["password"])

    def test_what_is_not_saved_is_a_miss_and_not_an_empty_password(self) -> None:
        for reference in (
            "profile:uso-que-no-existe:source",
            "profile:uso-11:cualquiera",
            "restore:otro",
            "uso-11",
        ):
            with self.subTest(reference=reference):
                status, body = self.request(reference)
                self.assertEqual(status, 404)
                self.assertTrue(body["error"])

    def test_a_token_that_no_longer_decrypts_is_a_miss_and_not_a_crash(self) -> None:
        # Clave cambiada o credenciales copiadas de otro equipo: para quien
        # pregunta es lo mismo que no tenerla, no un 500 de `cryptography`.
        self.store.credential_records["test-node"]["uso-11"]["source_password"] = "no-es-un-token"
        status, body = self.request("profile:uso-11:source")
        self.assertEqual(status, 404)
        self.assertIn("ya no se puede descifrar", body["error"])

    def test_an_unknown_node_does_not_reach_another_nodes_secret(self) -> None:
        path = "/api/nodes/otro-nodo/live-audit-password/profile:uso-11:source"
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                status, body = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            status, body = error.code, json.loads(error.read())
        self.assertEqual(status, 404)
        self.assertNotIn("la-de-la-cuenta-real", json.dumps(body))

    def test_the_public_state_still_answers_only_yes_or_no(self) -> None:
        # Que exista la ruta no significa que el estado lleve secretos: la
        # tarjeta se sigue pintando con `*_password_saved`.
        state = json.dumps(self.store.state("test-node"))
        self.assertIn('"source_password_saved": true', state)
        for secret in ("la-de-la-cuenta-real", "la-de-pruebas", "la-final-del-terminal"):
            self.assertNotIn(secret, state)


if __name__ == "__main__":
    unittest.main()
