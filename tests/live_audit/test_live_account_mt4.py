"""La terminal MT4 del sondeo se cierra siempre al terminar su cuenta."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mt5_manager.live_account import live_account_mt4
from mt5_manager.live_audit.live_audit_processes import _ProcessMixin


class FakeController:
    def __init__(self, root: Path) -> None:
        self.runtime_dir = root
        self.closed: list[set[int]] = []

    def _snapshot_reports_dir(self, snapshot_id: str) -> Path:
        return self.runtime_dir / snapshot_id

    def _close_terminal_pids_gracefully(self, pids: set[int]) -> None:
        self.closed.append(set(pids))


class Mt4CloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.controller = FakeController(self.root)
        self.account = {
            "account_key": "1@Broker", "login": "1", "server": "Broker",
            "password": "secret",
        }
        self.period = datetime(2026, 11, 17, tzinfo=timezone.utc)

    def _read(self) -> dict:
        profile = {"mt4_path": str(self.root / "terminal.exe"), "name": "MT4"}
        with mock.patch.object(live_account_mt4, "_profiles", return_value=[("MT4.1", profile)]), mock.patch.object(
            live_account_mt4, "_launch", return_value={11916},
        ), mock.patch.object(
            live_account_mt4, "export_native_mt4_history_report", return_value={"filename": "a.html"},
        ), mock.patch.object(live_account_mt4, "_summary", return_value=("EUR", 10.0, 11.0)):
            return live_account_mt4.read_mt4_account(
                self.controller, self.account, "snapshot", self.period, self.period,
            )

    def test_a_successful_read_closes_the_mt4_terminal(self) -> None:
        self.assertEqual(self._read()["status"], "completed")
        self.assertEqual(self.controller.closed, [{11916}])

    def test_a_failed_export_also_closes_the_mt4_terminal(self) -> None:
        profile = {"mt4_path": str(self.root / "terminal.exe")}
        with mock.patch.object(live_account_mt4, "_profiles", return_value=[("MT4.1", profile)]), mock.patch.object(
            live_account_mt4, "_launch", return_value={11916},
        ), mock.patch.object(
            live_account_mt4, "export_native_mt4_history_report", side_effect=RuntimeError("roto"),
        ):
            with self.assertRaisesRegex(RuntimeError, "roto"):
                live_account_mt4.read_mt4_account(
                    self.controller, self.account, "snapshot", self.period, self.period,
                )
        self.assertEqual(self.controller.closed, [{11916}])

    def test_a_launch_timeout_retires_the_process_it_created(self) -> None:
        self.controller._terminal_pids_for_path = lambda _path: set()
        profile = {"mt4_path": str(self.root / "terminal.exe")}
        with mock.patch.object(
            live_account_mt4.subprocess, "Popen", return_value=SimpleNamespace(pid=11916),
        ), mock.patch.object(live_account_mt4.time, "monotonic", side_effect=[0.0, 31.0]):
            with self.assertRaisesRegex(RuntimeError, "MT4 no abrió"):
                live_account_mt4._launch(self.controller, profile, self.root / "account.ini")
        self.assertEqual(self.controller.closed, [{11916}])

    def test_process_lookup_accepts_mt4_and_mt5_executables(self) -> None:
        terminal = str(self.root / "terminal.exe")
        answer = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ProcessId": 11916, "ExecutablePath": terminal}),
        )
        with mock.patch("sys.platform", "win32"), mock.patch(
            "mt5_manager.live_audit.live_audit_processes.subprocess.run", return_value=answer,
        ) as run:
            self.assertEqual(_ProcessMixin._terminal_pids_for_path(terminal), {11916})
        command = run.call_args.args[0][-1]
        self.assertIn("terminal64.exe", command)
        self.assertIn("terminal.exe", command)


if __name__ == "__main__":
    unittest.main()
