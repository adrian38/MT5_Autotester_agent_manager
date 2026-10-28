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


class FakeSettings:
    """Lo mínimo que el inventario le pide al estado del nodo."""

    def __init__(self, profiles: dict[str, object]) -> None:
        self._state = {
            "profiles": profiles,
            "credential_state": {
                audit_id: {"source_password_saved": True} for audit_id in profiles
            },
        }

    def state(self, _node_id: str) -> dict[str, object]:
        return self._state


def profile(portfolio_id: int = 173) -> dict[str, object]:
    return {
        "portfolio_id": portfolio_id, "portfolio_type": "balanced",
        "deployment_name": "Profalgo_Robo", "live_trading": True,
        "source_login": "77028839", "source_server": "RoboForex-ECN",
    }


class FakeServer:
    def __init__(self, root: Path, profiles: dict[str, object] | None = None) -> None:
        self.live_accounts = LiveAccountArchive(root / "live_accounts")
        self.live_audit_settings = FakeSettings(
            {"audit-173": profile()} if profiles is None else profiles,
        )


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


class TrackedPortfolioTests(unittest.TestCase):
    """Desde cuándo las barras son del portafolio que la cuenta sirve hoy.

    El tramo **no** se corta por esa fecha: medido el 2026-10-24, seis de los
    nueve usos en real se guardaron a mediados de octubre sobre cuentas que
    llevaban meses operando, así que cortar dejaría seis gráficos de una
    barra. Se marca, que es lo que permite distinguirlas sin perderlas.
    """

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name))
        self.server.live_accounts.merge(
            "roboforex", KEY, login="77028839", server="RoboForex-ECN",
            currency="EUR", movements=[
                (moment("2026-08-10"), 50.0), (moment("2026-09-20"), 100.0),
                (moment("2026-10-05"), -20.0),
            ],
            period_start="", period_end="2026-10-23",
        )

    def created(self, day: str) -> None:
        self.server.live_accounts.write_expectation(
            "roboforex", 173,
            {
                "portfolio_created_at": f"{day}T02:52:14",
                "portfolio_name": "Mejora #100",
                "portfolio_valley_limit": 90.0,
                "portfolio_historical_valley": 70.0,
                "months": {
                    "09": {"profit": 80.0, "drawdown": 20.0, "years": [2024, 2025]},
                    "10": {"profit": 30.0, "drawdown": 10.0, "years": [2024, 2025]},
                },
            },
        )

    def history(self) -> dict:
        return live_account_monthly.monthly_history(
            self.server, "roboforex", KEY, NOW,
        )

    def test_the_whole_life_of_the_account_is_still_there(self) -> None:
        self.created("2026-09-15")
        months = self.history()["months"]
        self.assertEqual([month["label"] for month in months][0], "2026-08")
        self.assertEqual(len(months), 3)

    def test_each_month_says_whether_it_is_of_this_portfolio(self) -> None:
        self.created("2026-09-15")
        marks = {
            month["label"]: (month["tracked"], month["partial"])
            for month in self.history()["months"]
        }
        self.assertEqual(marks["2026-08"], (False, False))
        # El mes de entrada va a medias: parte de sus cierres son de antes.
        self.assertEqual(marks["2026-09"], (False, True))
        self.assertEqual(marks["2026-10"], (True, False))

    def test_each_full_month_of_the_span_says_if_it_met_its_average(self) -> None:
        self.created("2026-08-15")
        by_label = {month["label"]: month for month in self.history()["months"]}

        september = by_label["2026-09"]
        self.assertEqual(september["average_profit"], 80.0)
        self.assertEqual(september["average_target"], 80.0)
        self.assertEqual(september["average_drawdown"], 20.0)
        self.assertEqual(september["average_years"], [2024, 2025])
        self.assertIs(september["meets_average"], True)
        self.assertIs(september["meets_drawdown"], True)

        october = by_label["2026-10"]
        self.assertEqual(october["average_profit"], 30.0)
        self.assertLess(october["average_target"], october["average_profit"])
        self.assertIs(october["meets_average"], False)
        self.assertIs(october["meets_drawdown"], False)

    def test_the_span_drawdown_is_compared_with_both_portfolio_valleys(self) -> None:
        self.created("2026-08-15")
        totals = self.history()["totals"]
        self.assertEqual(totals["portfolio_valley_limit"], 90.0)
        self.assertEqual(totals["portfolio_historical_valley"], 70.0)
        self.assertIs(totals["within_valley_limit"], True)
        self.assertIs(totals["within_historical_valley"], True)

    def test_autolot_uses_the_historical_valley_recalculated_at_that_months_lots(self) -> None:
        self.created("2026-08-15")
        stored = self.server.live_accounts.read_expectation("roboforex", 173)
        stored["months_at"] = {"2026-10": {
            "profit": 30.0, "drawdown": 10.0, "years": [2024, 2025],
            "portfolio_historical_valley": 140.0,
            "basis": "autolot_mean_balance", "lots": {"ROBO:1": 0.08},
        }}
        self.server.live_accounts.write_expectation("roboforex", 173, stored)
        totals = self.history()["totals"]
        self.assertEqual(totals["portfolio_historical_valley"], 140.0)
        self.assertEqual(totals["portfolio_valley_basis"], "autolot_mean_balance")

    def test_the_entry_month_compares_only_the_slice_since_the_portfolio_entered(self) -> None:
        self.created("2026-09-15")
        september = next(
            month for month in self.history()["months"] if month["label"] == "2026-09"
        )
        self.assertEqual(september["average_profit"], 80.0)
        self.assertAlmostEqual(september["average_target"], 80.0 * 16 / 30)
        self.assertEqual(september["comparison_profit"], 100.0)
        self.assertEqual(september["comparison_drawdown"], 0.0)
        self.assertEqual(september["comparison_trades"], 1)
        self.assertIs(september["meets_average"], True)
        self.assertIs(september["meets_drawdown"], True)

    def test_the_payload_names_the_portfolio_and_the_day(self) -> None:
        self.created("2026-09-15")
        payload = self.history()
        self.assertEqual(payload["tracked_since"], "2026-09-15")
        self.assertEqual(
            payload["portfolios"],
            [{
                "portfolio_id": 173, "name": "Mejora #100",
                "created_day": "2026-09-15", "created_month": "2026-09",
            }],
        )

    def test_the_summary_only_measures_the_active_portfolio(self) -> None:
        """Las barras antiguas siguen visibles, pero no alimentan las tarjetas."""
        self.server.live_accounts.merge(
            "roboforex", KEY, login="77028839", server="RoboForex-ECN",
            currency="EUR", movements=[
                (moment("2026-08-10"), 50.0),
                (moment("2026-09-10"), 400.0),
                (moment("2026-09-14"), -100.0),
                (moment("2026-09-20"), 25.0),
                (moment("2026-10-05"), -20.0),
            ],
            period_start="", period_end="2026-10-23",
        )
        self.created("2026-09-15")

        payload = self.history()
        self.assertEqual(len(payload["months"]), 3)
        self.assertEqual(payload["totals"]["months"], 2)
        self.assertEqual(payload["totals"]["trades"], 2)
        self.assertAlmostEqual(payload["totals"]["profit"], 5.0)
        self.assertAlmostEqual(payload["totals"]["drawdown"], 20.0)
        self.assertEqual(payload["totals"]["positive_months"], 1)
        self.assertEqual(payload["totals"]["best_month"], "2026-09")

    def test_without_a_measured_promise_nothing_is_marked(self) -> None:
        """Sin fecha no se marca: ni se inventa el corte ni se niega el tramo.

        `tracked=False` lo lee el gráfico como «anterior a este portafolio»:
        atenúa la barra y añade la leyenda. Con la fecha todavía sin escribir
        —las nueve cuentas reales el 2026-10-24, porque la promesa guardada es
        de antes de que la ingesta la escribiera— eso pintaba la serie entera
        como anterior a su propio portafolio.
        """
        payload = self.history()
        self.assertEqual(payload["tracked_since"], "")
        self.assertEqual(payload["portfolios"], [])
        self.assertFalse(any(month["partial"] for month in payload["months"]))
        self.assertEqual(
            [month["tracked"] for month in payload["months"]], [True, True, True],
        )

    def test_with_two_uses_the_oldest_one_opens_the_tramo(self) -> None:
        self.server.live_audit_settings = FakeSettings({
            "audit-173": profile(), "audit-183": profile(183),
        })
        self.created("2026-09-15")
        self.server.live_accounts.write_expectation(
            "roboforex", 183, {"portfolio_created_at": "2026-10-19T00:00:00"},
        )
        payload = self.history()
        self.assertEqual(payload["tracked_since"], "2026-09-15")
        self.assertEqual(
            [row["portfolio_id"] for row in payload["portfolios"]], [173, 183],
        )


if __name__ == "__main__":
    unittest.main()
