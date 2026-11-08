"""El tope de estrategias activas y hasta dónde llega: motor v3.

El greedy respeta `maximum_active_strategies` desde siempre. La búsqueda local
no lo veía: un intercambio que estrena estrategia sube el recuento de activas,
y nadie lo comparaba con el tope. Quien pide una composición de tamaño exacto
-la mejora, y las variantes A/M/C con composición bloqueada- recibía una
cartera más grande de la pedida y la descartaba entera, culpando al selector de
lo contrario de lo que había hecho.

Medido el 2026-11-08 sobre el portafolio #113 de AXI: el greedy devolvía las 10
activas pedidas y la búsqueda local las dejaba en 12, así que el intento de dos
incorporaciones moría con «El selector añadió 4 estrategias; este intento
requiere 2».
"""

import unittest

from portfolio_manager.ubs_portfolio import CandidateFunnel, SearchLimits, SearchPlan

from .test_portfolio_breadth import curve_set


class ActiveCapInLocalSearchTests(unittest.TestCase):
    """Un intercambio no puede estrenar estrategia por encima del tope."""

    def sets(self):
        # `a.set` rinde más por punto de DD, así que el greedy la elige y la
        # llena hasta su tope de dos unidades. `b.set` gana más en absoluto:
        # mover una unidad de la primera a la segunda sube el beneficio, y ese
        # intercambio es el que estrena una segunda estrategia activa.
        return [
            curve_set("a.set", "EURUSD", dip=10.0, net=100.0),
            curve_set("b.set", "USDJPY", dip=30.0, net=150.0),
        ]

    def optimize(self, *, engine_v3: bool, restarts: int = 0):
        from portfolio_manager.ubs_portfolio import PortfolioType, optimize_portfolio

        return optimize_portfolio(
            raw_sets=self.sets(),
            capital=5000.0,
            valley_dd_pct=2.0,
            point_dd_pct=100.0,
            portfolio_type=PortfolioType.BALANCED,
            limits=SearchLimits(enforce_point_dd=False, max_units_per_set=2),
            funnel=CandidateFunnel(
                min_trades_2020_2026=1, top_k_per_symbol=5, max_total_candidates=10,
                # La original bloqueada es lo que impide que el intercambio
                # siguiente vacie `a.set` y devuelva el recuento a una activa.
                required_set_ids=["a.set"],
            ),
            search=SearchPlan(
                minimum_active_strategies=1,
                maximum_active_strategies=1,
                run_local_search=True,
                search_restarts=restarts,
                enforce_maximum_in_local_search=engine_v3,
            ),
        )

    def active_ids(self, result) -> list[str]:
        return sorted(item.set_id for item in result.allocations if item.units > 0)

    def test_without_the_v3_engine_the_local_search_passes_the_cap(self) -> None:
        result = self.optimize(engine_v3=False)

        self.assertEqual(self.active_ids(result), ["a.set", "b.set"])
        self.assertEqual(result.active_strategies, 2)

    def test_the_v3_engine_keeps_the_cap_in_the_local_search(self) -> None:
        result = self.optimize(engine_v3=True)

        self.assertEqual(self.active_ids(result), ["a.set"])
        self.assertEqual(result.active_strategies, 1)

    def test_the_v3_engine_also_keeps_it_in_the_multi_start(self) -> None:
        self.assertEqual(self.optimize(engine_v3=True, restarts=3).active_strategies, 1)


if __name__ == "__main__":
    unittest.main()
