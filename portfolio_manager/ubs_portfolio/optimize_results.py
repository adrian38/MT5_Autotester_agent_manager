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
from .optimize_search import _DeepRefinement


def _margin_and_daily_summaries(
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    limits: SearchLimits,
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
    limits: SearchLimits,
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
