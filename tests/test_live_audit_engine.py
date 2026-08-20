from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mt5_manager.live_audit_engine import LiveAuditController, normalize_request


def request() -> dict:
    return {
        "portfolio_id": 9,
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
        return {"portfolio": {"id": 9, "members": []}}

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
