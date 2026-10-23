from __future__ import annotations

import base64
import io
import unittest
import zipfile
from datetime import datetime, timezone

from mt5_manager.live_audit_analysis import analyze
from mt5_manager.live_audit_attribution import (
    attribution_by_ticket,
    attribution_sources,
    import_workbook,
    import_workbook_bundle,
    merge_attribution,
    normalize_attribution,
    remove_source,
    split_reason,
)
from mt5_manager.live_audit_settings_schema import normalize_live_audit_settings


STRATEGY = "ROBOFOREX/ECN:4426"
SET_STEM = "XAUUSD_H4_GOLD_XAUUSD_H4_GOLD_fee5a2f9_g002_s021_v006"
MEMBER = {
    "candidate_id": STRATEGY, "symbol": "XAUUSD", "lot": 0.02,
    "set_name": f"{SET_STEM}.set", "set_id": f"/runs/gen_002/{SET_STEM}.set",
}


def workbook_bytes(
    rows: list[tuple[str, str]], last_header: str = "Razon",
    row_ids: list[str] | None = None, statuses: list[str] | None = None,
) -> bytes:
    """Un libro con la forma de la exportación del detalle de operaciones.

    El ticket viaja dentro de la celda de mercado, que es como lo imprime la
    pantalla (`...; magic 123; ticket 123`), y la columna de la derecha es la
    que el usuario añade a mano.
    """
    shared = ["#", "Estado", "Mercado", last_header]
    for market, reason in rows:
        shared.extend([market, reason])
    strings = "".join(f"<si><t>{value}</t></si>" for value in shared)
    cells = [
        '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2" t="s"><v>1</v></c>'
        '<c r="C2" t="s"><v>2</v></c><c r="D2" t="s"><v>3</v></c></row>',
    ]
    for index, _row in enumerate(rows):
        line = index + 3
        market_index, reason_index = 4 + index * 2, 5 + index * 2
        row_id = row_ids[index] if row_ids else f"R{index + 1}"
        status = statuses[index] if statuses else "REAL SIN TESTER"
        cells.append(
            f'<row r="{line}"><c r="A{line}" t="str"><v>{row_id}</v></c>'
            f'<c r="B{line}" t="str"><v>{status}</v></c>'
            f'<c r="C{line}" t="s"><v>{market_index}</v></c>'
            f'<c r="D{line}" t="s"><v>{reason_index}</v></c></row>'
        )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""")
        archive.writestr("xl/_rels/workbook.xml.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>""")
        archive.writestr("xl/sharedStrings.xml", f"<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">{strings}</sst>")
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def imported(rows: list[tuple[str, str]], last_header: str = "Razon") -> dict[str, object]:
    contents = workbook_bytes(rows, last_header)
    return import_workbook({
        "filename": "auditor.xlsx",
        "content_base64": base64.b64encode(contents).decode("ascii"),
    })


EXPORTED_ROWS = [
    (f"XAUUSD · 0,02 lotes; sell; Sin estrategia del tester; magic 111; ticket 111", SET_STEM),
    ("GOOGL · 0,01 lotes; buy; Sin estrategia del tester; magic 222; ticket 222",
     "no contabiliza en este portafolio"),
]


class WorkbookTests(unittest.TestCase):
    def test_the_exported_detail_plus_a_column_is_all_that_is_needed(self) -> None:
        result = imported(EXPORTED_ROWS)
        self.assertEqual(sorted(result["rules"]), ["111", "222"])
        self.assertEqual(result["rules"]["111"]["strategy_set"], SET_STEM)
        self.assertEqual(result["source_name"], "auditor.xlsx")
        self.assertEqual(len(result["source_sha256"]), 64)

    def test_a_note_before_the_semicolon_is_kept_and_the_set_is_the_last_piece(self) -> None:
        # El fichero que originó esto traía «duplicado, gestionada por mi; <set>».
        self.assertEqual(split_reason(f"duplicado, gestionada por mi;{SET_STEM}"),
                         (SET_STEM, "duplicado, gestionada por mi"))
        self.assertEqual(split_reason("no contabiliza en este portafolio"),
                         ("no contabiliza en este portafolio", ""))

    def test_the_ticket_is_read_even_when_the_column_is_written_by_hand(self) -> None:
        # `auditor_p105.xlsx`: la columna añadida decía «ticket real
        # correspondiente: 46430179» y el patrón exigía el número pegado a la
        # palabra, así que el Excel se rechazaba entero por no traer tickets.
        rows = [
            ("BTCUSD · 0,09 lotes; sell; BTCUSD_H1_Bitcoin_f4f272a8_g001_s004_v002.set; AXI/STANDARD:21169",
             "ticket real correspondiente: 46430179"),
            ("Tesla+ · 5 lotes; buy; Tesla_M30_Bitcoin_37f12147_g001_s003_v004.set; AXI/STANDARD:32134",
             "ticket real correspondiente: 46568182"),
        ]
        # Sin cabecera en la columna añadida, que es como sale de exportar y
        # escribir el ticket a la derecha.
        result = imported(rows, last_header="")
        self.assertEqual(sorted(result["rules"]), ["46430179", "46568182"])
        # Y la estrategia sale de la columna que nombra el `.set`, no de la que
        # el usuario añadió con el ticket: antes se tomaba la última con texto y
        # la «estrategia» acababa siendo «ticket real correspondiente: …».
        self.assertEqual(result["rules"]["46430179"]["strategy_set"], "AXI/STANDARD:21169")
        self.assertEqual(result["rules"]["46568182"]["strategy_set"], "AXI/STANDARD:32134")

    def test_a_ticket_written_on_a_missing_tester_row_is_also_kept_as_a_pair(self) -> None:
        rows = [
            ("EURUSD · 0,08 lotes; buy; EURUSD_H1.set; ROBOFOREX/ECN:32807",
             "ticket real correspondiente: 771329182"),
            ("EURUSD · 0,08 lotes; buy; EURUSD_H1.set; ROBOFOREX/ECN:32807",
             "ticket real correspondiente: 769643751"),
        ]
        contents = workbook_bytes(
            rows, last_header="", row_ids=["T12", "T13"], statuses=["SIN REAL", "SIN REAL"],
        )
        result = import_workbook_bundle({
            "filename": "auditoria_p170.xlsx",
            "content_base64": base64.b64encode(contents).decode("ascii"),
        })
        self.assertEqual(result["manual_pair_rows"], {
            "12": "771329182", "13": "769643751",
        })
        self.assertEqual(sorted(result["real_attribution"]["rules"]), ["769643751", "771329182"])

    def test_an_already_paired_tester_row_is_not_turned_into_a_manual_pair(self) -> None:
        contents = workbook_bytes(
            [("EURUSD · ticket 771329182; ROBOFOREX/ECN:32807", SET_STEM)],
            row_ids=["T12"], statuses=["DENTRO"],
        )
        result = import_workbook_bundle({
            "filename": "auditoria.xlsx",
            "content_base64": base64.b64encode(contents).decode("ascii"),
        })
        self.assertEqual(result["manual_pair_rows"], {})

    def test_a_hand_written_note_still_wins_over_the_set_of_the_exported_row(self) -> None:
        # La celda de mercado de una fila emparejada también nombra su `.set`.
        # Preferir siempre «la columna que parece una estrategia» pisaba lo que
        # el usuario había escrito: sólo se busca ahí cuando lo añadido a la
        # derecha es el ticket, porque entonces la estrategia no está ahí.
        exported = (
            "Tesla+ · 5 lotes; buy; Tesla_M30_Bitcoin_37f12147_g001_s003_v004.set; "
            "AXI/STANDARD:32134; ticket 46568182"
        )
        note = "esta no es del portafolio, la abrí a mano"
        result = imported([(exported, note)], last_header="")
        self.assertEqual(result["rules"]["46568182"]["strategy_set"], note)

    def test_the_ticket_and_the_set_may_share_the_added_cell(self) -> None:
        result = imported(
            [("XAUUSD · 0,02 lotes; sell; sin pareja", f"ticket 46000002; {SET_STEM}.set")],
            last_header="",
        )
        self.assertEqual(result["rules"]["46000002"]["strategy_set"], f"{SET_STEM}.set")

    def test_a_number_that_is_not_a_ticket_does_not_pass_as_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "ticket"):
            imported([("XAUUSD · 0,02 lotes; sell; sin pareja", "ticket pendiente, 2 intentos")])

    def test_a_file_without_tickets_says_what_falta(self) -> None:
        with self.assertRaisesRegex(ValueError, "ticket"):
            imported([("XAUUSD · 0,02 lotes; sell; sin identificador", SET_STEM)])

    def test_the_stored_attribution_is_validated(self) -> None:
        configured = normalize_live_audit_settings({
            "real_attribution": {
                "source_name": "auditor.xlsx", "source_sha256": "a" * 64,
                "rules": {"111": {"strategy_set": SET_STEM, "reason": ""}},
            },
        })
        self.assertEqual(configured["real_attribution"]["rules"]["111"]["strategy_set"], SET_STEM)
        with self.assertRaisesRegex(ValueError, "ticket"):
            normalize_attribution({"rules": {"no-es-un-ticket": {"strategy_set": SET_STEM}}})


