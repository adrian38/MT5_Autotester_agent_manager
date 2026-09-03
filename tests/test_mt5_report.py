from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.mt5_report import RawDeal, _build_trades


def raw_deal(ticket: str, moment: datetime, trade_type: str, direction: str, price: float) -> RawDeal:
    return RawDeal(
        timestamp=moment,
        ticket=ticket,
        symbol="XAUUSD",
        trade_type=trade_type,
        direction=direction,
        volume=0.03,
        price=price,
        order=ticket,
        commission=0.0,
        swap=0.0,
        profit=0.0,
        balance=5000.0,
        comment="",
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


if __name__ == "__main__":
    unittest.main()
