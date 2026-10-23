"""Normalización por lote de la cuenta real que el usuario eligió en el manager."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from portfolio_manager.mt5_report import RawDeal
from mt5_manager import (
    live_account_expectation, live_account_ingest, live_account_report,
    live_account_watch,
)
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
            "snapshot-1:merge-window-v2+account-lot-normalization-v1",
        )
        self.assertEqual(
            live_account_report.ingestion_key("snapshot-1", ["otra@cuenta"]),
            "snapshot-1:merge-window-v2",
        )

    def test_the_promise_is_measured_at_the_same_lot_the_series_is_shown_at(self) -> None:
        """Las dos mitades de la tarjeta, a la misma escala.

        La serie real de esta cuenta lleva BTCUSD y ETHUSD a 0,01, pero el
        perfil declara los 0,10 que el EA ejecuta. Medida la promesa a 0,10, el
        objetivo del mes salía diez veces mayor en esos dos símbolos.
        """
        members = [
            {"candidate_id": "AXI/STANDARD:21175", "symbol": "BTCUSD"},
            {"candidate_id": "AXI/STANDARD:46978", "symbol": "ETHUSD"},
            {"candidate_id": "AXI/STANDARD:3813", "symbol": "XAUUSD"},
        ]
        self.assertEqual(live_account_report.base_lots(self.KEY, members), {
            "AXI/STANDARD:21175": 0.01, "AXI/STANDARD:46978": 0.01,
        })
        lots = live_account_ingest._measured_lots(
            {
                "account_key": self.KEY,
                "real_strategy_lots": {
                    "AXI/STANDARD:21175": 0.1, "AXI/STANDARD:46978": 0.1,
                    "AXI/STANDARD:3813": 0.01,
                },
            },
            {"members": members}, "",
        )
        self.assertEqual(lots, {
            "AXI/STANDARD:21175": 0.01, "AXI/STANDARD:46978": 0.01,
            "AXI/STANDARD:3813": 0.01,
        })

    def test_an_account_without_normalization_keeps_the_lot_of_its_profile(self) -> None:
        members = [{"candidate_id": "RF/ECN:1", "symbol": "BTCUSD"}]
        self.assertEqual(live_account_report.base_lots("otra@cuenta", members), {})
        self.assertEqual(
            live_account_ingest._measured_lots(
                {"account_key": "otra@cuenta", "real_strategy_lots": {"RF/ECN:1": 0.5}},
                {"members": members}, "",
            ),
            {"RF/ECN:1": 0.5},
        )

    def test_a_new_criterion_remeasures_without_waiting_for_the_clock(self) -> None:
        """La edad decide cada cuánto se comprueba; el criterio, si vale.

        El 2026-10-23 la media del #67 se arregló con 18 horas de vida: por
        edad no tocaba recalcularla hasta la medianoche, así que la pantalla
        siguió enseñando el número viejo con sondeos de por medio.
        """
        now = datetime(2026, 10, 23, 17, tzinfo=timezone.utc)
        fresh = {
            "updated_at": "2026-10-23T15:00:00+00:00",
            "criterion": live_account_expectation.CRITERION_VERSION,
        }
        self.assertFalse(live_account_ingest._needs_rebuild(fresh, now))
        self.assertTrue(live_account_ingest._needs_rebuild(
            {**fresh, "criterion": "criterio-anterior"}, now,
        ))
        # Una media de antes de que existiera el campo tampoco es de ahora.
        self.assertTrue(live_account_ingest._needs_rebuild(
            {"updated_at": fresh["updated_at"]}, now,
        ))

    def test_every_account_carries_its_criterion_not_only_the_scaled_ones(self) -> None:
        """Sin versión no hay reparación posible.

        El solapamiento que duplicaba movimientos afectaba a cualquier cuenta,
        no sólo a las del mapa de lotes. Con la versión vacía, `_period` nunca
        habría pedido toda la vida de las demás y sus duplicados se habrían
        quedado ahí hasta que alguien borrase el fichero a mano.
        """
        self.assertIn(
            live_account_report.SERIES_VERSION,
            live_account_report.normalization_version("otra@cuenta"),
        )
        self.assertIn(
            live_account_report.NORMALIZATION_VERSION,
            live_account_report.normalization_version(self.KEY),
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
