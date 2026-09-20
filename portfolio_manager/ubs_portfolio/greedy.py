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


def _swap_respects_caps(
    sets: list[RobustStrategySet],
    to_set: RobustStrategySet,
    temp_allocations: dict[str, int],
    limits: SearchLimits,
    minimum_active_strategies: int | None,
) -> bool:
    """Si la cartera resultante del intercambio sigue dentro de los topes."""
    if minimum_active_strategies is not None:
        active_count = sum(1 for units in temp_allocations.values() if units > 0)
        if active_count < minimum_active_strategies:
            return False
    if not _allocations_respect_constraints(
        sets,
        temp_allocations,
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
        return False
    return _target_group_units_pct_allowed(
        to_set,
        sets,
        temp_allocations,
        limits.max_units_per_group_pct,
        limits.group_unit_cap_bootstrap,
    )


def _swap_respects_correlation(
    sets: list[RobustStrategySet],
    to_set: RobustStrategySet,
    allocations: dict[str, int],
    temp_allocations: dict[str, int],
    limits: SearchLimits,
) -> bool:
    """La correlacion solo se comprueba si el intercambio estrena estrategia."""
    if allocations.get(to_set.set_id, 0) > 0:
        return True
    corr_allocations = temp_allocations.copy()
    corr_allocations[to_set.set_id] = 0
    rejected_by_corr, _corr_reason = violates_correlation_limits(
        to_set,
        sets,
        corr_allocations,
        limits.max_pair_corr,
        limits.max_downside_corr,
        limits.max_dd_overlap,
    )
    return not rejected_by_corr


def _portfolio_corr_allows(
    temp: PortfolioEvaluation,
    limits: SearchLimits,
    portfolio_curves: list[Sequence[float]],
) -> bool:
    """Si la curva resultante no se parece demasiado a una cartera existente."""
    if limits.max_portfolio_corr is None or not portfolio_curves:
        return True
    worst_portfolio_corr = max(
        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
        for curve in portfolio_curves
    )
    return worst_portfolio_corr <= limits.max_portfolio_corr


def _swap_candidate(
    from_set: RobustStrategySet,
    to_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    *,
    portfolio_curves: list[Sequence[float]],
    minimum_active_strategies: int | None,
    target_valley_dd: float,
    target_point_dd: float,
) -> dict[str, object] | None:
    """Mueve una unidad de una estrategia a otra. ``None`` si no vale la pena."""
    if from_set.set_id == to_set.set_id:
        return None
    temp_allocations = allocations.copy()
    temp_allocations[from_set.set_id] -= 1
    temp_allocations[to_set.set_id] += 1
    if not _swap_respects_caps(sets, to_set, temp_allocations, limits, minimum_active_strategies):
        return None
    if not _swap_respects_correlation(sets, to_set, allocations, temp_allocations, limits):
        return None
    temp = evaluate_portfolio(
        sets,
        temp_allocations,
        target_valley_dd,
        target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allows(temp, limits, portfolio_curves):
        return None
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 0:
        return None
    return {
        "from_set": from_set,
        "to_set": to_set,
        "allocations": temp_allocations,
        "evaluation": temp,
        "gain": gain,
    }


def _best_swap(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    *,
    protected_ids: set[str],
    portfolio_curves: list[Sequence[float]],
    minimum_active_strategies: int | None,
    target_valley_dd: float,
    target_point_dd: float,
) -> dict[str, object] | None:
    """El intercambio que mas beneficio gana sin romper ningun limite."""
    best_move: dict[str, object] | None = None
    for from_set in sets:
        if allocations.get(from_set.set_id, 0) <= 0:
            continue
        if from_set.set_id in protected_ids and allocations.get(from_set.set_id, 0) <= 1:
            continue
        for to_set in sets:
            move = _swap_candidate(
                from_set, to_set, sets, allocations, current, limits,
                portfolio_curves=portfolio_curves,
                minimum_active_strategies=minimum_active_strategies,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
            )
            if move is None:
                continue
            if best_move is None or float(move["gain"]) > float(best_move["gain"]):
                best_move = move
    return best_move


def _swap_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    step: int,
) -> OptimizationDecision:
    """La linea del registro que explica el intercambio aplicado."""
    from_set = best_move["from_set"]
    to_set = best_move["to_set"]
    assert isinstance(from_set, RobustStrategySet)
    assert isinstance(to_set, RobustStrategySet)
    return OptimizationDecision(
        step=step,
        action="swap_unit",
        set_id=None,
        from_set_id=from_set.set_id,
        to_set_id=to_set.set_id,
        gain=current.total_net_profit - previous.total_net_profit,
        valley_cost=current.valley_dd - previous.valley_dd,
        point_cost=current.point_dd - previous.point_dd,
        score=current.total_net_profit - previous.total_net_profit,
        portfolio_net_profit_after=current.total_net_profit,
        portfolio_valley_dd_after=current.valley_dd,
        portfolio_point_dd_after=current.point_dd,
        reason="Local search improved total net profit",
    )


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    max_iterations: int = 1000,
    protected_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    decision_log: list[OptimizationDecision] = []
    portfolio_curves = list(limits.existing_portfolio_curves or [])
    protected_ids = {str(set_id) for set_id in (protected_set_ids or ())}
    for iteration in range(1, max_iterations + 1):
        best_move = _best_swap(
            sets, allocations, current, limits,
            protected_ids=protected_ids,
            portfolio_curves=portfolio_curves,
            minimum_active_strategies=minimum_active_strategies,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
        )
        if best_move is None:
            break
        previous = current
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(_swap_decision(best_move, previous, current, iteration))
    return allocations, current, decision_log


def _perturbation_move(
    source: RobustStrategySet,
    target: RobustStrategySet,
    sets: list[RobustStrategySet],
    trial_allocations: dict[str, int],
    limits: SearchLimits,
    *,
    portfolio_curves: list[Sequence[float]],
    target_valley_dd: float,
    target_point_dd: float,
) -> tuple[dict[str, int], PortfolioEvaluation] | None:
    """Movimiento de perturbacion valido. A diferencia de la busqueda local, no
    se le exige mejorar: la gracia es salir del optimo local."""
    temp_allocations = trial_allocations.copy()
    temp_allocations[source.set_id] -= 1
    temp_allocations[target.set_id] += 1
    if not _swap_respects_caps(sets, target, temp_allocations, limits, None):
        return None
    if not _swap_respects_correlation(sets, target, trial_allocations, temp_allocations, limits):
        return None
    temp = evaluate_portfolio(
        sets,
        temp_allocations,
        target_valley_dd,
        target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allows(temp, limits, portfolio_curves):
        return None
    return temp_allocations, temp


def _perturb_decision(
    source: RobustStrategySet,
    target: RobustStrategySet,
    trial: PortfolioEvaluation,
    temp: PortfolioEvaluation,
    perturbation: int,
    restart: int,
) -> OptimizationDecision:
    """La linea del registro que explica una perturbacion aceptada."""
    return OptimizationDecision(
        step=perturbation + 1,
        action="multi_start_perturb",
        set_id=None,
        from_set_id=source.set_id,
        to_set_id=target.set_id,
        gain=temp.total_net_profit - trial.total_net_profit,
        valley_cost=temp.valley_dd - trial.valley_dd,
        point_cost=temp.point_dd - trial.point_dd,
        score=temp.total_net_profit - trial.total_net_profit,
        portfolio_net_profit_after=temp.total_net_profit,
        portfolio_valley_dd_after=temp.valley_dd,
        portfolio_point_dd_after=temp.point_dd,
        reason=f"Multi-start perturbation {restart + 1}",
    )


def _perturbed_trial(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    rng: random.Random,
    *,
    perturbations: int,
    restart: int,
    portfolio_curves: list[Sequence[float]],
    target_valley_dd: float,
    target_point_dd: float,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    """Sacude la cartera con movimientos validos hasta que ninguno lo sea."""
    trial_allocations = allocations.copy()
    trial = current
    perturb_log: list[OptimizationDecision] = []
    for perturbation in range(perturbations):
        active = [item for item in sets if trial_allocations.get(item.set_id, 0) > 0]
        moves = [
            (source, target)
            for source in active for target in sets
            if source.set_id != target.set_id
        ]
        rng.shuffle(moves)
        accepted_move = False
        for source, target in moves:
            moved = _perturbation_move(
                source, target, sets, trial_allocations, limits,
                portfolio_curves=portfolio_curves,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
            )
            if moved is None:
                continue
            temp_allocations, temp = moved
            perturb_log.append(
                _perturb_decision(source, target, trial, temp, perturbation, restart)
            )
            trial_allocations, trial = temp_allocations, temp
            accepted_move = True
            break
        if not accepted_move:
            break
    return trial_allocations, trial, perturb_log


def improve_with_multi_start_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    *,
    restarts: int,
    perturbations: int = 2,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    if restarts <= 0 or perturbations <= 0 or len(sets) < 2:
        return allocations, current, [], 0

    best_allocations = allocations.copy()
    best = current
    best_log: list[OptimizationDecision] = []
    valid_restarts = 0
    portfolio_curves = list(limits.existing_portfolio_curves or [])

    for restart in range(restarts):
        trial_allocations, trial, perturb_log = _perturbed_trial(
            sets, allocations, current, limits,
            random.Random(104729 + restart * 7919 + len(sets) * 17),
            perturbations=perturbations,
            restart=restart,
            portfolio_curves=portfolio_curves,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
        )
        if not perturb_log:
            continue
        valid_restarts += 1
        trial_allocations, trial, local_log = improve_with_local_search(
            sets=sets,
            allocations=trial_allocations,
            current=trial,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            limits=limits,
            max_iterations=200,
        )
        if trial.total_net_profit > best.total_net_profit + 1e-9:
            best_allocations = trial_allocations
            best = trial
            best_log = perturb_log + local_log

    return best_allocations, best, best_log, valid_restarts


@dataclass
class _DeepScan:
    """El mejor movimiento profundo encontrado y cuantos se han probado."""

    best: dict[str, object] | None = None
    attempts: int = 0

    def offer(self, move: dict[str, object]) -> None:
        """Se queda con el movimiento si gana mas que el actual."""
        if self.best is None or float(move["gain"]) > float(self.best["gain"]):
            self.best = move


def _deep_gain(
    temp: PortfolioEvaluation,
    current: PortfolioEvaluation,
    limits: SearchLimits,
) -> float | None:
    """Cuanto gana el movimiento, o ``None`` si no es valido.

    La optimizacion profunda solo acepta mejoras reales que ademas respeten DD y
    correlacion de cartera; no se relaja ninguno de los dos para ganar mas.
    """
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 1e-9:
        return None
    if _evaluation_violates_dd_limits(temp):
        return None
    if not _portfolio_corr_allowed(temp, limits.existing_portfolio_curves, limits.max_portfolio_corr):
        return None
    return gain


def _deep_add_move(
    target: RobustStrategySet,
    working_sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    scan: _DeepScan,
) -> None:
    """Prueba anadir una unidad a la candidata."""
    if not _caps_allow(working_sets, target, allocations, limits):
        return
    if allocations.get(target.set_id, 0) <= 0:
        rejected_by_corr, _reason = violates_correlation_limits(
            target,
            working_sets,
            allocations,
            limits.max_pair_corr,
            limits.max_downside_corr,
            limits.max_dd_overlap,
        )
        if rejected_by_corr:
            return
    temp_allocations = allocations.copy()
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    temp = evaluate_portfolio(
        working_sets,
        temp_allocations,
        current.target_valley_dd,
        current.target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    gain = _deep_gain(temp, current, limits)
    if gain is None:
        return
    scan.offer({
        "action": "deep_add_unit",
        "from_set": None,
        "to_set": target,
        "allocations": temp_allocations,
        "evaluation": temp,
        "gain": gain,
    })


def _deep_swap_move(
    source: RobustStrategySet,
    target: RobustStrategySet,
    working_sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    minimum_active_strategies: int | None,
    scan: _DeepScan,
) -> None:
    """Prueba mover una unidad de una estrategia activa a la candidata."""
    temp_allocations = allocations.copy()
    temp_allocations[source.set_id] -= 1
    temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
    if (
        minimum_active_strategies is not None
        and _portfolio_active_count(temp_allocations) < minimum_active_strategies
    ):
        return
    if not _swap_respects_caps(working_sets, target, temp_allocations, limits, None):
        return
    if not _swap_respects_correlation(working_sets, target, allocations, temp_allocations, limits):
        return
    temp = evaluate_portfolio(
        working_sets,
        temp_allocations,
        current.target_valley_dd,
        current.target_point_dd,
        limits.max_daily_dd,
        limits.enforce_point_dd,
        limits.daily_dd_full_history,
    )
    gain = _deep_gain(temp, current, limits)
    if gain is None:
        return
    scan.offer({
        "action": "deep_swap_unit",
        "from_set": source,
        "to_set": target,
        "allocations": temp_allocations,
        "evaluation": temp,
        "gain": gain,
    })


def _best_deep_move(
    working_sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
    minimum_active_strategies: int | None,
) -> _DeepScan:
    """Recorre las candidatas por puntuacion y se queda con el mejor movimiento."""
    scan = _DeepScan()
    ordered_targets = sorted(
        working_sets,
        key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
        reverse=True,
    )
    for target in ordered_targets:
        scan.attempts += 1
        _deep_add_move(target, working_sets, allocations, current, limits, scan)
        active_sources = [
            source for source in working_sets if allocations.get(source.set_id, 0) > 0
        ]
        for source in active_sources:
            if source.set_id == target.set_id:
                continue
            scan.attempts += 1
            _deep_swap_move(
                source, target, working_sets, allocations, current, limits,
                minimum_active_strategies, scan,
            )
    return scan


def _deep_decision(
    best_move: dict[str, object],
    previous: PortfolioEvaluation,
    current: PortfolioEvaluation,
    iteration: int,
) -> OptimizationDecision:
    """La linea del registro que explica el movimiento profundo aplicado."""
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
        reason="Optimizacion profunda: movimiento validado contra DD, margen y correlacion",
    )


def _deep_refine_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    minimum_active_strategies: int | None,
    max_iterations: int = 160,
    limits: SearchLimits = SearchLimits(),
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    working_sets = list({strategy.set_id: strategy for strategy in sets}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in working_sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0
    for iteration in range(1, max_iterations + 1):
        scan = _best_deep_move(
            working_sets, allocations, current, limits, minimum_active_strategies,
        )
        attempts += scan.attempts
        if scan.best is None:
            break
        previous = current
        allocations = scan.best["allocations"]  # type: ignore[assignment]
        current = scan.best["evaluation"]  # type: ignore[assignment]
        decision_log.append(_deep_decision(scan.best, previous, current, iteration))
    return allocations, current, decision_log, attempts


def _active_unit_allocations(allocations: dict[str, int]) -> dict[str, int]:
    return {str(set_id): int(units) for set_id, units in allocations.items() if int(units) > 0}
