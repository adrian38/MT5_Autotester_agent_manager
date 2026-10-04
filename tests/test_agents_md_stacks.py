"""El orden de las pilas que dice `AGENTS.md` es el que vigilan las guardas.

El orden esta escrito tres veces: en `AGENTS.md`, en el `ORDER` de las pruebas
de capas y en el de `tools/sync_ubs_exports.py`. Ya derivo una vez:
`reports_monthly` entro en la pila de `ubs_portfolio`, se actualizaron las dos
listas del codigo y no la del documento, que se quedo un mes con veintisiete
modulos de veintiocho.

Es la peor clase de deriva. El documento es lo que se lee para orientarse antes
de tocar nada, y el test es lo que manda: mientras no discrepen no pasa nada, y
cuando discrepan el documento miente sin que nadie se entere.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"

#: Una cadena `a` -> `b` -> `c`, aunque parta lineas.
CHAIN = re.compile(r"`[a-z_0-9]+`(?:\s*→\s*`[a-z_0-9]+`)+")
NAME = re.compile(r"`([a-z_0-9]+)`")


def documented_chains() -> dict[str, list[str]]:
    """Las cadenas de `AGENTS.md`, indexadas por su primer modulo."""
    text = AGENTS.read_text(encoding="utf-8")
    chains = [NAME.findall(match.group()) for match in CHAIN.finditer(text)]
    return {chain[0]: chain for chain in chains}


def order_in(path: Path, name: str) -> list[str]:
    """El literal `ORDER` (o el que se pida) de un modulo, sin importarlo."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in targets
        ):
            return [str(value) for value in ast.literal_eval(node.value)]
    raise AssertionError(f"{path.name} ya no define {name}")


class AgentsMdStackTests(unittest.TestCase):
    def setUp(self):
        self.chains = documented_chains()

    def guards(self) -> dict[str, list[str]]:
        layering = ROOT / "tests" / "test_module_layering.py"
        return {
            "ubs_portfolio": order_in(ROOT / "tests" / "test_ubs_package_layering.py", "ORDER"),
            "portfolio_service": order_in(layering, "PORTFOLIO_STACK"),
            "manager": order_in(layering, "MANAGER_STACK"),
            "node": order_in(layering, "NODE_STACK"),
        }

    def test_agents_md_documents_each_stack_in_the_order_the_guard_enforces(self):
        for label, order in self.guards().items():
            with self.subTest(stack=label):
                self.assertIn(
                    order[0], self.chains,
                    f"AGENTS.md ya no describe la pila {label}; empieza en {order[0]}",
                )
                self.assertEqual(
                    self.chains[order[0]], order,
                    f"la pila {label} de AGENTS.md no coincide con su guarda",
                )

    def test_the_export_tool_and_the_layering_guard_agree_on_ubs(self):
        """`sync_ubs_exports` reexporta en ese orden: si discrepan, falta uno."""
        self.assertEqual(
            order_in(ROOT / "tools" / "sync_ubs_exports.py", "ORDER"),
            order_in(ROOT / "tests" / "test_ubs_package_layering.py", "ORDER"),
        )

    def test_no_stack_is_documented_that_nobody_enforces(self):
        """Una cadena en el documento sin guarda detras es una promesa vacia."""
        enforced = {order[0] for order in self.guards().values()}
        self.assertEqual(set(self.chains) - enforced, set())


if __name__ == "__main__":
    unittest.main()
