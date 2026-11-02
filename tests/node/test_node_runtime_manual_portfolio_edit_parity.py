"""Paridad de la edicion de sets en los portafolios manuales."""
from __future__ import annotations

from pathlib import Path

from .node_runtime_parity_base import NodeRuntimeForkParityBase, fork_node_source


class ManualPortfolioEditParityTests(NodeRuntimeForkParityBase):
    def test_manual_set_edits_reach_every_active_fork(self) -> None:
        def check(project: Path, _source: str) -> None:
            runtime = (project / "manager_node_runtime" / "manual_portfolio.py").read_text(
                encoding="utf-8", errors="replace",
            )
            for token in (
                "def update_manual_portfolio(", "def _add_manual_sets(",
                "def _remove_manual_set(",
            ):
                self.assertIn(token, runtime, f"{project}: falta {token}")
            self.assertIn(
                '"/api/v1/portfolios/manual-members"', fork_node_source(project),
                f"{project}: falta la ruta para editar los sets manuales",
            )
            tests = (project / "tests" / "test_manager_node_manual_portfolio.py").read_text(
                encoding="utf-8", errors="replace",
            )
            for token in (
                "test_sets_can_be_added_to_an_existing_manual_portfolio",
                "test_one_set_can_be_removed_from_an_existing_manual_portfolio",
            ):
                self.assertIn(token, tests, f"{project}: falta la prueba {token}")

        self._assert_on_every_fork(check, "edicion de sets en portafolios manuales")
