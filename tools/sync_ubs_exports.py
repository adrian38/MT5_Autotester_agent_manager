"""Regenera los reexports de ``portfolio_manager/ubs_portfolio/__init__.py``.

El paquete reexporta todos sus nombres para que los llamantes no tengan que
saber en que modulo vive cada cosa, y ``tests/test_ubs_package_layering.py`` lo
exige. Mantener esa lista a mano al partir una funcion es puro ruido, asi que se
genera::

    python -m tools.sync_ubs_exports

El orden de los bloques es el de la pila de dependencias, el mismo que la guarda
comprueba: un modulo solo puede importar de los anteriores.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "portfolio_manager" / "ubs_portfolio"

ORDER = [
    "symbols", "models", "rows", "curves", "monthly_validation", "reports", "selection",
    "evaluation", "margin", "limits", "constraints", "execution", "greedy",
    "optimize", "strict_monthly",
]


def public_names(module: str) -> list[str]:
    """Todo lo que el modulo define en su nivel superior."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return sorted(set(names))


def rebuild() -> int:
    init = PACKAGE / "__init__.py"
    header = init.read_text(encoding="utf-8").split("from .symbols import (", 1)[0]
    blocks, exported = [], []
    for module in ORDER:
        names = public_names(module)
        exported += names
        body = ",\n".join(f"    {name}" for name in names)
        blocks.append(f"from .{module} import (\n{body},\n)")
    text = header + "\n".join(blocks) + "\n\n__all__ = [\n"
    text += "".join(f'    "{name}",\n' for name in sorted(set(exported)))
    text += "]\n"
    init.write_text(text, encoding="utf-8")
    return len(set(exported))


if __name__ == "__main__":
    print(f"__init__.py regenerado: {rebuild()} nombres")
