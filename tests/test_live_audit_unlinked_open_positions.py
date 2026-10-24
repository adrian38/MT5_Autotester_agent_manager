"""Las abiertas del terminal que nadie reclamó, de punta a punta.

El auditor empareja las posiciones que seguían abiertas al acabar el periodo
con la operación del tester que las abrió, y las que sobran las tiraba: no
estaban emparejadas, ni sin pareja, ni en ninguna tabla. Es el mismo agujero
que `_attach_discarded_reals` tapó para los cierres que el filtro de lote
aparta, y en la materia prima guardada de los tres agentes hay tres.

Ahora se publican y Análisis las enseña con una casilla **Validada**, que es
el mismo gesto que la de `Demo contra real` sobre otra lista: no cambia la
comparación —una abierta no tiene cierre ni PnL y no entra en ninguna curva—,
sólo la saca de `Portafolios activos`. La marca va por **posición**, así que
sigue valiendo mientras la cuenta la mantenga abierta.

Aquí se comprueban las cuatro mitades: lo que la comparación publica, lo que
el archivo conserva, lo que el almacén y la ruta guardan, y lo que las dos
pantallas hacen con ello.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_archive import trimmed_detail
from mt5_manager.live_audit_engine import LiveAuditController
from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)
from mt5_manager.manager_live_audit_routes import handle_get, handle_post
from tests.live_audit.live_audit_engine_base import request
from tests.test_live_audit_settings import profile as stored_profile
from tests.test_live_audit_view_lots import RouteHandler

STATIC = Path(__file__).parents[1] / "mt5_manager" / "static"

OPENED = datetime(2026, 10, 16, 16, 37, 6, tzinfo=timezone.utc)


def tester_trade(symbol: str = "WFC", opened: datetime = OPENED) -> dict:
    return {
        "strategy": "s1", "symbol": symbol, "side": "buy",
        "open_time": opened, "close_time": opened + timedelta(hours=4),
        "open_price": 81.2, "volume": .02, "profit": 3.4,
    }


def open_position(symbol: str = "WFC", opened: datetime = OPENED, **extra: object) -> dict:
    return {
        "strategy": "771357469", "symbol": symbol, "side": "buy",
        "open_time": opened, "close_time": None,
        "open_price": 81.2, "volume": .02, "profit": None,
        "position_id": 771357469, **extra,
    }


class UnlinkedOpenPositionsComparisonTests(unittest.TestCase):
    """Lo que la comparación publica cuando sobra una abierta."""

    def detail(self, tester: list[dict], still_open: list[dict]) -> dict:
        order = {**request(), "real_positions_open_at_period_end": still_open}
        return LiveAuditController._compare(
            [], tester, {"WFC": .01, "XOM": .01}, order, {"s1": len(tester)},
        )

    def test_an_open_position_no_tester_operation_claimed_is_published(self) -> None:
        # Antes se calculaba y se tiraba: la pantalla decía que todo cuadraba
        # mientras la cuenta tenía una posición abierta que nadie explicaba.
        result = self.detail([], [open_position()])
        rows = result["comparison_detail"]["unmatched_open_positions"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open_extra")
        self.assertEqual(rows[0]["open_real_index"], 1)
        self.assertEqual(rows[0]["reason"], "not_claimed_by_any_tester_operation")
        self.assertEqual(rows[0]["real"]["position_id"], 771357469)
        self.assertEqual(rows[0]["real"]["symbol"], "WFC")
        self.assertIsNone(rows[0]["real"]["close_time"])
        self.assertIsNone(rows[0]["real"]["profit"])

    def test_the_one_that_did_find_its_tester_operation_is_not_in_the_list(self) -> None:
        result = self.detail([tester_trade()], [open_position()])

        self.assertEqual(result["open_real_trades"], 1)
        self.assertEqual(result["comparison_detail"]["unmatched_open_positions"], [])

    def test_only_the_leftovers_are_published_when_there_are_several(self) -> None:
        other = open_position("XOM", OPENED + timedelta(hours=2), position_id=772066112)
        result = self.detail([tester_trade()], [open_position(), other])
        rows = result["comparison_detail"]["unmatched_open_positions"]

        self.assertEqual([row["real"]["position_id"] for row in rows], [772066112])
        self.assertEqual(rows[0]["open_real_index"], 2)

    def test_publishing_them_does_not_move_the_verdict(self) -> None:
        # No tienen cierre ni PnL: no están en ninguna curva y no son una
        # desviación medida, son trabajo pendiente de mirar. Si contaran como
        # discrepancia, el veredicto cambiaría sin que nada nuevo se midiera.
        alone = self.detail([], [])
        leftover = self.detail([], [open_position()])

        for key in ("discrepancies", "extra_real_trades", "missing_real_trades",
                    "open_real_trades", "matched_trades"):
            self.assertEqual(alone[key], leftover[key], key)

    def test_the_archive_keeps_the_list_for_the_history_screen(self) -> None:
        detail = self.detail([], [open_position()])["comparison_detail"]
        self.assertEqual(
            trimmed_detail(detail)["unmatched_open_positions"],
            detail["unmatched_open_positions"],
        )


class ReviewedOpenPositionsStoreTests(unittest.TestCase):
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

    def test_a_validated_position_is_saved_per_use_and_survives_a_reload(self) -> None:
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["reviewed_open_positions"], {})
        self.assertEqual(self.store.reviewed_open_positions("nodo", "real-a"), {})

        saved = self.store.set_reviewed_open_positions("nodo", "real-a", {
            "771357469": {"at": "2026-10-24T08:00:00.000Z"},
        })

        self.assertEqual(saved, {"771357469": {"at": "2026-10-24T08:00:00.000Z"}})
        self.assertEqual(
            LiveAuditSettingsStore(self.path).reviewed_open_positions("nodo", "real-a"),
            saved,
        )
        # La pantalla de configuración no manda este campo y no se lo puede
        # comer, igual que con las estrategias validadas.
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
        self.assertEqual(kept["reviewed_open_positions"], saved)
        self.assertEqual(kept["period_days"], 30)
        # Desmarcar es guardar vacío, no dejar de guardar.
        self.assertEqual(self.store.set_reviewed_open_positions("nodo", "real-a", {}), {})
        with self.assertRaisesRegex(KeyError, "no tiene configuración"):
            self.store.set_reviewed_open_positions("nodo", "otro-uso", {})

    def test_the_mark_is_a_date_and_nothing_else(self) -> None:
        # Aquí no hay veredicto que guardar: una abierta sin enlazar no se
        # mide, está o no está. Lo que sí tiene que ser es un objeto, o el día
        # que haya algo más que decir no cabría sin migrar lo guardado.
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"reviewed_open_positions": []})
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"reviewed_open_positions": {"771357469": True}})
        self.assertEqual(
            normalize_live_audit_settings(
                {"reviewed_open_positions": {"771357469": {"at": "2026-10-24T08:00:00Z"}}},
            )["reviewed_open_positions"],
            {"771357469": {"at": "2026-10-24T08:00:00Z"}},
        )


class ReviewedOpenPositionsRouteTests(unittest.TestCase):
    """La misma puerta que el escalado de la vista, con un tercer campo."""

    PATH = ["api", "nodes", "nodo", "live-audits", "real-a", "reviewed-open-positions"]

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
        self.assertEqual(self.get(), (200, {"reviewed_open_positions": {}}))
        mark = {"771357469": {"at": "2026-10-24T08:00:00.000Z"}}

        self.assertEqual(self.post({"reviewed_open_positions": mark}), (200, {
            "reviewed_open_positions": mark,
        }))
        self.assertEqual(self.get(), (200, {"reviewed_open_positions": mark}))
        self.assertEqual(self.post({"reviewed_open_positions": {}}), (200, {
            "reviewed_open_positions": {},
        }))

    def test_a_malformed_mark_or_an_unknown_use_answers_400(self) -> None:
        status, body = self.post({"reviewed_open_positions": {"771357469": "sí"}})
        self.assertEqual(status, 400)
        self.assertIn("objeto JSON", str(body))

        status, body = self.post(
            {"reviewed_open_positions": {}},
            ["api", "nodes", "nodo", "live-audits", "sin-config", "reviewed-open-positions"],
        )
        self.assertEqual(status, 400)
        self.assertIn("no tiene configuración", str(body))


class UnlinkedOpenPositionsScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reproduction = (STATIC / "live_audit_reproduction.js").read_text(encoding="utf-8")
        cls.analysis = (STATIC / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.analysis_page = (STATIC / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.metrics = (STATIC / "dashboard_metrics.js").read_text(encoding="utf-8")
        cls.dashboard = (STATIC / "dashboard.js").read_text(encoding="utf-8")

    def test_analysis_has_the_table_with_its_validation_column(self) -> None:
        self.assertIn('<tbody id="unlinked-open-body"></tbody>', self.analysis_page)
        self.assertIn("<th>Validada</th>", self.analysis_page)
        self.assertIn("Abiertas en el terminal que nadie reclamó", self.analysis_page)
        self.assertIn("function unlinkedOpenRow(row) {", self.analysis)
        self.assertIn("renderUnlinkedOpen();", self.analysis)
        self.assertIn('colspan="5"', self.analysis)

    def test_marking_one_saves_it_against_the_position_and_undoes_a_failure(self) -> None:
        self.assertIn("openReviews[key] = {at: new Date().toISOString()}", self.analysis)
        self.assertIn("/reviewed-open-positions`", self.analysis)
        self.assertIn("body: JSON.stringify({reviewed_open_positions: value})", self.analysis)
        self.assertIn("openReviews = previous;", self.analysis)
        self.assertIn("No se pudo guardar la validación", self.analysis)

    def test_an_execution_without_the_list_does_not_claim_there_are_none(self) -> None:
        # `undefined` no es `[]`: una ejecución archivada antes de esto no dice
        # que todas las abiertas estén emparejadas, dice que no se midieron.
        self.assertIn(
            "    return Array.isArray(result?.comparison_detail?.unmatched_open_positions);",
            self.reproduction,
        )
        self.assertIn("if (!LiveAuditReproduction.measuredOpenPositions(currentResult)) {", self.analysis)
        self.assertIn("o es anterior a este cuadre", self.analysis)

    def test_the_dashboard_counts_only_what_is_still_unvalidated(self) -> None:
        self.assertIn("unlinked_open: unlinkedOpen(result, openReviews)", self.metrics)
        self.assertIn("row => !reproduction.openReviewed(validated, row),", self.metrics)
        self.assertIn("LiveAuditReproduction.storedOpenReviews(entry.profile)", self.dashboard)
        self.assertIn("abierta${abiertas === 1 ? '' : 's'} sin enlazar", self.dashboard)

    def test_the_key_is_the_position_so_the_mark_outlives_the_audit(self) -> None:
        # El índice de la fila cambia en cuanto la cuenta abre otra antes, y la
        # marca dejaría de valer sin que nadie la haya retirado.
        self.assertIn(
            "    return String(\n"
            "      real.position_id || real.ticket\n"
            "      || `${real.symbol || '?'}|${real.open_time || ''}`,\n"
            "    );",
            self.reproduction,
        )


if __name__ == "__main__":
    unittest.main()
