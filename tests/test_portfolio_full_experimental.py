from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_full_experimental import (
    build_experimental_full_candidate_pools,
    optimize_experimental_full_portfolio,
)
from mt5_manager.portfolio_service import (
    PORTFOLIO_TYPES,
    _locked_full_proposals,
    normalize_settings,
)


def strategy(index: int) -> SimpleNamespace:
    increments = [
        float(20 + index % 5),
        float(24 + index % 7),
        float(28 + index % 3),
    ]
    total = 0.0
    curve = [0.0]
    points = []
    for offset, increment in enumerate(increments):
        total += increment
        curve.append(total)
        points.append((datetime(2023 + offset, 6, 15), total))
    in_sample = SimpleNamespace(
        start_year=2020,
        end_year=2024,
        net_profit_001=60.0 + index,
        return_dd_ratio=2.0 + index % 4,
    )
    out_of_sample = SimpleNamespace(
        start_year=2025,
        end_year=2026,
        net_profit_001=35.0 + index,
        return_dd_ratio=1.5 + index % 3,
    )
    return SimpleNamespace(
        set_id=f"set-{index}",
        symbol=("EURUSD", "XAUUSD", "US500")[index % 3],
        robustness_status="accepted",
        already_used=False,
        report_2020_2024=in_sample,
        report_2025_2026=out_of_sample,
        curve_2020_2026_001=curve,
        curve_points_2020_2026_001=points,
        net_profit_2020_2026_001=total,
        return_dd_2020_2026=float(1 + index % 7),
        profit_factor_2020_2026=float(1.1 + (index % 5) / 10),
        valley_dd_2020_2026_001=float(20 + index % 11),
        max_floating_dd_001=float(index % 13),
        trades_2020_2026=130,
        has_recent_performance=True,
        recent_net_profit_001=float(10 + index % 5),
        recent_equity_dd_001=float(3 + index % 4),
        target_month=None,
        month_years=(),
        positive_month_years=(),
    )


