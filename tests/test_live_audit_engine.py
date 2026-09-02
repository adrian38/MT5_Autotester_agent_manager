from __future__ import annotations

import sys
import tempfile
import threading
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


def request() -> dict:
    return {
        "audit_key": "9",
        "portfolio_id": 9,
        "portfolio_type": "balanced",
        "source_login": "111",
        "source_server": "IC-Real",
        "source_password": "real-secret",
        "tester_login": "222",
        "tester_server": "IC-Demo",
        "tester_password": "tester-secret",
        "restore_login": "333",
        "restore_server": "CapitalPoint-Live",
        "restore_password": "restore-secret",
        "period_days": 7,
        "min_tick_history_quality_pct": 80,
        "trade_time_tolerance_seconds": 120,
        "price_tolerance_points": 15,
        "volume_tolerance_pct": 1,
        "pnl_deviation_warning_pct": 10,
        "drawdown_deviation_warning_pct": 15,
        "execution_delay_mode": "measured",
        "fixed_delay_ms": 0,
    }


class FakeOwner:
    def __init__(self, status: str) -> None:
        self.lock = threading.RLock()
        self.state = {"status": status, "pipeline": [{"action": "generation"}]}
        self.process = object() if status == "running" else None
        self.queue = []
        self.pause_calls = 0
        self.resume_calls = 0
        self.config = {"project_dir": ".", "settings_file": "ui_settings.ini"}

    def portfolio_detail(self, portfolio_id: int, scope: str) -> dict:
        if portfolio_id != 9 or scope != "full_history":
            raise ValueError("portfolio inesperado")
        return {"portfolio": {"id": 9, "members": [
            {"variant_key": "balanced", "candidate_id": "one", "symbol": "EURUSD", "lot": .01},
            {"variant_key": "aggressive", "candidate_id": "two", "symbol": "XAUUSD"},
        ]}}

    def pause(self) -> dict:
        self.pause_calls += 1
        self.process = None
        self.state["status"] = "paused"
        return dict(self.state)

    def resume(self) -> dict:
        self.resume_calls += 1
        self.state["status"] = "running"
        return dict(self.state)

    def _schedule_queue_drain(self) -> None:
        pass


