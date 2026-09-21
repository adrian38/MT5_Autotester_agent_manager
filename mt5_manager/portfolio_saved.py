"""Leer una cartera ya guardada: listarla, reconstruir sus ajustes y remedirla.

Encima de `portfolio_identity` y `portfolio_settings`. Todo lo de aqui trabaja
sobre filas de la memoria UBS, nunca sobre una propuesta recien calculada.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any, Callable

from portfolio_manager.ubs_portfolio import (
    bootstrap_valley_drawdown,
    portfolio_group_summary,
    portfolio_symbol_key,
)

from .common import safe_int
from .portfolio_identity import (
    IMPROVEMENT_PRIORITY_LABELS,
    TYPE_LABELS,
    _normalized_improvement_lineage,
    _valid_portfolio_uid,
    normalize_portfolio_alias,
)
from .portfolio_settings import ASSET_GROUPS, COMMON_DEFAULTS, DEFAULT_ACCOUNT_LEVERAGE


def _saved_portfolio_row(row: Any, value: Callable[..., Any], portfolio_scope: str) -> dict[str, Any]:
    """Una fila de `portfolios` como la espera el listado, con tipos ya fijados."""
    return {
        "id": int(value(row, "id", 0) or 0),
        "created_at": str(value(row, "created_at", "") or ""),
        "name": str(value(row, "name", "") or ""),
        "portfolio_type": str(value(row, "portfolio_type", value(row, "type", "")) or ""),
        "portfolio_scope": portfolio_scope,
        "target_month": int(value(row, "target_month", 0) or 0) or None,
        "capital": float(value(row, "capital", value(row, "account_capital", 0)) or 0),
        "total_net_profit": float(value(row, "total_net_profit", 0) or 0),
        "actual_valley_dd": float(value(row, "actual_valley_dd", 0) or 0),
        "actual_closed_valley_dd": float(value(row, "actual_closed_valley_dd", 0) or 0),
        "floating_dd_buffer": float(value(row, "floating_dd_buffer", 0) or 0),
        "target_valley_dd": float(value(row, "target_valley_dd", 0) or 0),
        "target_valley_dd_pct": float(value(row, "target_valley_dd_pct", 0) or 0),
        "valley_usage_pct": float(value(row, "valley_usage_pct", 0) or 0),
        "actual_point_dd": float(value(row, "actual_point_dd", 0) or 0),
        "target_point_dd": float(value(row, "target_point_dd", 0) or 0),
        "target_point_dd_pct": float(value(row, "target_point_dd_pct", 0) or 0),
        "point_usage_pct": float(value(row, "point_usage_pct", 0) or 0),
        "total_lot": float(value(row, "total_lot", 0) or 0),
        "total_units": int(value(row, "total_units", 0) or 0),
        "active_strategies": int(value(row, "active_strategies", 0) or 0),
        "target_strategies": int(value(row, "target_strategies", 0) or 0),
        "stop_reason": str(value(row, "stop_reason", "") or ""),
        "binding_constraint": str(value(row, "binding_constraint", "") or ""),
    }

def _improvement_origin(
    inputs: dict[str, Any], audit: dict[str, Any], source_id: int, mode: str,
) -> dict[str, Any]:
    """El linaje de una mejora, reconstruido de los ajustes o de la auditoria."""
    origin: dict[str, Any] = {"source_id": source_id, "mode": mode}
    source_uid = _valid_portfolio_uid(
        inputs.get("improvement_parent_uid") or audit.get("parent_uid")
    )
    if source_uid:
        origin["source_uid"] = source_uid
    origin["root_id"] = safe_int(
        inputs.get("improvement_root_portfolio_id")
        or audit.get("root_portfolio_id")
        or source_id,
        source_id,
    )
    root_uid = _valid_portfolio_uid(
        inputs.get("improvement_root_uid") or audit.get("root_uid")
    )
    if root_uid:
        origin["root_uid"] = root_uid
    origin["depth"] = max(
        1, safe_int(inputs.get("improvement_depth") or audit.get("depth"), 1)
    )
    lineage = _normalized_improvement_lineage(
        inputs.get("improvement_lineage") or audit.get("lineage")
    )
    if lineage:
        origin["lineage"] = lineage
    # Con qué criterio se eligió esta mejora. Sin él, dos mejoras del mismo
    # portafolio y modo son idénticas en la lista aunque una venga de maximizar
    # beneficio/DD y la otra de minimizar estrés.
    priority = str(
        inputs.get("improvement_selection_priority")
        or audit.get("selection_priority")
        or ""
    )
    if priority in IMPROVEMENT_PRIORITY_LABELS:
        origin["priority"] = priority
        origin["priority_label"] = IMPROVEMENT_PRIORITY_LABELS[priority]
    added = audit.get("added_count")
    if added is not None:
        origin["added_count"] = int(added)
    return origin

def _annotate_improvement_lineage(
    portfolios: list[dict[str, Any]], rows: list[Any], value: Callable[..., Any],
) -> None:
    """Recupera alias y linaje de `metrics_json`, sin reescribir la memoria.

    Los nodos embebidos antiguos guardaban una mejora de un solo modo con
    nombre generico de paquete. Una fila con metadatos ilegibles se queda como
    esta: el listado tiene que salir igual.
    """
    for portfolio, row in zip(portfolios, rows):
        try:
            metrics = json.loads(value(row, "metrics_json", "{}") or "{}")
            inputs = metrics.get("inputs") or {}
            portfolio["alias"] = normalize_portfolio_alias(inputs.get("portfolio_alias"))
            audit = (metrics.get("seasonal_validation") or {}).get("portfolio_improvement") or {}
            source_id = int(inputs.get("improvement_source_portfolio_id") or audit.get("source_portfolio_id") or 0)
            mode = inputs.get("improvement_portfolio_type") or audit.get("target_portfolio_type") or inputs.get("portfolio_type")
            if source_id > 0 and mode in TYPE_LABELS:
                origin = _improvement_origin(inputs, audit, source_id, mode)
                portfolio["improvement_origin"] = origin
                visible_label = str(
                    inputs.get("improvement_label")
                    or audit.get("label")
                    or f"Mejora del portafolio #{source_id} | modo {TYPE_LABELS[mode]}"
                ).strip()
                origin["label"] = visible_label
                portfolio["name"] = visible_label
        except (ValueError, TypeError, AttributeError):
            pass

SAVED_INPUT_FALLBACKS: dict[str, Any] = {
    "top_k_per_symbol": 3,
    "max_total_candidates": 30,
    "max_units_per_set": None,
    "max_total_units": None,
    "max_units_per_symbol": None,
    "max_sets_per_symbol": 1,
    "run_local_search": True,
    "deep_optimization": False,
    "use_correlation": True,
    "require_3_positive_months_6m": False,
    "grid_off": False,
    "exclude_used_sets": True,
    "experimental_full_search": False,
    "min_strategy_recent_contribution_pct": COMMON_DEFAULTS["min_strategy_recent_contribution_pct"],
    "exclude_monthly_used": False,
    "corr_with_monthly_portfolios": False,
    "strict_yearly_month_validation": False,
    "experimental_monthly_search": False,
    "daily_dd_full_history": False,
    "dd_reserve_pct": 0.0,
    "search_restarts": 0,
    "max_pair_corr": 0.35,
    "max_downside_corr": 0.25,
    "max_dd_overlap": 0.35,
    "max_portfolio_corr": 0.50,
    "allowed_asset_groups": list(ASSET_GROUPS),
    # Las carteras anteriores a este campo no pueden reconstruir la elección
    # original. Usamos el mismo valor inicial que ofrece AXI en el formulario y
    # dejamos que el diálogo de mejora lo muestre y permita corregirlo antes de
    # recalcular.
    "account_leverage": DEFAULT_ACCOUNT_LEVERAGE,
    "max_margin_pct": 100.0,
    "validate_margin": True,
}

def _saved_risk_targets(detail: dict[str, Any]) -> tuple[float, float, float]:
    """Capital y limites de DD, en porcentaje, de una fila guardada."""
    capital = float(detail.get("capital") or detail.get("account_capital") or 0)
    valley_pct = float(detail.get("target_valley_dd_pct") or 0)
    if valley_pct <= 0 and capital > 0:
        valley_pct = float(detail.get("target_valley_dd") or 0) * 100.0 / capital
    return capital, valley_pct, float(detail.get("target_point_dd_pct") or 0) or valley_pct

def _saved_portfolio_type(
    detail: dict[str, Any], metrics: dict[str, Any], stored: dict[str, Any],
) -> tuple[str, str]:
    """El tipo de la fila y el tipo con el que se calculo la composicion.

    En un paquete A/M/C la fila dice `bundle`: el tipo real es el de la
    composicion base, no el de la variante seleccionada.
    """
    saved_row_type = str(detail.get("portfolio_type") or detail.get("type") or "balanced").lower()
    if saved_row_type not in {"bundle", "grid_bundle"}:
        return saved_row_type, saved_row_type
    return saved_row_type, str(
        stored.get("composition_portfolio_type")
        or metrics.get("composition_portfolio_type")
        or stored.get("portfolio_type")
        or "balanced"
    ).lower()

def _migrated_asset_groups(values: dict[str, Any]) -> list[str]:
    """Los grupos de una cartera anterior al universo de ocho, ya migrados.

    Replica la migracion del escritorio: `IndicesEnergies` se parte en dos y a
    lo que no conoce ninguno de los cinco grupos nuevos se le anaden.
    """
    stored_groups = set(values.get("allowed_asset_groups") or [])
    legacy_groups = not stored_groups.intersection({"Indices", "Energies", "Crypto", "Bonds", "Softs"})
    if "IndicesEnergies" in stored_groups:
        stored_groups.remove("IndicesEnergies")
        stored_groups.update(("Indices", "Energies"))
    if legacy_groups:
        stored_groups.update(("Crypto", "Bonds", "Softs"))
    return sorted(stored_groups)

def _stored_metrics(portfolio: Any) -> dict[str, Any]:
    """Las metricas guardadas de una fila, o un diccionario vacio si no se leen."""
    try:
        metrics = json.loads(portfolio["metrics_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return metrics if isinstance(metrics, dict) else {}

def _nominal_valley_limit(portfolio: Any) -> float:
    return float(portfolio["capital"] or portfolio["account_capital"] or 0) * float(
        portfolio["target_valley_dd_pct"] or 0
    ) / 100.0

def _blank_recalculated_portfolio(
    conn: sqlite3.Connection, portfolio: Any, portfolio_id: int, metrics: dict[str, Any],
) -> None:
    """Deja a cero una cartera que se ha quedado sin ninguna asignacion."""
    metrics.update({"equity_curve_2020_2026": [0.0], "group_summary": {}, "seasonal_coverage": {}, "seasonal_validation": {}})
    metrics["stress_bootstrap"] = asdict(bootstrap_valley_drawdown(
        [0.0],
        nominal_valley_dd_limit=_nominal_valley_limit(portfolio),
        effective_valley_dd_limit=float(portfolio["target_valley_dd"] or 0),
    ))
    conn.execute(
        "update portfolios set num_symbols=0,actual_valley_dd=0,actual_point_dd=0,actual_closed_valley_dd=0,floating_dd_buffer=0,valley_usage_pct=0,point_usage_pct=0,total_net_profit=0,total_lot=0,total_units=0,active_strategies=0,metrics_json=? where id=?",
        (json.dumps(metrics, ensure_ascii=True), portfolio_id),
    )

def _allocation_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las asignaciones guardadas en la forma que espera la carga de informes."""
    return [{
        "candidate_id": row.get("candidate_id"), "set_path": row.get("set_path") or row.get("set_id"),
        "symbol": row.get("symbol"), "target_symbol": row.get("symbol"), "period": row.get("timeframe"),
        "family": "", "is_report_path": row.get("is_report_path"), "oos_report_path": row.get("oos_report_path"),
        "max_balance_dd_001": row.get("max_balance_dd_001"),
        "max_equity_dd_001": row.get("max_equity_dd_001"),
        "floating_dd_source": row.get("floating_dd_source"),
        "recent_net_profit_001": row.get("recent_net_profit_001"),
        "recent_equity_dd_001": row.get("recent_equity_dd_001"),
        "has_recent_performance": row.get("has_recent_performance"),
        "final_tick_report_path": row.get("final_tick_report_path"),
        "full_history_report_path": row.get("full_history_report_path"),
    } for row in rows]

