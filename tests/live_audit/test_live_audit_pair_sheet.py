"""El Excel que corrige los enlaces que el auditor hizo mal.

Los dos Excel anteriores sólo **completan** al auditor: la atribución rellena
los huecos y el ticket escrito en una fila `SIN REAL` también. Ninguno alcanza
a una fila ya emparejada, que es justo donde el usuario encontró el fallo de
`audit-173`: `#T19` tenía la real equivocada y `#T21` tenía la que le
correspondía a `#T19`. Este fichero es el contrato del Excel que sí llega ahí.
"""
from __future__ import annotations

import base64
import io
import unittest
import zipfile
from pathlib import Path

from mt5_manager.live_audit_attribution import import_pair_workbook
from mt5_manager.live_audit_manual_pairs import (
    normalize_manual_pairs, tester_pair_key, unpaired_keys,
)
from tests.live_audit.test_live_audit_assignment import compare, operation

STATIC = Path(__file__).parents[2] / "mt5_manager" / "static"


def sheet(rows: list[tuple[str, str, str]], headers: tuple[str, ...] = ("Fila", "Ticket", "Nota")) -> bytes:
    """Un libro con la cabecera del Excel de enlace y sus filas."""
    cells = []
    for index, values in enumerate([headers, *rows]):
        line = index + 1
        columns = "".join(
            f'<c r="{chr(ord("A") + column)}{line}" t="str"><v>{value}</v></c>'
            for column, value in enumerate(values)
        )
        cells.append(f'<row r="{line}">{columns}</row>')
    body = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Enlaces" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", body)
    return output.getvalue()


def imported(rows: list[tuple[str, str, str]], **kwargs) -> dict:
    return import_pair_workbook({
        "filename": "enlaces.xlsx",
        "content_base64": base64.b64encode(sheet(rows, **kwargs)).decode("ascii"),
    })


class PairSheetTests(unittest.TestCase):
    def test_the_three_columns_are_all_it_needs(self) -> None:
        result = imported([
            ("T19", "771314830", "el cierre coincide al segundo"),
            ("T21", "", "la cuenta no ejecutó esta"),
        ])
        self.assertEqual(result["rows"], {
            "19": {"ticket": "771314830", "note": "el cierre coincide al segundo"},
            "21": {"ticket": "", "note": "la cuenta no ejecutó esta"},
        })
        self.assertEqual(result["source_name"], "enlaces.xlsx")
        self.assertEqual(len(result["source_sha256"]), 64)

    def test_the_columns_can_be_in_any_order_and_the_note_is_optional(self) -> None:
        result = imported(
            [("771314830", "T19")], headers=("Ticket", "Fila"),
        )
        self.assertEqual(result["rows"], {"19": {"ticket": "771314830", "note": ""}})

    def test_sin_real_in_words_also_says_it_has_no_real(self) -> None:
        # Escribir «SIN REAL» es más explícito que dejar la celda vacía, y es
        # lo que el usuario ya escribía a mano en la columna de observaciones.
        self.assertEqual(imported([("T21", "SIN REAL", "")])["rows"]["21"]["ticket"], "")
        self.assertEqual(imported([("T21", "ninguna", "")])["rows"]["21"]["ticket"], "")

    def test_the_ticket_is_read_with_or_without_the_word(self) -> None:
        self.assertEqual(
            imported([("T19", "ticket 771314830", "")])["rows"]["19"]["ticket"],
            "771314830",
        )

    def test_what_is_not_a_ticket_and_is_not_sin_real_is_refused(self) -> None:
        with self.assertRaises(ValueError) as refused:
            imported([("T19", "el de las 15:45", "")])
        self.assertIn("T19", str(refused.exception))

    def test_two_rows_cannot_claim_the_same_real(self) -> None:
        with self.assertRaises(ValueError):
            imported([("T19", "771314830", ""), ("T21", "771314830", "")])

    def test_a_sheet_without_the_header_is_refused(self) -> None:
        with self.assertRaises(ValueError) as refused:
            imported([("T19", "771314830", "")], headers=("Operación", "Real", "Nota"))
        self.assertIn("Fila y Ticket", str(refused.exception))

    def test_a_sheet_that_names_no_tester_row_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            imported([("771314830", "", "")])


