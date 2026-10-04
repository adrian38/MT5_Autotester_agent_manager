"""Nombres globales que un módulo usa y no define ni importa.

Una extracción deja fuera ayudantes y constantes sin avisar: el módulo importa
igual, porque el ``NameError`` sólo salta al ejecutar esa rama. En una
extracción de `portfolio_service` faltaban tres nombres y el suite entero sólo
delataba uno.

    python -m tools.undefined_names mt5_manager/portfolio_saved.py

Sin argumentos recorre todo el código del proyecto. Devuelve 1 si encuentra
algo, así que sirve dentro de un script de verificación.

Dos cosas no se denuncian como nombres sueltos:

- Un módulo autorizado con ``import *`` no es comprobable y se dice así. La
  lista cerrada evita que una extracción nueva abra otro agujero silencioso.
- Un nombre que sólo aparece en anotaciones, con ``from __future__ import
  annotations`` activo, nunca se evalúa. Es lo que pasa con los tipos que los
  módulos de abajo de una pila declaran bajo ``if TYPE_CHECKING:``.
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

from tools.source_files import iter_python_files

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STAR_IMPORTS = frozenset({
    "mt5_manager/live_audit_comparison.py",
    "mt5_manager/live_audit_engine.py",
    "mt5_manager/live_audit_extraction.py",
    "mt5_manager/live_audit_lifecycle.py",
    "mt5_manager/live_audit_terminals.py",
    "mt5_manager/live_audit_tester.py",
})
ALWAYS_DEFINED = frozenset(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__all__",
}


def _bound_by(node: ast.AST) -> set[str]:
    """Los nombres que este nodo, por sí solo, deja definidos."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name} | _argument_names(getattr(node, "args", None))
    if isinstance(node, ast.Lambda):
        return _argument_names(node.args)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return set(node.names)
    return set()


def _argument_names(args: ast.arguments | None) -> set[str]:
    if args is None:
        return set()
    names = {
        arg.arg
        for kind in ("posonlyargs", "args", "kwonlyargs")
        for arg in getattr(args, kind, None) or []
    }
    return names | {a.arg for a in (args.vararg, args.kwarg) if a}


def _annotations(tree: ast.AST) -> list[ast.AST]:
    """Las anotaciones de todo el módulo: firmas, retornos y variables."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.extend(a.annotation for a in _annotated_args(node.args))
            found.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
    return [node for node in found if node is not None]


def _annotated_args(args: ast.arguments):
    for kind in ("posonlyargs", "args", "kwonlyargs"):
        yield from (a for a in getattr(args, kind, None) or [] if a.annotation)
    yield from (a for a in (args.vararg, args.kwarg) if a and a.annotation)


def _deferred(tree: ast.AST) -> bool:
    """¿Están las anotaciones diferidas? Entonces no se evalúan nunca."""
    return any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in ast.walk(tree)
    )


def unresolved(source: str) -> list[str] | None:
    """Los nombres leídos que nadie define. ``None`` si hay ``import *``."""
    tree = ast.parse(source)
    if any(
        isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
        for node in ast.walk(tree)
    ):
        return None
    defined = set(ALWAYS_DEFINED)
    for node in ast.walk(tree):
        defined |= _bound_by(node)
    skip = set()
    if _deferred(tree):
        skip = {id(n) for a in _annotations(tree) for n in ast.walk(a)}
    return sorted({
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in defined
        and id(node) not in skip
    })


def relative_path(path: Path) -> str:
    """Ruta estable para comparar la excepción con independencia del SO."""
    return path.resolve().relative_to(ROOT).as_posix()


def main(argv: list[str]) -> int:
    paths = [Path(name) for name in argv] or list(iter_python_files())
    found = allowed_stars = 0
    unexpected_stars = set()
    for path in paths:
        missing = unresolved(path.read_text(encoding="utf-8"))
        if missing is None:
            name = relative_path(path)
            if name in ALLOWED_STAR_IMPORTS:
                allowed_stars += 1
            else:
                unexpected_stars.add(name)
        elif missing:
            found += len(missing)
            print(f"{path}: {', '.join(missing)}")
    stale = set()
    if not argv:
        actual = {relative_path(path) for path in paths if unresolved(
            path.read_text(encoding="utf-8")) is None}
        stale = set(ALLOWED_STAR_IMPORTS) - actual
    for name in sorted(unexpected_stars):
        print(f"{name}: `import *` no autorizado")
    for name in sorted(stale):
        print(f"{name}: excepción de `import *` obsoleta")
    tail = f", {allowed_stars} `import *` autorizado(s)" if allowed_stars else ""
    print(f"{len(paths)} fichero(s), {found} nombre(s) sueltos{tail}")
    return 1 if found or unexpected_stars or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
