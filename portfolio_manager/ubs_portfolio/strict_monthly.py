"""Busqueda mensual estricta con la auditoria estacional 5A."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Sequence

from .symbols import portfolio_symbol_key
from .models import (
    OptimizationDecision,
    PortfolioEvaluation,
    PortfolioGroupLimits,
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
    group_limits_for_portfolio_type,
)
from .selection import (
    filter_eligible_sets,
    score_set_for_portfolio,
    select_top_k_per_symbol,
    validate_strict_monthly_portfolio,
)
from .evaluation import (
    _evaluation_violates_dd_limits,
    evaluate_portfolio,
)
from .limits import CandidateFunnel, SearchLimits, SearchPlan
from .margin import MarginModel
from .constraints import (
    _allocations_respect_constraints,
    _portfolio_active_count,
    _portfolio_corr_allowed,
    _target_group_units_pct_allowed,
    can_add_unit,
    violates_correlation_limits,
)
from .greedy import (
    _DeepScan,
    _active_unit_allocations,
    _caps_allow,
    _swap_respects_caps,
    _swap_respects_correlation,
)
from .optimize import optimize_portfolio


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


def _monthly_evaluate(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
) -> PortfolioEvaluation:
    """La cartera mensual medida con los limites de la base."""
    return evaluate_portfolio(
        sets,
        allocations,
        current.target_valley_dd,
        current.target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )


def _monthly_gain(
    temp: PortfolioEvaluation,
    temp_allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
) -> float | None:
    """Cuanto gana el movimiento, o ``None`` si no supera la auditoria 5A.

    Lo que distingue al mensual del optimizador normal: una mejora que rompa la
    validacion estacional no vale, por mucho beneficio que traiga.
    """
    validation = _strict_validation_for_allocations(
        full_by_id,
        temp_allocations,
        target_month=target_month,
        target_valley_dd=current.target_valley_dd,
        target_point_dd=current.target_point_dd,
        enforce_point_dd=limits.enforce_point_dd,
    )
    if not bool(validation.get("passed")):
        return None
    gain = temp.total_net_profit - current.total_net_profit
    return gain if gain > 1e-9 else None


def _ordered_monthly_targets(
    sets: list[RobustStrategySet], allocations: dict[str, int],
) -> list[RobustStrategySet]:
    """Candidatas por puntuacion: se prueba primero la que mas promete."""
    return sorted(
        sets,
        key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
        reverse=True,
    )


def _monthly_add_candidate(
    target: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
) -> tuple[dict[str, int], PortfolioEvaluation, float] | None:
    """Una unidad mas en la candidata, si sobrevive a todos los filtros."""
    if not _caps_allow(sets, target, allocations, limits):
        return None
    if allocations.get(target.set_id, 0) <= 0:
        rejected_by_corr, _reason = violates_correlation_limits(
            target,
            sets,
            allocations,
            limits.max_pair_corr,
            limits.max_downside_corr,
            limits.max_dd_overlap,
        )
        if rejected_by_corr:
            return None
    temp_allocations = allocations.copy()
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    temp = _monthly_evaluate(sets, temp_allocations, current, limits)
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allowed(temp, limits.existing_portfolio_curves, limits.max_portfolio_corr):
        return None
    gain = _monthly_gain(temp, temp_allocations, current, limits, full_by_id, target_month)
    if gain is None:
        return None
    return temp_allocations, temp, gain


def _monthly_swap_candidate(
    source: RobustStrategySet,
    target: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
    minimum_active_strategies: int,
) -> tuple[dict[str, int], PortfolioEvaluation, float] | None:
    """Mover una unidad de una activa a la candidata, si todo lo permite."""
    temp_allocations = allocations.copy()
    temp_allocations[source.set_id] -= 1
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    if _portfolio_active_count(temp_allocations) < minimum_active_strategies:
        return None
    if not _swap_respects_caps(sets, target, temp_allocations, limits, None):
        return None
    if not _swap_respects_correlation(sets, target, allocations, temp_allocations, limits):
        return None
    temp = _monthly_evaluate(sets, temp_allocations, current, limits)
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allowed(temp, limits.existing_portfolio_curves, limits.max_portfolio_corr):
        return None
    gain = _monthly_gain(temp, temp_allocations, current, limits, full_by_id, target_month)
    if gain is None:
        return None
    return temp_allocations, temp, gain


def _best_monthly_refill(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
) -> _DeepScan:
    """La unidad que mas gana sin romper nada; solo anade, nunca quita."""
    scan = _DeepScan()
    for target in _ordered_monthly_targets(sets, allocations):
        scan.attempts += 1
        found = _monthly_add_candidate(
            target, sets, allocations, current, limits, full_by_id, target_month,
        )
        if found is None:
            continue
        temp_allocations, temp, gain = found
        scan.offer({
            "target": target,
            "allocations": temp_allocations,
            "evaluation": temp,
            "gain": gain,
        })
    return scan


def _best_monthly_deep_move(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    full_by_id: dict[str, RobustStrategySet],
    target_month: int,
    minimum_active_strategies: int,
) -> _DeepScan:
    """El mejor movimiento profundo: anadir o intercambiar, siempre con 5A."""
    scan = _DeepScan()
    for target in _ordered_monthly_targets(sets, allocations):
        scan.attempts += 1
        added = _monthly_add_candidate(
            target, sets, allocations, current, limits, full_by_id, target_month,
        )
        if added is not None:
            temp_allocations, temp, gain = added
            scan.offer({
                "action": "deep_add_unit", "from_set": None, "to_set": target,
                "allocations": temp_allocations, "evaluation": temp, "gain": gain,
            })
        active_sources = [source for source in sets if allocations.get(source.set_id, 0) > 0]
        for source in active_sources:
            if source.set_id == target.set_id:
                continue
            scan.attempts += 1
            swapped = _monthly_swap_candidate(
                source, target, sets, allocations, current, limits, full_by_id,
                target_month, minimum_active_strategies,
            )
            if swapped is None:
                continue
            temp_allocations, temp, gain = swapped
            scan.offer({
                "action": "deep_swap_unit", "from_set": source, "to_set": target,
                "allocations": temp_allocations, "evaluation": temp, "gain": gain,
            })
    return scan


def _monthly_refill_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    iteration: int,
) -> OptimizationDecision:
    """La linea del registro que explica la unidad anadida por relleno seguro."""
    target = best_move["target"]
    assert isinstance(target, RobustStrategySet)
    return OptimizationDecision(
        step=iteration,
        action="strict_monthly_safe_add_unit",
        set_id=target.set_id,
        from_set_id=None,
        to_set_id=target.set_id,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=float(best_move["gain"]),
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason="Relleno seguro: unidad anadida sin romper DD, margen, correlacion ni 5A",
    )


def _monthly_deep_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    iteration: int,
) -> OptimizationDecision:
    """La linea del registro que explica el movimiento profundo mensual."""
    from_set = best_move["from_set"]
    to_set = best_move["to_set"]
    assert to_set is not None and isinstance(to_set, RobustStrategySet)
    return OptimizationDecision(
        step=iteration,
        action=str(best_move["action"]),
        set_id=to_set.set_id,
        from_set_id=from_set.set_id if isinstance(from_set, RobustStrategySet) else None,
        to_set_id=to_set.set_id,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=float(best_move["gain"]),
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason="Optimizacion profunda: movimiento validado contra DD, margen, correlacion y 5A",
    )


def _strict_monthly_safe_refill_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
    max_iterations: int = 160,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0
    for iteration in range(1, max_iterations + 1):
        scan = _best_monthly_refill(
            sets, allocations, current, limits, full_by_id, target_month,
        )
        attempts += scan.attempts
        if scan.best is None:
            break
        previous = current
        allocations = scan.best["allocations"]  # type: ignore[assignment]
        current = scan.best["evaluation"]  # type: ignore[assignment]
        decision_log.append(_monthly_refill_decision(scan.best, previous, current, iteration))
    return allocations, current, decision_log, attempts


def _strict_monthly_deep_refine_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
    minimum_active_strategies: int,
    max_iterations: int = 120,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0
    for iteration in range(1, max_iterations + 1):
        scan = _best_monthly_deep_move(
            sets, allocations, current, limits, full_by_id, target_month,
            minimum_active_strategies,
        )
        attempts += scan.attempts
        if scan.best is None:
            break
        previous = current
        allocations = scan.best["allocations"]  # type: ignore[assignment]
        current = scan.best["evaluation"]  # type: ignore[assignment]
        decision_log.append(_monthly_deep_decision(scan.best, previous, current, iteration))
    return allocations, current, decision_log, attempts


@dataclass(frozen=True)
class _MonthlyOptimizerArgs:
    """Lo que las cuatro llamadas a ``optimize_portfolio`` del mensual comparten.

    Las tres reconstrucciones -reparacion, relleno seguro y refinamiento- solo
    se diferencian del caso base en que fijan la asignacion; todo lo demas
    viajaba copiado tres veces.
    """

    capital: float
    valley_dd_pct: float
    point_dd_pct: float
    portfolio_type: PortfolioType
    min_trades_2020_2026: int
    dd_reserve_pct: float
    limits: SearchLimits

    def base_kwargs(self) -> dict[str, object]:
        """Lo que toda llamada del mensual comparte al nivel de la funcion."""
        return {
            "capital": self.capital,
            "valley_dd_pct": self.valley_dd_pct,
            "point_dd_pct": self.point_dd_pct,
            "portfolio_type": self.portfolio_type,
        }


def _strict_monthly_limits(
    args: _MonthlyOptimizerArgs,
    group_limits: PortfolioGroupLimits,
    max_total_units: int | None,
) -> SearchLimits:
    """Topes comunes del relleno seguro y del refinamiento profundo."""
    return SearchLimits(
        max_units_per_set=args.limits.max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=args.limits.max_units_per_symbol,
        max_sets_per_symbol=args.limits.max_sets_per_symbol,
        max_sets_per_group=group_limits.max_sets,
        max_units_per_group_pct=group_limits.max_units_pct,
        group_unit_cap_bootstrap=group_limits.bootstrap_units,
        max_pair_corr=args.limits.max_pair_corr,
        max_downside_corr=args.limits.max_downside_corr,
        max_dd_overlap=args.limits.max_dd_overlap,
        existing_portfolio_curves=args.limits.existing_portfolio_curves,
        max_portfolio_corr=args.limits.max_portfolio_corr,
        margin_balance=args.limits.margin_balance,
        max_margin_pct=args.limits.max_margin_pct,
        margin_profile=args.limits.margin_profile,
        stock_leverage=args.limits.stock_leverage,
        default_leverage=args.limits.default_leverage,
        stock_contract_size=args.limits.stock_contract_size,
        default_contract_size=args.limits.default_contract_size,
        max_daily_dd=args.limits.max_daily_dd,
        enforce_point_dd=args.limits.enforce_point_dd,
        daily_dd_full_history=args.limits.daily_dd_full_history,
    )


def _active_units_of(result: PortfolioResult) -> dict[str, int]:
    """Unidades por set del resultado, sin las asignaciones a cero."""
    return {
        allocation.set_id: allocation.units
        for allocation in result.allocations
        if allocation.units > 0
    }


def _drop_preserved_warning(result: PortfolioResult) -> None:
    """Quita el aviso de 'se preservo el portafolio': aqui no informa de nada.

    Lo emite ``optimize_portfolio`` porque se le fija la asignacion, pero en el
    mensual fijarla es el mecanismo de reconstruccion, no una decision.
    """
    result.warnings = [
        warning for warning in result.warnings
        if not warning.startswith("Existing portfolio strategies and units were preserved")
    ]


def _monthly_base_evaluation(
    pool: list[RobustStrategySet],
    units: dict[str, int],
    base_result: PortfolioResult,
    args: _MonthlyOptimizerArgs,
) -> PortfolioEvaluation:
    """Evaluacion de la base sobre el pool ampliado, punto de partida comun."""
    return evaluate_portfolio(
        pool,
        units,
        base_result.target_valley_dd,
        base_result.target_point_dd,
        args.limits.max_daily_dd,
        args.limits.enforce_point_dd,
        args.limits.daily_dd_full_history,
    )


def _reoptimize_locked_monthly(
    sets: list[RobustStrategySet],
    locked: dict[str, int],
    args: _MonthlyOptimizerArgs,
) -> PortfolioResult:
    """Reconstruye el resultado sobre una asignacion ya decidida.

    No vuelve a buscar: fija las unidades y deja que el motor recalcule DD,
    margen, lotes y curva. Es la forma que comparten la reparacion estricta, el
    relleno seguro y el refinamiento profundo.
    """
    return optimize_portfolio(
        raw_sets=sets,
        limits=replace(args.limits, max_total_units=sum(locked.values())),
        funnel=CandidateFunnel(
            min_trades_2020_2026=args.min_trades_2020_2026,
            top_k_per_symbol=max(1, len(sets)),
            max_total_candidates=None,
            required_initial_allocations=locked,
            preserve_required_allocations=True,
        ),
        search=SearchPlan(run_local_search=False, dd_reserve_pct=args.dd_reserve_pct),
        **args.base_kwargs(),
    )


def _strict_monthly_variant_result(
    pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    top_k_per_symbol: int,
    max_total_units: int | None,
    run_local_search: bool,
    search_restarts: int,
) -> tuple[PortfolioResult | None, str]:
    """Optimiza una variante, la repara al 5A y devuelve el motivo si falla."""
    try:
        base_result = optimize_portfolio(
            raw_sets=pool,
            limits=replace(args.limits, max_total_units=max_total_units),
            funnel=CandidateFunnel(
                min_trades_2020_2026=args.min_trades_2020_2026,
                top_k_per_symbol=max(top_k_per_symbol, len(pool)),
                max_total_candidates=None,
            ),
            search=SearchPlan(
                run_local_search=run_local_search,
                search_restarts=int(search_restarts),
                dd_reserve_pct=args.dd_reserve_pct,
            ),
            **args.base_kwargs(),
        )
    except Exception as exc:
        return None, str(exc)
    base_units = _active_units_of(base_result)
    repaired_units, _repaired_eval, validation, repair_log = _repair_allocations_to_strict_monthly(
        pool,
        full_by_id,
        base_units,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        limits=SearchLimits(
            max_daily_dd=args.limits.max_daily_dd,
            enforce_point_dd=args.limits.enforce_point_dd,
            daily_dd_full_history=args.limits.daily_dd_full_history,
        ),
    )
    if not bool(validation.get("passed")):
        reasons = validation.get("reasons") or []
        return None, "; ".join(str(item) for item in list(reasons)[:3])
    active_repaired = _active_unit_allocations(repaired_units)
    if not active_repaired:
        return None, "reparacion estricta dejo el portafolio sin estrategias"
    if active_repaired != base_units:
        base_result, error = _rebuild_after_strict_repair(
            pool, active_repaired, repair_log, args,
        )
        if base_result is None:
            return None, error
    base_result.seasonal_validation = validation
    return base_result, ""


def _rebuild_after_strict_repair(
    pool: list[RobustStrategySet],
    active_repaired: dict[str, int],
    repair_log: list[OptimizationDecision],
    args: _MonthlyOptimizerArgs,
) -> tuple[PortfolioResult | None, str]:
    """Reconstruye el resultado cuando la reparacion cambio las unidades."""
    repaired_sets = [
        strategy for strategy in pool if strategy.set_id in active_repaired
    ]
    try:
        result = _reoptimize_locked_monthly(repaired_sets, active_repaired, args)
    except Exception as exc:
        return None, f"reparacion estricta no pudo reconstruirse: {exc}"
    _drop_preserved_warning(result)
    result.decision_log.extend(repair_log)
    result.warnings.append(
        "Reparacion estricta mensual: se redujeron "
        f"{len(repair_log)} unidad(es) para cumplir DD de todos los meses y mejor mes 5A."
    )
    return result, ""


def _select_strict_monthly_base(
    variants: Sequence[tuple[str, list[RobustStrategySet]]],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    **variant_kwargs: object,
) -> tuple[PortfolioResult, str]:
    """Primera variante que supera la auditoria estricta, o error con motivos."""
    errors: list[str] = []
    for label, pool in variants:
        result, error = _strict_monthly_variant_result(
            pool, full_by_id, args, **variant_kwargs,
        )
        if result is not None:
            return result, label
        errors.append(f"{label}: {error}")
    detail = " | ".join(errors[:6])
    raise ValueError(
        "Ninguna variante de busqueda estricta mensual fue viable."
        + (f" {detail}" if detail else "")
    )


def _monthly_candidate_pool(
    variants: Sequence[tuple[str, list[RobustStrategySet]]],
    monthly_by_id: dict[str, RobustStrategySet],
    base_result: PortfolioResult,
) -> list[RobustStrategySet]:
    """Union de todas las variantes, con la version mensual de lo ya asignado."""
    pool_by_id: dict[str, RobustStrategySet] = {}
    for _label, variant_pool in variants:
        for strategy in variant_pool:
            pool_by_id[strategy.set_id] = strategy
    for allocation in base_result.allocations:
        strategy = monthly_by_id.get(allocation.set_id)
        if strategy is not None:
            pool_by_id[allocation.set_id] = strategy
    return list(pool_by_id.values())


def _monthly_safe_refill(
    base_result: PortfolioResult,
    base_label: str,
    candidate_pool: list[RobustStrategySet],
    monthly_by_id: dict[str, RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    max_total_units: int | None,
) -> PortfolioResult:
    """Anade unidades sin romper DD, margen, correlacion ni la auditoria 5A."""
    group_limits = group_limits_for_portfolio_type(args.portfolio_type)
    base_units = _active_units_of(base_result)
    safe_allocations, safe_eval, safe_log, attempts = _strict_monthly_safe_refill_allocations(
        candidate_pool,
        full_by_id,
        base_units,
        _monthly_base_evaluation(candidate_pool, base_units, base_result, args),
        target_month=month,
        limits=_strict_monthly_limits(args, group_limits, max_total_units),
    )
    active_safe = _active_unit_allocations(safe_allocations)
    validation = _strict_validation_for_allocations(
        full_by_id,
        active_safe,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        enforce_point_dd=args.limits.enforce_point_dd,
    )
    improves = safe_eval.total_net_profit > base_result.total_net_profit + 1e-9
    if not (improves and bool(validation.get("passed"))):
        base_result.warnings.append(
            "Relleno seguro mensual: no encontro unidades adicionales validas "
            f"sin romper DD/margen/correlacion/5A ({attempts} intentos evaluados)."
        )
        base_result.warnings.append(
            f"Generacion estricta mensual OK sin optimizacion profunda; base '{base_label}'."
        )
        return base_result
    safe_sets = [
        monthly_by_id[set_id] for set_id in active_safe if set_id in monthly_by_id
    ]
    safe_result = _reoptimize_locked_monthly(safe_sets, active_safe, args)
    safe_result.seasonal_validation = validation
    _drop_preserved_warning(safe_result)
    safe_result.decision_log.extend(base_result.decision_log)
    safe_result.decision_log.extend(safe_log)
    safe_result.warnings.extend(base_result.warnings)
    safe_result.warnings.append(
        "Relleno seguro mensual aplicado sin optimizacion profunda: "
        f"net {base_result.total_net_profit:,.2f} -> {safe_result.total_net_profit:,.2f}; "
        f"unidades {base_result.total_units} -> {safe_result.total_units}; "
        f"base '{base_label}', {attempts} intentos evaluados."
    )
    return safe_result


def _monthly_deep_refine(
    base_result: PortfolioResult,
    base_label: str,
    candidate_pool: list[RobustStrategySet],
    monthly_by_id: dict[str, RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    max_total_units: int | None,
) -> PortfolioResult:
    """Busqueda profunda sobre la base estricta; solo entra si supera la 5A."""
    group_limits = group_limits_for_portfolio_type(args.portfolio_type)
    base_units = _active_units_of(base_result)
    refined_allocations, refined_eval, refinement_log, attempts = _strict_monthly_deep_refine_allocations(
        candidate_pool,
        full_by_id,
        base_units,
        _monthly_base_evaluation(candidate_pool, base_units, base_result, args),
        target_month=month,
        minimum_active_strategies=base_result.active_strategies,
        limits=_strict_monthly_limits(args, group_limits, max_total_units),
    )
    if refined_eval.total_net_profit <= base_result.total_net_profit + 1e-9:
        base_result.warnings.append(
            "Optimizacion profunda: no encontro mejora valida sobre la base estricta "
            f"({attempts} movimientos evaluados)."
        )
        return base_result
    active_refined = _active_unit_allocations(refined_allocations)
    validation = _strict_validation_for_allocations(
        full_by_id,
        active_refined,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        enforce_point_dd=args.limits.enforce_point_dd,
    )
    lost_diversification = (
        _portfolio_active_count(active_refined) < base_result.active_strategies
    )
    if lost_diversification or not bool(validation.get("passed")):
        base_result.warnings.append(
            "Optimizacion profunda: mejora descartada por diversificacion o validacion 5A."
        )
        return base_result
    refined_sets = [
        monthly_by_id[set_id] for set_id in active_refined if set_id in monthly_by_id
    ]
    refined_result = _reoptimize_locked_monthly(refined_sets, active_refined, args)
    refined_result.seasonal_validation = validation
    _drop_preserved_warning(refined_result)
    refined_result.decision_log.extend(refinement_log)
    refined_result.warnings.append(
        "Optimizacion profunda aplicada: "
        f"net {base_result.total_net_profit:,.2f} -> {refined_result.total_net_profit:,.2f}; "
        f"estrategias {base_result.active_strategies} -> {refined_result.active_strategies}; "
        f"base '{base_label}', {attempts} movimientos evaluados."
    )
    return refined_result


def _strict_monthly_search(
    variants: Sequence[tuple[str, list[RobustStrategySet]]],
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    args: _MonthlyOptimizerArgs,
    *,
    month: int,
    top_k_per_symbol: int,
    run_local_search: bool,
    search_restarts: int,
    use_deep_refinement: bool,
) -> PortfolioResult:
    """Elige la base estricta y la mejora por la rama que pida el llamante."""
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    base_result, base_label = _select_strict_monthly_base(
        variants,
        full_by_id,
        args,
        month=month,
        top_k_per_symbol=top_k_per_symbol,
        max_total_units=args.limits.max_total_units,
        run_local_search=run_local_search,
        search_restarts=search_restarts,
    )
    monthly_by_id = {strategy.set_id: strategy for strategy in monthly_sets}
    branch = _monthly_deep_refine if use_deep_refinement else _monthly_safe_refill
    return branch(
        base_result,
        base_label,
        _monthly_candidate_pool(variants, monthly_by_id, base_result),
        monthly_by_id,
        full_by_id,
        args,
        month=month,
        max_total_units=args.limits.max_total_units,
    )


def optimize_strict_monthly_portfolio(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    limits: SearchLimits = SearchLimits(),
    funnel: CandidateFunnel = CandidateFunnel(),
    search: SearchPlan = SearchPlan(),
) -> PortfolioResult:
    """Cartera mensual con la prueba estacional de cinco anos dentro del bucle.

    Solo conserva los pools que superan la auditoria ano a ano y de mejor mes.
    """
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")
    reserve_factor = 1.0 - min(max(float(search.dd_reserve_pct), 0.0), 99.0) / 100.0
    variants = _strict_monthly_candidate_variants(
        monthly_sets,
        full_sets,
        target_month=month,
        target_valley_dd=float(capital) * float(valley_dd_pct) * reserve_factor / 100.0,
        target_point_dd=float(capital) * float(point_dd_pct) * reserve_factor / 100.0,
        min_trades_2020_2026=funnel.min_trades_2020_2026,
        top_k_per_symbol=funnel.top_k_per_symbol,
        max_total_candidates=funnel.max_total_candidates,
        limits=limits,
    )
    if not variants:
        raise ValueError("No hay candidatos mensuales elegibles para la busqueda estricta.")
    return _strict_monthly_search(
        variants,
        monthly_sets,
        full_sets,
        _MonthlyOptimizerArgs(
            capital=capital,
            valley_dd_pct=valley_dd_pct,
            point_dd_pct=point_dd_pct,
            portfolio_type=portfolio_type,
            min_trades_2020_2026=funnel.min_trades_2020_2026,
            dd_reserve_pct=search.dd_reserve_pct,
            limits=limits,
        ),
        month=month,
        top_k_per_symbol=funnel.top_k_per_symbol,
        run_local_search=search.run_local_search,
        search_restarts=search.search_restarts,
        use_deep_refinement=search.use_deep_refinement,
    )