class UnpairedRowTests(unittest.TestCase):
    """Un ticket vacío deja la fila sin real y devuelve la real al reparto."""

    def rows(self, pairs: dict) -> list[dict]:
        # El caso de `audit-173` al revés: aquí el auditor acierta solo, así que
        # se le fuerza el reparto contrario para comprobar que se puede
        # deshacer. La real cierra con `#T19` y abre dos segundos tarde.
        tester = [
            operation("manual:22", 0, 95_091, symbol=".USTECHCash", price=30_390.1),
            operation("manual:23", 0, 47_293, symbol=".USTECHCash", price=30_390.1),
        ]
        real = [{
            **operation("771314830", 2, 95_091, symbol=".USTECHCash", price=30_385.3),
            # La huella de la real es su `position_id`, no el nombre: es lo que
            # `forced_assignment` traduce a índices de esta ejecución.
            "position_id": 771314830,
        }]
        return compare(real, tester, manual_pairs=normalize_manual_pairs({
            tester_pair_key(tester[index]): pair for index, pair in pairs.items()
        }))

    def test_without_saying_anything_the_auditor_keeps_its_pair(self) -> None:
        rows = self.rows({})
        self.assertEqual(rows[0]["real"]["strategy"], "771314830")
        self.assertEqual(rows[1]["status"], "missing")

    def test_declaring_a_row_without_real_frees_the_one_it_had(self) -> None:
        # `#T19` dice que no tiene real, así que la real se va con `#T21`: la
        # fila liberada vuelve al reparto en vez de quedarse sin usar.
        rows = self.rows({0: {"position_id": "", "note": "no la operó"}})
        self.assertEqual(rows[0]["status"], "missing")
        self.assertTrue(rows[0]["manual_unpaired"])
        self.assertEqual(rows[0]["manual_pair_note"], "no la operó")
        self.assertEqual(rows[1]["real"]["strategy"], "771314830")

    def test_both_rows_can_say_it_and_the_real_stays_unused(self) -> None:
        rows = self.rows({0: {"position_id": ""}, 1: {"position_id": ""}})
        self.assertEqual([row["status"] for row in rows], ["missing", "missing"])

    def test_the_note_travels_with_a_forced_pair_too(self) -> None:
        rows = self.rows({1: {"position_id": "771314830", "note": "lo dice el Excel"}})
        self.assertEqual(rows[1]["real"]["strategy"], "771314830")
        self.assertTrue(rows[1]["manual_pair"])
        self.assertFalse(rows[1]["manual_unpaired"])
        self.assertEqual(rows[1]["manual_pair_note"], "lo dice el Excel")
        self.assertEqual(rows[0]["status"], "missing")

    def test_the_fingerprints_declared_without_real_are_the_ones_said(self) -> None:
        self.assertEqual(
            unpaired_keys({"a|b|sell|1|2": {"position_id": ""},
                           "c|d|buy|1|2": {"position_id": "771314830"}}),
            frozenset({"a|b|sell|1|2"}),
        )


class ShippedTemplateTests(unittest.TestCase):
    def test_the_template_we_hand_over_is_one_the_importer_reads(self) -> None:
        # Una plantilla que el importador no lee es peor que no darla: el
        # usuario rellena el fichero equivocado y el error aparece al subirlo.
        path = Path(__file__).parents[2] / "docs" / "plantilla_enlace_manual.xlsx"
        result = import_pair_workbook({
            "filename": path.name,
            "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
        self.assertEqual(result["rows"]["19"]["ticket"], "771314830")
        self.assertEqual(result["rows"]["21"]["ticket"], "")


class PairSheetScreenTests(unittest.TestCase):
    def test_the_screen_offers_the_button_and_posts_the_pairs(self) -> None:
        screen = (STATIC / "live_audit_analysis.js").read_text(encoding="utf-8")
        markup = (STATIC / "live_audit_analysis.html").read_text(encoding="utf-8")
        self.assertIn('id="pairs-file"', markup)
        self.assertIn("live-audit-pairs-import", screen)
        # Corrige al auditor, así que **no** puede exigir que la fila sea
        # «SIN REAL» como hace el importador de la atribución.
        self.assertIn("manualPairsFromLinkSheet", screen)
        self.assertNotIn(
            "status !== 'missing'", screen.split("function manualPairsFromLinkSheet")[1],
        )


if __name__ == "__main__":
    unittest.main()
