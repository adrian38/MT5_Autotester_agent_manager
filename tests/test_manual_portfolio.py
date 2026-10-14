from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mt5_manager import manager_portfolio_routes


ROOT = Path(__file__).parents[1]


class ManualPortfolioScreenTests(unittest.TestCase):
    def test_node_card_opens_the_dedicated_folder_screen(self) -> None:
        script = (ROOT / "mt5_manager" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Solo portafolio", script)
        self.assertIn("/portfolio_manual.html?node=", script)

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

    def test_live_audit_preselects_the_new_portfolio_from_the_url(self) -> None:
        script = (ROOT / "mt5_manager" / "static" / "live_audit.js").read_text(encoding="utf-8")
        self.assertIn("function selectRequestedPortfolio()", script)
        self.assertIn("params.get('portfolio')", script)
        self.assertIn("selectRequestedPortfolio();", script)


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
