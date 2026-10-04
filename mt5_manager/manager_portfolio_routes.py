"""Las rutas de la pantalla de portafolios, en su propio modulo.

Mismo patron que `correlation_routes`: funciones que reciben el handler. El
punto de entrada es `handle_post`, que decide la accion por la ultima parte de
la ruta; `handle_get` sirve el estado, la tarea y las carteras guardadas.

Aqui no se decide nada sobre la memoria del agente: eso es del nodo. Este
modulo traduce peticiones HTTP en llamadas al coordinador o en un reenvio.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
from typing import Any

from . import dev_branch
from . import manager_http
from .common import safe_int
from .portfolio_scope import PORTFOLIO_SCOPES, normalize_portfolio_scope
from . import manager_config
from .portfolio_service import (
    PortfolioCoordinator,
    legacy_compatible_portfolio_save_payload,
)


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    """Pantalla de portafolios, estado de su tarea y carteras guardadas."""
    if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-manager":
        try:
            node_id = urllib.parse.unquote(parts[2])
            node = handler._node(node_id)
            query = urllib.parse.parse_qs(parsed.query)
            scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
            state = handler.server.portfolios.state(node_id, scope)
            state["capabilities"] = {"export_mode": handler.server.export_mode}
            handler._send_json(200, state)
        except (KeyError, ValueError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if len(parts) == 5 and parts[:2] == ["api", "nodes"] and parts[3:] == ["portfolio-manager", "task"]:
        try:
            node_id = urllib.parse.unquote(parts[2])
            handler._node(node_id)
            query = urllib.parse.parse_qs(parsed.query)
            scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
            handler._send_json(200, handler.server.portfolios.task_state(node_id, scope))
        except (KeyError, ValueError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if len(parts) in {4, 5} and parts[:2] == ["api", "nodes"] and parts[3] == "portfolios":
        try:
            node = handler._node(urllib.parse.unquote(parts[2]))
            query = urllib.parse.parse_qs(parsed.query)
            scope = normalize_portfolio_scope(query.get("scope", ["full_history"])[0])
            portfolio_id = safe_int(parts[4], 0, minimum=1) if len(parts) == 5 else None
            if str(node.get("portfolio_project_dir") or "").strip():
                handler._send_json(200, handler.server.portfolios.saved(str(node["id"]), scope, portfolio_id))
            else:
                suffix = f"/{portfolio_id}" if portfolio_id is not None else ""
                status, value = manager_http.node_request(node, "GET", f"/api/v1/portfolios{suffix}?scope={scope}")
                handler._send_json(status, value)
        except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            handler._send_json(502, {"error": str(exc)})
        return True
    return False


def exclude(handler, node: dict, node_id: str, scope: str, body: dict) -> None:
    """Excluye miembros o candidatos y deja la memoria coherente."""
    if scope == "grid":
        # El paquete Grid vive en la base del manager y la
        # cuarentena en la memoria del nodo: el coordinador
        # reparte cada escritura a su dueño.
        handler._send_json(201, handler.server.portfolios.exclude_grid(node_id, body))
    elif body.get("set_paths") is not None:
        status, value = manager_http.node_request(
            node,
            "POST",
            "/api/v1/portfolios/exclude",
            {**body, "scope": scope},
            timeout=120,
        )
        if status == 404:
            raise ValueError(
                "El nodo todavía no admite exclusión múltiple local; "
                "actualiza su código y reinícialo."
            )
        if status >= 400 or not isinstance(value, dict):
            error = value.get("error") if isinstance(value, dict) else value
            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        portfolio_id = safe_int(body.get("portfolio_id"), 0, minimum=1)
        # El portafolio guardado ya no se borra, así que lo que se
        # confirma es la cuarentena, no un borrado.
        if not value.get("quarantine_ids") or safe_int(value.get("portfolio_id"), 0) != portfolio_id:
            raise ValueError("El nodo no confirmó correctamente la exclusión múltiple")
        handler.server.portfolios.invalidate_after_exclusion(node_id)
        # Misma comprobación que en la exclusión individual: un nodo
        # sin portar acepta el motivo y no escribe el veredicto.
        PortfolioCoordinator._assert_node_applied_verdict(body, value)
        handler._send_json(201, value)
    else:
        quarantine_result = handler.server.portfolios.exclude(node_id, scope, body)
        handler._send_json(201, {"quarantine_id": quarantine_result})


def save_grid(handler, node_id: str, scope: str, save_payload: dict) -> None:
    """Grid persiste su paquete en la base del manager, no en el nodo."""
    value = handler.server.portfolios.save_grid_package(node_id, save_payload)
    portfolio_id = safe_int(value.get("portfolio_id"), 0)
    request_id = str(value.get("request_id") or "")
    if portfolio_id <= 0 or request_id != str(save_payload["request_id"]):
        raise ValueError("El manager no confirmó correctamente el paquete Grid")
    handler.server.portfolios.confirm_save(
        node_id, scope, request_id, portfolio_id
    )
    variant_ids = {
        str(proposal.get("key") or ""): portfolio_id
        for proposal in save_payload.get("proposals") or []
        if isinstance(proposal, dict) and proposal.get("key")
    }
    handler._send_json(201, {
        "portfolio_id": portfolio_id,
        "portfolio_ids": variant_ids,
    })
    return


def report_action(handler, action: str, node: dict, node_id: str, scope: str, body: dict,
) -> bool:
    """Abrir un informe de un miembro, exportarlos todos o leer el log."""
    if action == "open-report":
        report = handler.server.portfolios.open_report(
            node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1),
            str(body.get("set_path") or ""),
        )
        handler._send_inline_content(report)
    elif action == "export-member-reports":
        result = handler.server.portfolios.export_member_reports_archive(
            node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1),
            str(body.get("set_path") or ""),
        )
        handler._send_download(result)
    elif action == "log":
        handler._send_json(200, handler.server.portfolios.log(
            node_id, scope, safe_int(body.get("lines"), 500, minimum=1, maximum=5000)
        ))
    else:
        return False
    return True


def save(handler, node: dict, node_id: str, scope: str, body: dict) -> None:
    """Guarda la propuesta preparada; Grid la persiste en el manager y UBS en el nodo."""
    save_payload = handler.server.portfolios.prepare_save(
        node_id, scope, str(body.get("proposal_key") or "")
    )
    if scope == "grid":
        save_grid(handler, node_id, scope, save_payload)
        return
    portfolio_ids: dict[str, int] = {}
    for variant_payload in (save_payload,):
        status, value = manager_http.node_request(
            node, "POST", "/api/v1/portfolios/save", variant_payload, timeout=120
        )
        error_text = str(value.get("error") if isinstance(value, dict) else value or "")
        if status >= 400 and "unexpected keyword argument" in error_text:
            status, value = manager_http.node_request(
                node,
                "POST",
                "/api/v1/portfolios/save",
                legacy_compatible_portfolio_save_payload(variant_payload),
                timeout=120,
            )
        if status == 404:
            raise ValueError(
                "El nodo todavía no admite guardado local de portafolios; "
                "actualiza su código y reinícialo."
            )
        if status >= 400 or not isinstance(value, dict):
            error = value.get("error") if isinstance(value, dict) else value
            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        portfolio_id = safe_int(value.get("portfolio_id"), 0)
        request_id = str(value.get("request_id") or "")
        if portfolio_id <= 0 or request_id != str(variant_payload["request_id"]):
            raise ValueError("El nodo no confirmó correctamente el guardado")
        portfolio_ids[str(variant_payload["selected_key"])] = portfolio_id
    selected_key = str(save_payload["selected_key"])
    selected_id = portfolio_ids.get(selected_key, 0)
    if selected_id <= 0:
        raise ValueError("No se guardó la variante Grid seleccionada")
    handler.server.portfolios.confirm_save(
        node_id, scope, str(save_payload["request_id"]), selected_id
    )
    handler._send_json(201, {
        "portfolio_id": selected_id,
        "portfolio_ids": portfolio_ids,
    })


def action_route(handler, action: str, node: dict, node_id: str, scope: str, body: dict,
) -> bool:
    """Acciones de calculo, guardado y ciclo de vida.

    Devuelve si ha reconocido la accion; asi las dos mitades de la
    cadena se encadenan sin que ninguna sepa de la otra.
    """
    if action == "settings":
        handler._send_json(200, handler.server.portfolios.apply_settings(node_id, scope, body))
    elif action == "generate":
        handler._send_json(202, {"job": handler.server.portfolios.start(node_id, scope, body)})
    elif action == "stop":
        handler._send_json(202, {"job": handler.server.portfolios.stop(node_id, scope)})
    elif action == "save":
        save(handler, node, node_id, scope, body)
    elif action in {"reoptimize", "complete", "improve"}:
        portfolio_id = safe_int(body.pop("portfolio_id", 0), 0, minimum=1)
        handler._send_json(202, {"job": handler.server.portfolios.start_saved_operation(
            node_id, scope, portfolio_id, action, body or None
        )})
    elif action == "alias":
        portfolio_id = safe_int(body.get("portfolio_id"), 0, minimum=1)
        alias = handler.server.portfolios.set_alias(
            node_id, scope, portfolio_id, body.get("alias")
        )
        handler._send_json(200, {"portfolio_id": portfolio_id, "alias": alias})
    elif action == "exclude":
        exclude(handler, node, node_id, scope, body)
    elif action == "release":
        handler.server.portfolios.release(node_id, scope, str(body.get("quarantine_id") or ""))
        handler._send_json(200, {"released": True})
    elif action == "requalify":
        # Mover una estrategia excluida entre los tres motivos y el
        # pool. Reintegrar es el caso `pool` de esta misma operación.
        target = handler.server.portfolios.requalify(
            node_id, scope,
            str(body.get("quarantine_id") or ""),
            str(body.get("reason_code") or "pool"),
        )
        handler._send_json(200, {"reason_code": target})
    elif action == "undo":
        version = handler.server.portfolios.undo(node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1))
        handler._send_json(200, {"restored_version": version})
    elif action == "delete":
        task = handler.server.portfolios.delete(
            node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1)
        )
        handler._send_json(202, {"task": task})
    else:
        return False
    return True


def transfer_action(handler, action: str, node: dict, node_id: str, scope: str, body: dict,
) -> bool:
    """Importar, exportar, abrir informes y leer el log.

    Devuelve si ha reconocido la accion; asi las dos mitades de la
    cadena se encadenan sin que ninguna sepa de la otra.
    """
    if action == "choose-import-folder":
        if handler.server.export_mode != "folder":
            raise ValueError("El selector local de carpetas no está disponible en modo Docker")
        folder = manager_config.choose_directory(
            str(body.get("initial_directory") or "").strip() or None,
            title="Selecciona la carpeta del portafolio exportado",
        )
        handler._send_json(200, {"folder": folder, "cancelled": folder is None})
    elif action == "import":
        handler._send_json(201, handler.server.portfolios.import_portfolio(node_id, scope, body))
    elif action == "choose-export-folder":
        if handler.server.export_mode != "folder":
            raise ValueError("El selector local de carpetas no está disponible en modo Docker")
        folder = manager_config.choose_directory(
            str(body.get("initial_directory") or "").strip() or None
        )
        handler._send_json(200, {"folder": folder, "cancelled": folder is None})
    elif action == "symbol-sets":
        handler._send_json(200, handler.server.portfolios.symbol_sets(
            node_id, scope, str(body.get("symbol") or "")
        ))
    elif action == "export-symbol-download":
        result = handler.server.portfolios.export_symbol_archive(
            node_id, scope, str(body.get("symbol") or ""), body.get("set_paths")
        )
        handler._send_download(result)
    elif action == "export-symbol":
        result = handler.server.portfolios.export_symbol(
            node_id, scope, str(body.get("symbol") or ""), body.get("set_paths"),
            str(body.get("destination") or "").strip() or None,
        )
        handler._send_json(200, result)
    elif action == "export-download":
        result = handler.server.portfolios.export_archive(
            node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1)
        )
        handler._send_download(result)
    elif action == "export":
        result = handler.server.portfolios.export(
            node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1),
            str(body.get("destination") or "").strip() or None,
        )
        handler._send_json(200, result)
    else:
        return report_action(handler, action, node, node_id, scope, body)
    return True


def handle_post(handler, parts: list[str]) -> None:
    """Despacha /api/nodes/<id>/portfolio-manager/<accion>.

    Eran doscientas lineas dentro de do_POST, que asi no dejaba ver el
    resto del enrutado.
    """
    try:
        node_id = urllib.parse.unquote(parts[2])
        node = handler._node(node_id)
        body = handler._body()
        scope = normalize_portfolio_scope(body.pop("scope", "full_history"))
        action = parts[4]
        if not action_route(handler, action, node, node_id, scope, body):
            if not transfer_action(handler, action, node, node_id, scope, body):
                handler._send_json(404, {"error": "Acción de portafolio desconocida"})
    except (
        KeyError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError,
        urllib.error.URLError, TimeoutError,
    ) as exc:
        handler._send_json(400, {"error": str(exc)})
