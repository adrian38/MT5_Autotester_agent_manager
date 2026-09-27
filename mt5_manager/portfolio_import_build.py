"""Reconstruir las propuestas de un portafolio exportado.

Del resumen solo sale la composicion; los numeros se recalculan con las mismas
funciones que un calculo nuevo. Ver `mt5_manager/portfolio_import.py` para el
formato y sus limites.

Encima de `portfolio_identity`, `portfolio_report_cache` y
`portfolio_persistence`. De `PortfolioSource` solo usa sus metodos de lectura.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    StrategyAllocation,
    bootstrap_valley_drawdown,
    evaluate_portfolio,
    load_robust_sets_from_rows,
    portfolio_group_summary,
    portfolio_symbol_key,
    slice_strategy_sets_to_month,
)

from . import portfolio_import
from .common import safe_float, safe_int
from .portfolio_identity import (
    IMPROVEMENT_PRIORITY_LABELS,
    TYPE_LABELS,
    _normalized_improvement_lineage,
    _resolve_source_path,
    _stored_path_name,
    _valid_portfolio_uid,
    normalize_portfolio_alias,
)
from .portfolio_import_match import (
    _changed_verdict_notes,
    _import_resolution_warnings,
    _imported_target_month,
    _imported_variant_key,
    _resolve_import_members,
)
from .portfolio_persistence import settings_inputs
from .portfolio_report_cache import cached_report
from .portfolio_scope import normalize_portfolio_scope

if TYPE_CHECKING:  # solo para el tipo: importarlo de verdad seria un ciclo
    from .portfolio_service import PortfolioSource


def _imported_allocation(
    strategy: Any, units: dict[str, int], lots: dict[str, float],
) -> StrategyAllocation:
    """Asignacion de una estrategia que si se pudo medir."""
    count = units[strategy.set_id]
    return StrategyAllocation(
        set_id=strategy.set_id, candidate_id=strategy.candidate_id, symbol=strategy.symbol,
        units=count, lot=lots.get(strategy.set_id, 0.0),
        net_profit_contribution=strategy.net_profit_2020_2026_001 * count,
        standalone_valley_dd=max(strategy.valley_dd_2020_2026_001, strategy.max_floating_dd_001) * count,
        standalone_point_dd=strategy.point_dd_2020_2026_001 * count,
        timeframe=strategy.timeframe, set_path=strategy.set_path,
        is_report_path=strategy.is_report_path, oos_report_path=strategy.oos_report_path,
        lot_size_step=None,
        max_balance_dd_001=strategy.max_balance_dd_001, max_equity_dd_001=strategy.max_equity_dd_001,
        floating_dd_source=strategy.floating_dd_source,
        standalone_floating_dd=strategy.max_floating_dd_001 * count,
        recent_net_profit_001=strategy.recent_net_profit_001,
        recent_equity_dd_001=strategy.recent_equity_dd_001,
        has_recent_performance=strategy.has_recent_performance,
        final_tick_report_path=strategy.final_tick_report_path,
        full_history_report_path=strategy.full_history_report_path,
    )

def _unmeasured_allocation(
    member: Any,
    row_by_name: dict[str, dict[str, Any]],
    unresolved: list[str],
    ambiguous: list[str],
) -> StrategyAllocation:
    """Miembro conservado sin metricas: composicion si, numeros no.

    La composicion del ZIP no se recorta nunca. Cuando no hay candidato ni
    informe legible se guarda igual, con las metricas a cero y el motivo escrito
    en ``floating_dd_source``, en vez de atribuir numeros inventados.
    """
    name = str(member.set_name)
    row = row_by_name.get(name.casefold()) or {}
    set_path = str(row.get("set_path") or name)
    if name in unresolved:
        missing_reason = "No reconstruido al importar: no existe candidato ni informe"
    elif name in ambiguous:
        missing_reason = "No reconstruido al importar: varios candidatos posibles"
    else:
        missing_reason = "No reconstruido al importar: faltan informes legibles"
    return StrategyAllocation(
        set_id=set_path,
        candidate_id=str(row.get("candidate_id") or f"importado-sin-informes:{name}"),
        symbol=str(member.symbol),
        units=int(member.units),
        lot=float(member.lot),
        net_profit_contribution=0.0,
        standalone_valley_dd=0.0,
        standalone_point_dd=0.0,
        timeframe=str(member.timeframe),
        set_path=set_path,
        is_report_path=str(row.get("is_report_path") or ""),
        oos_report_path=str(row.get("oos_report_path") or ""),
        floating_dd_source=missing_reason,
    )

def _imported_portfolio_result(
    strategies: list[Any],
    units: dict[str, int],
    allocations: list[StrategyAllocation],
    evaluation: Any,
    inputs: dict[str, Any],
    *,
    target_valley: float,
    target_point: float,
    capital: float,
    warnings: list[str],
    incomplete: bool,
) -> PortfolioResult:
    """Resultado de una variante importada: medido, no copiado del texto."""
    return PortfolioResult(
        allocations=allocations,
        equity_curve_2020_2026=evaluation.equity_curve_2020_2026,
        total_net_profit=evaluation.total_net_profit,
        actual_valley_dd=evaluation.valley_dd, actual_point_dd=evaluation.point_dd,
        target_valley_dd=target_valley, target_point_dd=target_point,
        valley_usage_pct=evaluation.valley_usage_pct, point_usage_pct=evaluation.point_usage_pct,
        total_lot=sum(allocation.lot for allocation in allocations),
        total_units=sum(allocation.units for allocation in allocations),
        active_strategies=len(allocations),
        stop_reason=(
            "Composición importada; cálculo incompleto por informes ausentes"
            if incomplete
            else "Composición importada de una exportación previa"
        ),
        warnings=list(warnings), decision_log=[],
        group_summary=portfolio_group_summary(strategies, units),
        stress_bootstrap=bootstrap_valley_drawdown(
            evaluation.equity_curve_2020_2026,
            nominal_valley_dd_limit=capital * float(inputs["valley_dd_pct"]) / 100.0,
            effective_valley_dd_limit=target_valley,
        ),
        seasonal_coverage={
            strategy.set_id: {
                "target_month": strategy.target_month, "years": list(strategy.month_years),
                "positive_years": list(strategy.positive_month_years),
                "year_count": len(strategy.month_years),
                "positive_year_count": len(strategy.positive_month_years),
                "trades": strategy.trades_2020_2026,
            }
            for strategy in strategies
            if strategy.target_month is not None and units.get(strategy.set_id, 0) > 0
        },
        actual_closed_valley_dd=evaluation.closed_valley_dd,
        floating_dd_buffer=evaluation.floating_dd_buffer,
        enforce_point_dd=False,
    )

@dataclass(frozen=True)
class _ImportContext:
    """Lo que todas las variantes de una importacion comparten."""

    scope: str
    capital: float
    target_valley: float
    target_point: float
    target_month: int | None
    strategies: list[Any]
    by_set: dict[str, Any]
    path_by_name: dict[str, str]
    row_by_name: dict[str, dict[str, Any]]
    unresolved: list[str]
    ambiguous: list[str]
    warnings: list[str]
    blank_variant_key: str

def _variant_units(
    members: list[Any], ctx: _ImportContext,
) -> tuple[dict[str, int], dict[str, float], dict[str, Any]]:
    """Unidades y lotes de una variante, y los miembros que no se pudieron medir."""
    units: dict[str, int] = {}
    lots: dict[str, float] = {}
    unmeasured_members: dict[str, Any] = {}
    for member in members:
        set_path = ctx.path_by_name.get(str(member.set_name).casefold())
        if not set_path or set_path not in ctx.by_set:
            name = str(member.set_name)
            unmeasured_members[name.casefold()] = member
            continue
        units[set_path] = units.get(set_path, 0) + int(member.units)
        lots[set_path] = float(member.lot)
    return units, lots, unmeasured_members

def _variant_proposal(
    label: str, order: list[str], members: list[Any], ctx: _ImportContext,
) -> dict[str, Any] | None:
    """Propuesta de una variante del resumen, o ``None`` si no tiene miembros."""
    units, lots, unmeasured_members = _variant_units(members, ctx)
    if not units and not unmeasured_members:
        return None
    key = portfolio_import.variant_key_for(label, order)
    if not label.strip() and ctx.blank_variant_key:
        key = ctx.blank_variant_key
    inputs: dict[str, Any] = {
        "capital": ctx.capital,
        "valley_dd_pct": ctx.target_valley * 100.0 / ctx.capital if ctx.capital > 0 else 0.0,
        "point_dd_pct": ctx.target_point * 100.0 / ctx.capital if ctx.capital > 0 else 0.0,
        "portfolio_type": key,
        "composition_portfolio_type": key,
        "portfolio_scope": ctx.scope,
    }
    if ctx.scope == "monthly" and ctx.target_month:
        inputs["target_month"] = int(ctx.target_month)
    evaluation = evaluate_portfolio(
        ctx.strategies, units, ctx.target_valley, ctx.target_point, None, False, False,
    )
    allocations = [
        _imported_allocation(strategy, units, lots)
        for strategy in ctx.strategies if units.get(strategy.set_id, 0) > 0
    ]
    allocations.extend(
        _unmeasured_allocation(member, ctx.row_by_name, ctx.unresolved, ctx.ambiguous)
        for member in unmeasured_members.values()
    )
    if unmeasured_members:
        inputs["import_calculation_complete"] = False
        inputs["import_unmeasured_sets"] = [
            str(member.set_name) for member in unmeasured_members.values()
        ]
    else:
        inputs["import_calculation_complete"] = True
    return {
        "key": key,
        "label": label.strip() or TYPE_LABELS.get(key, key),
        "inputs": inputs,
        "result": _imported_portfolio_result(
            ctx.strategies, units, allocations, evaluation, inputs,
            target_valley=ctx.target_valley, target_point=ctx.target_point,
            capital=ctx.capital, warnings=ctx.warnings, incomplete=bool(unmeasured_members),
        ),
    }

def _reconstructed_added_count(
    source: PortfolioSource,
    improvement_source_id: int,
    improvement_mode: str,
    proposal: dict[str, Any],
) -> int:
    """Incorporaciones de una mejora cuyo resumen no las traia.

    Formato antiguo: origen y modo podian estar en el nombre, pero no el numero
    de incorporaciones. Si la base sigue guardada se puede reconstruir sin
    inferir ninguna decision del optimizador.
    """
    try:
        base = source.saved_portfolio_detail(improvement_source_id, "full_history")["portfolio"]
        base_members = base.get("members") or []
        if str(base.get("portfolio_type") or "") == "bundle":
            base_members = [
                member for member in base_members
                if str(member.get("variant_key") or "") == improvement_mode
            ]
        base_names = {
            Path(str(member.get("set_path") or member.get("set_id") or "")).name.casefold()
            for member in base_members
            if int(member.get("units") or 0) > 0
        }
        improved_names = {
            Path(str(allocation.set_path or allocation.set_id)).name.casefold()
            for allocation in proposal["result"].allocations
            if allocation.units > 0
        }
        if base_names and base_names <= improved_names:
            return len(improved_names - base_names)
    except (ValueError, TypeError, OSError):
        pass
    return -1

def _improvement_identity_inputs(
    proposal_inputs: dict[str, Any],
    header: dict[str, Any],
    *,
    improvement_mode: str,
    improvement_source_id: int,
    portfolio_uid: str,
    parent_uid: str,
) -> dict[str, Any]:
    """Escribe la identidad de mejora en los inputs y devuelve sus piezas."""
    proposal_inputs["portfolio_type"] = improvement_mode
    proposal_inputs["composition_portfolio_type"] = improvement_mode
    proposal_inputs["improvement_source_portfolio_id"] = improvement_source_id
    proposal_inputs["improvement_portfolio_type"] = improvement_mode
    if portfolio_uid:
        proposal_inputs["portfolio_uid"] = portfolio_uid
    label = str(header.get("improvement_label") or "").strip()
    if label:
        proposal_inputs["improvement_label"] = label[:240]
    if parent_uid:
        proposal_inputs["improvement_parent_uid"] = parent_uid
    root_id = safe_int(header.get("improvement_root_portfolio_id"), improvement_source_id)
    proposal_inputs["improvement_root_portfolio_id"] = root_id or improvement_source_id
    root_uid = _valid_portfolio_uid(header.get("improvement_root_uid"))
    if root_uid:
        proposal_inputs["improvement_root_uid"] = root_uid
    depth = max(1, safe_int(header.get("improvement_depth"), 1))
    proposal_inputs["improvement_depth"] = depth
    lineage = _normalized_improvement_lineage(header.get("improvement_lineage"))
    if lineage:
        proposal_inputs["improvement_lineage"] = lineage
    priority = str(header.get("improvement_selection_priority") or "").strip().lower()
    if priority:
        if priority not in IMPROVEMENT_PRIORITY_LABELS:
            raise ValueError("La exportación conserva una prioridad de mejora desconocida")
        proposal_inputs["improvement_selection_priority"] = priority
    return {
        "label": label, "root_id": root_id, "root_uid": root_uid,
        "depth": depth, "lineage": lineage, "priority": priority,
    }

def _apply_improvement_identity(
    proposals: list[dict[str, Any]],
    header: dict[str, Any],
    source: PortfolioSource,
    *,
    improvement_source_id: int,
    improvement_mode: str,
    exported_uid: str,
    exported_parent_uid: str,
) -> None:
    """Devuelve a la propuesta su linaje de mejora, o falla si no cuadra."""
    if improvement_mode not in TYPE_LABELS:
        raise ValueError("La exportación identifica una mejora, pero no conserva un modo válido")
    if len(proposals) != 1 or str(proposals[0]["key"]) != improvement_mode:
        raise ValueError(
            "La identidad de mejora de la exportación no coincide con su composición: "
            f"esperaba solo el modo {TYPE_LABELS[improvement_mode]}"
        )
    proposal = proposals[0]
    parts = _improvement_identity_inputs(
        proposal["inputs"], header,
        improvement_mode=improvement_mode,
        improvement_source_id=improvement_source_id,
        portfolio_uid=exported_uid,
        parent_uid=exported_parent_uid,
    )
    added_value = header.get("improvement_added_count")
    added_count = safe_int(added_value, -1) if added_value is not None else -1
    if added_count < 0:
        added_count = _reconstructed_added_count(
            source, improvement_source_id, improvement_mode, proposal,
        )
    audit: dict[str, Any] = {
        "source_portfolio_id": improvement_source_id,
        "target_portfolio_type": improvement_mode,
        "imported_lineage": True,
        "portfolio_uid": exported_uid,
        "label": parts["label"],
        "parent_uid": exported_parent_uid,
        "root_portfolio_id": parts["root_id"] or improvement_source_id,
        "root_uid": parts["root_uid"],
        "depth": parts["depth"],
        "lineage": parts["lineage"],
    }
    snapshot = header.get("improvement_source_snapshot")
    if isinstance(snapshot, dict):
        audit["source_snapshot"] = snapshot
    if parts["priority"]:
        audit["selection_priority"] = parts["priority"]
    if added_count >= 0:
        audit["added_count"] = added_count
    proposal["result"].seasonal_validation = {
        **(proposal["result"].seasonal_validation or {}),
        "portfolio_improvement": audit,
    }

def _assert_shared_composition(proposals: list[dict[str, Any]]) -> None:
    """Un paquete A/M/C guardado siempre comparte composicion entre variantes.

    ``save_proposal`` lo exige; solo cambian las unidades. Si el resumen no lo
    cumple, decirlo aqui evita que el guardado falle mas abajo con un mensaje
    que no senala al fichero.
    """
    compositions = {
        str(proposal["key"]): frozenset(
            allocation.set_id for allocation in proposal["result"].allocations
        )
        for proposal in proposals
    }
    if len(set(compositions.values())) > 1:
        raise ValueError(
            "Las variantes del resumen no comparten la misma composición, cosa que "
            "un paquete A/M/C guardado siempre cumple. Revisa que el resumen esté "
            "completo: " + "; ".join(
                f"{key}: {len(sets)} sets" for key, sets in sorted(compositions.items())
            )
        )

def _import_report(
    proposals: list[dict[str, Any]],
    ctx: _ImportContext,
    unmeasured: list[str],
    *,
    improvement_source_id: int,
    improvement_mode: str,
) -> dict[str, Any]:
    """Resumen de lo que la importacion reconstruyo y de lo que no."""
    keys = [str(proposal["key"]) for proposal in proposals]
    return {
        "variants": keys,
        "strategies": len({
            allocation.set_id
            for proposal in proposals
            for allocation in proposal["result"].allocations
        }),
        "unresolved": ctx.unresolved,
        "ambiguous": ctx.ambiguous,
        "skipped": [],
        "calculation_complete": not unmeasured,
        "unmeasured": unmeasured,
        "warnings": ctx.warnings,
        "target_month": ctx.target_month,
        "improvement_origin": {
            "source_id": improvement_source_id,
            "mode": improvement_mode,
        } if improvement_source_id > 0 and improvement_mode in TYPE_LABELS else None,
    }

def _import_context(
    source: PortfolioSource, scope: str, header: dict[str, Any], members: list[Any],
) -> tuple[_ImportContext, list[str]]:
    """Reconstruye y remide la composicion del ZIP; devuelve el contexto comun.

    Tambien la lista de miembros que no se pudieron medir: el resumen los nombra
    pero su informe no esta, no se dejo leer o quedo fuera del mes objetivo.
    """
    resolved, unresolved, ambiguous = _resolve_import_members(members, header, source)
    rows = list(resolved.values())
    changed_verdicts = _changed_verdict_notes(rows)
    strategies, warnings = load_robust_sets_from_rows(rows, [], parse=cached_report)
    if changed_verdicts:
        warnings.append(
            "La composición se restauró exactamente desde el ZIP aunque algunos "
            "veredictos actuales hayan cambiado."
        )
        warnings.append("Veredictos actuales: " + " | ".join(changed_verdicts))
    path_by_name = {
        Path(str(row.get("set_path") or "")).name.casefold(): str(row.get("set_path") or "")
        for row in rows
    }
    row_by_name = {
        Path(str(row.get("set_path") or "")).name.casefold(): row for row in rows
    }
    target_valley = float(header.get("target_valley_dd") or 0)
    target_month = _imported_target_month(header)
    if scope == "monthly" and target_month:
        strategies, monthly_warnings = slice_strategy_sets_to_month(strategies, int(target_month))
        warnings.extend(monthly_warnings)
    loaded_paths = {str(strategy.set_id) for strategy in strategies}
    unmeasured: list[str] = []
    for member in members:
        name = str(member.set_name)
        set_path = path_by_name.get(name.casefold())
        if (not set_path or set_path not in loaded_paths) and name not in unmeasured:
            unmeasured.append(name)
    warnings.extend(_import_resolution_warnings(unresolved, ambiguous, unmeasured))
    return _ImportContext(
        scope=scope, capital=float(header.get("capital") or 0),
        target_valley=target_valley,
        target_point=float(header.get("target_point_dd") or 0) or target_valley,
        target_month=target_month,
        strategies=strategies,
        by_set={strategy.set_id: strategy for strategy in strategies},
        path_by_name=path_by_name, row_by_name=row_by_name,
        unresolved=unresolved, ambiguous=ambiguous, warnings=warnings,
        blank_variant_key=_imported_variant_key(header),
    ), unmeasured

def _grouped_variants(members: list[Any]) -> tuple[list[str], dict[str, list[Any]]]:
    """Los miembros por variante, en el orden en que aparecen en el resumen."""
    order: list[str] = []
    grouped: dict[str, list[Any]] = {}
    for member in members:
        if member.variant_label not in order:
            order.append(member.variant_label)
        grouped.setdefault(member.variant_label, []).append(member)
    return order, grouped

def _imported_identity_uids(header: dict[str, Any], warnings: list[str]) -> tuple[str, str]:
    """El UID exportado y el de su origen, descartando el que se repite."""
    exported_uid = _valid_portfolio_uid(header.get("portfolio_uid"))
    exported_parent_uid = _valid_portfolio_uid(header.get("improvement_parent_uid"))
    if exported_uid and exported_uid == exported_parent_uid:
        # Una cartera no puede ser su propio origen. Un resumen que repite el UID
        # del padre —lo hace la exportación de una mejora de una mejora— haría
        # que las dos importaciones compartieran identidad y que la cadena se
        # midiera contra sí misma. Se descarta el UID repetido: la fila
        # importada recibe identidad propia y conserva el enlace al padre.
        warnings.append(
            "El resumen traía como UID del portafolio el de su origen "
            f"({exported_uid}); se descarta y se le asigna identidad propia."
        )
        exported_uid = ""
    return exported_uid, exported_parent_uid

def _apply_imported_identity(
    proposals: list[dict[str, Any]],
    source: PortfolioSource,
    header: dict[str, Any],
    scope: str,
    exported_uid: str,
    exported_parent_uid: str,
    improvement_source_id: int,
    improvement_mode: str,
) -> None:
    """Pone en las propuestas el UID, el alias y el linaje que traia el resumen."""
    if exported_uid:
        for proposal in proposals:
            proposal.setdefault("inputs", {})["portfolio_uid"] = exported_uid
    imported_alias = normalize_portfolio_alias(header.get("portfolio_alias"))
    if scope == "full_history" and imported_alias:
        for proposal in proposals:
            proposal.setdefault("inputs", {})["portfolio_alias"] = imported_alias
    if scope == "full_history" and improvement_source_id > 0:
        _apply_improvement_identity(
            proposals, header, source,
            improvement_source_id=improvement_source_id,
            improvement_mode=improvement_mode,
            exported_uid=exported_uid,
            exported_parent_uid=exported_parent_uid,
        )
    if scope == "full_history" and len(proposals) > 1:
        _assert_shared_composition(proposals)

def build_import_proposals(
    source: PortfolioSource,
    scope: str,
    header: dict[str, Any],
    members: list[Any],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Reconstruye las propuestas de un portafolio exportado.

    Del resumen solo sale la **composición**: qué set, con cuántas unidades y en
    qué variante. Los números se recalculan desde los informes MT5 del candidato
    con las mismas funciones que un cálculo nuevo (`load_robust_sets_from_rows`
    y `evaluate_portfolio`), así que el portafolio importado no es una copia
    degradada del texto: es el mismo cálculo sobre la misma composición.

    Ver `mt5_manager/portfolio_import.py` para el formato y sus límites.
    """
    scope = normalize_portfolio_scope(scope)
    ctx, unmeasured = _import_context(source, scope, header, members)
    order, grouped = _grouped_variants(members)
    exported_uid, exported_parent_uid = _imported_identity_uids(header, ctx.warnings)
    proposals = [
        proposal for proposal in (
            _variant_proposal(label, order, grouped[label], ctx) for label in order
        )
        if proposal is not None
    ]
    if not proposals:
        raise ValueError("El resumen no dejó ninguna variante reconstruible")
    improvement_source_id = safe_int(header.get("improvement_source_portfolio_id"), 0)
    improvement_mode = str(header.get("improvement_portfolio_type") or "").strip().lower()
    _apply_imported_identity(
        proposals, source, header, scope, exported_uid, exported_parent_uid,
        improvement_source_id, improvement_mode,
    )
    keys = [str(proposal["key"]) for proposal in proposals]
    return proposals, "balanced" if "balanced" in keys else keys[0], _import_report(
        proposals, ctx, unmeasured,
        improvement_source_id=improvement_source_id,
        improvement_mode=improvement_mode,
    )

