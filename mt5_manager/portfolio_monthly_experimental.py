from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
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








@dataclass(frozen=True)
class _TournamentConfig:
    full_sets: list[RobustStrategySet]
    target_month: int
    strict_yearly_month_validation: bool
    use_deep_refinement: bool
    progress: Progress | None
    optimizer_kwargs: dict[str, Any]
    minimum_trades: int
    pool_size: int


@dataclass
class _TournamentState:
    eligible: list[RobustStrategySet]
    current: list[RobustStrategySet]
    evaluated_ids: set[str] = field(default_factory=set)
    successful_pools: int = 0
    failed_pools: int = 0
    round_number: int = 0
    total_exposures: int = 0
    best_result: PortfolioResult | None = None
    best_result_pool: list[RobustStrategySet] = field(default_factory=list)
    correlation_cache: dict[tuple[str, str], CorrelationPair] = field(default_factory=dict)


@dataclass
class _RoundScores:
    appearances: dict[str, int]
    selections: dict[str, int]
    contributions: dict[str, float]
    results: list[PortfolioResult] = field(default_factory=list)



def _round_scores(strategies: Sequence[RobustStrategySet]) -> _RoundScores:
    ids = [_strategy_id(strategy) for strategy in strategies]
    return _RoundScores(
        appearances={set_id: 0 for set_id in ids},
        selections={set_id: 0 for set_id in ids},
        contributions={set_id: 0.0 for set_id in ids},
    )



def _qualifying_kwargs(optimizer_kwargs: dict[str, Any]) -> dict[str, Any]:
    qualifying_kwargs = dict(optimizer_kwargs)
    qualifying_kwargs["search_restarts"] = min(
        int(qualifying_kwargs.get("search_restarts") or 0),
        1,
    )
    qualifying_kwargs["run_local_search"] = False
    return qualifying_kwargs



def _evaluate_qualifying_pool(
    state: _TournamentState,
    scores: _RoundScores,
    config: _TournamentConfig,
    pool: list[RobustStrategySet],
    qualifying_kwargs: dict[str, Any],
) -> None:
    pool_ids = {_strategy_id(strategy) for strategy in pool}
    state.evaluated_ids.update(pool_ids)
    state.total_exposures += len(pool_ids)
    for set_id in pool_ids:
        scores.appearances[set_id] += 1
    try:
        result = _optimize_exact_pool(
            pool,
            config.full_sets,
            target_month=config.target_month,
            strict_yearly_month_validation=config.strict_yearly_month_validation,
            use_deep_refinement=False,
            optimizer_kwargs=qualifying_kwargs,
        )
        state.successful_pools += 1
        scores.results.append(result)
        if state.best_result is None or _result_rank(result) > _result_rank(state.best_result):
            state.best_result = result
            state.best_result_pool = pool
        for allocation in result.allocations:
            set_id = str(allocation.set_id)
            if int(allocation.units) <= 0 or set_id not in scores.selections:
                continue
            scores.selections[set_id] += 1
            scores.contributions[set_id] += float(allocation.net_profit_contribution)
    except Exception:
        state.failed_pools += 1



def _evaluate_round_pools(
    state: _TournamentState,
    scores: _RoundScores,
    config: _TournamentConfig,
    rotation_pools: list[list[list[RobustStrategySet]]],
) -> None:
    qualifying_kwargs = _qualifying_kwargs(config.optimizer_kwargs)
    for rotation, pools in enumerate(rotation_pools, 1):
        for pool_index, pool in enumerate(pools, 1):
            _evaluate_qualifying_pool(state, scores, config, pool, qualifying_kwargs)
            if config.progress:
                config.progress(
                    "5/6 · Búsqueda experimental: "
                    f"rotación {rotation}/{EXPERIMENTAL_POOL_ROTATIONS}, "
                    f"lote {pool_index}/{len(pools)}"
                )



def _advancing_candidates(
    current: list[RobustStrategySet],
    scores: _RoundScores,
    minimum_trades: int,
    pool_size: int,
) -> list[RobustStrategySet]:
    pareto_ids = {
        set_id
        for result in _pareto_archive(scores.results)
        for set_id in _active_allocation_ids(result)
    }
    base_order = _interleaved_candidate_order(current, minimum_trades)
    base_rank = {
        _strategy_id(strategy): len(base_order) - index
        for index, strategy in enumerate(base_order)
    }
    advancing_count = max(int(ceil(len(current) / 2)), min(pool_size, len(current)))
    return sorted(
        current,
        key=lambda strategy: (
            scores.selections[_strategy_id(strategy)]
            / max(scores.appearances[_strategy_id(strategy)], 1),
            1 if _strategy_id(strategy) in pareto_ids else 0,
            scores.contributions[_strategy_id(strategy)]
            / max(scores.selections[_strategy_id(strategy)], 1),
            _consistency_key(strategy),
            base_rank[_strategy_id(strategy)],
        ),
        reverse=True,
    )[:advancing_count]



