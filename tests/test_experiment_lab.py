"""Pruebas del laboratorio «Experimenta».

Lo que se protege aquí es lo que la pantalla promete y no existía en UBS: la
ventana se toma de la historia real y no del calendario de hoy, el drawdown se
mide contra el máximo de equity y no contra un presupuesto fijo, la
recomposición de lotes capitaliza de verdad, el margen recorta el
multiplicador en vez de suponer crédito infinito, y el veredicto que se da
cuando el objetivo no se alcanza es un número comprobable, no una opinión.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta

from mt5_manager import experiment_lab as lab
from mt5_manager.experiment_service import (
    DEFAULT_SETTINGS,
    ExperimentCoordinator,
    lab_config,
    normalize_settings,
)
from portfolio_manager.ubs_portfolio import (
    MarginModel,
    PeriodReport,
    RobustStrategySet,
)


def period(symbol: str) -> PeriodReport:
    return PeriodReport(
        period_name="IS", start_year=2020, end_year=2024, symbol=symbol, timeframe="H1",
        pnl_curve_001=[0.0], net_profit_001=0.0, valley_dd_001=0.0, point_dd_001=0.0,
        profit_factor=1.0, return_dd_ratio=0.0, trades=0,
    )


def strategy(set_id: str, symbol: str, daily: dict[str, float]) -> RobustStrategySet:
    """Estrategia mínima con la curva con fechas, que es lo que el lab usa."""
    points: list[tuple[datetime, float]] = []
    accumulated = 0.0
    for day in sorted(daily):
        accumulated += daily[day]
        points.append((datetime.fromisoformat(f"{day}T12:00:00"), accumulated))
    return RobustStrategySet(
        set_id=set_id, candidate_id=set_id, symbol=symbol, timeframe="H1",
        strategy_family="test", robustness_status="accepted", already_used=False,
        report_2020_2024=period(symbol), report_2025_2026=period(symbol),
        curve_2020_2026_001=[value for _time, value in points],
        net_profit_2020_2026_001=accumulated, valley_dd_2020_2026_001=0.0,
        point_dd_2020_2026_001=0.0, profit_factor_2020_2026=1.5,
        return_dd_2020_2026=1.0, trades_2020_2026=len(points),
        set_path=f"C:/sets/{set_id}.set", curve_points_2020_2026_001=points,
    )


def daily_range(start: str, days: int, value: float, *, step: int = 1) -> dict[str, float]:
    first = date.fromisoformat(start)
    return {(first + timedelta(days=index * step)).isoformat(): value for index in range(days)}


def daily_wave(
    start: str, days: int, up: float, down: float, *, period: int = 4, step: int = 1,
) -> dict[str, float]:
    """Serie con altibajos: hace falta para medir drawdown y correlación.

    Una serie constante no tiene varianza, y Pearson sobre varianza cero
    devuelve 0 por definición: probar el filtro de correlación con curvas
    planas no probaría nada.
    """
    first = date.fromisoformat(start)
    return {
        (first + timedelta(days=index * step)).isoformat(): (up if index % period else down)
        for index in range(days)
    }


def margin_model(**symbol_margin: float) -> MarginModel:
    return MarginModel(
        profile="ictrading",
        symbol_margin={key: value for key, value in symbol_margin.items()},
        symbol_min_lot={key: 0.01 for key in symbol_margin},
    )


def axis_for(daily: dict[str, float], months: int = 12) -> lab.LabAxis:
    return lab.build_axis({"only": daily}, window_months=months)


def config(**overrides) -> lab.LabConfig:
    settings = {**DEFAULT_SETTINGS, "target_node": "node", "source_nodes": ["node"]}
    settings.update({key: value for key, value in overrides.items() if key in settings})
    base = lab_config(normalize_settings(settings))
    unknown = {key: value for key, value in overrides.items() if key not in settings}
    return replace(base, **unknown) if unknown else base


class WindowTests(unittest.TestCase):
    def test_the_window_ends_on_the_last_day_with_trades_not_today(self):
        # Dos años de historia que termina en 2026-06-30. Si la ventana se
        # midiera contra la fecha de hoy, los doce meses caerían sobre meses
        # vacíos y el retorno anual saldría diluido sin avisar.
        daily = {
            **daily_range("2024-01-01", 24, 10.0, step=30),
            **daily_range("2025-07-15", 12, 10.0, step=30),
            "2026-06-30": 10.0,
        }
        axis = lab.build_axis({"a": daily}, window_months=12)
        self.assertEqual(axis.days[-1], "2026-06-30")
        self.assertGreater(axis.days[0], "2025-06-30")
        self.assertLessEqual(axis.months, 12)

    def test_a_shorter_window_keeps_fewer_months(self):
        daily = daily_range("2025-01-01", 400, 5.0)
        year = lab.build_axis({"a": daily}, window_months=12)
        quarter = lab.build_axis({"a": daily}, window_months=3)
        self.assertGreater(year.size, quarter.size)
        self.assertEqual(year.days[-1], quarter.days[-1])
        self.assertLessEqual(quarter.months, 4)


class PoolTests(unittest.TestCase):
    def test_the_pool_aligns_three_brokers_on_one_calendar(self):
        shared_day = "2026-03-02"
        pool, axis, warnings = lab.build_lab_strategies(
            [
                (strategy("a", "EURUSD", {shared_day: 30.0, "2026-03-03": 10.0}), "ICTRADING", "ic"),
                (strategy("b", "EURUSD", {shared_day: 20.0}), "AXI", "axi"),
                (strategy("c", "XAUUSD", {"2026-03-04": -5.0}), "ROBOFOREX", "rf"),
            ],
            margin_model=margin_model(EURUSD=40.0, XAUUSD=300.0),
            portable_symbols=frozenset({"EURUSD", "XAUUSD"}),
        )
        self.assertEqual(warnings, [])
        self.assertEqual({item.origin for item in pool}, {"ICTRADING", "AXI", "ROBOFOREX"})
        self.assertEqual(len(axis.days), 3)
        position = axis.days.index(shared_day)
        ic = next(item for item in pool if item.origin == "ICTRADING")
        axi = next(item for item in pool if item.origin == "AXI")
        # El mismo día ocupa la misma posición en las dos series: es lo que
        # permite sumar carteras de brokers distintos sin desalinear fechas.
        self.assertEqual(ic.series[position], 30.0)
        self.assertEqual(axi.series[position], 20.0)
        self.assertEqual(ic.margin_per_unit, 40.0)

    def test_a_symbol_the_target_broker_does_not_have_is_marked_not_portable(self):
        pool, _axis, _warnings = lab.build_lab_strategies(
            [
                (strategy("a", "EURUSD", {"2026-03-02": 10.0}), "ICTRADING", "ic"),
                (strategy("b", "1000xPEPE", {"2026-03-02": 10.0}), "ROBOFOREX", "rf"),
            ],
            margin_model=margin_model(EURUSD=40.0),
            portable_symbols=frozenset({"EURUSD"}),
        )
        portability = {item.symbol: item.portable for item in pool}
        self.assertTrue(portability["EURUSD"])
        self.assertFalse(portability["1000xPEPE"])

    def test_a_curve_without_dates_stays_out_and_says_so(self):
        naked = strategy("a", "EURUSD", {"2026-03-02": 10.0})
        naked.curve_points_2020_2026_001 = []
        naked.curve_2020_2026_001 = [0.0, 10.0, 25.0]
        pool, _axis, warnings = lab.build_lab_strategies(
            [(naked, "ICTRADING", "ic")],
            margin_model=margin_model(EURUSD=40.0),
            portable_symbols=frozenset({"EURUSD"}),
        )
        self.assertEqual(pool, [])
        self.assertTrue(any("sin fechas" in warning for warning in warnings))


class SimulationTests(unittest.TestCase):
    def test_rebalancing_lots_compounds_and_fixed_lots_does_not(self):
        daily = daily_range("2025-07-01", 360, 50.0)
        axis = axis_for(daily)
        series = [50.0] * axis.size
        fixed = lab.simulate(
            series, axis, margin_required=0.0, config=config(rebalance_months=0),
        )
        compounded = lab.simulate(
            series, axis, margin_required=0.0, config=config(rebalance_months=1),
        )
        self.assertAlmostEqual(fixed.final_equity, 10000.0 + 50.0 * axis.size, places=6)
        self.assertGreater(compounded.final_equity, fixed.final_equity)
        self.assertGreater(compounded.final_scale, 1.0)

    def test_the_margin_limit_cuts_the_lot_multiplier(self):
        daily = daily_range("2025-07-01", 200, 10.0)
        axis = axis_for(daily)
        series = [10.0] * axis.size
        # 5% de 10.000 son 500 de margen disponible para 1.000 requeridos:
        # solo se puede operar media cartera.
        limited = lab.simulate(
            series, axis, margin_required=1000.0,
            config=config(rebalance_months=0, max_margin_pct=5.0),
        )
        self.assertAlmostEqual(limited.final_equity, 10000.0 + 0.5 * 10.0 * axis.size, places=6)

    def test_drawdown_is_relative_to_the_equity_peak(self):
        axis = axis_for(daily_range("2026-01-01", 2, 1.0))
        simulation = lab.simulate(
            [10000.0, -10000.0], axis, margin_required=0.0,
            config=config(rebalance_months=0),
        )
        self.assertAlmostEqual(simulation.max_dd_pct, 50.0, places=6)
        self.assertAlmostEqual(simulation.max_dd_amount, 10000.0, places=6)
        self.assertFalse(simulation.ruined)

    def test_an_account_that_hits_zero_is_reported_as_ruined(self):
        axis = axis_for(daily_range("2026-01-01", 3, -1.0))
        simulation = lab.simulate(
            [-6000.0, -6000.0, 100000.0], axis, margin_required=0.0,
            config=config(rebalance_months=0),
        )
        self.assertTrue(simulation.ruined)
        self.assertEqual(simulation.final_equity, 0.0)
        self.assertEqual(simulation.max_dd_pct, 100.0)
        # La recuperación posterior no cuenta: la cuenta ya estaba cerrada.
        self.assertLess(simulation.final_equity, 10000.0)


class SelectionTests(unittest.TestCase):
    def _pool(self, portable_symbols=frozenset({"EURUSD", "XAUUSD"})):
        rising = daily_range("2025-07-01", 120, 20.0, step=3)
        falling = {day: -15.0 for day in rising}
        return lab.build_lab_strategies(
            [
                (strategy("win", "EURUSD", rising), "ICTRADING", "ic"),
                (strategy("lose", "XAUUSD", falling), "AXI", "axi"),
                (strategy("exotic", "1000xPEPE", rising), "ROBOFOREX", "rf"),
            ],
            margin_model=margin_model(EURUSD=40.0, XAUUSD=300.0),
            portable_symbols=portable_symbols,
        )[0]

    def test_negative_strategies_and_unportable_symbols_are_dropped(self):
        selected, notes = lab.select_candidates(
            self._pool(), config(), require_portable=True,
        )
        self.assertEqual([item.set_id for item in selected], ["win"])
        self.assertTrue(any("neto negativo" in note for note in notes))
        self.assertTrue(any("símbolo medido" in note for note in notes))

    def test_without_the_portability_gate_the_exotic_symbol_survives(self):
        selected, _notes = lab.select_candidates(
            self._pool(), config(max_pair_corr=1.0), require_portable=False,
        )
        self.assertEqual({item.set_id for item in selected}, {"win", "exotic"})

    def test_a_perfect_copy_of_a_strategy_is_rejected_by_correlation(self):
        # La misma estrategia validada en dos brokers: dos filas distintas en
        # el pool, un solo riesgo en la cuenta. Es el caso que la idea de
        # «tres demos, una cuenta» crea de serie.
        rising = daily_wave("2025-07-01", 90, 60.0, -30.0, step=3)
        pool = lab.build_lab_strategies(
            [
                (strategy("original", "EURUSD", rising), "ICTRADING", "ic"),
                (strategy("copy", "EURUSD", rising), "AXI", "axi"),
            ],
            margin_model=margin_model(EURUSD=40.0),
            portable_symbols=frozenset({"EURUSD"}),
        )[0]
        selected, notes = lab.select_candidates(
            pool, config(max_pair_corr=0.7, max_units_per_symbol=8, max_units_per_strategy=1),
            require_portable=True,
        )
        self.assertEqual(len(selected), 1)
        self.assertTrue(any("correlación" in note for note in notes))


class SearchTests(unittest.TestCase):
    def _candidates(self):
        rising = daily_range("2025-07-01", 150, 25.0, step=2)
        choppy = {
            day: (60.0 if index % 4 else -55.0)
            for index, day in enumerate(sorted(rising))
        }
        pool, axis, _warnings = lab.build_lab_strategies(
            [
                (strategy("steady", "EURUSD", rising), "ICTRADING", "ic"),
                (strategy("choppy", "XAUUSD", choppy), "AXI", "axi"),
            ],
            margin_model=margin_model(EURUSD=40.0, XAUUSD=120.0),
            portable_symbols=frozenset({"EURUSD", "XAUUSD"}),
        )
        return pool, axis

    def test_the_search_never_breaks_the_drawdown_limit(self):
        pool, axis = self._candidates()
        settings = config(
            capital=10000.0, target_equity=1000000.0, max_dd_pct=10.0,
            max_margin_pct=100.0, rebalance_months=1, max_units_total=50,
            max_units_per_strategy=25, max_units_per_symbol=25, greedy_steps=40,
        )
        candidates, _notes = lab.select_candidates(pool, settings, require_portable=True)
        allocation = lab.search_allocation(candidates, axis, settings)
        self.assertLessEqual(allocation.simulation.max_dd_pct, settings.max_dd_pct + 1e-9)
        self.assertLessEqual(allocation.total_units, settings.max_units_total)
        self.assertFalse(allocation.simulation.ruined)

    def test_the_unit_caps_are_respected(self):
        pool, axis = self._candidates()
        settings = config(
            capital=100000.0, target_equity=1000000.0, max_dd_pct=90.0,
            max_margin_pct=400.0, rebalance_months=1, max_units_total=7,
            max_units_per_strategy=3, max_units_per_symbol=3, greedy_steps=60,
        )
        candidates, _notes = lab.select_candidates(pool, settings, require_portable=True)
        allocation = lab.search_allocation(candidates, axis, settings)
        self.assertLessEqual(allocation.total_units, 7)
        for units in allocation.units.values():
            self.assertLessEqual(units, 3)

    def test_an_empty_pool_returns_an_empty_allocation_instead_of_failing(self):
        _pool, axis = self._candidates()
        allocation = lab.search_allocation([], axis, config())
        self.assertEqual(allocation.units, {})
        self.assertEqual(allocation.total_units, 0)
        self.assertIn("vacío", allocation.stop_reason)


class VerdictTests(unittest.TestCase):
    def test_when_the_target_is_missed_the_capital_that_reaches_it_is_exact(self):
        daily = daily_wave("2025-07-01", 250, 20.0, -12.0)
        pool, axis, _warnings = lab.build_lab_strategies(
            [(strategy("steady", "EURUSD", daily), "ICTRADING", "ic")],
            margin_model=margin_model(EURUSD=40.0),
            portable_symbols=frozenset({"EURUSD"}),
        )
        settings = config(
            capital=10000.0, target_equity=1000000.0, max_dd_pct=50.0,
            max_units_total=1, max_units_per_strategy=1, max_units_per_symbol=1,
            greedy_steps=10,
        )
        allocation = lab.search_allocation(pool, axis, settings)
        verdict = lab.build_verdict(allocation, pool, axis, settings)
        self.assertFalse(verdict.reached)
        growth = allocation.simulation.final_equity / settings.capital
        # El retorno es la invariante: el capital que llega al objetivo es el
        # objetivo dividido por ese crecimiento, y esa es la única respuesta
        # útil cuando la pantalla dice «no llega».
        self.assertAlmostEqual(verdict.capital_for_target * growth, settings.target_equity, places=2)
        self.assertAlmostEqual(
            verdict.scale_for_target,
            (settings.target_equity - settings.capital) / allocation.simulation.profit,
            places=6,
        )
        # El multiplicador que haría falta no es gratis: el drawdown que
        # costaría se simula de verdad y sale muy por encima del de la
        # composición operable, con el margen que exigiría al lado.
        self.assertGreater(verdict.dd_at_target_pct, allocation.simulation.max_dd_pct)
        self.assertGreater(verdict.margin_at_target_pct, allocation.simulation.max_margin_pct)
        self.assertIn("multiplicar los lotes", verdict.note)

    def test_an_allocation_blocked_by_the_limits_says_that_and_not_something_else(self):
        # Pool rentable, límites imposibles: el veredicto tiene que apuntar a
        # los límites. Decir «el pool no gana dinero» mandaría a buscar mejores
        # estrategias cuando lo que sobra es restricción.
        daily = daily_wave("2025-07-01", 120, 500.0, -4000.0, period=6)
        pool, axis, _warnings = lab.build_lab_strategies(
            [(strategy("spiky", "EURUSD", daily), "ICTRADING", "ic")],
            margin_model=margin_model(EURUSD=40.0),
            portable_symbols=frozenset({"EURUSD"}),
        )
        settings = config(
            capital=10000.0, target_equity=1000000.0, max_dd_pct=1.0,
            max_units_total=5, max_units_per_strategy=5, max_units_per_symbol=5,
            greedy_steps=10,
        )
        allocation = lab.search_allocation(pool, axis, settings)
        verdict = lab.build_verdict(allocation, pool, axis, settings)
        self.assertEqual(allocation.units, {})
        self.assertFalse(verdict.reached)
        self.assertIn("no pudo asignar ni una unidad", verdict.note)

    def test_a_pool_that_loses_money_does_not_pretend_capital_is_the_problem(self):
        daily = daily_range("2025-07-01", 100, -10.0)
        axis = axis_for(daily)
        allocation = lab.Allocation(
            # Con unidades asignadas: lo que falla es el pool, no los límites.
            units={"ICTRADING:losing": 1},
            simulation=lab.simulate(
                [-10.0] * axis.size, axis, margin_required=0.0,
                config=config(rebalance_months=0),
            ),
            margin_required=0.0, steps=0, stop_reason="prueba",
        )
        verdict = lab.build_verdict(allocation, [], axis, config(rebalance_months=0))
        self.assertFalse(verdict.reached)
        self.assertEqual(verdict.scale_for_target, 0.0)
        self.assertIn("no gana dinero", verdict.note)


class CandidateTrimTests(unittest.TestCase):
    """El tope por broker existe por el reloj, y nunca es silencioso."""

    ROWS = [
        {"source_candidate_id": index, "symbol": "EURUSD", "set_path": f"s{index}.set"}
        for index in range(1, 11)
    ]

    def test_the_newest_of_each_symbol_wins_and_the_cut_is_reported(self):
        warnings: list[str] = []
        trimmed = ExperimentCoordinator._trim_candidates(
            list(self.ROWS), {"max_candidates_per_node": 3}, "AXI", warnings,
        )
        self.assertEqual([row["source_candidate_id"] for row in trimmed], [10, 9, 8])
        self.assertTrue(any("3 candidatas de 10" in item for item in warnings))

    def test_the_cut_spreads_across_symbols_instead_of_taking_one_run(self):
        # Caso real del 2026-09-10: las 60 últimas candidatas de AXI eran todas
        # del mismo símbolo, que el destino no tenía medido, así que AXI
        # aportaba cero. El reparto por símbolo es lo que lo evita.
        rows = [
            {"source_candidate_id": 100 + index, "symbol": "COCOA.FS", "set_path": f"c{index}.set"}
            for index in range(20)
        ] + [
            {"source_candidate_id": index, "symbol": "EURUSD", "set_path": f"e{index}.set"}
            for index in range(5)
        ] + [
            {"source_candidate_id": index, "symbol": "XAUUSD", "set_path": f"x{index}.set"}
            for index in range(5)
        ]
        trimmed = ExperimentCoordinator._trim_candidates(
            rows, {"max_candidates_per_node": 6}, "AXI", [],
        )
        symbols = {row["symbol"] for row in trimmed}
        self.assertEqual(symbols, {"COCOA.FS", "EURUSD", "XAUUSD"})
        self.assertEqual(len(trimmed), 6)

    def test_a_symbol_with_fewer_candidates_does_not_waste_the_budget(self):
        rows = [
            {"source_candidate_id": index, "symbol": "EURUSD", "set_path": f"e{index}.set"}
            for index in range(10)
        ] + [{"source_candidate_id": 99, "symbol": "XAUUSD", "set_path": "x.set"}]
        trimmed = ExperimentCoordinator._trim_candidates(
            rows, {"max_candidates_per_node": 5}, "AXI", [],
        )
        self.assertEqual(len(trimmed), 5)
        self.assertEqual(sum(1 for row in trimmed if row["symbol"] == "XAUUSD"), 1)

    def test_zero_reads_everything_without_an_aviso(self):
        warnings: list[str] = []
        trimmed = ExperimentCoordinator._trim_candidates(
            list(self.ROWS), {"max_candidates_per_node": 0}, "AXI", warnings,
        )
        self.assertEqual(len(trimmed), len(self.ROWS))
        self.assertEqual(warnings, [])

    def test_a_pool_smaller_than_the_cap_is_left_alone(self):
        warnings: list[str] = []
        trimmed = ExperimentCoordinator._trim_candidates(
            list(self.ROWS), {"max_candidates_per_node": 50}, "AXI", warnings,
        )
        self.assertEqual(trimmed, self.ROWS)
        self.assertEqual(warnings, [])


class SettingsTests(unittest.TestCase):
    def test_the_target_has_to_be_above_the_capital(self):
        with self.assertRaises(ValueError):
            normalize_settings({**DEFAULT_SETTINGS, "capital": 5000.0, "target_equity": 1000.0})

    def test_unknown_fields_are_rejected_instead_of_ignored(self):
        with self.assertRaises(ValueError):
            normalize_settings({"leverage": 500})

    def test_defaults_encode_the_experiment_as_it_was_asked_for(self):
        settings = normalize_settings({})
        self.assertEqual(settings["target_equity"], 1000000.0)
        self.assertEqual(settings["horizon_months"], 12)
        self.assertEqual(settings["rebalance_months"], 1)


if __name__ == "__main__":
    unittest.main()
