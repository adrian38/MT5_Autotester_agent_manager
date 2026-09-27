"""Torneo y selección final de la búsqueda UBS experimental completa."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    CorrelationPair,
    PortfolioResult,
    RobustStrategySet,
)

from . import portfolio_full_experimental as _full
from .portfolio_full_experimental import (
    EXPERIMENTAL_FULL_POOL_ROTATIONS,
    _active_allocation_ids,
    _interleaved_candidate_order,
    _pareto_archive,
    _result_rank,
    _segment_stability_audit,
    _segment_stability_key,
    _strategy_id,
    build_experimental_full_candidate_pools,
)


Progress = Callable[[str], None]


@dataclass
class _TournamentState:
    eligible: list[RobustStrategySet]
    current: list[RobustStrategySet]
    minimum_trades: int
    pool_size: int
    evaluated_ids: set[str] = field(default_factory=set)
    successful_pools: int = 0
    failed_pools: int = 0
    round_number: int = 0
    total_exposures: int = 0
    best_result: PortfolioResult | None = None
    best_result_pool: list[RobustStrategySet] = field(default_factory=list)
    correlation_cache: dict[tuple[str, str], CorrelationPair] = field(default_factory=dict)


@dataclass
class _RoundScore:
    appearances: dict[str, int]
    selections: dict[str, int]
    contributions: dict[str, float]
    results: list[PortfolioResult] = field(default_factory=list)


def _initial_state(
    raw_sets: list[RobustStrategySet], optimizer_kwargs: dict[str, Any]
) -> _TournamentState:
    minimum_trades = int(optimizer_kwargs.get("min_trades_2020_2026") or 0)
    eligible = _full.filter_eligible_sets(raw_sets, minimum_trades)
    if not eligible:
        raise ValueError("No hay candidatos UBS elegibles para la búsqueda experimental.")
    pool_size = max(int(optimizer_kwargs.get("max_total_candidates") or 30), 2)
    return _TournamentState(eligible, eligible, minimum_trades, pool_size)


def _new_round_score(current: list[RobustStrategySet]) -> _RoundScore:
    ids = [_strategy_id(strategy) for strategy in current]
    return _RoundScore(
        appearances={set_id: 0 for set_id in ids},
        selections={set_id: 0 for set_id in ids},
        contributions={set_id: 0.0 for set_id in ids},
    )


def _qualifying_pools(state: _TournamentState) -> list[list[list[RobustStrategySet]]]:
    return [
        build_experimental_full_candidate_pools(
            state.current,
            pool_size=state.pool_size,
            min_trades_2020_2026=state.minimum_trades,
            rotation=rotation,
            correlation_cache=state.correlation_cache,
        )
        for rotation in range(EXPERIMENTAL_FULL_POOL_ROTATIONS)
    ]


def _record_pool_result(
    state: _TournamentState,
    score: _RoundScore,
    pool: list[RobustStrategySet],
    result: PortfolioResult,
) -> None:
    state.successful_pools += 1
    score.results.append(result)
    if state.best_result is None or _result_rank(result) > _result_rank(state.best_result):
        state.best_result = result
        state.best_result_pool = pool
    for allocation in result.allocations:
        set_id = str(allocation.set_id)
        if int(allocation.units) <= 0 or set_id not in score.selections:
            continue
        score.selections[set_id] += 1
        score.contributions[set_id] += float(allocation.net_profit_contribution)


def _evaluate_qualifying_pool(
    state: _TournamentState,
    score: _RoundScore,
    pool: list[RobustStrategySet],
    qualifying_kwargs: dict[str, Any],
) -> None:
    pool_ids = {_strategy_id(strategy) for strategy in pool}
    state.evaluated_ids.update(pool_ids)
    state.total_exposures += len(pool_ids)
    for set_id in pool_ids:
        score.appearances[set_id] += 1
    try:
        result = _full._optimize_exact_pool(
            pool,
            use_deep_refinement=False,
            optimizer_kwargs=qualifying_kwargs,
        )
        _record_pool_result(state, score, pool, result)
    except Exception:
        state.failed_pools += 1


def _evaluate_round(
    state: _TournamentState,
    optimizer_kwargs: dict[str, Any],
    progress: Progress | None,
) -> _RoundScore:
    score = _new_round_score(state.current)
    rotation_pools = _qualifying_pools(state)
    if progress:
        progress(
            "Búsqueda experimental UBS: "
            f"ronda {state.round_number}, {len(state.current)} candidatos, "
            f"{EXPERIMENTAL_FULL_POOL_ROTATIONS} rotaciones"
        )
    qualifying_kwargs = dict(optimizer_kwargs)
    qualifying_kwargs["search_restarts"] = 0
    qualifying_kwargs["run_local_search"] = False
    for rotation, pools in enumerate(rotation_pools, 1):
        for pool_index, pool in enumerate(pools, 1):
            _evaluate_qualifying_pool(state, score, pool, qualifying_kwargs)
            if progress:
                progress(
                    "Búsqueda experimental UBS: "
                    f"rotación {rotation}/{EXPERIMENTAL_FULL_POOL_ROTATIONS}, "
                    f"lote {pool_index}/{len(pools)}"
                )
    return score


def _advancing_candidates(
    state: _TournamentState, score: _RoundScore
) -> list[RobustStrategySet]:
    pareto_ids = {
        set_id
        for result in _pareto_archive(score.results)
        for set_id in _active_allocation_ids(result)
    }
    base_order = _interleaved_candidate_order(state.current, state.minimum_trades)
    base_rank = {
        _strategy_id(strategy): len(base_order) - index
        for index, strategy in enumerate(base_order)
    }
    advancing_count = max(
        int(ceil(len(state.current) / 2)),
        min(state.pool_size, len(state.current)),
    )
    return sorted(
        state.current,
        key=lambda strategy: (
            score.selections[_strategy_id(strategy)]
            / max(score.appearances[_strategy_id(strategy)], 1),
            1 if _strategy_id(strategy) in pareto_ids else 0,
            score.contributions[_strategy_id(strategy)]
            / max(score.selections[_strategy_id(strategy)], 1),
            _segment_stability_key(strategy),
            base_rank[_strategy_id(strategy)],
        ),
        reverse=True,
    )[:advancing_count]


def _run_qualifying_tournament(
    state: _TournamentState,
    optimizer_kwargs: dict[str, Any],
    progress: Progress | None,
) -> None:
    while len(state.current) > state.pool_size:
        state.round_number += 1
        advancing = _advancing_candidates(
            state, _evaluate_round(state, optimizer_kwargs, progress)
        )
        if not advancing or len(advancing) >= len(state.current):
            break
        state.current = advancing


def _final_optimization(
    state: _TournamentState,
    use_deep_refinement: bool,
    optimizer_kwargs: dict[str, Any],
    progress: Progress | None,
) -> tuple[PortfolioResult | None, Exception | None]:
    state.evaluated_ids.update(_strategy_id(strategy) for strategy in state.current)
    try:
        if progress:
            progress(
                "Búsqueda experimental UBS: "
                f"final completa con {len(state.current)} candidatos"
            )
        result = _full._optimize_exact_pool(
            state.current,
            use_deep_refinement=use_deep_refinement,
            optimizer_kwargs=optimizer_kwargs,
        )
        state.successful_pools += 1
        return result, None
    except Exception as exc:
        state.failed_pools += 1
        return None, exc


def _refined_finalists(
    state: _TournamentState,
    final_result: PortfolioResult | None,
    recent_filler_ids: Callable[[PortfolioResult], set[str]] | None,
    optimizer_kwargs: dict[str, Any],
    progress: Progress | None,
) -> tuple[list[tuple[PortfolioResult, list[RobustStrategySet], set[str]]], list[str]]:
    finalists: list[tuple[PortfolioResult, list[RobustStrategySet], set[str]]] = []
    errors: list[str] = []
    for candidate_result, candidate_pool in (
        (final_result, state.current),
        (state.best_result, state.best_result_pool),
    ):
        if candidate_result is None:
            continue
        try:
            refined, dropped = _full._refined_without_recent_fillers(
                candidate_result,
                candidate_pool,
                recent_filler_ids,
                optimizer_kwargs=optimizer_kwargs,
                progress=progress,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        finalists.append((refined, list(candidate_pool), dropped))
    return finalists, errors


def _select_finalist(
    finalists: list[tuple[PortfolioResult, list[RobustStrategySet], set[str]]],
    refinement_errors: list[str],
    final_error: Exception | None,
) -> tuple[PortfolioResult, list[RobustStrategySet], set[str]]:
    if finalists:
        return max(finalists, key=lambda item: _result_rank(item[0]))
    if final_error is not None:
        raise ValueError(
            "La búsqueda UBS experimental no encontró ningún lote viable."
        ) from final_error
    detail = " | ".join(refinement_errors)
    message = "La búsqueda UBS experimental no produjo resultados sin rellenos 6M."
    if detail:
        message += " " + detail
    raise ValueError(message)


def _attach_stability(
    result: PortfolioResult, selected_pool: list[RobustStrategySet]
) -> dict[str, object]:
    stability = _segment_stability_audit(result, selected_pool)
    result.seasonal_validation = dict(result.seasonal_validation or {})
    result.seasonal_validation["experimental_full_history_stability"] = stability
    return stability


def _append_search_warnings(
    state: _TournamentState,
    result: PortfolioResult,
    removed_recent: set[str],
    stability: dict[str, object],
) -> None:
    missing = sorted({_strategy_id(item) for item in state.eligible} - state.evaluated_ids)
    result.warnings.append(
        "Búsqueda UBS experimental: "
        f"{len(state.evaluated_ids)}/{len(state.eligible)} candidatos examinados; "
        f"{state.total_exposures} exposiciones clasificatorias en "
        f"{EXPERIMENTAL_FULL_POOL_ROTATIONS} rotaciones; "
        f"{state.successful_pools} lotes viables, {state.failed_pools} no viables; "
        f"{state.round_number} ronda(s)."
    )
    if removed_recent:
        result.warnings.append(
            "Regla antirrelleno 6M en la búsqueda experimental: "
            f"{len(removed_recent)} relleno(s) sustituido(s) desde el lote ganador; "
            "la composición conserva su amplitud en vez de encogerse a los supervivientes."
        )
    if stability.get("status") == "completed":
        segments = stability["segments"]
        result.warnings.append(
            "Estabilidad UBS experimental IS/OOS/6M: "
            f"IS {float(segments['is_2020_2024']['positive_rate']) * 100:.1f}% positivas; "
            f"OOS {float(segments['oos_2025_2026']['positive_rate']) * 100:.1f}% positivas; "
            f"6M {float(segments['final_tick_6m']['positive_rate']) * 100:.1f}% positivas; "
            f"{'OK' if stability['passed'] else 'REVISAR'}."
        )
    if missing:
        result.warnings.append(
            "Advertencia experimental UBS: quedaron sin examinar "
            f"{len(missing)} candidato(s) por una interrupción del torneo."
        )


def optimize_experimental_full_portfolio(
    *,
    raw_sets: list[RobustStrategySet],
    use_deep_refinement: bool,
    progress: Progress | None = None,
    recent_filler_ids: Callable[[PortfolioResult], set[str]] | None = None,
    **optimizer_kwargs: Any,
) -> PortfolioResult:
    """Evaluate every eligible full-history strategy before fixing A/M/C sets."""
    state = _initial_state(raw_sets, optimizer_kwargs)
    _run_qualifying_tournament(state, optimizer_kwargs, progress)
    final_result, final_error = _final_optimization(
        state, use_deep_refinement, optimizer_kwargs, progress
    )
    finalists, refinement_errors = _refined_finalists(
        state, final_result, recent_filler_ids, optimizer_kwargs, progress
    )
    selected, selected_pool, removed_recent = _select_finalist(
        finalists, refinement_errors, final_error
    )
    stability = _attach_stability(selected, selected_pool)
    _append_search_warnings(state, selected, removed_recent, stability)
    return selected
