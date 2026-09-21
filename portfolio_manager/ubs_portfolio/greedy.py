"""Busqueda: construccion greedy, busqueda local y multi-start."""

from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Sequence

from .models import (
    OptimizationDecision,
    PortfolioEvaluation,
    PortfolioType,
    RobustStrategySet,
)
from .curves import curve_increment_correlation
from .selection import score_set_for_portfolio
from .evaluation import (
    _evaluation_violates_dd_limits,
    _evaluation_violation_ratio,
    evaluate_portfolio,
)
from .margin import MarginModel
from .limits import SearchLimits
from .constraints import (
    _allocations_respect_constraints,
    _portfolio_active_count,
    _portfolio_corr_allowed,
    _target_group_units_pct_allowed,
    can_add_unit,
    score_increment,
    violates_correlation_limits,
)


from .greedy_increment import (
    _caps_allow,
    _IncrementRules,
    _StepScan,
    _slot_allows_increment,
    _note_dd_block,
    _portfolio_corr_rejects,
    _keep_best_candidate,
    _consider_increment,
    _repair_reduction,
    _greedy_stop_reason,
    _added_unit_decision,
    _greedy_start,
    _scan_step,
    _run_greedy_steps,
    build_portfolio_greedy,
)
from .greedy_swap import (
    _swap_respects_caps,
    _swap_respects_correlation,
    _portfolio_corr_allows,
    _swap_candidate,
    _best_swap,
    _swap_decision,
    improve_with_local_search,
    _perturbation_move,
    _perturb_decision,
    _perturbed_trial,
    improve_with_multi_start_search,
)
from .greedy_deep import (
    _DeepScan,
    _deep_gain,
    _deep_add_move,
    _deep_swap_move,
    _best_deep_move,
    _deep_decision,
    _deep_refine_allocations,
    _active_unit_allocations,
)
