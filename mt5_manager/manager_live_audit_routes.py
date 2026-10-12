"""Las rutas de la auditoria en vivo, en su propio modulo.

Mismo patron que `correlation_routes`: funciones que reciben el handler.

Lanzar un uso exige dos cosas del agente: que traiga el auditor nuevo y que
haya una cuenta de restauracion guardada. Sin ella los terminales se quedarian
con la cuenta del uso, que es de un tercero.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Any

from . import live_audit_analysis
from . import live_audit_attribution, live_audit_autolot
from . import live_audit_degradation
from . import manager_http
from .common import safe_int
from .portfolio_service import PortfolioSource


def strategy_history(handler, node_id: str, audit_id: str) -> dict[str, Any]:
    """Veredicto de degradacion por estrategia del uso pedido.

    Se lee del nodo la ejecucion ya analizada y de la memoria UBS del agente el
    historico de cada candidato. Es una ruta aparte y no parte del detalle
    porque la pantalla de configuracion sondea el detalle en bucle y esto abre
    la base del agente.
    """
    node = handler._node(node_id)
    status, value = manager_http.node_request(
        node, "GET", f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}", timeout=10
    )
    if status != 200:
        raise ValueError(str((value or {}).get("error") if isinstance(value, dict) else value))
    analysed = analysed_live_audit(handler, node_id, audit_id, value)
    state = analysed.get("audit") if isinstance(analysed.get("audit"), dict) else analysed
    result = (state or {}).get("last_result")
    if not isinstance(result, dict):
        return {"strategies": [], "reference_lot": live_audit_degradation.REFERENCE_LOT}
    return degradation_report(PortfolioSource(node), result)


def _period_days(result: dict[str, Any]) -> float:
    start = str(result.get("period_start") or "")
    end = str(result.get("period_end") or "")
    try:
        span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, span.total_seconds() / 86400)


def _tester_trades_by_strategy(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    detail = result.get("comparison_detail") or {}
    for row in detail.get("operation_comparisons") or []:
        tester = row.get("tester")
        if not isinstance(tester, dict) or not tester.get("close_time"):
            continue
        key = str(row.get("strategy") or tester.get("strategy") or "")
        grouped.setdefault(key, []).append(tester)
    return grouped


def degradation_report(source: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Compara cada estrategia del periodo con su historico en la memoria."""
    grouped = _tester_trades_by_strategy(result)
    days = _period_days(result)
    histories = live_audit_degradation.read_histories(source, list(grouped))
    sets = {
        str(row.get("strategy") or ""): str(row.get("strategy_set") or "")
        for row in (result.get("comparison_detail") or {}).get("strategy_summary") or []
    }
    symbols = {
        strategy: str((trades[0] or {}).get("symbol") or "")
        for strategy, trades in grouped.items() if trades
    }
    strategies = [{
        "strategy": strategy, "strategy_set": sets.get(strategy, ""),
        "symbol": symbols.get(strategy, ""),
        **live_audit_degradation.compare(histories.get(strategy), trades, days),
    } for strategy, trades in grouped.items()]
    strategies.sort(key=lambda row: (row["symbol"], row["strategy_set"], row["strategy"]))
    return {
        "strategies": strategies, "period_days": days,
        "reference_lot": live_audit_degradation.REFERENCE_LOT,
        "minimum_trades": live_audit_degradation.MINIMUM_TRADES,
    }