def _recalculated_metrics(
    metrics: dict[str, Any],
    evaluation: Any,
    strategies: list[Any],
    units: dict[str, int],
    portfolio: Any,
) -> None:
    metrics.update({
        "equity_curve_2020_2026": evaluation.equity_curve_2020_2026,
        "group_summary": portfolio_group_summary(strategies, units),
        "actual_closed_valley_dd": evaluation.closed_valley_dd,
        "floating_dd_buffer": evaluation.floating_dd_buffer,
        "seasonal_coverage": {
            strategy.set_id: {"target_month": strategy.target_month, "years": list(strategy.month_years),
                "positive_years": list(strategy.positive_month_years), "year_count": len(strategy.month_years),
                "positive_year_count": len(strategy.positive_month_years), "trades": strategy.trades_2020_2026}
            for strategy in strategies if strategy.target_month is not None and units.get(strategy.set_id, 0) > 0
        },
        "stress_bootstrap": asdict(bootstrap_valley_drawdown(
            evaluation.equity_curve_2020_2026,
            nominal_valley_dd_limit=_nominal_valley_limit(portfolio),
            effective_valley_dd_limit=float(portfolio["target_valley_dd"] or 0),
        )),
    })

def _update_recalculated_row(
    conn: sqlite3.Connection,
    portfolio_id: int,
    metrics: dict[str, Any],
    evaluation: Any,
    strategies: list[Any],
    units: dict[str, int],
) -> None:
    conn.execute(
        """update portfolios set num_symbols=?,actual_valley_dd=?,actual_point_dd=?,actual_closed_valley_dd=?,floating_dd_buffer=?,valley_usage_pct=?,point_usage_pct=?,
           total_net_profit=?,total_lot=?,total_units=?,active_strategies=?,metrics_json=? where id=?""",
        (len({portfolio_symbol_key(item.symbol) for item in strategies if units.get(item.set_id, 0) > 0}),
         evaluation.valley_dd, evaluation.point_dd, evaluation.closed_valley_dd, evaluation.floating_dd_buffer,
         evaluation.valley_usage_pct, evaluation.point_usage_pct,
         evaluation.total_net_profit, evaluation.total_lot, evaluation.total_units, evaluation.active_strategies,
         json.dumps(metrics, ensure_ascii=True), portfolio_id),
    )
