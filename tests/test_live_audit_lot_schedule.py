"""Subir el lote a mano a mitad del periodo, de punta a punta.

El caso que lo motivó (2026-10-20): una auditoría de AXI con una sola
estrategia en `XAUUSD`, lote 0,01 en el tester y 0,03 configurado en la cuenta
real. El usuario subió el lote a 0,04 y el cierre del 16-10 salió
`DESCARTADA POR LOTE`: «0,04 lotes queda fuera de lo que el portafolio opera en
XAUUSD (0,01 a 0,03)».

Las dos salidas que había eran malas. Atribuirlo por Excel es trabajo por
operación y para siempre. Subir el lote configurado a 0,04 ensancha la banda
para **todo** el periodo y, peor, convierte en desviadas por volumen a todas
las operaciones anteriores al cambio, que ejecutaron el lote de antes.

Un tramo dice las dos cosas: desde esa fecha el lote es 0,04, y antes seguía
siendo 0,03. Aquí se comprueban las cuatro mitades: lo que el módulo valida, lo
que el análisis hace con el tramo, lo que la ruta contesta y lo que la pantalla
ofrece.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mt5_manager.live_audit_analysis import analyze, portfolio_lot_bands
from mt5_manager.live_audit_autolot import expected_volume
from mt5_manager.live_audit_lot_schedule import (
    MAX_SCHEDULED_STEPS,
    normalize_lot_schedule,
    scheduled_lot,
    scheduled_lots,
    scheduled_steps,
)
from mt5_manager.live_audit_settings import LiveAuditSettingsStore
from mt5_manager.manager_live_audit_routes import handle_post
from tests.test_live_audit_analysis import profile
from tests.test_live_audit_settings import profile as stored_profile
from tests.test_live_audit_view_lots import RouteHandler

STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"
STRATEGY = "AXI/MT5:3758"
SET_NAME = "ScalpH1_C.set"
# El día en que la cuenta pasó a operar 0,04, y la operación que lo estrenó.
RAISED_AT = datetime(2026, 10, 16, 18, 52, 36, tzinfo=timezone.utc)
BEFORE = datetime(2026, 10, 12, 9, 19, 46, tzinfo=timezone.utc)


def _trade(opened: datetime, volume: float, profit: float, **extra) -> dict:
    return {
        "strategy": STRATEGY, "symbol": "XAUUSD", "side": "buy",
        "open_time": opened.isoformat(),
        "close_time": (opened + timedelta(minutes=33)).isoformat(),
        "open_price": 4218.55, "close_price": 4221.10,
        "volume": volume, "profit": profit, **extra,
    }


def raised_lot_payload() -> dict:
    """Dos operaciones de la misma estrategia, una a cada lado del cambio.

    La primera la ejecutó la cuenta a 0,03, que es el lote configurado. La
    segunda a 0,04, porque el usuario lo subió a mano ese día.
    """
    return {
        "audit_id": "20261017_030000_000001",
        "completed_at": "2026-10-17T03:00:00+00:00",
        "period_start": "2026-10-10T00:00:00+00:00",
        "period_end": "2026-10-17T23:59:59+00:00",
        "request": {
            "audit_key": "audit-109", "portfolio_id": 109, "portfolio_type": "balanced",
            "period_mode": "fixed_dates", "period_days": 7,
            "period_start_date": "2026-10-10", "period_end_date": "2026-10-17",
        },
        "tester_trades": [_trade(BEFORE, 0.01, 1.0), _trade(RAISED_AT, 0.01, 1.0)],
        "real_trades": [
            _trade(BEFORE, 0.03, 3.0, position_id=772100001),
            _trade(RAISED_AT, 0.04, 4.0, position_id=772365651),
        ],
        "symbol_points": {"XAUUSD": 0.01},
        "strategies": {STRATEGY: 2},
        "qualities": [100.0],
        "strategy_artifacts": [],
        "selected_members": [{
            "symbol": "XAUUSD", "lot": 0.01, "candidate_id": STRATEGY, "set_name": SET_NAME,
        }],
        "volume_rules": {},
    }


def schedule(lot: float = 0.04, moment: datetime = RAISED_AT) -> dict:
    return {STRATEGY: [{"from": moment.isoformat(), "lot": lot}]}


def analysed(**overrides) -> dict:
    return analyze(raised_lot_payload(), profile(
        real_strategy_lots={STRATEGY: 0.03}, **overrides,
    ))


def rows(result: dict) -> list[dict]:
    return result["comparison_detail"]["operation_comparisons"]


class LotScheduleNormalizationTests(unittest.TestCase):
    def test_a_step_is_a_lot_and_the_date_from_which_it_rules(self) -> None:
        self.assertEqual(
            normalize_lot_schedule(schedule()),
            {STRATEGY: [{"from": RAISED_AT.isoformat(), "lot": 0.04, "at": ""}]},
        )

    def test_a_date_without_a_zone_is_read_as_utc(self) -> None:
        # El nodo publica las aperturas con `+00:00` y el HTML nativo las
        # imprime sin sufijo. Python se niega a comparar las dos formas, así
        # que normalizarlas aquí es lo que evita reventar al elegir el tramo.
        naive = normalize_lot_schedule({STRATEGY: [{"from": "2026-10-16T18:52:36", "lot": 0.04}]})
        self.assertEqual(naive[STRATEGY][0]["from"], RAISED_AT.isoformat())

    def test_the_steps_come_out_sorted_whatever_order_they_were_given(self) -> None:
        # Quien elige el vigente se queda con el último de la lista: sin este
        # orden daría el lote de otro periodo sin que nada lo dijera.
        unsorted = {STRATEGY: [
            {"from": RAISED_AT.isoformat(), "lot": 0.04},
            {"from": BEFORE.isoformat(), "lot": 0.02},
        ]}
        self.assertEqual(
            [step["lot"] for step in normalize_lot_schedule(unsorted)[STRATEGY]], [0.02, 0.04],
        )

    def test_nothing_saved_is_an_empty_map(self) -> None:
        self.assertEqual(normalize_lot_schedule(None), {})
        self.assertEqual(normalize_lot_schedule({STRATEGY: []}), {})

    def test_a_step_without_date_or_lot_is_refused(self) -> None:
        for broken in (
            {STRATEGY: [{"lot": 0.04}]},
            {STRATEGY: [{"from": "no es una fecha", "lot": 0.04}]},
            {STRATEGY: [{"from": RAISED_AT.isoformat()}]},
            {STRATEGY: [{"from": RAISED_AT.isoformat(), "lot": 0}]},
            {"": [{"from": RAISED_AT.isoformat(), "lot": 0.04}]},
            {STRATEGY: [{"from": RAISED_AT.isoformat(), "lot": 0.04}] * (MAX_SCHEDULED_STEPS + 1)},
        ):
            with self.assertRaises(ValueError):
                normalize_lot_schedule(broken)

    def test_the_lot_in_force_is_the_last_one_declared_before_that_instant(self) -> None:
        stored = normalize_lot_schedule(schedule())
        self.assertEqual(scheduled_lot(stored, STRATEGY, RAISED_AT), 0.04)
        self.assertEqual(scheduled_lot(stored, STRATEGY, RAISED_AT + timedelta(days=3)), 0.04)
        # Antes del cambio no hay tramo: mandaba el lote configurado de
        # siempre, y devolver el nuevo marcaría como desviado lo que la cuenta
        # ejecutó bien.
        self.assertIsNone(scheduled_lot(stored, STRATEGY, BEFORE))
        self.assertIsNone(scheduled_lot(stored, "otra", RAISED_AT))
        self.assertIsNone(scheduled_lot(stored, STRATEGY, None))

    def test_the_band_needs_every_lot_and_the_verdict_only_the_one_in_force(self) -> None:
        stored = normalize_lot_schedule({STRATEGY: [
            {"from": BEFORE.isoformat(), "lot": 0.02},
            {"from": RAISED_AT.isoformat(), "lot": 0.04},
        ]})
        self.assertEqual(scheduled_lots(stored, STRATEGY), [0.02, 0.04])
        self.assertEqual(scheduled_lots(stored, "otra"), [])
        self.assertEqual(scheduled_steps(stored), 2)


class LotScheduleExpectedVolumeTests(unittest.TestCase):
    def test_each_operation_expects_the_lot_of_its_own_moment(self) -> None:
        request = {
            "real_strategy_lots": {STRATEGY: 0.03},
            "real_lot_schedule": normalize_lot_schedule(schedule()),
        }
        before, after = (
            expected_volume({"strategy": STRATEGY, "symbol": "XAUUSD", "volume": 0.01,
                             "open_time": moment}, request)
            for moment in (BEFORE, RAISED_AT)
        )
        self.assertEqual((before["expected"], before["source"]), (0.03, "configured_real_lot"))
        self.assertEqual((after["expected"], after["source"]), (0.04, "real_lot_schedule"))

    def test_the_real_opening_decides_the_step_when_there_is_a_pair(self) -> None:
        # Igual que el autolote: el lote esperado se lee en el instante de la
        # apertura **real**, que es la que la cuenta ejecutó.
        request = {"real_lot_schedule": normalize_lot_schedule(schedule())}
        detail = expected_volume(
            {"strategy": STRATEGY, "symbol": "XAUUSD", "volume": 0.01, "open_time": BEFORE},
            request, {"open_time": RAISED_AT},
        )
        self.assertEqual(detail["expected"], 0.04)

    def test_the_autolot_still_wins_where_it_has_a_rule(self) -> None:
        # El autolote calcula el lote con el saldo de la cuenta; un tramo lo
        # declara una persona. Donde hay fórmula, manda la fórmula.
        request = {
            "autolot": {"enabled": True},
            "_autolot_rules_by_strategy": {STRATEGY: {"risk_pct": 7.0, "dd_001": 135.0}},
            "_autolot_balance_points": [(BEFORE, 1632.51)],
            "real_lot_schedule": normalize_lot_schedule(schedule()),
        }
        detail = expected_volume(
            {"strategy": STRATEGY, "symbol": "XAUUSD", "volume": 0.01, "open_time": RAISED_AT},
            request,
        )
        self.assertEqual(detail["source"], "autolot")


class LotScheduleAnalysisTests(unittest.TestCase):
    def test_without_the_step_the_raised_lot_is_discarded_before_comparing(self) -> None:
        result = analysed()
        self.assertEqual(result["portfolio_filter"]["lot_bands"]["xauusd"], [0.01, 0.03])
        discarded = result["comparison_detail"]["discarded_real_operations"]
        self.assertEqual([str(row["real"]["position_id"]) for row in discarded], ["772365651"])
        self.assertEqual(discarded[0]["portfolio_lot_band"], [0.01, 0.03])
        self.assertEqual([row["status"] for row in rows(result)], ["matched", "missing"])

    def test_the_step_lets_it_through_and_measures_it_as_any_other(self) -> None:
        result = analysed(real_lot_schedule=schedule())
        self.assertEqual(result["portfolio_filter"]["lot_bands"]["xauusd"], [0.01, 0.04])
        self.assertEqual(result["comparison_detail"]["discarded_real_operations"], [])
        self.assertEqual([row["status"] for row in rows(result)], ["matched", "matched"])
        self.assertEqual(
            [row["limits"]["volume_expected_real"] for row in rows(result)], [0.03, 0.04],
        )
        self.assertEqual(
            [row["limits"]["volume_expected_source"] for row in rows(result)],
            ["configured_real_lot", "real_lot_schedule"],
        )

    def test_raising_the_configured_lot_instead_breaks_what_came_before(self) -> None:
        # Ésta es la alternativa que el tramo existe para evitar. Admite el
        # cierre nuevo, sí, pero pasa a esperar 0,04 también en la operación
        # anterior al cambio, que la cuenta ejecutó correctamente a 0,03.
        result = analyze(raised_lot_payload(), profile(real_strategy_lots={STRATEGY: 0.04}))
        self.assertEqual([row["status"] for row in rows(result)], ["deviation", "matched"])
        self.assertIn("volume", rows(result)[0]["reasons"])

    def test_an_operation_with_that_lot_before_the_change_stays_out(self) -> None:
        # El tramo no es una lista de lotes admitidos: es desde cuándo rige.
        # Lo que la banda deja pasar lo mide después la comparación, y una
        # operación anterior al cambio esperaba el lote de antes.
        result = analysed(real_lot_schedule=schedule(moment=RAISED_AT + timedelta(days=1)))
        self.assertEqual([row["status"] for row in rows(result)], ["matched", "deviation"])
        self.assertIn("volume", rows(result)[1]["reasons"])

    def test_the_methodology_declares_the_steps_instead_of_lying(self) -> None:
        # Sin esto el veredicto describe un criterio que no usó: diría que
        # comparó contra «el lote real configurado» a secas.
        plain = analysed()["comparison_detail"]["methodology"]
        self.assertEqual(plain["tolerances"]["volume_basis"], "configured_real_lot")
        self.assertEqual(plain["tolerances"]["real_lot_schedule_steps"], 0)
        detail = analysed(real_lot_schedule=schedule())["comparison_detail"]["methodology"]
        self.assertEqual(detail["tolerances"]["volume_basis"], "real_lot_schedule")
        self.assertEqual(detail["tolerances"]["real_lot_schedule_steps"], 1)
        self.assertIn("tramo(s) de lote declarados a mano", detail["validation"])

    def test_the_band_takes_every_declared_lot_and_not_only_the_last(self) -> None:
        # La banda es un prefiltro del periodo entero y no sabe de qué instante
        # es cada cierre: si sólo admitiera el último lote, apartaría los
        # cierres anteriores al cambio antes de poder compararlos.
        bands = portfolio_lot_bands(
            [{"symbol": "XAUUSD", "lot": 0.01, "candidate_id": STRATEGY}],
            {}, {STRATEGY: {"xauusd"}}, {STRATEGY: 0.03},
            {STRATEGY: [{"from": RAISED_AT.isoformat(), "lot": 0.04}]},
        )
        self.assertEqual(bands["xauusd"], (0.01, 0.04))


class LotScheduleRouteTests(unittest.TestCase):
    PATH = ["api", "nodes", "nodo", "live-audits", "audit-109", "real-lot-schedule"]

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = LiveAuditSettingsStore(Path(self.directory.name) / "live_audits.json")
        self.store.update("nodo", {
            "selected_audit_ids": ["audit-109"],
            "profiles": {"audit-109": {
                **stored_profile("50001", "50002"), "portfolio_id": 109,
                "portfolio_type": "balanced",
                "source_password": "real", "tester_password": "test",
                "real_strategy_lots": {STRATEGY: 0.03},
            }},
        })

    def post(self, body: object) -> tuple[int, object]:
        handler = RouteHandler(self.store, body)
        raw = {"audit": {"last_payload": raised_lot_payload()}}
        with patch("mt5_manager.manager_http.node_request", return_value=(200, raw)):
            self.assertTrue(handle_post(handler, None, self.PATH))
        return handler.sent

    def test_the_store_writes_that_field_and_only_that_field(self) -> None:
        # Quien declara el tramo está mirando el resultado y no conoce el resto
        # de la configuración del uso: un guardado completo borraría lo que esa
        # pantalla no tenía que enviar.
        before = self.store.state("nodo")["profiles"]["audit-109"]
        self.store.set_real_lot_schedule("nodo", "audit-109", schedule())
        after = self.store.state("nodo")["profiles"]["audit-109"]
        self.assertEqual(self.store.real_lot_schedule("nodo", "audit-109")[STRATEGY][0]["lot"], 0.04)
        self.assertEqual(
            {key: value for key, value in after.items() if key != "real_lot_schedule"},
            {key: value for key, value in before.items() if key != "real_lot_schedule"},
        )

    def test_the_store_refuses_a_use_it_does_not_know(self) -> None:
        with self.assertRaises(KeyError):
            self.store.set_real_lot_schedule("nodo", "sin-configurar", schedule())

    def test_saving_and_repainting_are_the_same_trip(self) -> None:
        # El criterio vive en el manager y se aplica al leer, así que guardado
        # sin repintar o repintado sin guardar son dos medias verdades: la
        # respuesta trae ya la real rescatada y emparejada.
        status, body = self.post({"real_lot_schedule": schedule()})
        self.assertEqual(status, 200)
        self.assertEqual(
            self.store.real_lot_schedule("nodo", "audit-109")[STRATEGY][0]["lot"], 0.04,
        )
        detail = body["audit"]["last_result"]["comparison_detail"]
        self.assertEqual([row["status"] for row in detail["operation_comparisons"]],
                         ["matched", "matched"])
        self.assertEqual(detail["discarded_real_operations"], [])

    def test_undoing_is_sending_the_map_without_that_step(self) -> None:
        # Sustituir y no acumular: la fecha la pone un botón, así que declarar
        # el tramo sobre la operación equivocada es el error fácil.
        self.post({"real_lot_schedule": schedule()})
        status, body = self.post({"real_lot_schedule": {}})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.real_lot_schedule("nodo", "audit-109"), {})
        self.assertEqual(
            body["audit"]["last_result"]["comparison_detail"]
            ["operation_comparisons"][1]["status"], "missing",
        )

    def test_a_broken_step_is_refused_and_nothing_is_saved(self) -> None:
        status, body = self.post({"real_lot_schedule": {STRATEGY: [{"lot": 0.04}]}})
        self.assertEqual(status, 400)
        self.assertIn("fecha de inicio", body["error"])
        self.assertEqual(self.store.real_lot_schedule("nodo", "audit-109"), {})


class LotScheduleScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (STATIC / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.page = (STATIC / "live_audit_analysis.html").read_text(encoding="utf-8")

    def test_the_date_is_the_opening_of_that_very_operation(self) -> None:
        # Teclearla invita a equivocarse por un día y a dejar fuera justo la
        # operación que se quería recuperar.
        self.assertIn('data-schedule-from="${escapeHtml(row.real.open_time)}"', self.script)
        self.assertIn('data-schedule-lot="${escapeHtml(lot)}"', self.script)
        self.assertIn("Desde aquí opero", self.script)

    def test_what_the_button_carries_is_the_raw_value_and_not_the_formatted_one(self) -> None:
        # El botón se pintaba y al pulsarlo no hacía nada: el atributo llevaba
        # `number(lot, 8)`, que en es-ES da «0,04», y `Number('0,04')` es NaN,
        # así que el oyente salía por la guarda antes de preguntar nada. Un
        # `data-*` que se relee como número no puede pasar por el formateador.
        for attribute in re.findall(r'data-schedule-[a-z]+="([^"]*)"', self.script):
            self.assertNotIn("number(", attribute)
        handler = self.script.split("const button = event.target.closest('[data-schedule-lot]')", 1)[1]
        self.assertIn("Number(button.dataset.scheduleLot)", handler)

    def test_the_offer_only_appears_on_a_real_the_lot_filter_discarded(self) -> None:
        self.assertIn("${discarded ? scheduleControl(row) : ''}", self.script)

    def test_who_owns_the_lot_is_not_guessed_when_several_could(self) -> None:
        # La banda va por símbolo justamente porque el magic del terminal real
        # no identifica al miembro: con dos estrategias en el mismo símbolo hay
        # que preguntar, y lo que el Excel ya dijo manda sobre todo.
        self.assertIn("function scheduleCandidates(row)", self.script)
        self.assertIn("const named = attributionOf(row).strategy;", self.script)
        self.assertIn("candidates.length > 1", self.script)
        self.assertIn("data-schedule-pick", self.script)

    def test_a_step_declared_by_mistake_can_be_taken_back(self) -> None:
        # Acumular sin poder deshacer es una calle de un solo sentido, y la
        # fecha la pone un botón: equivocarse de operación es fácil.
        self.assertIn('<div id="lot-schedule-sources"', self.page)
        self.assertIn("function renderLotSchedule()", self.script)
        self.assertIn("data-remove-step=", self.script)

    def test_the_dialog_shows_every_lot_the_account_ran_without_cutting_it(self) -> None:
        # Declarado el tramo, la estrategia pasa a tener dos lotes ejecutados y
        # el campo los enseña como marcador. Con el «varios:» que llevaba
        # delante no cabían en 150px y salía «varios: 0,03 · 0,0», cortado a
        # media cifra. La lista sola mantiene la estructura con dos lotes y con
        # quince, porque `lotList` resume por encima de tres.
        self.assertIn(": executed.length ? lotList(executed) : 'sin operaciones'", self.script)
        # El literal que llevaba el prefijo, no la palabra: el comentario que
        # explica por qué se quitó tiene que poder nombrarlo.
        self.assertNotIn("`varios: ${lotList(executed)}`", self.script)
        css = (STATIC / "live_audit_analysis.css").read_text(encoding="utf-8")
        self.assertIn(".lots-table input { width: 100%; max-width: 230px; }", css)

    def test_the_screen_goes_through_the_endpoint_that_recalculates(self) -> None:
        self.assertIn("changeCriterion('real-lot-schedule'", self.script)
        self.assertIn("real_lot_schedule: schedule", self.script)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
