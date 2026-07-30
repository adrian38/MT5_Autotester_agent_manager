from __future__ import annotations

import hashlib
from math import ceil
from typing import Any, Callable, Sequence

from portfolio_manager.ubs_portfolio import (
    CorrelationPair,
    PortfolioResult,
    RobustStrategySet,
    filter_eligible_sets,
    optimize_portfolio,
    score_set_for_portfolio,
    strategy_correlation_pair,
)


Progress = Callable[[str], None]
EXPERIMENTAL_FULL_POOL_ROTATIONS = 3


def _strategy_id(strategy: RobustStrategySet) -> str:
    return str(strategy.set_id)


def _period_years(report: Any) -> int:
    start = int(getattr(report, "start_year", 0) or 0)
    end = int(getattr(report, "end_year", start) or start)
    return max(end - start + 1, 1)


def _segment_stability_key(
    strategy: RobustStrategySet,
) -> tuple[int, float, float, float, float]:
    in_sample = strategy.report_2020_2024
    out_of_sample = strategy.report_2025_2026
    in_net = float(in_sample.net_profit_001)
    out_net = float(out_of_sample.net_profit_001)
    in_annual = in_net / _period_years(in_sample)
    out_annual = out_net / _period_years(out_of_sample)
    annual_balance = (
        min(in_annual, out_annual) / max(in_annual, out_annual, 1.0)
        if in_annual > 0 and out_annual > 0
        else min(in_annual, out_annual)
    )
    minimum_rdd = min(
        float(in_sample.return_dd_ratio),
        float(out_of_sample.return_dd_ratio),
    )
    recent_recovery = (
        float(strategy.recent_net_profit_001)
        / max(float(strategy.recent_equity_dd_001), 1.0)
        if strategy.has_recent_performance
        else -1.0
    )
    return (
        int(in_net > 0 and out_net > 0),
        minimum_rdd,
        annual_balance,
        recent_recovery,
        float(strategy.net_profit_2020_2026_001),
    )


def _low_risk_key(strategy: RobustStrategySet) -> tuple[float, float, float]:
    valley_dd = float(strategy.valley_dd_2020_2026_001)
    floating_dd = float(strategy.max_floating_dd_001)
    net_profit = float(strategy.net_profit_2020_2026_001)
    risk = max(valley_dd + floating_dd, 1.0)
    return net_profit / risk, -risk, net_profit


def _interleaved_candidate_order(
    strategies: Sequence[RobustStrategySet],
    min_trades_2020_2026: int,
) -> list[RobustStrategySet]:
    """Mix full-history quality lenses so one global score cannot own the funnel."""
    rankings = (
        sorted(
            strategies,
            key=lambda item: score_set_for_portfolio(
                item, min_trades_2020_2026
            ),
            reverse=True,
        ),
        sorted(
            strategies,
            key=lambda item: (
                float(item.net_profit_2020_2026_001),
                float(item.return_dd_2020_2026),
            ),
            reverse=True,
        ),
        sorted(
            strategies,
            key=lambda item: (
                float(item.return_dd_2020_2026),
                float(item.profit_factor_2020_2026),
            ),
            reverse=True,
        ),
        sorted(strategies, key=_segment_stability_key, reverse=True),
        sorted(strategies, key=_low_risk_key, reverse=True),
    )
    ordered: list[RobustStrategySet] = []
    seen: set[str] = set()
    for rank in range(len(strategies)):
        for ranking in rankings:
            strategy = ranking[rank]
            set_id = _strategy_id(strategy)
            if set_id in seen:
                continue
            ordered.append(strategy)
            seen.add(set_id)
    return ordered


def _rotated_candidate_order(
    strategies: Sequence[RobustStrategySet],
    min_trades_2020_2026: int,
    rotation: int,
) -> list[RobustStrategySet]:
    ordered = _interleaved_candidate_order(
        strategies, min_trades_2020_2026
    )
    if int(rotation) <= 0:
        return ordered
    return sorted(
        ordered,
        key=lambda strategy: hashlib.sha256(
            f"full:{int(rotation)}:{_strategy_id(strategy)}".encode("utf-8")
        ).digest(),
    )


