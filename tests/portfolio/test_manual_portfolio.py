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


ROOT = Path(__file__).parents[2]


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
        self.assertIn('id="manual-use-multiterminal"', page)
        self.assertIn("use_multiterminal: document.querySelector('#manual-use-multiterminal').checked", script)

    def test_manual_inventory_history_can_choose_multiterminal(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn('id="manual-use-multiterminal"', page)
        self.assertLess(page.index('id="manual-use-multiterminal"'), page.index('id="manual-history-controls"'))
        self.assertIn('class="check manual-history-mode"', page)
        self.assertIn("use_multiterminal:document.querySelector('#manual-use-multiterminal').checked", script)

    def test_manual_inventory_history_shows_immediate_real_progress(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn('id="manual-progress-track"', page)
        self.assertIn("paintProgress('loading')", script)
        self.assertIn("paintProgress('starting')", script)
        self.assertIn("function historyProgress", script)
        self.assertIn("node.live_progress||{}", script)

    def test_manual_detail_can_delete_the_portfolio_like_ubs(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn('id="manual-delete"', page)
        self.assertLess(page.index('id="manual-detail"'), page.index('id="manual-delete"'))
        self.assertIn("portfolio-manager/delete", script)
        self.assertIn("scope:'full_history',portfolio_id:portfolioId", script)
        self.assertIn("confirm(", script)
        self.assertIn("function waitForDeletion", script)
        self.assertIn("portfolio-manager/task?scope=full_history", script)
        self.assertIn("if(task.status==='failed')", script)
        self.assertIn("await loadInventory();", script)

    def test_existing_manual_portfolio_can_add_and_remove_sets(self) -> None:
        page = (ROOT / "mt5_manager" / "static" / "manual_portfolios.html").read_text(encoding="utf-8")
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn('id="manual-add-sets"', page)
        self.assertIn('id="manual-set-files"', page)
        self.assertIn("manual-remove-set", script)
        self.assertIn("portfolio-manager/manual-members", script)
        self.assertIn("operation:'add'", script)
        self.assertIn("operation:'remove'", script)
        self.assertIn("set_path:path", script)

    def test_the_screen_warns_and_repeats_the_removal_when_the_portfolio_is_live(self) -> None:
        # El 409 del manager trae los usos y el aviso ya redactado: la pantalla
        # los enseña y, si el usuario sigue, repite la misma peticion con la
        # confirmacion puesta.
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn("response.status===409&&data.needs_live_trading_ack", script)
        self.assertIn("live_trading_ack:true", script)

    def test_a_set_without_history_leaves_the_regeneration_pending_on_screen(self) -> None:
        # El aviso sale de los datos del detalle —`expected` son los miembros y
        # `available` los que encontraron su HTML—, así que sobrevive al
        # refresco y no depende de haber visto el alta.
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn("function pendingHistory", script)
        self.assertIn("(history.expected||0)-(history.available||0)", script)
        self.assertIn("#manual-history-note').textContent=historyNote(history)", script)
        self.assertIn("antes de volver a marcar el portafolio como operando en real", script)

    def test_a_set_nobody_declared_a_real_lot_for_says_so_in_the_audit_dialog(self) -> None:
        """Un set recién subido entra sin lote declarado para la cuenta real.

        El diálogo de lotes rellena su casilla con el lote del portafolio —que
        en una cartera manual es el `StartLots` del propio `.set`—, así que se
        ve idéntico a uno que el usuario revisó. La auditoría sí los distingue
        con `real_account_lot_source`, pero sólo en el resultado, con los
        backtests ya corridos.
        """
        script = (ROOT / "mt5_manager" / "static" / "live_audit.js").read_text(encoding="utf-8")
        self.assertIn(
            "const declared = Object.prototype.hasOwnProperty.call(configured, strategy)",
            script,
        )
        self.assertIn("Sin declarar: hereda el lote del portafolio.", script)

    def test_manual_history_ignores_previous_attempt_without_resetting_detail(self) -> None:
        script = (ROOT / "mt5_manager" / "static" / "manual_portfolios.js").read_text(encoding="utf-8")
        self.assertIn("function isCurrentHistory", script)
        self.assertIn("else historyJobId=", script)
        self.assertIn("const matches=!node.stale&&isCurrentHistory(job)", script)
        self.assertIn("function queuedHistoryDetail", script)
        self.assertIn("if(!matches){paintProgress('queued',[queuedHistoryDetail(node)]);return;}", script)
        self.assertIn("refreshPortfolioResults().catch", script)

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

    @staticmethod
    def _member_handler(profiles: dict) -> SimpleNamespace:
        """El handler con lo justo: usos marcados, caché y promesa guardada."""
        handler = SimpleNamespace(
            invalidated=[], discarded=[],
            _send_json=lambda status, value: setattr(handler, "sent", (status, value)),
        )
        handler.server = SimpleNamespace(
            portfolios=SimpleNamespace(
                _invalidate_node_snapshots=handler.invalidated.append,
            ),
            live_audit_settings=SimpleNamespace(
                state=lambda node_id: {"profiles": profiles},
            ),
            live_accounts=SimpleNamespace(
                discard_expectation=lambda node_id, portfolio_id: handler.discarded.append(
                    (node_id, portfolio_id),
                ),
            ),
        )
        return handler

    def test_manager_forwards_manual_member_changes_to_the_node(self) -> None:
        handler = self._member_handler({"7-balanced": {"portfolio_id": 9}})
        body = {"operation": "remove", "portfolio_id": 7, "set_path": "one.set"}
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request", return_value=(200, {"removed": 1}),
        ) as request:
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"id": "axi", "url": "http://axi"}, "manual-members", body,
            )
        request.assert_called_once_with(
            {"id": "axi", "url": "http://axi"}, "POST", "/api/v1/portfolios/manual-members",
            body, timeout=120,
        )
        self.assertEqual(handler.sent, (200, {"removed": 1}))
        self.assertEqual(handler.invalidated, ["axi"])

    def test_changing_the_sets_drops_the_promise_of_the_real_account(self) -> None:
        # La firma de la expectativa cubre los miembros, pero `_needs_rebuild`
        # deja pasar hasta 24 h antes de volver a medir: sin este borrado la
        # tarjeta de la cuenta promete el beneficio de la composición anterior.
        handler = self._member_handler({})
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request", return_value=(200, {"added": 2}),
        ):
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"id": "axi", "url": "http://axi"}, "manual-members",
                {"operation": "add", "portfolio_id": 7, "files": []},
            )
        self.assertEqual(handler.discarded, [("axi", 7)])

    def test_a_failed_change_leaves_the_promise_and_the_cache_alone(self) -> None:
        handler = self._member_handler({})
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
            return_value=(409, {"error": "El nodo esta auditando"}),
        ):
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"id": "axi", "url": "http://axi"}, "manual-members",
                {"operation": "add", "portfolio_id": 7, "files": []},
            )
        self.assertEqual(handler.sent, (409, {"error": "El nodo esta auditando"}))
        self.assertEqual((handler.invalidated, handler.discarded), ([], []))

    def test_removing_a_set_of_a_live_portfolio_asks_before_doing_it(self) -> None:
        # No es un rechazo: la pantalla necesita los usos para nombrarlos en el
        # aviso y repetir la peticion confirmada.
        handler = self._member_handler({"7-balanced": {"portfolio_id": 7, "live_trading": True}})
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
        ) as request:
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"id": "axi", "url": "http://axi"}, "manual-members",
                {"operation": "remove", "portfolio_id": 7, "set_path": "one.set"},
            )
        request.assert_not_called()
        status, value = handler.sent
        self.assertEqual(status, 409)
        self.assertEqual(value["live_trading_uses"], ["7-balanced"])
        self.assertTrue(value["needs_live_trading_ack"])
        self.assertIn("saldrán como ajenas", value["error"])
        self.assertEqual((handler.invalidated, handler.discarded), ([], []))

    def test_a_confirmed_removal_runs_and_refreshes_what_cuelga_de_la_cartera(self) -> None:
        # Confirmada se ejecuta igual que cualquier otra, y con la misma
        # limpieza: la copia de la memoria y la promesa de la cuenta real son
        # lo que alimenta la portada y la pantalla de cuentas.
        handler = self._member_handler({"7-balanced": {"portfolio_id": 7, "live_trading": True}})
        body = {
            "operation": "remove", "portfolio_id": 7, "set_path": "one.set",
            "live_trading_ack": True,
        }
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request", return_value=(200, {"removed": 1}),
        ) as request:
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"id": "axi", "url": "http://axi"}, "manual-members", body,
            )
        # El nodo no conoce la confirmacion: es un asunto del manager.
        request.assert_called_once_with(
            {"id": "axi", "url": "http://axi"}, "POST", "/api/v1/portfolios/manual-members",
            {"operation": "remove", "portfolio_id": 7, "set_path": "one.set"}, timeout=120,
        )
        self.assertEqual(handler.sent, (200, {"removed": 1}))
        self.assertEqual((handler.invalidated, handler.discarded), (["axi"], [("axi", 7)]))

    def test_a_confirmation_does_not_let_new_sets_into_a_live_portfolio(self) -> None:
        # El alta sigue rechazada: un set recien subido no tiene informe OHLC,
        # asi que la promesa mediria menos estrategias de las que opera.
        handler = self._member_handler({"7-balanced": {"portfolio_id": 7, "live_trading": True}})
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
        ) as request:
            with self.assertRaisesRegex(ValueError, "antes de añadirle sets"):
                manager_portfolio_routes.manual_portfolio_action(
                    handler, {"id": "axi", "url": "http://axi"}, "manual-members",
                    {"operation": "add", "portfolio_id": 7, "files": [], "live_trading_ack": True},
                )
        request.assert_not_called()

    def test_a_portfolio_trading_live_does_not_change_its_composition(self) -> None:
        handler = self._member_handler({
            "7-balanced": {"portfolio_id": 7, "live_trading": True},
            "9-balanced": {"portfolio_id": 9, "live_trading": True},
        })
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
        ) as request:
            with self.assertRaisesRegex(ValueError, "operando en real en 7-balanced"):
                manager_portfolio_routes.manual_portfolio_action(
                    handler, {"id": "axi", "url": "http://axi"}, "manual-members",
                    {"operation": "add", "portfolio_id": 7, "files": []},
                )
        request.assert_not_called()
        self.assertEqual((handler.invalidated, handler.discarded), ([], []))

    def test_a_use_that_is_no_longer_live_does_not_block_the_edit(self) -> None:
        handler = self._member_handler({"7-balanced": {"portfolio_id": 7}})
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request", return_value=(200, {"added": 1}),
        ):
            manager_portfolio_routes.manual_portfolio_action(
                handler, {"id": "axi", "url": "http://axi"}, "manual-members",
                {"operation": "add", "portfolio_id": 7, "files": []},
            )
        self.assertEqual(handler.sent, (200, {"added": 1}))
        self.assertEqual(handler.discarded, [("axi", 7)])


if __name__ == "__main__":
    unittest.main()