class MergeTests(unittest.TestCase):
    def test_a_second_excel_adds_to_the_first_instead_of_replacing_it(self) -> None:
        # Decidir de quién es cada real es trabajo manual que no se repite:
        # subir el Excel de la auditoría siguiente no puede borrar lo anterior.
        first = imported(EXPORTED_ROWS)
        second = imported([
            ("XAUUSD · 0,02 lotes; buy; Sin estrategia del tester; magic 333; ticket 333",
             SET_STEM),
        ])
        merged = merge_attribution(first, second)
        self.assertEqual(sorted(merged["rules"]), ["111", "222", "333"])
        self.assertEqual(merged["source_name"], "auditor.xlsx")

    def test_the_same_ticket_keeps_the_last_word(self) -> None:
        first = imported(EXPORTED_ROWS)
        corrected = imported([
            ("XAUUSD · 0,02 lotes; sell; Sin estrategia del tester; magic 111; ticket 111",
             "no contabiliza en este portafolio"),
        ])
        merged = merge_attribution(first, corrected)
        self.assertEqual(
            merged["rules"]["111"]["strategy_set"], "no contabiliza en este portafolio",
        )
        self.assertEqual(sorted(merged["rules"]), ["111", "222"])

    def test_merging_over_nothing_keeps_the_incoming_rules_and_stamps_their_file(self) -> None:
        first = imported(EXPORTED_ROWS)
        merged = merge_attribution(None, first)
        self.assertEqual(sorted(merged["rules"]), sorted(first["rules"]))
        # Cada regla recuerda de qué Excel salió: sin eso, acumular sería una
        # calle de un solo sentido y no se podría retirar un fichero concreto.
        self.assertTrue(all(
            rule["source"] == first["source_sha256"] for rule in merged["rules"].values()
        ))
        self.assertEqual(merged["sources"][first["source_sha256"]]["name"], "auditor.xlsx")

    def test_a_file_can_be_removed_without_losing_the_others(self) -> None:
        # Como lo acumula el almacén: cada subida pasa por `merge_attribution`,
        # que es donde cada regla queda sellada con su fichero.
        first = merge_attribution(None, imported(EXPORTED_ROWS))
        stored = merge_attribution(first, {
            "source_name": "correcciones.xlsx", "source_sha256": "b" * 64,
            "rules": {"333": {"strategy_set": SET_STEM, "reason": ""}},
        })
        self.assertEqual(len(stored["rules"]), 3)
        left = remove_source(stored, "b" * 64)
        self.assertEqual(sorted(left["rules"]), ["111", "222"])
        self.assertNotIn("b" * 64, left["sources"])
        self.assertEqual(
            [source["name"] for source in attribution_sources(left)], ["auditor.xlsx"],
        )

    def test_removing_a_file_that_was_never_uploaded_says_so(self) -> None:
        with self.assertRaisesRegex(ValueError, "no está entre los subidos"):
            remove_source(imported(EXPORTED_ROWS), "c" * 64)


