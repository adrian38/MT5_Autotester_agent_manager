"""Las rutas de la pantalla de portafolios, en su propio modulo.

Mismo patron que `correlation_routes`: funciones que reciben el handler. El
punto de entrada es `handle_post`, que decide la accion por la ultima parte de
la ruta; `handle_get` sirve el estado, la tarea y las carteras guardadas.

Aqui no se decide nada sobre la memoria del agente: eso es del nodo. Este
modulo traduce peticiones HTTP en llamadas al coordinador o en un reenvio.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.parse
import zipfile
from xml.etree import ElementTree
from typing import Any

from . import dev_branch
from . import manager_http
from .common import safe_int
from .portfolio_scope import PORTFOLIO_SCOPES, normalize_portfolio_scope
from . import manager_config
from .portfolio_service import PortfolioCoordinator


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
VALID_TIMEFRAMES = {
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
}


def _document_label(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().casefold()


def _document_cell(cell: ElementTree.Element) -> str:
    namespace = {"w": WORD_NAMESPACE}
    return "".join(node.text or "" for node in cell.findall(".//w:t", namespace)).strip()


def _timeframe_rows(document_xml: bytes) -> list[list[str]]:
    if len(document_xml) > 2_000_000:
        raise ValueError("El documento de temporalidades es demasiado grande")
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise ValueError("El DOCX de temporalidades no contiene XML válido") from exc
    namespace = {"w": WORD_NAMESPACE}
    for table in root.findall(".//w:tbl", namespace):
        rows = [
            [_document_cell(cell) for cell in row.findall("./w:tc", namespace)]
            for row in table.findall("./w:tr", namespace)
        ]
        if rows and {"nombre del archivo", "temporalidad"}.issubset(
            {_document_label(cell) for cell in rows[0]}
        ):
            return rows
    raise ValueError("No se encontró una tabla con Nombre del archivo y Temporalidad")


def parse_timeframe_document(content: str) -> dict[str, Any]:
    """Lee la tabla de temporalidades de un DOCX sin depender de Microsoft Word."""
    try:
        payload = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("El documento de temporalidades no está codificado correctamente") from exc
    if not payload or len(payload) > 5_000_000:
        raise ValueError("El documento de temporalidades está vacío o supera 5 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            document = archive.getinfo("word/document.xml")
            if document.file_size > 2_000_000:
                raise ValueError("El documento de temporalidades es demasiado grande")
            rows = _timeframe_rows(archive.read(document))
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("El archivo seleccionado no es un DOCX válido") from exc
    headings = [_document_label(cell) for cell in rows[0]]
    name_index = headings.index("nombre del archivo")
    timeframe_index = headings.index("temporalidad")
    mapping: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) <= max(name_index, timeframe_index):
            continue
        name = re.sub(r"\.(?:html?|set)$", "", row[name_index].strip(), flags=re.I).casefold()
        timeframe = row[timeframe_index].strip().upper()
        if not name or timeframe not in VALID_TIMEFRAMES:
            continue
        if name in mapping and mapping[name] != timeframe:
            raise ValueError(f"El documento asigna dos temporalidades distintas a {row[name_index]}")
        mapping[name] = timeframe
    if not mapping:
        raise ValueError("La tabla no contiene temporalidades válidas")
    return {"timeframes": mapping, "count": len(mapping)}


def create_manual_portfolio(handler, node: dict, body: dict[str, Any]) -> None:
    """Forward uploaded sets to the node that owns both files and memory."""
    status, value = manager_http.node_request(
        node, "POST", "/api/v1/portfolios/manual", body, timeout=120,
    )
    if status == 404:
        raise ValueError(
            "El nodo todavia no admite portafolios manuales; actualiza AXI y reinicia su app."
        )
    handler._send_json(status, value)


def manual_portfolio_action(handler, node: dict, action: str, body: dict[str, Any]) -> None:
    endpoint = "manual-defaults" if action == "manual-defaults" else "manual-history"
    status, value = manager_http.node_request(
        node, "POST", f"/api/v1/portfolios/{endpoint}", body, timeout=120,
    )
    if status == 404:
        raise ValueError("El nodo AXI necesita actualizarse y reiniciarse para generar el histórico OHLC.")
    handler._send_json(status, value)


def portfolio_aliases(handler, node: dict, ids: list[int]) -> dict[str, str]:
    """Alias de los portafolios pedidos, por su numero.

    La portada los pide para rotular sus tarjetas, asi que no puede permitirse
    el listado completo: `metrics_json` es la columna cara y son tres o cuatro
    portafolios. Cuando el manager no ve la memoria del agente —sin
    `portfolio_project_dir`— pregunta al nodo, igual que el listado guardado.
    """
    if str(node.get("portfolio_project_dir") or "").strip():
        found = handler.server.portfolios.aliases(str(node["id"]), "full_history", ids)
    else:
        status, value = manager_http.node_request(
            node, "GET", "/api/v1/portfolios?scope=full_history",
        )
        if status != 200 or not isinstance(value, dict):
            raise ValueError(str(
                value.get("error") if isinstance(value, dict) else value
                or f"El nodo devolvió HTTP {status}"
            ))
        found = {
            safe_int(row.get("id"), 0): str(row.get("alias") or "")
            for row in value.get("portfolios") or [] if isinstance(row, dict)
        }
    return {str(portfolio_id): found.get(portfolio_id, "") for portfolio_id in ids}


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    """Pantalla de portafolios, estado de su tarea y carteras guardadas."""
    if len(parts) == 4 and parts[:2] == ["api", "nodes"] and parts[3] == "portfolio-aliases":
        try:
            node = handler._node(urllib.parse.unquote(parts[2]))
            query = urllib.parse.parse_qs(parsed.query)
            ids = [
                number for number in (
                    safe_int(value, 0) for value in query.get("ids", [""])[0].split(",")
                ) if number > 0
            ]
            handler._send_json(200, {"aliases": portfolio_aliases(handler, node, ids)})
        except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            handler._send_json(502, {"error": str(exc)})
        return True
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


def manual_check_action(handler, action: str, node: dict, node_id: str, scope: str, body: dict,
) -> bool:
    """La comprobación manual de las estrategias nuevas de un portafolio.

    El manager decide qué se comprueba y lee lo que quedó en disco; quien
    ejecuta MT5 es el agente, así que el arranque es un reenvío.
    """
    portfolio_id = safe_int(body.get("portfolio_id"), 0, minimum=0)
    check_id = str(body.get("check_id") or "").strip()
    if action == "manual-check-settings":
        handler._send_json(200, handler.server.portfolios.manual_check_settings(node_id))
    elif action == "manual-check-preview":
        handler._send_json(200, handler.server.portfolios.manual_check_preview(
            node_id, scope, portfolio_id,
        ))
    elif action == "manual-check-sets-download":
        handler._send_download(handler.server.portfolios.manual_check_sets_archive(
            node_id, scope, portfolio_id,
        ))
    elif action == "manual-check-start":
        request = handler.server.portfolios.manual_check_request(
            node_id, scope, portfolio_id, body,
        )
        status, value = manager_http.node_request(
            node, "POST", "/api/v1/jobs/manual-check", request, timeout=120,
        )
        if status == 404:
            raise ValueError(
                "Este agente todavía no tiene la comprobación manual: hay que "
                "actualizarlo y reiniciarlo."
            )
        handler._send_json(status, value)
    elif action == "manual-check-latest":
        handler._send_json(200, handler.server.portfolios.manual_check_latest(
            node_id, scope, portfolio_id,
        ))
    elif action == "manual-check-state":
        handler._send_json(200, handler.server.portfolios.manual_check_state(node_id, check_id))
    elif action == "manual-check-analysis":
        handler._send_json(200, handler.server.portfolios.manual_check_analysis(node_id, check_id))
    elif action == "manual-check-download":
        handler._send_download(
            handler.server.portfolios.manual_check_reports_archive(node_id, check_id)
        )
    else:
        return False
    return True


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
    selected_key = str(body.get("proposal_key") or "")
    if scope == "grid":
        save_payload = handler.server.portfolios.prepare_save(node_id, scope, selected_key)
        save_grid(handler, node_id, scope, save_payload)
        return
    handler._send_json(201, handler.server.portfolios.save_to_node(
        node_id, scope, selected_key,
    ))


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
    elif action in {"reoptimize", "complete", "improve", "repair"}:
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
    elif action == "siblings":
        # Sólo lectura: quiénes caerían con esta estrategia si se quiere. La
        # exclusión de cada elegido sigue siendo una llamada `exclude` normal.
        handler._send_json(200, handler.server.portfolios.siblings(node_id, scope, body))
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
    elif action == "history-detail":
        handler._send_json(200, handler.server.portfolios.history_detail(
            node_id, scope, safe_int(body.get("portfolio_id"), 0, minimum=1),
        ))
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
    elif not manual_check_action(handler, action, node, node_id, scope, body):
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
        action = parts[4]
        maximum = (
            50_000_000 if action == "manual-create"
            else 7_000_000 if action == "manual-timeframes" else 1_000_000
        )
        body = handler._body(maximum)
        scope = normalize_portfolio_scope(body.pop("scope", "full_history"))
        if action == "manual-timeframes":
            handler._send_json(200, parse_timeframe_document(str(body.get("content") or "")))
            return
        if action == "manual-create":
            if scope != "full_history":
                raise ValueError("Solo portafolio pertenece al historico completo")
            create_manual_portfolio(handler, node, body)
            return
        if action in {"manual-defaults", "manual-history"}:
            manual_portfolio_action(handler, node, action, body)
            return
        if not action_route(handler, action, node, node_id, scope, body):
            if not transfer_action(handler, action, node, node_id, scope, body):
                handler._send_json(404, {"error": "Acción de portafolio desconocida"})
    except (
        KeyError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError,
        urllib.error.URLError, TimeoutError,
    ) as exc:
        handler._send_json(400, {"error": str(exc)})