def _run_monthly_tournament(state: _TournamentState, config: _TournamentConfig) -> None:
    while len(state.current) > config.pool_size:
        state.round_number += 1
        scores = _round_scores(state.current)
        rotation_pools = [
            build_experimental_candidate_pools(
                state.current,
                pool_size=config.pool_size,
                min_trades_2020_2026=config.minimum_trades,
                rotation=rotation,
                correlation_cache=state.correlation_cache,
            )
            for rotation in range(EXPERIMENTAL_POOL_ROTATIONS)
        ]
        if config.progress:
            config.progress(
                "5/6 · Búsqueda experimental: "
                f"ronda {state.round_number}, {len(state.current)} candidatos, "
                f"{EXPERIMENTAL_POOL_ROTATIONS} rotaciones"
            )
        _evaluate_round_pools(state, scores, config, rotation_pools)
        advancing = _advancing_candidates(
            state.current, scores, config.minimum_trades, config.pool_size,
        )
        if not advancing or len(advancing) >= len(state.current):
            break
        state.current = advancing



def _final_monthly_result(
    state: _TournamentState,
    config: _TournamentConfig,
) -> tuple[PortfolioResult | None, Exception | None]:
    try:
        if config.progress:
            config.progress(
                "5/6 · Búsqueda experimental: "
                f"final con {len(state.current)} candidatos y validación por años"
            )
        result = _optimize_exact_pool(
            state.current,
            config.full_sets,
            target_month=config.target_month,
            strict_yearly_month_validation=config.strict_yearly_month_validation,
            use_deep_refinement=config.use_deep_refinement,
            optimizer_kwargs=config.optimizer_kwargs,
        )
        state.successful_pools += 1
        return result, None
    except Exception as exc:
        state.failed_pools += 1
        return None, exc



def _selected_monthly_result(
    state: _TournamentState,
    final_result: PortfolioResult | None,
    final_error: Exception | None,
) -> tuple[PortfolioResult, list[RobustStrategySet]]:
    if final_result is not None and (
        state.best_result is None or _result_rank(final_result) >= _result_rank(state.best_result)
    ):
        return final_result, state.current
    if state.best_result is not None:
        return state.best_result, state.best_result_pool
    if final_error is not None:
        raise ValueError(
            "La búsqueda mensual experimental no encontró ningún lote viable."
        ) from final_error
    raise ValueError("La búsqueda mensual experimental no produjo resultados.")



def _append_experimental_warnings(
    result: PortfolioResult,
    state: _TournamentState,
    loyo_audit: dict[str, object],
) -> None:
    missing = sorted({_strategy_id(strategy) for strategy in state.eligible} - state.evaluated_ids)
    result.warnings.append(
        "Búsqueda mensual experimental: "
        f"{len(state.evaluated_ids)}/{len(state.eligible)} candidatos examinados; "
        f"{state.total_exposures} exposiciones en {EXPERIMENTAL_POOL_ROTATIONS} rotaciones; "
        f"{state.successful_pools} lotes viables, {state.failed_pools} no viables; "
        f"{state.round_number} ronda(s) de clasificación."
    )
    if loyo_audit.get("status") == "completed":
        result.warnings.append(
            "Validación experimental dejando un año fuera: "
            f"{int(loyo_audit['positive_folds'])}/{len(loyo_audit['years'])} años positivos; "
            f"{int(loyo_audit['dd_passed_folds'])}/{len(loyo_audit['years'])} dentro de DD; "
            f"estabilidad de selección {float(loyo_audit['mean_selection_overlap']) * 100:.1f}%; "
            f"{'OK' if loyo_audit['passed'] else 'REVISAR'}."
        )
    else:
        result.warnings.append(
            "Validación experimental dejando un año fuera no disponible: "
            "se necesitan al menos tres años con trades fechados."
        )
    if missing:
        result.warnings.append(
            "Advertencia experimental: quedaron sin examinar "
            f"{len(missing)} candidato(s) por una interrupción del torneo."
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
    config = _TournamentConfig(
        full_sets, target_month, strict_yearly_month_validation, use_deep_refinement,
        progress, optimizer_kwargs, minimum_trades, pool_size,
    )
    state = _TournamentState(eligible=eligible, current=eligible)
    _run_monthly_tournament(state, config)
    state.evaluated_ids.update(_strategy_id(strategy) for strategy in state.current)
    final_result, final_error = _final_monthly_result(state, config)
    selected_result, selected_pool = _selected_monthly_result(state, final_result, final_error)
    loyo_audit = _leave_one_year_out_audit(
        selected_result,
        selected_pool,
        target_month=target_month,
        optimizer_kwargs=optimizer_kwargs,
        optimize_exact_pool=_optimize_exact_pool,
    )
    selected_result.seasonal_validation = dict(selected_result.seasonal_validation or {})
    selected_result.seasonal_validation["experimental_leave_one_year_out"] = loyo_audit
    _append_experimental_warnings(selected_result, state, loyo_audit)
    return selected_result
