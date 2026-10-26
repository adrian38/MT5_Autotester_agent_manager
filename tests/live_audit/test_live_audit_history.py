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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_executions, live_audit_history, live_audit_watch
from mt5_manager.common import save_json
from mt5_manager.live_audit_archive import LiveAuditArchive, strategies_of, trimmed_detail
from tests.test_live_audit_autolot import SET_STEM, STRATEGY, audit_payload, audit_profile
from tests.live_audit.live_audit_cron_base import FakeServer as CronServer, audit_state
from tests.live_audit.live_audit_history_base import detail, trade




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

    def test_failed_legacy_rows_are_not_part_of_the_comparison_series(self) -> None:
        path = self.archive._index_path("n")
        path.parent.mkdir(parents=True)
        save_json(path, {"executions": [
            {**meta("11", "failed-attempt", 11, "2026-10-01", "2026-10-11"),
             "status": "failed", "file": "stale.json"},
            {**meta("11", "completed-attempt", 11, "2026-10-11", "2026-10-17"),
             "status": "completed", "file": "current.json"},
        ]})
        self.assertEqual(
            [row["execution_id"] for row in self.archive.index("n")],
            ["completed-attempt"],
        )

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


class MutableExecutionArchiveTests(unittest.TestCase):
    """Una carga manual corrige una ejecución sin cambiar su identificador."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        archive = LiveAuditArchive(Path(self.temporary.name) / "history")
        self.server = type("Server", (), {"live_audit_archive": archive})()
        self.archive = archive

    @staticmethod
    def _audit(profit: float, manual_sha: str = "") -> dict[str, object]:
        payload = audit_payload()
        payload["request"] = {**payload["request"], "audit_key": "11", "portfolio_id": 11}
        payload["tester_trades"][0]["profit"] = profit
        artifact = {
            "strategy": STRATEGY, "source_set": f"{SET_STEM}.set",
            "history_quality_pct": 100.0 if manual_sha else 40.0,
        }
        if manual_sha:
            artifact["manual_report_sha256"] = manual_sha
        payload["strategy_artifacts"] = [artifact]
        payload["qualities"] = [artifact["history_quality_pct"]]
        return {
            "status": "completed", "audit_id": payload["audit_id"],
            "last_result": None, "last_payload": payload,
        }

    def test_a_manual_report_replaces_the_verdict_already_archived(self) -> None:
        profile = audit_profile(portfolio_id=11)
        live_audit_executions.archive_execution(
            self.server, {}, "n", "11", self._audit(1.0), profile,
        )
        old_entry = self.archive.index("n")[0]
        old_detail = self.archive.detail("n", old_entry)
        self.assertEqual(old_detail["operation_comparisons"][0]["tester"]["profit"], 1.0)

        live_audit_executions.archive_execution(
            self.server, {}, "n", "11", self._audit(9.0, "a" * 64), profile,
        )

        entries = self.archive.index("n")
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["manual_reports"])
        stored = self.archive.detail("n", entries[0])
        first = stored["operation_comparisons"][0]
        self.assertEqual(first["tester"]["profit"], 9.0)

    def test_a_failed_attempt_cannot_archive_the_previous_success_payload(self) -> None:
        failed = self._audit(1.0)
        failed["status"] = "failed"
        failed["audit_id"] = "new-failed-attempt"
        live_audit_executions.archive_execution(
            self.server, {}, "n", "11", failed, audit_profile(portfolio_id=11),
        )
        self.assertEqual(self.archive.index("n"), [])


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
        with mock.patch.object(
            self.archive, "detail", wraps=self.archive.detail,
        ) as read_detail:
            series = live_audit_history.series(
                self.archive, "n", "a-1", detail("S:1", []),
            )
        self.assertEqual(read_detail.call_count, 1)
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