class BindingTests(unittest.TestCase):
    def test_the_set_name_resolves_to_the_candidate_id_of_the_member(self) -> None:
        resolved = attribution_by_ticket(imported(EXPORTED_ROWS), [MEMBER])
        self.assertEqual(resolved["111"]["strategy"], STRATEGY)
        self.assertFalse(resolved["111"]["excluded"])

    def test_text_that_names_no_member_excludes_the_operation_with_its_reason(self) -> None:
        resolved = attribution_by_ticket(imported(EXPORTED_ROWS), [MEMBER])
        self.assertTrue(resolved["222"]["excluded"])
        self.assertEqual(resolved["222"]["strategy"], "")
        self.assertEqual(resolved["222"]["reason"], "no contabiliza en este portafolio")


def trade(opened: datetime, volume: float, profit: float, position: int) -> dict[str, object]:
    return {
        "strategy": str(position), "symbol": "XAUUSD", "side": "sell",
        "open_time": opened.isoformat(),
        "close_time": opened.replace(minute=30).isoformat(),
        "open_price": 4300.0, "close_price": 4290.0, "volume": volume,
        "profit": profit, "position_id": position,
    }


def audit_payload() -> dict[str, object]:
    paired = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
    orphan = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
    return {
        "audit_id": "run-attribution", "completed_at": "2026-09-23T12:00:00+00:00",
        "period_start": "2026-09-22T00:00:00+00:00", "period_end": "2026-09-23T23:59:59+00:00",
        "request": {
            "audit_key": "audit-148", "portfolio_id": 148, "portfolio_type": "aggressive",
            "period_mode": "fixed_dates", "period_days": 2,
            "period_start_date": "2026-09-22", "period_end_date": "2026-09-23",
        },
        "tester_trades": [trade(paired, 0.02, 5.0, 1)],
        "real_trades": [trade(paired, 0.02, 4.0, 900), trade(orphan, 0.02, -11.0, 111)],
        "symbol_points": {"XAUUSD": 0.01}, "strategies": {STRATEGY: 1},
        "qualities": [100.0], "strategy_artifacts": [], "selected_members": [MEMBER],
        "volume_rules": {"xauusd": {"volume_min": 0.01, "volume_step": 0.01}},
        "account": {}, "real_history_detail": {}, "tester_execution": {},
        "real_account_report": {}, "terminal_restore": [], "open_positions_at_period_end": [],
    }