def _correlation_cache_key(
    strategy_a: RobustStrategySet,
    strategy_b: RobustStrategySet,
) -> tuple[str, str]:
    return tuple(
        sorted((_strategy_id(strategy_a), _strategy_id(strategy_b)))
    )


def _correlation_penalty(
    strategy_a: RobustStrategySet,
    strategy_b: RobustStrategySet,
    cache: dict[tuple[str, str], CorrelationPair],
) -> float:
    key = _correlation_cache_key(strategy_a, strategy_b)
    pair = cache.get(key)
    if pair is None:
        pair = strategy_correlation_pair(strategy_a, strategy_b)
        cache[key] = pair
    return max(
        float(pair.pearson_corr),
        float(pair.downside_corr),
        float(pair.dd_overlap),
        0.0,
    )


def _pool_diversity_key(
    strategy: RobustStrategySet,
    pool: Sequence[RobustStrategySet],
    cache: dict[tuple[str, str], CorrelationPair],
) -> tuple[float, float]:
    if not pool:
        return 0.0, 0.0
    penalties = [
        _correlation_penalty(strategy, member, cache)
        for member in pool
    ]
    return max(penalties), sum(penalties) / len(penalties)


def build_experimental_full_candidate_pools(
    strategies: Sequence[RobustStrategySet],
    *,
    pool_size: int,
    min_trades_2020_2026: int,
    rotation: int = 0,
    correlation_cache: dict[tuple[str, str], CorrelationPair] | None = None,
) -> list[list[RobustStrategySet]]:
    """Partition every full-history candidate once into diversified pools."""
    unique_by_id = {
        _strategy_id(strategy): strategy for strategy in strategies
    }
    unique = list(unique_by_id.values())
    if not unique:
        return []
    size = max(int(pool_size), 1)
    pool_count = max(ceil(len(unique) / size), 1)
    pools: list[list[RobustStrategySet]] = [
        [] for _ in range(pool_count)
    ]
    cache = correlation_cache if correlation_cache is not None else {}
    ordered = _rotated_candidate_order(
        unique,
        int(min_trades_2020_2026),
        int(rotation),
    )
    for order_index, strategy in enumerate(ordered):
        available = [
            pool_index
            for pool_index, pool in enumerate(pools)
            if len(pool) < size
        ]
        preferred = (order_index + int(rotation)) % pool_count
        selected_index = min(
            available,
            key=lambda pool_index: (
                *_pool_diversity_key(
                    strategy, pools[pool_index], cache
                ),
                len(pools[pool_index]),
                (pool_index - preferred) % pool_count,
            ),
        )
        pools[selected_index].append(strategy)
    return [pool for pool in pools if pool]


def _result_rank(
    result: PortfolioResult,
) -> tuple[float, int, float, int]:
    return (
        float(result.total_net_profit),
        int(result.active_strategies),
        -float(result.actual_valley_dd),
        int(result.total_units),
    )


def _dominates(left: PortfolioResult, right: PortfolioResult) -> bool:
    left_values = (
        float(left.total_net_profit),
        int(left.active_strategies),
        -float(left.actual_valley_dd),
    )
    right_values = (
        float(right.total_net_profit),
        int(right.active_strategies),
        -float(right.actual_valley_dd),
    )
    return (
        all(
            left_value >= right_value
            for left_value, right_value in zip(left_values, right_values)
        )
        and any(
            left_value > right_value
            for left_value, right_value in zip(left_values, right_values)
        )
    )


def _pareto_archive(
    results: Sequence[PortfolioResult],
) -> list[PortfolioResult]:
    return [
        candidate
        for candidate in results
        if not any(
            challenger is not candidate
            and _dominates(challenger, candidate)
            for challenger in results
        )
    ]


