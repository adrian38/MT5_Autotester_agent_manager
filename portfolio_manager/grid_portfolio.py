"""Grid-specific UBS portfolio optimization.

The shared UBS optimizer supplies selection, correlation, closed-curve and
margin primitives. Grid portfolios dimension every strategy in executable lot
steps and bind the result only by the greater of the combined closed DD and the
worst standalone floating DD. Internal EA loss limits never replace that
portfolio-level rule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from .grid_risk import (
    GridExposureModel,
    open_equity_curve,
    peak_margin_summary,
    portfolio_peak_lots,
)
from .ubs_portfolio import (
    OptimizationDecision,
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
    UnusedSetInfo,
    allocation_margin_required,
    bootstrap_valley_drawdown,
    daily_pnl_series,
    evaluate_portfolio,
    group_limits_for_portfolio_type,
    optimize_portfolio,
    portfolio_group_summary,
    portfolio_margin_summary,
)


def grid_floating_dd_001(strategy: RobustStrategySet) -> float:
    """Return the open/floating part of equity DD for one tested 0.01 unit."""
    return max(
        float(strategy.max_equity_dd_001) - float(strategy.max_balance_dd_001),
        0.0,
    )


def _grid_evaluation(
    strategies: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
    *,
    model: GridExposureModel | None = None,
) -> tuple[Any, float, float]:
    """Evalúa una asignación Grid: la regla sigue siendo max(flotante, cerrado).

    Lo que cambia frente a la primera versión es el término flotante. Antes era
    el máximo entre estrategias, que da por hecho que sólo una está bajo el agua
    en cada momento. Ahora es lo que midan los días: la exposición abierta
    agregada del peor día, con el máximo declarado como suelo para no quedar por
    debajo cuando un set llega sin operaciones legibles.
    """
    evaluation = evaluate_portfolio(
        strategies,
        allocations,
        target_valley_dd,
        target_point_dd,
        enforce_point_dd=False,
    )
    exposure = model or GridExposureModel(strategies)
    floating_max = exposure.floating(allocations)
    valley = max(float(evaluation.closed_valley_dd), float(floating_max))
    return evaluation, float(floating_max), valley


@dataclass(frozen=True)
class _GridPruneChoice:
    score: float
    lost_profit: float
    set_id: str
    next_units: int
    evaluation: Any
    floating: float
    valley: float
    margin: float


def _best_grid_prune_choice(
    strategies: list[RobustStrategySet],
    current: dict[str, int],
    evaluation: Any,
    valley: float,
    margin: float,
    target_valley_dd: float,
    target_point_dd: float,
    exposure: GridExposureModel,
    peak_margin_for: Callable[[dict[str, int]], float] | None,
) -> _GridPruneChoice:
    valley_binds = valley > target_valley_dd + 1e-9
    choices = []
    for set_id in current:
        trial = dict(current)
        next_units = trial[set_id] - 1
        if next_units > 0:
            trial[set_id] = next_units
        else:
            trial.pop(set_id)
        trial_eval, trial_floating, trial_valley = _grid_evaluation(
            strategies, trial, target_valley_dd, target_point_dd, model=exposure,
        )
        trial_margin = float(peak_margin_for(trial)) if peak_margin_for else 0.0
        relief = valley - trial_valley if valley_binds else margin - trial_margin
        lost_profit = evaluation.total_net_profit - trial_eval.total_net_profit
        score = lost_profit / relief if relief > 1e-9 else float("inf")
        choices.append(_GridPruneChoice(
            score, lost_profit, set_id, next_units, trial_eval,
            trial_floating, trial_valley, trial_margin,
        ))
    return min(choices, key=lambda item: (item.score, item.lost_profit, item.set_id))


def _grid_prune_decision(
    choice: _GridPruneChoice,
    *,
    step: int,
    valley_binds: bool,
    prior_valley: float,
    prior_point_dd: float,
) -> OptimizationDecision:
    if not valley_binds:
        reason = (
            "Reduccion de una unidad para respetar el margen de pico del grid."
            if choice.next_units > 0
            else "Retirada para respetar el margen de pico del grid."
        )
    else:
        reason = (
            "Reduccion de una unidad para respetar el valle grid del portafolio."
            if choice.next_units > 0
            else "Retirada para respetar el valle grid del portafolio."
        )
    return OptimizationDecision(
        step=step,
        action="reduce_for_grid_valley" if choice.next_units > 0 else "remove_for_grid_valley",
        set_id=choice.set_id,
        from_set_id=choice.set_id,
        to_set_id=None,
        gain=-float(choice.lost_profit),
        valley_cost=float(choice.valley - prior_valley),
        point_cost=float(choice.evaluation.point_dd - prior_point_dd),
        score=0.0 if choice.score == float("inf") else -float(choice.score),
        portfolio_net_profit_after=float(choice.evaluation.total_net_profit),
        portfolio_valley_dd_after=float(choice.valley),
        portfolio_point_dd_after=float(choice.evaluation.point_dd),
        reason=reason,
    )


def _prune_to_grid_valley(
    strategies: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
    *,
    model: GridExposureModel | None = None,
    peak_margin_for: Callable[[dict[str, int]], float] | None = None,
    peak_margin_limit: float | None = None,
) -> tuple[dict[str, int], Any, float, list[OptimizationDecision], list[str]]:
    """Recorta unidades hasta que la asignación cabe en valle y en margen.

    El margen entra aquí porque en un grid no es una comprobación cosmética: la
    escalera abre varias piernas a la vez y el margen del pico simultáneo es el
    que decide si el bróker liquida la cuenta. Cuando el valle ya cabe y el que
    aprieta es el margen, el alivio se mide en margen; si no, en valle.
    """
    exposure = model or GridExposureModel(strategies)
    current = {
        set_id: max(int(units), 0)
        for set_id, units in allocations.items()
        if int(units) > 0
    }
    evaluation, floating_max, valley = _grid_evaluation(
        strategies, current, target_valley_dd, target_point_dd, model=exposure,
    )

    margin = float(peak_margin_for(current)) if peak_margin_for else 0.0
    decisions: list[OptimizationDecision] = []
    removed: list[str] = []
    step = 1
    while current and (
        valley > target_valley_dd + 1e-9
        or peak_margin_limit is not None and margin > float(peak_margin_limit) + 1e-9
    ):
        valley_binds = valley > target_valley_dd + 1e-9
        choice = _best_grid_prune_choice(
            strategies, current, evaluation, valley, margin, target_valley_dd,
            target_point_dd, exposure, peak_margin_for,
        )
        if choice.next_units > 0:
            current[choice.set_id] = choice.next_units
        else:
            current.pop(choice.set_id)
            removed.append(choice.set_id)
        decisions.append(_grid_prune_decision(
            choice,
            step=step,
            valley_binds=valley_binds,
            prior_valley=valley,
            prior_point_dd=evaluation.point_dd,
        ))
        evaluation = choice.evaluation
        floating_max, valley, margin = choice.floating, choice.valley, choice.margin
        step += 1
    if not current:
        raise ValueError(
            "Ninguna estrategia grid respeta el valle máximo entre DD flotante y DD cerrado"
        )
    return current, evaluation, floating_max, decisions, removed


@dataclass(frozen=True)
class _GridResultContext:
    raw_sets: list[RobustStrategySet]
    optimizer_kwargs: dict[str, Any]
    result: PortfolioResult
    allocations: dict[str, int]
    evaluation: Any
    floating_max: float
    decisions: list[OptimizationDecision]
    removed: list[str]
    exposure_model: GridExposureModel
    exposure_audit: dict[str, Any]
    capital: float
    valley_dd_pct: float


def _grid_search_inputs(
    raw_sets: list[RobustStrategySet],
    portfolio_type: PortfolioType,
    capital: float,
    valley_dd_pct: float,
    kwargs: dict[str, Any],
) -> tuple[list[RobustStrategySet], PortfolioType, dict[str, Any]]:
    optimizer_kwargs = dict(kwargs)
    optimizer_kwargs.update({"enforce_point_dd": False, "max_daily_dd": None})
    group_limits = group_limits_for_portfolio_type(portfolio_type)
    optimizer_kwargs.setdefault("max_units_per_group_pct", group_limits.max_units_pct)
    optimizer_kwargs.setdefault("max_sets_per_group", group_limits.max_sets)
    optimizer_kwargs.setdefault(
        "group_unit_cap_bootstrap", min(int(group_limits.bootstrap_units), 2)
    )
    selection_type = (
        PortfolioType.BALANCED
        if portfolio_type == PortfolioType.AGGRESSIVE
        else portfolio_type
    )
    risk_sets = [
        replace(
            strategy,
            max_floating_dd_001=grid_floating_dd_001(strategy),
            has_recent_performance=False,
        )
        for strategy in raw_sets
    ]
    reserve_pct = min(max(float(optimizer_kwargs.get("dd_reserve_pct", 0.0)), 0.0), 99.0)
    target = float(capital) * float(valley_dd_pct) / 100.0 * (1.0 - reserve_pct / 100.0)
    risk_sets = [
        strategy
        for strategy in risk_sets
        if float(strategy.max_floating_dd_001) <= target + 1e-9
    ]
    if not risk_sets:
        raise ValueError(
            "Ninguna estrategia grid respeta el valle máximo entre DD flotante y DD cerrado"
        )
    return risk_sets, selection_type, optimizer_kwargs


def _peak_margin_calculator(
    raw_sets: list[RobustStrategySet],
    exposure_model: GridExposureModel,
    optimizer_kwargs: dict[str, Any],
) -> Callable[[dict[str, int]], float]:
    by_id = {strategy.set_id: strategy for strategy in raw_sets}

    def peak_margin_for(candidate: dict[str, int]) -> float:
        total = 0.0
        for set_id, units in candidate.items():
            strategy = by_id.get(set_id)
            count = max(int(units), 0)
            if strategy is None or count <= 0:
                continue
            nominal = allocation_margin_required(
                strategy,
                count,
                margin_profile=optimizer_kwargs.get("margin_profile", "roboforex"),
                stock_leverage=float(optimizer_kwargs.get("stock_leverage", 20.0)),
                default_leverage=float(optimizer_kwargs.get("default_leverage", 500.0)),
                stock_contract_size=float(optimizer_kwargs.get("stock_contract_size", 100.0)),
                default_contract_size=float(optimizer_kwargs.get("default_contract_size", 1.0)),
            )
            exposure = exposure_model.exposures.get(set_id)
            total += nominal * (exposure.peak_exposure_ratio if exposure else 1.0)
        return total

    return peak_margin_for


def _pruned_grid_context(
    raw_sets: list[RobustStrategySet],
    result: PortfolioResult,
    optimizer_kwargs: dict[str, Any],
    capital: float,
    valley_dd_pct: float,
) -> _GridResultContext:
    initial = {
        allocation.set_id: int(allocation.units)
        for allocation in result.allocations
        if allocation.units > 0
    }
    exposure_model = GridExposureModel(raw_sets)
    margin_balance = optimizer_kwargs.get("margin_balance")
    max_margin_pct = optimizer_kwargs.get("max_margin_pct")
    peak_margin_limit = (
        float(margin_balance) * float(max_margin_pct) / 100.0
        if margin_balance is not None and max_margin_pct is not None
        else None
    )
    peak_margin_for = _peak_margin_calculator(raw_sets, exposure_model, optimizer_kwargs)
    allocations, evaluation, floating_max, decisions, removed = _prune_to_grid_valley(
        raw_sets,
        initial,
        result.target_valley_dd,
        result.target_point_dd,
        model=exposure_model,
        peak_margin_for=peak_margin_for if peak_margin_limit is not None else None,
        peak_margin_limit=peak_margin_limit,
    )
    return _GridResultContext(
        raw_sets, optimizer_kwargs, result, allocations, evaluation, floating_max,
        decisions, removed, exposure_model, exposure_model.audit(allocations),
        capital, valley_dd_pct,
    )


def _kept_grid_allocations(context: _GridResultContext) -> list[Any]:
    original_by_id = {strategy.set_id: strategy for strategy in context.raw_sets}
    kept = []
    for allocation in context.result.allocations:
        if allocation.set_id not in context.allocations:
            continue
        original = original_by_id[allocation.set_id]
        units = int(context.allocations[allocation.set_id])
        original_units = max(int(allocation.units), 1)
        kept.append(replace(
            allocation,
            units=units,
            lot=float(allocation.lot) / original_units * units,
            net_profit_contribution=float(original.net_profit_2020_2026_001) * units,
            standalone_valley_dd=max(
                float(original.valley_dd_2020_2026_001), grid_floating_dd_001(original),
            ) * units,
            standalone_point_dd=float(original.point_dd_2020_2026_001) * units,
            margin_required=float(allocation.margin_required) / original_units * units,
            margin_pct=float(allocation.margin_pct) / original_units * units,
            max_balance_dd_001=float(original.max_balance_dd_001),
            max_equity_dd_001=float(original.max_equity_dd_001),
            floating_dd_source=str(original.floating_dd_source),
            standalone_floating_dd=grid_floating_dd_001(original) * units,
        ))
    return kept


def _grid_result_warnings(context: _GridResultContext) -> list[str]:
    warnings = [
        warning for warning in context.result.warnings
        if "flotante maximo individual" not in warning and "DD diario visual" not in warning
    ]
    audit = context.exposure_audit
    warnings.append(
        "Valle Grid UBS = max(DD flotante máximo "
        f"{context.floating_max:.2f}, DD cerrado combinado "
        f"{context.evaluation.closed_valley_dd:.2f}) = "
        f"{max(context.floating_max, float(context.evaluation.closed_valley_dd)):.2f}."
    )
    if audit["measured_days"]:
        warnings.append(
            "Exposición abierta medida día a día: peor día "
            f"{audit['worst_day']} con {audit['measured_open_exposure']:.2f} entre "
            f"{audit['coincident_sets']} estrategia(s) simultáneas; flotante declarado "
            f"por la peor estrategia {audit['declared_floating_dd']:.2f}."
        )
    warnings.append(
        "Los límites internos de pérdida o equity del EA no se usan para aceptar, rechazar ni dimensionar estrategias."
    )
    if context.removed:
        warnings.append(
            f"Se retiraron {len(context.removed)} estrategia(s) para que el valle grid agregado cupiera en el límite."
        )
    return warnings


def _grid_margin_output(
    context: _GridResultContext,
    warnings: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    kwargs = context.optimizer_kwargs
    if kwargs.get("margin_balance") is None or kwargs.get("max_margin_pct") is None:
        return {}, {}
    margin_summary = portfolio_margin_summary(
        context.raw_sets,
        context.allocations,
        balance=float(kwargs["margin_balance"]),
        max_margin_pct=float(kwargs["max_margin_pct"]),
        margin_profile=kwargs.get("margin_profile"),
        stock_leverage=float(kwargs.get("stock_leverage", 20.0)),
        default_leverage=float(kwargs.get("default_leverage", 500.0)),
        stock_contract_size=float(kwargs.get("stock_contract_size", 100.0)),
        default_contract_size=float(kwargs.get("default_contract_size", 1.0)),
    )
    peak_margin = peak_margin_summary(
        margin_summary,
        context.exposure_model,
        balance=float(kwargs["margin_balance"]),
        max_margin_pct=float(kwargs["max_margin_pct"]),
    )
    peak_lots = portfolio_peak_lots(context.exposure_model, context.allocations)
    warnings.append(
        f"Margen de pico del grid {peak_margin['total']:.2f}/{peak_margin['limit']:.2f} "
        f"({peak_margin['usage_pct']:.1f}% del límite) con hasta {peak_lots:.2f} lotes "
        "abiertos a la vez; el margen nominal de una posición por unidad era "
        f"{margin_summary.get('total', 0.0):.2f}."
    )
    if peak_margin.get("exceeds_limit"):
        warnings.append("ALERTA de margen: la escalera abierta del grid supera el límite configurado.")
    return margin_summary, peak_margin


def _finalize_grid_result(context: _GridResultContext) -> PortfolioResult:
    kept = _kept_grid_allocations(context)
    warnings = _grid_result_warnings(context)
    margin_summary, peak_margin = _grid_margin_output(context, warnings)
    audit = context.exposure_audit
    actual_valley = max(float(context.evaluation.closed_valley_dd), context.floating_max)
    stress = bootstrap_valley_drawdown(
        open_equity_curve(
            context.raw_sets, context.allocations, context.exposure_model, daily_pnl_series,
        ) or context.evaluation.equity_curve_2020_2026,
        nominal_valley_dd_limit=context.capital * context.valley_dd_pct / 100.0,
        effective_valley_dd_limit=context.result.target_valley_dd,
    )
    removed_unused = [
        UnusedSetInfo(set_id=set_id, symbol="", score=0.0, reason="removed_for_grid_valley")
        for set_id in context.removed
    ]
    return replace(
        context.result,
        allocations=kept,
        equity_curve_2020_2026=context.evaluation.equity_curve_2020_2026,
        total_net_profit=float(context.evaluation.total_net_profit),
        actual_valley_dd=actual_valley,
        actual_closed_valley_dd=float(context.evaluation.closed_valley_dd),
        floating_dd_buffer=context.floating_max,
        actual_point_dd=float(context.evaluation.point_dd),
        valley_usage_pct=actual_valley / context.result.target_valley_dd * 100.0 if context.result.target_valley_dd > 0 else 0.0,
        point_usage_pct=float(context.evaluation.point_usage_pct),
        total_lot=round(sum(allocation.lot for allocation in kept), 2),
        total_units=sum(context.allocations.values()),
        active_strategies=len(kept),
        stop_reason=context.result.stop_reason + "; validación de valle grid conservador",
        warnings=warnings,
        decision_log=context.result.decision_log + context.decisions,
        unused_sets=context.result.unused_sets + removed_unused,
        group_summary=portfolio_group_summary(context.raw_sets, context.allocations),
        stress_bootstrap=stress,
        margin_summary=margin_summary,
        max_daily_dd=0.0,
        target_daily_dd=None,
        daily_dd_summary={
            "grid_open_exposure": audit,
            "grid_peak_margin": peak_margin,
            "grid_peak_lots": portfolio_peak_lots(context.exposure_model, context.allocations),
        },
        daily_dd_full_history=False,
        enforce_point_dd=False,
    )


def optimize_grid_portfolio(
    raw_sets: list[RobustStrategySet],
    *,
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
    **kwargs: Any,
) -> PortfolioResult:
    """Build a variable-size Grid portfolio constrained by max(floating DD, closed DD)."""
    risk_sets, selection_type, optimizer_kwargs = _grid_search_inputs(
        raw_sets, portfolio_type, capital, valley_dd_pct, kwargs,
    )
    result = optimize_portfolio(
        risk_sets,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
        portfolio_type=selection_type,
        **optimizer_kwargs,
    )
    return _finalize_grid_result(
        _pruned_grid_context(raw_sets, result, optimizer_kwargs, capital, valley_dd_pct)
    )
