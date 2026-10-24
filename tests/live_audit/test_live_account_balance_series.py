"""El saldo diario que el informe nativo de la cuenta publica.

La tabla de transacciones de MT5 imprime el saldo acumulado tras cada
operación. De ahí sale con qué tamaño operaba la cuenta cada día, que es lo
que necesita la promesa de una cartera con autolote: allí el lote no está
declarado, lo decide el saldo.

Se queda **el último saldo de cada día** porque es lo que se pregunta —de qué
tamaño era la cuenta ese día— y uno por operación multiplicaría el fichero sin
añadir nada.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from portfolio_manager.mt5_report import RawDeal
from mt5_manager.live_account_report import daily_balances


def deal(stamp: Any, balance: float) -> RawDeal:
    moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
    return RawDeal(
        timestamp=moment, ticket="1", symbol="BTCUSD",
        trade_type="buy", direction="out", volume=0.01, price=1.0, order="1",
        commission=0.0, swap=0.0, profit=1.0, balance=balance, comment="",
    )


class DailyBalanceTests(unittest.TestCase):
    def test_each_day_keeps_the_balance_it_closed_with(self) -> None:
        found = daily_balances([
            deal("2026-10-20T08:00:00", 1_000.0),
            deal("2026-10-20T17:30:00", 1_025.0),
            deal("2026-10-21T09:00:00", 1_010.0),
        ])
        self.assertEqual(
            found, [("2026-10-20", 1_025.0), ("2026-10-21", 1_010.0)],
        )

    def test_the_rows_do_not_have_to_arrive_in_order(self) -> None:
        found = daily_balances([
            deal("2026-10-20T17:30:00", 1_025.0),
            deal("2026-10-20T08:00:00", 1_000.0),
        ])
        self.assertEqual(found, [("2026-10-20", 1_025.0)])

    def test_a_zero_balance_is_not_a_balance(self) -> None:
        """MT5 imprime cero en las filas que no mueven la cuenta.

        Es el mismo descarte que hace `live_audit_autolot` para el autolote, y
        por la misma razón: un cero ahí diría que la cuenta se quedó vacía.
        """
        found = daily_balances([
            deal("2026-10-20T08:00:00", 1_000.0),
            deal("2026-10-20T17:30:00", 0.0),
        ])
        self.assertEqual(found, [("2026-10-20", 1_000.0)])

    def test_a_row_without_an_instant_does_not_take_the_series_down(self) -> None:
        found = daily_balances([
            deal(None, 1_000.0), deal("2026-10-21T08:00:00", 1_010.0),
        ])
        self.assertEqual(found, [("2026-10-21", 1_010.0)])

    def test_the_day_is_cut_in_utc_like_everything_else(self) -> None:
        """El informe nativo imprime UTC sin sufijo; el JSON del nodo con él.

        Los dos tienen que caer en el mismo día o el saldo del último día del
        mes se iría a un mes y los cierres de ese día a otro.
        """
        naive = daily_balances([deal("2026-10-31T23:30:00", 1_000.0)])
        aware = daily_balances([
            deal(datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc), 1_000.0),
        ])
        self.assertEqual(naive, aware)
        self.assertEqual(naive, [("2026-10-31", 1_000.0)])


if __name__ == "__main__":
    unittest.main()
