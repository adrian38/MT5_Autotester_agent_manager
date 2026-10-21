"""El histórico OHLC guardado de un portafolio: por estrategia y combinado.

Lo que se comprueba aquí es lo que no se ve mirando la pantalla: que la fuente
son los dos informes OHLC del miembro y no la comprobación manual, que las
cifras van escaladas por las unidades, y que **las tres cifras cuadran entre
sí** —el total, la suma de las estrategias y la suma de la tabla mensual—,
que es justo lo que no pasaba al construir el mensual con el `monthly` del
informe.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import portfolio_history_detail
from mt5_manager.portfolio_history_detail import HISTORY_STAGES, history_detail


def _trade(day: str, profit: float) -> SimpleNamespace:
    return SimpleNamespace(close_time=datetime.fromisoformat(day), profit_loss=profit)


def _report(trades: list[SimpleNamespace], start: str, end: str, quality: str = "100%"):
    return SimpleNamespace(
        trades=trades, period_start=start, period_end=end,
        metrics={"Calidad del historial": quality},
        monthly={}, symbol="XAUUSD", timeframe="H1", path=Path("x.htm"),
    )


class HistoryDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "reports").mkdir()
        for name in ("base_uno", "oos_uno", "base_dos", "oos_dos"):
            (self.project / "reports" / f"{name}.htm").write_text("<html>", encoding="utf-8")
        self.members = [
            {
                "set_name": "uno.set", "set_path": "/data/ic/uno.set", "symbol": "XAUUSD",
                "timeframe": "H1", "units": 1, "lot": 0.01,
                "is_report_path": "/data/ic/reports/base_uno.htm",
                "oos_report_path": "/data/ic/reports/oos_uno.htm",
                "final_tick_report_path": "/data/ic/reports/tick_uno.htm",
            },
            {
                "set_name": "dos.set", "set_path": "/data/ic/dos.set", "symbol": "USDJPY",
                "timeframe": "M30", "units": 3, "lot": 0.03,
                "is_report_path": "/data/ic/reports/base_dos.htm",
                "oos_report_path": "/data/ic/reports/oos_dos.htm",
            },
        ]
        self.reports = {
            "base_uno.htm": _report([_trade("2020-03-10", 100.0), _trade("2020-04-10", -40.0)],
                                    "02.01.2020", "30.12.2024"),
            "oos_uno.htm": _report([_trade("2025-02-10", 50.0)], "02.01.2025", "29.05.2026", "98%"),
            "base_dos.htm": _report([_trade("2020-03-20", 10.0)], "08.01.2020", "30.12.2024"),
            "oos_dos.htm": _report([_trade("2025-02-20", -5.0)], "06.01.2025", "29.05.2026"),
        }
        self.source = SimpleNamespace(
            project=self.project,
            saved_portfolio_detail=lambda portfolio_id, scope: {
                "portfolio": {"id": 62, "alias": "Arreglo", "members": self.members},
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _detail(self) -> dict:
        def fake_cached(path: Path):
            return self.reports[Path(path).name]

        with patch.object(
            portfolio_history_detail, "_resolve_source_path",
            side_effect=lambda raw, project: project / "reports" / Path(str(raw)).name,
        ), patch.object(portfolio_history_detail, "cached_report", side_effect=fake_cached):
            return history_detail(self.source, 62, "full_history")

    def test_the_history_is_the_base_and_the_oos_report(self) -> None:
        # Y no el `final_tick`, que son seis meses con ticks reales, ni nada de
        # la comprobación manual.
        self.assertEqual(
            [key for key, _label in HISTORY_STAGES], ["is_report_path", "oos_report_path"],
        )
        detail = self._detail()
        uno = detail["strategies"][0]
        self.assertEqual(uno["trades"], 3)
        self.assertEqual(uno["period_start"], "02.01.2020")
        self.assertEqual(uno["period_end"], "29.05.2026")

    def test_the_figures_are_scaled_by_the_units(self) -> None:
        # Los informes se miden a lote 0,01 y el portafolio asigna unidades:
        # sin escalar, el total no sería el de esta cartera.
        detail = self._detail()
        uno, dos = detail["strategies"]
        self.assertEqual(uno["units"], 1)
        self.assertAlmostEqual(uno["net_profit"], 110.0)
        self.assertEqual(dos["units"], 3)
        self.assertAlmostEqual(dos["net_profit"], (10.0 - 5.0) * 3)

    def test_the_total_the_strategies_and_the_monthly_add_up_to_the_same(self) -> None:
        # Dos cifras que no cuadran en la misma pantalla no las cuadra nadie.
        detail = self._detail()
        combined = detail["portfolio"]
        monthly = sum(
            value for months in combined["monthly"].values() for value in months.values()
        )
        strategies = sum(row["net_profit"] for row in detail["strategies"])
        self.assertAlmostEqual(combined["net_profit"], 125.0)
        self.assertAlmostEqual(monthly, combined["net_profit"])
        self.assertAlmostEqual(strategies, combined["net_profit"])

    def test_the_deepest_valley_is_the_drawdown_of_the_whole_curve(self) -> None:
        detail = self._detail()
        combined = detail["portfolio"]
        valley = combined["valleys"][0]
        self.assertAlmostEqual(valley["depth"], combined["max_drawdown"])
        self.assertAlmostEqual(valley["depth"], 40.0)
        self.assertTrue(valley["trough_at"].startswith("2020-04-10"))
        # Y dice quién lo cavó: sólo la estrategia que perdió dentro.
        self.assertEqual([row["set_name"] for row in valley["strategies"]], ["uno.set"])
        self.assertAlmostEqual(valley["strategies"][0]["net_profit"], -40.0)

    def test_a_valley_that_never_recovers_says_so(self) -> None:
        self.reports["oos_uno.htm"] = _report(
            [_trade("2025-02-10", -500.0)], "02.01.2025", "29.05.2026",
        )
        valley = self._detail()["portfolio"]["valleys"][0]
        self.assertEqual(valley["recovered_at"], "")

    def test_a_missing_report_is_declared_instead_of_counted_as_zero(self) -> None:
        self.members[1]["oos_report_path"] = ""
        detail = self._detail()
        self.assertEqual(detail["strategies"][1]["missing"], ["Robustez OOS"])
        self.assertEqual(detail["incomplete"], ["dos.set"])

    def test_every_strategy_of_the_portfolio_is_here_not_only_the_new_ones(self) -> None:
        """Lo pidió el usuario: son todas, no las de la comprobación manual.

        «Nueva» es un concepto de la comprobación manual —la que no está en
        ningún antecesor— y ahí tiene sentido porque las heredadas ya tienen
        informe. Esta pantalla enseña justo esos informes, así que las
        heredadas son la mayor parte de lo que hay que ver: en el #62 de
        ICTrading son 16 de 16, mientras que nuevas sólo hay 3.
        """
        self.members.append({
            "set_name": "heredada.set", "set_path": "/data/ic/heredada.set",
            "symbol": "EURUSD", "timeframe": "H4", "units": 1, "lot": 0.01,
            "is_report_path": "/data/ic/reports/base_uno.htm",
            "oos_report_path": "/data/ic/reports/oos_uno.htm",
        })

        detail = self._detail()

        self.assertEqual(len(detail["strategies"]), len(self.members))
        self.assertIn("heredada.set", [row["set_name"] for row in detail["strategies"]])

    def test_an_excluded_member_is_not_part_of_the_history(self) -> None:
        self.members[1]["excluded"] = True
        detail = self._detail()
        self.assertEqual([row["set_name"] for row in detail["strategies"]], ["uno.set"])

    def test_the_curve_has_one_stop_per_day_with_trades(self) -> None:
        curve = self._detail()["portfolio"]["curve"]
        self.assertEqual([point["time"] for point in curve], [
            "2020-03-10", "2020-03-20", "2020-04-10", "2025-02-10", "2025-02-20",
        ])
        self.assertAlmostEqual(curve[-1]["value"], 125.0)


class DateOrderTests(unittest.TestCase):
    def test_the_mt5_dates_are_ordered_as_dates_and_not_as_text(self) -> None:
        # «02.01.2025» es menor que «08.01.2020» como cadena, y el periodo
        # salía al revés: «02.01.2025 → 30.12.2024».
        key = portfolio_history_detail._date_key
        self.assertLess(key("08.01.2020"), key("02.01.2025"))
        self.assertEqual(key(""), (0, 0, 0))
        self.assertEqual(key("no es fecha"), (0, 0, 0))


class ScreenTests(unittest.TestCase):
    STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"

    def test_the_two_buttons_open_their_own_tab(self) -> None:
        page = (self.STATIC / "portfolios.html").read_text(encoding="utf-8")
        script = (self.STATIC / "portfolios.js").read_text(encoding="utf-8")

        self.assertIn('id="detail-history-strategy"', page)
        self.assertIn('id="detail-history-portfolio"', page)
        self.assertIn("/portfolio_history.html?node=", script)
        self.assertIn("openHistoryDetail('strategy')", script)
        self.assertIn("openHistoryDetail('portfolio')", script)

    def test_the_page_shows_the_total_the_curve_and_the_valleys(self) -> None:
        script = (self.STATIC / "portfolio_history.js").read_text(encoding="utf-8")

        self.assertIn("Profit total", script)
        self.assertIn("PortfolioCurveView.renderCurve", script)
        self.assertIn("PortfolioCurveView.mountValleys", script)
        self.assertIn("history-detail", script)

    def test_the_valleys_are_one_table_with_a_selector(self) -> None:
        # Cinco tablas apiladas obligaban a bajar media pantalla para comparar
        # dos valles (petición del usuario, 2026-10-21).
        script = (self.STATIC / "portfolio_curve_view.js").read_text(encoding="utf-8")

        self.assertIn("data-valley", script)
        self.assertIn("portfolio-history-valley-tabs", script)
        self.assertIn("Number(button.dataset.valley)", script)

    def test_the_curve_and_the_valleys_are_one_primitive_for_both_screens(self) -> None:
        """Las dos pantallas dibujan lo mismo, así que lo dibuja el mismo código.

        Lo pidió el usuario para portafolios manuales (2026-10-21). Dos copias
        del mismo dibujo acaban enseñando dos cosas distintas, igual que dos
        copias de la misma cuenta acaban dando dos cifras.
        """
        shared = (self.STATIC / "portfolio_curve_view.js").read_text(encoding="utf-8")
        self.assertIn("window.PortfolioCurveView", shared)
        for page, script in (
            ("portfolio_history.html", "portfolio_history.js"),
            ("manual_portfolios.html", "manual_portfolios.js"),
        ):
            with self.subTest(page=page):
                markup = (self.STATIC / page).read_text(encoding="utf-8")
                self.assertIn("/portfolio_curve_view.js", markup)
                body = (self.STATIC / script).read_text(encoding="utf-8")
                self.assertIn("PortfolioCurveView.renderCurve", body)
                self.assertIn("PortfolioCurveView.mountValleys", body)
                # Y ninguna conserva su propia copia del dibujo.
                self.assertNotIn("<polyline", body)


if __name__ == "__main__":
    unittest.main()
