"""La serie de auditorías: archivo, acumulación y herencia entre portafolios.

El agente sólo guarda la última ejecución de cada uso, así que sin archivo no
hay serie. Y sin serie, la tabla «contra el histórico» contestaba
«INSUFICIENTE» en todas las filas: tres operaciones en una semana no deciden
nada sobre una estrategia.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_audit_history
from mt5_manager.live_audit_archive import LiveAuditArchive, strategies_of, trimmed_detail


def trade(symbol: str, close: str, profit: float, volume: float = 0.01) -> dict[str, object]:
    return {
        "symbol": symbol, "close_time": close, "open_time": close,
        "profit": profit, "volume": volume,
    }


def detail(strategy: str, trades: list[dict[str, object]], symbol: str = "EURUSD") -> dict[str, object]:
    return {
        "operation_comparisons": [
            {"strategy": strategy, "strategy_set": f"{strategy}.set",
             "tester": {**row, "strategy": strategy}, "real": row}
            for row in trades
        ],
        "strategy_summary": [{"strategy": strategy, "strategy_set": f"{strategy}.set"}],
        "methodology": {"ignorado": True},
    }


def meta(audit_key: str, execution: str, portfolio: int, start: str, end: str) -> dict[str, object]:
    return {
        "audit_key": audit_key, "execution_id": execution, "portfolio_id": portfolio,
        "executed_at": end, "period_start": start, "period_end": end,
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = LiveAuditArchive(Path(self.temporary.name) / "history")

    def test_an_execution_is_remembered_once_and_can_be_read_back(self) -> None:
        rows = [trade("EURUSD", "2026-10-10T10:00:00+00:00", 1.0)]
        entry = self.archive.remember(
            "nodo/raro", meta("a-1", "e-1", 11, "2026-10-03T00:00:00+00:00",
                              "2026-10-10T23:59:59+00:00"),
            detail("ROBOFOREX/ECN:1", rows),
        )
        self.assertTrue(self.archive.knows("nodo/raro", "a-1", "e-1"))
        self.assertFalse(self.archive.knows("nodo/raro", "a-1", "e-2"))
        self.assertEqual(entry["strategies"], ["ROBOFOREX/ECN:1"])
        stored = self.archive.detail("nodo/raro", entry)
        self.assertEqual(len(stored["operation_comparisons"]), 1)
        # Un identificador con barras no puede salirse de su carpeta.
        self.assertEqual(len(list((Path(self.temporary.name) / "history").iterdir())), 1)

    def test_rewriting_the_same_execution_does_not_duplicate_the_index(self) -> None:
        for _attempt in range(3):
            self.archive.remember(
                "n", meta("a-1", "e-1", 11, "2026-10-03T00:00:00+00:00",
                          "2026-10-10T23:59:59+00:00"),
                detail("S:1", [trade("EURUSD", "2026-10-10T10:00:00+00:00", 1.0)]),
            )
        self.assertEqual(len(self.archive.index("n")), 1)

    def test_a_repaired_portfolio_inherits_the_history_of_the_one_it_replaces(self) -> None:
        # El portafolio viejo sale de «Portafolios activos» y no se vuelve a
        # auditar. Sus estrategias siguen vivas en el nuevo, y lo que se midió
        # de ellas tiene que seguir contando.
        self.archive.remember(
            "n", meta("a-104", "e-1", 104, "2026-09-01T00:00:00+00:00",
                      "2026-09-08T23:59:59+00:00"),
            detail("S:1", [trade("EURUSD", "2026-09-05T10:00:00+00:00", 2.0)]),
        )
        self.archive.remember(
            "n", meta("a-148", "e-2", 148, "2026-10-01T00:00:00+00:00",
                      "2026-10-08T23:59:59+00:00"),
            detail("S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 3.0)]),
        )
        self.archive.remember(
            "n", meta("a-200", "e-3", 200, "2026-10-01T00:00:00+00:00",
                      "2026-10-08T23:59:59+00:00"),
            detail("S:9", [trade("GBPUSD", "2026-10-05T10:00:00+00:00", 5.0)]),
        )
        related = self.archive.related("n", "a-148", ["S:1"])
        self.assertEqual([row["portfolio_id"] for row in related], [104, 148])
        self.assertEqual([row["own"] for row in related], [False, True])
        # Un portafolio que no comparte ninguna estrategia no se cuela.
        self.assertNotIn(200, [row["portfolio_id"] for row in related])


class AccumulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = LiveAuditArchive(Path(self.temporary.name) / "history")

    def test_the_day_repeated_by_the_chained_period_is_counted_once(self) -> None:
        # El cron encadena arrancando en el último día ya auditado: esa jornada
        # llega dos veces y sin deduplicar doblaría la muestra.
        repeated = trade("EURUSD", "2026-10-10T10:00:00+00:00", 1.0)
        self.archive.remember(
            "n", meta("a-1", "e-1", 11, "2026-10-03T00:00:00+00:00",
                      "2026-10-10T23:59:59+00:00"),
            detail("S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 4.0), repeated]),
        )
        current = detail("S:1", [repeated, trade("EURUSD", "2026-10-14T10:00:00+00:00", 2.0)])
        accumulated = live_audit_history.accumulate(self.archive, "n", "a-1", current)
        self.assertEqual(len(accumulated["trades"]["S:1"]), 3)
        self.assertEqual(accumulated["audits"], 1)

    def test_overlapping_periods_do_not_inflate_the_audited_days(self) -> None:
        entries = [
            {"period_start": "2026-10-03T00:00:00+00:00", "period_end": "2026-10-10T23:59:59+00:00"},
            {"period_start": "2026-10-10T00:00:00+00:00", "period_end": "2026-10-17T23:59:59+00:00"},
        ]
        # Del 3 al 17 inclusive son quince días, no los dieciséis que saldrían
        # de sumar los dos periodos por separado.
        self.assertAlmostEqual(live_audit_history.covered_days(entries), 15.0, places=1)
        self.assertAlmostEqual(live_audit_history.covered_days(entries[:1]), 8.0, places=1)

    def test_the_series_carries_each_execution_trimmed(self) -> None:
        self.archive.remember(
            "n", meta("a-1", "e-1", 11, "2026-10-03T00:00:00+00:00",
                      "2026-10-10T23:59:59+00:00"),
            detail("S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 4.0)]),
        )
        series = live_audit_history.series(
            self.archive, "n", "a-1", detail("S:1", []),
        )
        self.assertEqual(series["audits"], 1)
        self.assertEqual(series["portfolios"], [11])
        execution = series["executions"][0]
        self.assertNotIn("file", execution)
        self.assertEqual(execution["portfolio_id"], 11)
        self.assertIn("operation_comparisons", execution["comparison_detail"])
        # Lo que la pantalla no usa no viaja al navegador.
        self.assertNotIn("methodology", execution["comparison_detail"])

    def test_strategies_are_read_from_both_places_the_node_writes_them(self) -> None:
        self.assertEqual(strategies_of(detail("S:1", [])), ["S:1"])
        self.assertEqual(strategies_of({}), [])
        self.assertEqual(trimmed_detail({}), {})


class DegradationIsCumulativeTests(unittest.TestCase):
    def test_the_report_can_measure_the_whole_series_instead_of_the_period(self) -> None:
        from mt5_manager import manager_live_audit_routes

        result = {
            "period_start": "2026-10-03T00:00:00+00:00",
            "period_end": "2026-10-10T23:59:59+00:00",
            "comparison_detail": detail("S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 4.0)]),
        }
        period = manager_live_audit_routes.degradation_report(object(), result)
        self.assertEqual(period["strategies"][0]["trades"], 1)
        self.assertAlmostEqual(period["period_days"], 8.0, places=1)
        accumulated = manager_live_audit_routes.degradation_report(
            object(), result,
            grouped={"S:1": [
                trade("EURUSD", f"2026-10-0{day}T10:00:00+00:00", 1.0) for day in range(1, 9)
            ]},
            days=56.0,
        )
        self.assertEqual(accumulated["strategies"][0]["trades"], 8)
        self.assertEqual(accumulated["period_days"], 56.0)
        # Con ocho operaciones ya no es «insuficiente» por tamaño de muestra.
        self.assertNotEqual(accumulated["strategies"][0]["verdict"], "insufficient")


def completed_audit() -> dict[str, object]:
    return {
        "status": "completed", "audit_id": "e-1",
        "last_result": {
            "portfolio_id": 11, "portfolio_type": "aggressive",
            "completed_at": "2026-10-10T23:00:00+00:00",
            "period_start": "2026-10-03T00:00:00+00:00",
            "period_end": "2026-10-10T23:59:59+00:00",
            "account": {"currency": "EUR"},
            "comparison_detail": detail(
                "S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 4.0)],
            ),
        },
    }


class FakeHandler:
    """Lo que la ruta necesita del handler: un nodo, los perfiles y responder."""

    def __init__(self, archive: LiveAuditArchive) -> None:
        self.sent: list[tuple[int, object]] = []
        settings = type("Settings", (), {"state": staticmethod(
            lambda _node_id: {"profiles": {"a-1": {"deployment_name": "Cartera viva"}}},
        )})()
        self.server = type("Server", (), {
            "live_audit_archive": archive, "live_audit_settings": settings,
        })()

    @staticmethod
    def _node(node_id: str) -> dict[str, object]:
        return {"id": node_id, "name": "Nodo N"}

    def _send_json(self, status: int, payload: object) -> None:
        self.sent.append((status, payload))


class HistoryRouteTests(unittest.TestCase):
    """La ruta que alimenta la tabla, incluida la ejecución que se está viendo."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = LiveAuditArchive(Path(self.temporary.name) / "history")

    def _get(self, handler: FakeHandler) -> bool:
        from unittest import mock

        from mt5_manager import manager_schedule_routes

        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(200, {"audit": completed_audit()}),
        ):
            return manager_schedule_routes.handle_get(
                handler, type("Parsed", (), {"path": "", "query": ""})(),
                ["api", "nodes", "n", "live-audits", "a-1", "history"],
            )

    def test_it_archives_what_it_serves_so_the_series_never_misses_today(self) -> None:
        handler = FakeHandler(self.archive)
        self.assertTrue(self._get(handler))
        status, payload = handler.sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["audits"], 1)
        self.assertEqual(payload["portfolios"], [11])
        self.assertEqual(payload["node"]["name"], "Nodo N")
        self.assertTrue(self.archive.knows("n", "a-1", "e-1"))
        # Y servirla otra vez no la duplica.
        self._get(FakeHandler(self.archive))
        self.assertEqual(len(self.archive.index("n")), 1)


class HistoryScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from mt5_manager.manager_handler import STATIC_FILES

        cls.static_files = STATIC_FILES
        static = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"
        cls.page = (static / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.script = (static / "live_audit_history.js").read_text(encoding="utf-8")
        cls.analysis = (static / "live_audit_analysis.js").read_text(encoding="utf-8")

    def test_the_second_table_lives_next_to_the_first_one(self) -> None:
        self.assertIn("live_audit_history.js", self.static_files)
        self.assertIn('<script src="/live_audit_history.js"></script>', self.page)
        self.assertIn('id="history-body"', self.page)
        self.assertIn('id="history-count"', self.page)
        # La del periodo se queda como estaba.
        self.assertIn('id="reproduction-body"', self.page)

    def test_the_historic_table_uses_the_same_verdict_as_the_period_one(self) -> None:
        self.assertIn("LiveAuditReproduction.verdict", self.script)
        self.assertIn("LiveAuditReproduction.numbers", self.script)
        self.assertIn("LiveAuditReproduction.compare", self.script)
        self.assertIn("strategyGroups(", self.script)
        self.assertIn("/history", self.script)

    def test_it_says_when_a_row_comes_from_a_portfolio_that_is_no_longer_audited(self) -> None:
        self.assertIn("Ya no está en el portafolio que se audita", self.script)
        self.assertIn("inherited_portfolios", self.script)

    def test_the_degradation_table_announces_that_it_accumulates(self) -> None:
        self.assertIn("renderAccumulatedNote", self.analysis)
        self.assertIn("acumulado ${escapeHtml(atHistoryLot", self.analysis)
        self.assertIn("todas las auditorías archivadas", self.page)
        # El desglose por estrategia sigue siendo el de la auditoría actual.
        self.assertIn("Análisis por estrategia", self.page)


if __name__ == "__main__":
    unittest.main()
