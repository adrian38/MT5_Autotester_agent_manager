"""Medida unica de longitud de fichero, companera de ``function_length``.

La guarda vive en ``tests/test_file_length.py``. El baseline se regenera con::

    python -m tools.file_length --write

Igual que el de funciones, regenerar solo esta permitido para BAJAR el
trinquete.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools import source_files
from tools.source_files import ROOT, iter_python_files

BASELINE_PATH = ROOT / "tests" / "file_length_baseline.json"

MAX_LINES = 600
"""Techo de lineas por fichero.

A ~12 tokens por linea, 600 lineas son ~7k tokens: un modulo entero cabe en la
ventana junto a sus llamantes. El techo por funcion evita releer un bloque; este
evita tener que cargar 6.000 lineas para cambiar tres.
"""


def file_lengths(root: Path | None = None) -> dict[str, int]:
    """Devuelve ``{"ruta/relativa.py": lineas}`` para todo el codigo propio."""
    base = root or ROOT
    return {
        path.relative_to(base).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in iter_python_files(base)
    }


def load_baseline() -> dict[str, int]:
    return source_files.load_baseline(BASELINE_PATH)


def write_baseline() -> dict[str, int]:
    over = {key: size for key, size in file_lengths().items() if size > MAX_LINES}
    source_files.write_baseline(BASELINE_PATH, over, MAX_LINES, "Ficheros")
    return over


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenera el baseline")
    args = parser.parse_args()
    if args.write:
        entries = write_baseline()
        print(f"baseline escrito: {len(entries)} fichero(s) por encima de {MAX_LINES}")
    else:
        sizes = file_lengths()
        over = {k: v for k, v in sizes.items() if v > MAX_LINES}
        print(f"{len(over)}/{len(sizes)} ficheros por encima de {MAX_LINES} lineas")
        print(f"exceso total: {sum(v - MAX_LINES for v in over.values())} lineas")
        for key, size in sorted(over.items(), key=lambda kv: -kv[1]):
            print(f"{size:>5}  {key}")
