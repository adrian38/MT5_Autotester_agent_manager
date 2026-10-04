"""El paquete ubs_portfolio es una pila: cada modulo solo usa los anteriores.

Sin esta guarda el troceo se deshace en dos semanas: basta un import hacia
arriba para volver a tener un solo bloque de 6.800 lineas repartido en veintisiete
ficheros, con ciclos de importacion de regalo.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "portfolio_manager" / "ubs_portfolio"

#: De abajo a arriba. Un modulo solo puede importar de los que tiene encima.
ORDER = [
    "symbols", "models", "rows", "curves", "monthly_validation", "reports",
    "reports_monthly", "selection",
    "evaluation", "margin_models", "margin_loaders", "margin_profiles",
    "margin_summary", "margin", "limits", "constraints", "execution",
    "greedy_increment", "greedy_swap", "greedy_deep", "greedy",
    "optimize_search", "optimize_results", "optimize_flow", "optimize",
    "strict_monthly_candidates", "strict_monthly_refinement",
    "strict_monthly",
]


def _relative_imports(path: Path) -> set[str]:
    """Modulos hermanos de los que importa este fichero."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            found.add(node.module)
    return found


class UbsPackageLayeringTest(unittest.TestCase):
    def test_the_package_has_exactly_the_declared_modules(self) -> None:
        on_disk = sorted(
            path.stem for path in PACKAGE.glob("*.py") if path.stem != "__init__"
        )
        self.assertEqual(
            on_disk,
            sorted(ORDER),
            "Modulo nuevo o renombrado: colocalo en ORDER, en su sitio de la pila.",
        )

    def test_no_module_imports_from_a_later_one(self) -> None:
        rank = {name: index for index, name in enumerate(ORDER)}
        offenders: list[str] = []
        for name in ORDER:
            for imported in sorted(_relative_imports(PACKAGE / f"{name}.py")):
                if imported not in rank:
                    offenders.append(f"{name}.py importa de '{imported}', que no esta en ORDER")
                elif rank[imported] >= rank[name]:
                    offenders.append(f"{name}.py importa de {imported}.py, que va despues")
        self.assertFalse(
            offenders,
            "Import hacia arriba en el paquete. Mueve la definicion compartida a un "
            "modulo mas abajo en vez de invertir la dependencia:\n"
            + "\n".join(f"  {item}" for item in offenders),
        )

    def test_the_init_reexports_every_public_name(self) -> None:
        """Los llamantes importan del paquete; nadie debe saber en que modulo esta."""
        import portfolio_manager.ubs_portfolio as package

        missing = [name for name in package.__all__ if not hasattr(package, name)]
        self.assertFalse(missing, f"En __all__ pero no reexportado: {missing}")

        declared = set(package.__all__)
        for name in ORDER:
            tree = ast.parse((PACKAGE / f"{name}.py").read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    self.assertIn(
                        node.name,
                        declared,
                        f"{name}.py define {node.name} y __init__.py no lo reexporta",
                    )


if __name__ == "__main__":
    unittest.main()
