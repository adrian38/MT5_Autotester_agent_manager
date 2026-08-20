from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_engine import (
    LiveAuditController, _read_set_text, _redact_log_files, _redact_runner_output, normalize_request,
)


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
        "period_days": 7,
        "min_tick_history_quality_pct": 80,
        "trade_time_tolerance_seconds": 60,
        "price_tolerance_points": 10,
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
            {"variant_key": "balanced", "candidate_id": "one", "symbol": "EURUSD"},
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
        controller._extract_real = lambda *_args: ([dict(trade)], {"EURUSD": .00001}, {"login": "111"})
        controller._run_tester = lambda *_args: ([dict(trade)], [] if quality is None else [quality], {"one": 1})
        return owner, controller

    @staticmethod
    def _wait(controller: LiveAuditController) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = controller.state(9)
            if state["status"] not in {"queued", "pausing", "extracting", "testing", "comparing", "resuming"}:
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
            controller._login_terminal = lambda *_args: (
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
            "close_time": now, "open_price": 1.0, "volume": .01, "profit": 1.0,
        }]
        result = LiveAuditController._compare(
            real, expected, {"EURUSD": .00001}, request(), {"one": 1, "two": 1},
        )
        self.assertEqual(result["comparison_detail"]["missing_by_strategy"], {"two": 1})
        self.assertEqual(result["comparison_detail"]["deviation_reasons"]["volume"], 1)
        self.assertEqual(result["comparison_detail"]["deviation_reasons"]["pnl"], 1)

    def test_active_pipeline_is_paused_and_only_that_pipeline_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running")
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "completed")
            self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))

    def test_pipeline_already_paused_by_user_stays_paused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "paused")
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "completed")
            self.assertEqual((owner.pause_calls, owner.resume_calls), (0, 0))
            self.assertEqual(owner.state["status"], "paused")

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
