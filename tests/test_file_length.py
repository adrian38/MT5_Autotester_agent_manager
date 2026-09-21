"""Trinquete de longitud de fichero.

Companero de ``test_function_length``: ese evita releer un bloque, este evita
tener que cargar el fichero entero para cambiar tres lineas. Cuando un modulo no
cabe, la salida no es un fichero mas corto a base de apreturas, sino un paquete
en pila como ``portfolio_manager/ubs_portfolio/``.

La lista de perdonados vive en ``tests/file_length_baseline.json`` y **solo
puede encoger**.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.file_length import MAX_LINES, file_lengths, load_baseline
from tools.source_files import ratchet_offenders


class FileLengthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.offenders = ratchet_offenders(file_lengths(), load_baseline(), MAX_LINES)

    def test_no_new_file_exceeds_the_limit(self) -> None:
        new = self.offenders["new"]
        self.assertFalse(
            new,
            f"Fichero nuevo por encima de {MAX_LINES} lineas. Partelo en modulos "
            "por dependencia, no por tema; no lo anadas al baseline:\n"
            + "\n".join(f"  {size:>5}  {key}" for key, size in sorted(new.items())),
        )

    def test_grandfathered_files_never_grow(self) -> None:
        grown = self.offenders["grown"]
        self.assertFalse(
            grown,
            "Un fichero ya perdonado ha crecido. El trinquete solo baja: lo nuevo "
            "va a un modulo aparte:\n"
            + "\n".join(
                f"  {key}: {before} -> {after}"
                for key, (before, after) in sorted(grown.items())
            ),
        )

    def test_the_baseline_has_no_stale_entries(self) -> None:
        stale = self.offenders["stale"]
        self.assertFalse(
            stale,
            "Estas entradas del baseline ya no hacen falta. Borralas de "
            "tests/file_length_baseline.json:\n"
            + "\n".join(f"  {key}" for key in sorted(stale)),
        )


if __name__ == "__main__":
    unittest.main()
