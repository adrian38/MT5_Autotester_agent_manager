"""Enlazar las reales por el comentario que el EA escribió en la orden.

El auditor empareja por símbolo, lado, lote y tiempo, y la atribución del Excel
nombra al dueño ticket a ticket. Las dos son conjeturas o trabajo a mano. En
AXI hay algo mejor: el EA escribe en el comentario de la orden de apertura un
código —`TESLA_M15_006`— que nombra el `.set` que la abrió. No es una
conjetura ni una opinión; lo escribió el programa que operó.

El comentario no dice el nombre del `.set`, así que hace falta la tabla de
correspondencias que el usuario mantiene aparte (`.docx`, dos columnas:
«Nombre del setfile» y «EA_Comment»). Esto la lee y la deja guardada en el
perfil del uso, como el Excel del autolote y el de la atribución.

**Esto no es un camino nuevo de emparejado**: resuelve a la misma forma que la
atribución del Excel —ticket → estrategia— y se mete por la misma puerta, de
modo que anota la real sin pareja, filtra lo que el selector ofrece y, si la
estrategia tiene además una fila «SIN REAL», forma la pareja ella sola. Lo que
cambia es de dónde sale el dato.

**El comentario sólo llega si lo publica el nodo.** `live_audit_extract.py` de
cada agente lo añadió el 2026-10-22; las auditorías ejecutadas antes de ese
reinicio traen las reales sin `comment` y aquí no hay nada que enlazar. No es
un fallo que haya que avisar fuera: una tabla sin comentarios que casen es
indistinguible de un uso que no es de AXI.

**Esto manda sobre el Excel** cuando los dos hablan del mismo ticket. Decidido
con el usuario el 2026-10-22: el comentario lo escribió el programa que operó y
el Excel lo tecleó él cuando no tenía esa señal, así que subir el documento
retira solo las atribuciones viejas que lo contradigan, sin tener que ir a
quitarlas una por una. Lo único que sigue por encima de las dos cosas son las
parejas que el usuario elige en el selector: ésas las decide él mirando la
fila.
"""
from __future__ import annotations

import base64
import hashlib
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .live_audit_attribution import _names_a_strategy
from .live_audit_autolot import _rule_key


MAX_DOCUMENT_BYTES = 700_000
MAX_DOCUMENT_UNCOMPRESSED_BYTES = 8_000_000
MAX_COMMENT_LINKS = 500
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_HEADERS = {"nombredelsetfile", "setfile", "eacomment", "comentario", "comment"}


def empty_comment_links() -> dict[str, Any]:
    return {"source_name": "", "source_sha256": "", "rules": {}}


def comment_links_supported(node: dict[str, Any]) -> bool:
    """Sólo los EA de AXI escriben el código del `.set` en el comentario.

    Es una propiedad de ese broker, no una regla de negocio: en los demás el
    comentario trae lo que ponga el terminal —«[sl 359.87]», el nombre de la
    plataforma— y leerlo como si nombrara una estrategia enlazaría reales
    ajenas sin que nada lo dijera.

    **El campo es `portfolio_broker`**, que es el que lleva el registro del
    nodo en `manager.json`. El `broker` a secas sólo aparece en lo que el
    agente dice de sí mismo —`/api/nodes` lo compone con él—, y no llega hasta
    aquí: mirarlo únicamente a él dejaba el botón oculto también en AXI, sin
    que nada lo dijera. Se aceptan los dos porque las dos formas circulan.
    """
    for key in ("portfolio_broker", "broker"):
        if str((node or {}).get(key) or "").strip().casefold() == "axi":
            return True
    return False


def comment_key(value: Any) -> str:
    """El comentario normalizado. MT5 recorta y el usuario escribe en mayúsculas."""
    return " ".join(str(value or "").split()).casefold()


def _header_cell(value: str) -> bool:
    return "".join(char for char in value.casefold() if char.isalnum()) in _HEADERS