class LiveAuditEngineTests(unittest.TestCase):
    def _controller(self, root: Path, status: str, quality: float | None = 99.0):
        owner = FakeOwner(status)
        controller = LiveAuditController(owner, root)
        now = datetime.now(timezone.utc)
        trade = {
            "strategy": "one", "symbol": "EURUSD", "side": "buy",
            "open_time": now, "close_time": now, "open_price": 1.1,
            "close_price": 1.1, "volume": .01, "profit": 1.0,
        }
        controller._extract_real = lambda *_args: (
            [dict(trade)], {"EURUSD": .00001},
            {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True}},
        )
        controller._run_tester = lambda *_args: (
            [dict(trade)], [] if quality is None else [quality], {"one": 1}, [],
            {"portfolio_type": "balanced", "set_count": 1, "workers": 1, "terminal_profiles": ["MT5_IC_1"]},
        )
        return owner, controller

    @staticmethod
    def _remember_on_extraction(controller: LiveAuditController) -> None:
        """Imita al auditor real: la extracción deja la cuenta real en un terminal."""
        extract = controller._extract_real

        def remembering(*args):
            controller._remember_real_account_terminal(
                "9", "Terminal.2", {"name": "MT5_IC_1", "mt5_path": r"C:\IC\terminal64.exe"},
            )
            return extract(*args)

        controller._extract_real = remembering

    @staticmethod
    def _wait(controller: LiveAuditController) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = controller.state(9)
            if state["status"] not in {
                "queued", "pausing", "extracting", "testing", "comparing", "finalizing", "resuming",
            }:
                return state
            time.sleep(.01)
        raise AssertionError("la auditoría no terminó")

    def test_credentials_are_required_and_never_enter_public_state(self) -> None:
        payload = request()
        payload["source_password"] = ""
        with self.assertRaisesRegex(ValueError, "source_password"):
            normalize_request(payload)
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            controller.start(request())
            state = self._wait(controller)
            self.assertNotIn("password", str(state).casefold())
            self.assertNotIn("secret", (Path(temp) / "live_audits" / "state.json").read_text(encoding="utf-8"))

    def test_rolling_period_uses_complete_calendar_days(self) -> None:
        normalized = normalize_request(request())
        start, end = _audit_period(normalized, datetime(2026, 8, 30, 16, 45, tzinfo=timezone.utc))

        self.assertEqual(start, datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(end.date().isoformat(), "2026-08-30")
        self.assertEqual(end.time(), datetime.max.time())

    def test_fixed_calendar_period_is_inclusive_and_validated(self) -> None:
        payload = {
            **request(), "period_mode": "fixed_dates",
            "period_start_date": "2026-08-23", "period_end_date": "2026-08-30",
        }
        normalized = normalize_request(payload)
        start, end = _audit_period(normalized)

        self.assertEqual(start.isoformat(), "2026-08-23T00:00:00+00:00")
        self.assertEqual(end.date().isoformat(), "2026-08-30")
        with self.assertRaisesRegex(ValueError, "posterior"):
            normalize_request({**payload, "period_start_date": "2026-08-31"})

    def test_runner_output_redacts_ini_and_incidental_secret_copies(self) -> None:
        text = "[Common]\nPassword=tester-secret\nerror tester-secret\nPassword=another-value\n"
        redacted = _redact_runner_output(text, "tester-secret")
        self.assertNotIn("tester-secret", redacted)
        self.assertNotIn("another-value", redacted)
        self.assertEqual(redacted.count("[REDACTED]"), 3)

    def test_run_tests_own_log_files_are_redacted_too(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "run.log").write_text("Password=tester-secret\n", encoding="utf-8")
            (root / "ignore.htm").write_text("Password=tester-secret\n", encoding="utf-8")
            _redact_log_files(root, "tester-secret")
            log = (root / "run.log").read_text(encoding="utf-8")
            report = (root / "ignore.htm").read_text(encoding="utf-8")

        self.assertNotIn("tester-secret", log)
        self.assertIn("tester-secret", report)

    def test_utf16_set_is_decoded_and_start_lots_is_replaced_without_nuls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "strategy.set"
            path.write_text(
                "EA_MagicNumber=1007||1000||1||10000||N\nStartLots=0.05||0.01||0.01||1||N\n",
                encoding="utf-16",
            )
            text, encoding = _read_set_text(path)
            changed = LiveAuditController._set_value(text, "StartLots", "0.02")
            target = Path(temp) / "changed.set"
            target.write_text(changed, encoding=encoding)
            reread, _ = _read_set_text(target)

        self.assertEqual(encoding, "utf-16")
        self.assertNotIn("\x00", reread)
        self.assertIn("StartLots=0.02||0.01||0.01||1||N", reread)
        self.assertEqual(LiveAuditController._set_parameter(reread, "EA_MagicNumber"), "1007")

    def test_saved_ustec_lot_is_raised_to_the_broker_minimum_for_the_tester(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "ictrading_symbol_specs.json").write_text(
                '{"symbols":{"USTEC":{"volume_min":0.1,"volume_step":0.1}}}',
                encoding="utf-8",
            )
            owner = FakeOwner("idle")
            owner.config.update(project_dir=str(root), broker="ICTRADING")
            controller = LiveAuditController(owner, root / "runtime")
            rules = controller._broker_volume_rules()
            result = controller._tester_lot(
                {"symbol": "USTEC", "units": 1, "lot": 0.01}, rules,
            )

        self.assertEqual(result, (0.01, 0.1, 0.1, 0.1, 1))

    def test_portfolio_units_do_not_multiply_the_broker_minimum(self) -> None:
        result = LiveAuditController._tester_lot(
            {"symbol": "DE40", "units": 3, "lot": 0.03}, {"de40": (0.1, 0.1)},
        )

        self.assertEqual(result, (0.03, 0.1, 0.1, 0.1, 3))

    def test_real_account_report_must_be_the_native_terminal_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ReportHistory-111.html"
            path.write_text(
                '<html><head><title>111 - Trade History Report</title>'
                '<meta name="generator" content="client terminal"></head><body>'
                + ("x" * 600) + "</body></html>",
                encoding="utf-16",
            )
            artifact = validate_native_history_report(path, "111")
            localized = Path(temp) / "InformeHistorial-111.html"
            localized.write_text(
                '<html><head><title>111 - Informe del historial de trading</title>'
                '<meta name="generator" content="client terminal"></head><body>'
                + ("x" * 600) + "</body></html>",
                encoding="utf-16",
            )
            localized_artifact = validate_native_history_report(localized, "111")
            fake = Path(temp) / "reconstructed.html"
            fake.write_text("<html>Historial reconstruido</html>", encoding="utf-8")

            with self.assertRaises(NativeHistoryReportError):
                validate_native_history_report(fake, "111")

        self.assertTrue(artifact["native_terminal_report"])
        self.assertTrue(localized_artifact["native_terminal_report"])
        self.assertEqual(artifact["source"], "mt5_terminal_history_report")

    def test_artifact_path_only_exposes_reports_from_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = LiveAuditController(FakeOwner("idle"), Path(temp))
            controller.states["9"] = {"audit_key": "9", "audit_id": "run_1", "status": "completed"}
            reports = Path(temp) / "live_audits" / "audit_9" / "run_1" / "reports"
            reports.mkdir(parents=True)
            report = reports / "strategy.htm"
            report.write_text("report", encoding="utf-8")
            hidden_set = reports / "strategy.set"
            hidden_set.write_text("StartLots=0.06", encoding="utf-8")

            self.assertEqual(controller.artifact_path("9", "run_1", "strategy.htm"), report.resolve())
            with self.assertRaises(ValueError):
                controller.artifact_path("9", "run_1", "strategy.set")
            with self.assertRaises(ValueError):
                controller.artifact_path("9", "run_1", "../strategy.htm")
            with self.assertRaises(FileNotFoundError):
                controller.artifact_path("9", "old_run", "strategy.htm")

    def test_real_history_waits_for_sync_and_recovers_open_before_period(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = LiveAuditController(FakeOwner("idle"), Path(temp))
            controller.history_sync_attempts = 4
            controller.history_sync_delay_seconds = 0
            period_end = datetime.now(timezone.utc)
            period_start = period_end - timedelta(days=7)

            def deal(ticket: int, position: int, entry: int, moment: datetime, deal_type: int) -> SimpleNamespace:
                timestamp = int(moment.timestamp())
                return SimpleNamespace(
                    ticket=ticket, position_id=position, entry=entry, type=deal_type,
                    time=timestamp, time_msc=timestamp * 1000, magic=11008,
                    symbol="EURUSD", volume=.01, price=1.1, profit=1.0,
                    commission=0.0, swap=0.0, fee=0.0, comment="",
                )

            prior_open = deal(1, 10, 0, period_start - timedelta(days=1), 0)
            prior_close = deal(2, 10, 1, period_start + timedelta(hours=1), 1)
            current_open = deal(3, 20, 0, period_start + timedelta(days=1), 0)
            current_close = deal(4, 20, 1, period_start + timedelta(days=1, hours=1), 1)
            period_deals = [prior_close, current_open, current_close]

            class FakeMt5:
                def __init__(self) -> None:
                    self.period_calls = 0
                    self.shutdown_called = False

                @staticmethod
                def account_info() -> SimpleNamespace:
                    return SimpleNamespace(login=111, server="IC-Real", currency="USD")

                @staticmethod
                def terminal_info() -> SimpleNamespace:
                    return SimpleNamespace(connected=True)

                def history_deals_get(self, *_args, **kwargs):
                    if "position" in kwargs:
                        return [prior_open, prior_close] if kwargs["position"] == 10 else []
                    self.period_calls += 1
                    return [] if self.period_calls == 1 else period_deals

                @staticmethod
                def symbol_info(_symbol: str) -> SimpleNamespace:
                    return SimpleNamespace(point=.00001)

                @staticmethod
                def last_error() -> tuple[int, str]:
                    return 1, "Success"

                def shutdown(self) -> None:
                    self.shutdown_called = True

            mt5 = FakeMt5()
            controller._login_terminal = lambda *_args, **_kwargs: (
                mt5, "Terminal.2", {"name": "MT5_IC_1"}, set()
            )
            trades, points, account = controller._extract_real(request(), period_start, period_end)

        self.assertEqual(len(trades), 2)
        self.assertEqual(points, {"EURUSD": .00001})
        self.assertTrue(account["connected"])
        self.assertEqual(account["server"], "IC-Real")
        detail = account["history_detail"]
        self.assertEqual(detail["sync_snapshots"], [0, 3, 3])
        self.assertEqual(detail["period_raw_deals"], 3)
        self.assertEqual(detail["closing_deals"], 2)
        self.assertEqual(detail["positions_missing_open_in_period"], 1)
        self.assertEqual(detail["positions_recovered"], 1)
        self.assertEqual(detail["trades_reconstructed"], 2)
        self.assertTrue(mt5.shutdown_called)

    def test_portfolio_variant_is_required_and_selects_only_that_variant(self) -> None:
        payload = request()
        payload["portfolio_type"] = ""
        with self.assertRaisesRegex(ValueError, "portfolio_type"):
            normalize_request(payload)
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            _detail, members = controller._portfolio_members(9, "balanced")
            self.assertEqual([row["candidate_id"] for row in members], ["one"])

    def test_tester_uses_five_configured_broker_terminals_for_six_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            profiles = [
                (f"Terminal.{index}", {"name": f"MT5_IC_{index}", "mt5_path": fr"C:\\IC{index}\\terminal64.exe"})
                for index in range(1, 6)
            ]
            controller._terminal_profiles = lambda *, include_disabled=False: (
                list(profiles) if include_disabled else list(profiles[:1])
            )
            selected = controller._tester_terminal_pool("Terminal.3", profiles[2][1], 6)

        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0][0], "Terminal.3")
        self.assertEqual({section for section, _profile in selected}, {section for section, _profile in profiles})

    def test_native_report_fallback_uses_only_the_active_broker_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary = root / "primary.exe"
            fallback = root / "fallback.exe"
            foreign = root / "foreign.exe"
            for path in (primary, fallback, foreign):
                path.touch()
            (root / "ui_settings.ini").write_text(
                "[Terminal.1]\nname=Primary\nenabled=1\nbroker=ICTRADING\nmt5_path=" + str(primary) + "\n"
                "[Terminal.2]\nname=Fallback\nenabled=0\nbroker=ICTRADING\nmt5_path=" + str(fallback) + "\n"
                "[Terminal.3]\nname=Foreign\nenabled=1\nbroker=ROBOFOREX\nmt5_path=" + str(foreign) + "\n",
                encoding="utf-8",
            )
            owner = FakeOwner("idle")
            owner.config.update(project_dir=str(root), settings_file="ui_settings.ini")
            controller = LiveAuditController(owner, root / "runtime")
            profiles = controller._native_report_profiles(primary)

        self.assertEqual([profile[1]["name"] for profile in profiles], ["Fallback"])

    def test_comparison_explains_missing_extra_and_deviation_reasons(self) -> None:
        now = datetime.now(timezone.utc)
        real = [{
            "strategy": "1007", "symbol": "EURUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 1.2, "volume": .02, "profit": 5.0,
        }]
        expected = [{
            "strategy": "one", "symbol": "EURUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 1.1, "volume": .01, "profit": 1.0,
        }, {
            "strategy": "two", "symbol": "XAUUSD", "side": "sell", "open_time": now,
            "close_time": now - timedelta(hours=1), "open_price": 1.0, "volume": .01, "profit": 1.0,
        }]
        result = LiveAuditController._compare(
            real, expected, {"EURUSD": .00001}, request(), {"one": 1, "two": 1},
        )
        self.assertEqual(result["comparison_detail"]["missing_by_strategy"], {"two": 1})
        self.assertEqual(result["comparison_detail"]["deviation_reasons"]["volume"], 1)
        self.assertEqual(result["comparison_detail"]["deviation_reasons"]["pnl"], 1)
        self.assertEqual(result["matched_trades"], 1)
        self.assertEqual(result["within_tolerance_trades"], 0)
        self.assertEqual(result["deviating_pairs"], 1)
        rows = result["comparison_detail"]["operation_comparisons"]
        self.assertEqual([row["status"] for row in rows], ["deviation", "missing"])
        self.assertEqual(rows[0]["real"]["strategy"], "1007")
        self.assertIsInstance(rows[0]["tester"]["open_time"], str)
        self.assertEqual(rows[0]["measurements"]["open_price_delta_points"], 10000.0)
        self.assertEqual(rows[1]["reasons"], ["no_real_same_symbol_and_side"])
        self.assertEqual(rows[1]["data_issues"], ["close_before_open"])
        self.assertEqual(result["comparison_detail"]["tester_data_issues"], {"close_before_open": 1})
        self.assertEqual(result["comparison_detail"]["strategy_summary"][0], {
            "strategy": "one", "tester_trades": 1, "aligned": 1,
            "within_tolerance": 0, "with_deviations": 1, "missing_real": 0,
        })
        self.assertIn("cada real se usa una vez", result["comparison_detail"]["methodology"]["alignment"])

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
            initialize_calls: list[dict[str, object]] = []
            launches: list[tuple[str, str | None]] = []
            closed_gracefully: list[set[int]] = []

            class FakeMt5:
                @staticmethod
                def initialize(**kwargs) -> bool:
                    initialize_calls.append(dict(kwargs))
                    return True

                @staticmethod
                def account_info() -> SimpleNamespace:
                    return SimpleNamespace(login=333, server="CapitalPoint-Live", currency="EUR")

                @staticmethod
                def terminal_info() -> SimpleNamespace:
                    return SimpleNamespace(connected=True)

                @staticmethod
                def shutdown() -> None:
                    pass

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

        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(initialize_calls), 2)
        self.assertTrue(all(set(call) == {"path", "timeout"} for call in initialize_calls))
        self.assertEqual(len(launches), 2)
        self.assertIn("KeepPrivate = 1", launches[0][1] or "")
        self.assertIn("Login = 333", launches[0][1] or "")
        self.assertIn("Password = restore-secret", launches[0][1] or "")
        self.assertIsNone(launches[1][1])
        self.assertEqual(closed_gracefully, [set(), set(), set()])
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
        # La restauración precede a la reanudación: el pipeline no puede reabrir
        # el terminal en la cuenta real.
        self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))
        self.assertTrue(any("MT5_IC_1 → 333 (CapitalPoint-Live)" in line for line in state["log_lines"]))

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
