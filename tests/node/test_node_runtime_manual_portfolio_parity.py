"""El histórico OHLC manual no puede pisar un informe del pool, en cada fork.

Esta regla no tiene nada que comprobar en `mt5_manager/`: el manager sólo
reenvía el lote de `.set` y la orden de generar el histórico. Quien escribe en
`reports/` es `run_tests.py` del agente, lanzado por
`manager_node_runtime/manual_portfolio_history.py`, y nombra cada informe con
el del `.set`. Por eso un portafolio manual con un set llamado como un
candidato sustituye su informe base: el 2026-11-08 cuatro candidatos de AXI
perdieron su tramo 2020-2024 y las tres carteras que los contenían dejaron de
poder reconstruir su base.

Sin esta prueba el arreglo vive en cuatro copias que nadie compara.
"""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from .node_runtime_parity_base import NodeRuntimeForkParityBase
except ImportError:
    from tests.node.node_runtime_parity_base import NodeRuntimeForkParityBase


class ManualPortfolioReportParityTests(NodeRuntimeForkParityBase):
    def _manual_source(self, project: Path) -> str:
        runtime = project / "manager_node_runtime"
        return "\n".join(
            (runtime / name).read_text(encoding="utf-8", errors="replace")
            for name in ("manual_portfolio.py", "manual_portfolio_history.py")
            if (runtime / name).is_file()
        )

    def test_every_fork_refuses_to_overwrite_a_pool_report(self) -> None:
        def check(project: Path, _source: str) -> None:
            source = self._manual_source(project)
            for pattern, what in (
                (r"def manual_report_stem\(", "el nombre con prefijo del informe"),
                (r"def _history_sets\(", "la copia renombrada sobre la que corre el histórico"),
                (r"def pool_report_names\(", "el inventario de informes del pool"),
                (r"_refuse_pool_report_overwrite\(copies", "la última red sobre las copias"),
            ):
                self._assert_present(
                    source,
                    pattern,
                    f"{project}: falta {what} en manager_node_runtime/manual_portfolio*.py, "
                    "así que «Generar histórico OHLC» de un portafolio manual puede "
                    "sustituir el informe base de un candidato y dejar sin base "
                    "reconstruible a las carteras que lo usen. Portarlo desde una copia "
                    "ya portada. Hay que reiniciar la aplicación del agente.",
                )
            # Prohibir el nombre al subirlo prohibía el caso normal: los
            # portafolios manuales son usos en real y sus estrategias suelen
            # ser candidatos aceptados del pool.
            self._assert_absent(
                source,
                r"reserved_reports",
                f"{project}: manager_node_runtime/manual_portfolio.py sigue rechazando "
                "al subir un .set que se llama como un informe del pool. Eso bloquea "
                "subir las estrategias que la cuenta opera de verdad.",
            )

        self._assert_on_every_fork(check, "el histórico manual no pisa informes del pool")

    def test_every_fork_checks_the_four_candidate_stages(self) -> None:
        """No sólo la base: un set llamado como un informe de etapa lo pisa igual."""
        def check(project: Path, _source: str) -> None:
            source = self._manual_source(project)
            for table in (
                "candidates", "candidate_robustness",
                "candidate_final_tick", "candidate_final_tick_6m",
            ):
                self._assert_present(
                    source,
                    rf'"{table}"',
                    f"{project}: manager_node_runtime/manual_portfolio.py no protege "
                    f"los informes de `{table}`, así que un set manual homónimo de esa "
                    "etapa la sobrescribiría sin aviso.",
                )

        self._assert_on_every_fork(check, "las cuatro etapas protegidas")


if __name__ == "__main__":
    unittest.main()
