"""El aviso de que una estrategia dejo de parecerse a su historico.

El veredicto DEGRADA ya existia en la tabla «contra el historico» de Analisis,
pero habia que abrir esa pantalla uso por uso para verlo. Aqui se comprueba lo
que falta: que llega a la campana -y por ella al dispositivo- y que queda
escrito donde lo lee la tarjeta de «Portafolios activos».

El historico se sirve desde una memoria UBS de verdad, en sqlite y en memoria,
para que el criterio que se mide sea el de `live_audit_degradation` y no un
doble que diga lo que convenga.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_degradation_alerts, live_audit_watch
from mt5_manager.live_audit_archive import LiveAuditArchive
from mt5_manager.live_audit_watch import LiveAuditRunState
from tests.live_audit.live_audit_cron_base import FakeServer, audit_state

LABEL = "AXI/STANDARD"
DEGRADING = f"{LABEL}:3813"
STEADY = f"{LABEL}:4201"

# Media de +2 por operacion a 0,01 lotes y SQN 2 sobre 200 operaciones: la
# desviacion por operacion que se despeja es raiz(200), o sea 14,14.
HISTORY = {
    "trades": 200, "net_profit": 400.0, "avg_trade": 2.0, "sqn": 2.0,
    "active_months": 60, "drawdown": 100.0, "profit_factor": 1.5,
}

# A 0,01 lotes el historico rinde 200 por lote. Con doce operaciones, -20 por
# operacion son -5,4 sigma y -4 son -1,5 (AVISO).
#
# -10 bastaba cuando DEGRADA era cruzar -2 sigma. Ya no: el cruce es la
# condicion de entrada y `live_audit_degradation_evidence` lo contrasta contra
# una frontera que aguanta mirar cada semana, que con doce operaciones pide
# ~4,1 sigma. Aqui se comprueba el camino del aviso, no el criterio -ese vive
# en `test_live_audit_degradation_evidence`-, asi que la estrategia rota lo
# esta sin lugar a dudas.
DEGRADED_PROFIT = -20.0
WARNING_PROFIT = -4.0

# Y -10 son -2,94 sigma: cruza el umbral y no supera la evidencia. Es el caso
# que la tarjeta tiene que nombrar sin llevarlo al movil.
CROSSING_PROFIT = -10.0


def detail(*rows: tuple[str, str, str, float, int]) -> dict:
    """Una comparacion con las operaciones del tester de cada estrategia."""
    comparisons = []
    summary = []
    for strategy, strategy_set, symbol, profit, count in rows:
        summary.append({"strategy": strategy, "strategy_set": strategy_set})
        for index in range(count):
            day = f"2026-01-{index + 1:02d}"
            comparisons.append({
                "strategy": strategy, "strategy_set": strategy_set,
                "tester": {
                    "strategy": strategy, "symbol": symbol,
                    "open_time": f"{day}T08:00:00+00:00",
                    "close_time": f"{day}T20:00:00+00:00",
                    "volume": 0.01, "profit": profit,
                },
            })
    return {"operation_comparisons": comparisons, "strategy_summary": summary}


def memory(*candidates: str):
    """Una memoria UBS que conoce el backtest de esos candidatos."""

    class Source:
        memory_sources = [(LABEL, "memoria.db")]

        def __init__(self, _node: dict) -> None:
            pass

        @contextlib.contextmanager
        def connect_memory(self, _memory):
            connection = sqlite3.connect(":memory:")
            connection.execute("create table candidates (id integer, metrics_json text)")
            connection.executemany(
                "insert into candidates values (?, ?)",
                [
                    (int(candidate.rpartition(":")[2]), json.dumps(HISTORY))
                    for candidate in candidates
                ],
            )
            try:
                yield connection
            finally:
                connection.close()

    # Envuelta en un doble para poder contar cuantas veces se abre la memoria
    # del agente: medir de mas es justo lo que esta ruta no puede permitirse.
    return mock.patch(
        "mt5_manager.manager_live_audit_reports.PortfolioSource",
        mock.Mock(side_effect=Source),
    )


class DegradationAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.server = FakeServer(self.root, audit_state())
        self.server.live_audit_archive = LiveAuditArchive(self.root / "archivo")
        self.pushed: list[dict] = []
        self.server.live_audit_notifications.announce = self.pushed.append

    def archive(self, execution_id: str, comparison: dict) -> None:
        self.server.live_audit_archive.remember(
            "n",
            {
                "audit_key": "11", "execution_id": execution_id, "portfolio_id": 11,
                "status": "completed", "executed_at": f"2026-01-15T0{len(execution_id)}:00:00+00:00",
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-01-15T00:00:00+00:00",
            },
            comparison,
        )

    def review(self) -> None:
        live_audit_degradation_alerts.review(
            self.server, {"id": "n", "name": "Nodo N"}, "n", "11",
            {"portfolio_id": 11, "deployment_name": "Cartera viva"},
        )

    def stored(self) -> dict:
        return live_audit_degradation_alerts.stored(self.server, "n", "11")

    def test_a_degraded_strategy_rings_the_bell_and_reaches_the_device(self) -> None:
        self.archive("run-1", detail((DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12)))
        with memory(DEGRADING):
            self.review()
        events = self.server.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "strategy_degraded")
        self.assertEqual(events[0]["level"], "warning")
        self.assertIn("Cartera viva", events[0]["title"])
        self.assertIn("EURUSD · uno.set", events[0]["message"])
        self.assertIn("live_audit_analysis.html", events[0]["link"])
        # El aviso del escritorio sale por el mismo camino que el de la
        # campana: sin esto el veredicto se quedaria en la pantalla.
        self.assertEqual([event["kind"] for event in self.pushed], ["strategy_degraded"])

    def test_the_card_reads_what_the_watcher_measured(self) -> None:
        self.archive("run-1", detail(
            (DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12),
            (STEADY, "dos.set", "GBPUSD", WARNING_PROFIT, 12),
        ))
        with memory(DEGRADING, STEADY):
            self.review()
        stored = self.stored()
        self.assertEqual(
            [(row["symbol"], row["strategy_set"], row["verdict"]) for row in stored["strategies"]],
            [("EURUSD", "uno.set", "degraded")],
        )
        self.assertEqual(stored["watching"], 1)
        self.assertEqual(stored["compared"], 2)
        self.assertTrue(stored["checked_at"])
        self.assertLess(stored["strategies"][0]["deviations"], -2)

    def test_an_aviso_is_counted_but_never_announced(self) -> None:
        self.archive("run-1", detail((STEADY, "dos.set", "GBPUSD", WARNING_PROFIT, 12)))
        with memory(STEADY):
            self.review()
        self.assertEqual([], self.server.events())
        self.assertEqual([], self.stored()["strategies"])
        self.assertEqual(self.stored()["watching"], 1)

    def test_a_crossing_is_named_on_the_card_and_kept_off_the_device(self) -> None:
        # Cruza -2,94 sigma pero la evidencia no lo confirma. La tarjeta tiene
        # que nombrarlo -el recuento a secas lo escondia- y el movil no, que
        # es justo la diferencia entre «vigila esto» y «cambia esto».
        self.archive("run-1", detail((DEGRADING, "uno.set", "EURUSD", CROSSING_PROFIT, 12)))
        with memory(DEGRADING):
            self.review()
        self.assertEqual([], self.server.events())
        self.assertEqual([], self.pushed)
        stored = self.stored()
        self.assertEqual([], stored["strategies"])
        self.assertEqual(
            [(row["symbol"], row["verdict"]) for row in stored["crossing"]],
            [("EURUSD", "warning")],
        )
        self.assertLess(stored["crossing"][0]["deviations"], -2)
        self.assertEqual(stored["watching"], 1)

    def test_the_card_separates_the_three_facts(self) -> None:
        # Degradar, cruzar sin confirmar y operar menos no son el mismo hecho
        # y no se deciden igual: solo el primero va al movil.
        found = live_audit_degradation_alerts.flagged({"strategies": [
            {"strategy": "a", "symbol": "EURUSD", "verdict": "degraded", "trades": 30},
            {"strategy": "b", "symbol": "GBPUSD", "verdict": "warning", "trades": 12,
             "evidence": {"crossed": True}},
            {"strategy": "c", "symbol": "USDJPY", "verdict": "warning", "trades": 9},
            {"strategy": "d", "symbol": "XAUUSD", "verdict": "insufficient", "trades": 2,
             "rate": {"flagged": True, "observed": 2, "expected": 10.8}},
        ]})
        self.assertEqual([row["symbol"] for row in found["degraded"]], ["EURUSD"])
        self.assertEqual([row["symbol"] for row in found["crossing"]], ["GBPUSD"])
        self.assertEqual([row["symbol"] for row in found["slow"]], ["XAUUSD"])
        self.assertEqual(found["slow"][0]["observed"], 2)
        self.assertEqual(found["slow"][0]["expected"], 10.8)
        # `watching` sigue contando los dos avisos, cruzara o no.
        self.assertEqual(found["watching"], 2)
        self.assertEqual(found["compared"], 4)

    def test_an_aviso_that_never_crossed_is_only_counted(self) -> None:
        # El de -1,5 sigma no se nombra: ahi no ha pasado nada que mirar.
        self.archive("run-1", detail((STEADY, "dos.set", "GBPUSD", WARNING_PROFIT, 12)))
        with memory(STEADY):
            self.review()
        self.assertEqual([], self.stored()["crossing"])
        self.assertEqual(self.stored()["watching"], 1)

    def test_the_same_execution_is_only_measured_once(self) -> None:
        self.archive("run-1", detail((DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12)))
        with memory(DEGRADING) as source:
            self.review()
            self.review()
        self.assertEqual(len(self.server.events()), 1)
        self.assertEqual(source.call_count, 1)

    def test_a_change_of_criterion_remeasures_what_is_already_archived(self) -> None:
        # El candado es el `execution_id`: sin la marca del criterio, cambiarlo
        # no llega a la tarjeta hasta la auditoria siguiente y la portada se
        # queda una semana enseñando un DEGRADA que el criterio nuevo no firma.
        self.archive("run-1", detail((DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12)))
        with memory(DEGRADING) as source:
            self.review()
            with mock.patch.object(live_audit_degradation_alerts, "CRITERION", "otro"):
                self.review()
        self.assertEqual(source.call_count, 2)
        runs = self.server.live_audit_runs
        record = runs.record(runs.key("n", "11"))
        self.assertTrue(str(record["degradation_execution"]).endswith("@otro"))
        # Y lo ya anunciado no se vuelve a anunciar por haber vuelto a medir.
        self.assertEqual(len(self.server.events()), 1)

    def test_a_strategy_that_keeps_degrading_is_not_announced_again(self) -> None:
        # Una lista de avisos que no se puede vaciar deja de leerse: lo nuevo
        # no se distingue de lo de siempre.
        rows = detail((DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12))
        self.archive("run-1", rows)
        with memory(DEGRADING):
            self.review()
            self.archive("run-2", rows)
            self.review()
        self.assertEqual(len(self.server.events()), 1)
        self.assertEqual(len(self.stored()["strategies"]), 1)

    def test_only_the_strategy_that_just_broke_is_announced(self) -> None:
        # La muestra es acumulativa, asi que la segunda auditoria no sustituye
        # a la primera: en `run-1` `dos.set` aun no concluia nada -tres
        # operaciones- y con las doce de `run-2` ya se sale de su historico.
        with memory(DEGRADING, STEADY):
            self.archive("run-1", detail(
                (DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12),
                (STEADY, "dos.set", "GBPUSD", 5.0, 3),
            ))
            self.review()
            self.archive("run-2", detail(
                (DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12),
                (STEADY, "dos.set", "GBPUSD", DEGRADED_PROFIT, 12),
            ))
            self.review()
        events = self.server.events()
        self.assertEqual(len(events), 2)
        self.assertIn("GBPUSD · dos.set", events[0]["message"])
        self.assertNotIn("EURUSD", events[0]["message"])
        self.assertEqual(len(self.stored()["strategies"]), 2)

    def test_without_an_archived_execution_there_is_nothing_to_measure(self) -> None:
        with memory(DEGRADING) as source:
            self.review()
        self.assertEqual([], self.server.events())
        self.assertEqual(0, source.call_count)
        self.assertEqual([], self.stored()["strategies"])

    def test_a_memory_that_cannot_be_read_leaves_the_execution_for_the_next_turn(self) -> None:
        # Un historico ilegible no puede tumbar la vuelta del vigilante ni
        # grabar «no degrada nada» sobre lo que no se pudo medir.
        self.archive("run-1", detail((DEGRADING, "uno.set", "EURUSD", DEGRADED_PROFIT, 12)))
        with mock.patch(
            "mt5_manager.manager_live_audit_reports.PortfolioSource",
            side_effect=OSError("la base del agente no responde"),
        ):
            self.review()
        self.assertEqual([], self.server.events())
        self.assertEqual("", self.stored()["checked_at"])
        with memory(DEGRADING):
            self.review()
        self.assertEqual(len(self.server.events()), 1)


class DegradationInTheWatchTests(unittest.TestCase):
    """La vuelta del vigilante: mide al archivar y solo los usos reales."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_the_watcher_measures_the_live_uses_after_archiving_them(self) -> None:
        server = FakeServer(self.root, audit_state())
        server.live_audit_archive = LiveAuditArchive(self.root / "archivo")
        audits = {
            "11": {"status": "completed"}, "22": {"status": "completed"},
        }
        with mock.patch.object(
            live_audit_degradation_alerts, "review",
        ) as review, mock.patch("mt5_manager.live_audit_executions.archive_execution"):
            live_audit_watch.record_transitions(
                server, {"id": "n", "name": "Nodo N"}, "n", audit_state(), audits,
            )
        # `22` esta configurado pero no opera en real: no sale en «Portafolios
        # activos» y no se mide contra su historico.
        self.assertEqual([call.args[3] for call in review.call_args_list], ["11"])

    def test_nothing_is_measured_while_the_audit_is_still_running(self) -> None:
        server = FakeServer(self.root, audit_state())
        server.live_audit_archive = LiveAuditArchive(self.root / "archivo")
        with mock.patch.object(live_audit_degradation_alerts, "review") as review:
            live_audit_watch.record_transitions(
                server, {"id": "n", "name": "Nodo N"}, "n", audit_state(),
                {"11": {"status": "testing"}},
            )
        review.assert_not_called()
        self.assertEqual(
            {}, server.live_audit_runs.record(LiveAuditRunState.key("n", "11")).get("degradation", {}),
        )


if __name__ == "__main__":
    unittest.main()
