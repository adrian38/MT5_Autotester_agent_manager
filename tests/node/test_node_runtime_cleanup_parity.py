"""La limpieza historica de cada fork no sale de las terminales del agente."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from .node_runtime_parity_base import (
        NodeRuntimeForkParityBase, fork_node_source, manager_node_source,
    )
except ImportError:
    from tests.node.node_runtime_parity_base import (
        NodeRuntimeForkParityBase, fork_node_source, manager_node_source,
    )


class NodeRuntimeCleanupParityTests(NodeRuntimeForkParityBase):
    def test_the_cleanup_of_every_fork_stops_at_its_own_terminals(self) -> None:
        """Cada agente limpia las terminales de su broker y las de nadie más.

        Mientras el criterio fue `%APPDATA%\\MetaQuotes\\Terminal` entero, los
        cuatro agentes de esta máquina compartían objetivo: el 2026-11-17 la
        limpieza de IC borró la caché del Tester de AXI y después `cleanup_verify`
        marcó el trabajo como fallido porque no pudo vaciar unos `bases` que AXI
        tenía abiertos desde otra sesión de Windows. Un fork sin portar vuelve a
        pisar al vecino, y el síntoma aparece en la tarjeta del otro agente.
        """
        self.assertIn("cleanup_terminal_targets", manager_node_source())

        def check(project: Path, _source: str) -> None:
            node_source = fork_node_source(project)
            self._assert_present(
                node_source,
                r"def cleanup_terminal_targets",
                f"{project}: el nodo sigue sin acotar la limpieza a sus terminales; "
                "portar `cleanup_terminal_targets` a manager_node_runtime/node_settings.py.",
            )
            self._assert_present(
                node_source,
                r'"-DataDirs"',
                f"{project}: el nodo no le pasa sus carpetas a los scripts de limpieza, "
                "así que PowerShell vuelve a recorrer %APPDATA% entero.",
            )
            self._assert_absent(
                node_source,
                r'terminal_root\.iterdir\(\)',
                f"{project}: `cleanup_verify` sigue contando todas las terminales de "
                "%APPDATA%, también las de los otros agentes.",
            )
            for name in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
                script = (project / "scripts" / name).read_text(
                    encoding="utf-8", errors="replace",
                )
                self._assert_present(
                    script,
                    r"\$DataDirs",
                    f"{project}: {name} no recibe las carpetas del agente.",
                )
                self._assert_absent(
                    script,
                    r"Get-ChildItem \$basePath -Directory",
                    f"{project}: {name} sigue recorriendo todas las terminales del usuario.",
                )
                self._assert_absent(
                    script,
                    r'Get-Process \$pattern',
                    f"{project}: {name} sigue cerrando cualquier terminal de la máquina, "
                    "no sólo las de este agente.",
                )

        self._assert_on_every_fork(check, "la limpieza no sale de las terminales del agente")


if __name__ == "__main__":
    unittest.main()
