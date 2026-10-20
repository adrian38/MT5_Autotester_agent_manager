"""Profit y DD por día y por mes de una cuenta real.

El día se corta en UTC y los apuntes de saldo no son beneficio: son las dos
cosas que, mal hechas, convierten un depósito en el mejor día de la cuenta o
mueven un cierre de madrugada al día de antes.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_account_series


@dataclass
class Movement:
    """Lo mismo que publica `RawDeal` del informe nativo, sin lo demás."""

    timestamp: datetime
    trade_type: str
    commission: float = 0.0
    swap: float = 0.0
    profit: float = 0.0

    @property
    def net_profit(self) -> float:
        return self.commission + self.swap + self.profit


def deal(stamp: str, profit: float, kind: str = "sell", **extra: float) -> Movement:
    return Movement(
        timestamp=datetime.fromisoformat(stamp), trade_type=kind, profit=profit, **extra,
    )


class MovementTests(unittest.TestCase):
    def test_a_balance_entry_is_not_profit(self) -> None:
        ordered = live_account_series.movements([
            deal("2026-10-20T10:00:00", 12.0),
            deal("2026-10-20T11:00:00", 1000.0, kind="balance"),
            deal("2026-10-20T12:00:00", 500.0, kind="Saldo"),
        ])
        self.assertEqual([amount for _moment, amount in ordered], [12.0])

    def test_commission_and_swap_are_part_of_the_result(self) -> None:
        ordered = live_account_series.movements([
            deal("2026-10-20T10:00:00", 12.0, commission=-1.5, swap=-0.5),
        ])
        self.assertAlmostEqual(ordered[0][1], 10.0)

    def test_movements_come_out_ordered_and_in_utc(self) -> None:
        ordered = live_account_series.movements([
            deal("2026-10-20T12:00:00", 2.0),
            deal("2026-10-20T09:00:00", 1.0),
        ])
        self.assertEqual([amount for _moment, amount in ordered], [1.0, 2.0])
        self.assertEqual(ordered[0][0].tzinfo, timezone.utc)

    def test_a_stamp_with_zone_keeps_its_instant(self) -> None:
        madrid = timezone(timedelta(hours=2))
        ordered = live_account_series.movements([
            Movement(
                timestamp=datetime(2026, 10, 21, 1, 30, tzinfo=madrid),
                trade_type="buy", profit=5.0,
            ),
        ])
        # 01:30 en Madrid es 23:30 UTC del día anterior: el día es el de UTC.
        self.assertEqual(live_account_series.day_key(ordered[0][0]), "2026-10-20")


class RowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ordered = live_account_series.movements([
            deal("2026-09-30T23:00:00", 7.0),
            deal("2026-10-20T08:00:00", 10.0),
            deal("2026-10-20T09:00:00", -4.0),
            deal("2026-10-20T10:00:00", 1.0),
            deal("2026-10-21T08:00:00", -2.0),
        ])

    def test_the_day_adds_its_closes_and_measures_its_own_valley(self) -> None:
        rows = live_account_series.daily_rows(self.ordered)
        self.assertAlmostEqual(rows["2026-10-20"]["profit"], 7.0)
        # Sube a 10, baja a 6: la caída del día son 4, no la del mes.
        self.assertAlmostEqual(rows["2026-10-20"]["drawdown"], 4.0)
        self.assertEqual(rows["2026-10-20"]["trades"], 3)

    def test_the_month_is_measured_over_the_whole_month(self) -> None:
        rows = live_account_series.monthly_rows(self.ordered)
        self.assertAlmostEqual(rows["2026-10"]["profit"], 5.0)
        # Curva del mes: 10, 6, 7, 5. El pico son 10 y el valle 5, así que la
        # caída del mes son 5, mayor que los 4 de su peor día.
        self.assertAlmostEqual(rows["2026-10"]["drawdown"], 5.0)
        self.assertEqual(rows["2026-10"]["trades"], 4)
        self.assertAlmostEqual(rows["2026-09"]["profit"], 7.0)

    def test_a_day_without_trades_is_a_measured_zero(self) -> None:
        rows = live_account_series.daily_rows(self.ordered)
        self.assertEqual(
            live_account_series.row(rows, "2026-10-19"),
            {"profit": 0.0, "drawdown": 0.0, "trades": 0},
        )

    def test_the_row_never_hands_out_the_stored_dictionary(self) -> None:
        rows = live_account_series.daily_rows(self.ordered)
        live_account_series.row(rows, "2026-10-20")["profit"] = 999.0
        self.assertAlmostEqual(rows["2026-10-20"]["profit"], 7.0)


class LabelTests(unittest.TestCase):
    def test_the_previous_month_crosses_the_year(self) -> None:
        self.assertEqual(live_account_series.previous_month("2026-01"), "2025-12")
        self.assertEqual(live_account_series.previous_month("2026-10"), "2026-09")

    def test_the_month_number_is_the_seasonal_key(self) -> None:
        self.assertEqual(live_account_series.month_number("2026-10"), 10)


if __name__ == "__main__":
    unittest.main()
