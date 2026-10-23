"""Emparejar a mano una operación del tester con una real, de punta a punta.

El auditor empareja por símbolo, lado, lote y tiempo, y antes de eso el filtro
de pertenencia aparta las reales cuyo lote no cae en la banda del portafolio.
Cuando las dos reglas fallan, la fila sale «SIN REAL» con su real delante, en
la tabla de descartadas: en `audit-170` de RoboForex pasaba en tres de las
cuatro filas sin pareja.

Aquí se comprueban las cuatro mitades: lo que el módulo valida, lo que el
análisis hace con una pareja forzada, lo que la ruta contesta y lo que la
pantalla de Resultado ofrece.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mt5_manager.live_audit_analysis import analyze
from mt5_manager.live_audit_manual_pairs import (
    MAX_MANUAL_PAIRS,
    forced_assignment,
    normalize_manual_pairs,
    real_pair_id,
    rescued_position_ids,
    tester_pair_key,
)
from mt5_manager.live_audit_settings import LiveAuditSettingsStore
from mt5_manager.manager_live_audit_routes import handle_post
from tests.test_live_audit_analysis import payload, profile
from tests.test_live_audit_settings import profile as stored_profile
from tests.test_live_audit_view_lots import RouteHandler

STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"
TICKET = "772550823"


SET_NAME = "EURUSD_H1_Advanced_Scalper_g001_s027_v010.set"
STRATEGY = "ROBOFOREX/ECN:27672"


def discarded_payload() -> dict:
    """Una real que el filtro de lote aparta y ninguna operación reclama.

    Reproduce el `.JP225Cash` de `audit-170`: el portafolio lleva 0,08 y la
    cuenta operó 0,06, fuera de la banda, y además abrió 400 segundos tarde, de
    modo que la pareja no la puede encontrar ni el reparto ni el rescate.
    """
    base = payload()
    tester = dict(base["tester_trades"][0])
    opened = datetime.fromisoformat(tester["open_time"]) + timedelta(seconds=400)
    closed = datetime.fromisoformat(tester["close_time"]) + timedelta(seconds=400)
    real = {
        **tester, "volume": 0.06, "profit": 2.1, "position_id": int(TICKET),
        "open_time": opened.isoformat(), "close_time": closed.isoformat(),
    }
    return payload(
        real_trades=[real],
        selected_members=[{
            "symbol": "EURUSD", "lot": 0.08, "candidate_id": STRATEGY,
            "set_name": SET_NAME,
        }],
    )


# La regla que el selector aplica, portada de `offerableTo` de
# `live_audit_result.js`. El port sólo vale si la expresión que mide está
# literalmente en el fichero, y eso lo exige
# `test_the_ported_offer_rule_is_the_one_the_screen_runs`.
def offerable_to(row: dict, strategy: str) -> bool:
    attribution = row.get("attribution")
    if not attribution:
        return True
    if attribution.get("excluded"):
        return False
    return str(attribution.get("strategy") or "") == strategy


def analysed(pairs: dict | None = None) -> dict:
    return analyze(discarded_payload(), profile(manual_pairs=pairs or {}))


def manual_pair_for(trade: dict, ticket: str = TICKET) -> dict:
    return {tester_pair_key(trade): {"position_id": ticket, "at": "2026-10-19T06:40:00+00:00"}}


class ManualPairNormalizationTests(unittest.TestCase):
    def test_a_real_cannot_be_the_pair_of_two_tester_operations(self) -> None:
        # Dejar que lo fuera repartiría el mismo PnL real dos veces y la curva
        # de la cuenta diría más de lo que la cuenta hizo.
        with self.assertRaises(ValueError) as raised:
            normalize_manual_pairs({"a|b|sell|1|2": TICKET, "c|d|buy|3|4": TICKET})
        self.assertIn(TICKET, str(raised.exception))

    def test_a_bare_ticket_is_accepted_and_normalised(self) -> None:
        self.assertEqual(
            normalize_manual_pairs({"a|b|sell|1|2": TICKET}),
            {"a|b|sell|1|2": {"position_id": TICKET, "at": ""}},
        )

    def test_nothing_saved_is_an_empty_map(self) -> None:
        self.assertEqual(normalize_manual_pairs(None), {})

    def test_an_empty_ticket_says_the_row_has_no_real(self) -> None:
        # No es un dato que falte: es la decisión de que esa fila no tiene real,
        # la única forma de deshacer un emparejado de más del auditor.
        self.assertEqual(
            normalize_manual_pairs({"a|b|sell|1|2": {"position_id": "", "note": "no la operó"}}),
            {"a|b|sell|1|2": {"position_id": "", "at": "", "note": "no la operó"}},
        )
        # Y varias filas pueden decirlo: no reclaman ninguna real.
        self.assertEqual(
            len(normalize_manual_pairs({"a|b|sell|1|2": "", "c|d|buy|1|2": ""})), 2,
        )

    def test_what_does_not_name_a_real_operation_is_refused(self) -> None:
        for broken in ({"a|b|sell|1|2": "no-es-un-ticket"},
                       {"": TICKET}, {"a\nb": TICKET}):
            with self.assertRaises(ValueError):
                normalize_manual_pairs(broken)
        with self.assertRaises(ValueError):
            normalize_manual_pairs({f"k{index}|b|sell|1|2": str(index)
                                    for index in range(MAX_MANUAL_PAIRS + 1)})


class TesterFingerprintTests(unittest.TestCase):
    def test_the_fingerprint_does_not_depend_on_the_position_in_the_list(self) -> None:
        # El `#T6` de la pantalla se mueve en cuanto la auditoría se repite; la
        # huella la componen la estrategia, el símbolo, el lado y los instantes.
        trade = payload()["tester_trades"][0]
        self.assertEqual(
            tester_pair_key(trade),
            "ROBOFOREX/ECN:27672|EURUSD|sell"
            "|2026-09-22T09:19:46+00:00|2026-09-22T10:32:58+00:00",
        )

    def test_the_fingerprint_is_the_same_before_and_after_the_json_trip(self) -> None:
        # El análisis revive los instantes a `datetime` y la pantalla los lee en
        # texto: si las dos formas no dieran la misma huella, una pareja
        # guardada desde el navegador no la encontraría nadie.
        trade = payload()["tester_trades"][0]
        revived = {
            **trade,
            "open_time": datetime(2026, 9, 22, 9, 19, 46, tzinfo=timezone.utc),
            "close_time": datetime(2026, 9, 22, 10, 32, 58, tzinfo=timezone.utc),
        }
        self.assertEqual(tester_pair_key(revived), tester_pair_key(trade))

    def test_a_real_is_named_by_its_position_id(self) -> None:
        self.assertEqual(real_pair_id({"position_id": 759105555}), "759105555")
        self.assertEqual(real_pair_id({"ticket": 46430179}), "46430179")
        self.assertEqual(real_pair_id({}), "")

    def test_a_pair_saved_over_another_execution_is_ignored_instead_of_failing(self) -> None:
        # Una ejecución nueva puede no tener ya esa operación. Eso no es motivo
        # para dejar sin análisis a las demás filas.
        tester = payload()["tester_trades"][0]
        self.assertEqual(
            forced_assignment([tester], [], manual_pair_for(tester)), {},
        )
        self.assertEqual(
            forced_assignment([tester], [{"position_id": 1}],
                              {"otra|huella|buy|1|2": {"position_id": "1"}}), {},
        )


class ManualPairAnalysisTests(unittest.TestCase):
    def test_without_the_pair_the_row_has_no_real_and_the_lot_filter_keeps_it(self) -> None:
        result = analysed()
        row = result["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["reasons"], ["no_real_same_symbol_and_side"])
        self.assertEqual(
            [str(extra["real"]["position_id"])
             for extra in result["comparison_detail"]["discarded_real_operations"]],
            [TICKET],
        )

    def test_the_pair_rescues_the_discarded_real_and_measures_it_as_any_other(self) -> None:
        # No afloja ninguna tolerancia: la real llevaba 0,06 contra 0,08 y abrió
        # 400 s tarde, así que la fila sale con esas desviaciones en vez de
        # desaparecer del recuento.
        tester = discarded_payload()["tester_trades"][0]
        result = analyze(discarded_payload(), profile(manual_pairs=manual_pair_for(tester)))
        row = result["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(row["status"], "deviation")
        self.assertTrue(row["manual_pair"])
        self.assertEqual(str(row["real"]["position_id"]), TICKET)
        self.assertEqual(
            sorted(row["reasons"]), ["close_time", "open_time", "pnl", "volume"],
        )
        self.assertEqual(result["missing_real_trades"], 0)
        self.assertEqual(result["portfolio_filter"]["manually_paired_kept"], 1)
        self.assertEqual(result["comparison_detail"]["discarded_real_operations"], [])

    def test_the_methodology_says_how_many_pairs_the_auditor_did_not_find(self) -> None:
        # Sin esta línea el informe presenta como alineación medida algo que en
        # parte decidió una persona.
        tester = discarded_payload()["tester_trades"][0]
        detail = analyze(
            discarded_payload(), profile(manual_pairs=manual_pair_for(tester)),
        )["comparison_detail"]
        self.assertEqual(detail["manual_pairs"], 1)
        self.assertIn("las decidió el usuario a mano", detail["methodology"]["alignment"])
        self.assertEqual(analysed()["comparison_detail"]["manual_pairs"], 0)

    def test_a_forced_pair_consumes_its_real_before_any_cost(self) -> None:
        # La real forzada no se vuelve a repartir: el reparto global la ve ya
        # tomada aunque fuese la mejor candidata de otra fila.
        base = discarded_payload()
        tester = base["tester_trades"][0]
        second = {**tester, "strategy": "ROBOFOREX/ECN:99999"}
        forced = forced_assignment(
            [second, tester], base["real_trades"], manual_pair_for(tester),
        )
        self.assertEqual(forced, {1: 0})

    def test_the_rescue_only_covers_what_someone_claims(self) -> None:
        self.assertEqual(rescued_position_ids(None), set())
        self.assertEqual(
            rescued_position_ids({"k": {"position_id": TICKET}}), {TICKET},
        )


class ManualPairOfferTests(unittest.TestCase):
    """Lo que el usuario ya dijo por Excel manda sobre lo que se le ofrece.

    En `audit-170` había repartido por Excel ocho de las nueve reales libres,
    así que la fila EURUSD de `ROBOFOREX/ECN:32807` llegaba a ofrecer los
    USDJPY y los AUDUSD que él mismo había dado a otras estrategias.
    """

    def attributed_analysis(self, strategy_set: str) -> dict:
        return analyze(discarded_payload(), profile(real_attribution={
            "rules": {TICKET: {"strategy_set": strategy_set, "reason": ""}},
        }))

    def test_a_discarded_real_carries_what_the_excel_said_about_it(self) -> None:
        # La mitad no evidente: la atribución anota también las descartadas por
        # banda de lote, que es de donde salen casi todas las candidatas.
        detail = self.attributed_analysis(SET_NAME)["comparison_detail"]
        row = detail["discarded_real_operations"][0]
        self.assertEqual(row["attribution"]["strategy"], STRATEGY)
        self.assertFalse(row["attribution"]["excluded"])

    def test_a_real_already_given_to_this_strategy_is_offered(self) -> None:
        detail = self.attributed_analysis(SET_NAME)["comparison_detail"]
        row = detail["discarded_real_operations"][0]
        self.assertTrue(offerable_to(row, STRATEGY))

    def test_a_real_given_to_another_strategy_is_not_offered(self) -> None:
        detail = self.attributed_analysis(SET_NAME)["comparison_detail"]
        row = detail["discarded_real_operations"][0]
        self.assertFalse(offerable_to(row, "ROBOFOREX/ECN:99999"))

    def test_a_real_declared_foreign_is_never_offered(self) -> None:
        # «no contabiliza en este portafolio»: ningún miembro reclama ese
        # nombre, así que `attribution_by_ticket` la deja excluida.
        detail = self.attributed_analysis("no contabiliza en este portafolio")
        row = detail["comparison_detail"]["discarded_real_operations"][0]
        self.assertTrue(row["attribution"]["excluded"])
        for strategy in (STRATEGY, "", "ROBOFOREX/ECN:99999"):
            self.assertFalse(offerable_to(row, strategy))

    def test_a_real_nobody_claimed_stays_offered_to_any_row(self) -> None:
        row = analysed()["comparison_detail"]["discarded_real_operations"][0]
        self.assertIsNone(row.get("attribution"))
        self.assertTrue(offerable_to(row, "ROBOFOREX/ECN:99999"))


class ManualPairRouteTests(unittest.TestCase):
    """Guardar y reanalizar son un solo viaje, igual que la atribución."""

    PATH = ["api", "nodes", "nodo", "live-audits", "real-a", "manual-pairs"]

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = LiveAuditSettingsStore(Path(temp.name) / "live_audit_settings.json")
        self.store.update("nodo", {
            "selected_audit_ids": ["real-a"],
            "profiles": {"real-a": {
                **stored_profile("111", "911"), "portfolio_id": 11,
                "portfolio_type": "balanced",
                "source_password": "real", "tester_password": "test",
            }},
        })

    def post(self, body: object) -> tuple[int, object]:
        handler = RouteHandler(self.store, body)
        raw = {"audit": {"last_payload": discarded_payload()}}
        with patch("mt5_manager.manager_http.node_request", return_value=(200, raw)):
            self.assertTrue(handle_post(handler, None, self.PATH))
        return handler.sent

    def post_attribution(self, body: object) -> tuple[int, object]:
        handler = RouteHandler(self.store, body)
        raw = {"audit": {"last_payload": discarded_payload()}}
        path = ["api", "nodes", "nodo", "live-audits", "real-a", "attribution"]
        with patch("mt5_manager.manager_http.node_request", return_value=(200, raw)):
            self.assertTrue(handle_post(handler, None, path))
        return handler.sent

    def test_saving_a_pair_answers_with_the_verdict_already_recalculated(self) -> None:
        tester = discarded_payload()["tester_trades"][0]
        status, body = self.post({"manual_pairs": manual_pair_for(tester)})
        self.assertEqual(status, 200)
        row = body["audit"]["last_result"]["comparison_detail"]["operation_comparisons"][0]
        self.assertTrue(row["manual_pair"])
        self.assertEqual(
            self.store.manual_pairs("nodo", "real-a"),
            {tester_pair_key(tester): {"position_id": TICKET, "at": "2026-10-19T06:40:00+00:00"}},
        )

    def test_undoing_is_sending_the_map_without_that_pair(self) -> None:
        # Sustituir y no acumular, al revés que la atribución: la pantalla
        # tiene el mapa entero delante.
        tester = discarded_payload()["tester_trades"][0]
        self.post({"manual_pairs": manual_pair_for(tester)})
        status, body = self.post({"manual_pairs": {}})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.manual_pairs("nodo", "real-a"), {})
        self.assertEqual(
            body["audit"]["last_result"]["comparison_detail"]
            ["operation_comparisons"][0]["status"], "missing",
        )

    def test_a_pair_that_claims_a_taken_real_is_refused_without_saving(self) -> None:
        status, body = self.post({"manual_pairs": {"a|b|sell|1|2": TICKET, "c|d|buy|3|4": TICKET}})
        self.assertEqual(status, 400)
        self.assertIn(TICKET, body["error"])
        self.assertEqual(self.store.manual_pairs("nodo", "real-a"), {})

    def test_the_excel_saves_its_attribution_and_pair_in_the_same_trip(self) -> None:
        tester = discarded_payload()["tester_trades"][0]
        attribution = {
            "source_name": "auditoria.xlsx", "source_sha256": "a" * 64,
            "rules": {TICKET: {"strategy_set": SET_NAME, "reason": ""}},
        }
        status, body = self.post_attribution({
            "real_attribution": attribution,
            "manual_pairs": manual_pair_for(tester),
        })
        self.assertEqual(status, 200)
        row = body["audit"]["last_result"]["comparison_detail"]["operation_comparisons"][0]
        self.assertTrue(row["manual_pair"])
        profile = self.store.state("nodo")["profiles"]["real-a"]
        self.assertEqual(profile["manual_pairs"], manual_pair_for(tester))
        self.assertIn(TICKET, profile["real_attribution"]["rules"])


class ManualPairScreenTests(unittest.TestCase):
    """No hay runtime de JavaScript aquí: se comprueba el texto del fichero."""

    def setUp(self) -> None:
        self.source = (STATIC / "live_audit_result.js").read_text(encoding="utf-8")

    def test_the_screen_builds_the_same_fingerprint_as_the_manager(self) -> None:
        # Si las dos expresiones se separan, la pareja se guarda con una huella
        # que el análisis no encuentra y la pantalla no cambia de veredicto sin
        # que nada lo diga.
        self.assertIn(
            "[tester.strategy, tester.symbol, tester.side, tester.open_time, tester.close_time]",
            self.source,
        )
        self.assertIn(".map(value => String(value || '')).join('|')", self.source)

    def test_the_picker_offers_the_free_and_the_discarded_reals(self) -> None:
        self.assertIn("detail.unmatched_real_operations", self.source)
        self.assertIn("detail.discarded_real_operations", self.source)
        self.assertIn("data-manual-pair=", self.source)
        self.assertIn("data-undo-pair=", self.source)

    def test_the_ported_offer_rule_is_the_one_the_screen_runs(self) -> None:
        # `offerable_to` de este fichero mide la regla; si la pantalla la
        # reescribe, esta prueba se cae en vez de medir algo que ya no existe.
        self.assertIn(
            "if (attribution.excluded) return {state: 'excluded', strategy: ''};",
            self.source,
        )
        self.assertIn(
            "return owner.state === 'free'\n"
            "    || (owner.state === 'attributed' && owner.strategy === strategy);",
            self.source,
        )
        self.assertIn(".filter(candidate => offerableTo(candidate, strategy))", self.source)

    def test_the_row_says_why_it_has_nothing_to_offer(self) -> None:
        # Un desplegable vacío sin motivo se lee como «no hay ninguna real»,
        # que es falso cuando las hay y están atribuidas a otra estrategia.
        self.assertIn("ya están atribuidas a otra estrategia o excluidas", self.source)
        self.assertIn("ya atribuida a esta estrategia", self.source)

    def test_saving_and_repainting_are_the_same_call(self) -> None:
        self.assertIn("/manual-pairs", self.source)
        self.assertIn("JSON.stringify({manual_pairs: pairs})", self.source)

    def test_a_forced_pair_is_said_in_the_row_and_in_the_csv(self) -> None:
        self.assertIn("Pareja decidida a mano", self.source)
        self.assertIn("' · pareja decidida a mano'", self.source)


if __name__ == "__main__":
    unittest.main()
