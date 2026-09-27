from __future__ import annotations

import json
import math
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from portfolio_manager.ubs_portfolio import (
    BootstrapDrawdownAnalysis,
    OptimizationDecision,
    PortfolioResult,
    StrategyAllocation,
    UnusedSetInfo,
    portfolio_symbol_key,
)

from .common import safe_float, safe_int
from .portfolio_identity import TYPE_LABELS
from .portfolio_persistence import (
    _insert_allocation,
    _insert_decisions,
    _result_metrics,
    result_payload,
    save_proposal,
    settings_inputs,
)
from .portfolio_schema import _table_exists
from .portfolio_scope import normalize_portfolio_scope
from .portfolio_settings import normalize_settings

if TYPE_CHECKING:
    from .portfolio_service import PortfolioSource


def serialize_portfolio_proposals(
    proposals: list[dict[str, Any]], request_id: str
) -> list[dict[str, Any]]:
    """Convert in-memory optimizer results into an authenticated node payload."""
    if not request_id:
        raise ValueError("Falta el identificador de la solicitud de guardado")
    payload: list[dict[str, Any]] = []
    for proposal in proposals:
        result = proposal.get("result")
        inputs = proposal.get("inputs")
        if not isinstance(result, PortfolioResult) or not isinstance(inputs, dict):
            raise ValueError("La propuesta calculada no tiene un formato guardable")
        result_payload = asdict(result)
        # Broker nodes anteriores insertan el log de decisiones en columnas
        # NOT NULL. JSON convierte +/-Infinity en null, por lo que hay que
        # estabilizar esos valores antes de cruzar la red, no solo al escribir
        # desde una version nueva del nodo.
        decision_number_fields = (
            "gain", "valley_cost", "point_cost", "score",
            "portfolio_net_profit_after", "portfolio_valley_dd_after",
            "portfolio_point_dd_after",
        )
        for decision in result_payload.get("decision_log") or []:
            if not isinstance(decision, dict):
                continue
            for field_name in decision_number_fields:
                parsed = safe_float(decision.get(field_name), 0.0)
                decision[field_name] = parsed if math.isfinite(parsed) else 0.0
        payload.append({
            "key": str(proposal.get("key") or ""),
            "label": str(proposal.get("label") or ""),
            "reserve_pct": float(proposal.get("reserve_pct") or 0),
            "auto_adjusted_valley": bool(proposal.get("auto_adjusted_valley", False)),
            "requested_valley_dd_pct": float(
                proposal.get("requested_valley_dd_pct")
                or inputs.get("valley_dd_pct")
                or 0
            ),
            "adjusted_valley_dd_pct": float(
                proposal.get("adjusted_valley_dd_pct")
                or inputs.get("valley_dd_pct")
                or 0
            ),
            # Segunda red en el limite HTTP: lo que salga de aqui se serializa a
            # JSON, asi que aqui no puede quedar ningun objeto vivo aunque el
            # llamante se haya olvidado de filtrarlo.
            "inputs": {**settings_inputs(inputs), "_manager_save_request_id": request_id},
            "result": result_payload,
        })
    return payload


LEGACY_ALLOCATION_RISK_FIELDS = {
    "max_balance_dd_001",
    "max_equity_dd_001",
    "floating_dd_source",
    "standalone_floating_dd",
    "recent_net_profit_001",
    "recent_equity_dd_001",
    "has_recent_performance",
    "final_tick_report_path",
    "full_history_report_path",
}
LEGACY_RESULT_RISK_FIELDS = {
    "actual_closed_valley_dd", "floating_dd_buffer", "floating_overlap_audit",
}


