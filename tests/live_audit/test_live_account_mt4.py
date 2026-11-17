"""La terminal MT4 del sondeo se cierra siempre al terminar su cuenta."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mt5_manager.live_account import live_account_mt4, mt4_native_history_report
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


def _mfc_terminal(visible: set[int], ids: dict[int, int]) -> object:
    """Reproduce el panel Terminal del MT4 de Axi: dock, franja y páginas."""
    exporter = object.__new__(mt4_native_history_report._WindowsMt4ReportExporter)
    tree = {1: [2, 3, 4, 5], 2: [3, 4, 5], 3: [4, 5]}
    exporter._children = lambda hwnd: tree.get(hwnd, [])
    exporter._class_name = lambda hwnd: {
        2: "AfxControlBar140s", 3: "Afx:008B0000", 4: "AfxWnd140s",
    }.get(hwnd, "SysListView32")
    exporter._window_text = lambda hwnd: {2: "Terminal", 3: "Terminal"}.get(hwnd, "")
    exporter._window_rect = lambda hwnd: (24, 991, 1924, 1012) if hwnd == 4 else (0, 0, 0, 0)
    exporter.user32 = SimpleNamespace(
        GetParent=lambda hwnd: 3 if hwnd in {4, 5} else 0,
        GetDlgCtrlID=lambda hwnd: ids.get(hwnd, 0),
        IsWindowVisible=lambda hwnd: hwnd in visible,
        SendMessageW=mock.Mock(),
    )
    return exporter


class Mt4HistoryTabTests(unittest.TestCase):
    def test_mfc_strip_is_clicked_until_the_history_page_shows(self) -> None:
        visible = {4}
        ids = {5: mt4_native_history_report.HISTORY_PAGE_ID}
        exporter = _mfc_terminal(visible, ids)
        clicks: list[int] = []

        def click(hwnd: int, message: int, _wparam: int, lparam: int) -> int:
            if message == mt4_native_history_report.WM_LBUTTONUP:
                clicks.append(lparam & 0xFFFF)
                if len(clicks) == 3:
                    visible.add(5)
            return 0

        exporter.user32.SendMessageW.side_effect = click
        with mock.patch.object(mt4_native_history_report.time, "sleep"):
            self.assertEqual(exporter._custom_history_tab(1), (4, -1, -1))

        self.assertEqual(clicks, [10, 30, 50])

    def test_the_history_page_already_visible_is_not_clicked(self) -> None:
        ids = {5: mt4_native_history_report.HISTORY_PAGE_ID}
        exporter = _mfc_terminal({4, 5}, ids)

        self.assertEqual(exporter._custom_history_tab(1), (4, -1, -1))
        exporter.user32.SendMessageW.assert_not_called()

    def test_a_terminal_without_the_history_page_is_not_a_custom_tab(self) -> None:
        exporter = _mfc_terminal({4}, {5: 33217})

        self.assertIsNone(exporter._custom_history_tab(1))
        exporter.user32.SendMessageW.assert_not_called()

    def test_the_right_click_declares_the_button_or_mt4_ignores_it(self) -> None:
        exporter = object.__new__(mt4_native_history_report._WindowsMt4ReportExporter)
        exporter.user32 = SimpleNamespace(PostMessageW=mock.Mock())
        exporter._wait = lambda predicate, message: 7
        exporter._menu_items = lambda popup: [("guardar como informe", 33064)]

        exporter._context_command(1, 10, 5, ("guardar como informe",))

        posted = exporter.user32.PostMessageW.call_args_list
        self.assertEqual(posted[0].args[:3], (
            5, mt4_native_history_report.WM_RBUTTONDOWN, mt4_native_history_report.MK_RBUTTON,
        ))
        self.assertEqual(posted[-1].args, (1, mt4_native_history_report.mt5_report.WM_COMMAND, 33064, 0))

    def test_a_strip_that_never_shows_the_page_fails_with_a_named_error(self) -> None:
        ids = {5: mt4_native_history_report.HISTORY_PAGE_ID}
        exporter = _mfc_terminal({4}, ids)

        with mock.patch.object(mt4_native_history_report.time, "sleep"):
            with self.assertRaises(mt4_native_history_report.NativeMt4HistoryReportError):
                exporter._custom_history_tab(1)


if __name__ == "__main__":
    unittest.main()
