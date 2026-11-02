"""Validar una alarma de «contra el histórico» la saca de las listas.

La portada y el resumen del mes repetían semana tras semana las mismas
estrategias en `DEGRADA`, `AVISO FUERTE` u «opera menos», también las que el
usuario ya había mirado y decidido dejar como están. Aquí se comprueba lo que
hace falta para que esa lista se pueda vaciar sin dejar de avisar cuando algo
cambia de verdad.

Lo que **no** se comprueba aquí porque no existe: que una validación de
reproducción calle esta alarma. Son dos preguntas distintas sobre el mismo
nombre —si el real se parece al tester, y si la estrategia sigue haciendo lo de
siempre— y una estrategia puede reproducir a la perfección y estar
degradándose. Cada lista tiene su marca, como ya la tienen el periodo y la
serie.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_degradation_alerts as alerts
from mt5_manager import live_audit_degradation_reviews as reviews
from mt5_manager.live_audit_schedule import MANAGER_ONLY_PROFILE_KEYS
from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
)
from mt5_manager import monthly_summary_notices as notices
from mt5_manager.manager_live_audit_routes import handle_get, handle_post
from tests.live_audit.test_monthly_summary import facts as summary_facts
from tests.test_live_audit_settings import profile as stored_profile
from tests.test_live_audit_view_lots import RouteHandler

STATIC = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"

STRATEGY = "AXI/STANDARD:10751"
OTHER = "AXI/STANDARD:21145"


def mark(verdict: str, deviations: float | None = None, slow: bool = False) -> dict:
    return {
        "verdict": verdict, "at": "2026-10-25T02:21:53Z",
        "slow": slow, **({} if deviations is None else {"deviations": deviations}),
    }


def alarm(strategy: str = STRATEGY, deviations: float = -2.76) -> dict:
    return {
        "strategy": strategy, "strategy_set": "un.set", "symbol": "NAS100.fs",
        "verdict": "warning", "trades": 19, "deviations": deviations,
        "difference_pct": -536.9,
    }


def found(**lists: list[dict]) -> dict:
    return {
        "strategies": [], "crossing": [], "slow": [],
        "watching": 2, "compared": 13, "checked_at": "2026-11-01T15:12:31+00:00",
        **lists,
    }


class DegradationReviewRuleTests(unittest.TestCase):
    """Cuándo una marca tapa una alarma y cuándo deja de taparla."""

    def test_a_crossing_validated_at_its_own_level_leaves_the_list(self):
        hidden = reviews.hide(
            found(crossing=[alarm()]), {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertEqual(hidden["crossing"], [])
        self.assertEqual(hidden["reviewed"], 1)

    def test_an_unvalidated_strategy_stays(self):
        hidden = reviews.hide(
            found(crossing=[alarm(), alarm(OTHER)]), {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertEqual([row["strategy"] for row in hidden["crossing"]], [OTHER])
        self.assertEqual(hidden["reviewed"], 1)

    def test_validating_the_mild_warning_does_not_cover_the_crossing(self):
        """Lo que valida un AVISO no puede tapar el cruce de la semana siguiente."""
        hidden = reviews.hide(
            found(crossing=[alarm()]), {STRATEGY: mark("warning", -1.4)},
        )
        self.assertEqual(len(hidden["crossing"]), 1)
        self.assertEqual(hidden["reviewed"], 0)

    def test_validating_the_crossing_does_not_cover_a_later_degradation(self):
        hidden = reviews.hide(
            found(strategies=[{**alarm(), "verdict": "degraded", "deviations": -5.4}]),
            {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertEqual(len(hidden["strategies"]), 1)

    def test_a_degradation_validated_as_such_leaves_the_list(self):
        hidden = reviews.hide(
            found(strategies=[{**alarm(), "verdict": "degraded", "deviations": -5.4}]),
            {STRATEGY: mark("degraded", -5.4)},
        )
        self.assertEqual(hidden["strategies"], [])

    def test_improving_keeps_it_out(self):
        """Que mejore la mantiene fuera: ya se miró, y peor no se ha puesto."""
        hidden = reviews.hide(
            found(crossing=[alarm()]), {STRATEGY: mark("degraded", -5.4)},
        )
        self.assertEqual(hidden["crossing"], [])

    def test_falling_within_the_same_level_retires_the_mark(self):
        """La etiqueta sola no basta: −2,8 σ y −4,0 σ son el mismo AVISO FUERTE."""
        hidden = reviews.hide(
            found(crossing=[alarm(deviations=-4.0)]),
            {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertEqual(len(hidden["crossing"]), 1)

    def test_a_handful_of_hundredths_does_not_retire_it(self):
        """Las sigmas se recalculan cada auditoría y se mueven solas."""
        hidden = reviews.hide(
            found(crossing=[alarm(deviations=-2.9)]),
            {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertEqual(hidden["crossing"], [])

    def test_a_mark_without_sigmas_still_compares_by_level(self):
        """Marcas guardadas sin el número: se comparan por nivel, no desaparecen."""
        hidden = reviews.hide(
            found(crossing=[alarm()]), {STRATEGY: mark("crossed")},
        )
        self.assertEqual(hidden["crossing"], [])


class SlowIsItsOwnFactTests(unittest.TestCase):
    """«Opera menos» no es un veredicto peor: es otra cosa que mirar."""

    def test_validating_the_verdict_does_not_silence_the_rate(self):
        hidden = reviews.hide(
            found(slow=[alarm()]), {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertEqual(len(hidden["slow"]), 1)
        self.assertEqual(hidden["reviewed"], 0)

    def test_validating_the_rate_silences_it(self):
        hidden = reviews.hide(
            found(slow=[alarm()]), {STRATEGY: mark("crossed", -2.76, slow=True)},
        )
        self.assertEqual(hidden["slow"], [])

    def test_a_strategy_in_two_lists_counts_once(self):
        hidden = reviews.hide(
            found(crossing=[alarm()], slow=[alarm()]),
            {STRATEGY: mark("crossed", -2.76, slow=True)},
        )
        self.assertEqual(hidden["reviewed"], 1)


class AnnotatedRowsTests(unittest.TestCase):
    """Lo que la pantalla recibe para pintar la casilla sin recalcular nada."""

    def row(self, **changes) -> dict:
        return {**alarm(), "evidence": {"crossed": True}, "rate": {}, **changes}

    def test_the_server_says_what_level_the_row_is_at(self):
        annotated = reviews.annotate([self.row()], {})
        self.assertEqual(annotated[0]["review_level"], "crossed")
        self.assertFalse(annotated[0]["reviewed"])

    def test_a_warning_that_did_not_cross_is_not_the_same_level(self):
        annotated = reviews.annotate([self.row(evidence={"crossed": False})], {})
        self.assertEqual(annotated[0]["review_level"], "warning")

    def test_a_row_with_nothing_to_look_at_has_no_level(self):
        annotated = reviews.annotate([self.row(verdict="normal")], {})
        self.assertEqual(annotated[0]["review_level"], "")
        self.assertFalse(annotated[0]["review_slow"])

    def test_a_validated_row_keeps_its_mark_for_the_screen(self):
        annotated = reviews.annotate(
            [self.row()], {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertTrue(annotated[0]["reviewed"])
        self.assertEqual(annotated[0]["review"]["verdict"], "crossed")

    def test_a_row_validated_only_on_the_verdict_is_not_reviewed_if_it_slowed(self):
        annotated = reviews.annotate(
            [self.row(rate={"flagged": True})], {STRATEGY: mark("crossed", -2.76)},
        )
        self.assertTrue(annotated[0]["review_slow"])
        self.assertFalse(annotated[0]["reviewed"])


class StoredFiltersAtReadTimeTests(unittest.TestCase):
    """Se esconde al leer, no al medir: la casilla tiene que notarse hoy."""

    class Runs:
        def __init__(self, record: dict) -> None:
            self._record = record

        def key(self, node_id: str, audit_key: str) -> str:
            return f"{node_id}|{audit_key}"

        def record(self, _key: str) -> dict:
            return self._record

    class Settings:
        def __init__(self, marks: dict) -> None:
            self._marks = marks

        def reviewed_degradation_strategies(self, _node: str, _audit: str) -> dict:
            return self._marks

    def server(self, marks: dict):
        holder = type("Server", (), {})()
        holder.live_audit_runs = self.Runs({
            "degradation": [], "degradation_crossing": [alarm()],
            "degradation_slow": [], "degradation_watching": 2,
            "degradation_compared": 13,
            "degradation_checked_at": "2026-11-01T15:12:31+00:00",
        })
        holder.live_audit_settings = self.Settings(marks)
        return holder

    def test_the_record_is_not_rewritten_and_the_mark_still_applies(self):
        """El registro del cron está cerrado con el `execution_id`.

        Filtrar al escribirlo dejaría la validación sin efecto hasta la
        auditoría siguiente: una semana mirando una alarma ya mirada.
        """
        stored = alerts.stored(
            self.server({STRATEGY: mark("crossed", -2.76)}), "n", "audit-105",
        )
        self.assertEqual(stored["crossing"], [])
        self.assertEqual(stored["reviewed"], 1)
        self.assertEqual(stored["compared"], 13)

    def test_without_marks_nothing_changes(self):
        stored = alerts.stored(self.server({}), "n", "audit-105")
        self.assertEqual(len(stored["crossing"]), 1)
        self.assertEqual(stored["reviewed"], 0)

    def test_a_server_without_the_field_does_not_break(self):
        """Un manager a medio arrancar no puede dejar la portada sin tarjeta."""
        holder = self.server({})
        del holder.live_audit_settings
        self.assertEqual(len(alerts.stored(holder, "n", "audit-105")["crossing"]), 1)


class StoredMarkValidationTests(unittest.TestCase):
    """El esquema valida el nivel, como el de reproducción valida la etiqueta."""

    def test_an_unknown_level_is_rejected(self):
        with self.assertRaises(ValueError):
            reviews.normalize_reviewed_degradation_strategies(
                {STRATEGY: {"verdict": "ROTA", "at": ""}},
            )

    def test_no_level_means_it_was_marked_only_for_the_rate(self):
        normalized = reviews.normalize_reviewed_degradation_strategies(
            {STRATEGY: {"verdict": "", "at": "", "slow": True}},
        )
        self.assertEqual(normalized[STRATEGY]["verdict"], "")
        self.assertTrue(normalized[STRATEGY]["slow"])

    def test_sigmas_that_are_not_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            reviews.normalize_reviewed_degradation_strategies(
                {STRATEGY: {"verdict": "crossed", "at": "", "deviations": "mucho"}},
            )

    def test_an_absent_sigma_is_not_invented(self):
        normalized = reviews.normalize_reviewed_degradation_strategies(
            {STRATEGY: {"verdict": "crossed", "at": "", "deviations": None}},
        )
        self.assertNotIn("deviations", normalized[STRATEGY])

    def test_nothing_stored_is_an_empty_map(self):
        self.assertEqual(reviews.normalize_reviewed_degradation_strategies(None), {})


class MonthlyCardTests(unittest.TestCase):
    """Lo que la tarjeta del mes dice de lo que se está callando."""

    def test_an_alarm_already_validated_stops_moving_the_verdict(self) -> None:
        # Lo validado ya no llega: lo quita `stored`. Lo que la tarjeta tiene
        # que hacer es no concluir sobre ello y decir que lo está callando.
        notice = notices.strategies_degraded(summary_facts(degradation_reviewed=2))
        self.assertEqual(notice["verdict"], "")
        self.assertEqual(notice["level"], "info")
        self.assertIn("validada", str(notice["title"]))
        self.assertEqual(notices.verdict([notice]), notices.KEEP)

    def test_a_card_that_hides_alarms_says_how_many(self) -> None:
        # Una tarjeta que calla tres avisos sin contarlos afirma más de lo que
        # sabe, que es lo mismo que ya hace la portada con las validadas.
        notice = notices.strategies_degraded(summary_facts(
            crossing=[{"symbol": "EURUSD", "strategy": "B"}],
            degradation_reviewed=3,
        ))
        self.assertIn("Otras 3 ya están validadas", str(notice["message"]))

    def test_without_alarms_and_without_validations_there_is_no_notice(self) -> None:
        self.assertIsNone(notices.strategies_degraded(summary_facts()))


class DegradationReviewPlumbingTests(unittest.TestCase):
    """Dónde vive la marca, por dónde se guarda y qué pantalla la enseña."""

    PATH = [
        "api", "nodes", "nodo", "live-audits", "real-a",
        "reviewed-degradation-strategies",
    ]

    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis_page = (STATIC / "live_audit_analysis.html").read_text(
            encoding="utf-8",
        )
        cls.analysis = (STATIC / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.dashboard = (STATIC / "dashboard.js").read_text(encoding="utf-8")

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

    def test_the_degradation_mark_is_its_own_field_of_the_profile(self) -> None:
        self.assertEqual(
            DEFAULT_LIVE_AUDIT_PROFILE["reviewed_degradation_strategies"], {},
        )
        saved = self.store.set_reviewed_degradation_strategies("nodo", "real-a", {
            STRATEGY: mark("crossed", -2.76),
        })
        self.assertEqual(saved[STRATEGY]["verdict"], "crossed")
        self.assertEqual(
            LiveAuditSettingsStore(self.path).reviewed_degradation_strategies(
                "nodo", "real-a",
            ),
            saved,
        )
        # Y no toca las otras dos, que es el motivo de que sean tres campos.
        self.assertEqual(self.store.reviewed_strategies("nodo", "real-a"), {})
        self.assertEqual(self.store.reviewed_history_strategies("nodo", "real-a"), {})

    def test_what_analysis_marks_is_what_the_dashboard_reads_back(self) -> None:
        handler = RouteHandler(self.store)
        self.assertTrue(handle_get(handler, SimpleNamespace(query=""), self.PATH))
        self.assertEqual(handler.sent, (200, {"reviewed_degradation_strategies": {}}))

        stored = {STRATEGY: mark("crossed", -2.76)}
        handler = RouteHandler(self.store, {"reviewed_degradation_strategies": stored})
        self.assertTrue(handle_post(handler, None, self.PATH))
        self.assertEqual(handler.sent[0], 200)

        handler = RouteHandler(self.store)
        self.assertTrue(handle_get(handler, SimpleNamespace(query=""), self.PATH))
        self.assertEqual(
            handler.sent[1]["reviewed_degradation_strategies"][STRATEGY]["verdict"],
            "crossed",
        )

    def test_an_unknown_level_answers_400(self) -> None:
        handler = RouteHandler(self.store, {
            "reviewed_degradation_strategies": {STRATEGY: {"verdict": "ROTA"}},
        })
        self.assertTrue(handle_post(handler, None, self.PATH))
        self.assertEqual(handler.sent[0], 400)

    def test_the_field_is_criterion_of_the_manager_and_never_goes_to_the_node(self) -> None:
        self.assertIn("reviewed_degradation_strategies", MANAGER_ONLY_PROFILE_KEYS)

    def test_the_degradation_table_carries_its_own_checkbox(self) -> None:
        self.assertIn(
            "<th>Conclusión para decidir</th><th>Validada</th>", self.analysis_page,
        )
        self.assertIn("${degradationReviewCell(row)}", self.analysis)
        self.assertIn("data-degradation-review=", self.analysis)
        self.assertIn("/reviewed-degradation-strategies`", self.analysis)
        self.assertIn("reviewed_degradation_strategies: value", self.analysis)

    def test_the_screen_does_not_recompute_who_is_covered(self) -> None:
        # El criterio vive en Python. La pantalla pinta `reviewed` y guarda el
        # nivel que le mandó el servidor: dos cálculos serían dos criterios y
        # la casilla acabaría discrepando de lo que la portada esconde.
        self.assertIn("row.reviewed ? ' checked' : ''", self.analysis)
        self.assertIn("verdict: row.review_level || ''", self.analysis)

    def test_the_dashboard_says_how_many_it_is_hiding(self) -> None:
        self.assertIn("entry.degradation?.reviewed", self.dashboard)
        self.assertIn("validada${", self.dashboard)


if __name__ == "__main__":
    unittest.main()
