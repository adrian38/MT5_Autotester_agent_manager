"""Lotes ejecutables: plan de exportacion, redondeo y su reparacion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .models import (
    MIN_RECENT_EQUITY_RECOVERY,
    OptimizationDecision,
    PortfolioEvaluation,
    RobustStrategySet,
    UnusedSetInfo,
)
from .rows import (
    _step_for_max_units,
    execution_units_from_step,
)
from .selection import score_set_for_portfolio
from .evaluation import (
    _evaluation_violates_dd_limits,
    _evaluation_violation_ratio,
    evaluate_portfolio,
)
from .margin import MarginModel


def set_current_value(text: str, key: str, value: object) -> tuple[str, bool]:
    out: list[str] = []
    found = False
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(";"):
            lhs, rhs = line.split("=", 1)
            if lhs.strip() == key:
                if "||" in rhs:
                    parts = rhs.split("||")
                    parts[0] = str(value)
                    rhs = "||".join(parts)
                else:
                    rhs = str(value)
                line = f"{lhs}={rhs}"
                found = True
        out.append(line)
    return "\n".join(out), found


def apply_portfolio_lot_text(text: str, lot_size_step: float) -> tuple[str, int, bool]:
    step_int = max(1, int(math.ceil(lot_size_step)))
    text, _ = set_current_value(text, "Risk", 2)
    text, found_step = set_current_value(text, "LotPerBalance_step", step_int)
    return text, step_int, found_step


def _execution_plan_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    capital: float,
    model: MarginModel | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Traduce unidades a un ``LotPerBalance_step`` que el EA pueda ejecutar.

    El EA dimensiona en escalones de 0.01 lotes. Una unidad son
    ``lot_increments_for`` escalones, que es 1 en cualquier simbolo con minimo
    0.01 y 100 en uno con minimo 1.0. Sin ese factor el step exportado pedia
    0.01xN lotes, MT5 lo subia al minimo del simbolo y las N unidades acababan
    ejecutandose como una sola: el portafolio contaba con N veces la curva y
    recibia una.
    """
    executable = allocations.copy()
    steps: dict[str, int] = {}
    for strategy in sets:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            executable[strategy.set_id] = 0
            continue
        increments = model.lot_increments_for(strategy.symbol) if model else 1
        step = _step_for_max_units(capital, units * increments)
        # Se baja a unidades enteras: media posicion minima no existe, MT5 la
        # redondearia al step del simbolo y la curva dejaria de cuadrar.
        executable[strategy.set_id] = execution_units_from_step(capital, step) // increments
        steps[strategy.set_id] = step
    return executable, steps


@dataclass(frozen=True)
class _ExecutableRepairConfig:
    sets: list[RobustStrategySet]
    capital: float
    model: MarginModel | None
    target_valley_dd: float
    target_point_dd: float
    max_daily_dd: float | None
    enforce_point_dd: bool
    daily_dd_full_history: bool
    protected: set[str]
    minimum_active: int


@dataclass
class _ExecutableRepairState:
    allocations: dict[str, int]
    evaluation: PortfolioEvaluation
    steps: dict[str, int]


def _evaluate_executable_allocations(
    config: _ExecutableRepairConfig,
    allocations: dict[str, int],
) -> PortfolioEvaluation:
    return evaluate_portfolio(
        config.sets,
        allocations,
        config.target_valley_dd,
        config.target_point_dd,
        config.max_daily_dd,
        config.enforce_point_dd,
        config.daily_dd_full_history,
    )


def _executable_reduction_choice(
    config: _ExecutableRepairConfig,
    state: _ExecutableRepairState,
    strategy: RobustStrategySet,
) -> tuple[bool, tuple[object, ...]] | None:
    units = state.allocations.get(strategy.set_id, 0)
    minimum_units = 1 if strategy.set_id in config.protected else 0
    if units <= minimum_units:
        return None
    requested = state.allocations.copy()
    requested[strategy.set_id] = units - 1
    trial_allocations, trial_steps = _execution_plan_allocations(
        config.sets, requested, config.capital, config.model,
    )
    if trial_allocations == state.allocations:
        return None
    if any(trial_allocations.get(set_id, 0) <= 0 for set_id in config.protected):
        return None
    active = sum(1 for value in trial_allocations.values() if value > 0)
    if active < config.minimum_active:
        return None
    trial = _evaluate_executable_allocations(config, trial_allocations)
    payload = (strategy.set_id, trial_allocations, trial, trial_steps)
    if not _evaluation_violates_dd_limits(trial):
        return True, (
            -trial.total_net_profit,
            _evaluation_violation_ratio(trial),
            strategy.set_id,
            *payload,
        )
    return False, (
        _evaluation_violation_ratio(trial),
        -trial.total_net_profit,
        strategy.set_id,
        *payload,
    )


