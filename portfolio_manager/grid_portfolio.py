"""Grid-specific UBS portfolio optimization.

The shared UBS optimizer supplies selection, correlation, closed-curve and
margin primitives. Grid portfolios dimension every strategy in executable lot
steps and bind the result only by the greater of the combined closed DD and the
worst standalone floating DD. Internal EA loss limits never replace that
portfolio-level rule.
"""

from __future__ import annotations

from dataclasses import replace
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

    def margin_of(candidate: dict[str, int]) -> float:
        return float(peak_margin_for(candidate)) if peak_margin_for else 0.0

    def margin_exceeded(value: float) -> bool:
        return peak_margin_limit is not None and value > float(peak_margin_limit) + 1e-9

    margin = margin_of(current)
    decisions: list[OptimizationDecision] = []
    removed: list[str] = []
    step = 1
    while current and (valley > target_valley_dd + 1e-9 or margin_exceeded(margin)):
        valley_binds = valley > target_valley_dd + 1e-9
        choices: list[tuple[float, float, str, int, Any, float, float, float]] = []
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
            trial_margin = margin_of(trial)
            relief = valley - trial_valley if valley_binds else margin - trial_margin
            lost_profit = evaluation.total_net_profit - trial_eval.total_net_profit
            score = lost_profit / relief if relief > 1e-9 else float("inf")
            choices.append((
                score, lost_profit, set_id, next_units,
                trial_eval, trial_floating, trial_valley, trial_margin,
            ))
        (
            score, lost_profit, set_id, next_units,
            trial_eval, trial_floating, trial_valley, trial_margin,
        ) = min(choices, key=lambda item: (item[0], item[1], item[2]))
        if next_units > 0:
            current[set_id] = next_units
        else:
            current.pop(set_id)
            removed.append(set_id)
        decisions.append(OptimizationDecision(
            step=step,
            action="reduce_for_grid_valley" if next_units > 0 else "remove_for_grid_valley",
            set_id=set_id,
            from_set_id=set_id,
            to_set_id=None,
            gain=-float(lost_profit),
            valley_cost=float(trial_valley - valley),
            point_cost=float(trial_eval.point_dd - evaluation.point_dd),
            score=0.0 if score == float("inf") else -float(score),
            portfolio_net_profit_after=float(trial_eval.total_net_profit),
            portfolio_valley_dd_after=float(trial_valley),
            portfolio_point_dd_after=float(trial_eval.point_dd),
            reason=(
                "Reduccion de una unidad para respetar el margen de pico del grid."
                if not valley_binds and next_units > 0
                else "Retirada para respetar el margen de pico del grid."
                if not valley_binds
                else "Reduccion de una unidad para respetar el valle grid del portafolio."
                if next_units > 0
                else "Retirada para respetar el valle grid del portafolio."
            ),
        ))
        evaluation, floating_max, valley = trial_eval, trial_floating, trial_valley
        margin = trial_margin
        step += 1
    if not current:
        raise ValueError(
            "Ninguna estrategia grid respeta el valle máximo entre DD flotante y DD cerrado"
        )
    return current, evaluation, floating_max, decisions, removed


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
    optimizer_kwargs = dict(kwargs)
    optimizer_kwargs.update({
        "enforce_point_dd": False,
        "max_daily_dd": None,
    })
    # El agresivo puntúa por ganancia absoluta (70%) en el optimizador
    # compartido. Con la regla `max()` del valle Grid eso es contraproducente:
    # añadir un set cuyo flotante queda por debajo del máximo vigente no cuesta
    # nada, así que el criterio de eficiencia recoge más beneficio con el mismo
    # riesgo. Se le deja su identidad -- reserva menor y más concentración por
    # grupo -- pero se selecciona por eficiencia. Los límites de grupo viajan
    # explícitos para no heredar los de la variante que puntúa.
    group_limits = group_limits_for_portfolio_type(portfolio_type)
    optimizer_kwargs.setdefault("max_units_per_group_pct", group_limits.max_units_pct)
    optimizer_kwargs.setdefault("max_sets_per_group", group_limits.max_sets)
    # Sembrar 5 unidades de golpe multiplica por 5 el flotante de ese set antes
    # de que nadie mida nada. En Grid la siembra se queda en 2.
    optimizer_kwargs.setdefault(
        "group_unit_cap_bootstrap", min(int(group_limits.bootstrap_units), 2)
    )
    selection_type = (
        PortfolioType.BALANCED
        if portfolio_type == PortfolioType.AGGRESSIVE
        else portfolio_type
    )
    # The shared evaluator already takes max(closed DD, floating buffer). Feed
    # it the Grid-specific floating measurement without changing UBS/monthly.
    risk_sets = [
        replace(
            strategy,
            max_floating_dd_001=grid_floating_dd_001(strategy),
            # Four-stage acceptance is the Grid quality gate. Do not add the
            # shared UBS recent-recovery rejection as a hidden fifth filter.
            has_recent_performance=False,
        )
        for strategy in raw_sets
    ]
    reserve_pct = min(max(float(optimizer_kwargs.get("dd_reserve_pct", 0.0)), 0.0), 99.0)
    target_grid_valley = (
        float(capital) * float(valley_dd_pct) / 100.0 * (1.0 - reserve_pct / 100.0)
    )
    # Candidate ranking must not evict the low-DD set before construction. This
    # was leaving an empty result even when a valid Grid strategy existed.
    risk_sets = [
        strategy for strategy in risk_sets
        if float(strategy.max_floating_dd_001) <= target_grid_valley + 1e-9
    ]
    if not risk_sets:
        raise ValueError(
            "Ninguna estrategia grid respeta el valle máximo entre DD flotante y DD cerrado"
        )
    result = optimize_portfolio(
        risk_sets,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
        portfolio_type=selection_type,
        **optimizer_kwargs,
    )
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
    by_id = {strategy.set_id: strategy for strategy in raw_sets}

    def peak_margin_for(candidate: dict[str, int]) -> float:
        """Margen del pico simultáneo de la escalera, no de una sola posición."""
        total = 0.0
        for set_id, units in candidate.items():
            strategy = by_id.get(set_id)
            count = max(int(units), 0)
            if strategy is None or count <= 0:
                continue
            nominal = allocation_margin_required(
                strategy, count,
                margin_profile=optimizer_kwargs.get("margin_profile", "roboforex"),
                stock_leverage=float(optimizer_kwargs.get("stock_leverage", 20.0)),
                default_leverage=float(optimizer_kwargs.get("default_leverage", 500.0)),
                stock_contract_size=float(optimizer_kwargs.get("stock_contract_size", 100.0)),
                default_contract_size=float(optimizer_kwargs.get("default_contract_size", 1.0)),
            )
            exposure = exposure_model.exposures.get(set_id)
            total += nominal * (exposure.peak_exposure_ratio if exposure else 1.0)
        return total

    allocations, evaluation, floating_max, decisions, removed = _prune_to_grid_valley(
        raw_sets,
        initial,
        result.target_valley_dd,
        result.target_point_dd,
        model=exposure_model,
        peak_margin_for=peak_margin_for if peak_margin_limit is not None else None,
        peak_margin_limit=peak_margin_limit,
    )
    exposure_audit = exposure_model.audit(allocations)
    original_by_id = {strategy.set_id: strategy for strategy in raw_sets}
    kept_allocations = []
    for allocation in result.allocations:
        if allocation.set_id not in allocations:
            continue
        original = original_by_id[allocation.set_id]
        units = int(allocations[allocation.set_id])
        original_units = max(int(allocation.units), 1)
        unit_lot = float(allocation.lot) / original_units
        kept_allocations.append(replace(
            allocation,
            units=units,
            lot=unit_lot * units,
            net_profit_contribution=float(original.net_profit_2020_2026_001) * units,
            standalone_valley_dd=max(
                float(original.valley_dd_2020_2026_001),
                grid_floating_dd_001(original),
            ) * units,
            standalone_point_dd=float(original.point_dd_2020_2026_001) * units,
            margin_required=float(allocation.margin_required) / original_units * units,
            margin_pct=float(allocation.margin_pct) / original_units * units,
            max_balance_dd_001=float(original.max_balance_dd_001),
            max_equity_dd_001=float(original.max_equity_dd_001),
            floating_dd_source=str(original.floating_dd_source),
            standalone_floating_dd=grid_floating_dd_001(original) * units,
        ))
    warnings = [
        warning for warning in result.warnings
        if "flotante maximo individual" not in warning
        and "DD diario visual" not in warning
    ]
    warnings.append(
        "Valle Grid UBS = max(DD flotante máximo "
        f"{floating_max:.2f}, DD cerrado combinado {evaluation.closed_valley_dd:.2f}) "
        f"= {max(floating_max, float(evaluation.closed_valley_dd)):.2f}."
    )
    if exposure_audit["measured_days"]:
        warnings.append(
            "Exposición abierta medida día a día: peor día "
            f"{exposure_audit['worst_day']} con {exposure_audit['measured_open_exposure']:.2f} "
            f"entre {exposure_audit['coincident_sets']} estrategia(s) simultáneas; "
            f"flotante declarado por la peor estrategia {exposure_audit['declared_floating_dd']:.2f}."
        )
    warnings.append(
        "Los límites internos de pérdida o equity del EA no se usan para aceptar, rechazar ni dimensionar estrategias."
    )
    if removed:
        warnings.append(
            f"Se retiraron {len(removed)} estrategia(s) para que el valle grid agregado cupiera en el límite."
        )
    margin_summary: dict[str, object] = {}
    peak_margin: dict[str, object] = {}
    if optimizer_kwargs.get("margin_balance") is not None and optimizer_kwargs.get("max_margin_pct") is not None:
        margin_summary = portfolio_margin_summary(
            raw_sets,
            allocations,
            balance=float(optimizer_kwargs["margin_balance"]),
            max_margin_pct=float(optimizer_kwargs["max_margin_pct"]),
            margin_profile=optimizer_kwargs.get("margin_profile"),
            stock_leverage=float(optimizer_kwargs.get("stock_leverage", 20.0)),
            default_leverage=float(optimizer_kwargs.get("default_leverage", 500.0)),
            stock_contract_size=float(optimizer_kwargs.get("stock_contract_size", 100.0)),
            default_contract_size=float(optimizer_kwargs.get("default_contract_size", 1.0)),
        )
        peak_margin = peak_margin_summary(
            margin_summary,
            exposure_model,
            balance=float(optimizer_kwargs["margin_balance"]),
            max_margin_pct=float(optimizer_kwargs["max_margin_pct"]),
        )
        peak_lots = portfolio_peak_lots(exposure_model, allocations)
        warnings.append(
            f"Margen de pico del grid {peak_margin['total']:.2f}/{peak_margin['limit']:.2f} "
            f"({peak_margin['usage_pct']:.1f}% del límite) con hasta {peak_lots:.2f} lotes "
            f"abiertos a la vez; el margen nominal de una posición por unidad era "
            f"{margin_summary.get('total', 0.0):.2f}."
        )
        if peak_margin.get("exceeds_limit"):
            warnings.append(
                "ALERTA de margen: la escalera abierta del grid supera el límite configurado."
            )
    actual_valley = max(float(evaluation.closed_valley_dd), float(floating_max))
    # El bootstrap del proyecto estresa la curva de operaciones cerradas. En un
    # grid ésa es la cara amable: hay que restarle la exposición abierta de cada
    # día para estresar algo parecido a la equity que ve la cuenta.
    stress = bootstrap_valley_drawdown(
        open_equity_curve(raw_sets, allocations, exposure_model, daily_pnl_series)
        or evaluation.equity_curve_2020_2026,
        nominal_valley_dd_limit=float(capital) * float(valley_dd_pct) / 100.0,
        effective_valley_dd_limit=result.target_valley_dd,
    )
    removed_unused = [
        UnusedSetInfo(set_id=set_id, symbol="", score=0.0, reason="removed_for_grid_valley")
        for set_id in removed
    ]
    return replace(
        result,
        allocations=kept_allocations,
        equity_curve_2020_2026=evaluation.equity_curve_2020_2026,
        total_net_profit=float(evaluation.total_net_profit),
        actual_valley_dd=actual_valley,
        actual_closed_valley_dd=float(evaluation.closed_valley_dd),
        floating_dd_buffer=floating_max,
        actual_point_dd=float(evaluation.point_dd),
        valley_usage_pct=actual_valley / result.target_valley_dd * 100.0 if result.target_valley_dd > 0 else 0.0,
        point_usage_pct=float(evaluation.point_usage_pct),
        total_lot=round(sum(allocation.lot for allocation in kept_allocations), 2),
        total_units=sum(allocations.values()),
        active_strategies=len(kept_allocations),
        stop_reason=result.stop_reason + "; validación de valle grid conservador",
        warnings=warnings,
        decision_log=result.decision_log + decisions,
        unused_sets=result.unused_sets + removed_unused,
        group_summary=portfolio_group_summary(raw_sets, allocations),
        stress_bootstrap=stress,
        margin_summary=margin_summary,
        max_daily_dd=0.0,
        target_daily_dd=None,
        # Grid no usa el tope de DD diario, así que su hueco de resumen queda
        # libre para las dos medidas que sí son suyas y que salen de la misma
        # serie de posiciones abiertas. Es el único contenedor que sobrevive a
        # la serialización sin tocar el dataclass compartido.
        daily_dd_summary={
            "grid_open_exposure": exposure_audit,
            "grid_peak_margin": peak_margin,
            "grid_peak_lots": portfolio_peak_lots(exposure_model, allocations),
        },
        daily_dd_full_history=False,
        enforce_point_dd=False,
    )
