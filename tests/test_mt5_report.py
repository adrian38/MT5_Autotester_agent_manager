from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.mt5_report import RawDeal, _build_trades, _parse_order_stops


def raw_deal(
    ticket: str, moment: datetime, trade_type: str, direction: str, price: float,
    *, order: str | None = None, profit: float = 0.0, comment: str = "",
) -> RawDeal:
    return RawDeal(
        timestamp=moment,
        ticket=ticket,
        symbol="XAUUSD",
        trade_type=trade_type,
        direction=direction,
        volume=0.03,
        price=price,
        order=order or ticket,
        commission=0.0,
        swap=0.0,
        profit=profit,
        balance=5000.0,
        comment=comment,
    )


class MT5ReportParserTests(unittest.TestCase):
    def test_out_of_order_html_deals_do_not_cross_two_closures(self) -> None:
        early_open = raw_deal("6", datetime(2026, 9, 1, 11, 9, 50), "sell", "in", 4399.18)
        early_close = raw_deal("7", datetime(2026, 9, 1, 11, 12, 58), "buy", "out", 4399.21)
        late_open = raw_deal("8", datetime(2026, 9, 1, 16, 12, 4), "sell", "in", 4327.25)
        late_close = raw_deal("9", datetime(2026, 9, 1, 16, 13, 0), "buy", "out", 4331.56)

        trades = _build_trades([late_open, early_open, early_close, late_close])

        self.assertEqual([(trade.ticket, trade.open_time, trade.close_time) for trade in trades], [
            ("6", early_open.timestamp, early_close.timestamp),
            ("8", late_open.timestamp, late_close.timestamp),
        ])
        self.assertTrue(all(trade.close_time >= trade.open_time for trade in trades))

    def test_stop_comment_links_overlapping_positions_to_the_correct_entry(self) -> None:
        first_open = raw_deal(
            "2", datetime(2026, 9, 1, 21, 27, 18), "sell", "in", 77010.70, order="5",
        )
        second_open = raw_deal(
            "3", datetime(2026, 9, 1, 21, 28, 1), "sell", "in", 76883.75, order="3",
        )
        stop_close = raw_deal(
            "4", datetime(2026, 9, 1, 21, 29, 48), "buy", "out", 77007.05,
            order="6", profit=-17.03, comment="sl 77006.22",
        )
        target_close = raw_deal(
            "5", datetime(2026, 9, 1, 21, 45, 34), "buy", "out", 76465.51,
            order="7", profit=75.28, comment="tp 76466.87",
        )

        trades = _build_trades(
            [first_open, second_open, stop_close, target_close],
            {"5": {"sl": 77133.96, "tp": 76466.87}, "3": {"sl": 77006.22, "tp": 76342.37}},
        )

        self.assertEqual([(trade.ticket, trade.close_time) for trade in trades], [
            ("2", target_close.timestamp),
            ("3", stop_close.timestamp),
        ])
        self.assertEqual([trade.profit_loss for trade in trades], [75.28, -17.03])

    def test_spanish_order_table_exposes_stop_levels(self) -> None:
        rows = [
            ["Órdenes"],
            ["Hora de apertura", "Orden", "Símbolo", "Tipo", "Volumen", "Precio", "S / L", "T / P", "Fecha/Hora", "Estado", "Comentario"],
            ["2026.09.01 07:00:00", "5", "BTCUSD", "sell stop", "0.16 / 0.16", "77016.24", "77133.96", "76466.87", "2026.09.01 21:27:18", "filled", "EA"],
            ["Transacciones"],
        ]

        self.assertEqual(_parse_order_stops(rows), {"5": {"sl": 77133.96, "tp": 76466.87}})


if __name__ == "__main__":
    unittest.main()
