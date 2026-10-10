"""El veredicto de degradación por estrategia del periodo auditado."""

from __future__ import annotations

import math
import unittest

from mt5_manager.live_audit_degradation import (
    MINIMUM_TRADES, REFERENCE_LOT, StrategyHistory, compare, expected_trades,
    history_from_metrics, observed_per_lot, read_histories, split_candidate_id,
)


def metrics(**overrides) -> dict:
    base = {
        "trades": 670, "net_profit": 792.43, "raw_net_profit": 792.43,
        "avg_trade": 1.1827, "sqn": 8.9136, "active_months": 60,
        "drawdown": 30.98, "profit_factor": 3.1867,
    }
    base.update(overrides)
    return base


def trades(profits: list[float], volume: float = 0.01) -> list[dict]:
    return [{"profit": profit, "volume": volume} for profit in profits]


class StrategyHistoryTests(unittest.TestCase):
    def test_the_deviation_comes_out_of_the_sqn(self) -> None:
        # SQN = media / desviacion * raiz(n). Es la unica dispersion que guarda
        # la memoria, y sin ella el veredicto seria una opinion sobre dos medias.
        history = history_from_metrics(metrics())
        expected = 1.1827 * math.sqrt(670) / 8.9136
        self.assertAlmostEqual(history.trade_deviation, expected, places=6)

    def test_without_sqn_there_is_no_dispersion_to_invent(self) -> None:
        for broken in (None, 0, ""):
            self.assertIsNone(history_from_metrics(metrics(sqn=broken)).trade_deviation)

    def test_the_raw_profit_wins_over_the_normalised_one(self) -> None:
        # `net_profit` puede venir normalizado por nocional en algunos brokers
        # y entonces no es dinero comparable con el de una cuenta real.
        history = history_from_metrics(metrics(net_profit=195.92, raw_net_profit=792.43))
        self.assertEqual(history.net_profit, 792.43)

    def test_metrics_without_trades_are_not_a_history(self) -> None:
        self.assertIsNone(history_from_metrics(None))
        self.assertIsNone(history_from_metrics({}))
        self.assertIsNone(history_from_metrics(metrics(trades=0)))

    def test_the_average_is_derived_when_the_memory_does_not_carry_it(self) -> None:
        history = history_from_metrics(metrics(avg_trade=None))
        self.assertAlmostEqual(history.average_trade, 792.43 / 670, places=6)


class ObservedTests(unittest.TestCase):
    def test_the_period_is_measured_per_lot_and_not_per_operation(self) -> None:
        # Dividir por volumen y no por numero de operaciones deja el periodo en
        # la misma unidad que el historico aunque la estrategia operara
        # volumenes distintos.
        mixed = [{"profit": 10.0, "volume": 0.02}, {"profit": 5.0, "volume": 0.01}]
        value, volume = observed_per_lot(mixed)
        self.assertAlmostEqual(value, 15.0 / 0.03, places=6)
        self.assertAlmostEqual(volume, 0.03, places=9)

    def test_operations_without_volume_cannot_be_measured_per_lot(self) -> None:
        self.assertIsNone(observed_per_lot([{"profit": 10.0, "volume": 0}]))
        self.assertIsNone(observed_per_lot([]))

    def test_the_expected_count_comes_from_the_historical_rate(self) -> None:
        history = history_from_metrics(metrics(trades=600, active_months=60))
        # 600 operaciones en 60 meses son 10 al mes: en una semana, unas 2,3.
        self.assertAlmostEqual(expected_trades(history, 7), 600 / (60 * 30.436875) * 7, places=6)
        self.assertIsNone(expected_trades(history, 0))
        self.assertIsNone(expected_trades(history_from_metrics(metrics(active_months=0)), 7))


