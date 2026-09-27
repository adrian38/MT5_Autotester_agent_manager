"""Orquestacion de optimize_portfolio y sus avisos."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

from .symbols import portfolio_group_key
from .models import (
    BootstrapDrawdownAnalysis,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_BOOTSTRAP_SIMULATIONS,
    MIN_RECENT_EQUITY_RECOVERY,
    OptimizationDecision,
    PortfolioEvaluation,
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
    StrategyAllocation,
    UnusedSetInfo,
    group_limits_for_portfolio_type,
)
from .rows import _lot_size_step
from .curves import (
    bootstrap_valley_drawdown,
    portfolio_daily_closed_floating_dd,
    portfolio_floating_overlap_audit,
)
from .selection import (
    filter_eligible_sets,
    select_top_k_per_symbol,
)
from .evaluation import portfolio_group_summary
from .limits import CandidateFunnel, SearchLimits, SearchPlan
from .margin import (
    MarginModel,
    margin_profile_label,
    normalize_margin_profile,
    portfolio_margin_summary,
    resolve_margin_model,
)
from .constraints import _candidate_group_count
from .execution import (
    _build_unused_sets,
    _repair_executable_allocations,
)
from .greedy import (
    _deep_refine_allocations,
    build_portfolio_greedy,
    improve_with_local_search,
    improve_with_multi_start_search,
)


@dataclass(frozen=True)
class _PassContext:
    """Lo que las pasadas de busqueda comparten y no son limites."""

    capital: float
    valley_dd_pct: float
    point_dd_pct: float
    portfolio_type: PortfolioType
    target_valley_dd: float
    target_point_dd: float
    initial_allocations: dict[str, int]
    minimum_active_strategies: int | None
    maximum_active_strategies: int | None
    fixed_set_ids: set[str]
    preserve_required_allocations: bool
    required_ids: set[str]
    run_local_search: bool


@dataclass
class _SearchPass:
    """Resultado de una pasada greedy mas su busqueda local."""

    allocations: dict[str, int]
    current: PortfolioEvaluation
    greedy_log: list[OptimizationDecision]
    local_log: list[OptimizationDecision]
    stop_reason: str
    correlation_rejections: int


@dataclass
class _CandidatePool:
    """Pool elegible y su seleccion, con las obligatorias reincorporadas."""

    eligible: list[RobustStrategySet]
    eligible_by_id: dict[str, RobustStrategySet]
    selected: list[RobustStrategySet]
    base_selected_count: int
    required_ids: set[str]


def _build_candidate_pool(
    raw_sets: list[RobustStrategySet],
    *,
    min_trades_2020_2026: int,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    required_set_ids: Sequence[str] | None,
    required_initial_allocations: dict[str, int] | None,
) -> _CandidatePool:
    """Filtra, selecciona y devuelve las obligatorias al pool."""
    eligible = filter_eligible_sets(raw_sets, min_trades_2020_2026)
    if not eligible:
        raise ValueError("No eligible robust sets found")
    selected = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    base_selected_count = len(selected)
    required_ids = {str(set_id) for set_id in (required_set_ids or ())}
    required_ids.update(str(set_id) for set_id in (required_initial_allocations or {}))
    eligible_by_id = {strategy.set_id: strategy for strategy in eligible}
    # Una estrategia obligatoria no es una candidata: ya pertenece al
    # portafolio que se esta ampliando, y el llamante la bloquea. El embudo
    # decide a quien se INVITA, no a quien se expulsa de lo ya guardado, asi
    # que las obligatorias vuelven aunque hoy no lo pasen -por cuarentena, por
    # aporte reciente o por lo que sea-. Sin esto, cualquier miembro que se
    # degradase convertia su portafolio en inmejorable.
    reinstated = [
        strategy for strategy in raw_sets
        if strategy.set_id in required_ids and strategy.set_id not in eligible_by_id
    ]
    if reinstated:
        eligible = list(eligible) + reinstated
        eligible_by_id.update({strategy.set_id: strategy for strategy in reinstated})
    missing_required = sorted(required_ids - set(eligible_by_id))
    if missing_required:
        raise ValueError(
            "Required portfolio sets are no longer in the candidate pool: "
            + ", ".join(Path(set_id).name for set_id in missing_required)
        )
    selected_ids = {strategy.set_id for strategy in selected}
    selected.extend(eligible_by_id[set_id] for set_id in required_ids - selected_ids)
    return _CandidatePool(
        eligible, eligible_by_id, selected, base_selected_count, required_ids,
    )


def _feasible_group_units_pct(
    configured: float | None, candidate_group_count: int,
) -> tuple[float | None, float | None]:
    """Sube el tope por grupo hasta 1/N cuando N grupos lo hacen imposible.

    Devuelve el tope aplicable y el suelo de factibilidad; el suelo es ``None``
    cuando no hizo falta tocar nada, y es lo que despues justifica el aviso.
    """
    if configured is None or candidate_group_count <= 1:
        return configured, None
    # Un tope por debajo de 1/N es inalcanzable si el pool solo tiene N grupos
    # (por ejemplo 40% con Forex + Metales). Se usa el minimo factible en vez de
    # dejar al asignador greedy atascado.
    floor = 1.0 / candidate_group_count
    return max(float(configured), floor), floor


def _greedy_then_local_search(
    selected: list[RobustStrategySet],
    limits: SearchLimits,
    ctx: _PassContext,
    *,
    prefer_breadth_below_minimum: bool,
) -> _SearchPass:
    """Construccion greedy y, si procede, busqueda local sobre su resultado.

    Es la secuencia que ``optimize_portfolio`` ejecuta dos veces: con el tope
    por grupo y sin el.
    """
    allocations, current, greedy_log, stop_reason, rejections = build_portfolio_greedy(
        sets=selected,
        capital=ctx.capital,
        valley_dd_pct=ctx.valley_dd_pct,
        point_dd_pct=ctx.point_dd_pct,
        portfolio_type=ctx.portfolio_type,
        initial_allocations=ctx.initial_allocations,
        minimum_active_strategies=ctx.minimum_active_strategies,
        maximum_active_strategies=ctx.maximum_active_strategies,
        prefer_breadth_below_minimum=prefer_breadth_below_minimum,
        fixed_set_ids=ctx.fixed_set_ids,
        allow_fixed_reductions_for_repair=ctx.preserve_required_allocations,
        limits=limits,
    )
    local_log: list[OptimizationDecision] = []
    if ctx.run_local_search and not ctx.preserve_required_allocations:
        allocations, current, local_log = improve_with_local_search(
            sets=selected,
            allocations=allocations,
            current=current,
            target_valley_dd=ctx.target_valley_dd,
            target_point_dd=ctx.target_point_dd,
            protected_set_ids=ctx.required_ids,
            minimum_active_strategies=ctx.minimum_active_strategies,
            limits=limits,
        )
    return _SearchPass(
        allocations, current, greedy_log, local_log, stop_reason, rejections,
    )


def _relaxed_group_cap_pass(
    strict: _SearchPass,
    selected: list[RobustStrategySet],
    limits: SearchLimits,
    ctx: _PassContext,
) -> _SearchPass | None:
    """Repite la busqueda sin tope por grupo cuando Balanced dejo el DD ocioso.

    Devuelve ``None`` si no procede, o si la pasada relajada no mejora beneficio
    y unidades a la vez.
    """
    if ctx.portfolio_type != PortfolioType.BALANCED:
        return None
    if limits.max_units_per_group_pct is None or _candidate_group_count(selected) <= 1:
        return None
    if strict.current.valley_usage_pct >= 70:
        return None
    relaxed = _greedy_then_local_search(
        selected,
        limits.without_group_cap(),
        ctx,
        # La pasada estricta reenvia el valor del llamante; esta nunca lo hizo y
        # se quedaba con el default. Se mantiene la asimetria a proposito:
        # igualarla cambiaria carteras ya guardadas.
        prefer_breadth_below_minimum=False,
    )
    improves = (
        relaxed.current.total_net_profit > strict.current.total_net_profit
        and relaxed.current.total_units > strict.current.total_units
    )
    return relaxed if improves else None


def _multi_start_pass(
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    ctx: _PassContext,
    *,
    search_restarts: int,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    """Multi-start sobre la solucion local, si el llamante pidio reinicios."""
    if search_restarts <= 0 or ctx.preserve_required_allocations:
        return allocations, current, [], 0
    return improve_with_multi_start_search(
        sets=selected,
        allocations=allocations,
        current=current,
        target_valley_dd=ctx.target_valley_dd,
        target_point_dd=ctx.target_point_dd,
        restarts=int(search_restarts),
        # Sin minimum_active_strategies a proposito: esta fase nunca lo recibio.
        limits=limits,
    )


def _deep_refinement_pool(
    pool: _CandidatePool,
    selected: list[RobustStrategySet],
    *,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    min_trades_2020_2026: int,
) -> list[RobustStrategySet]:
    """Pool ampliado para la pasada profunda, sin perder nada de lo ya elegido."""
    deep_top_k = max(int(top_k_per_symbol), min(20, int(top_k_per_symbol) * 2))
    if max_total_candidates is None:
        deep_max_candidates = None
    else:
        deep_max_candidates = min(
            len(pool.eligible),
            max(int(max_total_candidates), int(max_total_candidates) * 2),
        )
    deep_selected = select_top_k_per_symbol(
        pool.eligible,
        top_k_per_symbol=deep_top_k,
        max_total_candidates=deep_max_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    deep_selected_by_id = {strategy.set_id: strategy for strategy in deep_selected}
    for set_id in pool.required_ids:
        if set_id in pool.eligible_by_id:
            deep_selected_by_id.setdefault(set_id, pool.eligible_by_id[set_id])
    for strategy in selected:
        deep_selected_by_id.setdefault(strategy.set_id, strategy)
    return list(deep_selected_by_id.values())


@dataclass
class _DeepRefinement:
    """Lo que la pasada profunda deja atras, se aplique o no."""

    log: list[OptimizationDecision]
    attempts: int
    pool_expanded: bool
    pool_count: int
    applied: bool


def _apply_deep_refinement(
    pool: _CandidatePool,
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    ctx: _PassContext,
    *,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    min_trades_2020_2026: int,
) -> tuple[list[RobustStrategySet], dict[str, int], PortfolioEvaluation, _DeepRefinement]:
    """Refina sobre un pool ampliado y solo adopta el resultado si mejora."""
    deep_selected = _deep_refinement_pool(
        pool,
        selected,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    refined_allocations, refined_current, deep_log, deep_attempts = _deep_refine_allocations(
        deep_selected,
        allocations,
        current,
        minimum_active_strategies=ctx.minimum_active_strategies,
        limits=limits,
    )
    outcome = _DeepRefinement(
        log=deep_log,
        attempts=deep_attempts,
        pool_expanded=len(deep_selected) > len(selected),
        pool_count=len(deep_selected),
        applied=refined_current.total_net_profit > current.total_net_profit + 1e-9,
    )
    if outcome.applied:
        return deep_selected, refined_allocations, refined_current, outcome
    return selected, allocations, current, outcome


def _repair_to_executable_lots(
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    ctx: _PassContext,
    *,
    margin_profile: str | MarginModel | None,
    max_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
) -> tuple[dict[str, int], PortfolioEvaluation, dict[str, float], list[OptimizationDecision], dict[str, int]]:
    """Redondea a lotes exportables y repara el DD que ese redondeo estropee."""
    optimized_allocations = allocations.copy()
    (
        executable_allocations,
        current,
        executable_steps,
        execution_repair_log,
    ) = _repair_executable_allocations(
        selected,
        allocations,
        ctx.capital,
        resolve_margin_model(margin_profile),
        target_valley_dd=ctx.target_valley_dd,
        target_point_dd=ctx.target_point_dd,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
        minimum_active_strategies=ctx.minimum_active_strategies,
        protected_set_ids=ctx.required_ids,
    )
    execution_adjustments = {
        set_id: executable_allocations[set_id]
        for set_id, units in optimized_allocations.items()
        if units > 0 and executable_allocations.get(set_id, 0) != units
    }
    return (
        executable_allocations,
        current,
        executable_steps,
        execution_repair_log,
        execution_adjustments,
    )
