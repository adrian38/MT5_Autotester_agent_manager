"""Atribución manual de las reales que ningún cierre del tester reclamó.

El auditor empareja por símbolo, lado, tiempo y lote. Lo que queda fuera no es
siempre ajeno al portafolio: en `audit-148` de RoboForex quedaron 20 cierres
reales sin pareja, y 14 de ellos eran de la misma estrategia XAUUSD. Sin
atribuirlos, esa estrategia salía `REPRODUCE` con 22,09 € de real cuando en la
cuenta había perdido 67,06 €.

**El magic no sirve y no se intenta.** El del terminal real no tiene por qué
coincidir con el del `.set` importado —por eso la pertenencia al portafolio se
decide por símbolo y lote—, y el nodo ni siquiera lo publica: las 102 reales de
esa auditoría vienen con el campo vacío. El símbolo tampoco decide cuando dos
miembros del portafolio lo operan, que era el caso de 17 de las 20. Así que lo
dice el usuario, operación por operación, y esto sólo lee lo que dijo.

El fichero es la **exportación del detalle de operaciones** con una columna
añadida a mano; no hay que preparar nada aparte. De cada fila se usan dos cosas:

- el `ticket` que la celda de mercado ya imprime (`ticket 759405393`), que es el
  `position_id` y el único identificador único de la materia prima;
- la columna nueva, cuyo último segmento separado por `;` es el nombre del
  `.set`. Lo que haya delante es nota del usuario y se conserva para enseñarla.

Si ese último segmento no nombra a ningún miembro del portafolio, la operación
queda **excluida** y el texto pasa a ser el motivo: así es como «no contabiliza
en este portafolio» saca de la curva real los tres cierres que no eran del
portafolio.
"""
from __future__ import annotations

import base64
import hashlib
import posixpath
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from .live_audit_autolot import MAX_WORKBOOK_BYTES, _rule_key, _sheet_rows


MAX_ATTRIBUTIONS = 5000
# El ticket pegado a la palabra es lo que exporta la pantalla («· ticket 46430179»),
# pero la columna la escribe una persona y «ticket real correspondiente: 46430179»
# es igual de legítimo. Con palabras en medio se exigen cuatro dígitos para no
# confundir un «ticket pendiente, 2 intentos» con un número de operación.
_TICKET = re.compile(
    r"\bticket\s*[:#]?\s*(\d{1,20})\b|\bticket\b\D{1,40}?(\d{4,20})\b", re.IGNORECASE
)
_TESTER_ROW = re.compile(r"^\s*T(\d{1,9})\s*$", re.IGNORECASE)


def empty_attribution() -> dict[str, Any]:
    return {"source_name": "", "source_sha256": "", "sources": {}, "rules": {}}


def _ticket(value: Any) -> str:
    digits = str(value or "").strip()
    return digits if digits.isdigit() and len(digits) <= 20 else ""


