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


def _caps_allow(
    sets: list[RobustStrategySet],
    strategy: RobustStrategySet,
    allocations: dict[str, int],
    limits: SearchLimits,
) -> bool:
    """Si los topes de unidades, grupo y margen dejan sitio a una mas."""
    return can_add_unit(
        target_set=strategy,
        sets=sets,
        allocations=allocations,
        max_units_per_set=limits.max_units_per_set,
        max_total_units=limits.max_total_units,
        max_units_per_symbol=limits.max_units_per_symbol,
        max_sets_per_symbol=limits.max_sets_per_symbol,
        max_units_per_group_pct=limits.max_units_per_group_pct,
        max_sets_per_group=limits.max_sets_per_group,
        # Sin resolver por tipo de cartera, vale el defecto de can_add_unit.
        group_unit_cap_bootstrap=(
            10 if limits.group_unit_cap_bootstrap is None
            else limits.group_unit_cap_bootstrap
        ),
        margin_balance=limits.margin_balance,
        max_margin_pct=limits.max_margin_pct,
        margin_profile=limits.margin_profile,
        stock_leverage=limits.stock_leverage,
        default_leverage=limits.default_leverage,
        stock_contract_size=limits.stock_contract_size,
        default_contract_size=limits.default_contract_size,
    )


@dataclass(frozen=True)
class _IncrementRules:
    """Contra que se mide cada candidata: los topes mas el contexto del paso.

    Los topes viven en ``limits``, no copiados aqui: son los mismos que ve el
    resto de la busqueda.
    """

    sets: list[RobustStrategySet]
    portfolio_type: PortfolioType
    target_valley_dd: float
    target_point_dd: float
    limits: SearchLimits
    minimum_active_strategies: int | None
    maximum_active_strategies: int | None
    allow_fixed_reductions_for_repair: bool
    fixed_ids: set[str]
    portfolio_curves: list[Sequence[float]]

    def evaluate(self, allocations: dict[str, int]) -> PortfolioEvaluation:
        """La cartera medida con los limites de esta busqueda."""
        return evaluate_portfolio(
            self.sets,
            allocations,
            self.target_valley_dd,
            self.target_point_dd,
            self.limits.max_daily_dd,
            self.limits.enforce_point_dd,
            self.limits.daily_dd_full_history,
        )

    def caps_allow(self, strategy: RobustStrategySet, allocations: dict[str, int]) -> bool:
        """Si los topes dejan sitio a una unidad mas de esta candidata."""
        return _caps_allow(self.sets, strategy, allocations, self.limits)


@dataclass
class _StepScan:
    """Lo que un paso acumula mientras prueba todas las candidatas."""

    step: int
    opening_slots: bool
    decision_log: list[OptimizationDecision]
    correlation_rejections: int
    best: dict[str, object] | None = None
    best_repair: dict[str, object] | None = None
    blocked_by_risk: bool = False
    # Por que se quedo sin incrementos en ESTE paso. El motivo lo decide el
    # recuento, no una cadena fija: el `stop_reason` culpaba siempre al DD y
    # mando dos veces la investigacion al sitio equivocado cuando quien
    # bloqueaba era la correlacion con el DD al 30% del presupuesto.
    blocks: dict[str, int] = field(
        default_factory=lambda: {"dd": 0, "pair_corr": 0, "portfolio_corr": 0, "caps": 0}
    )

    def reject(self, kind: str) -> None:
        self.blocks[kind] += 1

    def log_correlation_rejection(
        self,
        action: str,
        strategy: RobustStrategySet,
        current: PortfolioEvaluation,
        reason: str,
    ) -> None:
        """Deja constancia de una candidata rechazada por correlacion."""
        self.correlation_rejections += 1
        self.decision_log.append(
            OptimizationDecision(
                step=self.step + 1,
                action=action,
                set_id=strategy.set_id,
                from_set_id=None,
                to_set_id=None,
                gain=0.0,
                valley_cost=0.0,
                point_cost=0.0,
                score=float("-inf"),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason=reason,
            )
        )


def _slot_allows_increment(
    rules: _IncrementRules,
    strategy: RobustStrategySet,
    allocations: dict[str, int],
    current: PortfolioEvaluation,
) -> bool:
    """Si el cupo de estrategias activas permite tocar esta candidata."""
    if (
        rules.maximum_active_strategies is not None
        and current.active_strategies >= rules.maximum_active_strategies
        and allocations.get(strategy.set_id, 0) <= 0
    ):
        return False
    if (
        rules.minimum_active_strategies is not None
        and current.active_strategies < rules.minimum_active_strategies
        and allocations.get(strategy.set_id, 0) > 0
    ):
        # During repair, fill the missing strategy slots before adding
        # more risk to strategies that are already active.
        return False
    return True


