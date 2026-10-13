import unittest

from portfolio_manager.ubs_portfolio import (
    PeriodReport,
    SearchLimits,
    _deep_refine_allocations,
    build_robust_strategy_set,
    evaluate_portfolio,
    improve_with_multi_start_search,
)


def _curve_set(set_id: str, symbol: str, net: float):
    def report(name: str, start: int, end: int) -> PeriodReport:
        return PeriodReport(
            period_name=name, start_year=start, end_year=end, symbol=symbol,
            timeframe="H1", pnl_curve_001=[0.0, net], net_profit_001=net,
            valley_dd_001=0.0, point_dd_001=0.0, profit_factor=2.0,
            return_dd_ratio=net, trades=100,
            balance_dd_metric_001=0.0, equity_dd_metric_001=0.0,
        )

    return build_robust_strategy_set(
        set_id, set_id, symbol, "H1", None, "accepted", False,
        report("2020_2024", 2020, 2024), report("2025_2026", 2025, 2026),
        set_path=set_id, has_final_tick_performance=True,
        final_tick_net_profit_001=net, final_tick_equity_dd_001=0.0,
    )


class RequiredSetRefinementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sets = [
            _curve_set("required.set", "EURUSD", 10.0),
            _curve_set("replacement.set", "USDJPY", 100.0),
        ]
        self.allocations = {"required.set": 1, "replacement.set": 0}
        self.current = evaluate_portfolio(self.sets, self.allocations, 1000.0, 1000.0)
        self.limits = SearchLimits(max_total_units=1, enforce_point_dd=False)

    def test_deep_refinement_cannot_swap_the_last_required_unit(self) -> None:
        allocations, _evaluation, log, _attempts = _deep_refine_allocations(
            self.sets, self.allocations, self.current,
            minimum_active_strategies=1,
            protected_set_ids=["required.set"],
            limits=self.limits,
        )

        self.assertEqual(allocations, self.allocations)
        self.assertEqual(log, [])

    def test_multi_start_cannot_perturb_away_the_last_required_unit(self) -> None:
        allocations, _evaluation, log, valid_restarts = improve_with_multi_start_search(
            self.sets, self.allocations, self.current, 1000.0, 1000.0,
            restarts=2,
            perturbations=1,
            protected_set_ids=["required.set"],
            limits=self.limits,
        )

        self.assertEqual(allocations, self.allocations)
        self.assertEqual(log, [])
        self.assertEqual(valid_restarts, 0)


if __name__ == "__main__":
    unittest.main()
