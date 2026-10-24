from __future__ import annotations

import base64
import hashlib
import io
import math
import posixpath
import tempfile
import unicodedata
import zipfile
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

from portfolio_manager.mt5_report import parse_report

from .common import load_json
from .live_audit_lot_schedule import scheduled_lot


MAX_WORKBOOK_BYTES = 700_000
MAX_WORKBOOK_UNCOMPRESSED_BYTES = 8_000_000
MAX_RULES = 500
_XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_BALANCE_CACHE: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()


def empty_autolot() -> dict[str, Any]:
    return {"enabled": False, "source_name": "", "source_sha256": "", "rules": {}}


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} debe ser numérico")
    try:
        result = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} debe ser numérico") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{label} debe estar entre {minimum:g} y {maximum:g}")
    return result


def normalize_autolot(value: Any) -> dict[str, Any]:
    if value is None:
        return empty_autolot()
    if not isinstance(value, dict):
        raise ValueError("autolot debe ser un objeto JSON")
    unknown = set(value) - {"enabled", "source_name", "source_sha256", "rules"}
    if unknown:
        raise ValueError(f"Campos de autolote desconocidos: {', '.join(sorted(unknown))}")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("autolot.enabled debe ser verdadero o falso")
    source_name = str(value.get("source_name") or "").strip()
    source_sha256 = str(value.get("source_sha256") or "").strip().lower()
    if len(source_name) > 255 or "\n" in source_name or "\r" in source_name:
        raise ValueError("El nombre del Excel de autolote no es válido")
    if source_sha256 and (len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256)):
        raise ValueError("La huella del Excel de autolote no es válida")
    rules = _normalize_rules(value.get("rules"))
    if enabled and not rules:
        raise ValueError("Activa el autolote después de importar un Excel válido")
    return {"enabled": enabled, "source_name": source_name, "source_sha256": source_sha256, "rules": rules}


def _rule_key(value: Any) -> str:
    filename = posixpath.basename(str(value or "").replace("\\", "/")).strip()
    return filename[:-4].casefold() if filename.casefold().endswith(".set") else filename.casefold()


