"""Las rutas de la auditoria en vivo, en su propio modulo.

Mismo patron que `correlation_routes`: funciones que reciben el handler.

Lanzar un uso exige dos cosas del agente: que traiga el auditor nuevo y que
haya una cuenta de restauracion guardada. Sin ella los terminales se quedarian
con la cuenta del uso, que es de un tercero.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
from typing import Any

from . import manager_http
from .common import safe_int


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
        state["audit_states"] = value.get("audits") if isinstance(value.get("audits"), dict) else {}
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
    if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audits":
        try:
            node = handler._node(urllib.parse.unquote(parts[2]))
            audit_id = urllib.parse.unquote(parts[4])
            status, value = manager_http.node_request(
                node, "GET", f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}", timeout=10
            )
            handler._send_json(status, value)
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
    return {
        **profile, **credentials, **restore,
        "audit_key": audit_id,
        "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=1),
    }


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    """Configuracion del auditor, cuenta de restauracion y lanzar un uso."""
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
