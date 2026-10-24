"""La tarjeta de cada cuenta real en la portada: qué dice y con qué se compara.

Salió de `test_live_account_cron.py`, que se pasaba de las 600 líneas. Allí
queda el cron —alcance, recogida, reintentos y campana— y aquí lo que la
tarjeta decide: el día sin comparar, el mes contra su media del backtest, el
mes anterior y la conversión a una sola moneda.

Las piezas de mentira son las del cron y se importan de allí: son el mismo
servidor y el mismo estado de nodo, y duplicarlos dejaría dos fixtures que
podrían discrepar.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_account_dashboard
from mt5_manager.live_account_archive import account_key
from tests.live_audit.test_live_account_cron import NOW, FakeServer, node_state


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name), node_state())
        self.key = account_key("60082017", "Axi-US50-Live")
        self.server.live_accounts.merge(
            "axi", self.key, login="60082017", server="Axi-US50-Live", currency="EUR",
            movements=[
                (datetime(2026, 9, 15, 8, tzinfo=timezone.utc), 120.0),
                (datetime(2026, 10, 20, 8, tzinfo=timezone.utc), 40.0),
                (datetime(2026, 10, 21, 9, tzinfo=timezone.utc), 30.0),
                (datetime(2026, 10, 21, 12, tzinfo=timezone.utc), -10.0),
            ],
            period_start="", period_end="2026-10-21", balance=1200.0,
            taken_at="2026-10-21T21:10:00+00:00",
        )
        self.server.live_accounts.write_expectation("axi", 105, {
            "signature": "abc",
            "months": {
                "10": {"profit": 600.0, "drawdown": 150.0, "years": [2020, 2021, 2022]},
                "09": {"profit": 300.0, "drawdown": 100.0, "years": [2020, 2021]},
            },
        })

    def payload(self) -> dict[str, object]:
        return live_account_dashboard.snapshot(self.server, NOW)

    def card(self) -> dict[str, object]:
        return next(
            row for row in self.payload()["accounts"] if row["login"] == "60082017"
        )

    def test_the_daily_row_is_the_day_and_carries_no_comparison(self) -> None:
        card = self.card()
        self.assertEqual(card["daily"]["day"], "2026-10-21")
        self.assertAlmostEqual(card["daily"]["profit"], 20.0)
        self.assertAlmostEqual(card["daily"]["drawdown"], 10.0)
        self.assertNotIn("profit_tone", card["daily"])

    def test_the_monthly_row_is_compared_with_that_month_in_the_backtest(self) -> None:
        card = self.card()
        self.assertAlmostEqual(card["monthly"]["profit"], 60.0)
        self.assertAlmostEqual(card["expected_month"]["profit"], 600.0)
        self.assertEqual(card["expected_month"]["years"], [2020, 2021, 2022])
        # El día 21 de 31: la media prorrateada son 406,45 y 60 va por debajo.
        self.assertAlmostEqual(card["expected_month"]["profit_prorated"], 600.0 * 21 / 31)
        self.assertEqual(card["monthly"]["profit_tone"], "bad")

    def test_a_drawdown_under_the_mean_is_good_news(self) -> None:
        card = self.card()
        self.assertAlmostEqual(card["monthly"]["drawdown"], 10.0)
        self.assertEqual(card["monthly"]["drawdown_tone"], "good")

    def test_the_previous_month_is_shown_and_compared(self) -> None:
        card = self.card()
        self.assertEqual(card["previous_month"]["label"], "2026-09")
        self.assertAlmostEqual(card["previous_month"]["profit"], 120.0)
        # 60 este mes contra 120 el pasado: peor, y la diferencia a la vista.
        self.assertEqual(card["previous_month"]["profit_tone"], "bad")
        self.assertAlmostEqual(card["previous_month"]["profit_delta"], -60.0)
        self.assertAlmostEqual(card["expected_previous"]["profit"], 300.0)

    def test_the_comparison_with_the_previous_month_is_about_this_one(self) -> None:
        """El tono habla de **este** mes, no del anterior.

        Pintado sobre el importe de septiembre, un verde se leia como
        «septiembre fue bueno» cuando lo que decia era que octubre va mejor.
        La pantalla lo enseña como nota; aqui se fija que el numero que lo
        decide es la diferencia entre los dos meses.
        """
        card = self.card()
        self.assertAlmostEqual(
            card["previous_month"]["profit_delta"],
            card["monthly"]["profit"] - card["previous_month"]["profit"],
        )
        self.assertAlmostEqual(
            card["previous_month"]["drawdown_delta"],
            card["monthly"]["drawdown"] - card["previous_month"]["drawdown"],
        )

    def test_without_a_stored_mean_there_is_no_colour(self) -> None:
        self.server.live_accounts.write_expectation("axi", 105, {"months": {}})
        card = self.card()
        self.assertIsNone(card["expected_month"]["profit"])
        self.assertEqual(card["monthly"]["profit_tone"], "")

    def test_the_totals_convert_to_one_currency_and_say_what_they_cannot(self) -> None:
        self.server.live_accounts.merge(
            "axi", account_key("60082303", "Axi-US50-Live"), login="60082303",
            server="Axi-US50-Live", currency="GBP",
            movements=[(datetime(2026, 10, 21, 9, tzinfo=timezone.utc), 50.0)],
            period_start="", period_end="2026-10-21",
        )
        totals = self.payload()["totals"]
        self.assertAlmostEqual(totals["daily"], 20.0)
        self.assertAlmostEqual(totals["previous_month"], 120.0)
        self.assertEqual(totals["currency"], "EUR")
        self.assertEqual(totals["missing_rates"], ["GBP"])

    def test_the_payload_carries_the_selection_shared_between_browsers(self) -> None:
        selection_key = f"axi::{self.key}"
        self.server.live_accounts.update_total_selection({
            "account": selection_key, "included": False,
        })
        self.assertEqual(self.payload()["total_selection"], {
            "initialized": True, "excluded_accounts": [selection_key],
        })

    def test_a_dollar_account_is_converted_with_its_stored_rate(self) -> None:
        self.server.live_accounts.merge(
            "axi", account_key("60082303", "Axi-US50-Live"), login="60082303",
            server="Axi-US50-Live", currency="USD",
            movements=[
                (datetime(2026, 9, 21, 9, tzinfo=timezone.utc), 100.0),
                (datetime(2026, 10, 21, 9, tzinfo=timezone.utc), 100.0),
            ],
            period_start="", period_end="2026-10-21",
        )
        totals = self.payload()["totals"]
        self.assertAlmostEqual(totals["daily"], 20.0 + 92.0)
        self.assertAlmostEqual(totals["previous_month"], 120.0 + 92.0)
        self.assertEqual(totals["missing_rates"], [])

    def test_an_account_never_read_says_so_instead_of_showing_zeros(self) -> None:
        card = next(
            row for row in self.payload()["accounts"] if row["login"] == "60082303"
        )
        self.assertEqual(card["state"], "never_read")
        self.assertIn("Sondear ahora", card["message"])

    def test_a_series_older_than_two_days_is_marked_stale(self) -> None:
        self.server.live_accounts.merge(
            "axi", self.key, login="60082017", server="Axi-US50-Live", currency="EUR",
            movements=[], period_start="2026-10-21", period_end="2026-10-21",
            taken_at="2026-10-18T21:10:00+00:00",
        )
        self.assertEqual(self.card()["state"], "stale")

    def test_the_card_names_the_portfolios_the_account_serves(self) -> None:
        card = self.card()
        self.assertEqual(
            [row["name"] for row in card["portfolios"]], ["Port_107_AXI"],
        )
        self.assertAlmostEqual(card["balance"], 1200.0)


if __name__ == "__main__":
    unittest.main()