def rules_by_strategy(autolot: Any, members: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Une las filas del Excel, nombradas por `.set`, con el ID del auditor."""
    rules = normalize_autolot(autolot)["rules"]
    indexed = {_rule_key(name): rule for name, rule in rules.items()}
    bound: dict[str, dict[str, float]] = {}
    for member in members:
        strategy = str(member.get("candidate_id") or "").strip()
        source = str(member.get("set_id") or member.get("set_path") or "").strip()
        if not strategy:
            strategy = posixpath.splitext(posixpath.basename(source.replace("\\", "/")))[0]
        labels = (strategy, member.get("set_name"), source)
        rule = None
        for label in labels:
            key = _rule_key(label)
            if key in indexed:
                rule = indexed[key]
                break
        if strategy and rule:
            bound[strategy] = rule
    return bound


def _normalize_rules(value: Any) -> dict[str, dict[str, float]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_RULES:
        raise ValueError(f"autolot.rules debe contener como máximo {MAX_RULES} estrategias")
    result: dict[str, dict[str, float]] = {}
    for raw_strategy, raw_rule in value.items():
        strategy = str(raw_strategy or "").strip()
        if not strategy or len(strategy) > 512 or "\n" in strategy or "\r" in strategy:
            raise ValueError("Cada regla de autolote debe identificar una estrategia válida")
        if not isinstance(raw_rule, dict):
            raise ValueError(f"La regla de {strategy} debe ser un objeto JSON")
        result[strategy] = {
            "dd_001": _number(raw_rule.get("dd_001"), f"DD 0.01 de {strategy}", 0.00000001, 1e12),
            "risk_pct": _number(raw_rule.get("risk_pct"), f"riesgo de {strategy}", 0.00000001, 100.0),
        }
    return result


def _canonical_header(value: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in plain.casefold() if char.isalnum())


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.text or "" for node in item.iter(f"{{{_XML_NS}}}t")) for item in root]


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    first = workbook.find(f".//{{{_XML_NS}}}sheet")
    if first is None:
        raise ValueError("El Excel no contiene hojas")
    relation_id = first.attrib.get(f"{{{_REL_NS}}}id")
    relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relation = next((item for item in relations if item.attrib.get("Id") == relation_id), None)
    if relation is None:
        raise ValueError("No se pudo localizar la primera hoja del Excel")
    target = relation.attrib.get("Target") or ""
    return target.lstrip("/") if target.startswith("/xl/") else posixpath.normpath(f"xl/{target}")


def _column_number(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


def _cell_value(cell: ElementTree.Element, strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{_XML_NS}}}t"))
    value_node = cell.find(f"{{{_XML_NS}}}v")
    value = value_node.text if value_node is not None else ""
    if cell_type == "s":
        try:
            return strings[int(value)]
        except (ValueError, IndexError):
            return ""
    if cell_type in {"str", "b"}:
        return value
    try:
        return float(value) if "." in value or "e" in value.casefold() else int(value)
    except ValueError:
        return value


def _sheet_rows(contents: bytes) -> list[dict[int, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_WORKBOOK_UNCOMPRESSED_BYTES:
                raise ValueError("El Excel de autolote es demasiado grande al descomprimirlo")
            strings = _shared_strings(archive)
            root = ElementTree.fromstring(archive.read(_first_sheet_path(archive)))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("El archivo no es un Excel .xlsx válido") from exc
    rows: list[dict[int, Any]] = []
    for row in root.iter(f"{{{_XML_NS}}}row"):
        rows.append({_column_number(cell.attrib.get("r", "")): _cell_value(cell, strings) for cell in row})
    return rows


def _rules_from_rows(rows: list[dict[int, Any]]) -> dict[str, dict[str, float]]:
    header_index = -1
    columns: dict[str, int] = {}
    aliases = {"estrategia": "strategy", "dd001": "dd_001", "riesgo": "risk_pct"}
    for index, row in enumerate(rows):
        found = {aliases[key]: column for column, value in row.items() if (key := _canonical_header(value)) in aliases}
        if set(found) == {"strategy", "dd_001", "risk_pct"}:
            header_index, columns = index, found
            break
    if header_index < 0:
        raise ValueError("El Excel debe contener las columnas ESTRATEGIA, Dd_0.01 y %_riesgo")
    rules: dict[str, dict[str, float]] = {}
    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        strategy = str(row.get(columns["strategy"]) or "").strip()
        if not strategy:
            continue
        if strategy in rules:
            raise ValueError(f"La estrategia {strategy} está repetida en el Excel")
        rules[strategy] = {
            "dd_001": _number(row.get(columns["dd_001"]), f"Dd_0.01 de la fila {row_number}", 0.00000001, 1e12),
            "risk_pct": _number(row.get(columns["risk_pct"]), f"%_riesgo de la fila {row_number}", 0.00000001, 100.0),
        }
        if len(rules) > MAX_RULES:
            raise ValueError(f"El Excel no puede contener más de {MAX_RULES} estrategias")
    if not rules:
        raise ValueError("El Excel no contiene ninguna estrategia de autolote")
    return rules


def import_workbook(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("La importación de autolote debe ser un objeto JSON")
    filename = Path(str(payload.get("filename") or "")).name
    if not filename.casefold().endswith(".xlsx"):
        raise ValueError("Selecciona un archivo .xlsx")
    try:
        contents = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("El contenido del Excel no está codificado correctamente") from exc
    if not contents or len(contents) > MAX_WORKBOOK_BYTES:
        raise ValueError("El Excel de autolote debe pesar entre 1 byte y 700 KB")
    result = {
        "enabled": True,
        "source_name": filename[:255],
        "source_sha256": hashlib.sha256(contents).hexdigest(),
        "rules": _rules_from_rows(_sheet_rows(contents)),
    }
    return normalize_autolot(result)


def balance_history_from_html(contents: bytes) -> list[dict[str, Any]]:
    if not contents:
        raise ValueError("El informe nativo de la cuenta real está vacío")
    with tempfile.TemporaryDirectory(prefix="mt5-autolot-") as folder:
        path = Path(folder) / "real_account_report.html"
        path.write_bytes(contents)
        report = parse_report(path)
    history = [
        {"time": deal.timestamp.isoformat(), "balance": float(deal.balance)}
        for deal in report.raw_deals
        if math.isfinite(float(deal.balance)) and float(deal.balance) != 0.0
    ]
    history.sort(key=lambda item: item["time"])
    if not history:
        raise ValueError("El HTML nativo de MT5 no contiene el saldo histórico necesario para el autolote")
    return history


def cached_balance_history(
    cache_key: str, loader: Callable[[], bytes],
) -> list[dict[str, Any]]:
    if cache_key in _BALANCE_CACHE:
        _BALANCE_CACHE.move_to_end(cache_key)
        return list(_BALANCE_CACHE[cache_key])
    history = balance_history_from_html(loader())
    _BALANCE_CACHE[cache_key] = history
    while len(_BALANCE_CACHE) > 32:
        _BALANCE_CACHE.popitem(last=False)
    return list(history)


def prepare_request(request: dict[str, Any]) -> None:
    autolot = normalize_autolot(request.get("autolot"))
    request["autolot"] = autolot
    points: list[tuple[datetime, float]] = []
    for item in request.pop("_autolot_balance_history", []) or []:
        try:
            points.append((_utc_datetime(datetime.fromisoformat(str(item["time"]))), float(item["balance"])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda item: item[0])
    request["_autolot_balance_points"] = points
    if autolot["enabled"] and not points:
        message = request.pop("_autolot_balance_error", "")
        raise ValueError(message or "El autolote no pudo leer el saldo histórico del informe nativo de MT5")


def _utc_datetime(value: datetime) -> datetime:
    """Normaliza a UTC; el informe nativo de MT5 imprime UTC sin sufijo."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _balance_at(points: list[tuple[datetime, float]], moment: datetime) -> float | None:
    comparable_moment = _utc_datetime(moment)
    previous = [balance for timestamp, balance in points if timestamp <= comparable_moment]
    if previous:
        return previous[-1]
    return points[0][1] if points else None


def executable_lot(raw_lot: float, rule: tuple[float, float] | None) -> float:
    """El lote bruto llevado al paso del bróker, sin bajar de su mínimo.

    Es público porque la promesa de la portada tiene que redondear igual que
    el veredicto: dos redondeos distintos del mismo lote compararían dos
    tamaños de cuenta que no son el mismo.
    """
    minimum, step = rule or (0.01, 0.01)
    step = step if step > 0 else minimum
    units = (Decimal(str(max(raw_lot, minimum))) / Decimal(str(step))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    )
    return round(float(units * Decimal(str(step))), 8)


def broker_volume_rules(project: Any, broker: str) -> dict[str, tuple[float, float]]:
    """`volume_min`/`volume_step` que el agente del bróker dejó en su proyecto.

    Puede no existir —RoboForex no publica el fichero— y entonces el
    redondeo cae en el 0,01 por defecto de `executable_lot`, que es lo que ya
    hacía el veredicto.
    """
    name = str(broker or "ICTRADING").strip().lower()
    try:
        data = load_json(Path(project) / "assets" / f"{name}_symbol_specs.json")
    except (OSError, ValueError):
        return {}
    symbols = data.get("symbols") if isinstance(data, dict) else None
    if not isinstance(symbols, dict):
        return {}
    rules: dict[str, tuple[float, float]] = {}
    for symbol, raw in symbols.items():
        if not isinstance(raw, dict):
            continue
        try:
            minimum = float(raw.get("volume_min") or 0.0)
            step = float(raw.get("volume_step") or minimum or 0.0)
        except (TypeError, ValueError):
            continue
        if minimum > 0:
            rules[str(symbol).casefold()] = (minimum, step if step > 0 else minimum)
    return rules


def expected_volume(
    expected: dict[str, Any], request: dict[str, Any], actual: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tester_volume = float(expected.get("volume") or 0.0)
    strategy = str(expected.get("strategy") or "")
    autolot = request.get("autolot") or {}
    bound = request.get("_autolot_rules_by_strategy")
    rules = bound if isinstance(bound, dict) else autolot.get("rules") or {}
    rule = rules.get(strategy) if autolot.get("enabled") else None
    moment = (actual or {}).get("open_time") or expected.get("open_time")
    balance = _balance_at(request.get("_autolot_balance_points") or [], moment) if isinstance(moment, datetime) else None
    if rule and balance is not None:
        raw_lot = balance * float(rule["risk_pct"]) / 100.0 * 0.01 / float(rule["dd_001"])
        volume_rule = (request.get("_autolot_volume_rules") or {}).get(str(expected.get("symbol") or "").casefold())
        return {
            "expected": executable_lot(raw_lot, volume_rule), "tester": tester_volume,
            "source": "autolot", "balance": balance, "dd_001": float(rule["dd_001"]),
            "risk_pct": float(rule["risk_pct"]), "raw_lot": raw_lot,
        }
    # Un tramo declarado a mano manda sobre el lote único configurado, pero
    # sólo desde su fecha: antes del cambio la cuenta operaba el de siempre y
    # darle el nuevo marcaría como desviado lo que ejecutó bien.
    scheduled = scheduled_lot(request.get("real_lot_schedule"), strategy, moment)
    configured = scheduled if scheduled is not None else (
        request.get("real_strategy_lots") or {}
    ).get(strategy)
    try:
        expected_volume = float(configured) if configured is not None else tester_volume
    except (TypeError, ValueError):
        expected_volume = tester_volume
    if configured is None:
        source = "tester_lot"
    else:
        source = "real_lot_schedule" if scheduled is not None else "configured_real_lot"
    return {
        "expected": expected_volume if expected_volume > 0 else tester_volume,
        "tester": tester_volume,
        "source": source,
    }


def dynamic_lot_bands(
    tester_trades: list[dict[str, Any]], request: dict[str, Any],
) -> dict[str, tuple[float, float]]:
    bands: dict[str, tuple[float, float]] = {}
    for trade in tester_trades:
        detail = expected_volume(trade, request)
        if detail["source"] != "autolot":
            continue
        symbol = str(trade.get("symbol") or "").casefold()
        values = [float(trade.get("volume") or 0.0), float(detail["expected"])]
        low, high = min(values), max(values)
        previous = bands.get(symbol)
        bands[symbol] = (min(low, previous[0]), max(high, previous[1])) if previous else (low, high)
    return bands
