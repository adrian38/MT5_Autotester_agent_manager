"""Orquestacion de optimize_portfolio y sus avisos."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
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
class _SearchLimits:
    """Los limites que todas las fases de busqueda reenvian intactos.

    Viajaban como veintidos argumentos repetidos en cinco llamadas casi
    identicas, y lo unico que de verdad cambiaba entre ellas -el tope por
    grupo- se perdia en el ruido. Agrupados, cada fase declara con
    ``kwargs(...)`` solo aquello en lo que se desvia.
    """

    max_units_per_set: int | None
    max_total_units: int | None
    max_units_per_symbol: int | None
    max_sets_per_symbol: int | None
    max_pair_corr: float | None
    max_downside_corr: float | None
    max_dd_overlap: float | None
    existing_portfolio_curves: Sequence[Sequence[float]] | None
    max_portfolio_corr: float | None
    max_units_per_group_pct: float | None
    max_sets_per_group: int | None
    group_unit_cap_bootstrap: int
    margin_balance: float | None
    max_margin_pct: float | None
    margin_profile: str | MarginModel | None
    stock_leverage: float
    default_leverage: float
    stock_contract_size: float
    default_contract_size: float
    max_daily_dd: float | None
    enforce_point_dd: bool
    daily_dd_full_history: bool

    def kwargs(self, **overrides: object) -> dict[str, object]:
        """Los limites como kwargs.

        Se copia campo a campo a proposito: ``asdict`` recorre en profundidad y
        convertiria un ``MarginModel`` en un diccionario.
        """
        data = {item.name: getattr(self, item.name) for item in fields(self)}
        data.update(overrides)
        return data


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
    limits: _SearchLimits,
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
        **limits.kwargs(),
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
            **limits.kwargs(),
        )
    return _SearchPass(
        allocations, current, greedy_log, local_log, stop_reason, rejections,
    )


def _relaxed_group_cap_pass(
    strict: _SearchPass,
    selected: list[RobustStrategySet],
    limits: _SearchLimits,
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
        replace(limits, max_units_per_group_pct=None),
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
    limits: _SearchLimits,
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
        **limits.kwargs(),
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
    limits: _SearchLimits,
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
        **limits.kwargs(),
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


def _margin_and_daily_summaries(
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: _SearchLimits,
) -> tuple[dict, dict[str, object]]:
    """Resumen de margen y resumen de DD diario, ambos opcionales."""
    margin_summary: dict = {}
    if limits.margin_balance is not None and limits.max_margin_pct is not None:
        margin_summary = portfolio_margin_summary(
            selected,
            allocations,
            balance=float(limits.margin_balance),
            max_margin_pct=float(limits.max_margin_pct),
            margin_profile=limits.margin_profile,
            stock_leverage=limits.stock_leverage,
            default_leverage=limits.default_leverage,
            stock_contract_size=limits.stock_contract_size,
            default_contract_size=limits.default_contract_size,
        )
    daily_dd_summary: dict[str, object] = {}
    if limits.max_daily_dd is not None:
        _daily_dd, daily_dd_summary = portfolio_daily_closed_floating_dd(
            selected,
            allocations,
            full_history=bool(limits.daily_dd_full_history),
        )
        limit = float(limits.max_daily_dd)
        daily_dd_summary["limit"] = limit
        daily_dd_summary["usage_pct"] = current.daily_dd / limit * 100.0 if limit > 0 else 0.0
    return margin_summary, daily_dd_summary


def _allocation_row(
    strategy: RobustStrategySet,
    units: int,
    *,
    capital: float,
    margin_row: dict,
    margin_balance: float | None,
    margin_model: MarginModel,
    executable_steps: dict[str, float],
) -> StrategyAllocation:
    """Una fila del resultado, con su margen y su lote ya resueltos."""
    margin_required = float(margin_row.get("margin", 0.0) or 0.0)
    return StrategyAllocation(
        set_id=strategy.set_id,
        candidate_id=strategy.candidate_id,
        symbol=strategy.symbol,
        units=units,
        lot=margin_model.lot_size_for(strategy.symbol, units),
        net_profit_contribution=strategy.net_profit_2020_2026_001 * units,
        standalone_valley_dd=max(
            strategy.valley_dd_2020_2026_001,
            strategy.max_floating_dd_001,
        ) * units,
        standalone_point_dd=strategy.point_dd_2020_2026_001 * units,
        timeframe=strategy.timeframe,
        set_path=strategy.set_path,
        is_report_path=strategy.is_report_path,
        oos_report_path=strategy.oos_report_path,
        lot_size_step=float(executable_steps.get(strategy.set_id, _lot_size_step(capital, units) or 0)),
        margin_required=margin_required,
        margin_pct=(
            margin_required / max(float(margin_balance or 0.0), 1e-9) * 100.0
            if margin_balance is not None
            else 0.0
        ),
        margin_leverage=float(margin_row.get("leverage", 0.0) or 0.0),
        margin_contract_size=float(margin_row.get("contract_size", 0.0) or 0.0),
        margin_price=float(margin_row.get("price", 0.0) or 0.0),
        max_balance_dd_001=strategy.max_balance_dd_001,
        max_equity_dd_001=strategy.max_equity_dd_001,
        floating_dd_source=strategy.floating_dd_source,
        standalone_floating_dd=strategy.max_floating_dd_001 * units,
        recent_net_profit_001=strategy.recent_net_profit_001,
        recent_equity_dd_001=strategy.recent_equity_dd_001,
        has_recent_performance=strategy.has_recent_performance,
        final_tick_report_path=strategy.final_tick_report_path,
        full_history_report_path=strategy.full_history_report_path,
    )


def _result_allocation_rows(
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    margin_summary: dict,
    limits: _SearchLimits,
    *,
    capital: float,
    executable_steps: dict[str, float],
) -> list[StrategyAllocation]:
    """Filas del resultado, ordenadas por unidades y aportacion."""
    margin_by_set = margin_summary.get("by_set", {}) if isinstance(margin_summary, dict) else {}
    margin_model = resolve_margin_model(limits.margin_profile)
    rows: list[StrategyAllocation] = []
    for strategy in selected:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            continue
        margin_row = margin_by_set.get(strategy.set_id, {}) if isinstance(margin_by_set, dict) else {}
        rows.append(
            _allocation_row(
                strategy,
                units,
                capital=capital,
                margin_row=margin_row if isinstance(margin_row, dict) else {},
                margin_balance=limits.margin_balance,
                margin_model=margin_model,
                executable_steps=executable_steps,
            )
        )
    rows.sort(key=lambda item: (item.units, item.net_profit_contribution), reverse=True)
    return rows


def _group_limit_overages(
    group_summary: dict, eligible_groups: set[str], max_units_per_group_pct: float | None,
) -> list[str]:
    """Grupos que quedaron por encima del tope tras optimizar."""
    if max_units_per_group_pct is None or len(eligible_groups) <= 1:
        return []
    limit_pct = max_units_per_group_pct * 100.0
    return [
        f"{group} {float(stats['unit_pct']):.1f}%"
        for group, stats in group_summary.items()
        if float(stats["unit_pct"]) > limit_pct + 0.1
    ]


def _group_floor_warning(
    configured: float | None,
    floor: float | None,
    applied: float | None,
    candidate_group_count: int,
) -> list[str]:
    """Aviso de que el tope por grupo se subio al minimo factible."""
    if configured is None or floor is None or applied is None:
        return []
    if float(applied) <= float(configured) + 1e-9:
        return []
    return [
        "Group unit cap adjusted to the feasible diversification floor: "
        f"{float(configured) * 100.0:.1f}% -> "
        f"{float(applied) * 100.0:.1f}% for "
        f"{candidate_group_count} available asset groups."
    ]


def _dd_composition_warnings(
    current: PortfolioEvaluation,
    dd_reserve_pct: float,
    floating_overlap_audit: dict,
) -> list[str]:
    """Como se compuso el DD aplicado y si varias curvas coinciden bajo el agua."""
    notes: list[str] = []
    if dd_reserve_pct > 0:
        notes.append(
            f"DD reserve {float(dd_reserve_pct):.1f}% applied; optimizer used reduced effective DD targets."
        )
    if current.floating_dd_buffer > 0:
        notes.append(
            "DD historico aplicado (2020-hoy + Final Tick 6M): max(DD cerrado "
            f"{current.closed_valley_dd:.2f}, flotante maximo individual "
            f"{current.floating_dd_buffer:.2f}) = {current.valley_dd:.2f}."
        )
    if floating_overlap_audit.get("overlap_detected") and floating_overlap_audit.get("exceeds_declared"):
        notes.append(
            f"{int(floating_overlap_audit['coincident_sets'])} de "
            f"{int(floating_overlap_audit['active_sets'])} estrategias coinciden bajo el agua el "
            f"{floating_overlap_audit['worst_day']}: exposicion agregada "
            f"{float(floating_overlap_audit['measured_aggregate']):.2f} frente a "
            f"{float(floating_overlap_audit['worst_single']):.2f} de la peor sola "
            f"(+{float(floating_overlap_audit['overlap_excess']):.2f}), por encima del flotante "
            f"aplicado {float(floating_overlap_audit['declared_floating_dd']):.2f}. "
            "Medida informativa; el riesgo aplicado sigue siendo el maximo individual."
        )
    return notes


def _recent_recovery_warning(raw_sets: list[RobustStrategySet]) -> list[str]:
    """Cuantas estrategias cayeron por recuperacion reciente insuficiente."""
    rejected = sum(
        1
        for strategy in raw_sets
        if strategy.has_recent_performance
        and strategy.recent_net_profit_001 / max(strategy.recent_equity_dd_001, 1.0)
        < MIN_RECENT_EQUITY_RECOVERY
    )
    if not rejected:
        return []
    return [
        f"{rejected} estrategia(s) excluida(s): recuperacion 6M "
        f"sobre DD de equity < {MIN_RECENT_EQUITY_RECOVERY:.1f}."
    ]


def _deep_refinement_warning(
    deep: _DeepRefinement, base_selected_count: int, selected_count: int,
) -> str:
    """Texto de la pasada profunda, se haya aplicado o no."""
    if deep.log:
        return (
            "Optimizacion profunda aplicada: "
            f"{len(deep.log)} movimiento(s), {deep.attempts} intento(s), "
            f"pool {base_selected_count}->{selected_count} candidato(s)."
        )
    pool_text = (
        f" pool {base_selected_count}->{deep.pool_count} candidato(s)"
        if deep.pool_expanded
        else f" pool {selected_count} candidato(s)"
    )
    return (
        "Optimizacion profunda: no encontro mejora valida "
        f"tras {deep.attempts} intento(s),{pool_text}."
    )


def _search_phase_warnings(
    *,
    search_restarts: int,
    valid_restarts: int,
    use_deep_refinement: bool,
    deep: _DeepRefinement,
    base_selected_count: int,
    selected_count: int,
    preserve_required_allocations: bool,
    required_ids: set[str],
    greedy_log: list[OptimizationDecision],
    group_cap_relaxed: bool,
) -> list[str]:
    """Que hizo cada fase de busqueda."""
    notes: list[str] = []
    if search_restarts > 0:
        notes.append(
            f"Multi-start search evaluated {valid_restarts}/{int(search_restarts)} valid restart(s)."
        )
    if use_deep_refinement:
        notes.append(_deep_refinement_warning(deep, base_selected_count, selected_count))
    if preserve_required_allocations and required_ids:
        repair_reductions = sum(
            1 for decision in greedy_log if decision.action == "reduce_unit_for_repair"
        )
        if repair_reductions:
            notes.append(
                f"Repair preserved every existing strategy and changed only {repair_reductions} existing unit(s) required by DD limits."
            )
        else:
            notes.append(
                "Existing portfolio strategies and units were preserved; only replacement allocations were optimized."
            )
    if group_cap_relaxed:
        notes.append(
            "Balanced relajo el limite porcentual por grupo porque la asignacion estricta dejaba el portfolio infrautilizado."
        )
    return notes


def _usage_warnings(
    current: PortfolioEvaluation,
    *,
    portfolio_type: PortfolioType,
    eligible_groups: set[str],
    enforce_point_dd: bool,
    result_allocations: list[StrategyAllocation],
    execution_adjustments: dict[str, int],
    execution_repair_log: list[OptimizationDecision],
    correlation_rejections: int,
    group_limit_overages: list[str],
    max_units_per_group_pct: float | None,
) -> list[str]:
    """Diversificacion alcanzada, uso de los limites y ajustes de ejecucion."""
    notes: list[str] = []
    if portfolio_type != PortfolioType.AGGRESSIVE and len(eligible_groups) <= 1:
        only_group = next(iter(eligible_groups), "none")
        notes.append(
            f"Solo un grupo de activo tuvo curvas elegibles ({only_group}); "
            "no fue posible diversificar por grupo en Balanced/Conservative."
        )
    if current.valley_usage_pct < 70:
        notes.append(
            "Valley DD usage is below 70%. This can be acceptable if no efficient increments remained."
        )
    if enforce_point_dd and current.point_usage_pct > 95:
        notes.append("Point DD usage is above 95%. Portfolio is close to point DD limit.")
    if not result_allocations:
        notes.append("No eligible robust sets found.")
    if execution_adjustments:
        notes.append(
            "Lots were rounded down to match integer LotPerBalance_step export values."
        )
    if execution_repair_log:
        notes.append(
            "Executable lot rounding raised combined DD; "
            f"{len(execution_repair_log)} additional unit reduction(s) restored the configured limits."
        )
    if correlation_rejections:
        notes.append(f"{correlation_rejections} increment candidate(s) rejected by correlation limits.")
    if group_limit_overages:
        notes.append(
            f"Concentracion por grupo sobre {max_units_per_group_pct * 100.0:.0f}% tras optimizar: "
            + ", ".join(group_limit_overages)
        )
    return notes


def _ttp_margin_rule_text(margin_summary: dict) -> str:
    """Tramos TTP realmente aplicados, no la tabla publicada."""
    # Redactado con los tramos que el modelo ha aplicado de verdad. El texto
    # fijo anterior prometia la tabla publicada mientras el calculo usaba 1:500
    # y contract_size 1 para todo.
    applied = margin_summary.get("group_leverage_applied") or {}
    measured = int(margin_summary.get("contract_size_measured") or 0)
    symbols = int(margin_summary.get("symbol_count") or 0)
    return (
        "tramos aplicados "
        + ", ".join(
            f"{group} 1:{float(leverage):.0f}"
            for group, leverage in sorted(applied.items())
        )
        + f"; contract_size medido en {measured}/{symbols} simbolo(s); "
        "sin apalancamiento de cuenta (el tramo es el requisito)."
    )


def _axi_margin_rule_text(margin_summary: dict) -> str:
    """Margen AXI medido en el terminal y reescalado a la cuenta configurada."""
    account = float(margin_summary.get("account_leverage") or 0.0)
    reference = float(margin_summary.get("reference_account_leverage") or 0.0)
    text = (
        f"margen medido en el terminal con la cuenta en 1:{reference:.0f} y "
        f"reescalado a 1:{account:.0f}, con el tope de cada producto como techo "
        "(cada simbolo usa min(cuenta, tope)). Lote de una unidad = lote minimo "
        "real del simbolo."
    )
    pending = list(margin_summary.get("unmeasured_symbols") or [])
    if pending:
        text += (
            f" Sin medir ({len(pending)}): {', '.join(pending[:5])}"
            + ("..." if len(pending) > 5 else "")
            + "; usan la estimacion por precio."
        )
    return text


def _default_margin_rule_text(margin_summary: dict) -> str:
    """Perfil por defecto: acciones 1:20, resto 1:500."""
    measured = int(margin_summary.get("contract_size_measured") or 0)
    symbols = int(margin_summary.get("symbol_count") or 0)
    return (
        "Stocks 1:20; resto 1:500. contract_size medido en "
        f"{measured}/{symbols} simbolo(s)"
        + ("" if measured >= symbols else ", el resto por grupo (acciones 100/resto 1)")
        + "."
    )


def _margin_warning(
    margin_summary: dict, margin_profile: str | MarginModel | None,
) -> list[str]:
    """Regla de margen aplicada y uso estimado frente al limite."""
    if not margin_summary:
        return []
    profile_label = str(margin_summary.get("profile_label") or margin_profile_label(margin_profile))
    summary_profile = normalize_margin_profile(margin_summary.get("profile") or margin_profile)
    if summary_profile == "ttp":
        rule_text = _ttp_margin_rule_text(margin_summary)
    elif summary_profile == "axi" and margin_summary.get("margin_source"):
        rule_text = _axi_margin_rule_text(margin_summary)
    else:
        rule_text = _default_margin_rule_text(margin_summary)
    return [
        f"Margen {profile_label} aplicado: {rule_text} "
        f"Uso estimado {float(margin_summary['total']):.2f}/"
        f"{float(margin_summary['limit']):.2f} "
        f"({float(margin_summary['usage_pct']):.1f}% del limite)."
    ]


def _daily_dd_warning(
    current: PortfolioEvaluation,
    daily_dd_summary: dict[str, object],
    *,
    max_daily_dd: float | None,
    daily_dd_full_history: bool,
) -> list[str]:
    """DD diario informativo; no limita lotes."""
    if max_daily_dd is None:
        return []
    worst_day = str(daily_dd_summary.get("worst_day") or "-")
    return [
        "DD diario visual (no limita lotes): cerrado + flotante estimado "
        f"({'historico completo' if daily_dd_full_history else 'mes objetivo'}) "
        f"{current.daily_dd:.2f}/{float(max_daily_dd):.2f}"
        + (f" en {worst_day}." if worst_day != "-" else ".")
    ]


def _build_portfolio_result(
    result_allocations: list[StrategyAllocation],
    current: PortfolioEvaluation,
    *,
    target_valley_dd: float,
    target_point_dd: float,
    stop_reason: str,
    warnings: list[str],
    decision_log: list[OptimizationDecision],
    unused_sets: list[UnusedSetInfo],
    correlation_rejections: int,
    group_summary: dict,
    stress_bootstrap: BootstrapDrawdownAnalysis,
    margin_summary: dict,
    daily_dd_summary: dict[str, object],
    max_daily_dd: float | None,
    daily_dd_full_history: bool,
    enforce_point_dd: bool,
    floating_overlap_audit: dict,
) -> PortfolioResult:
    """Ensambla el resultado final a partir de lo que dejo cada fase."""
    return PortfolioResult(
        allocations=result_allocations,
        equity_curve_2020_2026=current.equity_curve_2020_2026,
        total_net_profit=current.total_net_profit,
        actual_valley_dd=current.valley_dd,
        actual_point_dd=current.point_dd,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=current.valley_usage_pct,
        point_usage_pct=current.point_usage_pct,
        # ``evaluate_portfolio`` no conoce el perfil, asi que su ``total_lot`` es
        # el viejo ``unidades x 0.01``. Aqui si hay modelo: se suma el lote real
        # de cada simbolo. Sin el da lo mismo que antes.
        total_lot=round(sum(allocation.lot for allocation in result_allocations), 2),
        total_units=current.total_units,
        active_strategies=current.active_strategies,
        stop_reason=stop_reason,
        warnings=warnings,
        decision_log=decision_log,
        unused_sets=unused_sets,
        correlation_rejections=correlation_rejections,
        group_summary=group_summary,
        stress_bootstrap=stress_bootstrap,
        margin_summary=margin_summary,
        max_daily_dd=current.daily_dd,
        target_daily_dd=float(max_daily_dd) if max_daily_dd is not None else None,
        daily_dd_summary=daily_dd_summary,
        daily_dd_full_history=bool(daily_dd_full_history),
        enforce_point_dd=bool(enforce_point_dd),
        actual_closed_valley_dd=current.closed_valley_dd,
        floating_dd_buffer=current.floating_dd_buffer,
        floating_overlap_audit=floating_overlap_audit,
    )


def optimize_portfolio(
    raw_sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    min_trades_2020_2026: int = 100,
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    run_local_search: bool = True,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int | None = None,
    required_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    prefer_breadth_below_minimum: bool = False,
    required_initial_allocations: dict[str, int] | None = None,
    preserve_required_allocations: bool = False,
    dd_reserve_pct: float = 0.0,
    search_restarts: int = 0,
    bootstrap_simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS,
    bootstrap_block_size: int | None = None,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
    use_deep_refinement: bool = False,
) -> PortfolioResult:
    """Encadena las fases del optimizador; cada una vive en su propia funcion.

    El orden importa y es el contrato: pool de candidatos, greedy + busqueda
    local, relajacion del tope por grupo, multi-start, refinamiento profundo,
    redondeo a lotes ejecutables y, solo entonces, medicion y avisos.
    """
    reserve_factor = 1.0 - min(max(float(dd_reserve_pct), 0.0), 99.0) / 100.0
    effective_valley_dd_pct = valley_dd_pct * reserve_factor
    effective_point_dd_pct = point_dd_pct * reserve_factor
    target_valley_dd = capital * effective_valley_dd_pct / 100.0
    target_point_dd = capital * effective_point_dd_pct / 100.0
    group_limits = group_limits_for_portfolio_type(portfolio_type)
    if max_units_per_group_pct is None:
        max_units_per_group_pct = group_limits.max_units_pct
    if max_sets_per_group is None:
        max_sets_per_group = group_limits.max_sets
    if group_unit_cap_bootstrap is None:
        group_unit_cap_bootstrap = group_limits.bootstrap_units

    pool = _build_candidate_pool(
        raw_sets,
        min_trades_2020_2026=min_trades_2020_2026,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        required_set_ids=required_set_ids,
        required_initial_allocations=required_initial_allocations,
    )
    selected = pool.selected
    configured_group_units_pct = max_units_per_group_pct
    candidate_group_count = _candidate_group_count(selected)
    max_units_per_group_pct, group_units_pct_floor = _feasible_group_units_pct(
        max_units_per_group_pct, candidate_group_count,
    )

    limits = _SearchLimits(
        max_units_per_set=max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        max_pair_corr=max_pair_corr,
        max_downside_corr=max_downside_corr,
        max_dd_overlap=max_dd_overlap,
        existing_portfolio_curves=existing_portfolio_curves,
        max_portfolio_corr=max_portfolio_corr,
        max_units_per_group_pct=max_units_per_group_pct,
        max_sets_per_group=max_sets_per_group,
        group_unit_cap_bootstrap=group_unit_cap_bootstrap,
        margin_balance=margin_balance,
        max_margin_pct=max_margin_pct,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
    )
    ctx = _PassContext(
        capital=capital,
        valley_dd_pct=effective_valley_dd_pct,
        point_dd_pct=effective_point_dd_pct,
        portfolio_type=portfolio_type,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        initial_allocations={
            set_id: max(int((required_initial_allocations or {}).get(set_id, 1)), 1)
            for set_id in pool.required_ids
        },
        minimum_active_strategies=minimum_active_strategies,
        maximum_active_strategies=maximum_active_strategies,
        fixed_set_ids=pool.required_ids if preserve_required_allocations else set(),
        preserve_required_allocations=preserve_required_allocations,
        required_ids=pool.required_ids,
        run_local_search=run_local_search,
    )

    strict = _greedy_then_local_search(
        selected, limits, ctx, prefer_breadth_below_minimum=prefer_breadth_below_minimum,
    )
    relaxed = _relaxed_group_cap_pass(strict, selected, limits, ctx)
    group_cap_relaxed = relaxed is not None
    best = relaxed or strict
    allocations, current = best.allocations, best.current
    stop_reason = best.stop_reason
    if group_cap_relaxed:
        stop_reason += "; group unit cap relaxed after strict Balanced allocation underused DD"

    # A partir de aqui el tope por grupo ya no rige si se relajo.
    search_limits = replace(limits, max_units_per_group_pct=None) if group_cap_relaxed else limits
    allocations, current, multi_start_log, valid_restarts = _multi_start_pass(
        selected, allocations, current, search_limits, ctx, search_restarts=search_restarts,
    )
    if multi_start_log:
        stop_reason += "; multi-start search improved the local solution"

    deep = _DeepRefinement([], 0, False, len(selected), False)
    if use_deep_refinement and not preserve_required_allocations:
        selected, allocations, current, deep = _apply_deep_refinement(
            pool, selected, allocations, current, search_limits, ctx,
            top_k_per_symbol=top_k_per_symbol,
            max_total_candidates=max_total_candidates,
            min_trades_2020_2026=min_trades_2020_2026,
        )
        if deep.applied:
            stop_reason += "; deep optimization refined the solution"

    (
        allocations,
        current,
        executable_steps,
        execution_repair_log,
        execution_adjustments,
    ) = _repair_to_executable_lots(
        selected,
        allocations,
        ctx,
        margin_profile=margin_profile,
        max_daily_dd=max_daily_dd,
        enforce_point_dd=enforce_point_dd,
        daily_dd_full_history=daily_dd_full_history,
    )
    if execution_repair_log:
        stop_reason += "; executable lot rounding repaired to preserve DD limits"

    if current.valley_dd > target_valley_dd:
        raise ValueError("Final portfolio violates valley DD")
    if enforce_point_dd and current.point_dd > target_point_dd:
        raise ValueError("Final portfolio violates point DD")

    margin_summary, daily_dd_summary = _margin_and_daily_summaries(
        selected, allocations, current, limits,
    )
    result_allocations = _result_allocation_rows(
        selected, allocations, margin_summary, limits,
        capital=capital, executable_steps=executable_steps,
    )
    group_summary = portfolio_group_summary(selected, allocations)
    eligible_groups = {portfolio_group_key(strategy.symbol) for strategy in pool.eligible}
    # Auditoria del supuesto del max(): se mide una sola vez sobre la cartera
    # final, nunca dentro de la busqueda. No cambia el riesgo aplicado; avisa
    # cuando varias estrategias coinciden bajo el agua y el maximo individual
    # deja de describir la exposicion.
    floating_overlap_audit = portfolio_floating_overlap_audit(
        selected, allocations, current.floating_dd_buffer,
    )
    warnings = [
        *_group_floor_warning(
            configured_group_units_pct,
            group_units_pct_floor,
            max_units_per_group_pct,
            candidate_group_count,
        ),
        *_dd_composition_warnings(current, dd_reserve_pct, floating_overlap_audit),
        *_recent_recovery_warning(raw_sets),
        *_search_phase_warnings(
            search_restarts=search_restarts,
            valid_restarts=valid_restarts,
            use_deep_refinement=use_deep_refinement,
            deep=deep,
            base_selected_count=pool.base_selected_count,
            selected_count=len(selected),
            preserve_required_allocations=preserve_required_allocations,
            required_ids=pool.required_ids,
            greedy_log=best.greedy_log,
            group_cap_relaxed=group_cap_relaxed,
        ),
        *_usage_warnings(
            current,
            portfolio_type=portfolio_type,
            eligible_groups=eligible_groups,
            enforce_point_dd=enforce_point_dd,
            result_allocations=result_allocations,
            execution_adjustments=execution_adjustments,
            execution_repair_log=execution_repair_log,
            correlation_rejections=best.correlation_rejections,
            group_limit_overages=_group_limit_overages(
                group_summary, eligible_groups, max_units_per_group_pct,
            ),
            max_units_per_group_pct=max_units_per_group_pct,
        ),
        *_margin_warning(margin_summary, margin_profile),
        *_daily_dd_warning(
            current,
            daily_dd_summary,
            max_daily_dd=max_daily_dd,
            daily_dd_full_history=daily_dd_full_history,
        ),
    ]

    stress_bootstrap = bootstrap_valley_drawdown(
        current.equity_curve_2020_2026,
        nominal_valley_dd_limit=capital * valley_dd_pct / 100.0,
        effective_valley_dd_limit=target_valley_dd,
        simulations=bootstrap_simulations,
        block_size=bootstrap_block_size,
        seed=bootstrap_seed,
    )
    if stress_bootstrap.alert:
        warnings.append(
            f"ALERTA bootstrap: DD valle P95 {stress_bootstrap.valley_dd_p95:.2f} "
            f"supera el limite efectivo {stress_bootstrap.effective_valley_dd_limit:.2f}."
        )
    return _build_portfolio_result(
        result_allocations,
        current,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        stop_reason=stop_reason,
        warnings=warnings,
        decision_log=(
            best.greedy_log + best.local_log + multi_start_log + deep.log + execution_repair_log
        ),
        unused_sets=_build_unused_sets(
            raw_sets, pool.eligible, selected, allocations, min_trades_2020_2026,
        ),
        correlation_rejections=best.correlation_rejections,
        group_summary=group_summary,
        stress_bootstrap=stress_bootstrap,
        margin_summary=margin_summary,
        daily_dd_summary=daily_dd_summary,
        max_daily_dd=max_daily_dd,
        daily_dd_full_history=daily_dd_full_history,
        enforce_point_dd=enforce_point_dd,
        floating_overlap_audit=floating_overlap_audit,
    )