def legacy_compatible_portfolio_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Downgrade only the wire shape for nodes from before equity-risk fields.

    Core safety values remain in ``actual_valley_dd`` and
    ``standalone_valley_dd``; only the new audit breakdown is omitted.
    """
    compatible = dict(payload)
    compatible_proposals: list[dict[str, Any]] = []
    for raw_proposal in payload.get("proposals") or []:
        if not isinstance(raw_proposal, dict):
            continue
        proposal = dict(raw_proposal)
        raw_result = proposal.get("result")
        if isinstance(raw_result, dict):
            result = {
                key: value for key, value in raw_result.items()
                if key not in LEGACY_RESULT_RISK_FIELDS
            }
            result["allocations"] = [
                {
                    key: value for key, value in allocation.items()
                    if key not in LEGACY_ALLOCATION_RISK_FIELDS
                }
                for allocation in raw_result.get("allocations") or []
                if isinstance(allocation, dict)
            ]
            proposal["result"] = result
        compatible_proposals.append(proposal)
    compatible["proposals"] = compatible_proposals
    return compatible


def _supported_dataclass_values(dataclass_type: type, raw: dict[str, Any]) -> dict[str, Any]:
    supported = {item.name for item in fields(dataclass_type)}
    return {key: value for key, value in raw.items() if key in supported}


def deserialize_portfolio_proposals(
    payload: object, scope: str, broker: str
) -> list[dict[str, Any]]:
    """Validate and rebuild optimizer dataclasses inside the node process."""
    if not isinstance(payload, list) or not payload:
        raise ValueError("No se recibieron propuestas para guardar")
    proposals: list[dict[str, Any]] = []
    for raw_proposal in payload:
        if not isinstance(raw_proposal, dict):
            raise ValueError("Propuesta remota inválida")
        raw_result = raw_proposal.get("result")
        raw_inputs = raw_proposal.get("inputs")
        if not isinstance(raw_result, dict) or not isinstance(raw_inputs, dict):
            raise ValueError("La propuesta remota no contiene inputs y resultado")
        result_values = dict(raw_result)
        result_values["allocations"] = [
            StrategyAllocation(**_supported_dataclass_values(StrategyAllocation, item))
            for item in result_values.get("allocations") or []
            if isinstance(item, dict)
        ]
        result_values["decision_log"] = [
            OptimizationDecision(**_supported_dataclass_values(OptimizationDecision, item))
            for item in result_values.get("decision_log") or []
            if isinstance(item, dict)
        ]
        result_values["unused_sets"] = [
            UnusedSetInfo(**_supported_dataclass_values(UnusedSetInfo, item))
            for item in result_values.get("unused_sets") or []
            if isinstance(item, dict)
        ]
        stress = result_values.get("stress_bootstrap")
        result_values["stress_bootstrap"] = (
            BootstrapDrawdownAnalysis(**_supported_dataclass_values(BootstrapDrawdownAnalysis, stress))
            if isinstance(stress, dict) else None
        )
        try:
            result = PortfolioResult(**_supported_dataclass_values(PortfolioResult, result_values))
        except TypeError as exc:
            raise ValueError(f"Resultado de propuesta incompatible: {exc}") from exc
        proposals.append({
            "key": str(raw_proposal.get("key") or ""),
            "label": str(raw_proposal.get("label") or ""),
            "reserve_pct": float(raw_proposal.get("reserve_pct") or 0),
            "auto_adjusted_valley": bool(raw_proposal.get("auto_adjusted_valley", False)),
            "requested_valley_dd_pct": float(
                raw_proposal.get("requested_valley_dd_pct")
                or raw_inputs.get("valley_dd_pct")
                or 0
            ),
            "adjusted_valley_dd_pct": float(
                raw_proposal.get("adjusted_valley_dd_pct")
                or raw_inputs.get("valley_dd_pct")
                or 0
            ),
            "inputs": normalize_settings(scope, raw_inputs, broker),
            "result": result,
        })
    return proposals


def _saved_request_portfolio_id(source: PortfolioSource, request_id: str, scope: str) -> int | None:
    portfolio_scope = normalize_portfolio_scope(scope)
    with source.connect() as conn:
        if not _table_exists(conn, "portfolios"):
            return None
        rows = conn.execute(
            "select id,metrics_json from portfolios "
            "where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
            (portfolio_scope,),
        ).fetchall()
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except json.JSONDecodeError:
            continue
        inputs = metrics.get("inputs") if isinstance(metrics, dict) else None
        if isinstance(inputs, dict) and inputs.get("_manager_save_request_id") == request_id:
            return int(row["id"])
    return None


def save_portfolio_payload(source: PortfolioSource, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a manager proposal locally on its owning node, with retry deduplication."""
    scope = normalize_portfolio_scope(payload.get("scope"))
    request_id = str(payload.get("request_id") or "").strip()
    selected_key = str(payload.get("selected_key") or "").strip()
    operation = str(payload.get("operation") or "generate")
    if not request_id or not selected_key:
        raise ValueError("Solicitud de guardado incompleta")
    if operation not in {"generate", "reoptimize", "complete"}:
        raise ValueError("Operación de guardado desconocida")
    existing_id = _saved_request_portfolio_id(source, request_id, scope)
    if existing_id is not None:
        return {"portfolio_id": existing_id, "request_id": request_id, "deduplicated": True}
    proposals = deserialize_portfolio_proposals(payload.get("proposals"), scope, source.broker)
    if operation in {"reoptimize", "complete"}:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
        saved_id = replace_saved_proposal(
            source, proposals, selected_key, scope, portfolio_id,
            "Antes de reoptimizar" if operation == "reoptimize" else "Antes de completar portafolio",
        )
    else:
        saved_id = save_proposal(source, proposals, selected_key, scope)
    detail = source.saved_portfolio_detail(saved_id, scope)["portfolio"]
    if not detail.get("members"):
        raise ValueError(f"El portafolio #{saved_id} se escribió sin estrategias")
    source.notify(
        f"Portfolio Builder guardado: #{saved_id}, net {float(detail.get('total_net_profit') or 0):,.2f}, "
        f"lote {float(detail.get('total_lot') or 0):.2f}, {int(detail.get('active_strategies') or 0)} estrategias"
    )
    return {"portfolio_id": saved_id, "request_id": request_id, "deduplicated": False}


