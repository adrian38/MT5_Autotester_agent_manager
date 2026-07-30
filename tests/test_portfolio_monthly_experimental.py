from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_monthly_experimental import (
    build_experimental_candidate_pools,
    optimize_experimental_monthly_portfolio,
)
from mt5_manager.portfolio_monthly_service import _monthly_proposals
from mt5_manager.portfolio_service import normalize_settings


def strategy(index: int) -> SimpleNamespace:
    year_count = 3 + index % 3
    increments = [
        float(20 + (index % 5) * 2 + ((year + index) % 3) * 3)
        for year in range(year_count)
    ]
    total = 0.0
    curve = [0.0]
    points = []
    for offset, increment in enumerate(increments):
        total += increment
        curve.append(total)
        points.append((datetime(2020 + offset, 1, 15), total))
    return SimpleNamespace(
        set_id=f"set-{index}",
        symbol=("EURUSD", "XAUUSD", "US500")[index % 3],
        robustness_status="accepted",
        already_used=False,
        curve_2020_2026_001=curve,
        curve_points_2020_2026_001=points,
        closed_trades_2020_2026=[],
        target_month=1,
        net_profit_2020_2026_001=total,
        return_dd_2020_2026=float(1 + index % 7),
        profit_factor_2020_2026=float(1.1 + (index % 5) / 10),
        valley_dd_2020_2026_001=float(20 + index % 11),
        max_floating_dd_001=float(index % 13),
        trades_2020_2026=30,
        month_years=tuple(range(2020, 2020 + year_count)),
        positive_month_years=tuple(range(2020, 2020 + year_count - index % 2)),
        has_recent_performance=False,
        recent_net_profit_001=0.0,
        recent_equity_dd_001=0.0,
    )