def _note_dd_block(
    strategy: RobustStrategySet,
    temp: PortfolioEvaluation,
    temp_allocations: dict[str, int],
    current: PortfolioEvaluation,
    rules: _IncrementRules,
    scan: _StepScan,
) -> None:
    """Candidata que rompe el DD; en reparacion puede servir si lo reduce."""
    scan.blocked_by_risk = True
    scan.reject("dd")
    if not rules.allow_fixed_reductions_for_repair:
        return
    current_violation = _evaluation_violation_ratio(current)
    temp_violation = _evaluation_violation_ratio(temp)
    if temp_violation >= current_violation - 1e-9:
        return
    repair_score = (current_violation - temp_violation) * 1_000_000_000.0
    repair_score += max(temp.total_net_profit - current.total_net_profit, 0.0)
    if scan.best_repair is None or repair_score > float(scan.best_repair["score"]):
        scan.best_repair = {
            "set": strategy,
            "allocations": temp_allocations,
            "evaluation": temp,
            "score": repair_score,
            "reason": "Replacement increment reduced the DD violation",
        }


def _portfolio_corr_rejects(
    temp: PortfolioEvaluation,
    strategy: RobustStrategySet,
    current: PortfolioEvaluation,
    rules: _IncrementRules,
    scan: _StepScan,
) -> bool:
    """Si la curva resultante se parece demasiado a una cartera ya existente."""
    if rules.limits.max_portfolio_corr is None or not rules.portfolio_curves:
        return False
    worst_portfolio_corr = max(
        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
        for curve in rules.portfolio_curves
    )
    if worst_portfolio_corr <= rules.limits.max_portfolio_corr:
        return False
    scan.blocked_by_risk = True
    scan.reject("portfolio_corr")
    scan.log_correlation_rejection(
        "reject_portfolio_corr", strategy, current,
        f"portfolio_corr>{rules.limits.max_portfolio_corr:.2f}",
    )
    return True


def _keep_best_candidate(
    strategy: RobustStrategySet,
    temp: PortfolioEvaluation,
    temp_allocations: dict[str, int],
    current: PortfolioEvaluation,
    score: float,
    scan: _StepScan,
) -> None:
    """Se queda con la mejor candidata del paso segun el criterio en vigor."""
    selection_key = (
        (-(temp.valley_dd - current.valley_dd), score)
        if scan.opening_slots else (score,)
    )
    previous_key = (
        scan.best.get("selection_key", (float(scan.best["score"]),))
        if scan.best is not None else None
    )
    if previous_key is not None and selection_key <= previous_key:
        return
    scan.best = {
        "set": strategy,
        "allocations": temp_allocations,
        "evaluation": temp,
        "score": score,
        "selection_key": selection_key,
        "reason": (
            "Cheapest valid +0.01 increment while opening required slots"
            if scan.opening_slots else "Best valid +0.01 increment"
        ),
    }


def _consider_increment(
    strategy: RobustStrategySet,
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    rules: _IncrementRules,
    scan: _StepScan,
) -> None:
    """Prueba una unidad mas en esta candidata y actualiza el mejor del paso."""
    if strategy.set_id in rules.fixed_ids:
        return
    if not _slot_allows_increment(rules, strategy, allocations, current):
        return
    if not rules.caps_allow(strategy, allocations):
        scan.reject("caps")
        return
    rejected_by_corr, corr_reason = violates_correlation_limits(
        strategy,
        rules.sets,
        allocations,
        rules.limits.max_pair_corr,
        rules.limits.max_downside_corr,
        rules.limits.max_dd_overlap,
    )
    if rejected_by_corr:
        scan.reject("pair_corr")
        scan.log_correlation_rejection("reject_corr", strategy, current, corr_reason)
        return
    temp_allocations = allocations.copy()
    temp_allocations[strategy.set_id] += 1
    temp = rules.evaluate(temp_allocations)
    if _evaluation_violates_dd_limits(temp):
        _note_dd_block(strategy, temp, temp_allocations, current, rules, scan)
        return
    if _portfolio_corr_rejects(temp, strategy, current, rules, scan):
        return
    score = score_increment(current, temp, allocations[strategy.set_id], rules.portfolio_type)
    if score == float("-inf"):
        return
    _keep_best_candidate(strategy, temp, temp_allocations, current, score, scan)


