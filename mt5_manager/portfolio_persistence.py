"""Serializar un resultado y escribirlo en la memoria UBS.

Encima de `portfolio_identity`; del resto solo necesita el resultado del
optimizador y una conexion. No importa `portfolio_service` en tiempo de
ejecucion: lo unico que le pide es `source.connect(write=True)`.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from portfolio_manager.ubs_portfolio import PortfolioResult, portfolio_symbol_key

from .common import safe_float
from .portfolio_identity import TYPE_LABELS
from .portfolio_scope import normalize_portfolio_scope

if TYPE_CHECKING:  # solo para el tipo: importarlo de verdad seria un ciclo
    from .portfolio_service import PortfolioSource


def result_payload(result: PortfolioResult) -> dict[str, Any]:
    return {
        "total_net_profit": result.total_net_profit,
        "actual_valley_dd": result.actual_valley_dd,
        "actual_closed_valley_dd": result.actual_closed_valley_dd,
        "floating_dd_buffer": result.floating_dd_buffer,
        "actual_point_dd": result.actual_point_dd,
        "target_valley_dd": result.target_valley_dd,
        "target_point_dd": result.target_point_dd,
        "valley_usage_pct": result.valley_usage_pct,
        "point_usage_pct": result.point_usage_pct,
        "total_lot": result.total_lot,
        "total_units": result.total_units,
        "active_strategies": result.active_strategies,
        "stop_reason": result.stop_reason,
        "warnings": list(result.warnings),
        "group_summary": result.group_summary,
        "equity_curve_2020_2026": result.equity_curve_2020_2026,
        "unused_sets": [asdict(item) for item in result.unused_sets],
        "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
        "seasonal_coverage": result.seasonal_coverage,
        "seasonal_validation": result.seasonal_validation,
        "margin_summary": result.margin_summary,
        "floating_overlap_audit": result.floating_overlap_audit,
        "daily_dd_summary": result.daily_dd_summary,
        "max_daily_dd": result.max_daily_dd,
        "target_daily_dd": result.target_daily_dd,
        "daily_dd_full_history": result.daily_dd_full_history,
        "enforce_point_dd": result.enforce_point_dd,
        "allocations": [asdict(allocation) for allocation in result.allocations],
        "decision_log": [asdict(decision) for decision in result.decision_log],
    }

#: Claves de ``inputs`` que son objetos vivos del calculo, no ajustes del
#: formulario. Nunca deben viajar al nodo ni persistirse: el payload de guardado
#: se serializa a JSON y un ``MarginModel`` ahi dentro reventaba el POST con
#: "Object of type MarginModel is not JSON serializable", que en la pantalla
#: aparecia como un escueto "failed to fetch".
RUNTIME_ONLY_INPUT_KEYS = frozenset({"margin_model"})

def settings_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Copia de ``inputs`` con solo lo que es un ajuste serializable."""
    return {key: value for key, value in inputs.items() if key not in RUNTIME_ONLY_INPUT_KEYS}

def _result_metrics(inputs: dict[str, Any], result: PortfolioResult) -> dict[str, Any]:
    return {
        # Igual que en el limite HTTP: esto acaba como JSON en la memoria UBS.
        "inputs": settings_inputs(inputs),
        "warnings": result.warnings,
        "group_summary": result.group_summary,
        "equity_curve_2020_2026": result.equity_curve_2020_2026,
        "unused_sets": [asdict(item) for item in result.unused_sets],
        "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
        "seasonal_coverage": result.seasonal_coverage,
        "seasonal_validation": result.seasonal_validation,
        "margin_summary": result.margin_summary,
        "floating_overlap_audit": result.floating_overlap_audit,
        "daily_dd_summary": result.daily_dd_summary,
        "max_daily_dd": result.max_daily_dd,
        "target_daily_dd": result.target_daily_dd,
        "daily_dd_full_history": result.daily_dd_full_history,
        "enforce_point_dd": result.enforce_point_dd,
        "actual_closed_valley_dd": result.actual_closed_valley_dd,
        "floating_dd_buffer": result.floating_dd_buffer,
    }