class ExperimentalMonthlySearchTests(unittest.TestCase):
    def test_monthly_orchestrator_uses_experimental_engine_only_when_enabled(self) -> None:
        strategies = [strategy(index) for index in range(3)]
        inputs = normalize_settings(
            "monthly",
            {
                "allowed_asset_groups": ["Forex"],
                "experimental_monthly_search": True,
            },
            "ICTRADING",
        )
        marker = SimpleNamespace()

        def run_optimizer(candidate_sets, _minimum_recent, optimize):
            return optimize(candidate_sets), []

        with patch(
            "mt5_manager.portfolio_monthly_service._optimize_without_recent_fillers",
            side_effect=run_optimizer,
        ), patch(
            "mt5_manager.portfolio_monthly_service.optimize_experimental_monthly_portfolio",
            return_value=marker,
        ) as experimental, patch(
            "mt5_manager.portfolio_monthly_service.optimize_portfolio",
        ) as stable, patch(
            "mt5_manager.portfolio_monthly_service._seasonal_coverage",
        ):
            proposals = _monthly_proposals(strategies, strategies, inputs, [])

        self.assertEqual(len(proposals), 3)
        self.assertEqual(experimental.call_count, 3)
        stable.assert_not_called()

    def test_candidate_pools_cover_every_strategy_once(self) -> None:
        strategies = [strategy(index) for index in range(73)]

        pools = build_experimental_candidate_pools(
            strategies,
            pool_size=10,
            min_trades_2020_2026=15,
        )

        flattened = [item.set_id for pool in pools for item in pool]
        self.assertEqual(len(pools), 8)
        self.assertLessEqual(max(map(len, pools)), 10)
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), {item.set_id for item in strategies})

    def test_rotations_change_candidate_companions(self) -> None:
        strategies = [strategy(index) for index in range(12)]

        signatures = []
        for rotation in range(3):
            pools = build_experimental_candidate_pools(
                strategies,
                pool_size=3,
                min_trades_2020_2026=15,
                rotation=rotation,
            )
            signatures.append(
                {
                    frozenset(item.set_id for item in pool)
                    for pool in pools
                }
            )
            self.assertEqual(
                {item.set_id for pool in pools for item in pool},
                {item.set_id for item in strategies},
            )

        self.assertEqual(len({frozenset(signature) for signature in signatures}), 3)

    def test_highly_correlated_candidates_are_split_when_capacity_allows(self) -> None:
        strategies = [strategy(index) for index in range(4)]

        def fake_pair(left, right):
            correlated = {left.set_id, right.set_id} == {"set-0", "set-1"}
            return SimpleNamespace(
                pearson_corr=0.99 if correlated else 0.0,
                downside_corr=0.99 if correlated else 0.0,
                dd_overlap=0.99 if correlated else 0.0,
            )

        with patch(
            "mt5_manager.portfolio_monthly_experimental._rotated_candidate_order",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_monthly_experimental.strategy_correlation_pair",
            side_effect=fake_pair,
        ):
            pools = build_experimental_candidate_pools(
                strategies,
                pool_size=2,
                min_trades_2020_2026=15,
            )

        pool_by_id = {
            item.set_id: pool_index
            for pool_index, pool in enumerate(pools)
            for item in pool
        }
        self.assertNotEqual(pool_by_id["set-0"], pool_by_id["set-1"])

    def test_tournament_examines_all_candidates_before_the_final(self) -> None:
        strategies = [strategy(index) for index in range(35)]
        evaluated: list[list[str]] = []

        def fake_optimize(pool, _full_sets, **_kwargs):
            evaluated.append([item.set_id for item in pool])
            allocations = [
                SimpleNamespace(
                    set_id=item.set_id,
                    units=1,
                    net_profit_contribution=item.net_profit_2020_2026_001,
                )
                for item in pool[: max(len(pool) // 2, 1)]
            ]
            return SimpleNamespace(
                allocations=allocations,
                total_net_profit=sum(item.net_profit_2020_2026_001 for item in pool),
                active_strategies=len(allocations),
                actual_valley_dd=10.0,
                target_valley_dd=1000.0,
                target_point_dd=1000.0,
                total_units=len(allocations),
                warnings=[],
                seasonal_validation={},
            )

        with patch(
            "mt5_manager.portfolio_monthly_experimental.filter_eligible_sets",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_monthly_experimental._optimize_exact_pool",
            side_effect=fake_optimize,
        ):
            result = optimize_experimental_monthly_portfolio(
                monthly_sets=strategies,
                full_sets=strategies,
                target_month=1,
                strict_yearly_month_validation=False,
                use_deep_refinement=True,
                min_trades_2020_2026=15,
                max_total_candidates=10,
                top_k_per_symbol=3,
            )

        first_round = evaluated[:12]
        self.assertEqual(
            {set_id for pool in first_round for set_id in pool},
            {item.set_id for item in strategies},
        )
        appearances = {
            set_id: sum(set_id in pool for pool in first_round)
            for set_id in {item.set_id for item in strategies}
        }
        self.assertEqual(set(appearances.values()), {3})
        self.assertTrue(
            any("35/35 candidatos examinados" in warning for warning in result.warnings)
        )
        yearly_audit = result.seasonal_validation[
            "experimental_leave_one_year_out"
        ]
        self.assertEqual(yearly_audit["status"], "completed")
        self.assertEqual(len(yearly_audit["folds"]), 5)

    def test_strict_search_caps_each_pool_at_forty(self) -> None:
        strategies = [strategy(index) for index in range(85)]
        observed_sizes: list[int] = []

        def fake_optimize(pool, _full_sets, **_kwargs):
            observed_sizes.append(len(pool))
            item = pool[0]
            return SimpleNamespace(
                allocations=[
                    SimpleNamespace(
                        set_id=item.set_id,
                        units=1,
                        net_profit_contribution=item.net_profit_2020_2026_001,
                    )
                ],
                total_net_profit=item.net_profit_2020_2026_001,
                active_strategies=1,
                actual_valley_dd=10.0,
                target_valley_dd=1000.0,
                target_point_dd=1000.0,
                total_units=1,
                warnings=[],
                seasonal_validation={},
            )

        with patch(
            "mt5_manager.portfolio_monthly_experimental.filter_eligible_sets",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_monthly_experimental._optimize_exact_pool",
            side_effect=fake_optimize,
        ):
            optimize_experimental_monthly_portfolio(
                monthly_sets=strategies,
                full_sets=strategies,
                target_month=1,
                strict_yearly_month_validation=True,
                use_deep_refinement=False,
                min_trades_2020_2026=15,
                max_total_candidates=100,
                top_k_per_symbol=3,
            )

        self.assertTrue(observed_sizes)
        self.assertLessEqual(max(observed_sizes), 40)


if __name__ == "__main__":
    unittest.main()
