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
from typing import Any

from .live_audit_autolot import MAX_WORKBOOK_BYTES, _rule_key, _sheet_rows


MAX_ATTRIBUTIONS = 5000
_TICKET = re.compile(r"\bticket\s*[:#]?\s*(\d{1,20})\b", re.IGNORECASE)


def empty_attribution() -> dict[str, Any]:
    return {"source_name": "", "source_sha256": "", "rules": {}}


def _ticket(value: Any) -> str:
    digits = str(value or "").strip()
    return digits if digits.isdigit() and len(digits) <= 20 else ""


def _normalize_rule(ticket: str, raw: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise ValueError(f"La atribución de {ticket} debe ser un objeto JSON")
    strategy_set = str(raw.get("strategy_set") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    if len(strategy_set) > 512 or len(reason) > 512:
        raise ValueError(f"La atribución de {ticket} es demasiado larga")
    if any(char in strategy_set + reason for char in "\n\r"):
        raise ValueError(f"La atribución de {ticket} no puede ocupar varias líneas")
    return ticket, {"strategy_set": strategy_set, "reason": reason}


def normalize_attribution(value: Any) -> dict[str, Any]:
    if value is None:
        return empty_attribution()
    if not isinstance(value, dict):
        raise ValueError("real_attribution debe ser un objeto JSON")
    unknown = set(value) - {"source_name", "source_sha256", "rules"}
    if unknown:
        raise ValueError(f"Campos de atribución desconocidos: {', '.join(sorted(unknown))}")
    source_name = str(value.get("source_name") or "").strip()
    source_sha256 = str(value.get("source_sha256") or "").strip().lower()
    if len(source_name) > 255 or "\n" in source_name or "\r" in source_name:
        raise ValueError("El nombre del Excel de atribución no es válido")
    if source_sha256 and (
        len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256)
    ):
        raise ValueError("La huella del Excel de atribución no es válida")
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
    return {"source_name": source_name, "source_sha256": source_sha256, "rules": rules}


def _row_ticket(row: dict[int, Any]) -> str:
    """El ticket, lo lleve la columna que lo lleve.

    Se busca en toda la fila en vez de fijar una columna: el fichero es la
    exportación de la pantalla, y sus columnas pueden reordenarse sin que eso
    deba romper la importación.
    """
    for value in row.values():
        found = _TICKET.search(str(value or ""))
        if found:
            return found.group(1)
    return ""


def _canonical(value: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in plain.casefold() if char.isalnum())


def _reason_column(header: dict[int, Any], body: list[dict[int, Any]]) -> int:
    """La columna donde el usuario escribió la estrategia.

    Primero por el nombre de la cabecera. Si no la nombra —o la exportación
    cambia de columnas— se coge la última con contenido, que es donde acaba una
    columna añadida a mano a la derecha de lo exportado.
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
    return max(candidates)


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


def import_workbook(payload: Any) -> dict[str, Any]:
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
    return normalize_attribution({
        "source_name": filename[:255],
        "source_sha256": hashlib.sha256(contents).hexdigest(),
        "rules": rules_from_rows(_sheet_rows(contents)),
    })


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
    rules = {**previous["rules"], **added["rules"]}
    return {
        "source_name": added["source_name"] or previous["source_name"],
        "source_sha256": added["source_sha256"] or previous["source_sha256"],
        "rules": rules,
    }


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
            "reason": rule["reason"], "excluded": rule["excluded"], "source": "manual",
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
