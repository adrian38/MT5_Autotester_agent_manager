"""Las dos mitades del trasiego de un portafolio: leer memorias e exportar.

Por debajo solo tiene `portfolio_schema` (sondeos de tabla y columna) y
`portfolio_identity` (rutas, etiquetas y linaje). No conoce `PortfolioSource`:
recibe el proyecto y la cuenta como argumentos.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import dev_branch
from .common import safe_int
from .portfolio_identity import (
    IMPROVEMENT_PRIORITY_LABELS,
    TYPE_LABELS,
    _normalized_improvement_lineage,
    _portable_portfolio_uid,
    _resolve_source_path,
    _valid_portfolio_uid,
    normalize_portfolio_alias,
)
from .portfolio_schema import _has_column, _table_exists
from .stage_reports import recover_robustness_report


def _sql_when(present: bool, expression: str, absent: str = "") -> str:
    """La expresion si esa etapa existe en la memoria; si no, lo que la sustituye."""
    return expression if present else absent

def _import_candidate_sql(
    conn: sqlite3.Connection, *, include_without_robustness: bool
) -> str | None:
    """El select de importacion, adaptado a las etapas que tenga esa memoria.

    Devuelve ``None`` cuando la memoria no sirve como origen: sin ``candidates``
    no hay nada que leer, y sin ``candidate_robustness`` solo la ventana de
    familia acepta seguir.
    """
    if not _table_exists(conn, "candidates"):
        return None
    robust = _table_exists(conn, "candidate_robustness")
    if not robust and not include_without_robustness:
        return None
    tick = _table_exists(conn, "candidate_final_tick")
    tick6m = _table_exists(conn, "candidate_final_tick_6m")
    robustness_join = _sql_when(robust, "left join candidate_robustness cr on cr.candidate_id=c.id")
    final_tick_join = _sql_when(tick, "left join candidate_final_tick ft on ft.candidate_id=c.id")
    final_tick_6m_join = _sql_when(tick6m, "left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id")
    oos_report_sql = _sql_when(robust, "cr.report_path", "null")
    robustness_status_sql = _sql_when(robust, "cr.status", "null")
    full_history_sql = _sql_when(tick, "ft.real_tick_report_path", "null")
    final_tick_status_sql = _sql_when(tick, "ft.status", "null")
    final_ohlc_sql = _sql_when(tick6m, "ft6.ohlc_report_path", "null")
    final_real_sql = _sql_when(tick6m, "ft6.real_tick_report_path", "null")
    final_from_sql = _sql_when(tick6m, "ft6.from_date", "null")
    final_to_sql = _sql_when(tick6m, "ft6.to_date", "null")
    final_tick_6m_status_sql = _sql_when(tick6m, "ft6.status", "null")
    final_tick_metrics_sql = _sql_when(
        tick6m and _has_column(conn, "candidate_final_tick_6m", "real_tick_metrics_json"),
        "ft6.real_tick_metrics_json",
        "null",
    )
    return f"""
        select ? as account_type, ? || ':' || c.id as candidate_id,
               c.id as source_candidate_id, c.set_path, c.symbol, c.target_symbol,
               c.period, c.family, c.report_path as is_report_path,
               {oos_report_sql} as oos_report_path,
               {full_history_sql} as full_history_report_path,
               {final_ohlc_sql} as final_ohlc_report_path,
               {final_real_sql} as final_tick_report_path,
               {final_from_sql} as final_tick_from_date,
               {final_to_sql} as final_tick_to_date,
               {final_tick_metrics_sql} as final_tick_metrics_json,
               c.status as base_status, {robustness_status_sql} as robustness_status,
               {final_tick_status_sql} as final_tick_status,
               {final_tick_6m_status_sql} as final_tick_6m_status
        from candidates c
        {robustness_join}
        {final_tick_join}
        {final_tick_6m_join}
        order by c.id
        """

def _imported_candidate(item: dict[str, Any], project: Path, memory: Path) -> dict[str, Any]:
    """Completa una fila importada: informe historico, simbolo ejecutable y rutas."""
    if not str(item.get("oos_report_path") or "").strip():
        historical_report = recover_robustness_report(
            project, item.get("source_candidate_id"), item.get("set_path")
        )
        if historical_report:
            item["oos_report_path"] = historical_report
            item["historical_robustness_report_recovered"] = True
    final_tick_metrics = item.pop("final_tick_metrics_json", None)
    if final_tick_metrics:
        try:
            executable_symbol = str(
                (json.loads(final_tick_metrics) or {}).get("symbol") or ""
            ).strip()
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            executable_symbol = ""
        if executable_symbol:
            item["executable_symbol"] = executable_symbol
    item["source_memory_path"] = str(memory)
    for key in (
        "set_path", "is_report_path", "oos_report_path", "full_history_report_path",
        "final_ohlc_report_path", "final_tick_report_path",
    ):
        item[key] = _resolve_source_path(item.get(key), project)
    return item

def _export_folder(detail: dict[str, Any], portfolio_id: int, destination: str | None, project: Path) -> Path:
    """La carpeta de destino, ya creada y ya autorizada."""
    root = Path(destination).expanduser() if destination else project / "exports"
    created = str(detail.get("created_at") or "").replace("T", "_").replace(":", "").replace("-", "")
    portfolio_type = str(detail.get("portfolio_type") or "").lower()
    label = (
        "GRID_A_M_C"
        if portfolio_type == "grid_bundle"
        else "A_M_C"
        if portfolio_type == "bundle"
        else str(detail.get("portfolio_type") or "Portfolio")
    )
    folder_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"PORTAFOLIO_{portfolio_id}_{label}_{created[:15]}").strip("._")
    output = root.resolve() / (folder_name or f"PORTAFOLIO_{portfolio_id}")
    # La carpeta de destino la elige el usuario y no es dato de un agente:
    # solo se acota cuando cae dentro del proyecto del propio agente, que es
    # donde va el destino por defecto.
    dev_branch.assert_export_destination(output, project)
    output.mkdir(parents=True, exist_ok=True)
    return output

def _copy_exported_sets(
    members: list[dict[str, Any]], output: Path, detail: dict[str, Any], project: Path, account: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Copia cada .set original y devuelve (tabla, miembros portables, omitidos)."""
    copied: set[str] = set()
    exported: list[dict[str, Any]] = []
    exported_members: list[dict[str, Any]] = []
    missing: list[str] = []
    for member in members:
        source_path = Path(_resolve_source_path(member.get("set_path") or member.get("set_id"), project))
        if not source_path.is_file():
            missing.append(source_path.name)
            continue
        key = str(source_path.resolve()).casefold()
        if key not in copied:
            shutil.copy2(source_path, output / source_path.name)
            copied.add(key)
        exported.append({
            # Un portafolio de una sola variante (una mejora, un mensual) se
            # guarda con `variant_key` y `variant_label` vacios: la variante
            # es la fila entera. Sin este respaldo la columna PERFIL sale en
            # blanco y el resumen deja de decir en que modo se guardo.
            "variant": (
                member.get("variant_label") or member.get("variant_key")
                or TYPE_LABELS.get(str(detail.get("portfolio_type") or ""), "")
            ),
            "account": str(member.get("candidate_id") or "").split(":", 1)[0] or account,
            "symbol": member.get("symbol") or "", "timeframe": member.get("timeframe") or "",
            "units": int(member.get("units") or 0), "lot": float(member.get("lot") or 0), "set": source_path.name,
        })
        portable_member = dict(member)
        portable_member["set_id"] = str(member.get("set_id") or source_path)
        portable_member["set_path"] = str(source_path)
        portable_member["set_name"] = source_path.name
        exported_members.append(portable_member)
    return exported, exported_members, missing