def _sha256(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if digest and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError(f"La huella {label} no es válida")
    return digest


def _normalize_rule(ticket: str, raw: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise ValueError(f"La atribución de {ticket} debe ser un objeto JSON")
    strategy_set = str(raw.get("strategy_set") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    if len(strategy_set) > 512 or len(reason) > 512:
        raise ValueError(f"La atribución de {ticket} es demasiado larga")
    if any(char in strategy_set + reason for char in "\n\r"):
        raise ValueError(f"La atribución de {ticket} no puede ocupar varias líneas")
    # De qué fichero salió. Sin esto, acumular sería una calle de un solo
    # sentido: no habría forma de retirar un Excel equivocado sin perder los
    # demás. Vacío en lo guardado antes de que existiera el campo.
    return ticket, {
        "strategy_set": strategy_set, "reason": reason,
        "source": _sha256(raw.get("source"), f"del origen de {ticket}"),
    }


def _normalize_sources(value: Any) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("real_attribution.sources debe ser un objeto por fichero")
    sources = {}
    for raw_digest, raw_source in value.items():
        digest = _sha256(raw_digest, "de un fichero de atribución")
        if not digest:
            continue
        name = str((raw_source or {}).get("name") or "").strip() if isinstance(raw_source, dict) else ""
        if len(name) > 255 or "\n" in name or "\r" in name:
            raise ValueError("El nombre de un Excel de atribución no es válido")
        sources[digest] = {"name": name}
    return sources


def normalize_attribution(value: Any) -> dict[str, Any]:
    if value is None:
        return empty_attribution()
    if not isinstance(value, dict):
        raise ValueError("real_attribution debe ser un objeto JSON")
    unknown = set(value) - {"source_name", "source_sha256", "sources", "rules"}
    if unknown:
        raise ValueError(f"Campos de atribución desconocidos: {', '.join(sorted(unknown))}")
    source_name = str(value.get("source_name") or "").strip()
    source_sha256 = _sha256(value.get("source_sha256"), "del Excel de atribución")
    if len(source_name) > 255 or "\n" in source_name or "\r" in source_name:
        raise ValueError("El nombre del Excel de atribución no es válido")
    sources = _normalize_sources(value.get("sources"))
    raw_rules = value.get("rules") or {}
    if not isinstance(raw_rules, dict) or len(raw_rules) > MAX_ATTRIBUTIONS:
        raise ValueError(f"La atribución admite como máximo {MAX_ATTRIBUTIONS} operaciones")
    rules = {}
    for raw_ticket, raw_rule in raw_rules.items():
        ticket = _ticket(raw_ticket)
        if not ticket:
            raise ValueError(f"«{raw_ticket}» no es un ticket válido")
        key, rule = _normalize_rule(ticket, raw_rule)
        rules[key] = rule
    return {
        "source_name": source_name, "source_sha256": source_sha256,
        "sources": sources, "rules": rules,
    }


def attribution_sources(attribution: Any) -> list[dict[str, Any]]:
    """Los Excel subidos y cuántas operaciones aporta cada uno, para poder
    quitarlos de uno en uno."""
    stored = normalize_attribution(attribution)
    counts: dict[str, int] = {}
    for rule in stored["rules"].values():
        counts[rule["source"]] = counts.get(rule["source"], 0) + 1
    listed = [
        {
            "sha256": digest, "name": source["name"] or "Excel sin nombre",
            "operations": counts.get(digest, 0),
        }
        for digest, source in stored["sources"].items()
    ]
    # Lo guardado antes de que existiera el campo no tiene fichero asociado y
    # no puede quedar invisible: se enseña para poder retirarlo.
    if counts.get(""):
        listed.append({"sha256": "", "name": "Subidas anteriores", "operations": counts[""]})
    return sorted(listed, key=lambda item: (-item["operations"], item["name"]))


def remove_source(attribution: Any, sha256: str) -> dict[str, Any]:
    """Retira un Excel y sólo lo que ese Excel dijo."""
    stored = normalize_attribution(attribution)
    digest = _sha256(sha256, "del Excel que se retira") if sha256 else ""
    rules = {
        ticket: rule for ticket, rule in stored["rules"].items()
        if rule["source"] != digest
    }
    if len(rules) == len(stored["rules"]) and digest not in stored["sources"]:
        raise ValueError("Ese Excel no está entre los subidos para este uso")
    sources = {key: value for key, value in stored["sources"].items() if key != digest}
    return normalize_attribution({
        "source_name": stored["source_name"] if digest != stored["source_sha256"] else "",
        "source_sha256": stored["source_sha256"] if digest != stored["source_sha256"] else "",
        "sources": sources, "rules": rules,
    })


def _row_ticket(row: dict[int, Any]) -> str:
    """El ticket, lo lleve la columna que lo lleve.

    Se busca en toda la fila en vez de fijar una columna: el fichero es la
    exportación de la pantalla, y sus columnas pueden reordenarse sin que eso
    deba romper la importación.
    """
    for value in row.values():
        found = _TICKET.search(str(value or ""))
        if found:
            return found.group(1) or found.group(2)
    return ""


def _canonical(value: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in plain.casefold() if char.isalnum())


def _names_a_strategy(value: Any) -> bool:
    """¿Esta celda nombra una estrategia? Un `.set` o un `BROKER/TIPO:id`."""
    text = str(value or "")
    return ".set" in text.casefold() or bool(re.search(r"\w/\w+:\d+", text))


def _reason_column(header: dict[int, Any], body: list[dict[int, Any]]) -> int:
    """La columna donde el usuario escribió la estrategia.

    Manda el nombre de la cabecera. Si no la nombra, manda la última columna
    con contenido: es donde acaba lo que se añade a la derecha de lo exportado,
    y da igual que sea el nombre del `.set` o una nota a mano.

    La excepción es que lo añadido sea el **ticket**. Entonces la estrategia no
    está ahí, sino en lo exportado, y se busca la columna que nombra un `.set`
    o un identificador del auditor. Decidirlo mirando sólo la última columna
    importa: la celda de mercado de una fila emparejada también nombra su
    `.set`, así que preferirla siempre pisaría la nota del usuario.
    """
    named = {
        column for column, value in (header or {}).items()
        if _canonical(value) in {"razon", "estrategia", "atribucion", "pertenece", "setname"}
    }
    if named:
        return max(named)
    candidates = {
        column for row in body for column, value in row.items()
        if str(value or "").strip()
    }
    if not candidates:
        raise ValueError(
            "Las filas del Excel no traen ninguna columna con la estrategia de cada operación"
        )
    tickets = {
        column for row in body for column, value in row.items()
        if _TICKET.search(str(value or ""))
    }
    last = max(candidates)
    if last not in tickets:
        return last
    strategies = {
        column for row in body for column, value in row.items()
        if _names_a_strategy(value)
    }
    return max(strategies) if strategies else max(candidates - tickets or candidates)


def split_reason(value: Any) -> tuple[str, str]:
    """Separa la nota del usuario del nombre del `.set`, que va al final."""
    text = str(value or "").strip()
    if not text:
        return "", ""
    parts = [part.strip() for part in text.split(";") if part.strip()]
    if not parts:
        return "", ""
    return parts[-1], "; ".join(parts[:-1])


def rules_from_rows(rows: list[dict[int, Any]]) -> dict[str, dict[str, str]]:
    body = [row for row in rows if _row_ticket(row)]
    if not body:
        raise ValueError(
            "Ninguna fila del Excel trae el ticket de una operación real. "
            "Exporta el detalle de operaciones de esta auditoría y añade la columna."
        )
    headers = [row for row in rows if not _row_ticket(row) and len(row) >= 3]
    column = _reason_column(headers[0] if headers else {}, body)
    rules: dict[str, dict[str, str]] = {}
    for row in body:
        ticket = _row_ticket(row)
        strategy_set, reason = split_reason(row.get(column))
        if not strategy_set:
            continue
        rules[ticket] = {"strategy_set": strategy_set, "reason": reason}
        if len(rules) > MAX_ATTRIBUTIONS:
            raise ValueError(f"La atribución admite como máximo {MAX_ATTRIBUTIONS} operaciones")
    if not rules:
        raise ValueError("Ninguna fila del Excel dice a qué estrategia pertenece la operación")
    return rules


def _workbook_payload(payload: Any) -> tuple[str, bytes, list[dict[int, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("La importación de atribución debe ser un objeto JSON")
    filename = Path(str(payload.get("filename") or "")).name
    if not filename.casefold().endswith(".xlsx"):
        raise ValueError("Selecciona un archivo .xlsx")
    try:
        contents = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("El contenido del Excel no está codificado correctamente") from exc
    if not contents or len(contents) > MAX_WORKBOOK_BYTES:
        raise ValueError("El Excel de atribución debe pesar entre 1 byte y 700 KB")
    return filename, contents, _sheet_rows(contents)


def _attribution_from_workbook(
    filename: str, contents: bytes, rows: list[dict[int, Any]],
) -> dict[str, Any]:
    return normalize_attribution({
        "source_name": filename[:255],
        "source_sha256": hashlib.sha256(contents).hexdigest(),
        "rules": rules_from_rows(rows),
    })


def manual_pair_rows(rows: list[dict[int, Any]]) -> dict[str, str]:
    """Las filas SIN REAL donde el usuario escribió su ticket correspondiente."""
    pairs: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for row in rows:
        if "sinreal" not in {_canonical(value) for value in row.values()}:
            continue
        tester = next((
            match.group(1) for value in row.values()
            if (match := _TESTER_ROW.fullmatch(str(value or "")))
        ), "")
        ticket = _row_ticket(row)
        if not tester or not ticket:
            continue
        if tester in pairs and pairs[tester] != ticket:
            raise ValueError(f"La fila T{tester} nombra dos operaciones reales distintas")
        if claimed.setdefault(ticket, tester) != tester:
            raise ValueError(f"La operación real {ticket} corresponde a más de una fila del tester")
        pairs[tester] = ticket
    return pairs


_NO_REAL = {"", "sinreal", "ninguna", "no", "nada"}


def _pair_columns(rows: list[dict[int, Any]]) -> tuple[int, dict[str, int]]:
    """La cabecera del Excel de enlace: `Fila`, `Ticket` y, si está, `Nota`.

    Por nombre y no por posición: el usuario reordena columnas, y fijar la
    tercera obligaría a dejarla vacía cuando no tiene nada que anotar.
    """
    aliases = {"fila": "row", "ticket": "ticket", "nota": "note"}
    for index, row in enumerate(rows):
        found = {
            aliases[key]: column for column, value in row.items()
            if (key := _canonical(value)) in aliases
        }
        if {"row", "ticket"} <= set(found):
            return index, found
    raise ValueError(
        "El Excel de enlace necesita una cabecera con las columnas Fila y Ticket",
    )


def pair_rows_from_workbook(rows: list[dict[int, Any]]) -> dict[str, dict[str, str]]:
    """`Tn → ticket` tal y como lo escribió el usuario, con su nota.

    Es el Excel que corrige al auditor, así que **no** mira el estado de la
    fila: la `Tn` puede estar ya emparejada y lo que el usuario dice es que lo
    está con la real equivocada. Un ticket vacío dice que esa fila no tiene
    real, que es lo que no se podía expresar de ninguna otra forma.
    """
    header, columns = _pair_columns(rows)
    pairs: dict[str, dict[str, str]] = {}
    claimed: dict[str, str] = {}
    for row in rows[header + 1:]:
        tester = _TESTER_ROW.fullmatch(str(row.get(columns["row"]) or "").strip())
        if not tester:
            continue
        raw = str(row.get(columns["ticket"]) or "").strip()
        found = _TICKET.search(raw)
        ticket = (found.group(1) or found.group(2)) if found else _ticket(raw)
        if not ticket and _canonical(raw) not in _NO_REAL:
            raise ValueError(f"La fila T{tester.group(1)} no nombra un ticket válido: {raw}")
        if tester.group(1) in pairs:
            raise ValueError(f"La fila T{tester.group(1)} aparece dos veces")
        if ticket and claimed.setdefault(ticket, tester.group(1)) != tester.group(1):
            raise ValueError(f"La operación real {ticket} la reclaman dos filas del tester")
        pairs[tester.group(1)] = {
            "ticket": ticket,
            "note": str(row.get(columns.get("note", -1)) or "").strip()[:200],
        }
    if not pairs:
        raise ValueError("Ninguna fila del Excel nombra una operación del tester (T19, T21…)")
    if len(pairs) > MAX_ATTRIBUTIONS:
        raise ValueError(f"El Excel de enlace admite como máximo {MAX_ATTRIBUTIONS} filas")
    return pairs


def import_pair_workbook(payload: Any) -> dict[str, Any]:
    """El Excel que sólo enlaza: ni atribuye estrategias ni excluye reales."""
    filename, contents, rows = _workbook_payload(payload)
    return {
        "source_name": filename[:255],
        "source_sha256": hashlib.sha256(contents).hexdigest(),
        "rows": pair_rows_from_workbook(rows),
    }


def import_workbook(payload: Any) -> dict[str, Any]:
    filename, contents, rows = _workbook_payload(payload)
    return _attribution_from_workbook(filename, contents, rows)


def import_workbook_bundle(payload: Any) -> dict[str, Any]:
    """Atribución y correspondencias explícitas conservadas del mismo Excel."""
    filename, contents, rows = _workbook_payload(payload)
    return {
        "real_attribution": _attribution_from_workbook(filename, contents, rows),
        "manual_pair_rows": manual_pair_rows(rows),
    }


def _member_keys(member: dict[str, Any]) -> tuple[str, ...]:
    strategy = str(member.get("candidate_id") or "").strip()
    source = str(member.get("set_id") or member.get("set_path") or "").strip()
    if not strategy:
        strategy = posixpath.splitext(posixpath.basename(source.replace("\\", "/")))[0]
    return strategy, str(member.get("set_name") or ""), source


def merge_attribution(stored: Any, incoming: Any) -> dict[str, Any]:
    """Acumula lo subido sobre lo que ya había, por ticket.

    Cada Excel suele cubrir sólo las operaciones de una auditoría, y el trabajo
    de decidir de quién es cada una no se repite: subir el segundo fichero no
    puede borrar lo dicho en el primero. Un ticket repetido se queda con lo
    último subido, que es la correción; el resto se conserva.
    """
    previous = normalize_attribution(stored)
    added = normalize_attribution(incoming)
    digest = added["source_sha256"]
    marked = {
        ticket: {**rule, "source": rule["source"] or digest}
        for ticket, rule in added["rules"].items()
    }
    sources = dict(previous["sources"])
    if digest:
        sources[digest] = {"name": added["source_name"]}
    return normalize_attribution({
        "source_name": added["source_name"] or previous["source_name"],
        "source_sha256": digest or previous["source_sha256"],
        "sources": sources, "rules": {**previous["rules"], **marked},
    })


def rewrite_profile_attribution(
    record: dict[str, Any], audit_id: str, rewrite: Callable[[Any], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Los perfiles de un nodo con la atribución de un uso reescrita.

    Devuelve copias: quien llame decide cuándo persistir. Toca **un solo
    campo** de **un solo uso**, porque quien cambia la atribución es la pantalla
    de Análisis y esa no conoce el resto de la configuración —credenciales
    referenciadas, cuentas, los demás usos—: reescribir el perfil entero desde
    ahí lo borraría.
    """
    profiles = {
        str(key): dict(value) for key, value in (record.get("profiles") or {}).items()
    }
    profile = profiles.get(str(audit_id))
    if profile is None:
        raise KeyError(f"El uso {audit_id} no tiene configuración guardada")
    profile["real_attribution"] = normalize_attribution(rewrite(profile.get("real_attribution")))
    return profiles


def _trade_ticket(trade: Any) -> str:
    if not isinstance(trade, dict):
        return ""
    return _ticket(trade.get("position_id")) or _ticket(trade.get("ticket"))


def annotate_unmatched(
    operations: list[dict[str, Any]], resolved: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Pone en cada real sin pareja lo que el usuario dijo de ella.

    No se inventa una pareja ni se toca el emparejado del auditor: la fila
    sigue siendo `extra`. Sólo queda dicho de quién es, para que la pantalla
    pueda sumarla al real de esa estrategia o dejarla fuera.
    """
    attributed = excluded = 0
    attributed_total = excluded_total = 0.0
    for row in operations:
        rule = resolved.get(_trade_ticket(row.get("real")))
        if not rule:
            continue
        profit = float((row.get("real") or {}).get("profit") or 0.0)
        row["attribution"] = {
            "strategy": rule["strategy"], "strategy_set": rule["strategy_set"],
            "reason": rule["reason"], "excluded": rule["excluded"],
            # De dónde salió: el Excel que escribió el usuario («manual») o el
            # comentario que el EA dejó en la orden («comment»).
            "source": rule.get("source") or "manual",
        }
        if rule["excluded"]:
            excluded += 1
            excluded_total += profit
        else:
            attributed += 1
            attributed_total += profit
    return {
        "attributed": attributed, "excluded": excluded,
        "unresolved": len(operations) - attributed - excluded,
        "attributed_profit": round(attributed_total, 2),
        "excluded_profit": round(excluded_total, 2),
    }


def attribution_by_ticket(
    attribution: Any, members: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resuelve cada ticket a su `candidate_id`, o lo deja excluido con su motivo.

    Mismo emparejado por nombre de `.set` que usa el autolote: la columna
    nombra el fichero y el auditor identifica por `candidate_id`.
    """
    rules = normalize_attribution(attribution)["rules"]
    indexed = {}
    for member in members:
        strategy = _member_keys(member)[0]
        for label in _member_keys(member):
            key = _rule_key(label)
            if key and strategy:
                indexed.setdefault(key, strategy)
    resolved: dict[str, dict[str, Any]] = {}
    for ticket, rule in rules.items():
        strategy = indexed.get(_rule_key(rule["strategy_set"]))
        resolved[ticket] = {
            "strategy": strategy or "",
            "strategy_set": rule["strategy_set"] if strategy else "",
            # Sin miembro que lo reclame, lo escrito deja de ser el nombre de
            # una estrategia y pasa a ser el motivo de dejarla fuera.
            "reason": rule["reason"] if strategy else (
                "; ".join(part for part in (rule["reason"], rule["strategy_set"]) if part)
            ),
            "excluded": not strategy,
        }
    return resolved