def _proposal_metrics(
    proposals: list[dict[str, Any]], selected: dict[str, Any], selected_key: str, scope: str
) -> tuple[dict[str, Any], str, str]:
    result: PortfolioResult = selected["result"]
    inputs: dict[str, Any] = selected["inputs"]
    if scope in {"full_history", "grid"}:
        common = [allocation.set_id for allocation in result.allocations if allocation.units > 0]
        common_set = set(common)
        if scope == "full_history" and any({allocation.set_id for allocation in item["result"].allocations if allocation.units > 0} != common_set for item in proposals):
            raise ValueError("Las variantes A/M/C no comparten la misma composición")
        variants: dict[str, Any] = {}
        for item in proposals:
            variant_result: PortfolioResult = item["result"]
            payload = _result_metrics(item["inputs"], variant_result)
            payload.update({"label": item["label"], "summary": result_payload(variant_result), "allocations": [asdict(value) for value in variant_result.allocations]})
            variants[str(item["key"])] = payload
        metrics = _result_metrics(inputs, result)
        metrics.update({"portfolio_bundle": True, "bundle_display": "Grid A/M/C" if scope == "grid" else "A/M/C", "selected_variant": selected_key,
                        "variant_order": [str(item["key"]) for item in proposals], "variants": variants,
                        "common_set_ids": common if scope == "full_history" else [],
                        "variant_set_ids": {str(item["key"]): [allocation.set_id for allocation in item["result"].allocations if allocation.units > 0] for item in proposals}})
        if scope == "grid":
            metrics["grid_portfolio"] = True
            row_type = "grid_bundle"
            name = f"Grid A/M/C | {datetime.now():%d.%m.%Y %H:%M}"
        else:
            row_type = "bundle"
            name = f"A/M/C | Base {TYPE_LABELS.get(str(inputs.get('composition_portfolio_type')), 'Moderado')} | {len(common)} sets | {datetime.now():%d.%m.%Y %H:%M}"
    else:
        metrics = _result_metrics(inputs, result)
        row_type = str(inputs["portfolio_type"])
        name = f"{TYPE_LABELS.get(row_type, row_type)} | Mes {int(inputs.get('target_month') or 0):02d} | {result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
    return metrics, row_type, name


