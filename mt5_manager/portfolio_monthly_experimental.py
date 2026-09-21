from __future__ import annotations

import hashlib
from math import ceil
from typing import Any, Callable, Sequence

from portfolio_manager.ubs_portfolio import (
    optimizer_overrides,
    CorrelationPair,
    PortfolioResult,
    RobustStrategySet,
    filter_eligible_sets,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
    score_set_for_portfolio,
    strategy_correlation_pair,
)
from .portfolio_monthly_validation import (
    EXPERIMENTAL_LOYO_YEARS,
    _active_allocation_ids,
    leave_one_year_out_audit as _leave_one_year_out_audit,
)


Progress = Callable[[str], None]
EXPERIMENTAL_POOL_ROTATIONS = 3


def _strategy_id(strategy: RobustStrategySet) -> str:
    return str(strategy.set_id)


def _consistency_key(strategy: RobustStrategySet) -> tuple[float, int, float]:
    years = tuple(strategy.month_years or ())
    positive_years = tuple(strategy.positive_month_years or ())
    ratio = len(positive_years) / max(len(years), 1)
    return ratio, len(positive_years), float(strategy.net_profit_2020_2026_001)


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
    """Mix several monthly lenses without allowing one score to own the funnel."""
    rankings = (
        sorted(
            strategies,
            key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
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
        sorted(strategies, key=_consistency_key, reverse=True),
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
    ordered = _interleaved_candidate_order(strategies, min_trades_2020_2026)
    if int(rotation) <= 0:
        return ordered
    return sorted(
        ordered,
        key=lambda strategy: hashlib.sha256(
            f"{int(rotation)}:{_strategy_id(strategy)}".encode("utf-8")
        ).digest(),
    )


def _correlation_cache_key(
    strategy_a: RobustStrategySet,
    strategy_b: RobustStrategySet,
) -> tuple[str, str]:
    return tuple(sorted((_strategy_id(strategy_a), _strategy_id(strategy_b))))


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


def build_experimental_candidate_pools(
    strategies: Sequence[RobustStrategySet],
    *,
    pool_size: int,
    min_trades_2020_2026: int,
    rotation: int = 0,
    correlation_cache: dict[tuple[str, str], CorrelationPair] | None = None,
) -> list[list[RobustStrategySet]]:
    """Partition every candidate once into correlation-diversified bounded pools."""
    unique_by_id = {_strategy_id(strategy): strategy for strategy in strategies}
    unique = list(unique_by_id.values())
    if not unique:
        return []
    size = max(int(pool_size), 1)
    pool_count = max(ceil(len(unique) / size), 1)
    pools: list[list[RobustStrategySet]] = [[] for _ in range(pool_count)]
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
                *_pool_diversity_key(strategy, pools[pool_index], cache),
                len(pools[pool_index]),
                (pool_index - preferred) % pool_count,
            ),
        )
        pools[selected_index].append(strategy)
    return [pool for pool in pools if pool]


def _result_rank(result: PortfolioResult) -> tuple[float, int, float, int]:
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


def _pareto_archive(results: Sequence[PortfolioResult]) -> list[PortfolioResult]:
    return [
        candidate
        for candidate in results
        if not any(
            challenger is not candidate and _dominates(challenger, candidate)
            for challenger in results
        )
    ]


def _optimize_exact_pool(
    candidate_pool: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    strict_yearly_month_validation: bool,
    use_deep_refinement: bool,
    optimizer_kwargs: dict[str, Any],
) -> PortfolioResult:
    """Run the existing UBS engine without applying its preliminary top-K cut."""
    exact_kwargs = optimizer_overrides(
        optimizer_kwargs,
        top_k_per_symbol=max(
            int(optimizer_kwargs["funnel"].top_k_per_symbol or 1), len(candidate_pool),
        ),
        max_total_candidates=None,
    )
    if strict_yearly_month_validation:
        return optimize_strict_monthly_portfolio(
            monthly_sets=candidate_pool,
            full_sets=full_sets,
            target_month=int(target_month),
            **{**exact_kwargs, "search": exact_kwargs["search"].with_deep_refinement(
                bool(use_deep_refinement)
            )},
        )
    return optimize_portfolio(
        raw_sets=candidate_pool,
        **{**exact_kwargs, "search": exact_kwargs["search"].with_deep_refinement(bool(use_deep_refinement))},
    )








