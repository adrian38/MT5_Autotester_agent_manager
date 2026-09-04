from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lxml import html

from mt5_manager.manager import normalize_live_audit_scheduler_settings
from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)


class LiveAuditSchedulerSettingsTests(unittest.TestCase):
    def test_the_only_public_cadence_is_interval_days(self) -> None:
        self.assertEqual(
            normalize_live_audit_scheduler_settings({"enabled": True, "interval_days": 7}),
            {"enabled": True, "interval_days": 7},
        )
        with self.assertRaisesRegex(ValueError, "interval_days"):
            normalize_live_audit_scheduler_settings({"interval_days": 0})

    def test_old_technical_timers_are_migrated_without_remaining_public(self) -> None:
        self.assertEqual(
            normalize_live_audit_scheduler_settings({
                "enabled": False, "check_interval_minutes": 5, "startup_delay_seconds": 30,
            }),
            {"enabled": False, "interval_days": 30},
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
    def test_terminal_restore_account_is_independent_encrypted_and_has_safe_public_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = LiveAuditSettingsStore(root / "live_audit_settings.json")
            initial = store.state("node-a")["restore_account"]
            self.assertEqual(initial["login"], "11637157")
            self.assertEqual(initial["server"], "CapitalPointTrading-MT5-4")
            self.assertFalse(initial["configured"])

            saved = store.update_restore_account("node-a", {
                "login": "333", "server": "Broker-Live", "password": "restore-secret",
            })
            reloaded_store = LiveAuditSettingsStore(root / "live_audit_settings.json")
            reloaded = reloaded_store.state("node-a")["restore_account"]
            credentials = reloaded_store.restore_credentials("node-a")
            encrypted = (root / "live_audit_credentials.json").read_text(encoding="utf-8")

        self.assertTrue(saved["restore_account"]["configured"])
        self.assertEqual(reloaded, {
            "login": "333", "server": "Broker-Live",
            "password_saved": True, "configured": True,
        })
        self.assertEqual(credentials, {
            "restore_login": "333", "restore_server": "Broker-Live",
            "restore_password": "restore-secret",
        })
        self.assertNotIn("restore-secret", encrypted)
        self.assertNotIn("restore_password", str(saved))

    def test_empty_restore_password_preserves_the_saved_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update_restore_account("node-a", {
                "login": "333", "server": "Broker-A", "password": "secret",
            })
            store.update_restore_account("node-a", {
                "login": "444", "server": "Broker-B", "password": "",
            })
            credentials = store.restore_credentials("node-a")
        self.assertEqual(credentials["restore_login"], "444")
        self.assertEqual(credentials["restore_server"], "Broker-B")
        self.assertEqual(credentials["restore_password"], "secret")

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

    def test_saved_accounts_are_public_without_secrets_and_can_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = LiveAuditSettingsStore(root / "live_audit_settings.json")
            store.update("node-a", {
                "selected_audit_ids": ["original"],
                "profiles": {"original": {
                    **profile("111", "911"),
                    "portfolio_id": 11,
                    "portfolio_type": "balanced",
                    "source_password": "real-secret",
                    "tester_password": "tester-secret",
                }},
            })
            catalog = store.state("node-a")["saved_accounts"]
            source_id = next(account["id"] for account in catalog if account["login"] == "111")
            tester_id = next(account["id"] for account in catalog if account["login"] == "911")

            saved = store.update("node-a", {
                "selected_audit_ids": ["another-portfolio"],
                "profiles": {"another-portfolio": {
                    **DEFAULT_LIVE_AUDIT_PROFILE,
                    "portfolio_id": 12,
                    "portfolio_type": "conservative",
                    "source_saved_account_id": source_id,
                    "tester_saved_account_id": tester_id,
                }},
            })
            reused = store.credentials("node-a", "another-portfolio")

        self.assertEqual(reused, {
            "source_password": "real-secret",
            "tester_password": "tester-secret",
        })
        self.assertEqual(saved["profiles"]["another-portfolio"]["source_login"], "111")
        self.assertEqual(saved["profiles"]["another-portfolio"]["source_server"], "Broker-Live")
        self.assertEqual(saved["profiles"]["another-portfolio"]["tester_login"], "911")
        self.assertEqual(saved["profiles"]["another-portfolio"]["tester_server"], "Broker-Demo")
        public = json.dumps(saved)
        self.assertNotIn("real-secret", public)
        self.assertNotIn("tester-secret", public)
        self.assertNotIn("password", json.dumps(saved["saved_accounts"]))

    def test_restore_account_is_also_reusable_but_references_are_node_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update_restore_account("node-a", {
                "login": "333", "server": "Broker-Final", "password": "final-secret",
            })
            account_id = store.state("node-a")["saved_accounts"][0]["id"]
            saved = store.update("node-a", {
                "selected_audit_ids": ["portfolio-20"],
                "profiles": {"portfolio-20": {
                    **profile("", "900"),
                    "portfolio_id": 20,
                    "portfolio_type": "aggressive",
                    "source_saved_account_id": account_id,
                    "tester_password": "tester-secret",
                }},
            })
            credentials = store.credentials("node-a", "portfolio-20")
            with self.assertRaisesRegex(ValueError, "ya no está disponible"):
                store.update("node-b", {
                    "selected_audit_ids": ["portfolio-21"],
                    "profiles": {"portfolio-21": {
                        **profile("", "901"),
                        "portfolio_id": 21,
                        "portfolio_type": "balanced",
                        "source_saved_account_id": account_id,
                        "tester_password": "other-tester-secret",
                    }},
                })

        self.assertEqual(saved["profiles"]["portfolio-20"]["source_login"], "333")
        self.assertEqual(saved["profiles"]["portfolio-20"]["source_server"], "Broker-Final")
        self.assertEqual(credentials["source_password"], "final-secret")

    def test_catalog_keeps_real_tester_and_final_entries_even_when_credentials_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update("node-a", {
                "selected_audit_ids": ["portfolio-20"],
                "profiles": {"portfolio-20": {
                    **profile("111", "333"),
                    "portfolio_id": 20,
                    "portfolio_type": "balanced",
                    "source_password": "real-secret",
                    "tester_password": "shared-secret",
                }},
            })
            store.update_restore_account("node-a", {
                "login": "333", "server": "Broker-Demo", "password": "shared-secret",
            })
            catalog = store.state("node-a")["saved_accounts"]

        self.assertEqual(len(catalog), 3)
        self.assertEqual(
            [account["id"] for account in catalog],
            ["profile:portfolio-20:source", "profile:portfolio-20:tester", "restore:terminal"],
        )
        self.assertEqual([account["login"] for account in catalog], ["111", "333", "333"])
        self.assertNotIn("password", json.dumps(catalog))

    def test_profile_numeric_limits_and_fixed_policy_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "period_days"):
            normalize_live_audit_settings({"period_days": 0})
        with self.assertRaisesRegex(ValueError, "audit_interval_days"):
            normalize_live_audit_settings({"audit_interval_days": 0})
        with self.assertRaisesRegex(ValueError, "min_tick_history_quality_pct"):
            normalize_live_audit_settings({"min_tick_history_quality_pct": 100.1})
        with self.assertRaisesRegex(ValueError, "pause_resume"):
            normalize_live_audit_settings({"active_job_policy": "interrupt"})

    def test_fixed_calendar_period_requires_an_ordered_inclusive_range(self) -> None:
        normalized = normalize_live_audit_settings({
            "period_mode": "fixed_dates",
            "period_start_date": "2026-08-23",
            "period_end_date": "2026-08-30",
        })
        self.assertEqual(normalized["period_start_date"], "2026-08-23")
        self.assertEqual(normalized["period_end_date"], "2026-08-30")
        with self.assertRaisesRegex(ValueError, "fecha desde y fecha hasta"):
            normalize_live_audit_settings({"period_mode": "fixed_dates"})
        with self.assertRaisesRegex(ValueError, "posterior"):
            normalize_live_audit_settings({
                "period_mode": "fixed_dates",
                "period_start_date": "2026-08-31",
                "period_end_date": "2026-08-30",
            })

    def test_legacy_60_second_tolerance_is_migrated_but_new_explicit_values_are_kept(self) -> None:
        legacy = normalize_live_audit_settings({
            "trade_time_tolerance_seconds": 60, "price_tolerance_points": 10,
        })
        current = normalize_live_audit_settings({
            "period_mode": "rolling_days", "trade_time_tolerance_seconds": 60,
            "price_tolerance_points": 10,
        })

        self.assertEqual(legacy["trade_time_tolerance_seconds"], 120)
        self.assertEqual(legacy["price_tolerance_points"], 15)
        self.assertEqual(current["trade_time_tolerance_seconds"], 60)
        self.assertEqual(current["price_tolerance_points"], 10)

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

    def test_each_profile_has_two_accounts_passwords_period_and_tolerances(self) -> None:
        for field in (
            "source_login", "source_server", "source_password", "tester_login", "tester_server",
            "tester_password", "use_calendar_period", "period_days", "period_start_date", "period_end_date", "tester_model",
            "min_tick_history_quality_pct", "price_tolerance_points", "drawdown_deviation_warning_pct",
        ):
            self.assertIn(f'data-field="{field}"', self.script)
        self.assertIn("Tolerancia base de precio (puntos)", self.script)
        self.assertIn("Se amplía automáticamente según la escala y la familia del instrumento.", self.script)
        self.assertIn("Aviso por empeoramiento de PnL (%)", self.script)
        self.assertIn("Una mejora del resultado real frente al tester es admisible.", self.script)
        self.assertNotIn("terminal_path", self.page_text + self.script)
        self.assertIn("los logins pueden coincidir", self.script)
        self.assertNotIn("deben ser diferentes", self.script)

    def test_period_can_be_selected_with_native_calendar_inputs(self) -> None:
        self.assertIn('type="date"', self.script)
        self.assertIn("Usar calendario para elegir el periodo", self.script)
        self.assertIn('data-period-control="fixed_dates"', self.script)
        self.assertIn("marketDateTime", self.result_script)
        self.assertIn("hora MT5", self.result_script)

    def test_audit_now_saves_the_visible_calendar_period_before_starting(self) -> None:
        run = self.script.split("async function runAuditNow", 1)[1].split(
            "async function refreshAuditStates", 1
        )[0]
        self.assertIn("if (!form.reportValidity()) return", run)
        self.assertIn("await saveAuditSettings({apply: false})", run)
        self.assertLess(
            run.index("await saveAuditSettings({apply: false})"),
            run.index("/live-audits/${encodeURIComponent(id)}/run"),
        )
        self.assertGreater(run.index("applyState(savedSettings)"), run.index("/live-audits/${encodeURIComponent(id)}/run"))
        save = self.script.split("async function saveAuditSettings", 1)[1].split(
            "async function runAuditNow", 1
        )[0]
        self.assertIn("/live-audit-config", save)
        self.assertIn("JSON.stringify(payload())", save)

    def test_saved_accounts_can_be_selected_again_for_any_portfolio_use(self) -> None:
        self.assertIn("saved_accounts", self.script)
        self.assertIn('data-saved-account-role="source"', self.script)
        self.assertIn('data-saved-account-role="tester"', self.script)
        self.assertIn("source_saved_account_id", self.script)
        self.assertIn("tester_saved_account_id", self.script)
        self.assertIn("Cuenta para este uso", self.script)
        self.assertIn("Nueva cuenta · escribir login, servidor y contraseña", self.script)
        self.assertIn("la contraseña nunca vuelve al navegador", self.script)

    def test_profile_only_asks_for_the_audited_period(self) -> None:
        self.assertIn("Días hacia atrás · incluye hoy", self.script)
        self.assertIn("Usar calendario para elegir el periodo", self.script)
        self.assertNotIn('data-field="audit_interval_days"', self.script)
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
            "observed_trade_volumes", "report_volumes_match_start_lots", "tester_execution",
        ):
            self.assertIn(field, self.result_script)
        self.assertIn("sets ·", self.result_script)
        self.assertIn("terminales ·", self.result_script)
        self.assertIn("Resultado antiguo sin trazabilidad por operación", self.result_script)
        self.assertIn("Abrir reporte MT5", self.result_script)
        self.assertIn("Abrir HTML nativo de MT5", self.result_script)
        self.assertIn("· periodo ${auditedPeriod} ·", self.result_script)
        self.assertIn("Precio adaptativo por instrumento", self.result_script)
        self.assertIn("Límite absoluto", self.result_script)
        self.assertIn("adaptive_indices: 'índices'", self.result_script)
        self.assertIn("function pnlDelta(measurements, limit)", self.result_script)
        self.assertIn("A favor +", self.result_script)
        self.assertIn("PnL: alerta si el real empeora más de", self.result_script)

    def test_result_can_download_the_complete_comparison_as_excel_compatible_csv(self) -> None:
        buttons = self.result_page.xpath('//button[@id="download-comparison"]')
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].text, "Descargar tabla CSV")
        self.assertIn("disabled", buttons[0].attrib)
        for token in (
            "function downloadComparisons()", "comparisonRows.map(comparisonCsvRow)",
            "Validación / observaciones", "text/csv;charset=utf-8", "\\uFEFF",
            "link.download = `auditoria_", "URL.revokeObjectURL(url)",
            "#download-comparison').disabled = !comparisonRows.length",
        ):
            self.assertIn(token, self.result_script)
        self.assertIn("if (/^[=+\\-@]/.test(text))", self.result_script)

    def test_result_leads_with_mutually_exclusive_outcomes_and_hides_technical_noise(self) -> None:
        for text in (
            "1 · VEREDICTO", "Pertenencia al modo", "Cumplen todo",
            "Parejas con desviación", "Sin pareja", "Dónde está el problema",
            "Ver metodología, cuentas, origen MT5, lotes y reportes",
        ):
            self.assertIn(text, self.result_page_text + self.result_script)
        self.assertIn("let activeFilter = 'all'", self.result_script)
        self.assertIn("activeFilter === 'issues'", self.result_script)
        self.assertIn("Este no es el resultado de la última ejecución", self.result_script)
        self.assertIn('id="stale-result-warning"', self.result_page_text)
        self.assertIn("33 de 33", self.result_script.replace("${portfolioClosures}", "33").replace("${real}", "33"))

    def test_the_result_says_in_which_account_the_terminal_was_left(self) -> None:
        # El auditor cambia la cuenta del terminal para leer la real; lo que el
        # usuario necesita comprobar es que la dejó en la de pruebas.
        self.assertIn("Terminal devuelto a la cuenta final configurada", self.result_script)
        self.assertIn("function terminalRestore(result)", self.result_script)
        self.assertIn("terminal_restore", self.result_script)
        self.assertIn("password_persisted", self.result_script)
        self.assertIn("reopened_without_password", self.result_script)
        self.assertIn("contraseña persistida · reapertura verificada", self.result_script)
        self.assertIn("terminal_validations", self.result_script)
        self.assertIn("Cuenta tester confirmada por terminal", self.result_script)
        # Sin fila no se afirma nada, y una restauración fallida se marca en rojo.
        self.assertIn("NO REGISTRADO", self.result_script)
        self.assertIn("SIN RESTAURAR", self.result_script)

    def test_restore_account_and_scheduler_have_explicit_editable_dialogs(self) -> None:
        self.assertTrue(self.page.xpath('//button[@id="open-restore-account"]'))
        self.assertTrue(self.page.xpath('//button[@id="open-scheduler"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="restore-account-dialog"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="scheduler-dialog"]'))
        for token in (
            "/live-audit-restore-account", "/api/live-audit-scheduler-config",
            "restoreAccount.configured", "todos los terminales usados",
            "interval_days", "scheduler-interval-days", "environment_override",
        ):
            self.assertIn(token, self.page_text + self.script)
        for obsolete in ("scheduler-check-minutes", "scheduler-startup-delay", "check_interval_minutes", "startup_delay_seconds"):
            self.assertNotIn(obsolete, self.page_text + self.script)

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
