"""El análisis de una comprobación manual: OHLC M1 contra tick a tick.

Vive aparte de `test_portfolio_manual_check.py`, que ya roza las 600 líneas.
Aquí entra lo que la pantalla nueva necesita —las cinco cifras de los dos
informes, la curva de cada uno y la ruta que las sirve— y la marca de cada
tick en los sets, que es lo que hace que las dos corridas sean comparables.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import manager_handler, manager_portfolio_routes, portfolio_manual_check
from mt5_manager.manual_check_analysis import METRIC_KEYS, analysis


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "mt5_manager" / "static"


def _report(net: str, drawdown: str, trades: str) -> str:
    """Un .htm con la tabla de resultados que MT5 escribe y dos operaciones."""
    results = (
        f"<tr><td>Beneficio Neto:</td><td>{net}</td>"
        f"<td>Beneficio Bruto:</td><td>500.00</td></tr>"
        f"<tr><td>Reducción máxima del balance:</td><td>{drawdown}</td>"
        f"<td>Total de operaciones ejecutadas:</td><td>{trades}</td></tr>"
        "<tr><td>Total de transacciones:</td><td>4</td><td>Barras:</td><td>10</td></tr>"
    )
    headers = [
        "Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price",
        "Order", "Commission", "Swap", "Profit", "Balance", "Comment",
    ]
    rows = [
        ["2026.01.05 10:00:00", "1", "XAUUSD", "buy", "in", "0.01", "2000.00",
         "1", "0", "0", "0", "1000", ""],
        ["2026.01.06 10:00:00", "2", "XAUUSD", "sell", "out", "0.01", "2010.00",
         "2", "0", "0", net, "1000", ""],
    ]
    cells = lambda values: "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
    return (
        "<html><body><table>"
        "<tr><td>Expert:</td><td>EA</td></tr>"
        "<tr><td>Symbol:</td><td>XAUUSD</td></tr>"
        "<tr><td>Period:</td><td>H1 2026.01.01 - 2026.02.01</td></tr>"
        + cells(["Resultados"]) + results + cells(["Deals"]) + cells(headers)
        + "".join(cells(row) for row in rows)
        + "</table></body></html>"
    )


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.check_id = "check_20261021_120000_000001"
        self.work = self.project / "runtime" / "nodo" / "manual_checks" / self.check_id
        (self.work / "reports" / "ohlc").mkdir(parents=True)
        (self.work / "reports" / "tick").mkdir(parents=True)
        (self.work / "check.json").write_text(json.dumps({
            "portfolio_id": 62, "scope": "full_history",
            "from_date": "2026.01.01", "to_date": "2026.02.01", "lot": 0.01,
            "models": ["ohlc", "tick"],
            "strategies": [{
                "set_name": "tres.set", "symbol": "XAUUSD", "timeframe": "H1",
                "candidate_id": "IC:3", "report_stem": "check_001_tres",
            }],
        }), encoding="utf-8")
        (self.work / "reports" / "ohlc" / "check_001_tres.htm").write_text(
            _report("322.61", "39.51 (3.71%)", "83"), encoding="utf-8",
        )
        (self.work / "reports" / "tick" / "check_001_tres.htm").write_text(
            _report("346.81", "39.73 (3.73%)", "81"), encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_the_two_reports_of_a_strategy_come_back_together(self) -> None:
        data = analysis(self.project, self.check_id)

        self.assertEqual(data["check_id"], self.check_id)
        self.assertEqual(data["portfolio_id"], 62)
        strategy = data["strategies"][0]
        self.assertTrue(strategy["comparable"])
        self.assertEqual(strategy["models"]["ohlc"]["metrics"]["Beneficio Neto"], "322.61")
        self.assertEqual(strategy["models"]["tick"]["metrics"]["Beneficio Neto"], "346.81")
        self.assertEqual(
            strategy["models"]["ohlc"]["metrics"]["Total de operaciones ejecutadas"], "83",
        )

    def test_the_five_figures_are_the_ones_the_report_writes(self) -> None:
        # Se leen por su literal del .htm: traducirlos aquí haría que la
        # pantalla y el informe dejaran de decir lo mismo.
        self.assertEqual(METRIC_KEYS, (
            "Total de operaciones ejecutadas",
            "Total de transacciones",
            "Beneficio Neto",
            "Beneficio Bruto",
            "Reducción máxima del balance",
        ))
        data = analysis(self.project, self.check_id)
        self.assertEqual(list(data["metric_keys"]), list(METRIC_KEYS))
        self.assertEqual(
            sorted(data["strategies"][0]["models"]["ohlc"]["metrics"]), sorted(METRIC_KEYS),
        )

    def test_the_curve_is_the_closed_pnl_accumulated(self) -> None:
        curve = analysis(self.project, self.check_id)["strategies"][0]["models"]["ohlc"]["curve"]

        self.assertEqual([point["value"] for point in curve], [322.61])
        self.assertTrue(curve[0]["time"].startswith("2026-01-06"))

    def test_a_missing_data_model_is_declared_instead_of_filled_with_zeros(self) -> None:
        # Se pudo lanzar un solo tipo de datos, o el tester no dejó informe de
        # esa corrida: un cero diría que la estrategia no ganó nada.
        (self.work / "reports" / "tick" / "check_001_tres.htm").unlink()

        strategy = analysis(self.project, self.check_id)["strategies"][0]

        self.assertFalse(strategy["comparable"])
        self.assertFalse(strategy["models"]["tick"]["available"])
        self.assertEqual(strategy["models"]["tick"]["metrics"], {})
        self.assertEqual(strategy["models"]["tick"]["curve"], [])

    def test_an_invented_check_identifier_cannot_reach_another_folder(self) -> None:
        for check_id in ("../../etc", "no_existe", ""):
            with self.subTest(check_id=check_id):
                with self.assertRaises(ValueError):
                    analysis(self.project, check_id)


class RouteTests(unittest.TestCase):
    class Handler:
        def __init__(self, portfolios) -> None:
            self.server = SimpleNamespace(portfolios=portfolios)
            self.sent = None

        def _send_json(self, status: int, value) -> None:
            self.sent = (status, value)

    def test_the_screen_asks_the_manager_for_the_analysis_of_a_check(self) -> None:
        portfolios = SimpleNamespace(
            manual_check_analysis=lambda node_id, check_id: {"check": check_id, "node": node_id},
        )
        handler = self.Handler(portfolios)

        handled = manager_portfolio_routes.manual_check_action(
            handler, "manual-check-analysis", {}, "ic", "full_history",
            {"check_id": "check_20261021_120000_000001"},
        )

        self.assertTrue(handled)
        self.assertEqual(handler.sent, (200, {
            "check": "check_20261021_120000_000001", "node": "ic",
        }))


class EveryTickSetTests(unittest.TestCase):
    """Los sets de la comprobación manual salen con cada tick marcado.

    Sin esto las dos corridas pueden mirar lo mismo por dentro aunque el
    tester reciba modelos distintos, y la pantalla compararía dos informes que
    no se diferencian en lo que dice comparar.
    """

    def test_the_value_changes_and_the_rest_of_the_line_stays(self) -> None:
        marked = portfolio_manual_check.mark_every_tick(
            "Periodo=14||7||1||28||N\nUseEveryTick=false||false||0||true||N\n"
        )

        self.assertIn("UseEveryTick=true||false||0||true||N", marked)
        self.assertIn("Periodo=14||7||1||28||N", marked)

    def test_a_set_without_the_parameter_gets_it_added(self) -> None:
        marked = portfolio_manual_check.mark_every_tick("StartLots=0.01||0.01||0||0||N")

        self.assertIn("StartLots=0.01||0.01||0||0||N", marked)
        self.assertIn("UseEveryTick=true", marked)

    def test_a_utf16_set_survives_the_round_trip(self) -> None:
        # MT5 escribe los .set en UTF-16: leerlos como UTF-8 los convierte en
        # texto con NUL y el experto no reconoce ni un parámetro.
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "uno.set"
            path.write_text("UseEveryTick=false||false||0||true||N\n", encoding="utf-16")

            text, encoding = portfolio_manual_check.read_set_text(path)

            self.assertEqual(encoding, "utf-16")
            self.assertIn("UseEveryTick=true", portfolio_manual_check.mark_every_tick(text))

    def test_the_exported_zip_carries_the_same_set_that_mt5_will_run(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            (project / "outputs").mkdir()
            (project / "outputs" / "tres.set").write_text(
                "StartLots=0.01||0.01||0||0||N\nUseEveryTick=false||false||0||true||N\n",
                encoding="utf-8",
            )
            source = SimpleNamespace(
                project=project,
                saved_portfolio_detail=lambda portfolio_id, scope: {"portfolio": {
                    "id": 62, "members": [{
                        "candidate_id": "IC:3", "set_name": "tres.set",
                        "set_path": "/data/ic/outputs/tres.set",
                        "symbol": "XAUUSD", "timeframe": "H1", "units": 1,
                    }],
                }},
            )
            with patch.object(
                portfolio_manual_check, "_resolve_source_path",
                side_effect=lambda raw, project: project / "outputs" / Path(str(raw)).name,
            ):
                result = portfolio_manual_check.sets_archive(source, 62, "full_history")

        archive = zipfile.ZipFile(io.BytesIO(result["content"]))
        exported = archive.read("NUEVAS_62/tres.set").decode("utf-8")
        self.assertIn("UseEveryTick=true||false||0||true||N", exported)
        self.assertIn("StartLots=0.01||0.01||0||0||N", exported)


class ScreenTests(unittest.TestCase):
    def test_the_button_opens_the_analysis_in_its_own_tab(self) -> None:
        page = (STATIC / "portfolios.html").read_text(encoding="utf-8")
        script = (STATIC / "portfolio_manual_check.js").read_text(encoding="utf-8")

        self.assertIn('id="detail-manual-analysis"', page)
        # Arranca apagado: quien lo enciende es el agente, que es quien sabe
        # si queda alguna comprobación con informes.
        self.assertRegex(page, r'id="detail-manual-analysis"[^>]*disabled')
        self.assertIn("/manual_check_analysis.html?node=", script)
        self.assertIn("window.open(url, '_blank')", script)

    def test_the_page_and_its_pieces_are_served(self) -> None:
        for name in (
            "manual_check_analysis.html",
            "manual_check_analysis.js",
            "manual_check_analysis.css",
        ):
            with self.subTest(name=name):
                self.assertIn(name, manager_handler.STATIC_FILES)
                self.assertTrue((STATIC / name).is_file())

    def test_the_screen_shows_the_five_figures_and_lets_you_switch_strategy(self) -> None:
        script = (STATIC / "manual_check_analysis.js").read_text(encoding="utf-8")

        self.assertIn("analysis.metric_keys", script)
        self.assertIn("#strategy-list", script)
        self.assertIn("renderStrategy", script)
        # Subir no es mejorar en todas: en la reducción, más es peor.
        self.assertIn("BETTER_WHEN_DOWN", script)
        self.assertIn("Reducción máxima del balance", script)


if __name__ == "__main__":
    unittest.main()
