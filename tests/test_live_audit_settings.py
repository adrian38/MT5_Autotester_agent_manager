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
        self.assertEqual(state["settings"]["tester_model"], "real_ticks")
        self.assertEqual(state["phase"], "configuration_only")
        self.assertFalse(state["configured"])

    def test_enabled_configuration_requires_account_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "login"):
            normalize_live_audit_settings({"enabled": True, "account_server": "Broker-Live"})
        with self.assertRaisesRegex(ValueError, "servidor"):
            normalize_live_audit_settings({"enabled": True, "account_login": "123456"})

    def test_password_and_unknown_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Campos desconocidos: password"):
            normalize_live_audit_settings({"password": "never-store-this"})

    def test_numeric_limits_and_account_format_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "period_days"):
            normalize_live_audit_settings({"period_days": 0})
        with self.assertRaisesRegex(ValueError, "solo dígitos"):
            normalize_live_audit_settings({"account_login": "12A34"})
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            normalize_live_audit_settings({"daily_audit_time": "25:00"})
        with self.assertRaisesRegex(ValueError, "price_tolerance_points"):
            normalize_live_audit_settings({"price_tolerance_points": "nan"})

    def test_settings_are_persisted_per_node_and_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "live_audit_settings.json"
            store = LiveAuditSettingsStore(path)
            saved = store.update("node-a", {
                "enabled": True,
                "deployment_name": "UBS real",
                "account_login": "00123456",
                "account_server": "Broker-Live",
                "period_days": 30,
                "execution_delay_mode": "fixed",
                "fixed_delay_ms": 125,
            })
            reloaded = LiveAuditSettingsStore(path).state("node-a")
            other = LiveAuditSettingsStore(path).state("node-b")

        self.assertTrue(saved["configured"])
        self.assertIsNotNone(saved["updated_at"])
        self.assertEqual(reloaded["settings"]["account_login"], "00123456")
        self.assertEqual(reloaded["settings"]["period_days"], 30)
        self.assertEqual(reloaded["settings"]["fixed_delay_ms"], 125)
        self.assertEqual(other["settings"], DEFAULT_LIVE_AUDIT_SETTINGS)

    def test_persisted_document_contains_no_secret_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "live_audit_settings.json"
            LiveAuditSettingsStore(path).update("node-a", {
                "account_login": "123456",
                "account_server": "Broker-Live",
            })
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertNotIn("password", json.dumps(persisted).lower())


class LiveAuditConfigurationScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        cls.page_text = (cls.static_dir / "live_audit.html").read_text(encoding="utf-8")
        cls.script = (cls.static_dir / "live_audit.js").read_text(encoding="utf-8")
        cls.manager_script = (cls.static_dir / "app.js").read_text(encoding="utf-8")
        cls.page = html.fromstring(cls.page_text)

    def test_every_node_card_links_to_the_new_page(self) -> None:
        self.assertIn('/live_audit.html?node=${encodeURIComponent(id)}', self.manager_script)
        self.assertIn("Auditor real", self.manager_script)

    def test_page_is_explicitly_configuration_only_and_has_no_password_input(self) -> None:
        self.assertIn("MVP · solo configuración", self.page_text)
        self.assertIn("Todavía no conecta con MT5", self.page_text)
        self.assertIn("No se guarda ninguna contraseña", self.page_text)
        self.assertFalse(self.page.xpath('//input[@type="password"]'))
        self.assertFalse(self.page.xpath('//*[@name="password"]'))

    def test_form_exposes_connection_schedule_tester_and_tolerances(self) -> None:
        names = {element.get("name") for element in self.page.xpath('//form[@id="audit-form"]//*[@name]')}
        self.assertEqual(names, set(DEFAULT_LIVE_AUDIT_SETTINGS))
        tester = self.page.xpath('//select[@name="tester_model"]/option')[0]
        self.assertEqual(tester.get("value"), "real_ticks")

    def test_script_reads_and_writes_the_manager_endpoint(self) -> None:
        endpoint = "/live-audit-config`"
        self.assertGreaterEqual(self.script.count(endpoint), 2)
        self.assertIn("if (!form.reportValidity()) return", self.script)
        self.assertIn("account_login').required = enabled", self.script)
        self.assertIn("account_server').required = enabled", self.script)


if __name__ == "__main__":
    unittest.main()
