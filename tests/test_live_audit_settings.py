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

    def test_real_lot_per_strategy_defaults_empty_and_is_validated(self) -> None:
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["real_strategy_lots"], {})
        normalized = normalize_live_audit_settings({
            "real_strategy_lots": {"AXI/STANDARD:34173": "0.6", "nas-one": 0.01},
        })
        self.assertEqual(normalized["real_strategy_lots"], {
            "AXI/STANDARD:34173": 0.6, "nas-one": 0.01,
        })
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"real_strategy_lots": []})
        with self.assertRaisesRegex(ValueError, "fuera|entre"):
            normalize_live_audit_settings({"real_strategy_lots": {"bad": 0}})

    def test_real_lots_are_saved_independently_for_each_portfolio_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            saved = store.update("node-a", {
                "selected_audit_ids": ["real-a"],
                "profiles": {"real-a": {
                    **profile("111", "911"), "portfolio_id": 11, "portfolio_type": "balanced",
                    "real_strategy_lots": {"eth-grid": 0.6},
                    "source_password": "real", "tester_password": "test",
                }},
            })

        self.assertEqual(saved["profiles"]["real-a"]["real_strategy_lots"], {"eth-grid": 0.6})

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

    def test_catalog_lists_each_login_and_server_once_whatever_the_role(self) -> None:
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

        # 333 en Broker-Demo es la cuenta de pruebas y también la cuenta final:
        # la misma cuenta, una sola entrada, con la procedencia de la primera.
        self.assertEqual(
            [account["id"] for account in catalog],
            ["profile:portfolio-20:source", "profile:portfolio-20:tester"],
        )
        self.assertEqual([account["login"] for account in catalog], ["111", "333"])
        self.assertEqual([account["uses"] for account in catalog], [1, 2])
        self.assertEqual(catalog[1]["origin"], "Portafolio #20 · cuenta de pruebas")
        self.assertNotIn("password", json.dumps(catalog))

    def test_catalog_merges_the_same_account_across_uses_but_not_across_servers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            store.update("node-a", {
                "selected_audit_ids": ["use-9", "use-16"],
                "profiles": {
                    "use-9": {
                        **profile("111", "911"),
                        "portfolio_id": 9,
                        "portfolio_type": "balanced",
                        "source_password": "real-secret",
                        "tester_password": "tester-secret",
                    },
                    "use-16": {
                        **profile("111", "911", source_server="broker-live"),
                        "portfolio_id": 16,
                        "portfolio_type": "conservative",
                        "source_password": "real-secret",
                        "tester_password": "tester-secret",
                    },
                },
            })
            catalog = store.state("node-a")["saved_accounts"]
            same_server = store.update("node-a", {
                "selected_audit_ids": ["use-40"],
                "profiles": {
                    "use-40": {
                        **profile("111", "912", source_server="Broker-Live-2"),
                        "portfolio_id": 40,
                        "portfolio_type": "aggressive",
                        "source_password": "other-secret",
                        "tester_password": "other-tester-secret",
                    },
                },
            })["saved_accounts"]

        # El mismo login en el mismo servidor, aunque lo escribiera con otra caja.
        self.assertEqual(
            [(account["login"], account["server"], account["uses"]) for account in catalog],
            [("111", "Broker-Live", 2), ("911", "Broker-Demo", 2)],
        )
        # Otro servidor es otra cuenta aunque el login coincida.
        self.assertEqual(
            [(account["login"], account["server"]) for account in same_server],
            [("111", "Broker-Live"), ("911", "Broker-Demo"), ("111", "Broker-Live-2"), ("912", "Broker-Demo")],
        )

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




if __name__ == "__main__":
    unittest.main()