def _active_allocation_ids(result: PortfolioResult) -> set[str]:
    return {
        str(allocation.set_id)
        for allocation in result.allocations
        if int(allocation.units) > 0
    }


def _optimize_exact_pool(
    candidate_pool: list[RobustStrategySet],
    *,
    use_deep_refinement: bool,
    optimizer_kwargs: dict[str, Any],
) -> PortfolioResult:
    exact_kwargs = dict(optimizer_kwargs)
    exact_kwargs["top_k_per_symbol"] = max(
        int(exact_kwargs.get("top_k_per_symbol") or 1),
        len(candidate_pool),
    )
    exact_kwargs["max_total_candidates"] = None
    return optimize_portfolio(
        raw_sets=candidate_pool,
        use_deep_refinement=bool(use_deep_refinement),
        **exact_kwargs,
    )


def _segment_stability_audit(
    result: PortfolioResult,
    candidate_pool: Sequence[RobustStrategySet],
) -> dict[str, object]:
    by_id = {
        _strategy_id(strategy): strategy for strategy in candidate_pool
    }
    active = [
        allocation
        for allocation in result.allocations
        if int(allocation.units) > 0
        and str(allocation.set_id) in by_id
    ]
    if not active:
        return {
            "status": "no_active_allocations",
            "passed": False,
            "segments": {},
        }

    def period_metrics(attribute: str) -> dict[str, object]:
        rows = [
            (
                by_id[str(allocation.set_id)],
                int(allocation.units),
            )
            for allocation in active
        ]
        nets = [
            float(getattr(strategy, attribute).net_profit_001)
            for strategy, _units in rows
        ]
        weighted_net = sum(
            float(getattr(strategy, attribute).net_profit_001) * units
            for strategy, units in rows
        )
        positive = sum(net > 0 for net in nets)
        years = max(
            _period_years(getattr(strategy, attribute))
            for strategy, _units in rows
        )
        return {
            "net_profit_001": weighted_net,
            "annualized_net_profit_001": weighted_net / years,
            "positive_strategies": positive,
            "strategy_count": len(rows),
            "positive_rate": positive / len(rows),
            "years": years,
        }

    in_sample = period_metrics("report_2020_2024")
    out_of_sample = period_metrics("report_2025_2026")
    recent_rows = [
        (
            by_id[str(allocation.set_id)],
            int(allocation.units),
        )
        for allocation in active
        if by_id[str(allocation.set_id)].has_recent_performance
    ]
    recent_positive = sum(
        float(strategy.recent_net_profit_001) > 0
        for strategy, _units in recent_rows
    )
    recent_net = sum(
        float(strategy.recent_net_profit_001) * units
        for strategy, units in recent_rows
    )
    recent_dd = sum(
        max(float(strategy.recent_equity_dd_001), 0.0) * units
        for strategy, units in recent_rows
    )
    recent = {
        "net_profit_001": recent_net,
        "equity_dd_001": recent_dd,
        "recovery_ratio": recent_net / max(recent_dd, 1.0),
        "positive_strategies": recent_positive,
        "strategy_count": len(recent_rows),
        "positive_rate": (
            recent_positive / len(recent_rows)
            if recent_rows
            else 0.0
        ),
        "coverage_rate": len(recent_rows) / len(active),
    }
    in_annual = float(in_sample["annualized_net_profit_001"])
    out_annual = float(out_of_sample["annualized_net_profit_001"])
    annualized_ratio = (
        out_annual / in_annual
        if in_annual > 0
        else 0.0
    )
    recent_passed = (
        not recent_rows
        or (
            recent_net > 0
            and float(recent["positive_rate"]) >= 0.5
        )
    )
    passed = (
        float(in_sample["net_profit_001"]) > 0
        and float(out_of_sample["net_profit_001"]) > 0
        and float(in_sample["positive_rate"]) >= 0.6
        and float(out_of_sample["positive_rate"]) >= 0.6
        and 0.2 <= annualized_ratio <= 5.0
        and recent_passed
    )
    return {
        "status": "completed",
        "passed": passed,
        "active_strategies": len(active),
        "segments": {
            "is_2020_2024": in_sample,
            "oos_2025_2026": out_of_sample,
            "final_tick_6m": recent,
        },
        "oos_to_is_annualized_ratio": annualized_ratio,
    }


