"""Las ejecutadas en demo sin real que el usuario ya miró, una a una.

La portada avisa de dos cosas bajo «Operaciones sin enlazar»: las reales que
nadie explicó y las del tester que la cuenta no ejecutó. Para callar una de las
segundas sólo había un gesto, validar la estrategia entera en Análisis, y es
demasiado grueso: en `audit-109` de AXI eso ya calla veintidós de sus
veintitrés filas SIN REAL, por el veredicto de la estrategia y no porque nadie
las haya abierto.

Ahora la fila «SIN REAL» de la pantalla de operaciones lleva su propia casilla.
No es `manual_pairs`, que le pone real a la fila y la vuelve a medir: ésta sólo
dice «ya miré ésta» y lo único que cambia es que la portada deja de pedirla.

Aquí se comprueban las tres mitades: lo que el almacén persiste, lo que la ruta
contesta y lo que las dos pantallas hacen con ello.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_manual_pairs import tester_pair_key
from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)
from mt5_manager.manager_live_audit_routes import handle_get, handle_post
from tests.test_live_audit_settings import profile as stored_profile
from tests.test_live_audit_view_lots import RouteHandler

STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"

TRADE = {
    "strategy": "eth-grid", "symbol": "ETHUSD", "side": "buy",
    "open_time": "2026-10-19T06:00:00+00:00",
    "close_time": "2026-10-19T07:30:00+00:00",
}


def unlinked_tester(rows: list[dict], reviewed: set[str], validated: dict) -> int:
    """El recuento de la portada, portado desde `dashboard_metrics.js`.

    El port sólo vale si cada expresión que mide está literalmente en el
    fichero, y eso es lo que exige `test_the_ported_count_is_the_one_the_card_runs`.
    """
    return len([
        row for row in rows
        if row.get("status") == "missing"
        and str(row.get("strategy") or "Sin estrategia") not in reviewed
        and not validated.get(tester_pair_key(row.get("tester") or {}))
    ])


class ReviewedUnpairedTesterStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "live_audit_settings.json"
        self.store = LiveAuditSettingsStore(self.path)
        self.store.update("nodo", {
            "selected_audit_ids": ["real-a"],
            "profiles": {"real-a": {
                **stored_profile("111", "911"), "portfolio_id": 11,
                "portfolio_type": "balanced",
                "source_password": "real", "tester_password": "test",
            }},
        })

    def test_a_validated_row_is_saved_per_use_and_survives_a_reload(self) -> None:
        # Vive en el perfil y no en el navegador, igual que las estrategias
        # validadas: la portada tiene que contar lo mismo desde cualquier
        # dispositivo que la abra.
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["reviewed_unpaired_tester"], {})
        self.assertEqual(self.store.reviewed_unpaired_tester("nodo", "real-a"), {})

        key = tester_pair_key(TRADE)
        saved = self.store.set_reviewed_unpaired_tester("nodo", "real-a", {
            key: {"at": "2026-10-24T08:00:00.000Z"},
        })

        self.assertEqual(saved, {key: {"at": "2026-10-24T08:00:00.000Z"}})
        self.assertEqual(
            LiveAuditSettingsStore(self.path).reviewed_unpaired_tester("nodo", "real-a"),
            saved,
        )
        # Y no se come el resto del perfil, ni el perfil se come la marca: la
        # pantalla de configuración no manda este campo.
        self.store.update("nodo", {
            "selected_audit_ids": ["real-a"],
            "profiles": {"real-a": {
                "portfolio_id": 11, "portfolio_type": "balanced", "period_days": 30,
                "source_login": "111", "source_server": "Broker-Live",
                "tester_login": "911", "tester_server": "Broker-Demo",
                "source_password": "real", "tester_password": "test",
            }},
        })
        kept = self.store.state("nodo")["profiles"]["real-a"]
        self.assertEqual(kept["reviewed_unpaired_tester"], saved)
        self.assertEqual(kept["period_days"], 30)
        # Desmarcar es guardar vacío, no dejar de guardar.
        self.assertEqual(self.store.set_reviewed_unpaired_tester("nodo", "real-a", {}), {})
        with self.assertRaisesRegex(KeyError, "no tiene configuración"):
            self.store.set_reviewed_unpaired_tester("nodo", "otro-uso", {})

    def test_the_mark_is_the_fingerprint_of_the_tester_row_and_nothing_else(self) -> None:
        # El Strategy Tester no exporta ticket y el `#T6` de la pantalla es la
        # posición en la lista: guardar por índice perdería la marca en cuanto
        # el uso se reanalizara con una operación más.
        key = tester_pair_key(TRADE)
        self.assertEqual(key, "eth-grid|ETHUSD|buy|2026-10-19T06:00:00+00:00|2026-10-19T07:30:00+00:00")
        self.assertEqual(
            normalize_live_audit_settings(
                {"reviewed_unpaired_tester": {key: {"at": "2026-10-24T08:00:00.000Z"}}},
            )["reviewed_unpaired_tester"],
            {key: {"at": "2026-10-24T08:00:00.000Z"}},
        )
        # Sin fecha se guarda igual: lo que la pantalla enseña al lado es «desde
        # el …» y una marca sin fecha sigue siendo una marca.
        self.assertEqual(
            normalize_live_audit_settings(
                {"reviewed_unpaired_tester": {key: {}}},
            )["reviewed_unpaired_tester"],
            {key: {"at": ""}},
        )
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"reviewed_unpaired_tester": []})
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"reviewed_unpaired_tester": {key: True}})
        with self.assertRaisesRegex(ValueError, "huella"):
            normalize_live_audit_settings({"reviewed_unpaired_tester": {"": {}}})
        with self.assertRaisesRegex(ValueError, "2000"):
            normalize_live_audit_settings(
                {"reviewed_unpaired_tester": {str(index): {} for index in range(2001)}},
            )


class ReviewedUnpairedTesterRouteTests(unittest.TestCase):
    """La misma puerta que el escalado de la vista, con otro campo."""

    PATH = ["api", "nodes", "nodo", "live-audits", "real-a", "reviewed-unpaired-tester"]

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

    def post(self, body: object, parts: list[str] | None = None) -> tuple[int, object]:
        handler = RouteHandler(self.store, body)
        self.assertTrue(handle_post(handler, None, parts or self.PATH))
        return handler.sent

    def get(self) -> tuple[int, object]:
        handler = RouteHandler(self.store)
        self.assertTrue(handle_get(handler, SimpleNamespace(query=""), self.PATH))
        return handler.sent

    def test_what_the_operations_screen_marks_is_what_the_dashboard_reads_back(self) -> None:
        self.assertEqual(self.get(), (200, {"reviewed_unpaired_tester": {}}))
        mark = {tester_pair_key(TRADE): {"at": "2026-10-24T08:00:00.000Z"}}

        self.assertEqual(self.post({"reviewed_unpaired_tester": mark}), (200, {
            "reviewed_unpaired_tester": mark,
        }))
        self.assertEqual(self.get(), (200, {"reviewed_unpaired_tester": mark}))
        self.assertEqual(self.post({"reviewed_unpaired_tester": {}}), (200, {
            "reviewed_unpaired_tester": {},
        }))

    def test_a_malformed_mark_or_an_unknown_use_answers_400(self) -> None:
        status, body = self.post({"reviewed_unpaired_tester": {"eth": "sí"}})
        self.assertEqual(status, 400)
        self.assertIn("objeto JSON", str(body))

        status, body = self.post(
            {"reviewed_unpaired_tester": {}},
            ["api", "nodes", "nodo", "live-audits", "sin-cfg", "reviewed-unpaired-tester"],
        )
        self.assertEqual(status, 400)
        self.assertIn("no tiene configuración", str(body))


class ReviewedUnpairedTesterScreenTests(unittest.TestCase):
    """No hay runtime de JavaScript aquí: se comprueba el texto del fichero."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = (STATIC / "live_audit_result.js").read_text(encoding="utf-8")
        cls.reproduction = (STATIC / "live_audit_reproduction.js").read_text(encoding="utf-8")
        cls.metrics = (STATIC / "dashboard_metrics.js").read_text(encoding="utf-8")
        cls.dashboard = (STATIC / "dashboard.js").read_text(encoding="utf-8")

    def test_the_checkbox_shows_up_on_every_row_without_a_real(self) -> None:
        # También en la que el usuario declaró sin real a mano: esa decisión
        # dice que no hay real que buscar, no que la fila esté mirada.
        self.assertIn("function unpairedReviewControl(row, tester) {\n"
                      "  if (row.status !== 'missing') return '';", self.result)
        self.assertIn("data-unpaired-review=", self.result)
        self.assertIn("${manualPairControl(row, tester)}${unpairedReviewControl(row, tester)}",
                      self.result)

    def test_marking_it_saves_the_date_and_undoes_itself_if_the_save_fails(self) -> None:
        self.assertIn("unpairedReviews[key] = {at: new Date().toISOString()}", self.result)
        self.assertIn("/reviewed-unpaired-tester`", self.result)
        self.assertIn("body: JSON.stringify({reviewed_unpaired_tester: value})", self.result)
        # Una casilla marcada que no llegó al perfil sería una operación fuera
        # de la portada que nadie validó.
        self.assertIn("unpairedReviews = previous;", self.result)
        self.assertIn("No se pudo guardar la validación", self.result)

    def test_the_screen_and_the_card_build_the_same_fingerprint(self) -> None:
        # Tres copias de la misma huella —el manager, esta pantalla y la
        # portada— y lo que las ata es el texto: si una se separa, la portada
        # cuenta como pendiente lo que la pantalla da por validado.
        for source in (self.result, self.reproduction):
            self.assertIn(
                "[tester.strategy, tester.symbol, tester.side, tester.open_time, tester.close_time]",
                source,
            )
            self.assertIn(".map(value => String(value || '')).join('|')", source)

    def test_the_ported_count_is_the_one_the_card_runs(self) -> None:
        self.assertIn(
            "  function unlinkedTester(result, reviewed, validatedRows) {\n"
            "    return reproduction.unpairedTester(result).filter(row => (\n"
            "      !reviewed.has(String(row.strategy || row.tester?.strategy || 'Sin estrategia'))\n"
            "      && !reproduction.unpairedReviewed(validatedRows, row)\n"
            "    )).length;",
            self.metrics,
        )
        self.assertIn(
            "  function unpairedReviewed(validated, row) {\n"
            "    return Boolean(validated?.[unpairedKey(row)]);\n"
            "  }",
            self.reproduction,
        )
        self.assertIn(
            "LiveAuditReproduction.storedUnpairedReviews(entry.profile)", self.dashboard,
        )

    def test_a_validated_row_leaves_the_card_and_the_rest_stay(self) -> None:
        other = {**TRADE, "symbol": "BTCUSD"}
        rows = [
            {"status": "missing", "strategy": "eth-grid", "tester": TRADE},
            {"status": "missing", "strategy": "btc-grid", "tester": other},
            {"status": "matched", "strategy": "eth-grid", "tester": TRADE},
        ]
        self.assertEqual(unlinked_tester(rows, set(), {}), 2)
        # Validar la fila se lleva la suya y sólo la suya.
        validated = {tester_pair_key(TRADE): {"at": "2026-10-24T08:00:00.000Z"}}
        self.assertEqual(unlinked_tester(rows, set(), validated), 1)
        # Y el gesto viejo sigue valiendo: validar la estrategia entera en
        # Análisis se lleva todas las suyas.
        self.assertEqual(unlinked_tester(rows, {"btc-grid"}, validated), 0)


if __name__ == "__main__":
    unittest.main()
