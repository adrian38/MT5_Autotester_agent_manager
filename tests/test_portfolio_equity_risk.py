import unittest
from dataclasses import replace
from datetime import datetime

from portfolio_manager.ubs_portfolio import (
    PeriodReport,
    ClosedTrade,
    build_robust_strategy_set,
    evaluate_portfolio,
    filter_eligible_sets,
    _evaluation_violates_dd_limits,
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
    def test_final_tick_extends_closed_curve_only_after_oos_cutoff(self) -> None:
        base_trade = ClosedTrade(
            datetime(2024, 12, 1), datetime(2024, 12, 2), "XAGUSD", 0.01, 10.0
        )
        oos_trade = ClosedTrade(
            datetime(2026, 5, 28), datetime(2026, 5, 29), "XAGUSD", 0.01, 100.0
        )
        duplicate_recent = ClosedTrade(
            datetime(2026, 1, 1), datetime(2026, 2, 1), "XAGUSD", 0.01, 999.0
        )
        tail_trade = ClosedTrade(
            datetime(2026, 6, 16), datetime(2026, 6, 17), "XAGUSD", 0.01, -50.0
        )
        base = replace(
            period("2020_2024", 2020, 2024, balance_dd=10.0, equity_dd=12.0),
            closed_trades=[base_trade],
        )
        oos = replace(
            period("2025_2026", 2025, 2026, balance_dd=20.0, equity_dd=25.0),
            closed_trades=[oos_trade],
        )
        recent = replace(
            period("final_tick_6m", 2026, 2026, balance_dd=30.0, equity_dd=40.0),
            closed_trades=[duplicate_recent, tail_trade],
        )

        strategy = build_robust_strategy_set(
            set_id="tail.set",
            candidate_id="tail",
            symbol="XAGUSD",
            timeframe="H4",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=base,
            report_2025_2026=oos,
            final_tick_report=recent,
            final_tick_report_path="recent.htm",
        )

        self.assertEqual(strategy.final_tick_tail_trades, 1)
        self.assertEqual(strategy.trades_2020_2026, 3)
        self.assertAlmostEqual(strategy.net_profit_2020_2026_001, 60.0)
        self.assertAlmostEqual(strategy.valley_dd_2020_2026_001, 50.0)
        self.assertEqual(strategy.curve_points_2020_2026_001[-1][0], datetime(2026, 6, 17))

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
        self.assertAlmostEqual(strategy.max_floating_dd_001, 986.53)
        evaluation = evaluate_portfolio(
            [strategy], {strategy.set_id: 1}, 225.0, 225.0, enforce_point_dd=False
        )
        self.assertAlmostEqual(evaluation.closed_valley_dd, 0.0)
        self.assertAlmostEqual(evaluation.floating_dd_buffer, 986.53)
        self.assertGreater(evaluation.valley_dd, evaluation.target_valley_dd)

    def test_continuous_report_replaces_segmented_curve_and_is_authoritative_for_equity_dd(self) -> None:
        continuous_trades = [
            ClosedTrade(datetime(2020, 1, 2), datetime(2020, 1, 3), "XAGUSD", 0.01, 200.0),
            ClosedTrade(datetime(2026, 6, 1), datetime(2026, 6, 2), "XAGUSD", 0.01, -150.0),
        ]
        continuous = replace(
            period("continuous", 2020, 2026, balance_dd=0.06, equity_dd=986.53),
            closed_trades=continuous_trades,
        )
        strategy = build_robust_strategy_set(
            set_id="xag-continuous.set",
            candidate_id="37",
            symbol="XAGUSD",
            timeframe="H4",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=period("2020_2024", 2020, 2024, balance_dd=1.0, equity_dd=2.0),
            report_2025_2026=period("2025_2026", 2025, 2026, balance_dd=2.0, equity_dd=3.0),
            full_history_report=continuous,
            full_history_report_path="continuous.htm",
        )

        self.assertAlmostEqual(strategy.net_profit_2020_2026_001, 50.0)
        self.assertAlmostEqual(strategy.valley_dd_2020_2026_001, 150.0)
        self.assertAlmostEqual(strategy.max_floating_dd_001, 986.53)
        self.assertEqual(strategy.floating_dd_source, "Final Tick continuo 2020-hoy")
        self.assertEqual(strategy.full_history_report_path, "continuous.htm")

    def test_separate_floating_episodes_use_the_worst_one_instead_of_the_sum(self) -> None:
        strategy = build_robust_strategy_set(
            set_id="first.set",
            candidate_id="1",
            symbol="XAGUSD",
            timeframe="H4",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=period(
                "2020_2024", 2020, 2024, balance_dd=0.0, equity_dd=100.0
            ),
            report_2025_2026=period(
                "2025_2026", 2025, 2026, balance_dd=0.0, equity_dd=20.0
            ),
        )
        evaluation = evaluate_portfolio(
            [strategy],
            {strategy.set_id: 2},
            500.0,
            500.0,
            enforce_point_dd=False,
        )

        self.assertAlmostEqual(evaluation.floating_dd_buffer, 200.0)
        self.assertAlmostEqual(evaluation.valley_dd, 200.0)

    def test_closed_valley_wins_when_it_is_larger_than_floating_risk(self) -> None:
        strategy = build_robust_strategy_set(
            set_id="closed.set",
            candidate_id="3",
            symbol="XAGUSD",
            timeframe="H4",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=period(
                "2020_2024", 2020, 2024, balance_dd=0.0, equity_dd=100.0
            ),
            report_2025_2026=period(
                "2025_2026", 2025, 2026, balance_dd=0.0, equity_dd=20.0
            ),
        )
        strategy = replace(
            strategy,
            curve_2020_2026_001=[0.0, 300.0, 120.0],
            curve_points_2020_2026_001=[],
        )

        evaluation = evaluate_portfolio(
            [strategy],
            {strategy.set_id: 1},
            500.0,
            500.0,
            enforce_point_dd=False,
        )

        self.assertAlmostEqual(evaluation.closed_valley_dd, 180.0)
        self.assertAlmostEqual(evaluation.floating_dd_buffer, 100.0)
        self.assertAlmostEqual(evaluation.valley_dd, 180.0)

    def test_daily_threshold_is_visual_and_does_not_reject_the_portfolio(self) -> None:
        strategy = build_robust_strategy_set(
            set_id="visual-daily.set",
            candidate_id="4",
            symbol="XAGUSD",
            timeframe="H4",
            strategy_family="test",
            robustness_status="accepted",
            already_used=False,
            report_2020_2024=period("2020_2024", 2020, 2024, balance_dd=0.0, equity_dd=10.0),
            report_2025_2026=period("2025_2026", 2025, 2026, balance_dd=0.0, equity_dd=10.0),
        )
        evaluation = evaluate_portfolio(
            [strategy], {strategy.set_id: 1}, 500.0, 500.0, target_daily_dd=50.0,
            enforce_point_dd=False,
        )
        evaluation = replace(evaluation, daily_dd=100.0)

        self.assertFalse(_evaluation_violates_dd_limits(evaluation))

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
