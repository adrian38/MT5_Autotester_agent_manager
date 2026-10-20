"""Qué hace falta, además de cruzar −2 σ, para decir DEGRADA.

El caso que obligó a escribir esto es real y está medido: `AXI/STANDARD:10751`
(NAS100.fs M30) en la auditoría 105 del 2026-10-20. Nueve operaciones del
tester acumuladas en dos auditorías, media −3,8467 € por operación a 0,01
lotes contra +0,7678 € de su backtest de 1.363 operaciones, dispersión 6,5155
despejada del SQN: **−2,1249 σ**, que cruzaba el umbral y disparaba el aviso.

Los tres números que lo desmontan, sacados del propio informe del candidato:
la normal le daba 1,68 %, pero 90 de los 1.355 tramos consecutivos de nueve
operaciones del backtest fueron iguales o peores (6,71 %, uno de cada quince),
y el contraste que aguanta mirar la serie en cada auditoría lo deja en 44,12 %.
Aquí se fija ese comportamiento para que no vuelva a salir en la campana.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_audit_degradation_evidence as evidence
from mt5_manager.live_audit_degradation import DEGRADED_SIGMA, StrategyHistory

# Lo medido de `AXI/STANDARD:10751` el 2026-10-20.
AXI_SIGMAS = -2.1249
AXI_TRADES = 9
AXI_OBSERVED = -3.8467


def row(
    deviations: float | None, trades: int, observed: float = -5.0,
    verdict: str = "degraded", strategy: str = "AXI/STANDARD:10751",
) -> dict:
    return {
        "strategy": strategy, "verdict": verdict, "trades": trades,
        "deviations": deviations, "observed_at_reference_lot": observed,
    }


class LevelTests(unittest.TestCase):
    def test_the_level_is_the_one_the_threshold_in_force_already_exigia(self) -> None:
        # No se inventa un nivel nuevo: -2 sigma por la normal son 2,275 %, y
        # mover `DEGRADED_SIGMA` mueve el criterio entero a la vez.
        self.assertAlmostEqual(evidence.alpha_for(DEGRADED_SIGMA), 0.0227501, places=7)
        self.assertAlmostEqual(evidence.one_sided_p(-2.0), 0.0227501, places=7)
        self.assertAlmostEqual(evidence.one_sided_p(0.0), 0.5, places=9)

    def test_the_normal_probability_of_the_axi_case_is_the_one_that_fooled_us(self) -> None:
        self.assertAlmostEqual(evidence.one_sided_p(AXI_SIGMAS), 0.0168, places=4)


class SequentialTests(unittest.TestCase):
    def test_looking_every_week_costs_much_more_que_la_normal(self) -> None:
        # La serie se vuelve a contrastar en cada auditoria y se avisa la
        # primera vez que cruza: la probabilidad de una sola mirada ya no vale.
        measured = evidence.sequential_p(AXI_SIGMAS, AXI_TRADES)
        self.assertAlmostEqual(measured, 0.4412, places=4)
        self.assertGreater(measured, evidence.one_sided_p(AXI_SIGMAS) * 20)

    def test_the_frontier_asks_less_cuanto_mas_larga_es_la_serie(self) -> None:
        # Al nivel de -2 sigma pide 3,96 con nueve operaciones y 3,31 con cien:
        # la prueba gana poder con la muestra en vez de repartir el nivel entre
        # miradas que todavia no se sabe cuantas seran. Y no se dispara: a las
        # trescientas sigue en 3,4, asi que el aviso nunca deja de ser posible.
        level = evidence.alpha_for(DEGRADED_SIGMA)
        self.assertAlmostEqual(self.frontier(9, level), 3.96, places=2)
        self.assertAlmostEqual(self.frontier(100, level), 3.31, places=2)
        self.assertAlmostEqual(self.frontier(300, level), 3.39, places=2)

    def frontier(self, trades: int, alpha: float = 0.05) -> float:
        """Las sigmas que esa muestra necesita para bajar de `alpha`."""
        low, high = 0.0, 20.0
        for _ in range(60):
            middle = (low + high) / 2
            if evidence.sequential_p(-middle, trades) > alpha:
                low = middle
            else:
                high = middle
        return (low + high) / 2

    def test_an_empty_sample_proves_nothing(self) -> None:
        self.assertEqual(evidence.sequential_p(-9.0, 0), 1.0)

    def test_an_impossible_deviation_does_not_blow_up(self) -> None:
        self.assertEqual(evidence.sequential_p(-5000.0, 50), 0.0)


class WindowTests(unittest.TestCase):
    def test_the_windows_are_consecutive_y_solapadas(self) -> None:
        # Solapadas y en su orden: en `1 -9 1 1 -9` repetido, de cada cinco
        # tramos de dos solo `(1,1)` queda por encima de -1. Cuatro de cada
        # cinco cuentan, incluido el que cruza de una repeticion a la
        # siguiente: ahi es donde viven las rachas que la normal no ve.
        rarity = evidence.window_rarity([1.0, -9.0, 1.0, 1.0, -9.0] * 20, -1.0, 2)
        self.assertEqual(rarity["windows"], 99)
        self.assertEqual(rarity["worse"], 79)
        self.assertAlmostEqual(rarity["p"], 80 / 100, places=9)

    def test_nothing_as_bad_is_not_the_same_as_impossible(self) -> None:
        # Convencion de los contrastes por remuestreo: con cero tramos peores
        # la prueba afirma «menos de uno entre los que hay», no «imposible».
        rarity = evidence.window_rarity([1.0] * 100, -5.0, 10)
        self.assertEqual(rarity["worse"], 0)
        self.assertEqual(rarity["windows"], 91)
        self.assertAlmostEqual(rarity["p"], 1 / 92, places=9)
        self.assertAlmostEqual(rarity["resolution"], 1 / 92, places=9)

    def test_a_backtest_too_short_has_no_tramos_que_contar(self) -> None:
        self.assertIsNone(evidence.window_rarity([1.0] * 20, -5.0, 10))
        self.assertIsNone(evidence.window_rarity([1.0] * 100, -5.0, 0))
        self.assertEqual(evidence.window_rarity([1.0] * 39, -5.0, 10)["windows"], 30)


class ConfirmTests(unittest.TestCase):
    def confirm(self, rows: list[dict], series: list[float] | None = None) -> list[dict]:
        evidence.confirm(rows, lambda _row: series, DEGRADED_SIGMA)
        return rows

    def test_the_axi_crossing_stays_a_warning_with_its_numbers_a_la_vista(self) -> None:
        # Tramos como el suyo los hace el propio backtest: 90 de 1.355. Que no
        # degrade no significa esconderlo, significa que la fila cuenta por que.
        backtest = ([1.0] * 9 + [-20.0] * 9) * 80
        crossing = row(AXI_SIGMAS, AXI_TRADES, observed=AXI_OBSERVED)
        measured = self.confirm([crossing], backtest)[0]
        self.assertEqual(measured["verdict"], "warning")
        self.assertTrue(measured["evidence"]["crossed"])
        self.assertFalse(measured["evidence"]["confirmed"])
        self.assertEqual(measured["evidence"]["blocked_by"], ["sequential", "windows"])
        self.assertAlmostEqual(measured["evidence"]["normal_p"], 0.0168, places=4)
        self.assertAlmostEqual(measured["evidence"]["sequential_p"], 0.4412, places=4)
        self.assertGreater(measured["evidence"]["windows"]["worse"], 0)

    def test_a_collapse_that_the_backtest_never_hizo_sigue_degradando(self) -> None:
        broken = row(-6.0, 30, observed=-5.0)
        measured = self.confirm([broken], [1.0] * 200)[0]
        self.assertEqual(measured["verdict"], "degraded")
        self.assertTrue(measured["evidence"]["confirmed"])
        self.assertEqual(measured["evidence"]["blocked_by"], [])
        self.assertEqual(measured["evidence"]["windows"]["worse"], 0)

    def test_the_level_is_split_among_the_strategies_compared(self) -> None:
        # Se comparan todas a la vez: con trece, que alguna cruce -2 sigma por
        # azar deja de ser raro. Las que no dan desvio no se han contrastado y
        # no cuentan en el reparto.
        rows = [row(-0.3, 20, verdict="normal", strategy=f"X:{index}") for index in range(12)]
        rows.append(row(None, 3, verdict="insufficient", strategy="X:99"))
        rows.append(row(-6.0, 30))
        measured = self.confirm(rows, [1.0] * 200)[-1]
        self.assertEqual(measured["evidence"]["compared"], 13)
        self.assertAlmostEqual(
            measured["evidence"]["required"], evidence.alpha_for(DEGRADED_SIGMA) / 13, places=12,
        )
        self.assertIsNone(rows[-2].get("evidence"))

    def test_the_backtest_is_only_opened_for_the_rows_that_crossed(self) -> None:
        # Abrir trece informes de mil operaciones en cada vuelta del vigilante
        # para no cambiar doce respuestas es justo lo que esta ruta no puede
        # permitirse: la memoria del agente ya se abre una vez por uso.
        opened: list[str] = []

        def series_of(measured: dict) -> list[float]:
            opened.append(measured["strategy"])
            return [1.0] * 200

        rows = [row(-0.3, 20, verdict="normal", strategy="X:1"), row(-6.0, 30)]
        evidence.confirm(rows, series_of, DEGRADED_SIGMA)
        self.assertEqual(opened, ["AXI/STANDARD:10751"])

    def test_a_report_that_cannot_be_read_leaves_the_sequential_test_deciding(self) -> None:
        # Un informe que no esta donde dice la memoria no puede inventar una
        # prueba ni bloquear la otra: la fila lo dice con `windows` vacio.
        measured = self.confirm([row(-6.0, 30)], None)[0]
        self.assertEqual(measured["verdict"], "degraded")
        self.assertIsNone(measured["evidence"]["windows"])

    def test_a_backtest_too_short_to_prove_rarity_does_not_block_by_itself(self) -> None:
        # 31 tramos no bajan del 3 %, y el nivel exigido es 2,3 %: la prueba ya
        # ha dicho todo lo que podia -ningun tramo fue igual o peor- y no se
        # interpone.
        measured = self.confirm([row(-6.0, 30)], [1.0] * 60)[0]
        self.assertEqual(measured["evidence"]["windows"]["worse"], 0)
        self.assertGreater(
            measured["evidence"]["windows"]["resolution"], measured["evidence"]["required"],
        )
        self.assertEqual(measured["verdict"], "degraded")

    def test_but_a_tramo_igual_o_peor_blocks_even_in_a_short_backtest(self) -> None:
        # En cuanto el backtest hizo un tramo asi, lo que dice vale por si
        # solo: no hay excusa de resolucion.
        measured = self.confirm([row(-6.0, 30)], [-100.0] * 60)[0]
        self.assertEqual(measured["evidence"]["blocked_by"], ["windows"])
        self.assertEqual(measured["verdict"], "warning")


class BacktestSeriesTests(unittest.TestCase):
    def history(self, trades: int = 3) -> StrategyHistory:
        return StrategyHistory(
            trades=trades, net_profit=30.0, average_trade=10.0, sqn=2.0,
            active_months=60, drawdown=1.0, profit_factor=2.0,
            report_path=r"F:\TRADING\agente\reports\x.htm",
        )

    def report(self, *pairs: tuple[float, float]) -> SimpleNamespace:
        return SimpleNamespace(trades=[
            SimpleNamespace(profit_loss=profit, size=size) for profit, size in pairs
        ])

    def parsed(self, report: SimpleNamespace):
        return mock.patch.object(evidence, "cached_report", return_value=report)

    def test_the_series_is_taken_to_the_reference_lot(self) -> None:
        # Mismo gesto que con las del tester: comparar euros crudos mediria el
        # volumen con el que corrio el backtest, no el comportamiento.
        with self.parsed(self.report((10.0, 0.02), (5.0, 0.01), (1.0, 0.0))):
            series = evidence.backtest_series(
                SimpleNamespace(project=Path("/proyecto")), self.history(trades=2), 0.01,
            )
        self.assertEqual(series, [5.0, 5.0])

    def test_a_report_that_is_not_the_one_the_memory_measured_is_not_used(self) -> None:
        # Si lo parseado no trae las operaciones que la memoria dice, la media
        # contra la que se contrasta salio de otra medicion: no se mide con el.
        with self.parsed(self.report((10.0, 0.01), (5.0, 0.01))):
            self.assertIsNone(evidence.backtest_series(
                SimpleNamespace(project=Path("/proyecto")), self.history(trades=1363), 0.01,
            ))

    def test_without_a_stored_report_there_is_nothing_to_read(self) -> None:
        history = StrategyHistory(
            trades=10, net_profit=1.0, average_trade=0.1, sqn=1.0,
            active_months=1, drawdown=1.0, profit_factor=1.0,
        )
        self.assertIsNone(evidence.backtest_series(object(), history, 0.01))

    def test_an_unreadable_report_is_not_a_crash(self) -> None:
        with mock.patch.object(evidence, "cached_report", side_effect=OSError("no está")):
            self.assertIsNone(evidence.backtest_series(
                SimpleNamespace(project=Path("/proyecto")), self.history(), 0.01,
            ))
        # Y una fuente sin proyecto -el doble de las pruebas del informe- tampoco.
        self.assertIsNone(evidence.backtest_series(object(), self.history(), 0.01))


class RealCaseTests(unittest.TestCase):
    def test_the_axi_deviation_is_the_one_the_memory_sostiene(self) -> None:
        # Que los numeros de arriba son los del candidato y no una invencion:
        # 0,7678 * raiz(1363) / 4,3509 = 6,5155 por operacion.
        deviation = 0.7678 * math.sqrt(1363) / 4.3509
        self.assertAlmostEqual(deviation, 6.5150, places=4)
        sigmas = (AXI_OBSERVED - 0.7678) / (deviation / math.sqrt(AXI_TRADES))
        self.assertAlmostEqual(sigmas, AXI_SIGMAS, places=4)


if __name__ == "__main__":
    unittest.main()