def replace_saved_proposal(
    source: PortfolioSource,
    proposals: list[dict[str, Any]],
    selected_key: str,
    scope: str,
    portfolio_id: int,
    reason: str,
) -> int:
    selected = next((proposal for proposal in proposals if str(proposal["key"]) == selected_key), None)
    if selected is None:
        raise ValueError("La propuesta seleccionada ya no está disponible")
    result: PortfolioResult = selected["result"]
    inputs: dict[str, Any] = selected["inputs"]
    if not result.allocations:
        raise ValueError("La propuesta no tiene asignaciones")
    metrics, row_type, name = _proposal_metrics(proposals, selected, selected_key, scope)
    active_symbols = len({portfolio_symbol_key(item.symbol) for item in result.allocations if item.units > 0})
    target_month = int(inputs.get("target_month") or 0) or None
    with source.connect(write=True) as conn:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe")
        target_strategies = max(int(portfolio["target_strategies"] or 0), result.active_strategies)
        try:
            source._save_version(conn, portfolio_id, reason)
            conn.execute(
                """update portfolios set name=?,type=?,portfolio_type=?,num_symbols=?,account_capital=?,capital=?,
                   target_valley_dd_pct=?,target_point_dd_pct=?,target_valley_dd=?,target_point_dd=?,actual_valley_dd=?,
                   actual_point_dd=?,valley_usage_pct=?,point_usage_pct=?,total_net_profit=?,total_lot=?,total_units=?,
                   actual_closed_valley_dd=?,floating_dd_buffer=?,
                   active_strategies=?,target_strategies=?,stop_reason=?,binding_constraint=?,portfolio_scope=?,target_month=?,metrics_json=?
                   where id=?""",
                (name, row_type, row_type, active_symbols, float(inputs["capital"]), float(inputs["capital"]),
                 float(inputs["valley_dd_pct"]), float(inputs["point_dd_pct"]), result.target_valley_dd,
                 result.target_point_dd, result.actual_valley_dd, result.actual_point_dd, result.valley_usage_pct,
                 result.point_usage_pct, result.total_net_profit, result.total_lot, result.total_units,
                 result.actual_closed_valley_dd, result.floating_dd_buffer,
                 result.active_strategies, target_strategies, result.stop_reason, "valley", scope, target_month,
                 json.dumps(metrics, ensure_ascii=True), portfolio_id),
            )
            for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members"):
                conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
            rows_to_save = proposals if scope in {"full_history", "grid"} else [selected]
            for proposal in rows_to_save:
                variant_result: PortfolioResult = proposal["result"]
                variant_key = str(proposal["key"]) if scope in {"full_history", "grid"} else ""
                variant_label = str(proposal["label"]) if scope in {"full_history", "grid"} else ""
                for allocation in variant_result.allocations:
                    _insert_allocation(conn, portfolio_id, allocation, variant_key, variant_label)
                _insert_decisions(conn, portfolio_id, variant_result, variant_label)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return portfolio_id


def proposal_diff(previous_members: list[dict[str, Any]], result: PortfolioResult) -> list[dict[str, Any]]:
    before = {str(item.get("set_path") or item.get("set_id") or ""): item for item in previous_members}
    after = {str(item.set_path or item.set_id): item for item in result.allocations}
    rows: list[dict[str, Any]] = []
    for set_path in sorted(set(before) | set(after), key=lambda value: Path(value).name.casefold()):
        old, new = before.get(set_path), after.get(set_path)
        old_units = int(old.get("units") or 0) if old else 0
        new_units = int(new.units) if new else 0
        state = "NUEVA" if old is None else "RETIRADA" if new is None else "AJUSTADA" if old_units != new_units else "SIN CAMBIO"
        rows.append({
            "set_path": set_path, "set_name": Path(set_path).name,
            "symbol": str(new.symbol if new else old.get("symbol") or ""),
            "old_units": old_units, "new_units": new_units, "delta_units": new_units - old_units,
            "old_lot": float(old.get("lot") or 0) if old else 0.0,
            "new_lot": float(new.lot) if new else 0.0,
            "state": state,
        })
    return rows