class CompareTests(unittest.TestCase):
    def history(self, **overrides) -> StrategyHistory:
        return history_from_metrics(metrics(**overrides))

    def test_a_strategy_at_its_historical_average_is_normal(self) -> None:
        # 1,1827 por operacion a lote 0,01 es exactamente la media historica.
        report = compare(self.history(), trades([1.1827] * 20), 30)
        self.assertEqual(report["verdict"], "normal")
        self.assertAlmostEqual(report["deviations"], 0.0, places=6)
        self.assertAlmostEqual(report["observed_per_lot"], 118.27, places=4)
        self.assertAlmostEqual(report["history_per_lot"], 118.27, places=4)

    def sigmas_below(self, history: StrategyHistory, sigmas: float, count: int = 20) -> dict:
        """Periodo cuya media cae `sigmas` errores estandar de la historica."""
        target = history.average_trade + sigmas * history.trade_deviation / math.sqrt(count)
        return compare(history, trades([target] * count), 30)

    def test_two_sigmas_below_the_average_degrade(self) -> None:
        # No se construye justo sobre el umbral: a -2,0 exactos el redondeo en
        # coma flotante cae a un lado o a otro y la prueba seria un sorteo.
        report = self.sigmas_below(self.history(), -2.4)
        self.assertEqual(report["verdict"], "degraded")
        self.assertAlmostEqual(report["deviations"], -2.4, places=6)

    def test_one_sigma_below_is_only_a_warning(self) -> None:
        self.assertEqual(self.sigmas_below(self.history(), -1.2)["verdict"], "warning")
        # Y medio sigma todavia no es nada.
        self.assertEqual(self.sigmas_below(self.history(), -0.5)["verdict"], "normal")

    def test_two_sigmas_above_is_better_than_the_history(self) -> None:
        self.assertEqual(self.sigmas_below(self.history(), 2.5)["verdict"], "better")
        self.assertEqual(self.sigmas_below(self.history(), 1.4)["verdict"], "normal")

    def test_a_noisy_strategy_absorbs_a_big_drop_without_crying(self) -> None:
        # El caso que justifica usar la dispersion: ETHUSD de AXI #67 cayo un
        # 170 % frente a su media y aun asi esta dentro de lo normal, porque su
        # SQN de 1,83 dice que siempre fue asi de ruidosa. Un porcentaje a secas
        # habria dado la alarma en la estrategia equivocada.
        noisy = history_from_metrics(metrics(trades=541, raw_net_profit=124.36,
                                             avg_trade=0.2299, sqn=1.8296))
        report = compare(noisy, trades([-0.1623] * 12), 34)
        self.assertEqual(report["verdict"], "normal")
        self.assertLess(report["difference_pct"], -100)
        self.assertGreater(report["deviations"], -1.0)

    def test_too_few_operations_say_so_instead_of_guessing(self) -> None:
        report = compare(self.history(), trades([-50.0] * (MINIMUM_TRADES - 1)), 30)
        self.assertEqual(report["verdict"], "insufficient")
        self.assertEqual(report["minimum_trades"], MINIMUM_TRADES)
        # Aun sin veredicto se informa de lo medido, que es lo que hay.
        self.assertIsNotNone(report["observed_per_lot"])
        self.assertIsNone(report["deviations"])

    def test_no_operations_and_no_history_are_different_answers(self) -> None:
        self.assertEqual(compare(self.history(), [], 30)["verdict"], "no_trades")
        self.assertEqual(compare(None, trades([1.0] * 20), 30)["verdict"], "no_history")
        self.assertIsNone(compare(None, trades([1.0] * 20), 30)["history"])

    def test_without_historical_dispersion_it_refuses_to_rank(self) -> None:
        report = compare(self.history(sqn=None), trades([1.0] * 20), 30)
        self.assertEqual(report["verdict"], "no_dispersion")
        self.assertIsNone(report["deviations"])
        self.assertIsNotNone(report["difference_pct"])

    def test_the_period_lot_does_not_change_the_verdict(self) -> None:
        # Es el motivo de medir por lote: la misma estrategia operada al doble
        # de tamano se comporta igual y tiene que decir lo mismo.
        history = self.history()
        small = compare(history, trades([1.1827] * 20, volume=0.01), 30)
        large = compare(history, trades([2.3654] * 20, volume=0.02), 30)
        self.assertEqual(small["verdict"], large["verdict"])
        self.assertAlmostEqual(small["deviations"], large["deviations"], places=9)
        self.assertAlmostEqual(small["observed_per_lot"], large["observed_per_lot"], places=9)

    def test_the_reference_lot_is_the_start_lots_of_the_generated_sets(self) -> None:
        self.assertEqual(REFERENCE_LOT, 0.01)
        report = compare(self.history(), trades([1.1827] * 20), 30)
        self.assertEqual(report["reference_lot"], 0.01)


class FakeConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql: str, parameters: tuple = ()):  # noqa: D102
        self.queries.append((sql, parameters))
        return self

    def fetchall(self) -> list[tuple]:
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class FakeSource:
    def __init__(self, memories: dict[str, list[tuple]]) -> None:
        self.memory_sources = [(label, label) for label in memories]
        self.memories = memories
        self.opened: list[str] = []

    def connect_memory(self, memory):  # noqa: D102
        self.opened.append(memory)
        return FakeConnection(self.memories[memory])