def result_for(pool) -> SimpleNamespace:
    allocations = [
        SimpleNamespace(
            set_id=item.set_id,
            units=1,
            net_profit_contribution=item.net_profit_2020_2026_001,
            recent_net_profit_001=item.recent_net_profit_001,
            has_recent_performance=True,
        )
        for item in pool[: max(len(pool) // 2, 1)]
    ]
    return SimpleNamespace(
        allocations=allocations,
        total_net_profit=sum(
            item.net_profit_2020_2026_001 for item in pool
        ),
        active_strategies=len(allocations),
        actual_valley_dd=10.0,
        target_valley_dd=1000.0,
        target_point_dd=1000.0,
        total_units=len(allocations),
        warnings=[],
        seasonal_coverage={},
        seasonal_validation={},
    )


class ExperimentalFullSearchTests(unittest.TestCase):
    def test_full_setting_is_opt_in_and_cannot_leak_to_monthly(self) -> None:
        defaults = normalize_settings(
            "full_history",
            {"allowed_asset_groups": ["Forex"]},
            "ICTRADING",
        )
        enabled = normalize_settings(
            "full_history",
            {
                "allowed_asset_groups": ["Forex"],
                "experimental_full_search": True,
            },
            "ICTRADING",
        )
        monthly = normalize_settings(
            "monthly",
            {
                "allowed_asset_groups": ["Forex"],
                "experimental_full_search": True,
            },
            "ICTRADING",
        )

        self.assertFalse(defaults["experimental_full_search"])
        self.assertTrue(enabled["experimental_full_search"])
        self.assertFalse(monthly["experimental_full_search"])

    def test_candidate_pools_cover_every_strategy_per_rotation(self) -> None:
        strategies = [strategy(index) for index in range(35)]
        signatures = []

        for rotation in range(3):
            pools = build_experimental_full_candidate_pools(
                strategies,
                pool_size=10,
                min_trades_2020_2026=100,
                rotation=rotation,
            )
            flattened = [
                item.set_id for pool in pools for item in pool
            ]
            self.assertEqual(len(flattened), len(set(flattened)))
            self.assertEqual(
                set(flattened),
                {item.set_id for item in strategies},
            )
            self.assertLessEqual(max(map(len, pools)), 10)
            signatures.append(
                frozenset(
                    frozenset(item.set_id for item in pool)
                    for pool in pools
                )
            )

        self.assertEqual(len(set(signatures)), 3)

    def test_highly_correlated_candidates_are_separated(self) -> None:
        strategies = [strategy(index) for index in range(4)]

        def fake_pair(left, right):
            correlated = {
                left.set_id, right.set_id
            } == {"set-0", "set-1"}
            return SimpleNamespace(
                pearson_corr=0.99 if correlated else 0.0,
                downside_corr=0.99 if correlated else 0.0,
                dd_overlap=0.99 if correlated else 0.0,
            )

        with patch(
            "mt5_manager.portfolio_full_experimental._rotated_candidate_order",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_full_experimental.strategy_correlation_pair",
            side_effect=fake_pair,
        ):
            pools = build_experimental_full_candidate_pools(
                strategies,
                pool_size=2,
                min_trades_2020_2026=100,
            )

        pool_by_id = {
            item.set_id: pool_index
            for pool_index, pool in enumerate(pools)
            for item in pool
        }
        self.assertNotEqual(
            pool_by_id["set-0"], pool_by_id["set-1"]
        )

    def test_tournament_examines_every_candidate_and_records_audit(self) -> None:
        strategies = [strategy(index) for index in range(35)]
        evaluated: list[list[str]] = []

        def fake_optimize(pool, **_kwargs):
            evaluated.append([item.set_id for item in pool])
            return result_for(pool)

        with patch(
            "mt5_manager.portfolio_full_experimental.filter_eligible_sets",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_full_experimental._optimize_exact_pool",
            side_effect=fake_optimize,
        ):
            result = optimize_experimental_full_portfolio(
                raw_sets=strategies,
                use_deep_refinement=True,
                min_trades_2020_2026=100,
                max_total_candidates=10,
                top_k_per_symbol=3,
            )

        first_round = evaluated[:12]
        self.assertEqual(
            {
                set_id
                for pool in first_round
                for set_id in pool
            },
            {item.set_id for item in strategies},
        )
        appearances = {
            set_id: sum(
                set_id in pool for pool in first_round
            )
            for set_id in {
                item.set_id for item in strategies
            }
        }
        self.assertEqual(set(appearances.values()), {3})
        self.assertTrue(
            any(
                "35/35 candidatos examinados" in warning
                for warning in result.warnings
            )
        )
        audit = result.seasonal_validation[
            "experimental_full_history_stability"
        ]
        self.assertEqual(audit["status"], "completed")
        self.assertIn("is_2020_2024", audit["segments"])
        self.assertIn("oos_2025_2026", audit["segments"])
        self.assertIn("final_tick_6m", audit["segments"])

    def test_locked_bundle_uses_experimental_only_for_base_selection(self) -> None:
        strategies = [strategy(index) for index in range(4)]
        inputs = normalize_settings(
            "full_history",
            {
                "allowed_asset_groups": ["Forex"],
                "experimental_full_search": True,
            },
            "ICTRADING",
        )
        base_result = result_for(strategies)
        locked = strategies[:2]
        base_result.allocations = result_for(locked).allocations
        base_result.active_strategies = len(locked)
        base_result.seasonal_validation = {
            "experimental_full_history_stability": {
                "status": "completed",
                "passed": True,
            }
        }
        base_result.warnings = [
            "Búsqueda UBS experimental: 4/4 candidatos examinados;"
        ]

        def run_once(candidate_sets, _minimum_recent, optimize):
            return optimize(candidate_sets), set()

        with patch(
            "mt5_manager.portfolio_service._optimize_without_recent_fillers",
            side_effect=run_once,
        ), patch(
            "mt5_manager.portfolio_service.optimize_experimental_full_portfolio",
            return_value=base_result,
        ) as experimental, patch(
            "mt5_manager.portfolio_service.optimize_portfolio",
            side_effect=lambda **_kwargs: result_for(locked),
        ) as stable:
            proposals = _locked_full_proposals(
                strategies,
                inputs,
                {kind: [] for kind in PORTFOLIO_TYPES.values()},
            )

        self.assertEqual(len(proposals), 3)
        experimental.assert_called_once()
        self.assertEqual(stable.call_count, 3)
        for proposal in proposals:
            self.assertEqual(
                proposal["result"].seasonal_validation[
                    "experimental_full_history_stability"
                ]["status"],
                "completed",
            )
            self.assertTrue(
                any(
                    warning.startswith("Búsqueda UBS experimental:")
                    for warning in proposal["result"].warnings
                )
            )


if __name__ == "__main__":
    unittest.main()