def optimize_experimental_monthly_portfolio(
    *,
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    target_month: int,
    strict_yearly_month_validation: bool,
    use_deep_refinement: bool,
    progress: Progress | None = None,
    **optimizer_kwargs: Any,
) -> PortfolioResult:
    """Evaluate every eligible strategy with correlation-aware successive halving."""
    minimum_trades = int(optimizer_kwargs.get("min_trades_2020_2026") or 0)
    eligible = filter_eligible_sets(monthly_sets, minimum_trades)
    if not eligible:
        raise ValueError("No hay candidatos mensuales elegibles para la búsqueda experimental.")

    configured_size = max(int(optimizer_kwargs.get("max_total_candidates") or 30), 2)
    pool_size = min(configured_size, 40) if strict_yearly_month_validation else configured_size
    current = eligible
    evaluated_ids: set[str] = set()
    successful_pools = 0
    failed_pools = 0
    round_number = 0
    total_exposures = 0
    best_result: PortfolioResult | None = None
    best_result_pool: list[RobustStrategySet] = []
    correlation_cache: dict[tuple[str, str], CorrelationPair] = {}

    while len(current) > pool_size:
        round_number += 1
        appearances = {_strategy_id(strategy): 0 for strategy in current}
        selections = {_strategy_id(strategy): 0 for strategy in current}
        contributions = {_strategy_id(strategy): 0.0 for strategy in current}
        round_results: list[PortfolioResult] = []
        rotation_pools = [
            build_experimental_candidate_pools(
                current,
                pool_size=pool_size,
                min_trades_2020_2026=minimum_trades,
                rotation=rotation,
                correlation_cache=correlation_cache,
            )
            for rotation in range(EXPERIMENTAL_POOL_ROTATIONS)
        ]
        if progress:
            progress(
                "5/6 · Búsqueda experimental: "
                f"ronda {round_number}, {len(current)} candidatos, "
                f"{EXPERIMENTAL_POOL_ROTATIONS} rotaciones"
            )
        qualifying_kwargs = dict(optimizer_kwargs)
        qualifying_kwargs["search_restarts"] = min(
            int(qualifying_kwargs.get("search_restarts") or 0),
            1,
        )
        qualifying_kwargs["run_local_search"] = False
        for rotation, pools in enumerate(rotation_pools, 1):
            for pool_index, pool in enumerate(pools, 1):
                pool_ids = {_strategy_id(strategy) for strategy in pool}
                evaluated_ids.update(pool_ids)
                total_exposures += len(pool_ids)
                for set_id in pool_ids:
                    appearances[set_id] += 1
                try:
                    result = _optimize_exact_pool(
                        pool,
                        full_sets,
                        target_month=target_month,
                        strict_yearly_month_validation=strict_yearly_month_validation,
                        use_deep_refinement=False,
                        optimizer_kwargs=qualifying_kwargs,
                    )
                    successful_pools += 1
                    round_results.append(result)
                    if best_result is None or _result_rank(result) > _result_rank(best_result):
                        best_result = result
                        best_result_pool = pool
                    for allocation in result.allocations:
                        set_id = str(allocation.set_id)
                        if int(allocation.units) <= 0 or set_id not in selections:
                            continue
                        selections[set_id] += 1
                        contributions[set_id] += float(allocation.net_profit_contribution)
                except Exception:
                    failed_pools += 1
                if progress:
                    progress(
                        "5/6 · Búsqueda experimental: "
                        f"rotación {rotation}/{EXPERIMENTAL_POOL_ROTATIONS}, "
                        f"lote {pool_index}/{len(pools)}"
                    )

        pareto_ids = {
            set_id
            for result in _pareto_archive(round_results)
            for set_id in _active_allocation_ids(result)
        }
        base_order = _interleaved_candidate_order(current, minimum_trades)
        base_rank = {
            _strategy_id(strategy): len(base_order) - index
            for index, strategy in enumerate(base_order)
        }
        advancing_count = max(int(ceil(len(current) / 2)), min(pool_size, len(current)))
        advancing = sorted(
            current,
            key=lambda strategy: (
                selections[_strategy_id(strategy)]
                / max(appearances[_strategy_id(strategy)], 1),
                1 if _strategy_id(strategy) in pareto_ids else 0,
                contributions[_strategy_id(strategy)]
                / max(selections[_strategy_id(strategy)], 1),
                _consistency_key(strategy),
                base_rank[_strategy_id(strategy)],
            ),
            reverse=True,
        )[:advancing_count]
        if not advancing or len(advancing) >= len(current):
            break
        current = advancing

    evaluated_ids.update(_strategy_id(strategy) for strategy in current)
    final_result: PortfolioResult | None = None
    final_error: Exception | None = None
    try:
        if progress:
            progress(
                "5/6 · Búsqueda experimental: "
                f"final con {len(current)} candidatos y validación por años"
            )
        final_result = _optimize_exact_pool(
            current,
            full_sets,
            target_month=target_month,
            strict_yearly_month_validation=strict_yearly_month_validation,
            use_deep_refinement=use_deep_refinement,
            optimizer_kwargs=optimizer_kwargs,
        )
        successful_pools += 1
    except Exception as exc:
        failed_pools += 1
        final_error = exc

    if final_result is not None and (
        best_result is None or _result_rank(final_result) >= _result_rank(best_result)
    ):
        selected_result = final_result
        selected_pool = current
    elif best_result is not None:
        selected_result = best_result
        selected_pool = best_result_pool
    elif final_error is not None:
        raise ValueError(
            "La búsqueda mensual experimental no encontró ningún lote viable."
        ) from final_error
    else:
        raise ValueError("La búsqueda mensual experimental no produjo resultados.")

    loyo_audit = _leave_one_year_out_audit(
        selected_result,
        selected_pool,
        target_month=target_month,
        optimizer_kwargs=optimizer_kwargs,
        optimize_exact_pool=_optimize_exact_pool,
    )
    selected_result.seasonal_validation = dict(selected_result.seasonal_validation or {})
    selected_result.seasonal_validation["experimental_leave_one_year_out"] = loyo_audit

    missing = sorted({_strategy_id(strategy) for strategy in eligible} - evaluated_ids)
    selected_result.warnings.append(
        "Búsqueda mensual experimental: "
        f"{len(evaluated_ids)}/{len(eligible)} candidatos examinados; "
        f"{total_exposures} exposiciones en {EXPERIMENTAL_POOL_ROTATIONS} rotaciones; "
        f"{successful_pools} lotes viables, {failed_pools} no viables; "
        f"{round_number} ronda(s) de clasificación."
    )
    if loyo_audit.get("status") == "completed":
        selected_result.warnings.append(
            "Validación experimental dejando un año fuera: "
            f"{int(loyo_audit['positive_folds'])}/{len(loyo_audit['years'])} años positivos; "
            f"{int(loyo_audit['dd_passed_folds'])}/{len(loyo_audit['years'])} dentro de DD; "
            f"estabilidad de selección {float(loyo_audit['mean_selection_overlap']) * 100:.1f}%; "
            f"{'OK' if loyo_audit['passed'] else 'REVISAR'}."
        )
    else:
        selected_result.warnings.append(
            "Validación experimental dejando un año fuera no disponible: "
            "se necesitan al menos tres años con trades fechados."
        )
    if missing:
        selected_result.warnings.append(
            "Advertencia experimental: quedaron sin examinar "
            f"{len(missing)} candidato(s) por una interrupción del torneo."
        )
    return selected_result