def _normalize_rules(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_COMMENT_LINKS:
        raise ValueError(
            f"La tabla admite como máximo {MAX_COMMENT_LINKS} comentarios",
        )
    rules: dict[str, str] = {}
    for raw_comment, raw_set in value.items():
        comment = comment_key(raw_comment)
        set_name = " ".join(str(raw_set or "").split())
        if not comment or len(comment) > 128:
            raise ValueError(f"«{raw_comment}» no es un comentario válido")
        if not set_name or len(set_name) > 512:
            raise ValueError(f"El comentario {comment} no nombra ningún setfile")
        rules[comment] = set_name
    return rules


def normalize_comment_links(value: Any) -> dict[str, Any]:
    if value is None:
        return empty_comment_links()
    if not isinstance(value, dict):
        raise ValueError("comment_links debe ser un objeto JSON")
    unknown = set(value) - {"source_name", "source_sha256", "rules"}
    if unknown:
        raise ValueError(f"Campos desconocidos en comment_links: {', '.join(sorted(unknown))}")
    source_name = str(value.get("source_name") or "").strip()
    if len(source_name) > 255 or "\n" in source_name or "\r" in source_name:
        raise ValueError("El nombre del documento de comentarios no es válido")
    digest = str(value.get("source_sha256") or "").strip().lower()
    if digest and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("La huella del documento de comentarios no es válida")
    return {
        "source_name": source_name, "source_sha256": digest,
        "rules": _normalize_rules(value.get("rules")),
    }


def _cell_text(cell: Any) -> str:
    return " ".join(
        "".join(node.text or "" for node in cell.iter(f"{{{_W_NS}}}t")).split()
    )


def _table_rows(contents: bytes) -> list[list[str]]:
    """Las filas de las tablas del `.docx`, en texto plano."""
    with zipfile.ZipFile(BytesIO(contents)) as archive:
        entry = next(
            (name for name in archive.namelist() if name == "word/document.xml"), "",
        )
        if not entry:
            raise ValueError("El documento no tiene cuerpo: ¿es realmente un .docx?")
        if archive.getinfo(entry).file_size > MAX_DOCUMENT_UNCOMPRESSED_BYTES:
            raise ValueError("El cuerpo del documento es demasiado grande")
        body = ElementTree.fromstring(archive.read(entry))
    return [
        [_cell_text(cell) for cell in row.findall(f"{{{_W_NS}}}tc")]
        for table in body.iter(f"{{{_W_NS}}}tbl")
        for row in table.findall(f".//{{{_W_NS}}}tr")
    ]


def rules_from_rows(rows: list[list[str]]) -> dict[str, str]:
    """Separa el `.set` del comentario mirando la celda, no la columna.

    El documento declara «Nombre del setfile» y «EA_Comment» en ese orden,
    pero ordenar por columna rompería con sólo invertirlas y el error sería
    silencioso: cada comentario quedaría enlazado a un `.set` que no existe y
    la tabla diría que no enlaza nada. La celda que nombra un `.set` se
    reconoce sola.
    """
    rules: dict[str, str] = {}
    for row in rows:
        cells = [cell for cell in row if cell]
        if len(cells) < 2 or any(_header_cell(cell) for cell in cells[:2]):
            continue
        named = [cell for cell in cells[:2] if cell.casefold().endswith(".set")]
        set_name, comment = (
            (named[0], next(cell for cell in cells[:2] if cell != named[0]))
            if len(named) == 1 else (cells[0], cells[1])
        )
        if comment_key(comment):
            rules[comment_key(comment)] = set_name
    if not rules:
        raise ValueError(
            "El documento no trae ninguna fila con un setfile y su comentario. "
            "Se espera una tabla de dos columnas: «Nombre del setfile» y «EA_Comment»."
        )
    return rules


def import_document(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("La importación de comentarios debe ser un objeto JSON")
    filename = Path(str(payload.get("filename") or "")).name
    if not filename.casefold().endswith(".docx"):
        raise ValueError("Selecciona un archivo .docx")
    try:
        contents = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("El contenido del documento no está codificado correctamente") from exc
    if not contents or len(contents) > MAX_DOCUMENT_BYTES:
        raise ValueError("El documento debe pesar entre 1 byte y 700 KB")
    try:
        rows = _table_rows(contents)
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError("El documento no se puede leer como .docx") from exc
    return normalize_comment_links({
        "source_name": filename[:255],
        "source_sha256": hashlib.sha256(contents).hexdigest(),
        "rules": rules_from_rows(rows),
    })


def links_by_comment(
    links: Any, members: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Resuelve cada comentario al `candidate_id` del miembro que lo escribe.

    Mismo emparejado por nombre de `.set` que el autolote y la atribución. Un
    comentario cuyo `.set` no está en este portafolio **se descarta**, no se
    marca como ajeno: la tabla es una sola para todos los usos del usuario y
    nombrar un `.set` de otro portafolio no dice nada de éste.
    """
    rules = normalize_comment_links(links)["rules"]
    indexed: dict[str, dict[str, str]] = {}
    for member in members:
        strategy = str(member.get("candidate_id") or "").strip()
        source = str(member.get("set_id") or member.get("set_path") or "").strip()
        for label in (strategy, member.get("set_name"), source):
            key = _rule_key(label)
            if key and strategy:
                indexed.setdefault(key, {"strategy": strategy, "set_name": str(
                    member.get("set_name") or label or "",
                )})
    resolved: dict[str, dict[str, str]] = {}
    for comment, set_name in rules.items():
        member = indexed.get(_rule_key(set_name))
        if member:
            resolved[comment] = {
                "strategy": member["strategy"], "strategy_set": set_name,
            }
    return resolved


def merged_attribution(
    excel: dict[str, dict[str, Any]], comments: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """El comentario manda sobre el Excel, **salvo sobre una exclusión**.

    Las dos cosas que el Excel puede decir de un ticket no son del mismo tipo:

    - «es de esta estrategia» es una conjetura sobre su dueño, y ahí el
      comentario sabe más: lo escribió el programa que abrió la orden.
    - «no contabiliza en este portafolio» es una afirmación sobre **pertenencia**,
      y el comentario no está en condiciones de contradecirla: sólo nombra el
      `.set`, no el portafolio. La misma estrategia puede estar operando fuera
      de este portafolio, y entonces su comentario es correcto y la operación
      sigue sin contar aquí.

    Dejar que el comentario ganara también ahí devolvía a la curva Real 15
    operaciones y −31,01 € que el usuario había sacado a mano en
    `auditor_18.10.2026_no_contabiliza.xlsx` (medido el 2026-10-22 sobre
    `Christmas_Axi`), y sin decirlo en ninguna parte.

    Pero una exclusión no siempre es deliberada: `attribution_by_ticket` marca
    igual lo que **nombra un `.set` que ya no es miembro** de este portafolio,
    y eso sí es una atribución vieja, justo lo que subir el documento tenía que
    poder corregir sin ir a mano. Se distinguen por el texto, con el mismo
    criterio que ya usa el importador para decidir si una celda nombra una
    estrategia: una frase —«no contabiliza en este portafolio»— manda sobre el
    comentario; un nombre de `.set` que nadie reclama, no.
    """
    merged = dict(excel)
    for ticket, rule in comments.items():
        previous = merged.get(ticket) or {}
        deliberate = previous.get("excluded") and not _names_a_strategy(
            previous.get("reason"),
        )
        if not deliberate:
            merged[ticket] = rule
    return merged


def attribution_from_comments(
    real_trades: list[dict[str, Any]], resolved: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Lo que la tabla dice, con la forma que ya entiende la atribución.

    Reutilizar `ticket → estrategia` en vez de abrir un camino propio es lo
    que hace que esto anote la real suelta, filtre el selector y forme la
    pareja sin tocar ninguna de las tres cosas.
    """
    if not resolved:
        return {}
    rules: dict[str, dict[str, Any]] = {}
    for trade in real_trades:
        rule = resolved.get(comment_key(trade.get("comment")))
        ticket = str(trade.get("position_id") or trade.get("ticket") or "").strip()
        if rule and ticket.isdigit():
            rules[ticket] = {
                "strategy": rule["strategy"], "strategy_set": rule["strategy_set"],
                "reason": "", "excluded": False, "source": "comment",
            }
    return rules
