"""Las carteras manuales: su tabla de temporalidades, su alta y sus sets.

Salio de `manager_portfolio_routes` al pasar ese fichero de 600 lineas. Es el
grupo que se separa entero: ninguna de estas funciones la llama el resto del
enrutado, que solo necesita `parse_timeframe_document`,
`create_manual_portfolio` y `manual_portfolio_action` desde `handle_post`.

Debajo de `manager_portfolio_routes` en la pila, asi que solo importa de
`manager_http` y de los auxiliares comunes.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
import unicodedata
import zipfile
from xml.etree import ElementTree
from typing import Any

from . import manager_http
from .common import safe_int
from .live_audit_watch import live_audit_ids


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


def live_trading_uses(handler, node_id: str, portfolio_id: int) -> list[str]:
    """Los usos marcados «operando en real» que sirven a ese portafolio.

    Mismo criterio que la portada y el cron: el **perfil**, no las tarjetas
    abiertas en el formulario del nodo. Confundir las dos cosas es el fallo de
    `ai_context/live_audit_cron_and_bell.md`.
    """
    state = handler.server.live_audit_settings.state(node_id) or {}
    profiles = state.get("profiles") or {}
    return sorted(
        audit_id for audit_id in live_audit_ids(state)
        if safe_int((profiles.get(audit_id) or {}).get("portfolio_id"), 0) == portfolio_id
    )


class LiveTradingChangeNeedsAck(ValueError):
    """Cambiar la composición de una cartera en real, todavía sin confirmar.

    No es un error del usuario sino una pregunta, así que viaja aparte: quien
    la atiende contesta con los usos afectados para que la pantalla pueda
    nombrarlos en el aviso y repetir la petición ya confirmada.
    """

    def __init__(self, uses: list[str], operation: str) -> None:
        super().__init__(live_trading_change_warning(uses, operation))
        self.uses = uses
        self.operation = operation


def live_trading_change_warning(uses: list[str], operation: str) -> str:
    """Lo que de verdad queda descuadrado, que no es lo mismo quitar que añadir.

    Al **quitar**, la cartera queda entera: los sets que sobreviven conservan su
    informe OHLC. Lo que no se recoloca es la cuenta, que mantiene en su
    historial las operaciones que esa estrategia ya ejecutó mientras la
    auditoría pierde el lado tester con el que emparejarlas, así que a partir
    de aquí salen como ajenas.

    Al **añadir**, el que falta es el informe: un set recién subido no lo tiene,
    así que hasta que se genere el histórico OHLC la promesa de la cuenta mide
    menos estrategias de las que la cuenta opera. Es el mismo aviso que la
    pantalla ya deja fijo tras un alta, dicho antes de hacerla.
    """
    consequence = (
        "La cuenta conserva las operaciones que esa estrategia ya ejecutó y la "
        "auditoría se quedará sin su lado tester, así que saldrán como ajenas."
        if operation == "remove" else
        "El set entra sin informe OHLC, así que hasta que generes el histórico "
        "la promesa de la cuenta mide menos estrategias de las que opera."
    )
    return (
        "El portafolio está operando en real en "
        f"{', '.join(uses)}. {consequence} La promesa de la cuenta y las "
        "pantallas que cuelgan de ella se vuelven a medir con la composición "
        "nueva."
    )


def guard_manual_members(handler, node_id: str, body: dict[str, Any]) -> int:
    """Decide qué cambios de composición admite una cartera que opera en real.

    Un uso en real no es sólo una pantalla: su cuenta se sondea, su promesa se
    mide contra esa composición y sus lotes reales están declarados por
    estrategia.

    Quitar y añadir se admiten los dos con una confirmación explícita
    (`live_trading_ack`), y el aviso dice en cada caso qué queda descuadrado:
    `live_trading_change_warning`. El usuario es quien decide qué estrategias
    opera la cuenta, y obligarle a desmarcar el uso para volver a marcarlo
    después no protegía nada —dejaba la cuenta operando una cosa y la cartera
    diciendo otra durante todo el rodeo—.

    Lo que queda pendiente tras un alta no se pierde por admitirla: el aviso de
    «faltan N históricos OHLC» sigue fijo en el detalle, porque sale de los
    datos de la cartera y no de haber visto la subida.

    El nodo tiene su propio candado para la auditoría y el sondeo en curso: lo
    que no se puede ver desde aquí es qué terminales están ocupadas en este
    instante, y lo que no se puede ver desde allí es qué uso marcó el usuario.
    """
    portfolio_id = safe_int(body.get("portfolio_id"), 0, minimum=1)
    if not portfolio_id:
        raise ValueError("El identificador del portafolio no es válido")
    # Fuera del cuerpo que se reenvía: el nodo no conoce el campo y la
    # confirmación es un asunto del manager, que es quien sabe qué uso está
    # marcado en real.
    confirmed = body.pop("live_trading_ack", None) is True
    uses = live_trading_uses(handler, node_id, portfolio_id)
    if uses and not confirmed:
        raise LiveTradingChangeNeedsAck(uses, str(body.get("operation") or ""))
    return portfolio_id


def manual_portfolio_action(handler, node: dict, action: str, body: dict[str, Any]) -> None:
    endpoints = {
        "manual-defaults": "manual-defaults",
        "manual-history": "manual-history",
        "manual-members": "manual-members",
    }
    endpoint = endpoints[action]
    node_id = str(node.get("id") or "")
    try:
        portfolio_id = (
            guard_manual_members(handler, node_id, body) if action == "manual-members" else 0
        )
    except LiveTradingChangeNeedsAck as pending:
        handler._send_json(409, {
            "error": str(pending),
            "live_trading_uses": pending.uses,
            "needs_live_trading_ack": True,
        })
        return
    status, value = manager_http.node_request(
        node, "POST", f"/api/v1/portfolios/{endpoint}", body, timeout=120,
    )
    if status == 404:
        raise ValueError("El nodo necesita actualizarse y reiniciarse para gestionar este portafolio manual.")
    if portfolio_id and 200 <= status < 300:
        handler.server.portfolios._invalidate_node_snapshots(node_id)
        handler.server.live_accounts.discard_expectation(node_id, portfolio_id)
    handler._send_json(status, value)
