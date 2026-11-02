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

    def test_no_fork_changes_a_composition_while_its_terminals_are_busy(self) -> None:
        """La edicion se hace con el candado del auditor, en todos los forks.

        No basta con preguntar si hay una auditoria corriendo: el auditor lee
        la composicion al empezar, otra vez antes del tester y otra al
        publicar el resultado, asi que un cambio colado entre dos de esas
        lecturas mezclaria dos composiciones en una misma ejecucion. El
        candado es el mismo que ya se reparten la auditoria y el sondeo de
        cuentas, porque el recurso compartido es la cuenta guardada de cada
        terminal.
        """
        def check(project: Path, _source: str) -> None:
            api = (project / "manager_node_runtime" / "node_jobs_api.py").read_text(
                encoding="utf-8", errors="replace",
            )
            guard = api[api.index("def update_manual_portfolio"):]
            guard = guard[:guard.index("def set_portfolio_alias")]
            for token in (
                "with self.live_audits.lock:",
                "if self.live_audits.is_running():",
                "auditando o sondeando cuentas reales",
            ):
                self.assertIn(
                    token, guard,
                    f"{project}: la edicion de sets manuales no toma el candado ({token})",
                )
            tests = (project / "tests" / "test_manager_node_manual_portfolio.py").read_text(
                encoding="utf-8", errors="replace",
            )
            for token in (
                "test_sets_do_not_change_while_the_node_audits_or_probes_accounts",
                "test_the_change_commits_with_the_audit_lock_taken",
            ):
                self.assertIn(token, tests, f"{project}: falta la prueba {token}")

        self._assert_on_every_fork(check, "candado del auditor al editar sets manuales")
