"""Reparto de los cierres reales entre las operaciones del tester.

Los casos vienen del auditor de ICTrading del 2026-10-04 (`audit-53`), donde
elegir por operación y sólo por la apertura emparejó mal dos XAUUSD abiertos en
el mismo segundo: ninguna de las dos parejas quedaba dentro de tolerancia y la
que sí encajaba por el cierre estaba en la otra fila.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mt5_manager.live_audit_engine import LiveAuditController
from tests.live_audit_engine_base import request

START = datetime(2026, 10, 2, 4, 0, 0, tzinfo=timezone.utc)


def operation(
    strategy: str, opened: int, closed: int, *, volume: float = .01,
    symbol: str = "XAUUSD", price: float = 4139.8, profit: float = 1.0,
    sl: float = 0.0, tp: float = 0.0,
) -> dict:
    """Operación con apertura y cierre en segundos desde `START`."""
    return {
        "strategy": strategy, "symbol": symbol, "side": "sell",
        "open_time": START + timedelta(seconds=opened),
        "close_time": START + timedelta(seconds=closed),
        "open_price": price, "close_price": price, "volume": volume, "profit": profit,
        "sl": sl, "tp": tp,
    }


def compare(real: list[dict], tester: list[dict], **overrides) -> list[dict]:
    order = {**request(), **overrides}
    strategies = {str(row["strategy"]): 1 for row in tester}
    result = LiveAuditController._compare(
        real, tester, {"XAUUSD": .01, "EURUSD": .00001}, order, strategies,
    )
    return result["comparison_detail"]["operation_comparisons"]


class LiveAuditAssignmentTests(unittest.TestCase):
    def test_the_close_decides_between_two_openings_in_the_same_second(self) -> None:
        # 1295 abre un segundo después que 1467 y antes se quedaba con el cierre
        # de 1467: las dos filas salían fuera de tolerancia y la buena no existía.
        tester = [operation("1295", 569, 663, volume=.02), operation("1467", 568, 914)]
        real = [operation("real-a", 569, 846), operation("real-b", 570, 4347)]

        rows = compare(real, tester, real_strategy_lots={"1295": .01, "1467": .01})

        self.assertEqual(rows[1]["status"], "matched")
        self.assertEqual(rows[1]["real"]["strategy"], "real-a")
        self.assertEqual(rows[0]["real"]["strategy"], "real-b")
        self.assertIn("close_time", rows[0]["reasons"])

    def test_the_lot_identifies_the_strategy_when_several_open_at_once(self) -> None:
        # Tres EURUSD del portafolio abren en el mismo segundo. Lo único que
        # distingue a cada estrategia es el lote con el que opera en real.
        tester = [
            operation(strategy, 0, 100 * index, volume=.03, symbol="EURUSD", price=1.13586)
            for index, strategy in enumerate(("a", "b", "c"), 1)
        ]
        real = [
            operation(f"real-{volume}", 0, 300, volume=volume, symbol="EURUSD", price=1.13586)
            for volume in (.02, .03, .04)
        ]

        rows = compare(real, tester, real_strategy_lots={"a": .04, "b": .03, "c": .02})

        self.assertEqual(
            [row["real"]["volume"] for row in rows], [.04, .03, .02],
        )

    def test_a_real_that_only_fits_by_the_close_is_accepted_and_marked(self) -> None:
        # El usuario lo pidió así: si no hay correspondencia por la entrada, se
        # busca por la salida, pero la apertura queda señalada como desviación.
        rows = compare([operation("real", 3600, 7230)], [operation("a", 0, 7200)])

        self.assertEqual(rows[0]["status"], "deviation")
        self.assertEqual(rows[0]["reasons"], ["open_time"])
        self.assertEqual(rows[0]["measurements"]["open_time_delta_seconds"], 3600)

    def test_a_real_that_fits_by_neither_end_never_gets_used(self) -> None:
        rows = compare([operation("real", 3600, 5400)], [operation("a", 0, 7200)])

        self.assertEqual(rows[0]["status"], "missing")
        self.assertEqual(rows[0]["reasons"], ["open_time_outside_tolerance"])
        self.assertEqual(rows[0]["nearest_unused_real"]["strategy"], "real")

    def test_an_opening_within_tolerance_beats_one_that_only_closes_on_time(self) -> None:
        # La apertura manda: una real que abre a tiempo no puede perder contra
        # otra que sólo encaja por el cierre, aunque ésta cierre más cerca.
        real = [operation("por-cierre", 3600, 7200), operation("por-apertura", 1, 7500)]

        rows = compare(real, [operation("a", 0, 7200)])

        self.assertEqual(rows[0]["real"]["strategy"], "por-apertura")


    def test_the_stops_of_the_entry_order_decide_before_anything_else(self) -> None:
        # Los niveles son los reales del 2026-10-04: dos US30 del portafolio
        # abiertos en el mismo segundo y con el mismo lote, cada uno con el
        # SL/TP de su estrategia. Los cierres estan puestos para que el tiempo
        # prefiera el reparto contrario y solo los stops puedan deshacerlo: sin
        # ellos las dos parejas equivocadas cierran a un segundo.
        tester = [
            operation("largo", 0, 100, sl=51_077.14, tp=51_966.56, symbol="US30",
                      price=51_709.7, volume=.1),
            operation("corto", 0, 120, sl=49_807.18, tp=51_894.22, symbol="US30",
                      price=51_709.7, volume=.1),
        ]
        real = [
            operation("1970212220", 1, 101, sl=49_799.73, tp=51_894.94, symbol="US30",
                      price=51_708.2, volume=.1),
            operation("1970212277", 1, 119, sl=51_074.67, tp=51_967.57, symbol="US30",
                      price=51_702.7, volume=.1),
        ]

        rows = compare(real, tester)

        self.assertEqual(rows[0]["real"]["strategy"], "1970212277")
        self.assertEqual(rows[1]["real"]["strategy"], "1970212220")

        blind = compare(
            [{**row, "sl": 0.0, "tp": 0.0} for row in real],
            [{**row, "sl": 0.0, "tp": 0.0} for row in tester],
        )
        self.assertEqual(
            [row["real"]["strategy"] for row in blind], ["1970212220", "1970212277"],
        )

    def test_stops_that_nobody_declares_do_not_decide_anything(self) -> None:
        # Una orden a mercado no lleva niveles: sin evidencia manda el tiempo.
        tester = [operation("a", 0, 100), operation("b", 0, 900)]
        real = [operation("r1", 1, 905), operation("r2", 1, 101)]

        rows = compare(real, tester)

        self.assertEqual([row["real"]["strategy"] for row in rows], ["r2", "r1"])


if __name__ == "__main__":
    unittest.main()
