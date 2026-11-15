"""El patrimonio: lo que hay, lo que se puso y lo que puso operar.

La portada de cuentas contesta «cómo va hoy» y lo hace con la columna Profit
de MT5. Esta pantalla contesta otra cosa —de todo el dinero en real, cuánto es
mío y cuánto lo ha hecho operar— y por eso mide con el **neto**, que es lo que
mueve el saldo, y lee el saldo que publica el bróker en vez de acumular
cierres.

Medido el 2026-11-15 sobre el archivo real del equipo: 40.086,65 € en quince
cuentas, 1.311,01 € de resultado acumulado en catorce meses y 37.775,64 € de
variación que el archivo todavía no podía explicar, porque ninguna cuenta
tenía los apuntes de saldo. Ésa es la medida que decidió el criterio de aquí:
mientras falte una cuenta por releer, los flujos se dicen «no se saben» en vez
de enseñar un cero, y el cuadre no se publica.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import live_account_inventory, live_account_wealth
from mt5_manager.live_account.live_account_archive import LiveAccountArchive
from mt5_manager.live_account.live_account_report import SERIES_VERSION

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
NODE = {"id": "axi", "name": "AXI Standard", "portfolio_broker": "AXI"}
KEY = "60090762@Axi-US50-Live"
SETTINGS = {
    "display_currency": "EUR", "exchange_rates": {"USD": 0.5},
    "rates_updated_at": "2026-11-01",
}


def stamp(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class WealthBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = LiveAccountArchive(Path(self.temporary.name))
        self.accounts = [{
            "account_key": KEY, "login": "60090762", "server": "Axi-US50-Live",
            "portfolios": [{"portfolio_id": 105, "name": "Port_105", "autolot": False}],
            "audit_ids": ["1"], "autolot": False, "block_reason": "",
        }]
        self.server = SimpleNamespace(
            nodes=[NODE], live_accounts=self.archive,
            live_account_scheduler_settings=dict(SETTINGS),
        )

    def store(
        self, movements: list[tuple[datetime, float]],
        balances: list[tuple[str, float]],
        cash: list[tuple[datetime, float, str, str]] | None = None,
        currency: str = "EUR", version: str | None = SERIES_VERSION,
    ) -> None:
        self.archive.merge(
            "axi", KEY, login="60090762", server="Axi-US50-Live", currency=currency,
            movements=movements, profits=movements, balances=balances, cash=cash,
            period_start="", period_end="2026-11-15",
            balance=balances[-1][1] if balances else 0.0,
            equity=balances[-1][1] if balances else 0.0,
            normalization_version=version,
        )

    def snapshot(self) -> dict[str, Any]:
        with mock.patch.object(
            live_account_inventory, "node_accounts",
            return_value=self.accounts,
        ):
            return live_account_wealth.snapshot(self.server, NOW)

    def month(self, payload: dict[str, Any], label: str) -> dict[str, Any]:
        found = [row for row in payload["months"] if row["label"] == label]
        self.assertEqual(len(found), 1, f"falta el mes {label}")
        return found[0]


class WealthMonthsTests(WealthBase):
    def test_the_months_run_without_gaps_to_the_current_one(self) -> None:
        """Un mes sin nada es un cero medido, no una etiqueta que falta.

        Es el mismo criterio del gráfico mes a mes de una cuenta, y por eso se
        reutiliza su `month_span`: sin las etiquetas intermedias, septiembre y
        noviembre saldrían pegados como si fueran meses seguidos.
        """
        self.store(
            [(stamp("2026-09-10T09:00:00"), 50.0)],
            [("2026-09-10", 1_050.0), ("2026-11-14", 1_050.0)],
        )
        payload = self.snapshot()
        self.assertEqual(
            [month["label"] for month in payload["months"]],
            ["2026-09", "2026-10", "2026-11"],
        )
        self.assertEqual(self.month(payload, "2026-10")["profit"], 0.0)

    def test_a_month_before_the_account_existed_does_not_count_it(self) -> None:
        """Arrastrar el saldo hacia atrás haría antiguo un patrimonio nuevo."""
        self.accounts.append({
            "account_key": "77028839@RoboForex-ECN", "login": "77028839",
            "server": "RoboForex-ECN", "portfolios": [], "audit_ids": ["2"],
            "autolot": False, "block_reason": "",
        })
        self.store(
            [(stamp("2026-11-02T09:00:00"), 10.0)], [("2026-11-02", 1_010.0)],
        )
        self.archive.merge(
            "axi", "77028839@RoboForex-ECN", login="77028839",
            server="RoboForex-ECN", currency="EUR",
            movements=[(stamp("2026-09-02T09:00:00"), 20.0)],
            profits=[(stamp("2026-09-02T09:00:00"), 20.0)],
            balances=[("2026-09-02", 500.0)], period_start="", period_end="2026-11-15",
            balance=500.0, equity=500.0, normalization_version=SERIES_VERSION,
        )
        payload = self.snapshot()
        self.assertEqual(self.month(payload, "2026-09")["accounts"], 1)
        self.assertEqual(self.month(payload, "2026-09")["balance"], 500.0)
        self.assertEqual(self.month(payload, "2026-11")["accounts"], 2)
        self.assertEqual(self.month(payload, "2026-11")["balance"], 1_510.0)

    def test_the_current_month_uses_the_live_balance_and_not_the_last_day(self) -> None:
        """El saldo de hoy es el de la última lectura, no el del último cierre."""
        self.store(
            [(stamp("2026-11-02T09:00:00"), 10.0)], [("2026-11-02", 1_010.0)],
        )
        self.archive.merge(
            "axi", KEY, login="60090762", server="Axi-US50-Live", currency="EUR",
            movements=[], profits=[], period_start="2026-11-14",
            period_end="2026-11-15", balance=1_234.0, equity=1_234.0,
        )
        self.assertEqual(self.month(self.snapshot(), "2026-11")["balance"], 1_234.0)

    def test_amounts_are_converted_to_the_display_currency(self) -> None:
        self.store(
            [(stamp("2026-11-02T09:00:00"), 100.0)], [("2026-11-02", 2_000.0)],
            currency="USD",
        )
        month = self.month(self.snapshot(), "2026-11")
        self.assertEqual(month["profit"], 50.0)
        self.assertEqual(month["balance"], 1_000.0)

    def test_a_currency_without_a_rate_is_declared_instead_of_added(self) -> None:
        """Sumar dólares a euros daría un patrimonio falso sin decirlo."""
        self.store(
            [(stamp("2026-11-02T09:00:00"), 100.0)], [("2026-11-02", 2_000.0)],
            currency="JPY",
        )
        payload = self.snapshot()
        self.assertEqual(payload["warnings"]["missing_rates"], ["JPY"])
        self.assertEqual(self.month(payload, "2026-11")["profit"], 0.0)


class WealthCashTests(WealthBase):
    def test_an_account_without_the_apuntes_says_it_does_not_know(self) -> None:
        """37.775,64 € de depósitos ciertos no pueden salir como «0 ingresos»."""
        self.store(
            [(stamp("2026-11-02T09:00:00"), 10.0)], [("2026-11-02", 1_010.0)],
            version="balance-series-v5-profit-column",
        )
        payload = self.snapshot()
        self.assertFalse(payload["totals"]["cash_known"])
        self.assertEqual(payload["totals"]["cash_accounts"], 0)
        self.assertEqual(payload["warnings"]["cash_pending"], [KEY])
        month = self.month(payload, "2026-11")
        self.assertIsNone(month["cash"]["deposit"])
        self.assertIsNone(month["contributed"])
        self.assertIsNone(month["unexplained"])
        self.assertIsNone(payload["totals"]["return_on_contributed"])

    def test_each_apunte_goes_to_its_class_by_what_the_broker_wrote(self) -> None:
        self.store(
            [(stamp("2026-09-10T09:00:00"), 100.0)],
            [("2026-08-31", 1_000.0), ("2026-09-30", 1_200.0)],
            cash=[
                (stamp("2026-08-04T21:46:00"), 1_000.0, "balance", "DEP WPCC BDMEXBA"),
                (stamp("2026-09-23T13:19:00"), -915.0, "balance", "WDL PAY2 BO3S7BA"),
                (stamp("2026-09-14T04:03:00"), 779.38, "balance", "Transfer from us50-live"),
                (stamp("2026-09-01T03:00:00"), -100.0, "balance", "Transfer to 77028840"),
                (stamp("2026-09-05T09:31:00"), 0.21, "balance", "IB rebate (ltdn) #4699"),
            ],
        )
        payload = self.snapshot()
        august, september = self.month(payload, "2026-08"), self.month(payload, "2026-09")
        self.assertEqual(august["cash"]["deposit"], 1_000.0)
        self.assertEqual(september["cash"]["withdrawal"], -915.0)
        self.assertEqual(september["cash"]["transfer_in"], 779.38)
        self.assertEqual(september["cash"]["transfer_out"], -100.0)
        self.assertEqual(september["cash"]["rebate"], 0.21)
        # Lo aportado deja fuera el abono del bróker: no lo puso el usuario.
        self.assertAlmostEqual(september["contributed"], -235.62)
        self.assertAlmostEqual(payload["totals"]["deposits"], 1_000.0)
        self.assertAlmostEqual(payload["totals"]["withdrawals"], -915.0)
        self.assertAlmostEqual(payload["totals"]["rebates"], 0.21)

    def test_what_resultado_and_flujos_do_not_explain_is_published(self) -> None:
        """El único control que esta pantalla puede hacer de sí misma.

        Septiembre sube 200 €, de los que 100 € son resultado y 50 € un
        ingreso: los 50 € que faltan son un apunte que no está, y la pantalla
        lo dice en vez de repartirlo.
        """
        self.store(
            [(stamp("2026-09-10T09:00:00"), 100.0)],
            [("2026-08-31", 1_000.0), ("2026-09-30", 1_200.0)],
            cash=[(stamp("2026-09-05T09:00:00"), 50.0, "balance", "Deposit")],
        )
        payload = self.snapshot()
        september = self.month(payload, "2026-09")
        self.assertEqual(september["variation"], 200.0)
        self.assertAlmostEqual(september["unexplained"], 50.0)
        self.assertAlmostEqual(payload["totals"]["worst_unexplained"], 50.0)

    def test_a_month_that_adds_up_leaves_no_descuadre(self) -> None:
        self.store(
            [(stamp("2026-09-10T09:00:00"), 100.0)],
            [("2026-08-31", 1_000.0), ("2026-09-30", 1_150.0)],
            cash=[(stamp("2026-09-05T09:00:00"), 50.0, "balance", "Deposit")],
        )
        payload = self.snapshot()
        self.assertAlmostEqual(self.month(payload, "2026-09")["unexplained"], 0.0)
        self.assertTrue(payload["totals"]["cash_known"])
        self.assertAlmostEqual(payload["totals"]["contributed"], 50.0)
        self.assertAlmostEqual(payload["totals"]["return_on_contributed"], 200.0)

    def test_the_detail_leaves_out_the_cents_but_not_an_unknown_concept(self) -> None:
        """216 de los 249 apuntes de 77028839 son rebates de céntimos.

        En la lista no caben y en el resumen sí; lo que no se puede tirar es
        una fila de importe grande cuyo concepto no se sepa clasificar.
        """
        self.store(
            [], [("2026-09-30", 1_000.0)],
            cash=[
                (stamp("2026-09-05T09:31:00"), 0.21, "balance", "IB rebate (ltdn)"),
                (stamp("2026-09-06T09:31:00"), 400.0, "balance", "ZZZ 998 QQ"),
            ],
        )
        detail = self.snapshot()["contributors"][0]["cash_detail"]
        self.assertEqual([row["amount"] for row in detail], [400.0])
        self.assertEqual(detail[0]["kind"], "deposit")


class WealthContributorTests(WealthBase):
    def test_each_account_brings_its_month_by_month_and_its_total(self) -> None:
        """Lo que el usuario pidió ver: qué suma y qué resta cada mes."""
        self.store(
            [
                (stamp("2026-09-10T09:00:00"), 100.0),
                (stamp("2026-10-10T09:00:00"), -40.0),
            ],
            [("2026-09-30", 1_100.0), ("2026-10-31", 1_060.0)],
        )
        row = self.snapshot()["contributors"][0]
        self.assertEqual(row["login"], "60090762")
        self.assertEqual(row["monthly"], [100.0, -40.0, 0.0])
        self.assertEqual(row["profit"], 60.0)
        self.assertEqual(row["positive_months"], 1)
        self.assertEqual(row["negative_months"], 1)
        self.assertEqual(
            [portfolio["portfolio_id"] for portfolio in row["portfolios"]], [105],
        )

    def test_the_patrimonio_drawdown_is_not_the_trading_one(self) -> None:
        """Una retirada baja el patrimonio y no es una pérdida.

        Se publican las dos cifras porque confundirlas es leer un traspaso
        como un mal mes.
        """
        self.store(
            [
                (stamp("2026-09-10T09:00:00"), 100.0),
                (stamp("2026-10-10T09:00:00"), -50.0),
            ],
            [("2026-09-30", 1_100.0), ("2026-10-31", 650.0)],
            cash=[(stamp("2026-10-20T09:00:00"), -400.0, "balance", "WDL PAY2")],
        )
        payload = self.snapshot()
        self.assertAlmostEqual(payload["totals"]["drawdown"]["amount"], 450.0)
        self.assertAlmostEqual(payload["totals"]["drawdown"]["peak"], 1_100.0)
        self.assertAlmostEqual(payload["contributors"][0]["trade_drawdown"], 50.0)

    def test_a_blocked_account_is_named_instead_of_disappearing(self) -> None:
        """Una cuenta real que no se puede leer tiene que verse igual."""
        self.accounts[0]["block_reason"] = "Falta la contraseña de la cuenta real."
        payload = self.snapshot()
        self.assertEqual(payload["warnings"]["blocked"], [KEY])
        self.assertEqual(payload["totals"]["accounts"], 1)
        self.assertEqual(payload["months"], [])


if __name__ == "__main__":
    unittest.main()
