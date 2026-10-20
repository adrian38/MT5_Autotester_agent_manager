"""El sondeo de cuentas reales en el agente.

Lo que se comprueba aquí es la orquestación, que es lo que puede dejar un
terminal con la cuenta de un tercero: que pausa el pipeline, que lee una cuenta
tras otra, que un fallo no tumba la tanda, que restaura y reanuda pase lo que
pase, y que el estado publicado no lleva contraseñas.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_account_snapshot
from mt5_manager.live_audit_engine import LiveAuditController
from tests.helpers import ASYNC_TIMEOUT, wait_until
from tests.live_audit_engine_base import FakeOwner

FINAL = {"completed", "failed", "stopped", "idle"}


def payload(**changes: object) -> dict[str, object]:
    row = {
        "snapshot_key": "2026-10-21T23:00:00+02:00",
        "period_start_date": "2026-10-17",
        "period_end_date": "2026-10-21",
        "restore_login": "333",
        "restore_server": "CapitalPoint-Live",
        "restore_password": "restore-secret",
        "accounts": [
            {"account_key": "111@Axi-US50-Live", "login": "111",
             "server": "Axi-US50-Live", "password": "real-secret"},
            {"account_key": "222@RoboForex-ECN", "login": "222",
             "server": "RoboForex-ECN", "password": "otro-secreto"},
        ],
    }
    row.update(changes)
    return row


class FakeInfo:
    def __init__(self, login: str, server: str) -> None:
        self.login = int(login)
        self.server = server
        self.currency = "EUR"
        self.balance = 1000.0
        self.equity = 1012.5


class FakeMt5:
    def __init__(self) -> None:
        self.shutdowns = 0

    def shutdown(self) -> None:
        self.shutdowns += 1


class RequestTests(unittest.TestCase):
    def test_an_account_without_password_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            live_account_snapshot.normalize_snapshot_request(payload(accounts=[
                {"login": "111", "server": "Axi-US50-Live"},
            ]))

    def test_without_the_final_account_nothing_is_read(self) -> None:
        with self.assertRaises(ValueError) as caught:
            live_account_snapshot.normalize_snapshot_request(payload(restore_login=""))
        self.assertIn("cuenta final", str(caught.exception))

    def test_too_many_accounts_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            live_account_snapshot.normalize_snapshot_request(payload(accounts=[
                {"login": str(index), "server": "S", "password": "p"}
                for index in range(live_account_snapshot.MAXIMUM_ACCOUNTS + 1)
            ]))

    def test_the_account_key_defaults_to_login_and_server(self) -> None:
        request = live_account_snapshot.normalize_snapshot_request(payload(accounts=[
            {"login": "111", "server": "Axi-US50-Live", "password": "p"},
        ]))
        self.assertEqual(request["accounts"][0]["account_key"], "111@Axi-US50-Live")

    def test_a_period_without_start_asks_for_the_whole_life(self) -> None:
        request = live_account_snapshot.normalize_snapshot_request(
            payload(period_start_date=""),
        )
        start, end = live_account_snapshot.snapshot_period(
            request, datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(end.date().isoformat(), "2026-10-21")
        self.assertLess((end - start).days, live_account_snapshot.DEFAULT_HISTORY_DAYS + 1)
        self.assertGreater((end - start).days, 3000)

    def test_the_period_never_reaches_past_the_present(self) -> None:
        now = datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)
        _start, end = live_account_snapshot.snapshot_period(
            live_account_snapshot.normalize_snapshot_request(payload()), now,
        )
        self.assertEqual(end, now)

    def test_a_date_that_is_not_a_date_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            live_account_snapshot.normalize_snapshot_request(
                payload(period_end_date="el martes"),
            )


class SnapshotRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.owner = FakeOwner("running")
        self.controller = LiveAuditController(self.owner, Path(self.folder.name))
        self.read: list[str] = []
        self.restored: list[dict[str, object]] = []
        self.controller._login_terminal = self._login
        self.controller._synchronised_history = lambda *_args: ([], {})
        self.controller._export_native_account_report = self._export
        self.controller._close_terminal_pids = lambda *_args: None
        self.controller._restore_tester_login = self._restore
        self.failing: set[str] = set()

    def _login(self, login, password, server, remember_for=None):
        self.controller._remember_real_account_terminal(
            str(remember_for), "Terminal.1",
            {"name": "MT5_1", "mt5_path": r"C:\MT5\terminal64.exe"},
        )
        return FakeMt5(), "Terminal.1", {"name": "MT5_1", "mt5_path": r"C:\MT5\terminal64.exe"}, set()

    def _export(self, *, mt5, request, profile_name, terminal_path, login, server,
                period_start, period_end, destination):
        self.read.append(str(login))
        if str(login) in self.failing:
            raise RuntimeError("MT5 no entregó el HTML nativo")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_text("<html></html>", encoding="utf-8")
        return {
            "filename": Path(destination).name, "sha256": "abc", "bytes": 13,
            "native_terminal_report": True,
        }

    def _restore(self, request):
        self.restored.append(dict(request))
        return [{"terminal": "MT5_1", "restored": True, "error": None}]

    def _confirm(self, mt5, request):
        return FakeInfo(request["source_login"], request["source_server"]), request["source_server"]

    def _wait(self) -> dict:
        self.controller._confirmed_real_account = self._confirm
        last: dict = {}

        def finished() -> bool:
            nonlocal last
            last = self.controller.account_snapshot_state()
            return str(last.get("status")) in FINAL

        if not wait_until(finished, interval=.01):
            raise AssertionError(
                f"Plazo de {ASYNC_TIMEOUT:g}s agotado: el sondeo sigue en "
                f"'{last.get('status')}'"
            )
        return last

    def test_it_reads_every_account_and_restores_and_resumes(self) -> None:
        self.controller._confirmed_real_account = self._confirm
        self.controller.start_account_snapshot(payload())
        state = self._wait()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(self.read, ["111", "222"])
        self.assertEqual(self.owner.pause_calls, 1)
        self.assertEqual(self.owner.resume_calls, 1)
        self.assertEqual(self.restored[0]["restore_login"], "333")

    def test_each_account_publishes_its_report_and_its_balance(self) -> None:
        self.controller._confirmed_real_account = self._confirm
        self.controller.start_account_snapshot(payload())
        state = self._wait()
        row = state["accounts"]["111@Axi-US50-Live"]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["currency"], "EUR")
        self.assertAlmostEqual(row["balance"], 1000.0)
        self.assertEqual(row["report"]["filename"], "account_111_Axi-US50-Live.html")

    def test_a_failing_account_does_not_take_the_batch_down(self) -> None:
        self.failing = {"111"}
        self.controller._confirmed_real_account = self._confirm
        self.controller.start_account_snapshot(payload())
        state = self._wait()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["accounts"]["111@Axi-US50-Live"]["status"], "failed")
        self.assertIn("HTML nativo", state["accounts"]["111@Axi-US50-Live"]["error"])
        self.assertEqual(state["accounts"]["222@RoboForex-ECN"]["status"], "completed")
        self.assertEqual(self.owner.resume_calls, 1)

    def test_the_terminals_come_back_even_when_the_whole_batch_fails(self) -> None:
        self.controller._confirmed_real_account = self._confirm
        self.controller._login_terminal = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("ninguna terminal aceptó el login"),
        )
        self.controller.start_account_snapshot(payload())
        state = self._wait()
        self.assertEqual(state["status"], "completed")
        self.assertTrue(self.restored)
        self.assertEqual(self.owner.resume_calls, 1)

    def test_the_published_state_never_carries_a_password(self) -> None:
        self.controller._confirmed_real_account = self._confirm
        self.controller.start_account_snapshot(payload())
        state = self._wait()
        self.assertNotIn("real-secret", repr(state))
        self.assertNotIn("restore-secret", repr(state))
        stored = (Path(self.folder.name) / "live_audits" / "account_snapshot.json").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("real-secret", stored)

    def test_an_idle_pipeline_is_not_paused_nor_resumed(self) -> None:
        owner = FakeOwner("idle")
        controller = LiveAuditController(owner, Path(self.folder.name) / "idle")
        controller._login_terminal = self._login
        controller._confirmed_real_account = self._confirm
        controller._synchronised_history = lambda *_args: ([], {})
        controller._export_native_account_report = self._export
        controller._close_terminal_pids = lambda *_args: None
        controller._restore_tester_login = self._restore
        controller.start_account_snapshot(payload())
        self.controller = controller
        self._wait()
        self.assertEqual(owner.pause_calls, 0)
        self.assertEqual(owner.resume_calls, 0)


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.controller = LiveAuditController(FakeOwner("idle"), Path(self.folder.name))

    def test_a_snapshot_in_flight_blocks_an_audit_and_the_other_way_round(self) -> None:
        self.controller.account_snapshot["status"] = "extracting"
        self.assertTrue(self.controller.is_running())
        with self.assertRaises(RuntimeError):
            self.controller.start_account_snapshot(payload())
        self.controller.account_snapshot["status"] = "completed"
        self.assertFalse(self.controller.is_running())
        self.controller.states["9"] = {"status": "testing"}
        with self.assertRaises(RuntimeError) as caught:
            self.controller.start_account_snapshot(payload())
        self.assertIn("terminales del nodo", str(caught.exception))

    def test_an_interrupted_snapshot_does_not_come_back_as_running(self) -> None:
        self.controller.account_snapshot.update(status="extracting", snapshot_id="x")
        self.controller._persist_snapshot()
        again = LiveAuditController(FakeOwner("idle"), Path(self.folder.name))
        self.assertEqual(again.account_snapshot["status"], "failed")
        self.assertFalse(again.is_running())

    def test_the_artifact_is_only_served_for_the_visible_snapshot(self) -> None:
        self.controller.account_snapshot.update(snapshot_id="20261021_230000_000001")
        with self.assertRaises(FileNotFoundError):
            self.controller.account_snapshot_artifact("otro", "account_x.html")
        for name in ("../secreto.html", "informe.exe", ""):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.controller.account_snapshot_artifact("20261021_230000_000001", name)


if __name__ == "__main__":
    unittest.main()
