from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lxml import html

from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_SETTINGS,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)


class LiveAuditSettingsTests(unittest.TestCase):
    def test_defaults_are_safe_and_do_not_enable_the_future_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            state = store.state("node-a")

        self.assertEqual(state["settings"], DEFAULT_LIVE_AUDIT_SETTINGS)
        self.assertFalse(state["settings"]["enabled"])
        self.assertEqual(state["settings"]["active_job_policy"], "pause_resume")
        self.assertEqual(state["phase"], "configuration_only")
        self.assertFalse(state["configured"])
        self.assertFalse(state["source_password_saved"])
        self.assertFalse(state["tester_password_saved"])

    def test_enabled_configuration_requires_portfolio_and_both_accounts(self) -> None:
        base = {
            "enabled": True,
            "selected_portfolio_ids": [11],
            "source_login": "123456",
            "source_server": "Broker-Live",
            "tester_login": "987654",
            "tester_server": "Broker-Demo",
        }
        for key, error in (
            ("selected_portfolio_ids", "portafolio"),
            ("source_login", "login de la cuenta real"),
            ("source_server", "servidor de la cuenta real"),
            ("tester_login", "login de la cuenta de pruebas"),
            ("tester_server", "servidor de la cuenta de pruebas"),
        ):
            value = dict(base)
            value[key] = [] if key == "selected_portfolio_ids" else ""
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, error):
                normalize_live_audit_settings(value)

    def test_accounts_must_be_different_and_portfolio_ids_are_normalized(self) -> None:
        with self.assertRaisesRegex(ValueError, "logins diferentes"):
            normalize_live_audit_settings({"source_login": "123", "tester_login": "123"})
        normalized = normalize_live_audit_settings({"selected_portfolio_ids": [8, "7", 8]})
        self.assertEqual(normalized["selected_portfolio_ids"], [8, 7])

    def test_unknown_fields_and_invalid_policy_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Campos desconocidos: password"):
            normalize_live_audit_settings({"password": "never-store-this"})
        with self.assertRaisesRegex(ValueError, "pause_resume"):
            normalize_live_audit_settings({"active_job_policy": "interrupt"})

    def test_numeric_limits_and_account_format_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "period_days"):
            normalize_live_audit_settings({"period_days": 0})
        with self.assertRaisesRegex(ValueError, "solo dígitos"):
            normalize_live_audit_settings({"source_login": "12A34"})
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            normalize_live_audit_settings({"daily_audit_time": "25:00"})
        with self.assertRaisesRegex(ValueError, "price_tolerance_points"):
            normalize_live_audit_settings({"price_tolerance_points": "nan"})

    def test_settings_and_encrypted_credentials_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "live_audit_settings.json"
            store = LiveAuditSettingsStore(path)
            saved = store.update("node-a", {
                "enabled": True,
                "selected_portfolio_ids": [11, 12],
                "source_login": "00123456",
                "source_server": "Broker-Live",
                "source_password": "real-secret",
                "tester_login": "00987654",
                "tester_server": "Broker-Demo",
                "tester_password": "tester-secret",
                "period_days": 30,
                "execution_delay_mode": "fixed",
                "fixed_delay_ms": 125,
            })
            reloaded_store = LiveAuditSettingsStore(path)
            reloaded = reloaded_store.state("node-a")
            credentials = reloaded_store.credentials("node-a")
            public_document = path.read_text(encoding="utf-8")
            encrypted_document = (root / "live_audit_credentials.json").read_text(encoding="utf-8")

        self.assertTrue(saved["configured"])
        self.assertTrue(saved["source_password_saved"])
        self.assertTrue(saved["tester_password_saved"])
        self.assertNotIn("source_password", saved)
        self.assertNotIn("tester_password", saved)
        self.assertEqual(reloaded["settings"]["selected_portfolio_ids"], [11, 12])
        self.assertEqual(reloaded["settings"]["source_login"], "00123456")
        self.assertEqual(credentials, {"source_password": "real-secret", "tester_password": "tester-secret"})
        self.assertNotIn("real-secret", public_document + encrypted_document)
        self.assertNotIn("tester-secret", public_document + encrypted_document)

    def test_empty_password_fields_keep_the_saved_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update("node-a", {"source_password": "one", "tester_password": "two"})
            state = store.update("node-a", {"source_password": "", "tester_password": "", "period_days": 14})
            credentials = store.credentials("node-a")

        self.assertTrue(state["source_password_saved"])
        self.assertTrue(state["tester_password_saved"])
        self.assertEqual(credentials, {"source_password": "one", "tester_password": "two"})

    def test_enabling_requires_both_saved_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            with self.assertRaisesRegex(ValueError, "contraseñas"):
                store.update("node-a", {
                    "enabled": True,
                    "selected_portfolio_ids": [11],
                    "source_login": "123",
                    "source_server": "Live",
                    "tester_login": "456",
                    "tester_server": "Demo",
                    "source_password": "only-one",
                })

    def test_legacy_single_account_configuration_is_migrated_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "live_audit_settings.json"
            path.write_text(json.dumps({"node-a": {"settings": {
                **DEFAULT_LIVE_AUDIT_SETTINGS,
                "enabled": True,
                "deployment_name": "old",
                "account_login": "123",
                "account_server": "Live",
                "terminal_path": "terminal64.exe",
            }}}), encoding="utf-8")
            state = LiveAuditSettingsStore(path).state("node-a")

        self.assertFalse(state["settings"]["enabled"])
        self.assertEqual(state["settings"]["source_login"], "123")
        self.assertNotIn("terminal_path", state["settings"])


class LiveAuditConfigurationScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        cls.page_text = (cls.static_dir / "live_audit.html").read_text(encoding="utf-8")
        cls.script = (cls.static_dir / "live_audit.js").read_text(encoding="utf-8")
        cls.manager_script = (cls.static_dir / "app.js").read_text(encoding="utf-8")
        cls.page = html.fromstring(cls.page_text)

    def test_every_node_card_links_to_the_page(self) -> None:
        self.assertIn('/live_audit.html?node=${encodeURIComponent(id)}', self.manager_script)
        self.assertIn("Auditor real", self.manager_script)

    def test_portfolio_selection_is_first_and_only_uses_full_history(self) -> None:
        cards = self.page.xpath('//form[@id="audit-form"]/section')
        self.assertIn("PORTAFOLIOS", cards[0].text_content())
        self.assertIn("/portfolios?scope=full_history", self.script)
        self.assertIn("selected_portfolio_ids", self.script)

    def test_page_has_two_distinct_persistent_credentials_and_no_terminal_path(self) -> None:
        password_names = {element.get("name") for element in self.page.xpath('//input[@type="password"]')}
        self.assertEqual(password_names, {"source_password", "tester_password"})
        self.assertTrue(self.page.xpath('//*[@name="source_login"]'))
        self.assertTrue(self.page.xpath('//*[@name="tester_login"]'))
        self.assertFalse(self.page.xpath('//*[@name="terminal_path"]'))
        self.assertIn("nunca vuelven al navegador", self.page_text)

    def test_form_exposes_public_settings_plus_the_two_password_inputs(self) -> None:
        names = {element.get("name") for element in self.page.xpath('//form[@id="audit-form"]//*[@name]')}
        self.assertEqual(names, set(DEFAULT_LIVE_AUDIT_SETTINGS) | {"source_password", "tester_password"})
        tester = self.page.xpath('//select[@name="tester_model"]/option')[0]
        self.assertEqual(tester.get("value"), "real_ticks")

    def test_pause_resume_policy_and_existing_terminals_are_explicit(self) -> None:
        self.assertIn("pausará antes del trabajo", self.page_text)
        self.assertIn("lo reanudará al finalizar", self.page_text)
        self.assertIn("ya configurados en el agente", self.page_text)
        self.assertIn("active_job_policy: 'pause_resume'", self.script)

    def test_script_reads_and_writes_the_manager_endpoint(self) -> None:
        endpoint = "/live-audit-config`"
        self.assertGreaterEqual(self.script.count(endpoint), 2)
        self.assertIn("if (!form.reportValidity()) return", self.script)
        self.assertIn("source_login', 'source_server', 'tester_login', 'tester_server", self.script)
        self.assertIn("Guardada · deja vacío para conservar", self.script)


if __name__ == "__main__":
    unittest.main()