def _repair_reduction(
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    rules: _IncrementRules,
    scan: _StepScan,
) -> tuple[dict[str, int], PortfolioEvaluation, OptimizationDecision] | None:
    """Quita una unidad de las obligatorias cuando es la unica salida.

    Solo entra si la cartera ya incumple, o si faltan estrategias por abrir y el
    riesgo es lo que lo impide. Elige la reduccion minima que mejora el
    incumplimiento.
    """
    current_violation = _evaluation_violation_ratio(current)
    missing_required_strategy = (
        rules.minimum_active_strategies is not None
        and current.active_strategies < rules.minimum_active_strategies
    )
    if not (current_violation > 1.0 or (missing_required_strategy and scan.blocked_by_risk)):
        return None
    best_reduction: tuple[float, float, RobustStrategySet, dict[str, int], PortfolioEvaluation] | None = None
    for strategy in rules.sets:
        if strategy.set_id not in rules.fixed_ids or allocations.get(strategy.set_id, 0) <= 1:
            continue
        temp_allocations = allocations.copy()
        temp_allocations[strategy.set_id] -= 1
        temp = rules.evaluate(temp_allocations)
        temp_violation = _evaluation_violation_ratio(temp)
        if temp_violation >= current_violation - 1e-9:
            continue
        choice = (temp_violation, -temp.total_net_profit, strategy, temp_allocations, temp)
        if best_reduction is None or choice[:2] < best_reduction[:2]:
            best_reduction = choice
    if best_reduction is None:
        return None
    _violation, _negative_net, reduced_set, reduced_allocations, reduced_current = best_reduction
    decision = OptimizationDecision(
        step=len(scan.decision_log) + 1,
        action="reduce_unit_for_repair",
        set_id=reduced_set.set_id,
        from_set_id=reduced_set.set_id,
        to_set_id=None,
        gain=-reduced_set.net_profit_2020_2026_001,
        valley_cost=0.0,
        point_cost=0.0,
        score=-current_violation,
        portfolio_net_profit_after=reduced_current.total_net_profit,
        portfolio_valley_dd_after=reduced_current.valley_dd,
        portfolio_point_dd_after=reduced_current.point_dd,
        reason="Minimum existing-lot reduction required to make portfolio repair feasible",
    )
    return reduced_allocations, reduced_current, decision


def _greedy_stop_reason(blocks: dict[str, int]) -> str:
    """Quien bloqueo el paso, contado; no una cadena fija que culpe siempre al DD."""
    named = [
        f"{label} ({blocks[key]})"
        for key, label in (
            ("dd", "DD limits"),
            ("pair_corr", "correlation limits"),
            ("portfolio_corr", "portfolio correlation"),
            ("caps", "unit/group/margin caps"),
        )
        if blocks[key]
    ]
    if named:
        return "No valid +0.01 increment: " + "; ".join(named)
    return "No valid +0.01 increment left in the candidate pool"


def _added_unit_decision(
    selected_set: RobustStrategySet,
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    best_candidate: dict[str, object],
    step: int,
) -> OptimizationDecision:
    """La linea del registro que explica la unidad recien incorporada."""
    return OptimizationDecision(
        step=step,
        action="add_unit",
        set_id=selected_set.set_id,
        from_set_id=None,
        to_set_id=None,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=float(best_candidate["score"]),
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason=str(best_candidate.get("reason") or "Best valid +0.01 increment"),
    )