def _insert_allocation(
    conn: sqlite3.Connection,
    portfolio_id: int,
    allocation: Any,
    variant_key: str,
    variant_label: str,
) -> None:
    conn.execute(
        """
        insert into portfolio_allocations (
            portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
            net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe,
            lot_size_step,margin_required,margin_pct,margin_leverage,margin_contract_size,
            margin_price,is_report_path,oos_report_path,final_tick_report_path,full_history_report_path,
            max_balance_dd_001,max_equity_dd_001,
            floating_dd_source,standalone_floating_dd,recent_net_profit_001,recent_equity_dd_001,
            has_recent_performance
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            portfolio_id, variant_key, variant_label, allocation.set_id, allocation.candidate_id,
            allocation.symbol, allocation.units, allocation.lot, allocation.net_profit_contribution,
            allocation.standalone_valley_dd, allocation.standalone_point_dd,
            allocation.set_path or allocation.set_id, allocation.timeframe or "", allocation.lot_size_step,
            allocation.margin_required, allocation.margin_pct, allocation.margin_leverage,
            allocation.margin_contract_size, allocation.margin_price,
            allocation.is_report_path, allocation.oos_report_path,
            allocation.final_tick_report_path,
            allocation.full_history_report_path,
            allocation.max_balance_dd_001, allocation.max_equity_dd_001,
            allocation.floating_dd_source, allocation.standalone_floating_dd,
            allocation.recent_net_profit_001, allocation.recent_equity_dd_001,
            int(allocation.has_recent_performance),
        ),
    )
    candidate_text = str(allocation.candidate_id)
    candidate_suffix = candidate_text.rsplit(":", 1)[-1]
    candidate_value = int(candidate_suffix) if candidate_suffix.isdigit() else None
    conn.execute(
        """
        insert into portfolio_members (
            portfolio_id,variant_key,variant_label,candidate_id,set_path,symbol,period,
            lot_multiplier,lot,lot_size_step,standalone_dd,quality_score,combined_net_profit,
            is_report_path,oos_report_path
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            portfolio_id, variant_key, variant_label, candidate_value,
            allocation.set_path or allocation.set_id, allocation.symbol, allocation.timeframe or "",
            allocation.units, allocation.lot, allocation.lot_size_step, allocation.standalone_valley_dd,
            0.0, allocation.net_profit_contribution, allocation.is_report_path, allocation.oos_report_path,
        ),
    )

def _insert_decisions(
    conn: sqlite3.Connection,
    portfolio_id: int,
    result: PortfolioResult,
    prefix: str = "",
) -> None:
    def finite(value: Any) -> float:
        parsed = safe_float(value, 0.0)
        return parsed if math.isfinite(parsed) else 0.0

    for decision in result.decision_log:
        reason = f"{prefix}: {decision.reason}" if prefix else decision.reason
        conn.execute(
            """
            insert into portfolio_decision_log (
                portfolio_id,step,action,set_id,from_set_id,to_set_id,gain,valley_cost,point_cost,
                score,portfolio_net_profit_after,portfolio_valley_dd_after,portfolio_point_dd_after,reason
            ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                portfolio_id, decision.step, decision.action, decision.set_id,
                decision.from_set_id, decision.to_set_id, finite(decision.gain),
                finite(decision.valley_cost), finite(decision.point_cost), finite(decision.score),
                finite(decision.portfolio_net_profit_after),
                finite(decision.portfolio_valley_dd_after),
                finite(decision.portfolio_point_dd_after), reason,
            ),
        )

@dataclass(frozen=True)
class _SavedHeader:
    """La fila de `portfolios`, ya decidida, antes de tocar la base."""

    created_at: str
    name: str
    row_type: str
    active_symbols: int
    scope: str
    target_month: int | None
    metrics: dict[str, Any]

def _bundle_variant_payloads(proposals: list[dict[str, Any]]) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for proposal in proposals:
        result: PortfolioResult = proposal["result"]
        payload = _result_metrics(proposal["inputs"], result)
        payload.update({
            "label": proposal["label"],
            "summary": result_payload(result),
            "allocations": [asdict(allocation) for allocation in result.allocations],
        })
        variants[str(proposal["key"])] = payload
    return variants

def _bundle_metrics(
    proposals: list[dict[str, Any]], selected: dict[str, Any], selected_key: str, scope: str,
) -> tuple[dict[str, Any], str, str]:
    """Metricas, tipo y nombre de un paquete A/M/C (o Grid A/M/C)."""
    selected_result: PortfolioResult = selected["result"]
    selected_inputs: dict[str, Any] = selected["inputs"]
    common = [allocation.set_id for allocation in selected_result.allocations if allocation.units > 0]
    common_set = set(common)
    if scope == "full_history" and any({allocation.set_id for allocation in proposal["result"].allocations if allocation.units > 0} != common_set for proposal in proposals):
        raise ValueError("Las variantes A/M/C no comparten la misma composición")
    variants = _bundle_variant_payloads(proposals)
    metrics = _result_metrics(selected_inputs, selected_result)
    metrics.update({
        "portfolio_bundle": True,
        "bundle_display": "Grid A/M/C" if scope == "grid" else "A/M/C",
        "selected_variant": selected_key,
        "variant_order": [str(proposal["key"]) for proposal in proposals],
        "variants": variants,
        "common_set_ids": common if scope == "full_history" else [],
        "variant_set_ids": {
            str(proposal["key"]): [
                allocation.set_id for allocation in proposal["result"].allocations
                if allocation.units > 0
            ]
            for proposal in proposals
        },
    })
    if scope == "grid":
        metrics["grid_portfolio"] = True
        return metrics, "grid_bundle", f"Grid A/M/C | {datetime.now():%d.%m.%Y %H:%M}"
    name = f"A/M/C | Base {TYPE_LABELS.get(str(selected_inputs.get('composition_portfolio_type')), 'Moderado')} | {len(common)} sets | {datetime.now():%d.%m.%Y %H:%M}"
    return metrics, "bundle", name

def _single_metrics(
    selected: dict[str, Any], scope: str, standalone_improvement: bool, target_month: int | None,
) -> tuple[dict[str, Any], str, str]:
    """Metricas, tipo y nombre de un portafolio de una sola variante."""
    selected_result: PortfolioResult = selected["result"]
    selected_inputs: dict[str, Any] = selected["inputs"]
    metrics = _result_metrics(selected_inputs, selected_result)
    row_type = str(selected_inputs["portfolio_type"])
    if standalone_improvement:
        name = str(selected_inputs.get("improvement_label") or "").strip() or f"Mejora de #{int(selected_inputs['improvement_source_portfolio_id'])} | {TYPE_LABELS[row_type]} | {datetime.now():%d.%m.%Y %H:%M}"
        return metrics, row_type, name
    if scope == "monthly":
        name = f"{TYPE_LABELS.get(row_type, row_type)} | Mes {target_month:02d} | {selected_result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
        return metrics, row_type, name
    metrics["grid_portfolio"] = True
    name = (
        f"Grid {TYPE_LABELS.get(row_type, row_type)} | "
        f"{selected_result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
    )
    return metrics, row_type, name

def _insert_portfolio_row(
    conn: sqlite3.Connection,
    header: _SavedHeader,
    selected_inputs: dict[str, Any],
    selected_result: PortfolioResult,
) -> int:
    cur = conn.execute(
        """
        insert into portfolios (
            created_at,name,type,portfolio_type,num_symbols,account_capital,capital,
            target_valley_dd_pct,target_point_dd_pct,target_valley_dd,target_point_dd,
            actual_valley_dd,actual_point_dd,valley_usage_pct,point_usage_pct,total_net_profit,
            actual_closed_valley_dd,floating_dd_buffer,
            total_lot,total_units,active_strategies,target_strategies,stop_reason,binding_constraint,
            portfolio_scope,target_month,metrics_json
        ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            header.created_at, header.name, header.row_type, header.row_type,
            header.active_symbols, float(selected_inputs["capital"]),
            float(selected_inputs["capital"]), float(selected_inputs["valley_dd_pct"]),
            float(selected_inputs["point_dd_pct"]), selected_result.target_valley_dd,
            selected_result.target_point_dd, selected_result.actual_valley_dd,
            selected_result.actual_point_dd, selected_result.valley_usage_pct,
            selected_result.point_usage_pct, selected_result.total_net_profit,
            selected_result.actual_closed_valley_dd, selected_result.floating_dd_buffer,
            selected_result.total_lot, selected_result.total_units, selected_result.active_strategies,
            selected_result.active_strategies, selected_result.stop_reason,
            "valley", header.scope, header.target_month,
            json.dumps(header.metrics, ensure_ascii=True),
        ),
    )
    return int(cur.lastrowid)

