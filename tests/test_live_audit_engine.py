from __future__ import annotations

import sys
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_engine import (
    LiveAuditController, _audit_period, _read_set_text, _redact_log_files, _redact_runner_output,
    normalize_request,
)
from mt5_manager.mt5_native_history_report import NativeHistoryReportError, validate_native_history_report
from tests.live_audit_engine_base import FakeOwner, LiveAuditEngineTestCase, request


class LiveAuditEngineTests(LiveAuditEngineTestCase):
    def test_only_adverse_pnl_differences_trigger_the_tolerance(self) -> None:
        now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        base = {
            "strategy": "pnl", "symbol": "EURUSD", "side": "buy",
            "open_time": now, "close_time": now, "open_price": 1.1, "volume": .1,
        }
        cases = (
            (28.69, 37.64, "favorable", "matched"),
            (-.45, 1.79, "favorable", "matched"),
            (-4.73, -1.25, "favorable", "matched"),
            (1.80, -1.12, "unfavorable", "deviation"),
            (-3.15, -3.35, "unfavorable", "matched"),
            (10.0, 9.0, "unfavorable", "matched"),
        )
        for tester_profit, real_profit, direction, status in cases:
            with self.subTest(tester=tester_profit, real=real_profit):
                result = LiveAuditController._compare(
                    [{**base, "profit": real_profit}],
                    [{**base, "profit": tester_profit}],
                    {"EURUSD": .00001}, request(), {"pnl": 1},
                )
                row = result["comparison_detail"]["operation_comparisons"][0]
                self.assertEqual(row["status"], status)
                self.assertEqual(row["measurements"]["pnl_direction"], direction)
                self.assertEqual("pnl" in row["reasons"], status == "deviation")
                if direction == "favorable":
                    self.assertEqual(row["measurements"]["pnl_adverse_delta"], 0)

        adverse = LiveAuditController._compare(
            [{**base, "profit": -1.12}], [{**base, "profit": 1.80}],
            {"EURUSD": .00001}, request(), {"pnl": 1},
        )["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(adverse["reasons"], ["pnl"])
        self.assertEqual(adverse["measurements"]["pnl_adverse_delta"], 2.92)
        self.assertEqual(adverse["measurements"]["pnl_adverse_delta_pct"], 162.222)

        favorable_but_late = LiveAuditController._compare(
            [{**base, "close_time": now + timedelta(seconds=384), "profit": 37.64}],
            [{**base, "profit": 28.69}],
            {"EURUSD": .00001}, request(), {"pnl": 1},
        )["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(favorable_but_late["status"], "deviation")
        self.assertEqual(favorable_but_late["reasons"], ["close_time"])
        self.assertEqual(favorable_but_late["measurements"]["pnl_direction"], "favorable")

    def test_xauusd_eleven_point_price_delta_is_within_default_tolerance(self) -> None:
        now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        real = [{
            "strategy": "real", "symbol": "XAUUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 4566.63, "volume": .03, "profit": 1.0,
        }]
        tester = [{
            "strategy": "xau", "symbol": "XAUUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 4566.74, "volume": .03, "profit": 1.0,
        }]

        result = LiveAuditController._compare(
            real, tester, {"XAUUSD": .01}, request(), {"xau": 1},
        )

        row = result["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(row["measurements"]["open_price_delta_points"], 11.0)
        self.assertEqual(row["limits"]["open_price_points"], 205)
        self.assertEqual(row["limits"]["open_price_absolute"], 2.05)
        self.assertEqual(row["limits"]["open_price_configured_points"], 15)
        self.assertEqual(row["limits"]["open_price_rule"], "adaptive_gold")
        self.assertEqual(row["status"], "matched")
        self.assertEqual(result["within_tolerance_trades"], 1)

    def test_price_tolerance_is_adapted_to_each_validated_instrument_family(self) -> None:
        now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        cases = (
            ("US30", 53462.0, 53472.5, .01, 10.5, "adaptive_indices"),
            ("DE40", 20000.0, 20010.5, .1, 10.5, "adaptive_indices"),
            ("USTECH", 25000.0, 25010.5, .01, 10.5, "adaptive_indices"),
            ("NAS100.fs", 29570.0, 29575.0, .01, 5.0, "adaptive_nasdaq"),
            ("BTCUSD", 77010.0, 77020.0, .01, 10.0, "adaptive_crypto_btc"),
            ("USDJPY", 159.650, 159.700, .001, .05, "adaptive_jpy_fx"),
            ("XAUUSD", 4807.16, 4809.21, .01, 2.05, "adaptive_gold"),
            ("XAGUSD", 67.454, 67.474, .001, .02, "adaptive_silver"),
            ("EURUSD", 1.1621, 1.1626, .00001, .0005, "adaptive_fx"),
        )
        for symbol, real_price, tester_price, point, absolute_limit, rule in cases:
            with self.subTest(symbol=symbol):
                real = [{
                    "strategy": "real", "symbol": symbol, "side": "buy", "open_time": now,
                    "close_time": now, "open_price": real_price, "volume": .1, "profit": 1.0,
                }]
                tester = [{
                    "strategy": "tester", "symbol": symbol, "side": "buy", "open_time": now,
                    "close_time": now, "open_price": tester_price, "volume": .1, "profit": 1.0,
                }]

                result = LiveAuditController._compare(
                    real, tester, {symbol: point}, request(), {"tester": 1},
                )

                row = result["comparison_detail"]["operation_comparisons"][0]
                self.assertEqual(row["status"], "matched")
                self.assertAlmostEqual(row["limits"]["open_price_absolute"], absolute_limit)
                self.assertEqual(row["limits"]["open_price_rule"], rule)

        real = [{
            "strategy": "real", "symbol": "US30", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 53462.0, "volume": .1, "profit": 1.0,
        }]
        tester = [{
            "strategy": "tester", "symbol": "US30", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 53472.51, "volume": .1, "profit": 1.0,
        }]
        outside = LiveAuditController._compare(
            real, tester, {"US30": .01}, request(), {"tester": 1},
        )
        self.assertEqual(
            outside["comparison_detail"]["operation_comparisons"][0]["reasons"], ["open_price"],
        )

    def test_82_second_open_difference_is_aligned_with_the_new_default_tolerance(self) -> None:
        now = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
        real = [{
            "strategy": "real", "symbol": "DE40", "side": "buy",
            "open_time": now + timedelta(seconds=82), "close_time": now + timedelta(hours=1),
            "open_price": 100.0, "close_price": 101.0, "volume": .1, "profit": 5.0,
        }]
        expected = [{
            "strategy": "orb", "symbol": "DE40", "side": "buy", "open_time": now,
            "close_time": now + timedelta(hours=1), "open_price": 100.0,
            "close_price": 101.0, "volume": .1, "profit": 5.0,
        }]

        result = LiveAuditController._compare(real, expected, {"DE40": 1.0}, request(), {"orb": 1})

        self.assertEqual(result["matched_trades"], 1)
        self.assertEqual(result["missing_real_trades"], 0)
        self.assertEqual(result["within_tolerance_trades"], 1)

    def test_active_pipeline_is_paused_and_only_that_pipeline_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running")
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "completed")
            self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))

    def test_real_account_membership_uses_symbol_and_lot_not_magic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            now = datetime.now(timezone.utc)
            matching = {
                "strategy": "magic-can-differ", "symbol": "EURUSD", "side": "buy",
                "open_time": now, "close_time": now, "open_price": 1.1,
                "close_price": 1.1, "volume": .01, "profit": 1.0,
            }
            wrong_lot = {**matching, "strategy": "one", "volume": .02}
            controller._extract_real = lambda *_args: (
                [matching, wrong_lot], {"EURUSD": .00001},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller.start(request())
            state = self._wait(controller)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["portfolio_closures"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["foreign_closures_ignored"], 1)

    def test_real_account_filter_uses_effective_broker_lot_not_invalid_saved_lot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            owner.portfolio_detail = lambda *_args: {"portfolio": {"id": 9, "members": [{
                "variant_key": "balanced", "candidate_id": "de40", "symbol": "DE40",
                "lot": .03, "units": 3,
            }]}}
            controller._broker_volume_rules = lambda: {"de40": (.1, .1)}
            now = datetime.now(timezone.utc)
            base = {
                "strategy": "real", "symbol": "DE40", "side": "buy", "open_time": now,
                "close_time": now, "open_price": 100.0, "close_price": 100.0, "profit": 1.0,
            }
            controller._extract_real = lambda *_args: (
                [{**base, "volume": .1}, {**base, "volume": .3}], {"DE40": 1.0},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller.start(request())
            state = self._wait(controller)

        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["portfolio_closures"], 1)

    def test_real_account_filter_uses_the_configured_lot_for_each_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            owner.portfolio_detail = lambda *_args: {"portfolio": {"id": 9, "members": [{
                "variant_key": "balanced", "candidate_id": "eth-grid", "symbol": "ETHUSD", "lot": .7,
            }]}}
            now = datetime.now(timezone.utc)
            base = {
                "strategy": "real", "symbol": "ETHUSD", "side": "buy", "open_time": now,
                "close_time": now, "open_price": 100.0, "close_price": 100.0, "profit": 1.0,
            }
            controller._extract_real = lambda *_args: (
                [{**base, "volume": .6}, {**base, "volume": .7}], {"ETHUSD": .01},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller._run_tester = lambda *_args: (
                [{**base, "strategy": "eth-grid", "volume": .6}], [99.0], {"eth-grid": 1}, [], {},
            )
            controller.start({**request(), "real_strategy_lots": {"eth-grid": .6}})
            state = self._wait(controller)

        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["matched_trades"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["foreign_closures_ignored"], 1)

    def test_real_account_filter_uses_the_symbol_reported_by_the_tester(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            owner.portfolio_detail = lambda *_args: {"portfolio": {"id": 9, "members": [{
                "variant_key": "balanced", "candidate_id": "nas-one", "symbol": "NAS100", "lot": .01,
            }]}}
            now = datetime.now(timezone.utc)
            trade = {
                "strategy": "nas-one", "symbol": "NAS100.fs", "side": "buy", "open_time": now,
                "close_time": now, "open_price": 100.0, "close_price": 100.0,
                "volume": .01, "profit": 1.0,
            }
            controller._extract_real = lambda *_args: (
                [dict(trade)], {"NAS100.fs": .01},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller._run_tester = lambda *_args: ([dict(trade)], [99.0], {"nas-one": 1}, [], {})
            controller.start(request())
            state = self._wait(controller)

        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["matched_trades"], 1)

    def test_pipeline_already_paused_by_user_stays_paused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "paused")
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "completed")
            self.assertEqual((owner.pause_calls, owner.resume_calls), (0, 0))
            self.assertEqual(owner.state["status"], "paused")

    def test_the_terminal_is_left_on_the_configured_restore_account_and_the_result_proves_it(self) -> None:
        # El auditor loguea la cuenta real con initialize(login=...) y MT5 recuerda
        # la última cuenta del terminal: sin restaurar, el siguiente backtest del
        # pipeline probaría cada estrategia contra la cuenta real.
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running")
            evidence = self._run_restore_scenario(controller)

        state = evidence["state"]
        self.assertEqual(state["status"], "completed")
        self._assert_restore_login(evidence)
        self._assert_restore_result(state)
        self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))
        self.assertTrue(any("MT5_IC_1 → 333 (CapitalPoint-Live)" in line for line in state["log_lines"]))

    def _run_restore_scenario(self, controller) -> dict:
        initialize_calls: list[dict[str, object]] = []
        launches: list[tuple[str, str | None]] = []
        closed_gracefully: list[set[int]] = []

        class FakeMt5:
            @staticmethod
            def initialize(**kwargs) -> bool:
                initialize_calls.append(dict(kwargs))
                return True

            account_info = staticmethod(
                lambda: SimpleNamespace(login=333, server="CapitalPoint-Live", currency="EUR")
            )
            terminal_info = staticmethod(lambda: SimpleNamespace(connected=True))
            shutdown = staticmethod(lambda: None)

        self._remember_on_extraction(controller)
        controller._terminal_pids_for_path = lambda _path: set()
        controller._launch_terminal = lambda path, config_path=None: (
            launches.append((
                path, config_path.read_text(encoding="utf-8") if config_path else None,
            )) or {101}
        )
        controller._close_terminal_pids_gracefully = closed_gracefully.append
        with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": FakeMt5}):
            controller.start(request())
            state = self._wait(controller)
        return {
            "state": state, "initialize_calls": initialize_calls,
            "launches": launches, "closed_gracefully": closed_gracefully,
        }

    def _assert_restore_login(self, evidence: dict) -> None:
        initialize_calls = evidence["initialize_calls"]
        self.assertEqual(len(initialize_calls), 2)
        self.assertEqual(set(initialize_calls[0]), {"path", "timeout"})
        self.assertEqual(set(initialize_calls[1]), {"path", "timeout", "login", "server"})
        self.assertEqual(initialize_calls[1]["path"], "C:\\IC\\terminal64.exe")
        self.assertEqual(initialize_calls[1]["login"], 333)
        self.assertEqual(initialize_calls[1]["server"], "CapitalPoint-Live")
        self.assertNotIn("password", initialize_calls[1])
        # Solo el arranque con el INI es manual. La reapertura normal la hace
        # initialize(path=...) para no competir con una segunda instancia MT5.
        launches = evidence["launches"]
        self.assertEqual(len(launches), 1)
        self.assertIn("KeepPrivate = 1", launches[0][1] or "")
        self.assertIn("Login = 333", launches[0][1] or "")
        self.assertIn("Password = restore-secret", launches[0][1] or "")
        self.assertEqual(evidence["closed_gracefully"], [set(), set(), set()])

    def _assert_restore_result(self, state: dict) -> None:
        restore = state["terminal_restore"]
        self.assertEqual(len(restore), 1)
        self.assertEqual(restore[0]["terminal"], "MT5_IC_1")
        self.assertEqual((restore[0]["login"], restore[0]["server"]), ("333", "CapitalPoint-Live"))
        self.assertTrue(restore[0]["restored"])
        self.assertTrue(restore[0]["password_persisted"])
        self.assertTrue(restore[0]["reopened_without_password"])
        self.assertEqual(state["last_result"]["terminal_restore"], restore)
        self.assertNotIn("tester-secret", str(state))
        self.assertNotIn("restore-secret", str(state))

    def test_a_terminal_left_on_another_account_is_reported_without_hiding_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            attempts = 0

            class RefusingMt5:
                @staticmethod
                def initialize(**_kwargs) -> bool:
                    nonlocal attempts
                    attempts += 1
                    return attempts == 1

                @staticmethod
                def account_info() -> SimpleNamespace:
                    return SimpleNamespace(login=333, server="CapitalPoint-Live")

                @staticmethod
                def terminal_info() -> SimpleNamespace:
                    return SimpleNamespace(connected=True)

                @staticmethod
                def last_error() -> tuple[int, str]:
                    return -6, "Authorization failed"

                @staticmethod
                def shutdown() -> None:
                    pass

            self._remember_on_extraction(controller)
            controller._terminal_pids_for_path = lambda _path: set()
            controller._launch_terminal = lambda _path, _config_path=None: {101}
            controller._close_terminal_pids_gracefully = lambda _pids: None
            with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": RefusingMt5}):
                controller.start(request())
                state = self._wait(controller)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(attempts, 2)
        self.assertFalse(state["terminal_restore"][0]["restored"])
        self.assertFalse(state["terminal_restore"][0]["password_persisted"])
        self.assertFalse(state["terminal_restore"][0]["reopened_without_password"])
        self.assertIn("Authorization failed", state["terminal_restore"][0]["error"])
        self.assertIn("no quedó en la cuenta configurada 333", state["progress_text"])

    def test_the_same_terminal_is_only_restored_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = LiveAuditController(FakeOwner("idle"), Path(temp))
            for section in ("Terminal.2", "Terminal.2", "Terminal.3"):
                controller._remember_real_account_terminal(
                    "9", section,
                    {"name": section, "mt5_path": rf"C:\IC\{section}\terminal64.exe"},
                )
            controller._remember_real_account_terminal(
                "9", "Terminal.9", {"name": "sin ruta", "mt5_path": ""},
            )
            touched = controller.real_account_terminals["9"]

        self.assertEqual([row["section"] for row in touched], ["Terminal.2", "Terminal.3"])

    def test_tester_login_is_confirmed_independently_in_every_selected_terminal(self) -> None:
        controller = LiveAuditController(FakeOwner("idle"), Path(tempfile.gettempdir()))
        initialized: list[str] = []
        closed: list[set[int]] = []

        class FakeMt5:
            @staticmethod
            def initialize(**kwargs) -> bool:
                initialized.append(str(kwargs["path"]))
                return True

            @staticmethod
            def account_info() -> SimpleNamespace:
                return SimpleNamespace(login=222, server="IC-Demo")

            @staticmethod
            def terminal_info() -> SimpleNamespace:
                return SimpleNamespace(connected=True)

            @staticmethod
            def shutdown() -> None:
                pass

        controller._terminal_pids = lambda: set()
        controller._close_terminal_pids_gracefully = closed.append
        profiles = [
            ("Terminal.2", {"name": "MT5_IC_1", "mt5_path": r"C:\IC1\terminal64.exe"}),
            ("Terminal.3", {"name": "MT5_IC_2", "mt5_path": r"C:\IC2\terminal64.exe"}),
        ]
        with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": FakeMt5}):
            rows = controller._verify_tester_terminals(request(), profiles)

        self.assertEqual(initialized, [r"C:\IC1\terminal64.exe", r"C:\IC2\terminal64.exe"])
        self.assertEqual(closed, [set(), set()])
        self.assertTrue(all(row["verified"] for row in rows))
        self.assertEqual({row["login"] for row in rows}, {"222"})
        self.assertEqual({row["server"] for row in rows}, {"IC-Demo"})

    def test_main_journal_capture_keeps_only_new_lines_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "terminal-data"
            journal = data_dir / "logs" / "20260829.log"
            journal.parent.mkdir(parents=True)
            journal.write_bytes(b"\xff\xfe" + "old line\r\n".encode("utf-16-le"))
            profiles = [("Terminal.2", {
                "name": "MT5_IC_1", "data_dir": str(data_dir),
                "mt5_path": r"C:\IC1\terminal64.exe",
            })]
            snapshot = LiveAuditController._main_journal_snapshot(profiles)
            with journal.open("ab") as handle:
                handle.write(
                    "222: authorized on IC-Demo; tester-secret\r\n".encode("utf-16-le")
                )
            validations = [{
                "section": "Terminal.2", "terminal": "MT5_IC_1", "login": "222",
                "server": "IC-Demo", "connected": True, "verified": True, "error": None,
            }]
            controller = LiveAuditController(FakeOwner("idle"), root / "runtime")
            output_dir = root / "audit-logs"
            controller._capture_main_journals(
                profiles, snapshot, output_dir, validations, request()
            )
            captured = (output_dir / "main_journal_MT5_IC_1.txt").read_text(encoding="utf-8")

        self.assertNotIn("old line", captured)
        self.assertIn("222: authorized on IC-Demo", captured)
        self.assertNotIn("tester-secret", captured)
        self.assertIn("[REDACTED]", captured)
        self.assertTrue(validations[0]["journal_captured"])
        self.assertTrue(validations[0]["journal_login_seen"])
        self.assertTrue(validations[0]["journal_server_seen"])

    def test_missing_tick_quality_makes_the_result_not_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running", quality=None)
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "not_comparable")
            self.assertIsNone(state["last_result"]["history_quality_pct"])
            self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
