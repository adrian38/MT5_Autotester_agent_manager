from __future__ import annotations

import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mt5_manager.live_audit_engine import LiveAuditController
from tests.helpers import ASYNC_TIMEOUT, wait_until


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
        if scope != "full_history":
            raise ValueError("portfolio inesperado")
        if portfolio_id == 148:
            return {"portfolio": {
                "id": 148, "portfolio_type": "aggressive",
                "improvement_origin": {"source_id": 137, "mode": "aggressive", "depth": 2},
                "members": [
                    {"variant_key": "", "candidate_id": "imp-one", "symbol": "EURUSD", "lot": .02},
                    {"variant_key": "", "candidate_id": "imp-two", "symbol": "XAUUSD", "lot": .03},
                ],
            }}
        if portfolio_id != 9:
            raise ValueError("portfolio inesperado")
        return {"portfolio": {"id": 9, "portfolio_type": "bundle", "members": [
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


class LiveAuditEngineTestCase(unittest.TestCase):
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
        in_progress = {
            "queued", "pausing", "extracting", "testing", "comparing", "finalizing", "resuming",
        }
        last: dict = {}

        def finished() -> bool:
            nonlocal last
            last = controller.state(9)
            return last["status"] not in in_progress

        if not wait_until(finished, interval=.01):
            raise AssertionError(
                f"Plazo de {ASYNC_TIMEOUT:g}s agotado: la auditoría sigue en "
                f"'{last.get('status')}'"
            )
        return last