def _greedy_start(
    sets: list[RobustStrategySet],
    initial_allocations: dict[str, int] | None,
    rules: _IncrementRules,
) -> tuple[dict[str, int], PortfolioEvaluation]:
    """Asignacion de partida, validada contra los topes y contra el DD."""
    allocations = {
        strategy.set_id: max(int((initial_allocations or {}).get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    limits = rules.limits
    if not _allocations_respect_constraints(
        sets,
        allocations,
        limits.max_units_per_set,
        limits.max_total_units,
        limits.max_units_per_symbol,
        limits.max_sets_per_symbol,
        limits.max_sets_per_group,
        limits.margin_balance,
        limits.max_margin_pct,
        limits.margin_profile,
        limits.stock_leverage,
        limits.default_leverage,
        limits.stock_contract_size,
        limits.default_contract_size,
    ):
        raise ValueError("Initial portfolio allocations violate configured limits")
    current = rules.evaluate(allocations)
    if _evaluation_violates_dd_limits(current) and not rules.allow_fixed_reductions_for_repair:
        raise ValueError("Initial portfolio allocations violate DD limits")
    return allocations, current


def _scan_step(
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    rules: _IncrementRules,
    *,
    step: int,
    correlation_rejections: int,
    decision_log: list[OptimizationDecision],
    prefer_breadth_below_minimum: bool,
) -> _StepScan:
    """Prueba todas las candidatas del paso y devuelve lo que haya salido."""
    # Mientras faltan huecos por abrir, el objetivo es cuantas caben, no
    # cuanto rinde la siguiente. Eligiendo por rentabilidad se gasta la
    # holgura en la mejor candidata y las demas ya no entran, asi que el
    # resultado depende de lo gordo que sea el pool: darle mas candidatas
    # producia MENOS incorporaciones.
    scan = _StepScan(
        step=step,
        opening_slots=bool(
            prefer_breadth_below_minimum
            and rules.minimum_active_strategies is not None
            and current.active_strategies < rules.minimum_active_strategies
        ),
        decision_log=decision_log,
        correlation_rejections=correlation_rejections,
    )
    for strategy in rules.sets:
        _consider_increment(strategy, allocations, current, rules, scan)
    return scan


def _run_greedy_steps(
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    rules: _IncrementRules,
    *,
    prefer_breadth_below_minimum: bool,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
    """Un paso, una unidad, hasta que ninguna candidata cabe."""
    decision_log: list[OptimizationDecision] = []
    step = sum(allocations.values())
    max_steps = rules.limits.max_total_units
    if max_steps is None:
        max_steps = 10000
    correlation_rejections = 0
    while step < max_steps:
        scan = _scan_step(
            allocations, current, rules,
            step=step,
            correlation_rejections=correlation_rejections,
            decision_log=decision_log,
            prefer_breadth_below_minimum=prefer_breadth_below_minimum,
        )
        correlation_rejections = scan.correlation_rejections
        best_candidate = scan.best
        if best_candidate is None and scan.best_repair is not None:
            best_candidate = scan.best_repair
        if best_candidate is None and rules.allow_fixed_reductions_for_repair:
            reduction = _repair_reduction(allocations, current, rules, scan)
            if reduction is not None:
                allocations, current, decision = reduction
                step = sum(allocations.values())
                decision_log.append(decision)
                continue
        if best_candidate is None:
            stop_reason = _greedy_stop_reason(scan.blocks)
            break
        selected_set = best_candidate["set"]
        assert isinstance(selected_set, RobustStrategySet)
        previous = current
        allocations = best_candidate["allocations"]  # type: ignore[assignment]
        current = best_candidate["evaluation"]  # type: ignore[assignment]
        step += 1
        decision_log.append(
            _added_unit_decision(selected_set, previous, current, best_candidate, step)
        )
    else:
        stop_reason = "Max optimizer iterations reached"
    return allocations, current, decision_log, stop_reason, correlation_rejections


def build_portfolio_greedy(
    sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
    initial_allocations: dict[str, int] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    prefer_breadth_below_minimum: bool = False,
    fixed_set_ids: Sequence[str] | None = None,
    allow_fixed_reductions_for_repair: bool = False,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
    """Anade unidades de 0.01 una a una, siempre la mejor que cabe.

    Cada paso prueba todas las candidatas contra la cartera completa -topes,
    correlacion, margen y DD- y se queda con una. El bucle para cuando ninguna
    cabe, y el motivo lo decide el recuento de bloqueos de ese paso.
    """
    rules = _IncrementRules(
        sets=sets,
        portfolio_type=portfolio_type,
        target_valley_dd=capital * valley_dd_pct / 100.0,
        target_point_dd=capital * point_dd_pct / 100.0,
        limits=limits,
        minimum_active_strategies=minimum_active_strategies,
        maximum_active_strategies=maximum_active_strategies,
        allow_fixed_reductions_for_repair=allow_fixed_reductions_for_repair,
        fixed_ids={str(set_id) for set_id in (fixed_set_ids or ())},
        portfolio_curves=list(limits.existing_portfolio_curves or []),
    )
    allocations, current = _greedy_start(sets, initial_allocations, rules)
    return _run_greedy_steps(
        allocations, current, rules,
        prefer_breadth_below_minimum=prefer_breadth_below_minimum,
    )