class ReadHistoriesTests(unittest.TestCase):
    def test_the_candidate_id_carries_its_account_and_its_number(self) -> None:
        self.assertEqual(split_candidate_id("AXI/STANDARD:3813"), ("AXI/STANDARD", 3813))
        # El magic de una real suelta o el texto de una ejecucion vieja no lo son.
        for alien in ("1002", "", "AXI/STANDARD:", ":12", "sin dos puntos"):
            self.assertIsNone(split_candidate_id(alien))

    def test_each_account_is_asked_only_for_its_own_candidates(self) -> None:
        import json as json_module
        payload = json_module.dumps(metrics())
        source = FakeSource({
            "AXI/STANDARD": [(3813, payload)],
            "AXI/PRO": [(99, payload)],
        })
        found = read_histories(source, ["AXI/STANDARD:3813", "1002"])
        self.assertEqual(list(found), ["AXI/STANDARD:3813"])
        self.assertEqual(found["AXI/STANDARD:3813"].trades, 670)
        # La cuenta sin candidatos pedidos ni se abre.
        self.assertEqual(source.opened, ["AXI/STANDARD"])

    def test_an_unreadable_memory_leaves_the_strategy_without_history(self) -> None:
        # La base del agente puede estar bloqueada por una generacion o el
        # disco de red caido. Eso no puede tumbar la pantalla: la estrategia
        # sale «sin historico», que es exactamente lo que se sabe de ella.
        class Broken(FakeSource):
            def connect_memory(self, memory):
                raise OSError("la base del agente no responde")

        source = Broken({"AXI/STANDARD": []})
        self.assertEqual(read_histories(source, ["AXI/STANDARD:3813"]), {})

    def test_corrupt_metrics_are_skipped_without_killing_the_rest(self) -> None:
        import json as json_module
        source = FakeSource({"AXI/STANDARD": [
            (1, "{no es json"), (2, None), (3, json_module.dumps(metrics())),
        ]})
        found = read_histories(
            source, ["AXI/STANDARD:1", "AXI/STANDARD:2", "AXI/STANDARD:3"],
        )
        self.assertEqual(list(found), ["AXI/STANDARD:3"])


class DegradationReportTests(unittest.TestCase):
    def result(self, rows: list[dict]) -> dict:
        return {
            "period_start": "2026-09-21T00:00:00+00:00",
            "period_end": "2026-09-27T23:59:59+00:00",
            "comparison_detail": {
                "operation_comparisons": rows,
                "strategy_summary": [
                    {"strategy": "AXI/STANDARD:3813", "strategy_set": "XAUUSD_H1.set"},
                ],
            },
        }

    def row(self, strategy: str, profit: float, volume: float = 0.02) -> dict:
        return {
            "strategy": strategy,
            "tester": {
                "strategy": strategy, "symbol": "XAUUSD", "volume": volume,
                "profit": profit, "close_time": "2026-09-22T10:32:58+00:00",
            },
        }

    def test_the_report_groups_the_tester_operations_by_strategy(self) -> None:
        import json as json_module
        from mt5_manager.manager_live_audit_routes import degradation_report

        source = FakeSource({"AXI/STANDARD": [(3813, json_module.dumps(metrics()))]})
        report = degradation_report(source, self.result(
            [self.row("AXI/STANDARD:3813", 2.3654) for _ in range(20)]
        ))
        self.assertAlmostEqual(report["period_days"], 7, delta=0.01)
        self.assertEqual(len(report["strategies"]), 1)
        row = report["strategies"][0]
        self.assertEqual(row["strategy_set"], "XAUUSD_H1.set")
        self.assertEqual(row["symbol"], "XAUUSD")
        self.assertEqual(row["trades"], 20)
        # 2,3654 a lote 0,02 son 118,27 por lote: la media historica exacta.
        self.assertAlmostEqual(row["observed_per_lot"], 118.27, places=4)
        self.assertEqual(row["verdict"], "normal")

    def test_an_operation_without_close_is_not_part_of_the_period(self) -> None:
        from mt5_manager.manager_live_audit_routes import degradation_report

        open_row = self.row("AXI/STANDARD:3813", 1.0)
        open_row["tester"] = {**open_row["tester"], "close_time": None}
        report = degradation_report(FakeSource({}), self.result([open_row]))
        self.assertEqual(report["strategies"], [])

    def test_a_strategy_outside_the_pool_is_reported_without_history(self) -> None:
        from mt5_manager.manager_live_audit_routes import degradation_report

        report = degradation_report(
            FakeSource({}), self.result([self.row("1002", 1.0) for _ in range(20)]),
        )
        self.assertEqual(report["strategies"][0]["verdict"], "no_history")


if __name__ == "__main__":
    unittest.main()
