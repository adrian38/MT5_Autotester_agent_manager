from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lxml import html

from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)


def profile(source_login: str, tester_login: str, **changes: object) -> dict[str, object]:
    return {
        **DEFAULT_LIVE_AUDIT_PROFILE,
        "source_login": source_login,
        "source_server": "Broker-Live",
        "tester_login": tester_login,
        "tester_server": "Broker-Demo",
        **changes,
    }


class LiveAuditSettingsTests(unittest.TestCase):
    def test_defaults_have_no_unrequested_enable_switch(self) -> None:
        self.assertNotIn("enabled", DEFAULT_LIVE_AUDIT_PROFILE)
        self.assertNotIn("selected_portfolio_ids", DEFAULT_LIVE_AUDIT_PROFILE)
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["active_job_policy"], "pause_resume")
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["min_tick_history_quality_pct"], 80.0)
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["audit_interval_days"], 1)
        for obsolete in ("sync_interval_minutes", "daily_audit_time", "heartbeat_timeout_minutes"):
            self.assertNotIn(obsolete, DEFAULT_LIVE_AUDIT_PROFILE)

    def test_profile_logins_must_be_numeric_but_may_match(self) -> None:
        normalized = normalize_live_audit_settings({"source_login": "123", "tester_login": "123"})
        self.assertEqual(normalized["source_login"], "123")
        self.assertEqual(normalized["tester_login"], "123")
        with self.assertRaisesRegex(ValueError, "solo dígitos"):
            normalize_live_audit_settings({"source_login": "12A34"})

    def test_same_login_can_be_saved_with_independent_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            saved = store.update("node-a", {
                "selected_portfolio_ids": [11],
                "profiles": {"11": {
                    **profile("123", "123"),
                    "source_password": "real-secret",
                    "tester_password": "tester-secret",
                }},
            })
            credentials = store.credentials("node-a", 11)

        self.assertEqual(saved["configured_portfolio_ids"], [11])
        self.assertEqual(credentials, {
            "source_password": "real-secret",
            "tester_password": "tester-secret",
        })

    def test_profile_numeric_limits_and_fixed_policy_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "period_days"):
            normalize_live_audit_settings({"period_days": 0})
        with self.assertRaisesRegex(ValueError, "audit_interval_days"):
            normalize_live_audit_settings({"audit_interval_days": 0})
        with self.assertRaisesRegex(ValueError, "min_tick_history_quality_pct"):
            normalize_live_audit_settings({"min_tick_history_quality_pct": 100.1})
        with self.assertRaisesRegex(ValueError, "pause_resume"):
            normalize_live_audit_settings({"active_job_policy": "interrupt"})

    def test_obsolete_minute_schedule_is_migrated_to_a_daily_audit(self) -> None:
        normalized = normalize_live_audit_settings({
            "sync_interval_minutes": 5,
            "daily_audit_time": "00:30",
            "heartbeat_timeout_minutes": 5,
        })
        self.assertEqual(normalized["audit_interval_days"], 1)
        for obsolete in ("sync_interval_minutes", "daily_audit_time", "heartbeat_timeout_minutes"):
            self.assertNotIn(obsolete, normalized)

    def test_two_portfolios_keep_independent_profiles_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "live_audit_settings.json"
            store = LiveAuditSettingsStore(path)
            first = profile("001111", "009111", period_days=7)
            second = profile("002222", "009222", period_days=30)
            saved = store.update("node-a", {
                "selected_portfolio_ids": [11, 12],
                "profiles": {
                    "11": {**first, "source_password": "real-11", "tester_password": "test-11"},
                    "12": {**second, "source_password": "real-12", "tester_password": "test-12"},
                },
            })
            reloaded_store = LiveAuditSettingsStore(path)
            reloaded = reloaded_store.state("node-a")
            credentials_11 = reloaded_store.credentials("node-a", 11)
            credentials_12 = reloaded_store.credentials("node-a", 12)
            encrypted = (root / "live_audit_credentials.json").read_text(encoding="utf-8")

        self.assertEqual(saved["configured_portfolio_ids"], [11, 12])
        self.assertEqual(reloaded["profiles"]["11"]["period_days"], 7)
        self.assertEqual(reloaded["profiles"]["12"]["period_days"], 30)
        self.assertEqual(credentials_11, {"source_password": "real-11", "tester_password": "test-11"})
        self.assertEqual(credentials_12, {"source_password": "real-12", "tester_password": "test-12"})
        for secret in ("real-11", "test-11", "real-12", "test-12"):
            self.assertNotIn(secret, encrypted)

    def test_empty_passwords_preserve_each_portfolios_saved_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update("node-a", {
                "selected_portfolio_ids": [11],
                "profiles": {"11": {
                    **profile("111", "911"),
                    "source_password": "one",
                    "tester_password": "two",
                }},
            })
            saved = store.update("node-a", {
                "selected_portfolio_ids": [11],
                "profiles": {"11": {
                    **profile("111", "911", period_days=14),
                    "source_password": "",
                    "tester_password": "",
                }},
            })
            credentials = store.credentials("node-a", 11)

        self.assertEqual(saved["profiles"]["11"]["period_days"], 14)
        self.assertEqual(credentials, {"source_password": "one", "tester_password": "two"})

    def test_every_selected_portfolio_requires_its_own_profile_and_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            with self.assertRaisesRegex(ValueError, "portafolio #12"):
                store.update("node-a", {
                    "selected_portfolio_ids": [11, 12],
                    "profiles": {"11": {
                        **profile("111", "911"),
                        "source_password": "one",
                        "tester_password": "two",
                    }},
                })
            with self.assertRaisesRegex(ValueError, "contraseñas del portafolio #11"):
                store.update("node-a", {
                    "selected_portfolio_ids": [11],
                    "profiles": {"11": profile("111", "911")},
                })

    def test_unselected_portfolio_profile_is_retained_for_later(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update("node-a", {
                "selected_portfolio_ids": [11, 12],
                "profiles": {
                    "11": {**profile("111", "911"), "source_password": "a", "tester_password": "b"},
                    "12": {**profile("222", "922"), "source_password": "c", "tester_password": "d"},
                },
            })
            state = store.update("node-a", {
                "selected_portfolio_ids": [11],
                "profiles": {"11": {**profile("111", "911"), "source_password": "", "tester_password": ""}},
            })

        self.assertEqual(state["selected_portfolio_ids"], [11])
        self.assertIn("12", state["profiles"])

    def test_previous_shared_configuration_becomes_one_profile_per_selected_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "live_audit_settings.json"
            path.write_text(json.dumps({"node-a": {"settings": {
                "enabled": True,
                "selected_portfolio_ids": [11, 12],
                **profile("111", "911", period_days=21),
            }}}), encoding="utf-8")
            state = LiveAuditSettingsStore(path).state("node-a")

        self.assertEqual(state["selected_portfolio_ids"], [11, 12])
        self.assertEqual(state["profiles"]["11"]["period_days"], 21)
        self.assertEqual(state["profiles"]["12"]["period_days"], 21)

    def test_same_portfolio_can_have_modes_and_accounts_as_independent_uses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            saved = store.update("node-a", {
                "selected_audit_ids": ["main-balanced", "reserve-conservative"],
                "profiles": {
                    "main-balanced": {
                        **profile("111", "911"), "portfolio_id": 11, "portfolio_type": "balanced",
                        "source_password": "real-a", "tester_password": "test-a",
                    },
                    "reserve-conservative": {
                        **profile("222", "922"), "portfolio_id": 11, "portfolio_type": "conservative",
                        "source_password": "real-b", "tester_password": "test-b",
                    },
                },
            })

        self.assertEqual(saved["selected_portfolio_ids"], [11])
        self.assertEqual(saved["configured_audit_ids"], ["main-balanced", "reserve-conservative"])
        self.assertEqual(saved["profiles"]["main-balanced"]["portfolio_type"], "balanced")
        self.assertEqual(saved["profiles"]["reserve-conservative"]["source_login"], "222")

    def test_modern_use_requires_an_explicit_portfolio_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            with self.assertRaisesRegex(ValueError, "Selecciona Agresivo, Moderado o Conservador"):
                store.update("node-a", {
                    "selected_audit_ids": ["account-a"],
                    "profiles": {"account-a": {
                        **profile("111", "911"), "portfolio_id": 11, "portfolio_type": "",
                        "source_password": "real", "tester_password": "test",
                    }},
                })


class LiveAuditConfigurationScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        cls.page_text = (cls.static_dir / "live_audit.html").read_text(encoding="utf-8")
        cls.script = (cls.static_dir / "live_audit.js").read_text(encoding="utf-8")
        cls.result_page_text = (cls.static_dir / "live_audit_result.html").read_text(encoding="utf-8")
        cls.result_script = (cls.static_dir / "live_audit_result.js").read_text(encoding="utf-8")
        cls.manager_script = (cls.static_dir / "app.js").read_text(encoding="utf-8")
        cls.page = html.fromstring(cls.page_text)
        cls.result_page = html.fromstring(cls.result_page_text)

    def test_every_node_card_links_to_the_page(self) -> None:
        self.assertIn('/live_audit.html?node=${encodeURIComponent(id)}', self.manager_script)

    def test_unrequested_enable_row_and_monthly_notice_are_gone(self) -> None:
        self.assertNotIn("Habilitar cuando el servicio esté disponible", self.page_text)
        self.assertNotIn("El portafolio mensual permanece deshabilitado", self.page_text)
        self.assertFalse(self.page.xpath('//*[@name="enabled"]'))

    def test_each_marked_portfolio_renders_an_independent_configuration(self) -> None:
        self.assertIn("Cada uso del portafolio conserva modo Agresivo, Moderado o Conservador", self.page_text)
        self.assertIn("ids.map(profileMarkup)", self.script)
        self.assertIn("data-profile-id", self.script)
        self.assertIn("profiles: Object.fromEntries", self.script)
        self.assertIn("credentialState[String(auditId)]", self.script)
        self.assertIn('data-field="portfolio_type"', self.script)
        self.assertIn("Añadir otro uso", self.script)

    def test_each_profile_has_two_accounts_passwords_schedule_and_tolerances(self) -> None:
        for field in (
            "source_login", "source_server", "source_password", "tester_login", "tester_server",
            "tester_password", "period_days", "audit_interval_days", "tester_model",
            "min_tick_history_quality_pct", "price_tolerance_points", "drawdown_deviation_warning_pct",
        ):
            self.assertIn(f'data-field="{field}"', self.script)
        self.assertNotIn("terminal_path", self.page_text + self.script)
        self.assertIn("los logins pueden coincidir", self.script)
        self.assertNotIn("deben ser diferentes", self.script)

    def test_schedule_only_asks_for_audited_period_and_interval_in_days(self) -> None:
        self.assertIn("Periodo auditado (días)", self.script)
        self.assertIn("Ejecutar auditoría cada (días)", self.script)
        for obsolete in ("Sincronizar cada", "Auditoría diaria a las", "Heartbeat vencido"):
            self.assertNotIn(obsolete, self.script)

    def test_each_portfolio_has_manual_audit_progress_result_tab_and_logs(self) -> None:
        self.assertIn('data-audit-action="run"', self.script)
        self.assertIn('data-audit-action="result"', self.script)
        self.assertIn('data-audit-action="logs"', self.script)
        self.assertIn('role="progressbar"', self.script)
        for stage in ("Preparación", "Extracción real", "Strategy Tester", "Comparación", "Finalizado"):
            self.assertIn(stage, self.script)
        self.assertFalse(self.page.xpath('//dialog[@id="audit-result-dialog"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="audit-log-dialog"]'))
        self.assertIn("/live_audit_result.html?node=", self.script)
        self.assertIn("window.open(url, '_blank')", self.script)
        self.assertIn("configuration_only", self.script)

    def test_result_tab_explains_method_and_each_operation_in_tables(self) -> None:
        self.assertTrue(self.result_page.xpath('//table/tbody[@id="comparison-body"]'))
        self.assertTrue(self.result_page.xpath('//table/tbody[@id="strategy-body"]'))
        self.assertTrue(self.result_page.xpath('//table/tbody[@id="artifact-body"]'))
        self.assertTrue(self.result_page.xpath('//section[@id="extra-section"]'))
        for text in (
            "Cómo se decide cada emparejamiento", "Comparación tester ↔ real, fila por fila",
            "Qué archivo y qué lote ejecutó cada estrategia", "Lote del portafolio",
            "StartLots en set usado", "Volumen(es) del reporte",
            "Operaciones reales que ningún resultado del tester utilizó", "Diagnóstico técnico completo",
        ):
            self.assertIn(text, self.result_page_text)
        for field in (
            "operation_comparisons", "nearest_unused_real", "open_time_delta_seconds",
            "open_price_delta_points", "volume_delta_pct", "pnl_delta_pct", "strategy_summary",
            "strategy_artifacts", "real_account_report", "lot_matches_portfolio",
            "observed_trade_volumes", "report_volumes_match_start_lots",
        ):
            self.assertIn(field, self.result_script)
        self.assertIn("Resultado antiguo sin trazabilidad por operación", self.result_script)
        self.assertIn("Abrir reporte MT5", self.result_script)
        self.assertIn("Abrir HTML nativo de MT5", self.result_script)

    def test_tick_quality_is_a_required_comparison_gate_per_portfolio(self) -> None:
        self.assertIn("Calidad de datos tick a tick", self.script)
        self.assertIn("no se realiza la comparación", self.script)
        self.assertIn("MT5 no acredita este porcentaje", self.script)
        self.assertIn("min_tick_history_quality_pct: number('min_tick_history_quality_pct')", self.script)

    def test_script_loads_full_history_portfolios_and_the_manager_endpoint(self) -> None:
        self.assertIn("/portfolios?scope=full_history", self.script)
        self.assertGreaterEqual(self.script.count("/live-audit-config`"), 2)
        self.assertIn("if (!form.reportValidity()) return", self.script)

    def test_status_poll_does_not_replace_open_form_controls(self) -> None:
        refresh = self.script.split("async function refreshAuditStates()", 1)[1].split(
            "async function loadSettings()", 1
        )[0]
        self.assertIn("renderAuditOperations(ids)", refresh)
        self.assertNotIn("renderProfiles()", refresh)
        self.assertNotIn("captureDrafts()", refresh)


if __name__ == "__main__":
    unittest.main()
