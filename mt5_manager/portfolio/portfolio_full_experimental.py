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
EXPERIMENTAL_FULL_ANTIFILLER_RETRIES = 8


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
        **{**exact_kwargs, "search": exact_kwargs["search"].with_deep_refinement(bool(use_deep_refinement))},
    )


def _refill_without_fillers(
    pool: list[RobustStrategySet],
    removed: set[str],
    fillers: set[str],
    refinement_kwargs: dict[str, Any],
    progress: Progress | None,
) -> tuple[PortfolioResult, list[RobustStrategySet]]:
    """Reoptimiza el lote ganador menos los rellenos, y devuelve el lote nuevo."""
    candidates = [
        strategy for strategy in pool
        if _strategy_id(strategy) not in removed | fillers
    ]
    if len(candidates) >= len(pool):
        raise ValueError(
            "La regla antirrelleno experimental no redujo el lote ganador."
        )
    if not candidates:
        raise ValueError(
            "La regla antirrelleno experimental agotó el lote ganador."
        )
    if progress:
        progress(
            "Búsqueda experimental UBS: "
            f"{len(fillers)} relleno(s) 6M fuera; reoptimizando "
            f"{len(candidates)} candidato(s) del lote ganador"
        )
    try:
        refreshed = _optimize_exact_pool(
            candidates,
            use_deep_refinement=False,
            optimizer_kwargs=refinement_kwargs,
        )
    except Exception as exc:
        raise ValueError(
            "La búsqueda experimental no encontró una reposición viable "
            "para los rellenos 6M."
        ) from exc
    return refreshed, candidates


def _refined_without_recent_fillers(
    result: PortfolioResult,
    candidate_pool: Sequence[RobustStrategySet],
    recent_filler_ids: Callable[[PortfolioResult], set[str]] | None,
    *,
    optimizer_kwargs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[PortfolioResult, set[str]]:
    """Drop recent fillers while the freed risk budget can still be refilled.

    The caller-level rule refines only surviving allocations, so a broad
    composition that loses half its members can never use the released DD
    budget again: it ships smaller than the pool allows. Here the pool that
    produced the composition is known, so each removal is replaced from it and
    breadth survives the rule instead of being traded for it.

    The rule itself is not reimplemented and not folded into `_result_rank`:
    ranking by recent contribution share would bias the tournament towards
    concentrated compositions and defeat the diversification it exists for.
    Each retry reoptimises a single, strictly smaller pool, never the tournament.
    Replacement passes deliberately skip multi-start and deep refinement: the
    locked A/M/C variants perform the expensive final optimisation afterwards.
    A fixed retry budget keeps the experimental route predictably bounded; an
    unresolved finalist is rejected instead of being returned with fillers.
    """
    if recent_filler_ids is None:
        return result, set()
    pool = list(candidate_pool)
    removed: set[str] = set()
    current = result
    refinement_kwargs = dict(optimizer_kwargs)
    refinement_kwargs["search_restarts"] = 0
    refinement_kwargs["run_local_search"] = False
    for _attempt in range(min(len(pool), EXPERIMENTAL_FULL_ANTIFILLER_RETRIES)):
        fillers = set(recent_filler_ids(current))
        if not fillers:
            return current, removed
        current, pool = _refill_without_fillers(
            pool, removed, fillers, refinement_kwargs, progress
        )
        removed |= fillers
    if set(recent_filler_ids(current)):
        raise ValueError(
            "La búsqueda experimental agotó su lote sin eliminar todos los "
            "rellenos 6M."
        )
    return current, removed


def _active_stability_rows(
    result: PortfolioResult, candidate_pool: Sequence[RobustStrategySet]
) -> tuple[dict[str, RobustStrategySet], list[Any]]:
    by_id = {
        _strategy_id(strategy): strategy for strategy in candidate_pool
    }
    active = [
        allocation
        for allocation in result.allocations
        if int(allocation.units) > 0
        and str(allocation.set_id) in by_id
    ]
    return by_id, active


def _period_stability_metrics(
    active: list[Any],
    by_id: dict[str, RobustStrategySet],
    attribute: str,
) -> dict[str, object]:
    rows = [
        (by_id[str(allocation.set_id)], int(allocation.units))
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


def _recent_stability_metrics(
    active: list[Any], by_id: dict[str, RobustStrategySet]
) -> tuple[dict[str, object], bool]:
    rows = [
        (by_id[str(allocation.set_id)], int(allocation.units))
        for allocation in active
        if by_id[str(allocation.set_id)].has_recent_performance
    ]
    positive = sum(
        float(strategy.recent_net_profit_001) > 0
        for strategy, _units in rows
    )
    net = sum(
        float(strategy.recent_net_profit_001) * units
        for strategy, units in rows
    )
    dd = sum(
        max(float(strategy.recent_equity_dd_001), 0.0) * units
        for strategy, units in rows
    )
    return {
        "net_profit_001": net,
        "equity_dd_001": dd,
        "recovery_ratio": net / max(dd, 1.0),
        "positive_strategies": positive,
        "strategy_count": len(rows),
        "positive_rate": positive / len(rows) if rows else 0.0,
        "coverage_rate": len(rows) / len(active),
    }, bool(rows)


def _stability_passed(
    in_sample: dict[str, object],
    out_of_sample: dict[str, object],
    recent: dict[str, object],
    has_recent: bool,
    annualized_ratio: float,
) -> bool:
    recent_passed = not has_recent or (
        float(recent["net_profit_001"]) > 0
        and float(recent["positive_rate"]) >= 0.5
    )
    return (
        float(in_sample["net_profit_001"]) > 0
        and float(out_of_sample["net_profit_001"]) > 0
        and float(in_sample["positive_rate"]) >= 0.6
        and float(out_of_sample["positive_rate"]) >= 0.6
        and 0.2 <= annualized_ratio <= 5.0
        and recent_passed
    )


def _segment_stability_audit(
    result: PortfolioResult,
    candidate_pool: Sequence[RobustStrategySet],
) -> dict[str, object]:
    by_id, active = _active_stability_rows(result, candidate_pool)
    if not active:
        return {
            "status": "no_active_allocations",
            "passed": False,
            "segments": {},
        }

    in_sample = _period_stability_metrics(active, by_id, "report_2020_2024")
    out_of_sample = _period_stability_metrics(active, by_id, "report_2025_2026")
    recent, has_recent = _recent_stability_metrics(active, by_id)
    in_annual = float(in_sample["annualized_net_profit_001"])
    out_annual = float(out_of_sample["annualized_net_profit_001"])
    annualized_ratio = (
        out_annual / in_annual
        if in_annual > 0
        else 0.0
    )
    passed = _stability_passed(
        in_sample, out_of_sample, recent, has_recent, annualized_ratio
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


from .portfolio_full_experimental_search import optimize_experimental_full_portfolio
