"""Guardado automático al terminar construcción, mejora o arreglo."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mt5_manager import manager_http
from mt5_manager.portfolio_service import PortfolioCoordinator


class AutomaticSaveTests(unittest.TestCase):
    def coordinator(self, root: str) -> PortfolioCoordinator:
        return PortfolioCoordinator(
            [{"id": "ic", "portfolio_broker": "ICTRADING", "url": "http://ic"}],
            Path(root) / "settings.json",
        )

    def test_construction_uses_the_selected_reference_and_keeps_all_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = self.coordinator(temp)
            key = coordinator._key("ic", "full_history")
            coordinator.jobs[key] = {
                "status": "completed", "automatic_save": True,
                "automatic_save_key": "conservative",
            }
            coordinator.proposals[key] = [
                {"key": "aggressive"}, {"key": "balanced"}, {"key": "conservative"},
            ]
            with patch.object(
                coordinator, "save_to_node",
                return_value={"portfolio_id": 81, "portfolio_ids": {"conservative": 81}},
            ) as save:
                result = coordinator.automatic_save_completed(
                    "ic", "full_history", {"portfolio_type": "balanced"},
                )

        save.assert_called_once_with("ic", "full_history", "conservative")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(len(coordinator.proposals[key]), 3)

    def test_a_save_failure_leaves_the_completed_proposal_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = self.coordinator(temp)
            key = coordinator._key("ic", "full_history")
            coordinator.jobs[key] = {"status": "completed", "automatic_save": True}
            coordinator.proposals[key] = [{"key": "balanced"}]
            with patch.object(
                coordinator, "save_to_node", side_effect=ValueError("nodo apagado"),
            ):
                result = coordinator.automatic_save_completed(
                    "ic", "full_history", {},
                )

        self.assertEqual(result, {"status": "failed", "error": "nodo apagado"})
        self.assertEqual(coordinator.jobs[key]["status"], "completed")
        self.assertEqual(coordinator.proposals[key], [{"key": "balanced"}])

    def test_save_confirms_before_starting_the_inherited_manual_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = self.coordinator(temp)
            payload = {"request_id": "save-1", "selected_key": "balanced"}
            order: list[str] = []
            with patch.object(coordinator, "prepare_save", return_value=payload), \
                    patch.object(coordinator, "pending_automatic_manual_check", return_value={}), \
                    patch.object(coordinator, "_send_save_payload", return_value=82), \
                    patch.object(coordinator, "confirm_save", side_effect=lambda *_: order.append("save")), \
                    patch.object(
                        coordinator, "start_automatic_manual_check",
                        side_effect=lambda *_: order.append("manual") or {"status": "started"},
                    ):
                result = coordinator.save_to_node("ic", "full_history", "balanced")

        self.assertEqual(order, ["save", "manual"])
        self.assertEqual(result["manual_check"], {"status": "started"})

    def test_inherited_manual_check_is_forwarded_after_the_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = self.coordinator(temp)
            with patch.object(
                coordinator, "manual_check_preview",
                return_value={"strategies": [{"set_name": "nueva.set"}]},
            ), patch.object(
                coordinator, "manual_check_request", return_value={"portfolio_id": 82},
            ), patch.object(
                manager_http, "node_request", return_value=(202, {"queued": True}),
            ) as request:
                result = coordinator.start_automatic_manual_check(
                    "ic", "full_history", 82, {},
                )

        self.assertEqual(result, {"status": "queued"})
        request.assert_called_once_with(
            coordinator._node("ic"), "POST", "/api/v1/jobs/manual-check",
            {"portfolio_id": 82}, timeout=120,
        )

    def test_manual_check_is_ignored_when_automatic_save_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = self.coordinator(temp)
            changes = {
                "automatic_save": False,
                "automatic_manual_check": {"models": ["tick"]},
            }
            with patch.object(coordinator, "update_settings", return_value={}), \
                    patch.object(coordinator, "_start_job", return_value={}) as start:
                coordinator.start("ic", "full_history", changes)

        self.assertIsNone(start.call_args.args[6])
        self.assertFalse(start.call_args.args[7])


if __name__ == "__main__":
    unittest.main()
