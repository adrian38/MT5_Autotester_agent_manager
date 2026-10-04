"""El indice de `ai_context/` menciona todas sus notas.

`AGENTS.md` manda leer `ai_context/` antes de tocar un area, y el `README.md`
de esa carpeta se presenta como su indice. Llego a tener 24 de 42 notas sin
mencionar, entre ellas `guided_batches.md` y `cross_scope_parity.md`. No es
fatal -la regla manda `rg -il`, que no depende del indice- pero un indice a
medias miente por omision: quien lo lee cree que ya ha visto lo que hay.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_context_index import missing, notes


class AiContextIndexTests(unittest.TestCase):
    def test_every_note_is_in_the_index(self):
        absent = [path.name for path in missing()]
        self.assertEqual(
            absent, [],
            "anade su linea a ai_context/README.md: "
            "`python -m tools.ai_context_index` las lista",
        )

    def test_there_are_notes_to_index(self):
        """Si la carpeta se queda vacia, la prueba de arriba pasa sin mirar."""
        self.assertGreater(len(notes()), 20)


if __name__ == "__main__":
    unittest.main()
