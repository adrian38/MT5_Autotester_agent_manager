"""Lo que el nodo hace con la memoria UBS cuando el manager se lo pide.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/portfolio_save.py`, con otros
nombres de funcion. Cambiar una regla aqui **no tiene efecto** sobre ellos: ver
`AGENTS.md`, seccion «El nodo NO ejecuta este repositorio», y buscar la copia
por el texto del mensaje al usuario.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import candidate_verdict
from .common import safe_int, utc_now
from .node_settings import memory_path, read_settings
from .node_snapshots import _table_exists
from .portfolio_scope import normalize_portfolio_scope
from .portfolio_service import (
    PortfolioSource,
    normalize_portfolio_alias,
    save_portfolio_payload,
)


def _portfolio_source(handler) -> PortfolioSource:
    project = Path(str(handler.config["project_dir"])).expanduser().resolve()
    settings_path = Path(str(handler.config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    db_path = memory_path(handler.config, read_settings(settings_path))
    return PortfolioSource({
        "id": handler.config.get("node_id"),
        "name": handler.config.get("display_name") or handler.config.get("node_id"),
        "portfolio_project_dir": str(project),
        "portfolio_broker": handler.config.get("broker"),
        "portfolio_account_type": handler.config.get("account_type"),
        "portfolio_memory_path": str(db_path),
    })


def save_portfolio(handler, payload: dict[str, Any]) -> dict[str, Any]:
    return save_portfolio_payload(_portfolio_source(handler), payload)


def set_portfolio_alias(handler, payload: dict[str, Any]) -> dict[str, Any]:
    portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
    scope = normalize_portfolio_scope(payload.get("scope"))
    alias = _portfolio_source(handler).set_portfolio_alias(
        portfolio_id, scope, normalize_portfolio_alias(payload.get("alias"))
    )
    return {"portfolio_id": portfolio_id, "scope": scope, "alias": alias}


def exclude_portfolio_members(handler, payload: dict[str, Any]) -> dict[str, Any]:
    scope = normalize_portfolio_scope(payload.get("scope"))
    source = _portfolio_source(handler)
    # `verdict_applied` es la confirmación que el manager exige cuando el
    # motivo no es manual. Un nodo sin portar no devuelve esta clave y el
    # manager avisa en vez de dar por escrito un veredicto que no existe.
    reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
    verdict_applied = reason_code != candidate_verdict.MANUAL
    if payload.get("set_paths") is not None:
        portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
        quarantine_ids = source.remove_members_to_quarantine(payload, scope)
        return {
            "quarantine_ids": quarantine_ids,
            "deleted": True,
            "portfolio_id": portfolio_id,
            "scope": scope,
            "reason_code": reason_code,
            "verdict_applied": verdict_applied,
        }
    # Single exclusion (from a saved portfolio, or straight from the inventory
    # when no portfolio_id is sent). This MUST run on the node: the manager
    # only reads the node's memory through a read-only snapshot, and writing
    # to it directly over CIFS is unreliable because SQLite's WAL is not
    # coherent across a network share, so a manager-side quarantine/delete
    # silently failed to appear (the portfolio kept showing up after a
    # "successful" exclusion). remove_member_to_quarantine already falls back
    # to exclude_strategy when there is no portfolio_id.
    portfolio_id = safe_int(payload.get("portfolio_id"), 0)
    quarantine_id = source.remove_member_to_quarantine(payload, scope)
    return {
        "quarantine_id": quarantine_id,
        "portfolio_id": portfolio_id or None,
        "scope": scope,
        "reason_code": reason_code,
        "verdict_applied": verdict_applied,
    }


def requalify_portfolio_member(handler, payload: dict[str, Any]) -> dict[str, Any]:
    """Mueve una estrategia excluida entre los tres motivos y el pool.

    Corre en el nodo por lo mismo que la exclusión individual: el manager
    solo lee esta memoria por una copia de lectura, y escribirla
    directamente por CIFS o por un bind mount de Docker no falla en silencio
    sino con "disk I/O error", porque el modo WAL necesita un `-shm` que esos
    sistemas de ficheros no respaldan. Aquí la base es local.

    La confirmación es `requalified`: un nodo sin portar no tiene esta ruta y
    devuelve 404, que el manager traduce a qué hay que portar.
    """
    scope = normalize_portfolio_scope(payload.get("scope"))
    quarantine_key = str(payload.get("quarantine_id") or "").strip()
    if not quarantine_key:
        raise ValueError("Falta la estrategia excluida que se quiere reclasificar")
    source = _portfolio_source(handler)
    target = source.requalify_strategy(quarantine_key, str(payload.get("reason_code") or "pool"))
    return {
        "requalified": True,
        "quarantine_id": quarantine_key,
        "reason_code": target,
        "scope": scope,
    }


def delete_portfolio(handler, payload: dict[str, Any]) -> dict[str, Any]:
    portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=1)
    scope = normalize_portfolio_scope(payload.get("scope"))
    source = _portfolio_source(handler)
    source.delete_portfolio(portfolio_id, scope)
    if any(int(row["id"]) == portfolio_id for row in source.saved_portfolios(scope)["portfolios"]):
        raise RuntimeError(f"El portafolio #{portfolio_id} sigue presente después del borrado")
    return {"deleted": True, "portfolio_id": portfolio_id, "scope": scope}


def portfolios(handler, scope: str = "full_history") -> dict[str, Any]:
    portfolio_scope = normalize_portfolio_scope(scope)
    project = Path(str(handler.config["project_dir"])).expanduser().resolve()
    settings_path = Path(str(handler.config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    db_path = memory_path(handler.config, read_settings(settings_path))
    if not db_path.is_file():
        raise ValueError(f"No existe la memoria UBS: {db_path}")
    with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select * from portfolios where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
            (portfolio_scope,),
        ).fetchall() if _table_exists(conn, "portfolios") else []

    def value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
        return row[key] if key in row.keys() else default

    portfolios = [{
        "id": int(value(row, "id", 0) or 0), "created_at": str(value(row, "created_at", "") or ""),
        "name": str(value(row, "name", "") or ""),
        "portfolio_type": str(value(row, "portfolio_type", value(row, "type", "")) or ""),
        "portfolio_scope": portfolio_scope, "target_month": int(value(row, "target_month", 0) or 0) or None,
        "capital": float(value(row, "capital", value(row, "account_capital", 0)) or 0),
        "total_net_profit": float(value(row, "total_net_profit", 0) or 0),
        "actual_valley_dd": float(value(row, "actual_valley_dd", 0) or 0),
        "target_valley_dd": float(value(row, "target_valley_dd", 0) or 0),
        "valley_usage_pct": float(value(row, "valley_usage_pct", 0) or 0),
        "actual_point_dd": float(value(row, "actual_point_dd", 0) or 0),
        "target_point_dd": float(value(row, "target_point_dd", 0) or 0),
        "point_usage_pct": float(value(row, "point_usage_pct", 0) or 0),
        "total_lot": float(value(row, "total_lot", 0) or 0), "total_units": int(value(row, "total_units", 0) or 0),
        "active_strategies": int(value(row, "active_strategies", 0) or 0),
        "target_strategies": int(value(row, "target_strategies", 0) or 0),
        "stop_reason": str(value(row, "stop_reason", "") or ""),
        "binding_constraint": str(value(row, "binding_constraint", "") or ""),
    } for row in rows]
    if portfolio_scope == "full_history":
        for portfolio, row in zip(portfolios, rows):
            try:
                metrics = json.loads(value(row, "metrics_json", "{}") or "{}")
                inputs = metrics.get("inputs") if isinstance(metrics, dict) else {}
                portfolio["alias"] = normalize_portfolio_alias(
                    inputs.get("portfolio_alias") if isinstance(inputs, dict) else ""
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                portfolio["alias"] = ""
    return {
        "node": {"id": handler.config.get("node_id"), "name": handler.config.get("display_name") or handler.config.get("node_id"), "broker": handler.config.get("broker"), "account_type": handler.config.get("account_type")},
        "scope": portfolio_scope, "portfolios": portfolios,
        "summary": {"total": len(portfolios), "strategies": sum(item["active_strategies"] for item in portfolios), "latest_id": portfolios[0]["id"] if portfolios else None},
        "observed_at": utc_now(),
    }


def portfolio_detail(handler, portfolio_id: int, scope: str = "full_history") -> dict[str, Any]:
    listing = portfolios(handler, scope)
    selected = next((item for item in listing["portfolios"] if item["id"] == portfolio_id), None)
    if selected is None:
        raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
    project = Path(str(handler.config["project_dir"])).expanduser().resolve()
    settings_path = Path(str(handler.config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute(): settings_path = project / settings_path
    db_path = memory_path(handler.config, read_settings(settings_path))
    with contextlib.closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select metrics_json from portfolios where id=?", (portfolio_id,)).fetchone()
        try:
            parsed = json.loads(row["metrics_json"] or "{}") if row else {}
            metrics = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            metrics = {}
        members: list[dict[str, Any]] = []
        if _table_exists(conn, "portfolio_allocations"):
            members = [dict(item) for item in conn.execute("select * from portfolio_allocations where portfolio_id=? order by variant_key,set_id,units desc", (portfolio_id,)).fetchall()]
        if not members and _table_exists(conn, "portfolio_members"):
            for item in conn.execute("select * from portfolio_members where portfolio_id=? order by lot desc", (portfolio_id,)).fetchall():
                raw = dict(item)
                members.append({"variant_key":raw.get("variant_key") or "","variant_label":raw.get("variant_label") or "","set_id":raw.get("set_path") or "","candidate_id":raw.get("candidate_id") or "","symbol":raw.get("symbol") or "","timeframe":raw.get("period") or "","units":int(round(float(raw.get("lot") or 0)/0.01)),"lot":float(raw.get("lot") or 0),"lot_size_step":float(raw.get("lot_size_step") or .01),"net_profit_contribution":float(raw.get("combined_net_profit") or 0),"standalone_valley_dd":float(raw.get("standalone_dd") or 0),"standalone_point_dd":0.0,"set_path":raw.get("set_path") or "","margin_required":0.0,"margin_pct":0.0})
    selected["metrics"] = {"inputs": metrics.get("inputs") if isinstance(metrics.get("inputs"),dict) else {}, "stress_bootstrap": metrics.get("stress_bootstrap") if isinstance(metrics.get("stress_bootstrap"),dict) else {}, "common_set_ids": metrics.get("common_set_ids") if isinstance(metrics.get("common_set_ids"),list) else [], "variant_order": metrics.get("variant_order") if isinstance(metrics.get("variant_order"),list) else []}
    selected["members"] = [{
        "variant_key": str(raw.get("variant_key") or ""), "variant_label": str(raw.get("variant_label") or ""),
        "set_id": str(raw.get("set_id") or ""), "set_name": Path(str(raw.get("set_path") or raw.get("set_id") or "")).name,
        "set_path": str(raw.get("set_path") or raw.get("set_id") or ""),
        "candidate_id": str(raw.get("candidate_id") or ""), "symbol": str(raw.get("symbol") or ""),
        "timeframe": str(raw.get("timeframe") or ""), "units": int(raw.get("units") or 0),
        "lot": float(raw.get("lot") or 0), "lot_size_step": float(raw.get("lot_size_step") or 0),
        "net_profit_contribution": float(raw.get("net_profit_contribution") or 0),
        "standalone_valley_dd": float(raw.get("standalone_valley_dd") or 0),
        "standalone_point_dd": float(raw.get("standalone_point_dd") or 0),
        "margin_required": float(raw.get("margin_required") or 0), "margin_pct": float(raw.get("margin_pct") or 0),
        "max_balance_dd_001": float(raw.get("max_balance_dd_001") or 0),
        "max_equity_dd_001": float(raw.get("max_equity_dd_001") or 0),
        "floating_dd_source": str(raw.get("floating_dd_source") or ""),
        "standalone_floating_dd": float(raw.get("standalone_floating_dd") or 0),
        "recent_net_profit_001": float(raw.get("recent_net_profit_001") or 0),
        "recent_equity_dd_001": float(raw.get("recent_equity_dd_001") or 0),
        "has_recent_performance": bool(raw.get("has_recent_performance") or False),
        "margin_leverage": float(raw.get("margin_leverage") or 0),
        "margin_contract_size": float(raw.get("margin_contract_size") or 0),
        "margin_price": float(raw.get("margin_price") or 0),
        "is_report_path": str(raw.get("is_report_path") or ""),
        "oos_report_path": str(raw.get("oos_report_path") or ""),
        "final_tick_report_path": str(raw.get("final_tick_report_path") or ""),
        "full_history_report_path": str(raw.get("full_history_report_path") or ""),
    } for raw in members]
    return {"node": listing["node"], "scope": listing["scope"], "portfolio": selected, "observed_at": utc_now()}
