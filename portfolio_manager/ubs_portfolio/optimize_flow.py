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


from .optimize_search import (
    _PassContext,
    _SearchPass,
    _CandidatePool,
    _build_candidate_pool,
    _feasible_group_units_pct,
    _greedy_then_local_search,
    _relaxed_group_cap_pass,
    _multi_start_pass,
    _deep_refinement_pool,
    _DeepRefinement,
    _apply_deep_refinement,
    _repair_to_executable_lots,
)
from .optimize_results import (
    _margin_and_daily_summaries,
    _allocation_row,
    _result_allocation_rows,
    _group_limit_overages,
    _group_floor_warning,
    _dd_composition_warnings,
    _recent_recovery_warning,
    _deep_refinement_warning,
    _search_phase_warnings,
    _usage_warnings,
    _ttp_margin_rule_text,
    _axi_margin_rule_text,
    _default_margin_rule_text,
    _margin_warning,
    _daily_dd_warning,
    _build_portfolio_result,
)

@dataclass
class _SearchOutcome:
    """Lo que las fases de busqueda van dejando, antes de medir y redactar.

    Cada fase lo avanza: por eso es mutable y por eso arranca con lo unico que
    se sabe al empezar, el pool seleccionado.
    """

    selected: list[RobustStrategySet]
    allocations: dict[str, int] = field(default_factory=dict)
    current: PortfolioEvaluation | None = None
    stop_reason: str = ""
    best: _SearchPass | None = None
    multi_start_log: list[OptimizationDecision] = field(default_factory=list)
    valid_restarts: int = 0
    deep: _DeepRefinement | None = None
    group_cap_relaxed: bool = False
    executable_steps: dict[str, float] = field(default_factory=dict)
    execution_repair_log: list[OptimizationDecision] = field(default_factory=list)
    execution_adjustments: dict[str, int] = field(default_factory=dict)


def _apply_greedy_pass(
    outcome: _SearchOutcome,
    limits: SearchLimits,
    ctx: _PassContext,
    *,
    prefer_breadth_below_minimum: bool,
) -> None:
    """Pasada estricta y, si Balanced dejo el DD ocioso, la relajada."""
    strict = _greedy_then_local_search(
        outcome.selected, limits, ctx,
        prefer_breadth_below_minimum=prefer_breadth_below_minimum,
    )
    relaxed = _relaxed_group_cap_pass(strict, outcome.selected, limits, ctx)
    outcome.group_cap_relaxed = relaxed is not None
    outcome.best = relaxed or strict
    outcome.allocations = outcome.best.allocations
    outcome.current = outcome.best.current
    outcome.stop_reason = outcome.best.stop_reason
    if outcome.group_cap_relaxed:
        outcome.stop_reason += (
            "; group unit cap relaxed after strict Balanced allocation underused DD"
        )


def _apply_multi_start(
    outcome: _SearchOutcome,
    limits: SearchLimits,
    ctx: _PassContext,
    *,
    search_restarts: int,
) -> None:
    """Multi-start sobre la solucion local."""
    (
        outcome.allocations,
        outcome.current,
        outcome.multi_start_log,
        outcome.valid_restarts,
    ) = _multi_start_pass(
        outcome.selected, outcome.allocations, outcome.current, limits, ctx,
        search_restarts=search_restarts,
    )
    if outcome.multi_start_log:
        outcome.stop_reason += "; multi-start search improved the local solution"


