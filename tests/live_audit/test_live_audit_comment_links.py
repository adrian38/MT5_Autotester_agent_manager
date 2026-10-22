"""Enlazar las reales por el comentario que el EA escribió en la orden.

En AXI el EA escribe en el comentario de la orden de apertura un código
—`TESLA_M15_006`— que nombra el `.set` que la abrió. No es una conjetura como
el reparto por tiempo y lote, ni trabajo a mano como el Excel de atribución: lo
escribió el programa que operó.

Aquí se comprueban las cuatro mitades: lo que el lector saca del `.docx`, lo que
el módulo valida, lo que el análisis hace con la tabla guardada y quién puede
subirla.
"""
from __future__ import annotations

import base64
import io
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from mt5_manager.live_audit_analysis import analyze
from mt5_manager.live_audit_comment_links import (
    MAX_COMMENT_LINKS,
    comment_links_supported,
    import_document,
    links_by_comment,
    normalize_comment_links,
)
from tests.test_live_audit_attribution import (
    MEMBER, SET_STEM, STRATEGY, audit_payload, profile, trade,
)
from tests.test_live_audit_attribution import imported as excel

STATIC = Path(__file__).parents[2] / "mt5_manager" / "static"
COMMENT = "TESLA_M15_006"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def document_bytes(rows: list[tuple[str, str]], header: bool = True) -> bytes:
    """Un `.docx` con la tabla de dos columnas del documento del usuario."""
    body = "".join(
        "<w:tr>" + "".join(
            f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row
        ) + "</w:tr>"
        for row in ([("Nombre del setfile", "EA_Comment")] if header else []) + rows
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}">'
        f"<w:body><w:tbl>{body}</w:tbl></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def imported(rows: list[tuple[str, str]], **changes) -> dict:
    return import_document({
        "filename": "estrategia_comentario_tesla.docx",
        "content_base64": base64.b64encode(document_bytes(rows, **changes)).decode("ascii"),
    })


class DocumentReadingTests(unittest.TestCase):
    def test_the_two_columns_become_comment_to_setfile(self) -> None:
        links = imported([(f"{SET_STEM}.set", COMMENT)])
        self.assertEqual(links["rules"], {COMMENT.casefold(): f"{SET_STEM}.set"})
        self.assertEqual(links["source_name"], "estrategia_comentario_tesla.docx")
        self.assertEqual(len(links["source_sha256"]), 64)

    def test_the_cell_decides_which_column_is_the_setfile(self) -> None:
        # Invertir las columnas enlazaría cada comentario a un `.set` que no
        # existe, y el error sería silencioso: la tabla diría que no enlaza
        # nada. La celda que nombra un `.set` se reconoce sola.
        self.assertEqual(
            imported([(COMMENT, f"{SET_STEM}.set")])["rules"],
            imported([(f"{SET_STEM}.set", COMMENT)])["rules"],
        )

    def test_the_header_row_is_not_a_rule(self) -> None:
        self.assertEqual(len(imported([(f"{SET_STEM}.set", COMMENT)])["rules"]), 1)

    def test_the_comment_is_compared_without_case_or_spare_spaces(self) -> None:
        # MT5 recorta el comentario y el usuario lo escribe en mayúsculas.
        links = imported([(f"{SET_STEM}.set", "  tesla m15 006  ")])
        self.assertEqual(list(links["rules"]), ["tesla m15 006"])

    def test_a_document_without_the_table_says_what_se_espera(self) -> None:
        with self.assertRaises(ValueError) as raised:
            imported([])
        self.assertIn("dos columnas", str(raised.exception))

    def test_what_is_not_a_docx_is_refused(self) -> None:
        for broken in (
            {"filename": "tabla.xlsx", "content_base64": ""},
            {"filename": "tabla.docx", "content_base64": "no-es-base64!"},
            {"filename": "tabla.docx", "content_base64": ""},
            {"filename": "tabla.docx",
             "content_base64": base64.b64encode(b"esto no es un zip").decode("ascii")},
        ):
            with self.assertRaises(ValueError):
                import_document(broken)


