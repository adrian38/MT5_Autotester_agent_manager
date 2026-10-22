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

from . import live_audit_attribution, live_audit_autolot, live_audit_comment_links
from . import live_audit_degradation_alerts, live_audit_manual_memory
from . import manager_http
from .common import safe_int
# Lo que deriva de una ejecución guardada vive en su propio módulo desde que
# éste pasó de 600 líneas. Se reexporta porque es API: `manager_schedule_routes`
# y las pruebas lo importan de aquí.
from .manager_live_audit_reports import (  # noqa: F401
    analysed_live_audit, degradation_report, strategy_history,
)


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


def dashboard_nodes(handler) -> dict[str, Any]:
    """Identidades configuradas sin consultar el estado ni la memoria de los nodos."""
    return {"nodes": [
        {"id": str(node.get("id") or ""), "name": node.get("name") or node.get("id")}
        for node in handler.server.nodes if node.get("id")
    ]}


def dashboard_node_state(handler, node_id: str, node: dict[str, Any]) -> dict[str, Any]:
    """Perfiles reales y sus resultados usando una sola lectura del nodo."""
    settings = handler.server.live_audit_settings.state(node_id)
    profiles = settings.get("profiles") or {}
    live_ids = settings.get("live_audit_ids") or [
        audit_id for audit_id, profile in profiles.items()
        if isinstance(profile, dict) and profile.get("live_trading")
    ]
    result = {"node": {"id": node_id, "name": node.get("name") or node_id}, "entries": []}
    try:
        status, value = manager_http.node_request(
            node, "GET", "/api/v1/live-audits", timeout=10,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        result["connection_error"] = str(exc)
        return result
    if status != 200 or not isinstance(value, dict):
        result["connection_error"] = str(
            value.get("error") if isinstance(value, dict) else value
        )
        return result
    audits = value.get("audits") if isinstance(value.get("audits"), dict) else {}
    for audit_id in live_ids:
        state = audits.get(audit_id) if isinstance(audits.get(audit_id), dict) else {}
        analysed = analysed_live_audit(handler, node_id, audit_id, state, node)
        result["entries"].append({
            "audit_id": audit_id, "profile": profiles.get(audit_id) or {},
            "audit": analysed.get("audit") if isinstance(analysed, dict) and isinstance(analysed.get("audit"), dict) else analysed,
            # Lo midió el vigilante al archivar la ejecución y vive en el
            # estado del cron: abrir la memoria UBS del agente en cada sondeo
            # de la portada sería abrirla sesenta veces por hora y por uso.
            "degradation": live_audit_degradation_alerts.stored(
                handler.server, node_id, audit_id,
            ),
        })
    return result


def handle_dashboard_get(handler, parts: list[str]) -> bool:
    """Rutas ligeras que alimentan la portada sin consultar el estado general."""
    if parts == ["api", "live-dashboard"]:
        handler._send_json(200, dashboard_nodes(handler))
        return True
    if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "live-dashboard":
        try:
            node_id = urllib.parse.unquote(parts[2])
            handler._send_json(200, dashboard_node_state(handler, node_id, handler._node(node_id)))
        except KeyError as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    return False


# Lo que Análisis decide por su cuenta y guarda en el perfil del uso: el
# escalado que aplica al leer y las estrategias que el usuario ya revisó.
# Ninguno de los dos reanaliza nada ni viaja al nodo, y los dos viven aquí y no
# en `localStorage` por el mismo motivo: la portada tiene que enseñar lo mismo
# que Análisis desde cualquier navegador. El nombre del campo es también el del
# par de métodos del almacén, así que la tabla basta para enrutar los dos.
_VIEW_FIELDS = {
    "view-lots": "view_lot_overrides",
    "reviewed-strategies": "reviewed_strategies",
}


def _view_field(parts: list[str]) -> str:
    if len(parts) != 6 or parts[:2] != ["api", "nodes"] or parts[3] != "live-audits":
        return ""
    return _VIEW_FIELDS.get(parts[5], "")


def _handle_view_field(handler, parts: list[str], write: bool) -> bool:
    """Lee o guarda uno de esos campos. El veredicto se sigue recalculando en
    el cliente con las mismas primitivas, igual que antes.
    """
    field = _view_field(parts)
    if not field:
        return False
    try:
        node_id = urllib.parse.unquote(parts[2])
        handler._node(node_id)
        audit_id = urllib.parse.unquote(parts[4])
        settings = handler.server.live_audit_settings
        value = (
            getattr(settings, f"set_{field}")(
                node_id, audit_id, (handler._body() or {}).get(field),
            ) if write else getattr(settings, field)(node_id, audit_id)
        )
        handler._send_json(200, {field: value})
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True


def _handle_saved_password(handler, parts: list[str]) -> bool:
    """El ojo de la pantalla pidiendo una contraseña guardada, de una en una.

    La referencia es un id del catálogo de cuentas. Lo que no esté guardado es
    un 404: un 200 con el campo vacío diría que la contraseña es la vacía.
    """
    if len(parts) != 5 or parts[:2] != ["api", "nodes"] or parts[3] != "live-audit-password":
        return False
    try:
        node_id = urllib.parse.unquote(parts[2])
        handler._node(node_id)
        handler._send_json(200, {"password": handler.server.live_audit_settings.revealed_password(
            node_id, urllib.parse.unquote(parts[4]),
        )})
    except (KeyError, ValueError) as exc:
        handler._send_json(404, {"error": str(exc) or "No hay contraseña guardada"})
    return True


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    """Configuracion de la auditoria en vivo, sus informes y sus artefactos."""
    if handle_dashboard_get(handler, parts) or _handle_view_field(handler, parts, write=False):
        return True
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
    return _handle_saved_password(handler, parts)


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
    # El autolote, la atribución, la marca informativa de operación real, el
    # escalado de la vista, las estrategias ya revisadas y las parejas decididas
    # a mano y la tabla de comentarios del EA son criterio del manager; el nodo
    # sólo recibe datos necesarios para auditar.
    node_profile = {
        key: value for key, value in profile.items()
        if key not in {
            "autolot", "real_attribution", "live_trading",
            "view_lot_overrides", "reviewed_strategies", "manual_pairs",
            "comment_links",
        }
    }
    return {
        **node_profile, **credentials, **restore,
        "audit_key": audit_id,
        "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=1),
    }


def _refuse_outside_axi(node: dict[str, Any]) -> None:
    """La puerta, y no el análisis: así ningún otro uso llega a tener la tabla."""
    if not live_audit_comment_links.comment_links_supported(node):
        raise ValueError(
            "El enlace por comentario sólo existe en AXI: es su EA quien escribe "
            "el código del setfile en la orden."
        )


def _handle_comment_links_import(handler, parts: list[str]) -> bool:
    """Lee el `.docx` con la tabla «setfile ↔ comentario» y la devuelve."""
    if not (len(parts) == 4 and parts[:2] == ["api", "nodes"]
            and parts[3] == "live-audit-comment-links-import"):
        return False
    try:
        _refuse_outside_axi(handler._node(urllib.parse.unquote(parts[2])))
        imported = live_audit_comment_links.import_document(handler._body())
        handler._send_json(200, {
            "comment_links": imported, "comment_count": len(imported["rules"]),
        })
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True


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
        body = handler._body()
        if field == "real_attribution":
            imported = live_audit_attribution.import_workbook_bundle(body)
            handler._send_json(200, {
                **imported, count: len(imported["real_attribution"]["rules"]),
            })
        else:
            imported = read(body)
            handler._send_json(200, {field: imported, count: len(imported["rules"])})
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True


def _apply_attribution_change(handler, node_id: str, audit_id: str, body: Any) -> None:
    """Suma un Excel, retira uno, o los retira todos.

    Las tres operaciones por la misma puerta: lo que importa del endpoint es
    que deje la atribución guardada y devuelva el análisis rehecho, y eso es
    igual se añada o se quite.
    """
    settings = handler.server.live_audit_settings
    change = body if isinstance(body, dict) else {}
    if change.get("clear"):
        settings.drop_real_attribution(node_id, audit_id, None)
    elif "remove_source" in change:
        settings.drop_real_attribution(node_id, audit_id, str(change["remove_source"] or ""))
    elif "manual_pairs" in change:
        settings.set_real_attribution_with_manual_pairs(
            node_id, audit_id, change.get("real_attribution"), change.get("manual_pairs"),
        )
    else:
        settings.set_real_attribution(node_id, audit_id, change.get("real_attribution"))


def _apply_manual_pairs_change(handler, node_id: str, audit_id: str, body: Any) -> None:
    """Sustituye las parejas manuales del uso por las que manda la pantalla.

    Sustituir y no acumular, al revés que la atribución: la pantalla tiene el
    mapa entero delante y deshacer una pareja es mandarlo sin ella.
    """
    change = body if isinstance(body, dict) else {}
    handler.server.live_audit_settings.set_manual_pairs(
        node_id, audit_id, change.get("manual_pairs"),
    )


def _apply_lot_schedule_change(handler, node_id: str, audit_id: str, body: Any) -> None:
    """Sustituye los tramos de lote del uso por los que manda la pantalla.

    Sustituir y no acumular, igual que las parejas manuales: la pantalla lista
    los tramos con su ✕ y quitar uno es mandar el mapa sin él. Acumular aquí
    dejaría sin salida a un tramo declarado con la fecha equivocada, que es el
    error más fácil de cometer cuando la fecha la pone un botón.
    """
    change = body if isinstance(body, dict) else {}
    handler.server.live_audit_settings.set_real_lot_schedule(
        node_id, audit_id, change.get("real_lot_schedule"),
    )


def _apply_comment_links_change(handler, node_id: str, audit_id: str, body: Any) -> None:
    """Guarda la tabla del `.docx`, o la retira si llega vacía."""
    _refuse_outside_axi(handler._node(node_id))
    change = body if isinstance(body, dict) else {}
    handler.server.live_audit_settings.set_comment_links(
        node_id, audit_id, change.get("comment_links"),
    )


# Lo que una pantalla cambia del criterio del manager y obliga a reanalizar.
_SAVED_CRITERION = {
    "attribution": _apply_attribution_change,
    "manual-pairs": _apply_manual_pairs_change,
    "real-lot-schedule": _apply_lot_schedule_change,
    "comment-links": _apply_comment_links_change,
}


def _handle_saved_criterion(handler, parts: list[str]) -> bool:
    """Cambia un criterio de un uso y devuelve el veredicto ya recalculado.

    En una sola llamada: el criterio vive en el manager y se aplica al leer, de
    modo que guardar y volver a pedir son dos pasos que no pueden quedar a
    medias. La pantalla recibe el resultado nuevo y repinta con él.
    """
    if not (
        len(parts) == 6 and parts[:2] == ["api", "nodes"] and parts[3] == "live-audits"
        and parts[5] in _SAVED_CRITERION
    ):
        return False
    try:
        node_id = urllib.parse.unquote(parts[2])
        audit_id = urllib.parse.unquote(parts[4])
        node = handler._node(node_id)
        _SAVED_CRITERION[parts[5]](handler, node_id, audit_id, handler._body())
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


def _remember_manual_reports(
    handler, node_id: str, audit_id: str, body: Any, value: Any,
) -> None:
    """Guarda la copia del manager, que es la que sobrevive a la ejecución siguiente.

    El agente sólo sirve artefactos de la ejecución visible, así que en cuanto
    el cron vuelve a ejecutar ese uso el HTML deja de poder pedirse. Sin esta
    copia no hay nada que heredar.
    """
    memory = getattr(handler.server, "live_audit_manual_memory", None)
    if memory is None:
        return
    state = value.get("audit") if isinstance(value, dict) and isinstance(
        value.get("audit"), dict,
    ) else value
    payload = state.get("last_payload") if isinstance(state, dict) else None
    if isinstance(payload, dict):
        memory.remember(
            node_id, audit_id, payload, live_audit_manual_memory.uploaded_contents(body),
        )


def _handle_manual_reports(handler, parts: list[str]) -> bool:
    """Reenvía HTML MT5 al nodo y devuelve el veredicto ya recalculado."""
    if not (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "reports"
    ):
        return False
    try:
        node_id = urllib.parse.unquote(parts[2])
        audit_id = urllib.parse.unquote(parts[4])
        node = handler._node(node_id)
        body = handler._body()
        status, value = manager_http.node_request(
            node, "POST",
            f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}/reports",
            body, timeout=120,
        )
        if status == 404:
            raise ValueError(
                "El nodo todavía no admite reportes manuales; actualiza y reinicia el agente."
            )
        if status != 200:
            handler._send_json(status, value)
            return True
        _remember_manual_reports(handler, node_id, audit_id, body, value)
        handler._send_json(status, analysed_live_audit(handler, node_id, audit_id, value, node))
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    except (urllib.error.URLError, TimeoutError) as exc:
        handler._send_json(502, {"error": str(exc)})
    return True


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    """Configuracion del auditor, cuenta de restauracion y lanzar un uso."""
    if (
        _handle_workbook_import(handler, parts)
        or _handle_comment_links_import(handler, parts)
        or _handle_saved_criterion(handler, parts)
        or _handle_manual_reports(handler, parts)
        or _handle_view_field(handler, parts, write=True)
    ):
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
    if (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3] == "live-audits" and parts[5] == "stop"
    ):
        _handle_stop(handler, parts)
        return True
    return False


def _handle_stop(handler, parts: list[str]) -> None:
    """Detener no necesita el perfil ni las credenciales.

    La auditoría que corre ya los tiene; sólo hay que decírselo al agente, que
    es quien la ejecuta y quien tiene el proceso del tester que cortar.
    """
    try:
        node = handler._node(urllib.parse.unquote(parts[2]))
        audit_id = urllib.parse.unquote(parts[4])
        status, value = manager_http.node_request(
            node, "POST",
            f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}/stop",
            {}, timeout=30,
        )
        if status == 404:
            raise ValueError(
                "El agente todavía no sabe detener una auditoría; reinícialo cuando "
                "termine su trabajo actual."
            )
        handler._send_json(status, value)
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    except (urllib.error.URLError, TimeoutError) as exc:
        handler._send_json(502, {"error": str(exc)})
