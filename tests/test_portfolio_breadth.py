"""Breadth-first regression tests for the portfolio optimizer."""

import unittest

from portfolio_manager.ubs_portfolio import CandidateFunnel, SearchLimits, SearchPlan


def curve_set(set_id: str, symbol: str, dip: float, net: float):
    from portfolio_manager.ubs_portfolio import PeriodReport, build_robust_strategy_set

    def report(name: str, start: int, end: int) -> PeriodReport:
        return PeriodReport(
            period_name=name, start_year=start, end_year=end, symbol=symbol,
            timeframe="H1", pnl_curve_001=[0.0, -dip, net], net_profit_001=net,
            valley_dd_001=dip, point_dd_001=dip, profit_factor=2.0,
            return_dd_ratio=net / max(dip, 1.0), trades=100,
            balance_dd_metric_001=dip, equity_dd_metric_001=dip,
        )

    return build_robust_strategy_set(
        set_id, set_id, symbol, "H1", None, "accepted", False,
        report("2020_2024", 2020, 2024), report("2025_2026", 2025, 2026),
        set_path=set_id, has_final_tick_performance=True,
        final_tick_net_profit_001=net, final_tick_equity_dd_001=dip,
    )


class BreadthBelowMinimumTests(unittest.TestCase):
    """Abrir huecos busca cuántas caben, no cuánto rinde la siguiente."""

    def sets(self):
        # La gorda rinde más por unidad pero se come el presupuesto de DD; las
        # dos flacas caben juntas. Son las candidatas del selector de mejora.
        # Presupuesto de valle 50. La gorda cabe sola (35) y rinde mucho más
        # por punto de DD, pero con ella dentro no cabe ninguna otra: 35+20>50.
        # Las dos flacas juntas suman 40 y sí caben.
        return [
            curve_set("fat.set", "XAUUSD", dip=35.0, net=400.0),
            curve_set("thin-a.set", "EURUSD", dip=20.0, net=90.0),
            curve_set("thin-b.set", "USDJPY", dip=20.0, net=90.0),
        ]

    def optimize(self, *, prefer_breadth: bool):
        from portfolio_manager.ubs_portfolio import PortfolioType, optimize_portfolio

        return optimize_portfolio(
            raw_sets=self.sets(),
            capital=5000.0,
            valley_dd_pct=1.0,
            point_dd_pct=100.0,
            portfolio_type=PortfolioType.BALANCED,
            limits=SearchLimits(enforce_point_dd=False),
            funnel=CandidateFunnel(
                min_trades_2020_2026=1, top_k_per_symbol=5, max_total_candidates=10,
            ),
            search=SearchPlan(
                minimum_active_strategies=2,
                maximum_active_strategies=2,
                prefer_breadth_below_minimum=prefer_breadth,
                run_local_search=False,
            ),
        )

    def test_the_cheapest_increment_opens_the_slots_the_richest_one_blocks(self) -> None:
        greedy = self.optimize(prefer_breadth=False)
        breadth = self.optimize(prefer_breadth=True)

        # Por rentabilidad entra la gorda y ya no cabe una segunda.
        self.assertEqual(
            [item.set_id for item in greedy.allocations if item.units > 0], ["fat.set"]
        )
        self.assertEqual(greedy.active_strategies, 1)
        # Por coste de riesgo entran las dos flacas y se alcanza el mínimo.
        self.assertEqual(breadth.active_strategies, 2)
        self.assertEqual(
            sorted(item.set_id for item in breadth.allocations if item.units > 0),
            ["thin-a.set", "thin-b.set"],
        )
        self.assertIn(
            "Cheapest valid +0.01 increment while opening required slots",
            [decision.reason for decision in breadth.decision_log],
        )
