import unittest

from portfolio_manager.ubs_portfolio import (
    PeriodReport,
    build_robust_strategy_set,
    evaluate_portfolio,
    filter_eligible_sets,
)


def period(
    name: str,
    start_year: int,
    end_year: int,
    *,
    balance_dd: float,
    equity_dd: float,
    net_profit: float = 100.0,
) -> PeriodReport:
    return PeriodReport(
        period_name=name,
        start_year=start_year,
        end_year=end_year,
        symbol="XAGUSD",
        timeframe="H4",
        pnl_curve_001=[0.0, net_profit],
        net_profit_001=net_profit,
        valley_dd_001=0.0,
        point_dd_001=0.0,
        profit_factor=2.0,
        return_dd_ratio=net_profit,
        trades=100,
        balance_dd_metric_001=balance_dd,
        equity_dd_metric_001=equity_dd,
    )


class PortfolioEquityRiskTests(unittest.TestCase):
    def test_worst_floating_gap_is_searched_across_full_history_and_recent_report(self) -> None:
        strategy = build_robust_strategy_set(
            set_id="xag.set",
            candidate_id="37",
            symbol="XAGUSD",
            timeframe="H4",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=period(
                "2020_2024", 2020, 2024, balance_dd=0.06, equity_dd=986.53
            ),
            report_2025_2026=period(
                "2025_2026", 2025, 2026, balance_dd=5.0, equity_dd=25.0
            ),
            final_tick_balance_dd_001=2.12,
            final_tick_equity_dd_001=100.0,
            final_tick_net_profit_001=150.0,
            has_final_tick_performance=True,
        )

        self.assertEqual(strategy.floating_dd_source, "2020-2024")
        self.assertAlmostEqual(strategy.max_floating_dd_001, 986.47)
        evaluation = evaluate_portfolio(
            [strategy], {strategy.set_id: 1}, 225.0, 225.0, enforce_point_dd=False
        )
        self.assertAlmostEqual(evaluation.closed_valley_dd, 0.0)
        self.assertAlmostEqual(evaluation.floating_dd_buffer, 986.47)
        self.assertGreater(evaluation.valley_dd, evaluation.target_valley_dd)

    def test_recent_profit_must_recover_recent_equity_drawdown(self) -> None:
        strategy = build_robust_strategy_set(
            set_id="usdjpy.set",
            candidate_id="11335",
            symbol="USDJPY",
            timeframe="H1",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=period(
                "2020_2024", 2020, 2024, balance_dd=12.34, equity_dd=15.63
            ),
            report_2025_2026=period(
                "2025_2026", 2025, 2026, balance_dd=4.0, equity_dd=5.0
            ),
            final_tick_balance_dd_001=4.39,
            final_tick_equity_dd_001=5.88,
            final_tick_net_profit_001=1.22,
            has_final_tick_performance=True,
        )

        self.assertLess(strategy.recent_net_profit_001 / strategy.recent_equity_dd_001, 1.0)
        self.assertEqual(filter_eligible_sets([strategy], min_trades_2020_2026=1), [])


if __name__ == "__main__":
    unittest.main()
