from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_full_experimental import (
    _result_rank,
    build_experimental_full_candidate_pools,
    optimize_experimental_full_portfolio,
)
from mt5_manager.portfolio_service import (
    PORTFOLIO_TYPES,
    _locked_full_proposals,
    _underrepresented_recent_allocation_ids,
    normalize_settings,
)


def allocation(
    set_id: str,
    *,
    units: int = 1,
    recent: float = 10.0,
    contribution: float = 100.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        set_id=set_id,
        units=units,
        net_profit_contribution=contribution,
        recent_net_profit_001=recent,
        has_recent_performance=True,
    )


def built_result(allocations, *, profit: float | None = None) -> SimpleNamespace:
    allocations = list(allocations)
    return SimpleNamespace(
        allocations=allocations,
        total_net_profit=float(
            profit
            if profit is not None
            else sum(item.net_profit_contribution for item in allocations)
        ),
        active_strategies=len([item for item in allocations if item.units > 0]),
        actual_valley_dd=10.0,
        target_valley_dd=1000.0,
        target_point_dd=1000.0,
        total_units=sum(item.units for item in allocations),
        warnings=[],
        seasonal_coverage={},
        seasonal_validation={},
    )


def recent_fillers(result) -> set[str]:
    return _underrepresented_recent_allocation_ids(result, 5.0)


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

        def run_once(candidate_sets, _minimum_recent, optimize, *, progress=None):
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


