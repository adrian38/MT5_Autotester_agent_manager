"""Ninguna hoja de estilo puede sacar una celda de su tabla.

Un `th` o un `td` con `display: grid`, `flex` o `block` deja de ser celda: no
se estira a la altura de su fila y su borde inferior se dibuja donde acaba su
propio contenido, así que la fila aparece partida en la pantalla. Ha pasado
tres veces -`.audit-analysis-empty` en una celda vacía, `.lots-table tbody th`
y `.audit-analysis-table tbody th`-, siempre queriendo apilar `strong` y
`small`: eso se hace en los hijos, no en la celda.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CELL_ELEMENTS = {"th", "td", "tr", "tbody", "thead"}
# `table-cell` es justamente la reparación: devuelve la celda a la tabla.
ALLOWED_DISPLAYS = {"table-cell", "table-row", "table-row-group", "table-header-group"}
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
DISPLAY = re.compile(r"display\s*:\s*([^;]+)")


def _targets_a_cell(selector: str) -> bool:
    """¿El último elemento del selector es la propia celda y no un hijo suyo?"""
    last = selector.strip().split()[-1] if selector.strip() else ""
    last = last.split(":")[0].split(">")[-1].strip()
    return last in CELL_ELEMENTS


class TableCellDisplayTests(unittest.TestCase):
    def test_no_stylesheet_takes_a_cell_out_of_its_table(self) -> None:
        offenders: list[str] = []
        static = Path(__file__).parents[2] / "mt5_manager" / "static"
        sheets = sorted(static.glob("*.css"))
        self.assertTrue(sheets, "no se encontró ninguna hoja de estilo que revisar")
        for sheet in sheets:
            text = re.sub(r"/\*.*?\*/", "", sheet.read_text(encoding="utf-8"), flags=re.S)
            for selector, body in RULE.findall(text):
                found = DISPLAY.search(body)
                if not found:
                    continue
                display = found.group(1).strip()
                if display in ALLOWED_DISPLAYS:
                    continue
                for part in " ".join(selector.split()).split(","):
                    if _targets_a_cell(part):
                        offenders.append(f"{sheet.name}: {part.strip()} -> display: {display}")
        self.assertEqual(offenders, [], msg=(
            "Estas reglas sacan la celda de su tabla y parten la fila; apilar el "
            "contenido en los hijos (`th strong`, `th small`) en vez de en la celda: "
            f"{offenders}"
        ))


if __name__ == "__main__":
    unittest.main()
