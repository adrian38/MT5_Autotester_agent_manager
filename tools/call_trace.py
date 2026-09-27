"""Compara la secuencia de llamadas de una función antes y después de partirla.

Cuando una función no tiene cobertura —y las hay: `_recalculate_saved` y
`generate_completion_proposal` tenían cero mientras el suite entero pasaba—, lo
único que queda es comparar su forma. De cada versión se extrae la lista
ordenada de llamadas: nombre, argumentos posicionales tal como se escriben y
nombres de los kwargs, expandiendo en su sitio los pasos nuevos. Si las dos
listas coinciden, no se ha caído ni se ha renombrado ningún argumento.

    python -m tools.call_trace mt5_manager/portfolio_saved.py _recalculate_saved \\
        _saved_curve _saved_metrics

El primer argumento es el fichero, el segundo la función y el resto los
ayudantes nuevos que hay que expandir. Compara contra ``git show HEAD:``.
"""
from __future__ import annotations

import ast
import difflib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def functions(source: str) -> dict[str, ast.AST]:
    """Por nombre simple, métodos incluidos: aquí no hay nombres repetidos."""
    return {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def call_sequence(
    node: ast.AST, defs: dict[str, ast.AST], expand: set[str],
    seen: tuple[str, ...] = (),
) -> list[str]:
    """Las llamadas en orden de aparición; las de `expand`, sustituidas."""
    out: list[str] = []
    for child in _ordered(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child.func)
        # `self._helper(...)` se expande igual que `_helper(...)`: se compara la
        # secuencia, no si el paso nuevo quedó como método o como función.
        bare = name.split(".")[-1] if name.startswith("self.") else name
        step = next((n for n in (bare, name) if n in expand and n in defs and n not in seen), None)
        if step:
            out.extend(call_sequence(defs[step], defs, expand, seen + (step,)))
            continue
        out.append(_render(name, child))
    return out


def _render(name: str, call: ast.Call) -> str:
    args = ", ".join(_arg(a) for a in call.args)
    kwargs = ", ".join(k.arg or "**" for k in call.keywords)
    return f"{name}({args}{'; ' if call.keywords else ''}{kwargs})"


def _ordered(node: ast.AST) -> list[ast.AST]:
    """Nodos en orden de aparición en el fuente, no en orden de árbol."""
    nodes = [n for n in ast.walk(node) if hasattr(n, "lineno")]
    return sorted(nodes, key=lambda n: (n.lineno, n.col_offset))


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return f"{_call_name(func.value)}.{func.attr}"
    if isinstance(func, ast.Call):
        return _call_name(func.func) + "()"
    return type(func).__name__


def _arg(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_name(node)
    if isinstance(node, ast.Constant):
        # La sangría de un SQL cambia al mover el bloque de nivel y no es
        # significativa: se compara el texto con los espacios colapsados.
        if isinstance(node.value, str):
            return repr(" ".join(node.value.split()))
        return repr(node.value)
    return f"<{type(node).__name__}>"


def compare(target_file: str, name: str, helpers: set[str]) -> tuple[list[str], list[str]]:
    head = subprocess.run(
        ["git", "show", f"HEAD:{target_file}"], capture_output=True, check=True, cwd=REPO,
    ).stdout.decode("utf-8")
    now = (REPO / target_file).read_text(encoding="utf-8")
    head_defs, new_defs = functions(head), functions(now)
    if name not in head_defs:
        raise SystemExit(f"{name} no existe en HEAD:{target_file}")
    return (call_sequence(head_defs[name], head_defs, set()),
            call_sequence(new_defs[name], new_defs, helpers))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    target_file, name, *helpers = argv
    before, after = compare(target_file, name, set(helpers))
    if before == after:
        print(f"{name}: {len(before)} llamadas, secuencia idéntica a HEAD")
        return 0
    print(f"{name}: DIFIERE ({len(before)} llamadas en HEAD, {len(after)} ahora)")
    for line in difflib.unified_diff(before, after, "HEAD", "ahora", lineterm="", n=2):
        print(" ", line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