def _insert_proposal_rows(
    conn: sqlite3.Connection, portfolio_id: int, rows_to_save: list[dict[str, Any]], bundle: bool,
) -> None:
    """Las asignaciones y el log de decisiones. Sin variante cuando no es paquete."""
    for proposal in rows_to_save:
        result = proposal["result"]
        key = str(proposal["key"]) if bundle else ""
        label = str(proposal["label"]) if bundle else ""
        for allocation in result.allocations:
            _insert_allocation(conn, portfolio_id, allocation, key, label)
        _insert_decisions(conn, portfolio_id, result, label)

def save_proposal(
    source: PortfolioSource,
    proposals: list[dict[str, Any]],
    selected_key: str,
    scope: str,
) -> int:
    scope = normalize_portfolio_scope(scope)
    selected = next((proposal for proposal in proposals if str(proposal["key"]) == selected_key), None)
    if selected is None:
        raise ValueError("La propuesta seleccionada ya no está disponible")
    selected_result: PortfolioResult = selected["result"]
    selected_inputs: dict[str, Any] = selected["inputs"]
    if not selected_result.allocations:
        raise ValueError("La propuesta no tiene asignaciones")
    if scope == "monthly" and selected_inputs.get("strict_yearly_month_validation") and not selected_result.seasonal_validation.get("passed"):
        raise ValueError("La propuesta mensual no pasó la validación estricta")
    created_at = datetime.now().isoformat(timespec="seconds")
    target_month = int(selected_inputs.get("target_month") or 0) or None
    standalone_improvement = (
        scope == "full_history" and len(proposals) == 1
        and int(selected_inputs.get("improvement_source_portfolio_id") or 0) > 0
    )
    bundle = scope in {"full_history", "grid"} and not standalone_improvement
    if bundle:
        metrics, row_type, name = _bundle_metrics(proposals, selected, selected_key, scope)
    else:
        metrics, row_type, name = _single_metrics(
            selected, scope, standalone_improvement, target_month
        )
    active_symbols = len({portfolio_symbol_key(allocation.symbol) for allocation in selected_result.allocations if allocation.units > 0})
    header = _SavedHeader(created_at, name, row_type, active_symbols, scope, target_month, metrics)
    with source.connect(write=True) as conn:
        try:
            portfolio_id = _insert_portfolio_row(
                conn, header, selected_inputs, selected_result
            )
            _insert_proposal_rows(
                conn, portfolio_id, proposals if bundle else [selected], bundle
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return portfolio_id
