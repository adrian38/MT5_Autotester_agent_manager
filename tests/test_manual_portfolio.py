from __future__ import annotations

import unittest
import tempfile
import base64
import io
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import manager_portfolio_routes
from mt5_manager.manager_portfolio_routes import parse_timeframe_document
from mt5_manager.portfolio_saved import _saved_portfolio_row
from mt5_manager.portfolio_manual_reports import manual_history_summary
from mt5_manager.portfolio_coordinator_saved import PortfolioCoordinatorSavedMixin


ROOT = Path(__file__).parents[1]


class ManualPortfolioScreenTests(unittest.TestCase):
    def test_node_card_opens_the_dedicated_folder_screen(self) -> None:
        script = (ROOT / "mt5_manager" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Crear portafolio manual", script)
        self.assertIn("/portfolio_manual.html?node=", script)
        self.assertIn("/manual_portfolios.html?node=", script)

    def test_manual_inventory_is_separate_from_ubs(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        manual_script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        ubs_script = (ROOT / "mt5_manager" / "static" / "portfolios.js").read_text(encoding="utf-8")
        self.assertIn("Portafolios creados desde sets", page)
        self.assertIn("filter(row=>row.manual_portfolio)", manual_script)
        self.assertIn("filter(row => !row.manual_portfolio)", ubs_script)
        self.assertIn("/live_audit.html?node=", manual_script)

    def test_manual_detail_switches_between_strategy_and_combined_portfolio_tables(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn("Detalle / estrategia", page)
        self.assertIn("Detalle / portafolio", page)
        self.assertIn('id="manual-strategy-detail"', page)
        self.assertIn('id="manual-portfolio-detail"', page)
        self.assertIn("function combinedMonthly", script)
        self.assertIn("renderPortfolioDetail(history)", script)
        self.assertIn("showDetailView('portfolio')", script)

    def test_manual_detail_shows_loading_and_persistent_error_states(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn('id="manual-loading-spinner"', page)
        self.assertIn("function showDetailStatus", script)
        self.assertIn("Cargando detalle y tablas del portafolio", script)
        self.assertIn("No se pudo cargar el portafolio", script)

    def test_screen_uploads_a_directory_and_offers_audit_history(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "portfolio_manual.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "portfolio_manual.js").read_text(encoding="utf-8")
        self.assertIn("webkitdirectory", page)
        self.assertIn('id="manual-audit-link"', page)
        self.assertIn("portfolio-manager/manual-create", script)
        self.assertIn("/live_audit.html?node=", script)
        self.assertIn("&portfolio=", script)
        self.assertIn('id="manual-from-date"', page)
        self.assertIn('id="manual-to-date"', page)
        self.assertIn("Generar histórico OHLC", page)
        self.assertIn("portfolio-manager/manual-defaults", script)
        self.assertIn("portfolio-manager/manual-history", script)
        self.assertIn("decodeSet", script)
        self.assertIn('data-set-field="symbol"', script)
        self.assertIn('data-set-field="timeframe"', script)
        self.assertIn("updateSetField(text, 'TimeFrame'", script)
        self.assertIn("Lote origen:", script)
        self.assertIn("histórico OHLC: 0,01", script)
        self.assertIn("Risk, lote fijo 0,01, autolote, NFP y UseEveryTick", page)
        self.assertIn('id="manual-history-status"', page)
        self.assertIn("function monitorHistory", script)

    def test_screen_imports_timeframes_from_a_docx(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "portfolio_manual.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "portfolio_manual.js").read_text(encoding="utf-8")
        self.assertIn('id="manual-timeframes-button"', page)
        self.assertIn('accept=".docx"', page)
        self.assertIn("portfolio-manager/manual-timeframes", script)
        self.assertIn("applyImportedTimeframes()", script)
        self.assertIn("temporalidad importada", script)

    def test_docx_table_maps_report_names_to_set_names(self) -> None:
        xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>
        <w:tr><w:tc><w:p><w:r><w:t>N.º</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Nombre del archivo</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Temporalidad</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Gold_a.HTM</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>D1</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>M5_C.htm</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>M5</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl></w:body></w:document>'''
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as document:
            document.writestr("word/document.xml", xml)
        result = parse_timeframe_document(base64.b64encode(archive.getvalue()).decode())
        self.assertEqual(result, {"timeframes": {"gold_a": "D1", "m5_c": "M5"}, "count": 2})

    def test_saved_row_exposes_manual_origin_from_metrics(self) -> None:
        row = {
            "id": 7,
            "metrics_json": '{"inputs":{"manual_portfolio":true}}',
        }
        saved = _saved_portfolio_row(row, lambda item, key, default=None: item.get(key, default), "full_history")
        self.assertTrue(saved["manual_portfolio"])

    def test_saved_row_defaults_to_non_manual_for_old_or_invalid_metrics(self) -> None:
        value = lambda item, key, default=None: item.get(key, default)
        self.assertFalse(_saved_portfolio_row({"id": 1}, value, "full_history")["manual_portfolio"])
        self.assertFalse(_saved_portfolio_row({"id": 2, "metrics_json": "{"}, value, "full_history")["manual_portfolio"])

    def test_live_audit_preselects_the_new_portfolio_from_the_url(self) -> None:
        script = (ROOT / "mt5_manager" / "static" / "live_audit.js").read_text(encoding="utf-8")
        self.assertIn("function selectRequestedPortfolio()", script)
        self.assertIn("params.get('portfolio')", script)
        self.assertIn("selectRequestedPortfolio();", script)

    def test_manual_detail_reads_the_generated_history_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "reports").mkdir()
            (project / "reports" / "one.htm").write_text("report", encoding="utf-8")
            report = SimpleNamespace(
                path=project / "reports" / "one.htm", symbol="XAUUSD", timeframe="H1",
                period_start="01.01.2020", period_end="01.01.2026", initial_deposit=1000,
                metrics={"Calidad del historial": "99%"}, monthly={2026: {1: 25.0}},
                trades=[
                    SimpleNamespace(close_time=datetime(2026, 1, 1), profit_loss=100.0),
                    SimpleNamespace(close_time=datetime(2026, 1, 2), profit_loss=-25.0),
                ],
            )
            with patch("mt5_manager.portfolio_manual_reports.cached_report", return_value=report):
                history = manual_history_summary(project, [{"set_id": "one", "set_path": r"runtime\manual_portfolios\uuid\sets\one.set"}])
        self.assertEqual(history["available"], 1)
        self.assertEqual(history["net_profit"], 75.0)
        self.assertEqual(history["max_drawdown"], 25.0)
        self.assertEqual(history["strategies"][0]["history_quality"], "99%")
        self.assertEqual(history["strategies"][0]["set_name"], "one.set")

    def test_manual_detail_does_not_require_ubs_capital_or_dd_inputs(self) -> None:
        source = SimpleNamespace(
            saved_portfolio_detail=lambda _portfolio_id, _scope: {
                "portfolio": {"id": 172, "manual_portfolio": True},
            },
            saved_inputs=lambda *_args: self.fail("a manual portfolio must not normalize UBS inputs"),
        )
        owner = SimpleNamespace(_persistence_source=lambda _node_id, _scope: source)
        detail = PortfolioCoordinatorSavedMixin.saved(owner, "robo", "full_history", 172)
        self.assertEqual(detail["portfolio"]["id"], 172)
        self.assertNotIn("inherited_inputs", detail["portfolio"])


class ManualPortfolioRouteTests(unittest.TestCase):
    def test_manager_forwards_the_upload_to_the_node_owner(self) -> None:
        class Handler:
            sent = None

            def _send_json(self, status, value):
                self.sent = (status, value)

        handler = Handler()
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
            return_value=(201, {"portfolio_id": 7}),
        ) as request:
            manager_portfolio_routes.create_manual_portfolio(
                handler, {"url": "http://axi"}, {"files": [{"name": "one.set"}]},
            )
        request.assert_called_once_with(
            {"url": "http://axi"}, "POST", "/api/v1/portfolios/manual",
            {"files": [{"name": "one.set"}]}, timeout=120,
        )
        self.assertEqual(handler.sent, (201, {"portfolio_id": 7}))

    def test_manager_forwards_manual_history_to_the_node(self) -> None:
        class Handler:
            sent = None

            def _send_json(self, status, value):
                self.sent = (status, value)

        handler = Handler()
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request", return_value=(202, {"queued": False}),
        ) as request:
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"url": "http://axi"}, "manual-history", {"portfolio_id": 7},
            )
        request.assert_called_once_with(
            {"url": "http://axi"}, "POST", "/api/v1/portfolios/manual-history",
            {"portfolio_id": 7}, timeout=120,
        )
        self.assertEqual(handler.sent, (202, {"queued": False}))


if __name__ == "__main__":
    unittest.main()