class NormalizationTests(unittest.TestCase):
    def test_nothing_saved_is_an_empty_table(self) -> None:
        self.assertEqual(
            normalize_comment_links(None),
            {"source_name": "", "source_sha256": "", "rules": {}},
        )

    def test_a_comment_without_setfile_is_refused(self) -> None:
        for broken in ({"rules": {COMMENT: ""}}, {"rules": {"": "x.set"}},
                       {"rules": {"c" * 200: "x.set"}}, {"inventado": 1},
                       {"source_sha256": "no-es-una-huella"}):
            with self.assertRaises(ValueError):
                normalize_comment_links(broken)
        with self.assertRaises(ValueError):
            normalize_comment_links(
                {"rules": {f"c{index}": "x.set" for index in range(MAX_COMMENT_LINKS + 1)}},
            )

    def test_a_setfile_outside_this_portfolio_is_dropped_not_excluded(self) -> None:
        # La tabla es una sola para todos los usos: nombrar el `.set` de otro
        # portafolio no dice nada de éste, y marcarlo como ajeno sacaría de la
        # curva reales que sí son suyas.
        links = imported([(f"{SET_STEM}.set", COMMENT), ("de_otro_portafolio.set", "X_1")])
        resolved = links_by_comment(links, [MEMBER])
        self.assertEqual(list(resolved), [COMMENT.casefold()])
        self.assertEqual(resolved[COMMENT.casefold()]["strategy"], STRATEGY)


class BrokerTests(unittest.TestCase):
    def test_the_field_is_the_one_the_node_record_carries(self) -> None:
        # `manager.json` guarda `portfolio_broker`; el `broker` a secas sólo
        # aparece en lo que el agente dice de sí mismo y no llega hasta aquí.
        # Mirar sólo a `broker` dejaba el botón oculto también en AXI, y en
        # silencio: una capacidad que no se anuncia no se echa de menos.
        self.assertTrue(comment_links_supported({
            "id": "axi-standard-192-168-1-152", "name": "AXI Standard",
            "portfolio_broker": "AXI", "portfolio_account_type": "STANDARD",
        }))

    def test_only_axi_writes_the_setfile_in_the_comment(self) -> None:
        self.assertTrue(comment_links_supported({"broker": "AXI"}))
        self.assertTrue(comment_links_supported({"portfolio_broker": " axi "}))
        for other in (
            {"portfolio_broker": "ICTRADING"}, {"portfolio_broker": "ROBOFOREX"},
            {"broker": "ROBOFOREX"}, {"id": "axi-standard-192-168-1-152"}, {}, None,
        ):
            self.assertFalse(comment_links_supported(other))


def commented_payload() -> dict:
    """Una real con comentario que ninguna fila del tester reclamó, y una que sí.

    El `audit-109` de `Christmas_Axi` reducido: la real huérfana abre un día
    antes que la operación del tester, fuera de la tolerancia de 120 s, así que
    el reparto no las junta y sólo el comentario las ata.
    """
    base = audit_payload()
    missed = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)
    base["tester_trades"] = [
        {**row, "strategy": STRATEGY} for row in base["tester_trades"]
    ] + [{**trade(missed, 0.02, 7.0, 2), "strategy": STRATEGY}]
    base["real_trades"] = [
        {**row, "comment": COMMENT if row["position_id"] == 111 else "[sl 4133.78]"}
        for row in base["real_trades"]
    ]
    base["period_end"] = "2026-09-24T23:59:59+00:00"
    base["request"]["period_end_date"] = "2026-09-24"
    base["strategies"] = {STRATEGY: 2}
    return base