def _improvement_header_lines(detail: dict[str, Any], scope: str) -> list[str]:
    """La cabecera de linaje de una mejora, o nada si el portafolio no lo es."""
    origin = detail.get("improvement_origin") or {}
    source_id = safe_int(origin.get("source_id"), 0)
    mode = str(origin.get("mode") or "")
    if scope != "full_history" or source_id <= 0 or mode not in TYPE_LABELS:
        return []
    lines = [
        f"Mejora etiqueta: {str(origin.get('label') or detail.get('name') or '')}",
        f"Mejora origen: {source_id}",
        f"Mejora modo: {mode}",
    ]
    source_uid = _valid_portfolio_uid(origin.get("source_uid"))
    if source_uid:
        lines.append(f"Mejora origen UID: {source_uid}")
    lines.extend([
        f"Mejora raiz: {safe_int(origin.get('root_id'), source_id)}",
        f"Mejora nivel: {max(1, safe_int(origin.get('depth'), 1))}",
    ])
    root_uid = _valid_portfolio_uid(origin.get("root_uid"))
    if root_uid:
        lines.append(f"Mejora raiz UID: {root_uid}")
    lineage = _normalized_improvement_lineage(origin.get("lineage"))
    if lineage:
        lines.append(
            "Mejora linaje JSON: " + json.dumps(lineage, ensure_ascii=True, separators=(",", ":"))
        )
    audit = ((detail.get("metrics") or {}).get("seasonal_validation") or {}).get("portfolio_improvement") or {}
    snapshot = audit.get("source_snapshot")
    if isinstance(snapshot, dict):
        lines.append(
            "Mejora snapshot JSON: " + json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"))
        )
    priority = str(origin.get("priority") or "")
    if priority in IMPROVEMENT_PRIORITY_LABELS:
        lines.append(f"Mejora prioridad: {priority}")
    if origin.get("added_count") is not None:
        lines.append(f"Mejora incorporaciones: {safe_int(origin.get('added_count'), 0)}")
    return lines