def profile(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "trade_time_tolerance_seconds": 120, "price_tolerance_points": 15000.0,
        "volume_tolerance_pct": 5.0, "pnl_deviation_warning_pct": 50.0,
        "drawdown_deviation_warning_pct": 1000.0, "min_tick_history_quality_pct": 80.0,
        "real_strategy_lots": {},
    }
    values.update(changes)
    return values


class AnalysisTests(unittest.TestCase):
    def test_an_unmatched_real_is_annotated_without_inventing_a_pair(self) -> None:
        attribution = imported([
            ("XAUUSD · 0,02 lotes; sell; Sin estrategia del tester; magic 111; ticket 111",
             SET_STEM),
        ])
        result = analyze(audit_payload(), profile(real_attribution=attribution))
        detail = result["comparison_detail"]
        extras = detail["unmatched_real_operations"]
        self.assertEqual(len(extras), 1)
        # Sigue siendo una real sin pareja: el emparejado del auditor no cambia.
        self.assertEqual(extras[0]["status"], "extra")
        self.assertEqual(result["extra_real_trades"], 1)
        self.assertEqual(extras[0]["attribution"]["strategy"], STRATEGY)
        self.assertFalse(extras[0]["attribution"]["excluded"])
        self.assertEqual(detail["real_attribution"], {
            "attributed": 1, "excluded": 0, "unresolved": 0,
            "attributed_profit": -11.0, "excluded_profit": 0.0,
        })

    def test_without_the_excel_nothing_is_annotated(self) -> None:
        detail = analyze(audit_payload(), profile())["comparison_detail"]
        self.assertNotIn("attribution", detail["unmatched_real_operations"][0])
        self.assertEqual(detail["real_attribution"]["unresolved"], 1)


def unpaired_payload() -> dict[str, object]:
    """Una fila del tester sin pareja y una real libre atribuida a su estrategia.

    Es el `#T6` de `Christmas_Axi` reducido: el tester cierra el 24 y la real
    abrió el 23, un día fuera de la tolerancia de 120 s, así que el reparto no
    las junta. El Excel ya dijo que esa real es de esa estrategia.
    """
    base = audit_payload()
    missed = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)
    base["tester_trades"] = [
        {**row, "strategy": STRATEGY} for row in base["tester_trades"]
    ] + [{**trade(missed, 0.02, 7.0, 2), "strategy": STRATEGY}]
    base["period_end"] = "2026-09-24T23:59:59+00:00"
    base["request"]["period_end_date"] = "2026-09-24"
    base["strategies"] = {STRATEGY: 2}
    return base


ATTRIBUTED_ORPHAN = [
    ("XAUUSD · 0,02 lotes; sell; Sin estrategia del tester; magic 111; ticket 111", SET_STEM),
]


