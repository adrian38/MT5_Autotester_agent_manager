"""Rutas de lanzamiento manual y automático de comprobaciones."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import manager_portfolio_routes


class RouteTests(unittest.TestCase):
    class Handler:
        def __init__(self, portfolios) -> None:
            self.server = SimpleNamespace(portfolios=portfolios)
            self.sent = None
            self.downloaded = None

        def _send_json(self, status, value) -> None:
            self.sent = (status, value)

        def _send_download(self, result) -> None:
            self.downloaded = result

    def test_the_start_is_forwarded_to_the_node_with_the_recalculated_request(self) -> None:
        portfolios = SimpleNamespace(
            manual_check_request=lambda *_args: {"portfolio_id": 62, "strategies": []},
        )
        handler = self.Handler(portfolios)
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
            return_value=(202, {"job_id": "manual_check_1"}),
        ) as request:
            manager_portfolio_routes.manual_check_action(
                handler, "manual-check-start", {"url": "http://ic"}, "ic", "full_history",
                {"portfolio_id": 62},
            )

        request.assert_called_once_with(
            {"url": "http://ic"}, "POST", "/api/v1/jobs/manual-check",
            {"portfolio_id": 62, "strategies": []}, timeout=120,
        )
        self.assertEqual(handler.sent, (202, {"job_id": "manual_check_1"}))

    def test_settings_are_available_before_a_portfolio_has_an_id(self) -> None:
        portfolios = SimpleNamespace(manual_check_settings=lambda node_id: {"node": node_id})
        handler = self.Handler(portfolios)

        manager_portfolio_routes.manual_check_action(
            handler, "manual-check-settings", {}, "ic", "full_history", {},
        )

        self.assertEqual(handler.sent, (200, {"node": "ic"}))

    def test_save_confirms_the_portfolio_and_then_reports_the_automatic_check(self) -> None:
        portfolios = SimpleNamespace(
            save_to_node=lambda *_args: {
                "portfolio_id": 63,
                "portfolio_ids": {"balanced": 63},
                "manual_check": {"status": "started"},
            },
        )
        handler = self.Handler(portfolios)
        manager_portfolio_routes.save(
            handler, {"url": "http://ic"}, "ic", "full_history",
            {"proposal_key": "balanced"},
        )

        self.assertEqual(handler.sent[0], 201)
        self.assertEqual(handler.sent[1]["manual_check"], {"status": "started"})

    def test_an_agent_without_the_port_says_what_to_do(self) -> None:
        portfolios = SimpleNamespace(manual_check_request=lambda *_args: {})
        handler = self.Handler(portfolios)
        with patch.object(
            manager_portfolio_routes.manager_http, "node_request",
            return_value=(404, {"error": "Ruta no encontrada"}),
        ):
            with self.assertRaises(ValueError) as error:
                manager_portfolio_routes.manual_check_action(
                    handler, "manual-check-start", {"url": "http://ic"}, "ic",
                    "full_history", {"portfolio_id": 62},
                )

        self.assertIn("actualizarlo y reiniciarlo", str(error.exception))

    def test_an_unknown_action_is_left_for_the_next_link_of_the_chain(self) -> None:
        handler = self.Handler(SimpleNamespace())
        self.assertFalse(manager_portfolio_routes.manual_check_action(
            handler, "export", {}, "ic", "full_history", {},
        ))
