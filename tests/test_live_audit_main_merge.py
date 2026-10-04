from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_engine import (
    LiveAuditController, _adaptive_price_tolerance_floor,
)
from tests.live_audit_engine_base import FakeOwner, request


def trade(strategy: str = "s1", volume: float = .08, profit: float = 5.97) -> dict:
    now = datetime(2026, 9, 22, 9, 19, 46, tzinfo=timezone.utc)
    return {
        "strategy": strategy, "symbol": "EURUSD", "side": "sell",
        "open_time": now, "close_time": now, "open_price": 1.14621,
        "volume": volume, "profit": profit,
    }


class LiveAuditMainMergeTests(unittest.TestCase):
    def test_the_configured_real_lot_is_the_expectation_for_volume_and_pnl(self) -> None:
        order = {**request(), "real_strategy_lots": {"s1": .04}}
        result = LiveAuditController._compare(
            [trade(volume=.04, profit=3.06)], [trade()], {"EURUSD": .00001}, order, {"s1": 1},
        )
        row = result["comparison_detail"]["operation_comparisons"][0]

        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["limits"]["volume_expected_real"], .04)
        self.assertEqual(row["limits"]["volume_expected_source"], "configured_real_lot")
        self.assertEqual(row["measurements"]["volume_delta"], 0)
        self.assertEqual(row["measurements"]["pnl_scale"], .5)
        self.assertEqual(row["measurements"]["tester_profit_scaled"], 2.98)
        self.assertEqual(row["measurements"]["pnl_direction"], "favorable")

    def test_a_real_lot_that_disobeys_the_configuration_is_still_a_deviation(self) -> None:
        order = {**request(), "real_strategy_lots": {"s1": .04}}
        row = LiveAuditController._compare(
            [trade(volume=.02, profit=1.49)], [trade()], {"EURUSD": .00001}, order, {"s1": 1},
        )["comparison_detail"]["operation_comparisons"][0]

        self.assertEqual(row["status"], "deviation")
        self.assertIn("volume", row["reasons"])

    def test_without_a_configured_real_lot_the_tester_volume_stays_the_expectation(self) -> None:
        row = LiveAuditController._compare(
            [trade(volume=.04, profit=3.06)], [trade()], {"EURUSD": .00001},
            request(), {"s1": 1},
        )["comparison_detail"]["operation_comparisons"][0]

        self.assertEqual(row["status"], "deviation")
        self.assertEqual(row["limits"]["volume_expected_real"], .08)
        self.assertEqual(row["limits"]["volume_expected_source"], "tester_lot")
        self.assertEqual(row["measurements"]["pnl_scale"], 1)

    def test_a_real_position_still_open_is_not_reported_as_missing(self) -> None:
        opened = datetime(2026, 9, 24, 16, 59, 42, tzinfo=timezone.utc)
        expected = {
            **trade("open", .05, -18.44), "symbol": "AMZN",
            "open_time": opened - timedelta(seconds=1),
            "close_time": opened + timedelta(days=1), "open_price": 245.62,
        }
        order = {
            **request(), "drawdown_deviation_warning_pct": 1000,
            "real_strategy_lots": {"open": .02},
            "real_positions_open_at_period_end": [{
                **expected, "open_time": opened, "close_time": None,
                "open_price": 245.64, "volume": .02, "profit": None,
                "position_id": 760842306,
            }],
        }
        result = LiveAuditController._compare([], [expected], {"AMZN": .01}, order, {"open": 1})
        row = result["comparison_detail"]["operation_comparisons"][0]

        self.assertEqual(row["status"], "open")
        self.assertEqual(row["real"]["position_id"], 760842306)
        self.assertEqual(result["matched_trades"], 1)
        self.assertEqual(result["open_real_trades"], 1)
        self.assertEqual(result["missing_real_trades"], 0)
        self.assertEqual(result["discrepancies"], 0)

    def test_an_open_real_position_can_only_align_one_tester_operation(self) -> None:
        expected = trade(volume=.02, profit=-7)
        order = {**request(), "real_positions_open_at_period_end": [{
            **expected, "close_time": None, "profit": None, "position_id": 760842306,
        }]}
        result = LiveAuditController._compare(
            [], [expected, dict(expected)], {"EURUSD": .00001}, order, {"s1": 2},
        )

        statuses = [row["status"] for row in result["comparison_detail"]["operation_comparisons"]]
        self.assertEqual(statuses, ["open", "missing"])
        self.assertEqual(result["open_real_trades"], 1)
        self.assertEqual(result["missing_real_trades"], 1)

    def test_a_missing_real_without_an_open_position_keeps_its_old_reason(self) -> None:
        row = LiveAuditController._compare(
            [], [trade()], {"EURUSD": .00001}, request(), {"s1": 1},
        )["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(row["reasons"], ["no_real_same_symbol_and_side"])
        self.assertIsNone(row["real_position_still_open"])

    def test_broker_prefixed_symbols_keep_their_validated_price_floor(self) -> None:
        cases = {
            ".DE40Cash": (10.5, "adaptive_indices"),
            ".USTECHCash": (10.5, "adaptive_indices"),
            ".JP225Cash": (5.0, "adaptive_nikkei"),
            "JP225Cash": (5.0, "adaptive_nikkei"),
            "JPN225": (5.0, "adaptive_nikkei"),
            "NAS100.fs": (5.0, "adaptive_nasdaq"),
            "XAUUSD": (2.05, "adaptive_gold"),
            "AMZN": (None, "configured_points"),
        }
        for symbol, expected in cases.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(_adaptive_price_tolerance_floor(symbol), expected)

    @staticmethod
    def _verify_with(fake_mt5: type, controller: LiveAuditController) -> list[dict]:
        controller._terminal_pids = lambda: set()
        controller._close_terminal_pids_gracefully = lambda _pids: None
        profile = [("Terminal.2", {"name": "MT5_2", "mt5_path": r"C:\IC\terminal64.exe"})]
        with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            return controller._verify_tester_terminals(request(), profile)

    def test_a_terminal_parked_on_another_account_is_switched_with_login(self) -> None:
        controller = LiveAuditController(FakeOwner("idle"), Path(tempfile.gettempdir()))
        controller.account_probe_seconds = 0
        logins: list[int] = []

        class ParkedMt5:
            initialize = staticmethod(lambda **_kwargs: True)
            login = staticmethod(lambda login, **_kwargs: logins.append(login) or True)
            account_info = staticmethod(lambda: SimpleNamespace(
                login=222 if logins else 67188517, server="IC-Demo",
            ))
            terminal_info = staticmethod(lambda: SimpleNamespace(connected=True))
            shutdown = staticmethod(lambda: None)

        rows = self._verify_with(ParkedMt5, controller)
        self.assertEqual(logins, [222])
        self.assertTrue(rows[0]["verified"])

    def test_a_terminal_already_on_the_tester_account_is_not_switched(self) -> None:
        controller = LiveAuditController(FakeOwner("idle"), Path(tempfile.gettempdir()))
        controller.account_probe_seconds = 0
        logins: list[int] = []

        class ReadyMt5:
            initialize = staticmethod(lambda **_kwargs: True)
            login = staticmethod(lambda login, **_kwargs: logins.append(login) or True)
            account_info = staticmethod(lambda: SimpleNamespace(login=222, server="IC-Demo"))
            terminal_info = staticmethod(lambda: SimpleNamespace(connected=True))
            shutdown = staticmethod(lambda: None)

        rows = self._verify_with(ReadyMt5, controller)
        self.assertEqual(logins, [])
        self.assertTrue(rows[0]["verified"])

    def test_a_refused_account_switch_is_reported_as_such(self) -> None:
        controller = LiveAuditController(FakeOwner("idle"), Path(tempfile.gettempdir()))
        controller.account_probe_seconds = 0

        class RefusingMt5:
            initialize = staticmethod(lambda **_kwargs: True)
            login = staticmethod(lambda _login, **_kwargs: False)
            last_error = staticmethod(lambda: (-6, "Authorization failed"))
            account_info = staticmethod(lambda: SimpleNamespace(login=67188517, server="IC-Demo"))
            terminal_info = staticmethod(lambda: SimpleNamespace(connected=True))
            shutdown = staticmethod(lambda: None)

        with self.assertRaises(RuntimeError) as failure:
            self._verify_with(RefusingMt5, controller)
        self.assertIn("MT5 no cambió a la cuenta tester", str(failure.exception))
        self.assertIn("Authorization failed", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