class AttributedPairTests(unittest.TestCase):
    """Lo que el Excel atribuyó a una estrategia que además tiene filas sin pareja.

    Atribuir una real es nombrar a su dueño. Si ese dueño tiene encima una
    operación del tester «SIN REAL», volver a pedir la correspondencia en el
    selector es pedir dos veces el mismo trabajo, y la portada entretanto
    cuenta la fila como trabajo pendiente.
    """

    def analysed(self, **changes: object) -> dict:
        attribution = imported(ATTRIBUTED_ORPHAN)
        return analyze(
            unpaired_payload(), profile(real_attribution=attribution, **changes),
        )

    def test_the_attributed_real_becomes_the_pair_of_the_row_without_one(self) -> None:
        detail = self.analysed()["comparison_detail"]
        row = detail["operation_comparisons"][1]
        self.assertEqual(str(row["real"]["position_id"]), "111")
        self.assertTrue(row["attributed_pair"])
        # No es una pareja «a mano»: nadie la eligió en la pantalla.
        self.assertNotIn("manual_pair", row)
        self.assertEqual(detail["unmatched_real_operations"], [])

    def test_it_does_not_loosen_any_tolerance(self) -> None:
        # La real abrió un día antes y perdió 11,00 contra los 7,00 del tester:
        # la fila sale con esas desviaciones, no en verde.
        result = self.analysed()
        row = result["comparison_detail"]["operation_comparisons"][1]
        self.assertEqual(row["status"], "deviation")
        self.assertIn("open_time", row["reasons"])
        self.assertEqual(result["missing_real_trades"], 0)
        self.assertEqual(result["extra_real_trades"], 0)

    def test_the_methodology_separates_the_two_sources_of_a_forced_pair(self) -> None:
        detail = self.analysed()["comparison_detail"]
        self.assertEqual(detail["attributed_pairs"], 1)
        self.assertEqual(detail["manual_pairs"], 0)
        self.assertIn("salen del Excel de atribución", detail["methodology"]["alignment"])
        self.assertNotIn("a mano", detail["methodology"]["alignment"])

    def test_a_real_declared_foreign_is_never_paired(self) -> None:
        # «No contabiliza en este portafolio» la deja excluida: emparejarla
        # metería en la curva justo lo que el usuario sacó de ella.
        foreign = imported([(ATTRIBUTED_ORPHAN[0][0], "no contabiliza en este portafolio")])
        detail = analyze(
            unpaired_payload(), profile(real_attribution=foreign),
        )["comparison_detail"]
        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")
        self.assertEqual(detail["attributed_pairs"], 0)

    def test_an_attribution_to_another_strategy_is_not_offered_either(self) -> None:
        other = imported([(ATTRIBUTED_ORPHAN[0][0], "otro_set_que_no_es_del_portafolio")])
        detail = analyze(
            unpaired_payload(), profile(real_attribution=other),
        )["comparison_detail"]
        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")
        self.assertEqual(detail["attributed_pairs"], 0)

    def test_it_never_pairs_a_real_of_the_opposite_side(self) -> None:
        # El símbolo no filtra —las cuentas pueden nombrarlo distinto— pero el
        # lado sí: `_pair_cost` lo exige, así que la fila emparejada no vuelve
        # a compararlo y un `buy` contra un `sell` habría salido DESVIACIÓN sin
        # que ningún motivo dijera que son operaciones contrarias.
        base = unpaired_payload()
        base["tester_trades"][1] = {**base["tester_trades"][1], "side": "buy"}
        detail = analyze(
            base, profile(real_attribution=imported(ATTRIBUTED_ORPHAN)),
        )["comparison_detail"]
        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")
        self.assertEqual(detail["attributed_pairs"], 0)

    def test_a_real_that_opened_before_the_window_is_never_paired(self) -> None:
        # RoboForex `audit-173`: `769632478` está en la materia prima porque
        # cerró dentro del periodo, pero abrió tres días antes y su operación
        # del tester es la del backtest anterior. Emparejada con `#T19` salía
        # con desviación de apertura, cierre, precio, lote y PnL, y metía sus
        # −17,02 € en la curva Real de la estrategia.
        base = unpaired_payload()
        before = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
        base["real_trades"] = base["real_trades"][:-1] + [
            {**trade(before, 0.02, -11.0, 111),
             "close_time": datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc).isoformat()},
        ]
        detail = analyze(
            base, profile(real_attribution=imported(ATTRIBUTED_ORPHAN)),
        )["comparison_detail"]

        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")
        self.assertEqual(detail["attributed_pairs"], 0)
        # Sigue contando como real de esa estrategia, que es lo que dijo el
        # Excel: lo que no es es la pareja de una fila de esta ventana.
        self.assertEqual(len(detail["unmatched_real_operations"]), 1)
        self.assertEqual(
            detail["unmatched_real_operations"][0]["attribution"]["strategy"], STRATEGY,
        )

    def test_without_the_excel_the_row_keeps_having_no_real(self) -> None:
        detail = analyze(unpaired_payload(), profile())["comparison_detail"]
        self.assertEqual(detail["operation_comparisons"][1]["status"], "missing")
        self.assertEqual(detail["attributed_pairs"], 0)
        self.assertEqual(len(detail["unmatched_real_operations"]), 1)


if __name__ == "__main__":
    unittest.main()