def _export_summary_lines(
    detail: dict[str, Any], portfolio_id: int, scope: str,
    exported: list[dict[str, Any]], exported_members: list[dict[str, Any]], missing: list[str],
) -> list[str]:
    """El resumen .txt. Todo metadato se inserta en la 3a linea, en orden inverso.

    ``parse_summary`` deja de interpretar metadatos en cuanto empieza la tabla de
    sets, asi que la cabecera tiene que quedar completa por delante.
    """
    lines = [
        f"Portafolio: {detail.get('name') or portfolio_id}",
        f"Tipo: {detail.get('portfolio_type') or ''}   Capital: {float(detail.get('capital') or 0):,.0f}",
        f"DD valle objetivo: {float(detail.get('target_valley_dd') or 0):,.2f}",
        f"DD puntual objetivo: {float(detail.get('target_point_dd') or 0):,.2f}",
        f"DD valle usado: {float(detail.get('actual_valley_dd') or 0):,.2f}",
        f"DD puntual usado: {float(detail.get('actual_point_dd') or 0):,.2f}",
        f"Net profit total 2020-2026: {float(detail.get('total_net_profit') or 0):,.2f}", "",
        "Sets exportados: copia exacta del .set original probado.",
        "No se modifica Risk, LotPerBalance_step, grid ni ningún otro parámetro del EA.",
        "UNID. y LOTE son la asignación informativa calculada por el portafolio.", "",
        f"{'PERFIL':12s} {'CUENTA':12s} {'SIMBOLO':12s} {'TF':5s} {'UNID.':>7s} {'LOTE':>7s}   SET",
    ]
    if scope == "full_history":
        lines[2:2] = [f"Portafolio UID: {_portable_portfolio_uid(detail)}"]
        alias = normalize_portfolio_alias(detail.get("alias"))
        if alias:
            lines[2:2] = [f"Alias: {alias}"]
    lines[2:2] = _improvement_header_lines(detail, scope)
    # La tabla histórica trunca CUENTA y solo conserva el nombre del set. Eso no
    # basta cuando la memoria contiene varios candidatos con el mismo nombre: se
    # perdería el id que identifica qué informe de robustez usar. La tabla sigue
    # siendo legible y compatible; esta cabecera da a las importaciones nuevas la
    # identidad exacta de cada miembro.
    lines[2:2] = [
        "Miembros JSON: " + json.dumps(exported_members, ensure_ascii=True, separators=(",", ":"))
    ]
    for item in exported:
        lines.append(f"{str(item['variant'])[:12]:12s} {str(item['account'])[:12]:12s} {str(item['symbol']):12s} {str(item['timeframe']):5s} {item['units']:7d} {item['lot']:7.2f}   {item['set']}")
    if missing:
        lines.extend(("", "OMITIDOS (set no encontrado): " + ", ".join(missing)))
    return lines