def _autolot_balance_history(node: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    state = value.get("audit") if isinstance(value, dict) and isinstance(value.get("audit"), dict) else value
    payload = state.get("last_payload") if isinstance(state, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("La ejecución no publicó materia prima para calcular el autolote")
    report = payload.get("real_account_report") or {}
    filename = str(report.get("filename") or "")
    audit_key = str((payload.get("request") or {}).get("audit_key") or payload.get("audit_key") or "")
    execution_id = str(payload.get("audit_id") or "")
    if not filename or not audit_key or not execution_id:
        raise ValueError("La ejecución no publicó el HTML nativo necesario para calcular el autolote")
    encoded = "/".join(urllib.parse.quote(item, safe="") for item in (audit_key, execution_id, filename))

    def load() -> bytes:
        status, body, _content_type = manager_http.node_artifact_request(
            node, f"/api/v1/live-audits/{encoded.split('/')[0]}/artifacts/"
            f"{encoded.split('/')[1]}/{encoded.split('/')[2]}", timeout=120,
        )
        if status != 200:
            raise ValueError(f"El nodo devolvió {status} al leer el HTML nativo para el autolote")
        expected_sha = str(report.get("sha256") or "").lower()
        if expected_sha and hashlib.sha256(body).hexdigest() != expected_sha:
            raise ValueError("El HTML nativo del autolote no coincide con la ejecución guardada")
        return body

    cache_key = str(report.get("sha256") or f"{audit_key}:{execution_id}:{filename}")
    return live_audit_autolot.cached_balance_history(cache_key, load)


def analysed_live_audit(
    handler, node_id: str, audit_id: str, value: Any, node: dict[str, Any] | None = None,
) -> Any:
    """Aplica el criterio vigente del manager a la materia prima del nodo."""
    if not live_audit_analysis.has_raw_material(value):
        return value
    settings = handler.server.live_audit_settings.state(node_id)
    profile = dict((settings.get("profiles") or {}).get(audit_id) or {})
    if (profile.get("autolot") or {}).get("enabled"):
        try:
            profile["_autolot_balance_history"] = _autolot_balance_history(node or {}, value)
        except (KeyError, ValueError, OSError, urllib.error.URLError, TimeoutError) as exc:
            profile["_autolot_balance_error"] = str(exc)
    return live_audit_analysis.analyse_node_state(value, profile)


def config_state(handler, node: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Añade el estado operativo del agente sin exponer credenciales."""
    state = dict(state)
    state["audit_states"] = {}
    try:
        status, value = manager_http.node_request(node, "GET", "/api/v1/live-audits", timeout=10)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        state["phase"] = "agent_unavailable"
        state["connection_error"] = str(exc)
        return state
    if status == 200 and isinstance(value, dict):
        state["phase"] = "connected"
        audits = value.get("audits") if isinstance(value.get("audits"), dict) else {}
        state["audit_states"] = {
            key: ({
                **{name: field for name, field in row.items() if name != "last_payload"},
                "has_payload": bool(row.get("last_payload")),
            } if isinstance(row, dict) else row)
            for key, row in audits.items()
        }
    elif status != 404:
        state["phase"] = "agent_unavailable"
        state["connection_error"] = str(value.get("error") if isinstance(value, dict) else value)
    return state


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    """Configuracion de la auditoria en vivo, sus informes y sus artefactos."""
    if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audit-config":
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = handler._node(node_id)
            state = config_state(
                handler, node, handler.server.live_audit_settings.state(node_id)
            )
            state["node"] = {"id": node_id, "name": node.get("name") or node_id}
            handler._send_json(200, state)
        except KeyError as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if (
        len(parts) == 8 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "artifacts"
    ):
        try:
            node = handler._node(urllib.parse.unquote(parts[2]))
            encoded = "/".join(
                urllib.parse.quote(urllib.parse.unquote(value), safe="")
                for value in (parts[4], parts[6], parts[7])
            )
            status, body, content_type = manager_http.node_artifact_request(
                node, f"/api/v1/live-audits/{encoded.split('/')[0]}/artifacts/"
                f"{encoded.split('/')[1]}/{encoded.split('/')[2]}", timeout=120,
            )
            handler._send_artifact(status, body, content_type)
        except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            handler._send_json(502, {"error": str(exc)})
        return True
    if (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "strategy-history"
    ):
        try:
            handler._send_json(200, strategy_history(
                handler, urllib.parse.unquote(parts[2]), urllib.parse.unquote(parts[4]),
            ))
        except (KeyError, ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
            handler._send_json(502, {"error": str(exc)})
        return True
    if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audits":
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = handler._node(node_id)
            audit_id = urllib.parse.unquote(parts[4])
            status, value = manager_http.node_request(
                node, "GET", f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}", timeout=10
            )
            handler._send_json(status, analysed_live_audit(handler, node_id, audit_id, value, node))
        except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            handler._send_json(502, {"error": str(exc)})
        return True
    return False


def run_payload(handler, node_id: str, node: dict[str, Any], audit_id: str) -> dict[str, Any]:
    """Lo que se le manda al nodo para lanzar un uso, ya validado.

    El agente tiene que traer el auditor nuevo y la configuracion tiene que
    estar completa: sin la cuenta de restauracion los terminales se quedan
    con la cuenta del uso, que es de un tercero.
    """
    node_status, node_state = manager_http.node_request(node, "GET", "/api/v1/status", timeout=10)
    capabilities = (
        node_state.get("capabilities") if node_status == 200 and isinstance(node_state, dict) else {}
    )
    if not isinstance(capabilities, dict) or not capabilities.get("live_audit_restore_account"):
        raise ValueError(
            "El agente ICTrading aún usa el auditor anterior; reinícialo cuando termine su trabajo actual."
        )
    state = handler.server.live_audit_settings.state(node_id)
    if audit_id not in state.get("configured_audit_ids", []):
        raise ValueError(f"Guarda la configuración completa del uso {audit_id}")
    profile = dict((state.get("profiles") or {}).get(audit_id) or {})
    credentials = handler.server.live_audit_settings.credentials(node_id, audit_id)
    restore = handler.server.live_audit_settings.restore_credentials(node_id)
    if not restore:
        raise ValueError("Configura y guarda la cuenta que debe quedar en los terminales")
    # Ni el autolote ni la atribución manual viajan al nodo: los dos son
    # criterio del manager y se aplican al analizar lo que el nodo observó.
    node_profile = {
        key: value for key, value in profile.items()
        if key not in {"autolot", "real_attribution"}
    }
    return {
        **node_profile, **credentials, **restore,
        "audit_key": audit_id,
        "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=1),
    }


def _handle_workbook_import(handler, parts: list[str]) -> bool:
    """Los dos Excel que el manager lee y guarda en el perfil del uso."""
    readers = {
        "live-audit-autolot-import": (
            live_audit_autolot.import_workbook, "autolot", "strategy_count",
        ),
        "live-audit-attribution-import": (
            live_audit_attribution.import_workbook, "real_attribution", "operation_count",
        ),
    }
    reader = readers.get(parts[3]) if len(parts) == 4 and parts[:2] == ["api", "nodes"] else None
    if reader is None:
        return False
    read, field, count = reader
    try:
        handler._node(urllib.parse.unquote(parts[2]))
        imported = read(handler._body())
        handler._send_json(200, {field: imported, count: len(imported["rules"])})
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True


def _handle_attribution_save(handler, parts: list[str]) -> bool:
    """Guarda la atribución de un uso y devuelve el veredicto ya recalculado.

    En una sola llamada: el criterio vive en el manager y se aplica al leer, de
    modo que guardar y volver a pedir son dos pasos que no pueden quedar a
    medias. La pantalla recibe el resultado nuevo y repinta con él.
    """
    if not (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "attribution"
    ):
        return False
    try:
        node_id = urllib.parse.unquote(parts[2])
        audit_id = urllib.parse.unquote(parts[4])
        node = handler._node(node_id)
        body = handler._body()
        handler.server.live_audit_settings.set_real_attribution(
            node_id, audit_id, (body or {}).get("real_attribution"),
        )
        status, value = manager_http.node_request(
            node, "GET", f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}",
            timeout=10,
        )
        handler._send_json(status, analysed_live_audit(handler, node_id, audit_id, value, node))
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    except (urllib.error.URLError, TimeoutError) as exc:
        handler._send_json(502, {"error": str(exc)})
    return True


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    """Configuracion del auditor, cuenta de restauracion y lanzar un uso."""
    if _handle_workbook_import(handler, parts) or _handle_attribution_save(handler, parts):
        return True
    if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] in {
        "live-audit-config", "live-audit-restore-account",
    }:
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = handler._node(node_id)
            settings = handler.server.live_audit_settings
            updated = (
                settings.update(node_id, handler._body())
                if parts[3] == "live-audit-config"
                else settings.update_restore_account(node_id, handler._body())
            )
            state = config_state(handler, node, updated)
            state["node"] = {"id": node_id, "name": node.get("name") or node_id}
            handler._send_json(200, state)
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "run"
    ):
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = handler._node(node_id)
            payload = run_payload(
                handler, node_id, node, urllib.parse.unquote(parts[4])
            )
            status, value = manager_http.node_request(
                node, "POST", f"/api/v1/live-audits/{payload['portfolio_id']}/run", payload, timeout=30
            )
            if status == 404:
                raise ValueError("El agente ICTrading todavía no tiene cargado el motor de auditoría; reinícialo.")
            handler._send_json(status, value)
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        except (urllib.error.URLError, TimeoutError) as exc:
            handler._send_json(502, {"error": str(exc)})
        return True
    return False
