"""La serie de auditorías: archivo, acumulación y herencia entre portafolios.

El agente sólo guarda la última ejecución de cada uso, así que sin archivo no
hay serie. Y sin serie, la tabla «contra el histórico» contestaba
«INSUFICIENTE» en todas las filas: tres operaciones en una semana no deciden
nada sobre una estrategia.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_audit_history, live_audit_watch
from mt5_manager.live_audit_archive import LiveAuditArchive, strategies_of, trimmed_detail
from tests.test_live_audit_autolot import SET_STEM, audit_payload, audit_profile
from tests.test_live_audit_cron import FakeServer as CronServer, audit_state


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
        self.assertEqual(entry["strategies"], ["ROBOFOREX/ECN:1|ROBOFOREX/ECN:1.set"])
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
        related = self.archive.related("n", "a-148", ["S:1|S:1.set"], [104])
        self.assertEqual([row["portfolio_id"] for row in related], [104, 148])
        self.assertEqual([row["own"] for row in related], [False, True])
        # Un portafolio que no comparte ninguna estrategia no se cuela.
        self.assertNotIn(200, [row["portfolio_id"] for row in related])

    def test_sharing_a_strategy_without_being_a_parent_lends_nothing(self) -> None:
        # Dos portafolios independientes pueden llevar el mismo candidato. Sin
        # genealogía que los una, el histórico de uno no es el del otro.
        for portfolio, key, execution in ((104, "a-104", "e-1"), (148, "a-148", "e-2")):
            self.archive.remember(
                "n", meta(key, execution, portfolio, "2026-09-01T00:00:00+00:00",
                          "2026-09-08T23:59:59+00:00"),
                detail("S:1", [trade("EURUSD", "2026-09-05T10:00:00+00:00", 2.0)]),
            )
        self.assertEqual(
            [row["portfolio_id"] for row in self.archive.related("n", "a-148", ["S:1|S:1.set"])],
            [148],
        )

    def test_a_parent_only_lends_the_strategies_that_survive_in_the_child(self) -> None:
        # El padre llevaba dos; la reparación se quedó con una. La que se fue
        # no vuelve por la puerta de atrás.
        parent = {
            "operation_comparisons": [
                {"strategy": "S:1", "strategy_set": "S:1.set",
                 "tester": trade("EURUSD", "2026-09-05T10:00:00+00:00", 1.0)},
                {"strategy": "S:9", "strategy_set": "S:9.set",
                 "tester": trade("GBPUSD", "2026-09-05T11:00:00+00:00", 7.0)},
            ],
        }
        self.archive.remember(
            "n", meta("a-104", "e-1", 104, "2026-09-01T00:00:00+00:00",
                      "2026-09-08T23:59:59+00:00"), parent,
        )
        related = self.archive.related("n", "a-148", ["S:1|S:1.set"], [104])
        self.assertEqual([row["shared_strategies"] for row in related], [["S:1|S:1.set"]])

    def test_two_manual_portfolios_that_repeat_an_id_are_not_the_same_strategy(self) -> None:
        # Una cartera manual numera los suyos `manual:1`, `manual:2`…, así que
        # el identificador se repite entre carteras que no tienen nada que ver.
        # Lo que las distingue es el `.set`.
        first = {
            "operation_comparisons": [{
                "strategy": "manual:4", "strategy_set": "Gold_Phantom_strat2.set",
                "tester": trade("XAUUSD", "2026-10-05T10:00:00+00:00", 1.0),
            }],
        }
        second = {
            "operation_comparisons": [{
                "strategy": "manual:4", "strategy_set": "otra_cosa.set",
                "tester": trade("EURUSD", "2026-10-05T10:00:00+00:00", 1.0),
            }],
        }
        self.archive.remember(
            "n", meta("a-1", "e-1", 1, "2026-10-01T00:00:00+00:00",
                      "2026-10-08T23:59:59+00:00"), first,
        )
        self.archive.remember(
            "n", meta("a-2", "e-2", 2, "2026-10-01T00:00:00+00:00",
                      "2026-10-08T23:59:59+00:00"), second,
        )
        related = self.archive.related("n", "a-2", strategies_of(second))
        self.assertEqual([row["portfolio_id"] for row in related], [2])


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

    def test_the_series_says_which_days_it_covers_not_only_how_many(self) -> None:
        # El orden del índice es el de ejecución, no el de los periodos: una
        # auditoría lanzada a mano sobre un rango viejo llega la última.
        entries = [
            {"period_start": "2026-10-10T00:00:00+00:00",
             "period_end": "2026-10-17T23:59:59+00:00"},
            {"period_start": "2026-10-03T00:00:00+00:00",
             "period_end": "2026-10-10T23:59:59+00:00"},
        ]
        self.assertEqual(
            live_audit_history.covered_span(entries),
            ("2026-10-03T00:00:00+00:00", "2026-10-17T23:59:59+00:00"),
        )
        self.assertEqual(live_audit_history.covered_span([]), ("", ""))

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
        self.assertEqual(strategies_of(detail("S:1", [])), ["S:1|S:1.set"])
        self.assertEqual(strategies_of({}), [])
        self.assertEqual(trimmed_detail({}), {})

    def test_a_parent_portfolio_only_lends_the_strategies_this_one_keeps(self) -> None:
        # Comparten `S:1`; las otras del padre ya no están en este portafolio y
        # no pueden aparecer en su acumulado.
        sibling = {
            "operation_comparisons": [
                {"strategy": "S:1", "strategy_set": "S:1.set",
                 "tester": trade("EURUSD", "2026-09-05T10:00:00+00:00", 1.0)},
                {"strategy": "S:9", "strategy_set": "S:9.set",
                 "tester": trade("GBPUSD", "2026-09-05T11:00:00+00:00", 7.0)},
            ],
        }
        self.archive.remember(
            "n", meta("a-vieja", "e-1", 104, "2026-09-01T00:00:00+00:00",
                      "2026-09-08T23:59:59+00:00"), sibling,
        )
        current = detail("S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 2.0)])
        accumulated = live_audit_history.accumulate(
            self.archive, "n", "a-nueva", current, inherited_portfolio_ids=(104,),
        )
        self.assertEqual(sorted(accumulated["trades"]), ["S:1"])
        self.assertEqual(len(accumulated["trades"]["S:1"]), 2)
        self.assertEqual(accumulated["inherited"], [104])
        # Sin genealogía, el mismo archivo sólo devuelve lo propio.
        alone = live_audit_history.accumulate(self.archive, "n", "a-nueva", current)
        self.assertEqual(len(alone["trades"]["S:1"]), 1)
        self.assertEqual(alone["inherited"], [])


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


def native_report_html() -> bytes:
    """El HTML nativo de MT5 del que sale el saldo histórico del autolote."""
    headers = [
        "Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price",
        "Order", "Commission", "Swap", "Profit", "Balance", "Comment",
    ]
    rows = [
        ["2026.09.22 09:00:00", "1", "", "balance", "", "0", "0", "", "0", "0",
         "1000", "1000", "deposit"],
        ["2026.09.22 10:00:00", "2", "EURUSD", "buy", "out", "0.1", "1.1", "1", "0", "0",
         "1000", "2000", ""],
    ]
    cell = lambda values: "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
    return ("<html><body>" + cell(["Deals"]) + cell(headers)
            + "".join(cell(row) for row in rows) + "</body></html>").encode()


def autolot_execution(report: dict[str, object]) -> dict[str, object]:
    """Un uso con autolote terminado, tal y como lo enseña el agente."""
    payload = audit_payload()
    payload["request"] = {**payload["request"], "audit_key": "11"}
    payload["real_account_report"] = report
    return {
        "status": "completed", "audit_id": payload["audit_id"],
        "finished_at": "2026-09-22T12:30:00+00:00",
        "last_result": None, "last_payload": payload,
    }


class WatchedArchiveTests(unittest.TestCase):
    """El vigilante archiva lo mismo que la pantalla, autolote incluido.

    Medido el 2026-10-20: de los usos con autolote (#109, #173, #170 y #148)
    ninguno había entrado en la serie, porque el vigilante analizaba sin el
    saldo histórico del informe nativo y el análisis se negaba a inventar un
    lote esperado. El nodo guarda un único payload por uso, así que esa semana
    se perdía para siempre en cuanto corría la cita siguiente.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        state = audit_state()
        state["profiles"]["11"] = {
            **audit_profile(), "portfolio_id": 11, "deployment_name": "Cartera viva",
            "live_trading": True, "autolot": {
                "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
                "rules": {SET_STEM: {"dd_001": 10.0, "risk_pct": 10.0}},
            },
        }
        self.server = CronServer(root, state)
        self.server.live_audit_archive = LiveAuditArchive(root / "historico")

    def _tick(self, audit: dict[str, object], artifact: tuple) -> None:
        def request(_node, _method, path, payload=None, timeout=0):
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_audit_restore_account": True}}
            return 200, {"audits": {"11": audit}}

        with mock.patch("mt5_manager.manager_http.node_request", side_effect=request), \
                mock.patch(
                    "mt5_manager.manager_http.node_artifact_request", return_value=artifact,
                ):
            live_audit_watch.tick(self.server)

    def test_the_watcher_downloads_the_balance_the_autolot_needs(self) -> None:
        contents = native_report_html()
        self._tick(
            autolot_execution({
                "filename": "real.html", "sha256": hashlib.sha256(contents).hexdigest(),
            }),
            (200, contents, "text/html"),
        )
        self.assertTrue(self.server.live_audit_archive.knows("n", "11", "run-autolot"))
        entry = self.server.live_audit_archive.index("n")[0]
        self.assertEqual(entry["period_end"], "2026-09-22T23:59:59+00:00")
        self.assertEqual(entry["deployment_name"], "Cartera viva")

    def test_an_archive_that_cannot_be_done_leaves_a_trace_once(self) -> None:
        # Volver callando es lo que dejó la serie semanal a medias sin que
        # nadie pudiera enterarse hasta mirar la carpeta.
        self._tick(autolot_execution({}), (404, b"", "text/plain"))
        self.assertEqual(self.server.live_audit_archive.index("n"), [])
        events = [row for row in self.server.events() if row["kind"] == "archive_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("Cartera viva", events[0]["title"])
        self.assertIn("autolote", events[0]["message"])
        self._tick(autolot_execution({}), (404, b"", "text/plain"))
        self.assertEqual(
            1, len([row for row in self.server.events() if row["kind"] == "archive_failed"]),
        )


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

    def test_it_says_which_portfolios_it_inherits_from_and_with_what_limit(self) -> None:
        self.assertIn("inherited_portfolios", self.script)
        self.assertIn("hereda el histórico de", self.script)
        self.assertIn("sólo de las estrategias que conserva", self.script)

    def test_the_table_only_shows_the_strategies_the_portfolio_keeps(self) -> None:
        # El servidor ya recorta; la pantalla no puede volver a abrir la mano
        # con las ejecuciones propias, que era lo que hacía antes.
        self.assertIn("function retained()", self.script)
        self.assertIn("if (wanted.size && !wanted.has(identity(group))) return;", self.script)
        self.assertNotIn("execution.own", self.script)

    def test_the_degradation_table_announces_that_it_accumulates(self) -> None:
        self.assertIn("renderAccumulatedNote", self.analysis)
        self.assertIn("acumulado ${escapeHtml(atHistoryLot", self.analysis)
        self.assertIn("todas las auditorías archivadas", self.page)
        # El desglose por estrategia sigue siendo el de la auditoría actual.
        self.assertIn("Análisis por estrategia", self.page)


if __name__ == "__main__":
    unittest.main()
