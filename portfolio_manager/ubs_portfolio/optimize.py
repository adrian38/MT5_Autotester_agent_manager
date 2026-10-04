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
from .optimize_flow import (
    _SearchOutcome,
    _apply_greedy_pass,
    _apply_multi_start,
    _apply_deep_pass,
    _apply_executable_rounding,
    _run_search_phases,
    _OptimizationPlan,
    _Measured,
    _measure_portfolio,
    _optimizer_warnings,
    _finish_portfolio,
    _prepare_optimization,
)

def optimize_portfolio(
    raw_sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    limits: SearchLimits = SearchLimits(),
    funnel: CandidateFunnel = CandidateFunnel(),
    search: SearchPlan = SearchPlan(),
) -> PortfolioResult:
    """Encadena las fases del optimizador; cada una vive en su propia funcion.

    El orden es el contrato y lo fija _run_search_phases.
    """
    pool, effective, ctx, measured = _prepare_optimization(
        raw_sets, limits, funnel, search,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
        portfolio_type=portfolio_type,
    )
    outcome = _run_search_phases(pool, effective, ctx, search, funnel)
    return _finish_portfolio(pool, outcome, effective, measured)