def optimize_experimental_full_portfolio(
    *,
    raw_sets: list[RobustStrategySet],
    use_deep_refinement: bool,
    progress: Progress | None = None,
    **optimizer_kwargs: Any,
) -> PortfolioResult:
    """Evaluate every eligible full-history strategy before fixing A/M/C sets."""
    minimum_trades = int(
        optimizer_kwargs.get("min_trades_2020_2026") or 0
    )
    eligible = filter_eligible_sets(raw_sets, minimum_trades)
    if not eligible:
        raise ValueError(
            "No hay candidatos UBS elegibles para la búsqueda experimental."
        )

    pool_size = max(
        int(optimizer_kwargs.get("max_total_candidates") or 30),
        2,
    )
    current = eligible
    evaluated_ids: set[str] = set()
    successful_pools = 0
    failed_pools = 0
    round_number = 0
    total_exposures = 0
    best_result: PortfolioResult | None = None
    best_result_pool: list[RobustStrategySet] = []
    correlation_cache: dict[
        tuple[str, str], CorrelationPair
    ] = {}

    while len(current) > pool_size:
        round_number += 1
        appearances = {
            _strategy_id(strategy): 0 for strategy in current
        }
        selections = {
            _strategy_id(strategy): 0 for strategy in current
        }
        contributions = {
            _strategy_id(strategy): 0.0 for strategy in current
        }
        round_results: list[PortfolioResult] = []
        rotation_pools = [
            build_experimental_full_candidate_pools(
                current,
                pool_size=pool_size,
                min_trades_2020_2026=minimum_trades,
                rotation=rotation,
                correlation_cache=correlation_cache,
            )
            for rotation in range(EXPERIMENTAL_FULL_POOL_ROTATIONS)
        ]
        if progress:
            progress(
                "Búsqueda experimental UBS: "
                f"ronda {round_number}, {len(current)} candidatos, "
                f"{EXPERIMENTAL_FULL_POOL_ROTATIONS} rotaciones"
            )
        qualifying_kwargs = dict(optimizer_kwargs)
        qualifying_kwargs["search_restarts"] = min(
            int(qualifying_kwargs.get("search_restarts") or 0),
            1,
        )
        qualifying_kwargs["run_local_search"] = False
        for rotation, pools in enumerate(rotation_pools, 1):
            for pool_index, pool in enumerate(pools, 1):
                pool_ids = {
                    _strategy_id(strategy) for strategy in pool
                }
                evaluated_ids.update(pool_ids)
                total_exposures += len(pool_ids)
                for set_id in pool_ids:
                    appearances[set_id] += 1
                try:
                    result = _optimize_exact_pool(
                        pool,
                        use_deep_refinement=False,
                        optimizer_kwargs=qualifying_kwargs,
                    )
                    successful_pools += 1
                    round_results.append(result)
                    if (
                        best_result is None
                        or _result_rank(result) > _result_rank(best_result)
                    ):
                        best_result = result
                        best_result_pool = pool
                    for allocation in result.allocations:
                        set_id = str(allocation.set_id)
                        if (
                            int(allocation.units) <= 0
                            or set_id not in selections
                        ):
                            continue
                        selections[set_id] += 1
                        contributions[set_id] += float(
                            allocation.net_profit_contribution
                        )
                except Exception:
                    failed_pools += 1
                if progress:
                    progress(
                        "Búsqueda experimental UBS: "
                        f"rotación {rotation}/"
                        f"{EXPERIMENTAL_FULL_POOL_ROTATIONS}, "
                        f"lote {pool_index}/{len(pools)}"
                    )

        pareto_ids = {
            set_id
            for result in _pareto_archive(round_results)
            for set_id in _active_allocation_ids(result)
        }
        base_order = _interleaved_candidate_order(
            current, minimum_trades
        )
        base_rank = {
            _strategy_id(strategy): len(base_order) - index
            for index, strategy in enumerate(base_order)
        }
        advancing_count = max(
            int(ceil(len(current) / 2)),
            min(pool_size, len(current)),
        )
        advancing = sorted(
            current,
            key=lambda strategy: (
                selections[_strategy_id(strategy)]
                / max(appearances[_strategy_id(strategy)], 1),
                1 if _strategy_id(strategy) in pareto_ids else 0,
                contributions[_strategy_id(strategy)]
                / max(selections[_strategy_id(strategy)], 1),
                _segment_stability_key(strategy),
                base_rank[_strategy_id(strategy)],
            ),
            reverse=True,
        )[:advancing_count]
        if not advancing or len(advancing) >= len(current):
            break
        current = advancing

    evaluated_ids.update(
        _strategy_id(strategy) for strategy in current
    )
    final_result: PortfolioResult | None = None
    final_error: Exception | None = None
    try:
        if progress:
            progress(
                "Búsqueda experimental UBS: "
                f"final completa con {len(current)} candidatos"
            )
        final_result = _optimize_exact_pool(
            current,
            use_deep_refinement=use_deep_refinement,
            optimizer_kwargs=optimizer_kwargs,
        )
        successful_pools += 1
    except Exception as exc:
        failed_pools += 1
        final_error = exc

    if final_result is not None and (
        best_result is None
        or _result_rank(final_result) >= _result_rank(best_result)
    ):
        selected_result = final_result
        selected_pool = current
    elif best_result is not None:
        selected_result = best_result
        selected_pool = best_result_pool
    elif final_error is not None:
        raise ValueError(
            "La búsqueda UBS experimental no encontró ningún lote viable."
        ) from final_error
    else:
        raise ValueError(
            "La búsqueda UBS experimental no produjo resultados."
        )

    stability = _segment_stability_audit(
        selected_result, selected_pool
    )
    selected_result.seasonal_validation = dict(
        selected_result.seasonal_validation or {}
    )
    selected_result.seasonal_validation[
        "experimental_full_history_stability"
    ] = stability

    missing = sorted(
        {_strategy_id(strategy) for strategy in eligible}
        - evaluated_ids
    )
    selected_result.warnings.append(
        "Búsqueda UBS experimental: "
        f"{len(evaluated_ids)}/{len(eligible)} candidatos examinados; "
        f"{total_exposures} exposiciones clasificatorias en "
        f"{EXPERIMENTAL_FULL_POOL_ROTATIONS} rotaciones; "
        f"{successful_pools} lotes viables, "
        f"{failed_pools} no viables; "
        f"{round_number} ronda(s)."
    )
    if stability.get("status") == "completed":
        segments = stability["segments"]
        selected_result.warnings.append(
            "Estabilidad UBS experimental IS/OOS/6M: "
            f"IS {float(segments['is_2020_2024']['positive_rate']) * 100:.1f}% "
            "positivas; "
            f"OOS {float(segments['oos_2025_2026']['positive_rate']) * 100:.1f}% "
            "positivas; "
            f"6M {float(segments['final_tick_6m']['positive_rate']) * 100:.1f}% "
            "positivas; "
            f"{'OK' if stability['passed'] else 'REVISAR'}."
        )
    if missing:
        selected_result.warnings.append(
            "Advertencia experimental UBS: quedaron sin examinar "
            f"{len(missing)} candidato(s) por una interrupción del torneo."
        )
    return selected_result
