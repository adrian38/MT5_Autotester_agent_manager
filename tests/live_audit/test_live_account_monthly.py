"""El gráfico mes a mes de una cuenta real: tramo continuo y drawdown del tramo.

Lo que se comprueba aquí es lo que la pantalla **no** puede recalcular: que los
meses sin cierres existan como ceros, que el drawdown del tramo se mida sobre
la curva y no sobre las filas mensuales, y que un mes que no operó no se
anuncie como el peor.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_account_monthly, manager_account_routes
from mt5_manager.live_account_archive import LiveAccountArchive, account_key

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
KEY = account_key("77028839", "RoboForex-ECN")


def moment(day: str, hour: int = 10) -> datetime:
    year, month, number = (int(part) for part in day.split("-"))
    return datetime(year, month, number, hour, tzinfo=timezone.utc)


class FakeServer:
    def __init__(self, root: Path) -> None:
        self.live_accounts = LiveAccountArchive(root / "live_accounts")


class MonthSpanTests(unittest.TestCase):
    def test_the_span_rolls_the_year_and_keeps_every_month(self) -> None:
        self.assertEqual(live_account_monthly.next_month("2025-12"), "2026-01")
        self.assertEqual(
            live_account_monthly.month_span("2025-11", "2026-02"),
            ["2025-11", "2025-12", "2026-01", "2026-02"],
        )

    def test_without_a_first_month_there_is_no_span(self) -> None:
        self.assertEqual(live_account_monthly.month_span("", "2026-02"), [])

    def test_a_corrupt_instant_cannot_make_the_span_endless(self) -> None:
        span = live_account_monthly.month_span("1970-01", "9999-12")
        self.assertEqual(len(span), live_account_monthly.MAXIMUM_MONTHS)


class MonthlyHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name))

    def store(self, movements: list[tuple[datetime, float]]) -> None:
        self.server.live_accounts.merge(
            "roboforex", KEY, login="77028839", server="RoboForex-ECN",
            currency="EUR", movements=movements, period_start="",
            period_end="2026-10-23", taken_at="2026-10-23T11:00:00+00:00",
        )

    def history(self) -> dict[str, object]:
        return live_account_monthly.monthly_history(self.server, "roboforex", KEY, NOW)

    def test_a_month_without_closes_is_a_zero_and_not_a_hole(self) -> None:
        """Ocho meses parados no pueden salir como dos barras seguidas."""
        self.store([(moment("2025-11-03"), 180.0), (moment("2026-08-04"), 20.0)])
        months = self.history()["months"]
        self.assertEqual(
            [row["label"] for row in months],
            ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
             "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"],
        )
        parked = months[1]
        self.assertEqual(parked["label"], "2025-12")
        self.assertEqual((parked["profit"], parked["drawdown"], parked["trades"]), (0.0, 0.0, 0))

    def test_the_span_reaches_this_month_even_if_nothing_closed_in_it(self) -> None:
        self.store([(moment("2026-08-04"), 20.0)])
        months = self.history()["months"]
        self.assertEqual(months[-1]["label"], "2026-10")
        self.assertEqual(months[-1]["trades"], 0)

    def test_the_drawdown_of_the_span_is_not_the_worst_month(self) -> None:
        """Una caída puede atravesar el cambio de mes.

        Medido contra los datos reales el 2026-10-23: la cuenta 60082017 de AXI
        lleva 267,83 € de caída y su peor mes son 169,78 €. Aquí el mismo caso
        en pequeño: +100 y −80 en septiembre, −50 en octubre. Por meses el
        máximo son 80; la curva cae de 100 a −30, que son 130.
        """
        self.store([
            (moment("2026-09-10"), 100.0), (moment("2026-09-20"), -80.0),
            (moment("2026-10-05"), -50.0),
        ])
        totals = self.history()["totals"]
        self.assertAlmostEqual(totals["worst_month_drawdown"], 80.0)
        self.assertAlmostEqual(totals["drawdown"], 130.0)
        self.assertAlmostEqual(totals["profit"], -30.0)

    def test_a_month_that_never_operated_is_not_the_worst_month(self) -> None:
        """Un cero no compite con un resultado.

        Es la cuenta 10010194 de AXI: cuatro meses con operaciones, ocho
        parada y ninguno en rojo. El mínimo por importe caía en un mes que no
        existió como operación.
        """
        self.store([(moment("2025-11-03"), 180.0), (moment("2026-08-04"), 20.0)])
        totals = self.history()["totals"]
        self.assertEqual(totals["best_month"], "2025-11")
        self.assertEqual(totals["worst_month"], "2026-08")
        self.assertEqual(totals["months"], 12)
        self.assertEqual(totals["traded_months"], 2)
        self.assertEqual(totals["negative_months"], 0)
        self.assertEqual(totals["positive_months"], 2)

    def test_the_payload_names_the_account_it_is_talking_about(self) -> None:
        self.store([(moment("2026-09-10"), 100.0)])
        data = self.history()
        self.assertEqual(data["login"], "77028839")
        self.assertEqual(data["server"], "RoboForex-ECN")
        self.assertEqual(data["currency"], "EUR")
        self.assertEqual(data["first_day"], "2026-09-10")
        self.assertEqual(data["month"], "2026-10")
        self.assertEqual(data["account_key"], KEY)

    def test_an_account_without_a_series_says_so_instead_of_inventing_months(self) -> None:
        data = live_account_monthly.monthly_history(self.server, "roboforex", KEY, NOW)
        self.assertEqual(data["months"], [])
        self.assertEqual(data["totals"]["months"], 0)
        self.assertEqual(data["totals"]["best_month"], "")
        self.assertAlmostEqual(data["totals"]["drawdown"], 0.0)


class FakeHandler:
    """Lo que la ruta necesita del handler: el nodo, el archivo y responder."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.sent: list[tuple[int, object]] = []

    @staticmethod
    def _node(node_id: str) -> dict[str, object]:
        if node_id != "roboforex":
            raise KeyError(f"Nodo desconocido: {node_id}")
        return {"id": node_id, "name": "RoboForex ECN"}

    def _send_json(self, status: int, payload: object) -> None:
        self.sent.append((status, payload))


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name))
        self.server.live_accounts.merge(
            "roboforex", KEY, login="77028839", server="RoboForex-ECN",
            currency="EUR", movements=[(moment("2026-09-10"), 100.0)],
            period_start="", period_end="2026-10-23",
        )

    def _get(self, node: str, key: str) -> FakeHandler:
        handler = FakeHandler(self.server)
        self.assertTrue(manager_account_routes.handle_get(
            handler, type("Parsed", (), {"path": "", "query": ""})(),
            ["api", "live-accounts", node, key, "monthly"],
        ))
        return handler

    def test_the_route_serves_the_series_of_one_account(self) -> None:
        status, payload = self._get("roboforex", "77028839%40RoboForex-ECN").sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["account_key"], KEY)
        self.assertTrue(payload["months"])

    def test_an_unknown_node_is_a_404_and_not_an_empty_chart(self) -> None:
        status, payload = self._get("otro", "77028839%40RoboForex-ECN").sent[0]
        self.assertEqual(status, 404)
        self.assertIn("otro", payload["error"])


if __name__ == "__main__":
    unittest.main()
