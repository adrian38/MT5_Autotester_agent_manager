"""`_trade_view` publica lo mismo aquí que en la copia de cada agente.

Es una **lista blanca**, y es lo último que toca una operación antes de que el
agente la publique en `last_payload`. Un campo que no esté en ella no existe
para el manager por mucho que el extractor lo haya sacado de MT5.

Eso es lo que pasó el 2026-10-22 con `comment`. Se añadió al extractor de los
tres agentes y al espejo del manager, se reinició AXI, se volvió a ejecutar la
auditoría… y las 127 operaciones llegaron sin el campo, porque `_trade_view`
del agente lo tiraba al serializar. No lo delató ninguna prueba: el extractor
era correcto, el manager sabía leer el campo, y el único sitio donde se perdía
no lo miraba nadie. Lo vio el usuario, que avisó dos veces de que el campo no
se iba a guardar antes de que yo lo comprobara.

Por eso esto compara **el conjunto de campos**, no la presencia de un token:
añadir uno en un solo lado tiene que romper, y en los dos no.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from .node_runtime_parity_base import (
        FORK_CANDIDATES, MANAGER_ROOT, fork_live_audit_source,
    )
except ImportError:
    from tests.node.node_runtime_parity_base import (
        FORK_CANDIDATES, MANAGER_ROOT, fork_live_audit_source,
    )

_BODY = re.compile(
    r"def _trade_view\(.*?\n(?=\S|\n\ndef )", re.S,
)
_KEYS = re.compile(r"for key in \(([^)]*)\)", re.S)


def published_fields(source: str) -> set[str]:
    """Los campos que `_trade_view` deja pasar, vengan en una tupla o en dos."""
    body = _BODY.search(source)
    if body is None:
        raise AssertionError("No se encontró `_trade_view` en el código leído")
    groups = _KEYS.findall(body.group(0))
    if not groups:
        raise AssertionError("`_trade_view` ya no declara sus campos en un `for key in (...)`")
    return {
        name for group in groups for name in re.findall(r'"([^"]+)"', group)
    }


class TradeViewParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manager = published_fields(
            (MANAGER_ROOT / "mt5_manager" / "live_audit_core.py").read_text(encoding="utf-8")
        )
        cls.forks = [
            (project, fork_live_audit_source(project))
            for project in FORK_CANDIDATES
            if (project / "manager_node_runtime").is_dir()
        ]

    def test_the_manager_publishes_the_comment(self) -> None:
        # El enlace por comentario del EA se apoya entero en este campo.
        self.assertIn("comment", self.manager)

    def test_every_mounted_fork_publishes_the_same_fields(self) -> None:
        if not self.forks:
            self.skipTest(
                "Ninguna copia de manager_node_runtime/ es alcanzable; buscadas: "
                + ", ".join(str(path) for path in FORK_CANDIDATES)
            )
        for project, source in self.forks:
            with self.subTest(project=str(project)):
                fork = published_fields(source)
                missing = sorted(self.manager - fork)
                extra = sorted(fork - self.manager)
                self.assertEqual(
                    (missing, extra), ([], []),
                    f"{project}: `_trade_view` del agente no publica lo mismo que el "
                    f"manager. Le faltan {missing or 'ninguno'} y le sobran "
                    f"{extra or 'ninguno'}. Un campo que falte aquí se extrae de MT5 "
                    "y se tira al serializar, sin que nada avise.",
                )


if __name__ == "__main__":
    unittest.main()