def _apply_deep_pass(
    outcome: _SearchOutcome,
    pool: _CandidatePool,
    limits: SearchLimits,
    ctx: _PassContext,
    *,
    use_deep_refinement: bool,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    min_trades_2020_2026: int,
) -> None:
    """Refinamiento profundo sobre un pool ampliado, si el llamante lo pidio."""
    if not use_deep_refinement or ctx.preserve_required_allocations:
        return
    (
        outcome.selected,
        outcome.allocations,
        outcome.current,
        outcome.deep,
    ) = _apply_deep_refinement(
        pool, outcome.selected, outcome.allocations, outcome.current, limits, ctx,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    if outcome.deep.applied:
        outcome.stop_reason += "; deep optimization refined the solution"


def _apply_executable_rounding(
    outcome: _SearchOutcome,
    limits: SearchLimits,
    ctx: _PassContext,
) -> None:
    """Redondeo a lotes exportables y reparacion del DD que ese redondeo rompa."""
    (
        outcome.allocations,
        outcome.current,
        outcome.executable_steps,
        outcome.execution_repair_log,
        outcome.execution_adjustments,
    ) = _repair_to_executable_lots(
        outcome.selected,
        outcome.allocations,
        ctx,
        margin_profile=limits.margin_profile,
        max_daily_dd=limits.max_daily_dd,
        enforce_point_dd=limits.enforce_point_dd,
        daily_dd_full_history=limits.daily_dd_full_history,
    )
    if outcome.execution_repair_log:
        outcome.stop_reason += "; executable lot rounding repaired to preserve DD limits"


def _run_search_phases(
    pool: _CandidatePool,
    limits: SearchLimits,
    ctx: _PassContext,
    plan: SearchPlan,
    funnel: CandidateFunnel,
) -> _SearchOutcome:
    """Las fases, en el orden que es el contrato: cada una parte de la anterior."""
    outcome = _SearchOutcome(selected=pool.selected)
    _apply_greedy_pass(
        outcome, limits, ctx,
        prefer_breadth_below_minimum=plan.prefer_breadth_below_minimum,
    )
    outcome.deep = _DeepRefinement([], 0, False, len(outcome.selected), False)
    # A partir de aqui el tope por grupo ya no rige si se relajo.
    search_limits = limits.without_group_cap() if outcome.group_cap_relaxed else limits
    _apply_multi_start(outcome, search_limits, ctx, search_restarts=plan.search_restarts)
    _apply_deep_pass(
        outcome, pool, search_limits, ctx,
        use_deep_refinement=plan.use_deep_refinement,
        top_k_per_symbol=funnel.top_k_per_symbol,
        max_total_candidates=funnel.max_total_candidates,
        min_trades_2020_2026=funnel.min_trades_2020_2026,
    )
    _apply_executable_rounding(outcome, limits, ctx)
    return outcome


@dataclass(frozen=True)
class _OptimizationPlan:
    """Lo decidido antes de buscar que sigue haciendo falta al medir y avisar."""

    raw_sets: list[RobustStrategySet]
    valley_dd_pct: float
    funnel: CandidateFunnel
    search: SearchPlan
    ctx: _PassContext
    configured_group_units_pct: float | None
    group_units_pct_floor: float | None
    group_cap: float | None
    candidate_group_count: int


@dataclass(frozen=True)
class _Measured:
    """La cartera final medida: margen, DD diario, filas y concentracion."""

    margin_summary: dict
    daily_dd_summary: dict[str, object]
    allocations: list[StrategyAllocation]
    group_summary: dict
    eligible_groups: set[str]
    floating_overlap_audit: dict


def _measure_portfolio(
    pool: _CandidatePool,
    outcome: _SearchOutcome,
    limits: SearchLimits,
    plan: _OptimizationPlan,
) -> _Measured:
    """Mide la cartera final una sola vez, ya con los lotes ejecutables."""
    selected, allocations = outcome.selected, outcome.allocations
    current = outcome.current
    margin_summary, daily_dd_summary = _margin_and_daily_summaries(
        selected, allocations, current, limits,
    )
    return _Measured(
        margin_summary=margin_summary,
        daily_dd_summary=daily_dd_summary,
        allocations=_result_allocation_rows(
            selected, allocations, margin_summary, limits,
            capital=plan.ctx.capital, executable_steps=outcome.executable_steps,
        ),
        group_summary=portfolio_group_summary(selected, allocations),
        eligible_groups={portfolio_group_key(strategy.symbol) for strategy in pool.eligible},
        # Auditoria del supuesto del max(): se mide una sola vez sobre la cartera
        # final, nunca dentro de la busqueda. No cambia el riesgo aplicado; avisa
        # cuando varias estrategias coinciden bajo el agua y el maximo individual
        # deja de describir la exposicion.
        floating_overlap_audit=portfolio_floating_overlap_audit(
            selected, allocations, current.floating_dd_buffer,
        ),
    )


def _optimizer_warnings(
    pool: _CandidatePool,
    outcome: _SearchOutcome,
    limits: SearchLimits,
    plan: _OptimizationPlan,
    measured: _Measured,
) -> list[str]:
    """Todo lo que el usuario debe saber de esta cartera, en orden."""
    current, best = outcome.current, outcome.best
    return [
        *_group_floor_warning(
            plan.configured_group_units_pct,
            plan.group_units_pct_floor,
            plan.group_cap,
            plan.candidate_group_count,
        ),
        *_dd_composition_warnings(
            current, plan.search.dd_reserve_pct, measured.floating_overlap_audit,
        ),
        *_recent_recovery_warning(plan.raw_sets),
        *_search_phase_warnings(
            search_restarts=plan.search.search_restarts,
            valid_restarts=outcome.valid_restarts,
            use_deep_refinement=plan.search.use_deep_refinement,
            deep=outcome.deep,
            base_selected_count=pool.base_selected_count,
            selected_count=len(outcome.selected),
            preserve_required_allocations=plan.funnel.preserve_required_allocations,
            required_ids=pool.required_ids,
            greedy_log=best.greedy_log,
            group_cap_relaxed=outcome.group_cap_relaxed,
        ),
        *_usage_warnings(
            current,
            portfolio_type=plan.ctx.portfolio_type,
            eligible_groups=measured.eligible_groups,
            enforce_point_dd=limits.enforce_point_dd,
            result_allocations=measured.allocations,
            execution_adjustments=outcome.execution_adjustments,
            execution_repair_log=outcome.execution_repair_log,
            correlation_rejections=best.correlation_rejections,
            group_limit_overages=_group_limit_overages(
                measured.group_summary, measured.eligible_groups, plan.group_cap,
            ),
            max_units_per_group_pct=plan.group_cap,
        ),
        *_margin_warning(measured.margin_summary, limits.margin_profile),
        *_daily_dd_warning(
            current,
            measured.daily_dd_summary,
            max_daily_dd=limits.max_daily_dd,
            daily_dd_full_history=limits.daily_dd_full_history,
        ),
    ]


def _finish_portfolio(
    pool: _CandidatePool,
    outcome: _SearchOutcome,
    limits: SearchLimits,
    plan: _OptimizationPlan,
) -> PortfolioResult:
    """Comprueba los limites, mide, redacta los avisos y arma el resultado."""
    current = outcome.current
    if current.valley_dd > plan.ctx.target_valley_dd:
        raise ValueError("Final portfolio violates valley DD")
    if limits.enforce_point_dd and current.point_dd > plan.ctx.target_point_dd:
        raise ValueError("Final portfolio violates point DD")
    measured = _measure_portfolio(pool, outcome, limits, plan)
    warnings = _optimizer_warnings(pool, outcome, limits, plan, measured)
    stress_bootstrap = bootstrap_valley_drawdown(
        current.equity_curve_2020_2026,
        nominal_valley_dd_limit=plan.ctx.capital * plan.valley_dd_pct / 100.0,
        effective_valley_dd_limit=plan.ctx.target_valley_dd,
        simulations=plan.search.bootstrap_simulations,
        block_size=plan.search.bootstrap_block_size,
        seed=plan.search.bootstrap_seed,
    )
    if stress_bootstrap.alert:
        warnings.append(
            f"ALERTA bootstrap: DD valle P95 {stress_bootstrap.valley_dd_p95:.2f} "
            f"supera el limite efectivo {stress_bootstrap.effective_valley_dd_limit:.2f}."
        )
    best = outcome.best
    return _build_portfolio_result(
        measured.allocations,
        current,
        target_valley_dd=plan.ctx.target_valley_dd,
        target_point_dd=plan.ctx.target_point_dd,
        stop_reason=outcome.stop_reason,
        warnings=warnings,
        decision_log=(
            best.greedy_log + best.local_log + outcome.multi_start_log
            + outcome.deep.log + outcome.execution_repair_log
        ),
        unused_sets=_build_unused_sets(
            plan.raw_sets, pool.eligible, outcome.selected, outcome.allocations,
            plan.funnel.min_trades_2020_2026,
        ),
        correlation_rejections=best.correlation_rejections,
        group_summary=measured.group_summary,
        stress_bootstrap=stress_bootstrap,
        margin_summary=measured.margin_summary,
        daily_dd_summary=measured.daily_dd_summary,
        max_daily_dd=limits.max_daily_dd,
        daily_dd_full_history=limits.daily_dd_full_history,
        enforce_point_dd=limits.enforce_point_dd,
        floating_overlap_audit=measured.floating_overlap_audit,
    )


def _prepare_optimization(
    raw_sets: list[RobustStrategySet],
    limits: SearchLimits,
    funnel: CandidateFunnel,
    plan: SearchPlan,
    *,
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
) -> tuple[_CandidatePool, SearchLimits, _PassContext, _OptimizationPlan]:
    """Decide el pool, los topes efectivos y el contexto antes de buscar."""
    reserve_factor = 1.0 - min(max(float(plan.dd_reserve_pct), 0.0), 99.0) / 100.0
    effective_valley_dd_pct = valley_dd_pct * reserve_factor
    effective_point_dd_pct = point_dd_pct * reserve_factor
    pool = _build_candidate_pool(
        raw_sets,
        min_trades_2020_2026=funnel.min_trades_2020_2026,
        top_k_per_symbol=funnel.top_k_per_symbol,
        max_total_candidates=funnel.max_total_candidates,
        required_set_ids=funnel.required_set_ids,
        required_initial_allocations=funnel.required_initial_allocations,
    )
    by_type = limits.with_group_defaults(portfolio_type)
    candidate_group_count = _candidate_group_count(pool.selected)
    group_cap, group_units_pct_floor = _feasible_group_units_pct(
        by_type.max_units_per_group_pct, candidate_group_count,
    )
    ctx = _PassContext(
        capital=capital,
        valley_dd_pct=effective_valley_dd_pct,
        point_dd_pct=effective_point_dd_pct,
        portfolio_type=portfolio_type,
        target_valley_dd=capital * effective_valley_dd_pct / 100.0,
        target_point_dd=capital * effective_point_dd_pct / 100.0,
        initial_allocations={
            set_id: max(int((funnel.required_initial_allocations or {}).get(set_id, 1)), 1)
            for set_id in pool.required_ids
        },
        minimum_active_strategies=plan.minimum_active_strategies,
        maximum_active_strategies=plan.maximum_active_strategies,
        fixed_set_ids=pool.required_ids if funnel.preserve_required_allocations else set(),
        preserve_required_allocations=funnel.preserve_required_allocations,
        required_ids=pool.required_ids,
        run_local_search=plan.run_local_search,
    )
    measured = _OptimizationPlan(
        raw_sets=raw_sets,
        valley_dd_pct=valley_dd_pct,
        funnel=funnel,
        search=plan,
        ctx=ctx,
        configured_group_units_pct=by_type.max_units_per_group_pct,
        group_units_pct_floor=group_units_pct_floor,
        group_cap=group_cap,
        candidate_group_count=candidate_group_count,
    )
    return pool, replace(by_type, max_units_per_group_pct=group_cap), ctx, measured
