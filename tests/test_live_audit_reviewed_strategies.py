"""Las estrategias que el usuario ya revisó en Análisis, de punta a punta.

Validar una fila no cambia su veredicto: la saca de `Portafolios activos`, que
es la lista de lo que queda por mirar. Por eso la marca vive en el perfil del
uso y no en el navegador —la portada tiene que saberlo desde cualquier
dispositivo, igual que el escalado de la vista— y por eso guarda el veredicto
que la fila tenía al marcarla: validar no es silenciar para siempre.

Aquí se comprueban las tres mitades: lo que el almacén persiste, lo que la ruta
contesta y lo que las dos pantallas hacen con ello.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)
from mt5_manager.manager_live_audit_routes import handle_get, handle_post
from tests.test_live_audit_settings import profile as stored_profile
from tests.test_live_audit_view_lots import RouteHandler

STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"

# El orden de gravedad que la portada compara, portado desde
# `live_audit_reproduction.js`. El port sólo vale si cada expresión que mide
# está literalmente en el fichero, y eso es lo que exige
# `test_the_ported_rule_is_the_one_the_screen_runs`.
SEVERITY = {
    "NO REPRODUCE": 4, "AVISO": 3, "POCO IMPORTE": 2,
    "REPRODUCE": 1, "MEJOR QUE EL TESTER": 1, "SIN DATOS": 0,
}


def reviewed(reviews: dict, strategy: str, label: str) -> bool:
    mark = reviews.get(strategy)
    return bool(mark) and SEVERITY.get(label, 0) <= SEVERITY.get(mark["verdict"], 0)


class ReviewedStrategiesStoreTests(unittest.TestCase):
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

    def test_a_validated_strategy_is_saved_per_use_and_survives_a_reload(self) -> None:
        # Vive en el perfil y no en el navegador: si no, una estrategia
        # validada en el escritorio seguiría avisando en el móvil.
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["reviewed_strategies"], {})
        self.assertEqual(self.store.reviewed_strategies("nodo", "real-a"), {})

        saved = self.store.set_reviewed_strategies("nodo", "real-a", {
            "eth-grid": {"verdict": "AVISO", "at": "2026-10-19T08:00:00.000Z"},
        })

        self.assertEqual(saved, {
            "eth-grid": {"verdict": "AVISO", "at": "2026-10-19T08:00:00.000Z"},
        })
        self.assertEqual(
            LiveAuditSettingsStore(self.path).reviewed_strategies("nodo", "real-a"), saved,
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
        self.assertEqual(kept["reviewed_strategies"], saved)
        self.assertEqual(kept["period_days"], 30)
        # Desmarcar es guardar vacío, no dejar de guardar.
        self.assertEqual(self.store.set_reviewed_strategies("nodo", "real-a", {}), {})
        with self.assertRaisesRegex(KeyError, "no tiene configuración"):
            self.store.set_reviewed_strategies("nodo", "otro-uso", {})

    def test_the_mark_has_to_carry_a_verdict_the_dashboard_can_compare(self) -> None:
        # Sin veredicto conocido la portada decidiría «no ha empeorado» contra
        # una etiqueta que no sabe ordenar, y eso esconde la fila para siempre.
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"reviewed_strategies": []})
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"reviewed_strategies": {"eth": True}})
        with self.assertRaisesRegex(ValueError, "veredicto conocido"):
            normalize_live_audit_settings({"reviewed_strategies": {"eth": {"verdict": "OK"}}})
        self.assertEqual(
            normalize_live_audit_settings(
                {"reviewed_strategies": {"eth": {"verdict": "NO REPRODUCE"}}},
            )["reviewed_strategies"],
            {"eth": {"verdict": "NO REPRODUCE", "at": ""}},
        )


class ReviewedStrategiesRouteTests(unittest.TestCase):
    """La misma puerta que el escalado de la vista, con otro campo."""

    PATH = ["api", "nodes", "nodo", "live-audits", "real-a", "reviewed-strategies"]

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

    def test_what_analysis_marks_is_what_the_dashboard_reads_back(self) -> None:
        self.assertEqual(self.get(), (200, {"reviewed_strategies": {}}))
        mark = {"eth-grid": {"verdict": "AVISO", "at": "2026-10-19T08:00:00.000Z"}}

        self.assertEqual(self.post({"reviewed_strategies": mark}), (200, {
            "reviewed_strategies": mark,
        }))
        self.assertEqual(self.get(), (200, {"reviewed_strategies": mark}))
        self.assertEqual(self.post({"reviewed_strategies": {}}), (200, {
            "reviewed_strategies": {},
        }))

    def test_an_unknown_verdict_or_use_answers_400(self) -> None:
        status, body = self.post({"reviewed_strategies": {"eth": {"verdict": "VALE"}}})
        self.assertEqual(status, 400)
        self.assertIn("veredicto conocido", str(body))

        status, body = self.post(
            {"reviewed_strategies": {}},
            ["api", "nodes", "nodo", "live-audits", "sin-configurar", "reviewed-strategies"],
        )
        self.assertEqual(status, 400)
        self.assertIn("no tiene configuración", str(body))


class ReviewedStrategiesScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reproduction = (STATIC / "live_audit_reproduction.js").read_text(encoding="utf-8")
        cls.analysis = (STATIC / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.analysis_page = (STATIC / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.metrics = (STATIC / "dashboard_metrics.js").read_text(encoding="utf-8")
        cls.dashboard = (STATIC / "dashboard.js").read_text(encoding="utf-8")

    def test_the_checkbox_only_shows_up_where_there_is_something_to_review(self) -> None:
        # La casilla existe para sacar una fila de la portada, así que sale en
        # los mismos dos veredictos que la portada enseña. Una fila que
        # conserva su marca la sigue llevando aunque haya mejorado, o no
        # habría forma de retirarla.
        self.assertIn("<th>Validada</th>", self.analysis_page)
        self.assertIn(
            "const REVIEWABLE_VERDICTS = ['AVISO', 'NO REPRODUCE'];", self.analysis,
        )
        self.assertIn(
            "if (!REVIEWABLE_VERDICTS.includes(label) && !mark) {", self.analysis,
        )
        self.assertIn("${reviewCell(group, label)}", self.analysis)
        self.assertIn('colspan="7"', self.analysis)

    def test_marking_it_saves_the_verdict_it_had_and_not_just_a_yes(self) -> None:
        self.assertIn("reviews[strategy] = {verdict: label, at: new Date().toISOString()}", self.analysis)
        self.assertIn("/reviewed-strategies`", self.analysis)
        self.assertIn("body: JSON.stringify({reviewed_strategies: value})", self.analysis)
        # Repinta antes de guardar y se deshace si el guardado falla: una
        # casilla marcada que no llegó al perfil sería una estrategia fuera de
        # la portada sin que nadie la haya validado.
        self.assertIn("reviews = previous;", self.analysis)
        self.assertIn("No se pudo guardar la validación", self.analysis)

    def test_the_dashboard_hides_what_was_validated_and_says_how_many(self) -> None:
        self.assertIn("!reproduction.reviewed(", self.metrics)
        self.assertIn("flagged: pending, reviewed: marked.length - pending.length", self.metrics)
        self.assertIn("LiveAuditReproduction.storedReviews(entry.profile)", self.dashboard)
        # Esconderlo sin decir cuántos son convertiría «SIN AVISOS» en una
        # afirmación más fuerte de lo que es.
        self.assertIn("ya validada", self.dashboard)

    def test_the_ported_rule_is_the_one_the_screen_runs(self) -> None:
        # El criterio vive en JavaScript y en este equipo no hay runtime que lo
        # ejecute, así que la medida de abajo es un port: sólo vale si cada
        # expresión que mide está literalmente en el fichero.
        self.assertIn(
            "    return Boolean(mark) && (SEVERITY[label] || 0) <= (SEVERITY[mark.verdict] || 0);",
            self.reproduction,
        )
        self.assertIn(
            "  const SEVERITY = {\n"
            "    'NO REPRODUCE': 4, AVISO: 3, 'POCO IMPORTE': 2,\n"
            "    REPRODUCE: 1, 'MEJOR QUE EL TESTER': 1, 'SIN DATOS': 0,\n"
            "  };",
            self.reproduction,
        )

    def test_a_validated_strategy_comes_back_only_when_it_gets_worse(self) -> None:
        marks = {"eth": {"verdict": "AVISO", "at": "2026-10-19T08:00:00.000Z"}}
        # Lo que se validó sigue validado mientras no empeore, y que mejore no
        # lo devuelve a la lista: ya se miró.
        self.assertTrue(reviewed(marks, "eth", "AVISO"))
        self.assertTrue(reviewed(marks, "eth", "POCO IMPORTE"))
        self.assertTrue(reviewed(marks, "eth", "REPRODUCE"))
        # Pero NO REPRODUCE es otra cosa que la que el usuario miró, y la
        # portada es justo la pantalla que existe para decirlo.
        self.assertFalse(reviewed(marks, "eth", "NO REPRODUCE"))
        self.assertFalse(reviewed(marks, "btc", "AVISO"))


if __name__ == "__main__":
    unittest.main()
