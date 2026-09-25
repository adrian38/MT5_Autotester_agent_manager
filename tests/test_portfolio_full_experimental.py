from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_full_experimental import (
    EXPERIMENTAL_FULL_ANTIFILLER_RETRIES,
    _result_rank,
    build_experimental_full_candidate_pools,
    optimize_experimental_full_portfolio,
)
from mt5_manager.portfolio_service import (
    PORTFOLIO_TYPES,
    _locked_full_proposals,
    normalize_settings,
)
from tests.portfolio_full_experimental_fixtures import (
    allocation,
    built_result,
    recent_fillers,
    result_for,
    strategy,
)












def _experimental_inputs():
    return normalize_settings(
        "full_history",
        {
            "allowed_asset_groups": ["Forex"],
            "experimental_full_search": True,
        },
        "ICTRADING",
    )


def _run_locked_bundle(strategies, locked, engine, refine):
    """Ejecuta el paquete A/M/C con el motor y la primitiva sustituidos."""
    with patch(
        "mt5_manager.portfolio_generation_search._optimize_without_recent_fillers",
        side_effect=refine,
    ), patch(
        "mt5_manager.portfolio_generation_search.optimize_experimental_full_portfolio",
        **engine,
    ) as experimental, patch(
        "mt5_manager.portfolio_generation_search.optimize_portfolio",
        side_effect=lambda **_kwargs: result_for(locked),
    ) as stable:
        proposals = _locked_full_proposals(
            strategies,
            _experimental_inputs(),
            {kind: [] for kind in PORTFOLIO_TYPES.values()},
        )
    return proposals, experimental, stable


def _tournament_result(strategies, locked, warning: str, audit_active: int):
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

    def _assert_round_robin_coverage(self, first_round, strategies) -> None:
        """Cada candidato aparece el mismo numero de veces en la primera ronda."""
        self.assertEqual(
            {set_id for pool in first_round for set_id in pool},
            {item.set_id for item in strategies},
        )
        appearances = {
            set_id: sum(set_id in pool for pool in first_round)
            for set_id in {item.set_id for item in strategies}
        }
        self.assertEqual(set(appearances.values()), {3})

    def _assert_only_the_final_pass_refines(self, settings_seen) -> None:
        """Las rondas del torneo van sin reinicios ni refinado profundo."""
        for settings in settings_seen[:-1]:
            self.assertEqual(settings["optimizer_kwargs"]["search_restarts"], 0)
            self.assertFalse(settings["use_deep_refinement"])
        self.assertTrue(settings_seen[-1]["use_deep_refinement"])

    def test_tournament_examines_every_candidate_and_records_audit(self) -> None:
        strategies = [strategy(index) for index in range(35)]
        evaluated: list[list[str]] = []
        settings_seen: list[dict[str, object]] = []

        def fake_optimize(pool, **kwargs):
            evaluated.append([item.set_id for item in pool])
            settings_seen.append(kwargs)
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

        self._assert_round_robin_coverage(evaluated[:12], strategies)
        self._assert_only_the_final_pass_refines(settings_seen)
        self.assertTrue(
            any("35/35 candidatos examinados" in warning for warning in result.warnings)
        )
        audit = result.seasonal_validation["experimental_full_history_stability"]
        self.assertEqual(audit["status"], "completed")
        self.assertIn("is_2020_2024", audit["segments"])
        self.assertIn("oos_2025_2026", audit["segments"])
        self.assertIn("final_tick_6m", audit["segments"])

    def test_locked_bundle_uses_experimental_only_for_base_selection(self) -> None:
        strategies = [strategy(index) for index in range(4)]
        locked = strategies[:2]
        base_result = result_for(strategies)
        base_result.allocations = result_for(locked).allocations
        base_result.active_strategies = len(locked)
        base_result.seasonal_validation = {
            "experimental_full_history_stability": {"status": "completed", "passed": True}
        }
        base_result.warnings = ["Búsqueda UBS experimental: 4/4 candidatos examinados;"]
        refill_flags: list[bool] = []

        def run_once(
            candidate_sets, _minimum_recent, optimize, *, progress=None, refill_from_pool=False,
        ):
            refill_flags.append(refill_from_pool)
            return optimize(candidate_sets), set()

        proposals, experimental, stable = _run_locked_bundle(
            strategies, locked, {"return_value": base_result}, run_once,
        )

        self.assertEqual(len(proposals), 3)
        experimental.assert_called_once()
        self.assertEqual(stable.call_count, 3)
        # El motor repone dentro del torneo: reabrir el pool en la primitiva
        # compartida relanzaria el torneo entero por cada relleno.
        self.assertEqual(refill_flags, [False])
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

    def test_antifiller_retries_stop_at_budget(self) -> None:
        strategies = [strategy(index) for index in range(30)]
        calls: list[int] = []

        def always_leaves_fillers(pool, **_kwargs):
            calls.append(len(pool))
            items = list(pool)
            return built_result(
                [allocation(items[0].set_id, units=10, recent=100.0)]
                + [allocation(items[1].set_id, units=1, recent=0.1)]
            )

        with patch(
            "mt5_manager.portfolio_full_experimental.filter_eligible_sets",
            return_value=strategies,
        ), patch(
            "mt5_manager.portfolio_full_experimental._optimize_exact_pool",
            side_effect=always_leaves_fillers,
        ):
            with self.assertRaisesRegex(ValueError, "sin rellenos 6M"):
                optimize_experimental_full_portfolio(
                    raw_sets=strategies,
                    use_deep_refinement=True,
                    recent_filler_ids=recent_fillers,
                    min_trades_2020_2026=100,
                    max_total_candidates=40,
                    top_k_per_symbol=3,
                )

        self.assertEqual(
            len(calls), 1 + EXPERIMENTAL_FULL_ANTIFILLER_RETRIES
        )

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
        locked = strategies[:2]
        engine_results = [
            _tournament_result(
                strategies, locked,
                "Búsqueda UBS experimental: 486/486 candidatos examinados; 3 ronda(s).",
                8,
            ),
            _tournament_result(
                strategies, locked,
                "Búsqueda UBS experimental: 4/4 candidatos examinados; 0 ronda(s).",
                4,
            ),
        ]
        refill_flags: list[bool] = []

        def refine_over_survivors(
            candidate_sets, _minimum_recent, optimize, *, progress=None, refill_from_pool=False,
        ):
            refill_flags.append(refill_from_pool)
            optimize(candidate_sets)
            return optimize(candidate_sets[:2]), {"set-2", "set-3"}

        proposals, _experimental, _stable = _run_locked_bundle(
            strategies, locked, {"side_effect": engine_results}, refine_over_survivors,
        )

        self.assertEqual(len(proposals), 3)
        self.assertEqual(refill_flags, [False])
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
