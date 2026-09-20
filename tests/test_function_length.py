"""Trinquete de longitud de funcion.

El objetivo es el coste de leer, no la estetica: una funcion que no cabe de una
sentada obliga a cargar el fichero entero para cambiar tres lineas.

La lista de perdonadas vive en ``tests/function_length_baseline.json`` y **solo
puede encoger**. Al partir una funcion, borra su entrada; si la dejas, el test
avisa de que sobra.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.function_length import MAX_LINES, function_lengths, load_baseline


class FunctionLengthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sizes = function_lengths()
        cls.baseline = load_baseline()

    def test_no_new_function_exceeds_the_limit(self) -> None:
        offenders = {
            key: size
            for key, size in self.sizes.items()
            if size > MAX_LINES and key not in self.baseline
        }
        self.assertFalse(
            offenders,
            "Funcion nueva por encima de "
            f"{MAX_LINES} lineas. Partela en pasos con nombre; no la anadas al "
            "baseline:\n"
            + "\n".join(f"  {size:>4}  {key}" for key, size in sorted(offenders.items())),
        )

    def test_grandfathered_functions_never_grow(self) -> None:
        grown = {
            key: (self.baseline[key], self.sizes[key])
            for key in self.baseline
            if key in self.sizes and self.sizes[key] > self.baseline[key]
        }
        self.assertFalse(
            grown,
            "Una funcion ya perdonada ha crecido. El trinquete solo baja:\n"
            + "\n".join(
                f"  {key}: {before} -> {after}" for key, (before, after) in sorted(grown.items())
            ),
        )

    def test_the_baseline_has_no_stale_entries(self) -> None:
        stale = sorted(
            key
            for key in self.baseline
            if key not in self.sizes or self.sizes[key] <= MAX_LINES
        )
        self.assertFalse(
            stale,
            "Estas entradas del baseline ya no hacen falta (la funcion se partio, se "
            "renombro o desaparecio). Borralas de "
            "tests/function_length_baseline.json:\n" + "\n".join(f"  {key}" for key in stale),
        )


if __name__ == "__main__":
    unittest.main()
