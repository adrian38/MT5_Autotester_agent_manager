"""Medida unica de longitud de funcion, compartida por la guarda y su baseline.

La guarda vive en ``tests/test_function_length.py``. El baseline se regenera con::

    python -m tools.function_length --write

Regenerar solo esta permitido para BAJAR el trinquete: el test rechaza cualquier
entrada nueva o cualquier funcion que crezca por encima de lo ya registrado.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

from tools import source_files
from tools.source_files import ROOT, iter_python_files

BASELINE_PATH = ROOT / "tests" / "function_length_baseline.json"

MAX_LINES = 60
"""Techo de lineas por funcion, decorador y cuerpo incluidos.

Una funcion de 60 lineas son ~700 tokens: caben tres en la ventana sin pensarlo.
Por encima de eso, leerla ya cuesta mas que entenderla.
"""


def function_lengths(root: Path | None = None) -> dict[str, int]:
    """Devuelve ``{"ruta/relativa.py::qualname": lineas}`` para todo el repo."""
    base = root or ROOT
    found: dict[str, int] = {}
    for path in iter_python_files(base):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(base).as_posix()
        for node, qualname in _walk_functions(tree):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            found[f"{rel}::{qualname}"] = node.end_lineno - start + 1
    return found


def _walk_functions(tree: ast.AST, prefix: str = ""):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{node.name}"
            yield node, qualname
            yield from _walk_functions(node, f"{qualname}.")
        elif isinstance(node, ast.ClassDef):
            yield from _walk_functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            yield from _walk_functions(node, prefix)


def load_baseline() -> dict[str, int]:
    return source_files.load_baseline(BASELINE_PATH)


def write_baseline() -> dict[str, int]:
    over = {
        key: size for key, size in function_lengths().items() if size > MAX_LINES
    }
    source_files.write_baseline(BASELINE_PATH, over, MAX_LINES, "Funciones")
    return over


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenera el baseline")
    args = parser.parse_args()
    if args.write:
        entries = write_baseline()
        print(f"baseline escrito: {len(entries)} funcion(es) por encima de {MAX_LINES}")
    else:
        sizes = function_lengths()
        over = {k: v for k, v in sizes.items() if v > MAX_LINES}
        print(f"{len(over)}/{len(sizes)} funciones por encima de {MAX_LINES} lineas")
        for key, size in sorted(over.items(), key=lambda kv: -kv[1])[:20]:
            print(f"{size:>5}  {key}")