class AnalysisTests(unittest.TestCase):
    def analysed(self, **changes) -> dict:
        links = imported([(f"{SET_STEM}.set", COMMENT)])
        return analyze(commented_payload(), profile(comment_links=links, **changes))

    def test_the_comment_names_the_owner_of_a_real_without_a_pair(self) -> None:
        detail = analyze(
            commented_payload(), profile(),
        )["comparison_detail"]
        self.assertNotIn("attribution", detail["unmatched_real_operations"][0])
        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")

    def test_with_the_table_it_pairs_the_row_that_had_no_real(self) -> None:
        detail = self.analysed()["comparison_detail"]
        row = detail["operation_comparisons"][1]
        self.assertEqual(str(row["real"]["position_id"]), "111")
        # La fila dice de qué prueba salió: el EA, no el Excel.
        self.assertEqual(row["attributed_pair"], "comment")
        self.assertEqual(detail["attributed_pairs"], 1)
        self.assertEqual(detail["unmatched_real_operations"], [])

    def test_it_does_not_loosen_any_tolerance(self) -> None:
        # La real abrió un día antes y perdió 11,00 contra los 7,00 del tester.
        result = self.analysed()
        row = result["comparison_detail"]["operation_comparisons"][1]
        self.assertEqual(row["status"], "deviation")
        self.assertIn("open_time", row["reasons"])
        self.assertEqual(result["missing_real_trades"], 0)

    def test_it_never_pairs_a_real_of_the_opposite_side(self) -> None:
        # `_pair_cost` exige mismo lado, así que la fila emparejada no vuelve a
        # compararlo: un `buy` contra un `sell` salía DESVIACIÓN con desviación
        # de tiempo y PnL, como si fuera la misma operación medida con holgura,
        # y metía en la curva Real el resultado de la operación contraria.
        base = commented_payload()
        base["tester_trades"][1] = {**base["tester_trades"][1], "side": "buy"}
        links = imported([(f"{SET_STEM}.set", COMMENT)])
        detail = analyze(base, profile(comment_links=links))["comparison_detail"]
        row = detail["operation_comparisons"][1]
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["reasons"], ["no_real_same_symbol_and_side"])
        self.assertEqual(detail["attributed_pairs"], 0)

    def test_a_comment_that_no_rule_names_leaves_the_real_as_it_was(self) -> None:
        links = imported([(f"{SET_STEM}.set", "TESLA_M15_999")])
        detail = analyze(
            commented_payload(), profile(comment_links=links),
        )["comparison_detail"]
        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")
        self.assertEqual(detail["attributed_pairs"], 0)

    def test_the_comment_wins_over_an_excel_that_says_otherwise(self) -> None:
        # El comentario lo escribió el programa que operó; el Excel lo tecleó
        # el usuario cuando no tenía esa señal. Así, subir el documento retira
        # solo lo viejo que lo contradiga.
        mistaken = excel([
            ("XAUUSD · 0,02 lotes; sell; Sin estrategia del tester; magic 111; ticket 111",
             "no contabiliza en este portafolio"),
        ])
        detail = self.analysed(real_attribution=mistaken)["comparison_detail"]
        self.assertEqual(detail["operation_comparisons"][1]["attributed_pair"], "comment")
        self.assertEqual(detail["attributed_pairs"], 1)
        # Y sin el documento, el Excel sigue mandando: la real queda excluida.
        alone = analyze(
            commented_payload(), profile(real_attribution=mistaken),
        )["comparison_detail"]
        self.assertTrue(alone["unmatched_real_operations"][0]["attribution"]["excluded"])

    def test_the_table_travels_in_the_analysis_so_the_screen_can_show_it(self) -> None:
        analysis = self.analysed()["analysis"]
        self.assertEqual(list(analysis["tolerances"]["comment_links"]["rules"]), [COMMENT.casefold()])
        self.assertFalse(analysis["comment_links_supported"])
        self.assertTrue(
            analyze(commented_payload(), profile(_comment_links_supported=True))
            ["analysis"]["comment_links_supported"],
        )


class ScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = (STATIC / "live_audit_analysis.html").read_text(encoding="utf-8")
        self.script = (STATIC / "live_audit_analysis.js").read_text(encoding="utf-8")
        self.result = (STATIC / "live_audit_result.js").read_text(encoding="utf-8")

    def test_the_button_is_born_hidden_and_only_axi_uncovers_it(self) -> None:
        # Enseñarlo en otro broker ofrecería un criterio que ese EA no escribe
        # y que el manager rechazaría al guardar.
        self.assertIn('id="comment-links-upload"', self.page)
        self.assertRegex(self.page, r'id="comment-links-upload"[^>]*hidden')
        self.assertIn("comment_links_supported", self.script)

    def test_the_upload_goes_to_the_route_and_saves_the_criterion(self) -> None:
        self.assertIn("live-audit-comment-links-import", self.script)
        self.assertIn("changeCriterion('comment-links'", self.script)

    def test_the_row_does_not_present_the_two_sources_alike(self) -> None:
        self.assertIn("comentario del EA", self.result)
        self.assertIn("Excel de atribución", self.result)


if __name__ == "__main__":
    unittest.main()