def _best_executable_reduction(
    config: _ExecutableRepairConfig,
    state: _ExecutableRepairState,
) -> tuple[object, ...] | None:
    best_valid: tuple[object, ...] | None = None
    best_progress: tuple[object, ...] | None = None
    for strategy in config.sets:
        candidate = _executable_reduction_choice(config, state, strategy)
        if candidate is None:
            continue
        valid, choice = candidate
        if valid and (best_valid is None or choice[:3] < best_valid[:3]):
            best_valid = choice
        elif not valid and (best_progress is None or choice[:3] < best_progress[:3]):
            best_progress = choice
    return best_valid or best_progress


def _apply_executable_reduction(
    state: _ExecutableRepairState,
    selected: tuple[object, ...],
    decision_log: list[OptimizationDecision],
) -> None:
    reduced_set_id = str(selected[3])
    next_allocations, next_evaluation, next_steps = selected[4:7]
    assert isinstance(next_allocations, dict)
    assert isinstance(next_evaluation, PortfolioEvaluation)
    assert isinstance(next_steps, dict)
    previous = state.evaluation
    state.allocations = next_allocations
    state.evaluation = next_evaluation
    state.steps = next_steps
    decision_log.append(OptimizationDecision(
        step=len(decision_log) + 1,
        action="reduce_unit_for_execution_dd",
        set_id=reduced_set_id,
        from_set_id=reduced_set_id,
        to_set_id=None,
        gain=state.evaluation.total_net_profit - previous.total_net_profit,
        valley_cost=state.evaluation.valley_dd - previous.valley_dd,
        point_cost=state.evaluation.point_dd - previous.point_dd,
        score=-_evaluation_violation_ratio(state.evaluation),
        portfolio_net_profit_after=state.evaluation.total_net_profit,
        portfolio_valley_dd_after=state.evaluation.valley_dd,
        portfolio_point_dd_after=state.evaluation.point_dd,
        reason=(
            "Executable LotPerBalance_step rounding raised combined DD; "
            "reduced one executable unit while preserving required strategies"
        ),
    ))


def _repair_executable_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    capital: float,
    model: MarginModel | None,
    *,
    target_valley_dd: float,
    target_point_dd: float,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
    minimum_active_strategies: int | None = None,
    protected_set_ids: Sequence[str] | None = None,
) -> tuple[
    dict[str, int],
    PortfolioEvaluation,
    dict[str, int],
    list[OptimizationDecision],
]:
    """Make the integer EA lot plan safe without losing required strategies.

    Rounding one strategy down can remove profit that hedged another curve and
    therefore *increase* the combined closed drawdown.  Starting from the
    actual executable plan, reduce one executable unit at a time until both DD
    limits are valid.  A valid immediate choice keeps the most profit; while
    still invalid, the search follows the lowest violation ratio.  Reductions
    always make progress, so a required all-one composition is reached when
    that is the only feasible plan.
    """
    current_allocations, current_steps = _execution_plan_allocations(
        sets, allocations, capital, model,
    )
    config = _ExecutableRepairConfig(
        sets=sets,
        capital=capital,
        model=model,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
        protected={str(set_id) for set_id in (protected_set_ids or ())},
        minimum_active=max(int(minimum_active_strategies or 0), 0),
    )
    state = _ExecutableRepairState(
        current_allocations,
        _evaluate_executable_allocations(config, current_allocations),
        current_steps,
    )
    decision_log: list[OptimizationDecision] = []
    while _evaluation_violates_dd_limits(state.evaluation):
        selected = _best_executable_reduction(config, state)
        if selected is None:
            break
        _apply_executable_reduction(state, selected, decision_log)
    return state.allocations, state.evaluation, state.steps, decision_log


def _build_unused_sets(
    raw_sets: list[RobustStrategySet],
    eligible: list[RobustStrategySet],
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    min_trades_2020_2026: int,
) -> list[UnusedSetInfo]:
    eligible_ids = {strategy.set_id for strategy in eligible}
    selected_ids = {strategy.set_id for strategy in selected}
    unused: list[UnusedSetInfo] = []
    for strategy in raw_sets:
        reason = ""
        if strategy.robustness_status != "accepted":
            reason = "not_accepted"
        elif strategy.already_used:
            reason = "already_used"
        elif strategy.trades_2020_2026 < min_trades_2020_2026:
            reason = "below_min_trades"
        elif strategy.net_profit_2020_2026_001 <= 0:
            reason = "non_positive_net_profit"
        elif strategy.has_recent_performance and (
            strategy.recent_net_profit_001 / max(strategy.recent_equity_dd_001, 1.0)
            < MIN_RECENT_EQUITY_RECOVERY
        ):
            reason = "recent_equity_recovery_below_1"
        elif strategy.set_id not in eligible_ids:
            reason = "not_eligible"
        elif strategy.set_id not in selected_ids:
            reason = "not_selected_top_k"
        elif allocations.get(strategy.set_id, 0) <= 0:
            reason = "received_zero_units"
        if reason:
            unused.append(
                UnusedSetInfo(
                    set_id=strategy.set_id,
                    symbol=strategy.symbol,
                    score=score_set_for_portfolio(strategy, min_trades_2020_2026),
                    reason=reason,
                )
            )
    return sorted(unused, key=lambda item: (item.reason, -item.score, item.symbol))
