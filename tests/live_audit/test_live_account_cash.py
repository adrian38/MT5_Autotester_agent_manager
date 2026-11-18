"""Los apuntes de saldo de una cuenta real: ingresos, retiradas y abonos.

La tabla de transacciones de MT5 trae dos cosas mezcladas, y hasta la v6 el
manager sólo guardaba una. `movements` tira los apuntes a propósito —un
depósito de mil euros no es el mejor día de la historia de la cuenta— y eso
está bien para medir cómo opera. Para medir el **patrimonio** no: el saldo sube
igual ganando que ingresando, así que sin esta serie las dos cosas no se
distinguen.

Medido el 2026-11-15 sobre los informes reales del equipo: la cuenta 77028839
lleva 249 apuntes —13 ingresos por 5.337,89 €, 8 retiradas por 479,50 € y 228
rebates del bróker que suman 86,45 €— y 60090762 doce, con dos depósitos
externos (`DEP PSCC …`), una retirada (`WDL PAY2 …`) y seis traspasos a otras
cuentas del propio usuario. Por eso el concepto se guarda entero: es lo único
que los distingue, y cada bróker lo escribe a su manera.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from portfolio_manager.mt5_report import RawDeal
from mt5_manager.live_account import live_account_report
from mt5_manager.live_account.live_account_archive import (
    LiveAccountArchive, stored_cash,
)
from mt5_manager.live_account.live_account_series import cash_movements

HEADERS = [
    "Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price",
    "Order", "Commission", "Swap", "Profit", "Balance", "Comment",
]


def deal(stamp: Any, kind: str, profit: float, comment: str = "") -> RawDeal:
    moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
    return RawDeal(
        timestamp=moment, ticket="1", symbol="BTCUSD", trade_type=kind,
        direction="out", volume=0.02, price=1.0, order="1", commission=0.0,
        swap=0.0, profit=profit, balance=1_000.0, comment=comment,
    )


def report_html(rows: list[list[str]]) -> bytes:
    """El HTML nativo de MT5, con la tabla de transacciones que se le pase."""
    cell = lambda values: "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
    return (
        "<html><body>" + cell(["Deals"]) + cell(HEADERS)
        + "".join(cell(row) for row in rows) + "</body></html>"
    ).encode()


class CashMovementTests(unittest.TestCase):
    def test_an_mt4_statement_feeds_daily_and_wealth_series(self) -> None:
        """MT4 trae órdenes cerradas, no la tabla de deals de MT5."""
        def row(values: list[str]) -> str:
            return "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"

        headers = [
            "Ticket", "Open Time", "Type", "Size", "Item", "Price", "S/L", "T/P",
            "Close Time", "Price", "Commission", "Taxes", "Swap", "Profit",
        ]
        statement = ("<html><body>" + row(["Account: 7 Currency: USD"])
            + row(["Closed Transactions:"]) + row(headers)
            + row(["1", "2026.01.01 09:00", "balance", "", "Initial deposit", "", "", "", "", "", "0", "0", "0", "1000"])
            + row(["2", "2026.01.02 09:00", "buy", "0.10", "EURUSD", "1.1", "0", "0", "2026.01.02 10:00", "1.2", "-1", "0", "-0.5", "10"])
            + row(["Summary:"]) + row(["Balance:", "1008.50", "Equity:", "1008.50"])
            + "</body></html>").encode()

        net, profit, balances, cash = live_account_report.account_from_html(statement)

        self.assertEqual(net[0][1], 8.5)
        self.assertEqual(profit[0][1], 10.0)
        self.assertEqual(balances[-1], ("2026-01-02", 1008.5))
        self.assertEqual(cash[0][1:], (1000.0, "balance", "Initial deposit"))

    def test_an_mt4_apunte_is_five_cells_wide_and_not_fourteen(self) -> None:
        """Lo que pasó el 2026-11-18 con la MT4 3083784 de Axi.

        La prueba de arriba inventa el apunte con las catorce columnas del
        cierre, y así pasaba mientras el extracto de verdad perdía los catorce
        suyos: MT4 imprime el apunte con cinco celdas —ticket, instante, tipo,
        concepto e importe— y el filtro de ancho los tiraba todos. La misma
        sección trae además órdenes pendientes canceladas, de once celdas, que
        no son nada que archivar: el ancho es lo único que las separa.
        """
        def row(values: list[str]) -> str:
            return "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"

        statement = ("<html><body>" + row(["Account: 7 Currency: USD"])
            + row(["Closed Transactions:"])
            + row(["Ticket", "Open Time", "Type", "Size", "Item", "Price", "S/L",
                   "T/P", "Close Time", "Price", "Commission", "Taxes", "Swap", "Profit"])
            + row(["96854958", "2025.09.02 10:33:03", "balance",
                   "DEP PSCC BK2EEAA AXFSUK USD", "600.00"])
            + row(["2", "2025.09.03 09:00", "buy", "0.10", "EURUSD", "1.1", "0", "0",
                   "2025.09.03 10:00", "1.2", "-1", "0", "-0.5", "10"])
            + row(["105238789", "2026.10.02 17:00:01", "buy stop", "0.01", "euraud",
                   "1.64541", "1.60441", "1.69341", "2026.10.22 17:00:01", "1.59609",
                   "cancelled"])
            + row(["Summary:"]) + row(["Balance:", "608.50", "Equity:", "608.50"])
            + "</body></html>").encode()

        net, _profit, balances, cash = live_account_report.account_from_html(statement)

        self.assertEqual(
            cash, [(
                datetime(2025, 9, 2, 10, 33, 3, tzinfo=timezone.utc),
                600.0, "balance", "DEP PSCC BK2EEAA AXFSUK USD",
            )],
        )
        # Sin el apunte, el saldo reconstruido hacia atrás daba 608,50 el
        # primer día: la cuenta dibujada con dinero que no había ingresado.
        self.assertEqual(balances, [("2025-09-02", 600.0), ("2025-09-03", 608.5)])
        self.assertEqual(len(net), 1)

    def test_a_balance_row_keeps_its_amount_type_and_concept(self) -> None:
        found = cash_movements([
            deal("2026-09-23T13:19:31", "balance", -915.0, "WDL PAY2 BO3S7BA"),
            deal("2026-08-31T02:09:29", "balance", 4_634.47, "Transfer from US15-Live"),
            deal("2026-09-01T10:00:00", "buy", 12.5, ""),
        ])
        self.assertEqual(
            found,
            [
                (
                    datetime(2026, 8, 31, 2, 9, 29, tzinfo=timezone.utc),
                    4_634.47, "balance", "Transfer from US15-Live",
                ),
                (
                    datetime(2026, 9, 23, 13, 19, 31, tzinfo=timezone.utc),
                    -915.0, "balance", "WDL PAY2 BO3S7BA",
                ),
            ],
        )

    def test_a_rebate_is_an_apunte_like_any_other(self) -> None:
        """216 de los 249 apuntes de 77028839 son rebates de céntimos.

        No se filtran aquí: clasificar por concepto es cosa de la pantalla, y
        esta capa no puede decidir por un bróker que todavía no se ha visto.
        Lo que sí hace es no perderlos, porque el saldo los incluye y el
        cuadre del patrimonio sin ellos no cierra.
        """
        found = cash_movements([
            deal("2026-01-01T05:10:50", "credit", 0.26, "IR #126 VIP"),
            deal("2025-11-05T09:31:14", "balance", 0.21, "IB rebate (ltdn) #4699"),
        ])
        self.assertEqual([row[1] for row in found], [0.21, 0.26])
        self.assertEqual([row[2] for row in found], ["balance", "credit"])

    def test_a_row_without_an_instant_does_not_take_the_series_down(self) -> None:
        found = cash_movements([
            deal(None, "balance", 100.0, "Deposit"),
            deal("2026-10-21T08:00:00", "balance", 200.0, "Deposit"),
        ])
        self.assertEqual([row[1] for row in found], [200.0])

    def test_the_concept_of_a_naive_instant_is_read_as_utc(self) -> None:
        naive = cash_movements([deal("2026-10-31T23:30:00", "balance", 10.0)])
        aware = cash_movements([
            deal(datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc), "balance", 10.0),
        ])
        self.assertEqual(naive, aware)


class ReportReadsBothHalvesTest(unittest.TestCase):
    def test_the_cash_amount_is_not_rescaled_to_the_presentation_lot(self) -> None:
        """Un ingreso es dinero del bróker; un cierre se puede presentar a otro lote.

        Es la misma razón por la que el saldo diario tampoco se reescala: con
        el factor aplicado, el «aportado» de la pantalla sería un importe que
        nunca salió de ninguna cuenta.
        """
        body = report_html([
            ["2026.09.22 09:00:00", "1", "", "balance", "", "0", "0", "", "0",
             "0", "1000", "1000", "DEP PSCC BNRVJAA"],
            ["2026.09.22 10:00:00", "2", "BTCUSD", "buy", "out", "0.02", "1.1",
             "1", "0", "0", "50", "1050", ""],
        ])
        _net, profits, balances, cash = live_account_report.account_from_html(
            body, {"BTCUSD": 0.04},
        )
        self.assertEqual([row[1] for row in cash], [1_000.0])
        self.assertEqual([row[3] for row in cash], ["DEP PSCC BNRVJAA"])
        # El cierre sí se dobla, porque 0.04 es el doble de lo ejecutado.
        self.assertEqual([round(amount, 2) for _moment, amount in profits], [100.0])
        self.assertEqual(balances, [("2026-09-22", 1_050.0)])

    def test_an_account_that_never_was_fully_reread_says_it_does_not_know(self) -> None:
        """Lo contrario sería enseñar «0 € aportados» a una cuenta con depósitos.

        La versión sólo se reescribe cuando el sondeo relee la vida entera, y
        los apuntes están repartidos por toda ella: con una ventana de tres
        días no hay nada que sumar.
        """
        self.assertFalse(live_account_report.cash_is_complete({}))
        self.assertFalse(live_account_report.cash_is_complete(
            {"normalization_version": "balance-series-v5-profit-column"},
        ))
        self.assertTrue(live_account_report.cash_is_complete(
            {"normalization_version": "balance-series-v6-cash-movements"},
        ))
        self.assertTrue(live_account_report.cash_is_complete(
            {"normalization_version": live_account_report.SERIES_VERSION},
        ))
        self.assertTrue(live_account_report.cash_is_complete({
            "normalization_version": live_account_report.series_version({"BTCUSD": 0.01}),
        }))


class ArchivedCashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = LiveAccountArchive(Path(self.temporary.name))

    def merge(self, cash: list[tuple], start: str, end: str) -> dict[str, Any]:
        return self.archive.merge(
            "axi", "60090762@Axi-US50-Live", login="60090762",
            server="Axi-US50-Live", currency="USD",
            movements=[(datetime(2026, 10, 2, 9, tzinfo=timezone.utc), 1.0)],
            period_start=start, period_end=end, cash=cash,
        )

    def test_the_apuntes_travel_to_the_record_with_their_concept(self) -> None:
        record = self.merge(
            [(datetime(2026, 8, 4, 21, 46, tzinfo=timezone.utc), 1_000.0,
              "balance", "DEP WPCC BDMEXBA")],
            "", "2026-10-02",
        )
        self.assertEqual(record["cash"], [
            ["2026-08-04T21:46:00+00:00", 1_000.0, "balance", "DEP WPCC BDMEXBA"],
        ])

    def test_the_polled_window_replaces_its_own_apuntes_and_keeps_the_rest(self) -> None:
        self.merge(
            [
                (datetime(2026, 8, 4, 21, 46, tzinfo=timezone.utc), 1_000.0, "balance", "DEP"),
                (datetime(2026, 10, 1, 3, 23, tzinfo=timezone.utc), -1_133.0, "balance", "viejo"),
            ],
            "", "2026-10-02",
        )
        record = self.merge(
            [(datetime(2026, 10, 1, 3, 23, tzinfo=timezone.utc), -1_133.0, "balance", "nuevo")],
            "2026-09-30", "2026-10-02",
        )
        self.assertEqual(
            [(row[0][:10], row[3]) for row in record["cash"]],
            [("2026-08-04", "DEP"), ("2026-10-01", "nuevo")],
        )

    def test_a_window_without_apuntes_does_not_prune_the_ones_already_stored(self) -> None:
        """La mayoría de las ventanas no traen ninguno: son doce al año.

        Podar con eso se llevaría los ingresos ciertos de los días que la
        ventana roza, que es el mismo fallo que ya costó la serie entera de
        dos cuentas con los cierres.
        """
        self.merge(
            [(datetime(2026, 10, 1, 3, 23, tzinfo=timezone.utc), -1_133.0, "balance", "DEP")],
            "", "2026-10-02",
        )
        record = self.merge([], "2026-09-30", "2026-10-05")
        self.assertEqual(len(record["cash"]), 1)

    def test_a_corrupt_row_does_not_take_the_series_down(self) -> None:
        self.assertEqual(
            stored_cash({"cash": [
                ["2026-10-01T00:00:00+00:00", "no es un número", "balance", ""],
                ["2026-10-02T00:00:00+00:00", 5.0],
                "ni esto",
            ]}),
            [["2026-10-02T00:00:00+00:00", 5.0, "", ""]],
        )


if __name__ == "__main__":
    unittest.main()