class ExperimentalRecentContributionTests(unittest.TestCase):
    """La regla de aporte reciente no debe encoger la composición elegida."""

    def test_recent_fillers_are_replaced_from_the_winning_pool(self) -> None:
        strategies = [strategy(index) for index in range(6)]
        pools_seen: list[list[str]] = []

        def fake_optimize(pool, **_kwargs):
            pools_seen.append([item.set_id for item in pool])
            if len(pools_seen) == 1:
                # Dos miembros con peso y dos rellenos 6M.
                return built_result([
                    allocation("set-0", units=10, recent=50.0, contribution=500.0),
                    allocation("set-1", units=10, recent=45.0, contribution=450.0),
                    allocation("set-2", units=1, recent=0.2, contribution=5.0),
                    allocation("set-3", units=1, recent=0.2, contribution=5.0),
                ], profit=960.0)
            return built_result([
                allocation("set-0", units=10, recent=50.0, contribution=500.0),
                allocation("set-1", units=10, recent=45.0, contribution=450.0),
                allocation("set-4", units=8, recent=40.0, contribution=400.0),
                allocation("set-5", units=8, recent=38.0, contribution=380.0),
            ], profit=1730.0)

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
                recent_filler_ids=recent_fillers,
                min_trades_2020_2026=100,
                max_total_candidates=10,
                top_k_per_symbol=3,
            )

        self.assertEqual(len(pools_seen), 2)
        # La reoptimizacion no se limita a los supervivientes: ofrece el resto
        # del lote ganador, incluidos candidatos que la primera pasada no eligio.
        self.assertEqual(
            set(pools_seen[1]), {"set-0", "set-1", "set-4", "set-5"}
        )
        self.assertEqual(result.active_strategies, 4)
        self.assertEqual(result.total_net_profit, 1730.0)
        self.assertTrue(
            any(
                warning.startswith(
                    "Regla antirrelleno 6M en la búsqueda experimental:"
                )
                for warning in result.warnings
            )
        )

    def test_finalists_are_ranked_after_the_recent_contribution_rule(self) -> None:
        strategies = [strategy(index) for index in range(6)]
        calls: list[list[str]] = []

        def fake_optimize(pool, **_kwargs):
            calls.append([item.set_id for item in pool])
            items = list(pool)
            stage = len(calls)
            if stage <= 6:
                # Rondas clasificatorias: composicion limpia y repartida.
                return built_result([
                    allocation(item.set_id, units=5, recent=30.0, contribution=300.0)
                    for item in items
                ])
            if stage == 7:
                # Final completa: mas beneficio sobre el papel, pero casi todo
                # relleno; la regla la va a dejar en un solo miembro.
                return built_result(
                    [allocation(items[0].set_id, units=10, recent=100.0, contribution=1000.0)]
                    + [
                        allocation(item.set_id, units=1, recent=0.1, contribution=5.0)
                        for item in items[1:]
                    ],
                    profit=1500.0,
                )
            # Sin reposicion posible: solo queda el miembro con peso.
            return built_result(
                [allocation(items[0].set_id, units=10, recent=100.0, contribution=800.0)],
                profit=800.0,
            )

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
                recent_filler_ids=recent_fillers,
                min_trades_2020_2026=100,
                max_total_candidates=3,
                top_k_per_symbol=3,
            )

        # Gana la composicion amplia que sobrevive a la regla, no la que
        # declaraba 1500 antes de aplicarla y se queda en un miembro.
        self.assertEqual(result.total_net_profit, 900.0)
        self.assertEqual(result.active_strategies, 3)

    def test_antifiller_retries_continue_until_clean_and_shrink_the_pool(self) -> None:
        strategies = [strategy(index) for index in range(12)]
        calls: list[int] = []

        def always_leaves_fillers(pool, **_kwargs):
            calls.append(len(pool))
            items = list(pool)
            return built_result(
                [allocation(items[0].set_id, units=10, recent=100.0, contribution=1000.0)]
                + [
                    allocation(item.set_id, units=1, recent=0.1, contribution=5.0)
                    for item in items[1:3]
                ]
            )

        with patch(
            "mt5_manager.portfolio_full_experimental.filter_eligible_sets",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_full_experimental._optimize_exact_pool",
            side_effect=always_leaves_fillers,
        ):
            result = optimize_experimental_full_portfolio(
                raw_sets=strategies,
                use_deep_refinement=True,
                recent_filler_ids=recent_fillers,
                min_trades_2020_2026=100,
                max_total_candidates=20,
                top_k_per_symbol=3,
            )

        # Requiere mas de los tres intentos antiguos. Cada vuelta reduce el lote,
        # por lo que termina sin relanzar el torneo ni devolver rellenos.
        self.assertEqual(calls, [12, 10, 8, 6, 4, 2, 1])
        self.assertEqual(recent_fillers(result), set())

    def test_result_rank_still_rewards_breadth_over_concentration(self) -> None:
        # Guarda del objetivo del modo: a igual beneficio gana la composicion
        # amplia. Meter la cuota de aporte reciente en el rango invertiria esto
        # y convertiria la busqueda experimental en la estandar.
        broad = built_result([
            allocation(f"set-{index}", units=2, recent=10.0, contribution=100.0)
            for index in range(6)
        ], profit=600.0)
        narrow = built_result([
            allocation("set-0", units=12, recent=60.0, contribution=600.0)
        ], profit=600.0)

        self.assertGreater(_result_rank(broad), _result_rank(narrow))

    def test_the_tournament_record_survives_a_survivor_rerun(self) -> None:
        strategies = [strategy(index) for index in range(4)]
        inputs = normalize_settings(
            "full_history",
            {
                "allowed_asset_groups": ["Forex"],
                "experimental_full_search": True,
            },
            "ICTRADING",
        )
        locked = strategies[:2]

        def tournament_result(warning: str, audit_active: int) -> SimpleNamespace:
            result = result_for(strategies)
            result.allocations = result_for(locked).allocations
            result.active_strategies = len(locked)
            result.warnings = [warning]
            result.seasonal_validation = {
                "experimental_full_history_stability": {
                    "status": "completed",
                    "passed": True,
                    "active_strategies": audit_active,
                }
            }
            return result

        engine_results = [
            tournament_result(
                "Búsqueda UBS experimental: 486/486 candidatos examinados; "
                "3 ronda(s).",
                8,
            ),
            tournament_result(
                "Búsqueda UBS experimental: 4/4 candidatos examinados; "
                "0 ronda(s).",
                4,
            ),
        ]

        def refine_over_survivors(
            candidate_sets, _minimum_recent, optimize, *, progress=None
        ):
            optimize(candidate_sets)
            return optimize(candidate_sets[:2]), {"set-2", "set-3"}

        with patch(
            "mt5_manager.portfolio_service._optimize_without_recent_fillers",
            side_effect=refine_over_survivors,
        ), patch(
            "mt5_manager.portfolio_service.optimize_experimental_full_portfolio",
            side_effect=engine_results,
        ), patch(
            "mt5_manager.portfolio_service.optimize_portfolio",
            side_effect=lambda **_kwargs: result_for(locked),
        ):
            proposals = _locked_full_proposals(
                strategies,
                inputs,
                {kind: [] for kind in PORTFOLIO_TYPES.values()},
            )

        self.assertEqual(len(proposals), 3)
        for proposal in proposals:
            warnings = proposal["result"].warnings
            self.assertTrue(
                any("486/486 candidatos examinados" in item for item in warnings)
            )
            self.assertFalse(any("0 ronda(s)" in item for item in warnings))
            self.assertEqual(
                proposal["result"].seasonal_validation[
                    "experimental_full_history_stability"
                ]["active_strategies"],
                8,
            )


if __name__ == "__main__":
    unittest.main()
