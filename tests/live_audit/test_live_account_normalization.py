"""Normalización por lote de la cuenta real que el usuario eligió en el manager."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from portfolio_manager.mt5_report import RawDeal
from mt5_manager import live_account_report, live_account_watch
from mt5_manager.live_account_archive import LiveAccountArchive


def deal(symbol: str, volume: float, profit: float) -> RawDeal:
    return RawDeal(
        timestamp=datetime(2026, 10, 20, 12), ticket="1", symbol=symbol,
        trade_type="buy", direction="out", volume=volume, price=1.0, order="1",
        commission=-1.0, swap=-2.0, profit=profit, balance=0.0, comment="",
    )


class AccountLotNormalizationTests(unittest.TestCase):
    KEY = "10010194@Axi-US50-Demo"

    def test_btc_and_eth_are_scaled_from_the_executed_lot_to_001(self) -> None:
        for symbol in ("BTCUSD", "ETHUSD"):
            scaled = live_account_report.normalized_deal(deal(symbol, 0.10, 103.0), self.KEY)
            self.assertAlmostEqual(scaled.volume, 0.10)
            self.assertAlmostEqual(scaled.net_profit, 10.0)

    def test_other_symbols_and_accounts_remain_observed(self) -> None:
        original = deal("XAUUSD", 0.10, 103.0)
        self.assertIs(live_account_report.normalized_deal(original, self.KEY), original)
        self.assertIs(
            live_account_report.normalized_deal(original, "otra@cuenta"), original,
        )

    def test_the_current_snapshot_is_reingested_once_for_the_new_criterion(self) -> None:
        self.assertEqual(
            live_account_report.ingestion_key("snapshot-1", [self.KEY]),
            "snapshot-1:account-lot-normalization-v1",
        )
        self.assertEqual(
            live_account_report.ingestion_key("snapshot-1", ["otra@cuenta"]),
            "snapshot-1",
        )

    def test_an_old_archive_requests_the_full_history_once(self) -> None:
        with TemporaryDirectory() as folder:
            archive = LiveAccountArchive(Path(folder))
            archive.merge(
                "axi", self.KEY, login="10010194", server="Axi-US50-Demo",
                currency="EUR", movements=[(datetime(2026, 10, 20), 10.0)],
                period_start="", period_end="2026-10-20",
            )
            server = SimpleNamespace(
                live_accounts=archive,
                live_account_scheduler_settings={"overlap_days": 3},
            )
            period = live_account_watch._period(
                server, "axi", [{"account_key": self.KEY}],
                datetime(2026, 10, 21, tzinfo=timezone.utc),
            )
        self.assertEqual(period, ("", "2026-10-21"))


if __name__ == "__main__":
    unittest.main()
