"""Trinquete de capas del servicio de portafolio.

`mt5_manager/portfolio_service.py` eran 6.045 lineas. Ahora son diecisiete modulos y
**el orden es el de dependencia**: cada uno solo importa de los anteriores. Sin
esta guarda, el primer import hacia arriba vuelve a convertirlo en un solo
fichero con nombres repartidos.

Un import dentro de `if TYPE_CHECKING:` no cuenta: no existe en ejecucion y es
como los modulos de abajo declaran el tipo `PortfolioSource` sin crear un ciclo.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PACKAGE = Path(__file__).resolve().parents[1] / "mt5_manager"

ORDER = [
    "portfolio_scope",
    "portfolio_schema",
    "portfolio_report_cache",
    "portfolio_identity",
    "portfolio_settings",
    "portfolio_transfer",
    "portfolio_persistence",
    "portfolio_valley_floor",
    "portfolio_antifiller",
    "portfolio_generation_search",
    "portfolio_generation",
    "portfolio_completion",
    "portfolio_proposals",
    "portfolio_saved",
    "portfolio_import_match",
    "portfolio_import_build",
    "portfolio_service",
]


def _runtime_imports(module: str) -> set[str]:
    """Modulos de la pila que `module` importa de verdad, no solo para el tipo."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    type_checking_only: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test).endswith("TYPE_CHECKING"):
            type_checking_only.update(id(child) for child in ast.walk(node))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module in set(ORDER)
        and id(node) not in type_checking_only
    }


class PortfolioModuleLayeringTest(unittest.TestCase):
    def test_every_module_of_the_stack_exists(self) -> None:
        missing = [name for name in ORDER if not (PACKAGE / f"{name}.py").is_file()]
        self.assertFalse(
            missing,
            "ORDER nombra modulos que no existen. Si se renombro uno, actualiza la "
            f"lista y la seccion de AGENTS.md: {missing}",
        )

    def test_no_module_imports_from_a_later_one(self) -> None:
        upward = {
            module: sorted(
                dependency for dependency in _runtime_imports(module)
                if ORDER.index(dependency) >= ORDER.index(module)
            )
            for module in ORDER
        }
        offenders = {module: deps for module, deps in upward.items() if deps}
        self.assertFalse(
            offenders,
            "Import hacia arriba en la pila del portafolio. La definicion compartida "
            "baja de nivel; no se invierte la dependencia:\n"
            + "\n".join(f"  {module} -> {deps}" for module, deps in sorted(offenders.items())),
        )

    def test_the_service_still_re_exports_what_it_moved_out(self) -> None:
        """Los llamantes de fuera siguen importando desde `portfolio_service`."""
        service = ast.parse((PACKAGE / "portfolio_service.py").read_text(encoding="utf-8"))
        exported = {
            alias.asname or alias.name
            for node in ast.walk(service)
            if isinstance(node, ast.ImportFrom) and node.module in set(ORDER)
            for alias in node.names
        }
        expected = {
            "ensure_portfolio_schema", "cached_report", "normalize_settings",
            "normalize_portfolio_alias", "save_proposal", "build_import_proposals",
            "result_payload", "settings_inputs", "TYPE_LABELS", "PORTFOLIO_TYPES",
            "_optimize_without_recent_fillers", "_with_executable_valley_floor",
        }
        self.assertEqual(
            set(), expected - exported,
            "portfolio_service dejo de reexportar nombres que sus llamantes importan "
            f"de el: {sorted(expected - exported)}",
        )


if __name__ == "__main__":
    unittest.main()
