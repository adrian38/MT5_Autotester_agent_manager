"""Seleccion y reparacion de candidatos para el mensual estricto."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .symbols import portfolio_symbol_key
from .models import OptimizationDecision, PortfolioEvaluation, RobustStrategySet
from .selection import (
    filter_eligible_sets,
    score_set_for_portfolio,
    select_top_k_per_symbol,
    validate_strict_monthly_portfolio,
)
from .evaluation import evaluate_portfolio
from .limits import SearchLimits
from .greedy import _active_unit_allocations


def _strict_validation_for_allocations(
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    active_units = _active_unit_allocations(allocations)
    return validate_strict_monthly_portfolio(
        [full_by_id[set_id] for set_id in active_units if set_id in full_by_id],
        active_units,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        lookback_years=5,
        enforce_point_dd=enforce_point_dd,
    )


def _strict_monthly_candidate_validation(
    strategy: RobustStrategySet,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    return validate_strict_monthly_portfolio(
        [strategy],
        {strategy.set_id: 1},
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        lookback_years=5,
        enforce_point_dd=enforce_point_dd,
    )


def _strict_monthly_candidate_score(
    monthly_strategy: RobustStrategySet,
    full_strategy: RobustStrategySet,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
    enforce_point_dd: bool = True,
) -> float:
    validation = _strict_monthly_candidate_validation(
        full_strategy,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    target_net = float(validation.get("target_month_net") or 0.0)
    best_net = float(validation.get("best_month_net") or 0.0)
    best_month = int(validation.get("best_month") or 0)
    best_gap = max(best_net - target_net, 0.0) if best_month != target_month else 0.0
    yearly = validation.get("yearly") if isinstance(validation.get("yearly"), list) else []
    positive_years = 0
    dd_over = 0.0
    for item in yearly:
        if not isinstance(item, dict):
            continue
        net = float(item.get("net") or 0.0)
        if int(item.get("trades") or 0) > 0 and net > 0:
            positive_years += 1
        dd_over += max(float(item.get("valley_dd") or 0.0) - target_valley_dd, 0.0)
        if enforce_point_dd:
            dd_over += max(float(item.get("point_dd") or 0.0) - target_point_dd, 0.0)
    base_score = score_set_for_portfolio(monthly_strategy, min_trades_2020_2026)
    return (
        target_net * 4.0
        - best_gap * 6.0
        + positive_years * 10_000.0
        - dd_over * 25.0
        + base_score * 0.05
    )


def _limit_sorted_candidates_with_symbol_reserve(
    ordered: Sequence[RobustStrategySet],
    limit: int | None,
) -> list[RobustStrategySet]:
    unique: list[RobustStrategySet] = []
    seen_ids: set[str] = set()
    for strategy in ordered:
        if strategy.set_id in seen_ids:
            continue
        unique.append(strategy)
        seen_ids.add(strategy.set_id)
    if limit is None or limit <= 0 or len(unique) <= limit:
        return unique

    selected: list[RobustStrategySet] = []
    selected_ids: set[str] = set()
    by_symbol: dict[str, list[RobustStrategySet]] = {}
    for strategy in unique:
        by_symbol.setdefault(portfolio_symbol_key(strategy.symbol), []).append(strategy)
    for group in by_symbol.values():
        if len(selected) >= limit:
            break
        strategy = group[0]
        selected.append(strategy)
        selected_ids.add(strategy.set_id)
    for strategy in unique:
        if len(selected) >= limit:
            break
        if strategy.set_id in selected_ids:
            continue
        selected.append(strategy)
        selected_ids.add(strategy.set_id)
    return selected


def _monthly_orderings(
    eligible: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    limits: SearchLimits,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
) -> dict[str, list[RobustStrategySet]]:
    """Tres ordenes del mismo pool; se prueban por turno hasta que uno pasa."""
    validations = {
        strategy.set_id: _strict_monthly_candidate_validation(
            full_by_id[strategy.set_id],
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            enforce_point_dd=limits.enforce_point_dd,
        )
        for strategy in eligible
    }
    best_month = sorted(
        [
            strategy
            for strategy in eligible
            if int(validations[strategy.set_id].get("best_month") or 0) == target_month
            and float(validations[strategy.set_id].get("target_month_net") or 0.0) > 0
        ],
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )
    seasonal = sorted(
        eligible,
        key=lambda item: _strict_monthly_candidate_score(
            item,
            full_by_id[item.set_id],
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            min_trades_2020_2026=min_trades_2020_2026,
            enforce_point_dd=limits.enforce_point_dd,
        ),
        reverse=True,
    )
    target_net = sorted(
        eligible,
        key=lambda item: (
            float(validations[item.set_id].get("target_month_net") or 0.0),
            score_set_for_portfolio(item, min_trades_2020_2026),
        ),
        reverse=True,
    )
    return {
        "mejor_mes_individual": best_month,
        "estacionalidad": seasonal,
        "net_mes_objetivo": target_net,
    }


def _distinct_monthly_variants(
    sources: list[tuple[str, list[RobustStrategySet]]], strict_limit: int,
) -> list[tuple[str, list[RobustStrategySet]]]:
    """Recorta cada orden al limite y descarta los que dan el mismo pool."""
    variants: list[tuple[str, list[RobustStrategySet]]] = []
    seen_signatures: set[tuple[str, ...]] = set()
    for label, ordered in sources:
        limited = _limit_sorted_candidates_with_symbol_reserve(ordered, strict_limit)
        signature = tuple(strategy.set_id for strategy in limited)
        if not limited or signature in seen_signatures:
            continue
        variants.append((label, limited))
        seen_signatures.add(signature)
    return variants


def _strict_monthly_candidate_variants(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    limits: SearchLimits = SearchLimits(),
) -> list[tuple[str, list[RobustStrategySet]]]:
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    eligible = [
        strategy
        for strategy in filter_eligible_sets(monthly_sets, min_trades_2020_2026)
        if strategy.set_id in full_by_id
    ]
    if not eligible:
        return []

    symbol_count = len({portfolio_symbol_key(strategy.symbol) for strategy in eligible})
    configured_limit = max_total_candidates if max_total_candidates is not None else len(eligible)
    if configured_limit is None or configured_limit <= 0:
        configured_limit = len(eligible)
    strict_limit = min(len(eligible), max(symbol_count, min(int(configured_limit), 40)))

    normal = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=strict_limit,
        min_trades_2020_2026=min_trades_2020_2026,
    )

    orderings = _monthly_orderings(
        eligible, full_by_id, limits,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    best_month = orderings["mejor_mes_individual"]
    sources = [("mejor_mes_individual", best_month)] if best_month else []
    sources.append(("estacionalidad", orderings["estacionalidad"]))
    sources.append(("net_mes_objetivo", orderings["net_mes_objetivo"]))
    if not best_month:
        sources.append(("normal", normal))
    return _distinct_monthly_variants(sources, strict_limit)


def _strict_monthly_violation_score(validation: dict[str, object]) -> float:
    if bool(validation.get("passed")):
        return 0.0
    score = 0.0
    enforce_point_dd = bool(validation.get("enforce_point_dd", True))
    target_valley = float(validation.get("target_valley_dd") or 0.0)
    target_point = float(validation.get("target_point_dd") or 0.0)
    monthly_dd = validation.get("monthly_dd")
    if isinstance(monthly_dd, dict):
        for item in monthly_dd.values():
            if not isinstance(item, dict):
                continue
            score += max(float(item.get("valley_dd") or 0.0) - target_valley, 0.0) * 10.0
            if enforce_point_dd:
                score += max(float(item.get("point_dd") or 0.0) - target_point, 0.0) * 10.0
    yearly = validation.get("yearly")
    if isinstance(yearly, list):
        for item in yearly:
            if not isinstance(item, dict):
                continue
            if int(item.get("trades") or 0) <= 0:
                score += 1_000_000.0
            if float(item.get("net") or 0.0) <= 0.0:
                score += 1_000_000.0 + abs(float(item.get("net") or 0.0)) * 100.0
            score += max(float(item.get("valley_dd") or 0.0) - target_valley, 0.0) * 20.0
            if enforce_point_dd:
                score += max(float(item.get("point_dd") or 0.0) - target_point, 0.0) * 20.0
    best_month = int(validation.get("best_month") or 0)
    target_month = int(validation.get("target_month") or 0)
    if best_month != target_month:
        best_net = float(validation.get("best_month_net") or 0.0)
        target_net = float(validation.get("target_month_net") or 0.0)
        score += 1_000_000.0 + max(best_net - target_net, 0.0) * 100.0
    return score + len(validation.get("reasons") or []) * 1_000.0


@dataclass
class _MonthlyReduction:
    """Una reduccion candidata: cuanto acerca al 5A y que deja detras."""

    score: float
    reduced_set: RobustStrategySet
    allocations: dict[str, int]
    evaluation: PortfolioEvaluation
    validation: dict[str, object]
    #: Criterio de desempate, en el orden en que se compara.
    ranking: tuple[float, float, float, str]


def _best_monthly_reduction(
    monthly_sets: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    limits: SearchLimits,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
) -> _MonthlyReduction | None:
    """La unidad cuya retirada mas acerca la cartera a cumplir el 5A.

    A igualdad de incumplimiento, la que menos beneficio y menos diversificacion
    sacrifica; el id del set desempata para que el resultado sea reproducible.
    """
    best: _MonthlyReduction | None = None
    for strategy in monthly_sets:
        if allocations.get(strategy.set_id, 0) <= 0:
            continue
        trial_allocations = allocations.copy()
        trial_allocations[strategy.set_id] -= 1
        trial_eval = evaluate_portfolio(
            monthly_sets,
            trial_allocations,
            target_valley_dd,
            target_point_dd,
            limits.max_daily_dd,
            limits.enforce_point_dd,
            limits.daily_dd_full_history,
        )
        trial_validation = _strict_validation_for_allocations(
            full_by_id,
            trial_allocations,
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            enforce_point_dd=limits.enforce_point_dd,
        )
        trial_score = _strict_monthly_violation_score(trial_validation)
        ranking = (
            trial_score,
            -trial_eval.total_net_profit,
            -trial_eval.active_strategies,
            strategy.set_id,
        )
        if best is None or ranking < best.ranking:
            best = _MonthlyReduction(
                score=trial_score,
                reduced_set=strategy,
                allocations=trial_allocations,
                evaluation=trial_eval,
                validation=trial_validation,
                ranking=ranking,
            )
    return best


def _monthly_reduction_decision(
    reduction: _MonthlyReduction,
    previous_eval: PortfolioEvaluation,
    current_eval: PortfolioEvaluation,
    step: int,
) -> OptimizationDecision:
    """La linea del registro que explica la unidad retirada."""
    return OptimizationDecision(
        step=step,
        action="strict_monthly_reduce_unit",
        set_id=reduction.reduced_set.set_id,
        from_set_id=reduction.reduced_set.set_id,
        to_set_id=None,
        gain=-reduction.reduced_set.net_profit_2020_2026_001,
        valley_cost=current_eval.valley_dd - previous_eval.valley_dd,
        point_cost=current_eval.point_dd - previous_eval.point_dd,
        score=-float(reduction.score),
        portfolio_net_profit_after=current_eval.total_net_profit,
        portfolio_valley_dd_after=current_eval.valley_dd,
        portfolio_point_dd_after=current_eval.point_dd,
        reason="Reduccion necesaria para cumplir validacion mensual estricta 5A/DD",
    )


def _repair_allocations_to_strict_monthly(
    monthly_sets: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, dict[str, object], list[OptimizationDecision]]:
    current_allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in monthly_sets
    }
    current_eval = evaluate_portfolio(
        monthly_sets,
        current_allocations,
        target_valley_dd,
        target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    current_validation = _strict_validation_for_allocations(
        full_by_id,
        current_allocations,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        enforce_point_dd=limits.enforce_point_dd,
    )
    decision_log: list[OptimizationDecision] = []
    if bool(current_validation.get("passed")):
        return current_allocations, current_eval, current_validation, decision_log

    step = 0
    while sum(current_allocations.values()) > 0:
        current_score = _strict_monthly_violation_score(current_validation)
        reduction = _best_monthly_reduction(
            monthly_sets, full_by_id, current_allocations, limits,
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
        )
        if reduction is None or reduction.score >= current_score - 1e-9:
            break
        previous_eval = current_eval
        current_allocations = reduction.allocations
        current_eval = reduction.evaluation
        current_validation = reduction.validation
        step += 1
        decision_log.append(
            _monthly_reduction_decision(reduction, previous_eval, current_eval, step)
        )
        if bool(current_validation.get("passed")):
            break
    return current_allocations, current_eval, current_validation, decision_log
