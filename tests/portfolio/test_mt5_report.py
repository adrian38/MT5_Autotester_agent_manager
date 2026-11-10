from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.mt5_report import (
    RawDeal, _build_trades, _parse_order_stops, _parse_raw_deals,
)


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

    def test_an_open_position_is_not_a_closed_deal(self) -> None:
        """El informe de una cuenta real no termina en «Transacciones».

        Detrás vienen «Posiciones abiertas» y «Órdenes de trabajo», y el
        parser no salía de la sección: sus filas se emparejaban con la cabecera
        de las transacciones y cada columna caía una casilla corrida. El
        comentario `usdjpy_p67ag` aterrizaba en Swap, `_to_float` le sacaba un
        67 y una posición **abierta** entraba en la serie de la cuenta como
        66,70 € cerrados. Filas reales del informe de `audit-67-muzhv8jq-1` de
        AXI, 2026-10-24.
        """
        rows = [
            ["Transacciones"],
            ["Fecha/Hora", "Transacción", "Símbolo", "Tipo", "Dirección", "Volumen",
             "Precio", "Orden", "Coste", "Comisión", "Tasa", "Swap", "Beneficio",
             "Balance", "Comentario"],
            ["2026.10.15 16:54:43", "96262605", "USDJPY", "sell", "in", "0.01",
             "157.579", "126258833", "", "0.00", "0.00", "0.00", "0.00",
             "5 371.33", "usdjpy_p67ag"],
            ["Posiciones abiertas"],
            ["Fecha/Hora", "Posición", "Símbolo", "Tipo", "Volumen", "Precio",
             "S / L", "T / P", "Precio de mercado", "Swap", "Beneficio", "Comentario"],
            ["2026.10.15 16:54:43", "126258833", "USDJPY", "sell", "0.01", "157.579",
             "159.479", "157.079", "158.181", "-0.30", "-3.39", "usdjpy_p67ag"],
        ]

        deals = _parse_raw_deals(rows)

        self.assertEqual([deal.ticket for deal in deals], ["96262605"])
        self.assertEqual(deals[0].direction, "in")
        self.assertAlmostEqual(deals[0].volume, 0.01)
        self.assertAlmostEqual(deals[0].net_profit, 0.0)

    def test_a_deals_row_that_does_not_fill_its_header_is_not_a_deal(self) -> None:
        # El segundo candado: una tabla de otra sección con tantas columnas
        # como ésta tampoco se cuela. El corte por título la aparta antes, pero
        # el reparto por `zip` es silencioso y no puede ser lo único que mire.
        rows = [
            ["Transacciones"],
            ["Fecha/Hora", "Transacción", "Símbolo", "Tipo", "Dirección", "Volumen",
             "Precio", "Orden", "Coste", "Comisión", "Tasa", "Swap", "Beneficio",
             "Balance", "Comentario"],
            ["2026.10.15 16:54:43", "96262605", "USDJPY", "sell", "out", "0.01",
             "157.579", "126258833", "", "0.00", "0.00", "0.00", "1.50",
             "5 371.33", "usdjpy_p67ag"],
            ["2026.10.16 10:00:00", "96262606", "USDJPY", "sell", "0.01", "157.579",
             "159.479", "157.079", "158.181", "-0.30", "-3.39", "usdjpy_p67ag"],
        ]

        deals = _parse_raw_deals(rows)

        self.assertEqual([deal.ticket for deal in deals], ["96262605"])
        self.assertAlmostEqual(deals[0].net_profit, 1.5)


if __name__ == "__main__":
    unittest.main()
