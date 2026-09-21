"""Emparejar lo que dice un resumen exportado con los candidatos de la memoria.

La mitad de abajo de la importacion: de aqui salen las filas y los avisos sobre
lo que no se pudo identificar. Quien reconstruye el calculo es
`portfolio_import_build`.

Un nombre de .set puede repetirse entre candidatos, asi que el emparejamiento
usa primero el id exportado, luego el contenido del fichero y solo al final el
nombre; lo que sigue siendo ambiguo se declara, no se adivina.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .common import safe_int
from .portfolio_identity import TYPE_LABELS, _resolve_source_path, _stored_path_name

if TYPE_CHECKING:  # solo para el tipo: importarlo de verdad seria un ciclo
    from .portfolio_service import PortfolioSource


def _exported_candidate_ids(portable: list[dict[str, Any]]) -> set[str]:
    """Ids de candidato que el resumen trae de verdad para un nombre de set.

    Los marcadores ``importado-sin-informes:`` no identifican nada: los escribio
    una importacion anterior que tampoco supo resolverlo.
    """
    return {
        str(row.get("candidate_id") or "").strip()
        for row in portable
        if str(row.get("candidate_id") or "").strip()
        and not str(row.get("candidate_id") or "").startswith("importado-sin-informes:")
    }

def _narrow_by_exported_set_content(
    matches: list[dict[str, Any]], header: dict[str, Any], key: str,
) -> list[dict[str, Any]]:
    """Desempata candidatos homonimos por el SHA-256 del .set exportado.

    Para archivos antiguos, sin ``Miembros JSON``, la copia del .set que viajo en
    la exportacion es lo unico que queda para recuperar la identidad. Solo vale
    si hay una unica coincidencia de contenido.
    """
    exported_hashes = {
        str(value).strip().lower()
        for value in ((header.get("_set_sha256_by_name") or {}).get(key) or [])
        if str(value).strip()
    }
    if len(exported_hashes) != 1:
        return matches
    matching_content: list[dict[str, Any]] = []
    for row in matches:
        candidate_path = Path(str(row.get("set_path") or ""))
        try:
            digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in exported_hashes:
            matching_content.append(row)
    return matching_content if len(matching_content) == 1 else matches

def _row_from_exported_member(
    portable: dict[str, Any],
    candidate_id: str,
    name: str,
    source: PortfolioSource,
) -> dict[str, Any]:
    """Reconstruye la fila desde las rutas que conservo la exportacion.

    La fila puede haber desaparecido de la memoria tras un cambio de veredicto.
    Las exportaciones nuevas conservan las rutas exactas que tenia la asignacion;
    se reconstruye desde ellas igual que al mejorar un portafolio guardado, sin
    elegir otro candidato.
    """
    saved = dict(portable)
    saved.update({
        "candidate_id": candidate_id,
        "set_path": _resolve_source_path(
            saved.get("set_path") or saved.get("set_id") or name, source.project,
        ),
        "is_report_path": _resolve_source_path(saved.get("is_report_path"), source.project),
        "oos_report_path": _resolve_source_path(saved.get("oos_report_path"), source.project),
        "full_history_report_path": _resolve_source_path(saved.get("full_history_report_path"), source.project),
        "final_tick_report_path": _resolve_source_path(saved.get("final_tick_report_path"), source.project),
        "target_symbol": saved.get("target_symbol") or saved.get("symbol"),
        "period": saved.get("period") or saved.get("timeframe"),
    })
    return saved

def _matches_for_member(
    key: str,
    name: str,
    by_name: dict[str, list[dict[str, Any]]],
    portable_by_name: dict[str, list[dict[str, Any]]],
    header: dict[str, Any],
    source: PortfolioSource,
) -> list[dict[str, Any]]:
    """Candidatos que corresponden a un miembro del resumen.

    Devuelve cero (irresoluble), uno (resuelto) o varios (ambiguo). Nunca elige
    por nombre: dos candidatos homonimos pueden tener informes y curvas
    distintos, y escoger uno seria silencioso.
    """
    matches = by_name.get(key) or []
    portable = portable_by_name.get(key) or []
    exact_candidate_ids = _exported_candidate_ids(portable)
    if len(matches) > 1 and len(exact_candidate_ids) == 1:
        exact_id = next(iter(exact_candidate_ids))
        exact = [
            row for row in matches
            if str(row.get("candidate_id") or "").strip() == exact_id
        ]
        if len(exact) == 1:
            matches = exact
    if len(matches) > 1:
        matches = _narrow_by_exported_set_content(matches, header, key)
    if not matches and len(exact_candidate_ids) == 1 and portable:
        return [
            _row_from_exported_member(
                portable[0], next(iter(exact_candidate_ids)), name, source,
            )
        ]
    return matches

def _resolve_import_members(
    members: list[Any],
    header: dict[str, Any],
    source: PortfolioSource,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Empareja cada miembro del resumen con su fila de candidato.

    Devuelve las resueltas, las que no tienen candidato y las ambiguas.
    """
    # El ZIP es la autoridad de composicion. No reutilizar ``candidate_rows``:
    # ese inventario pertenece a calculos nuevos y elimina estrategias cuyo
    # veredicto actual ya no supera las cuatro etapas, que fue precisamente lo
    # que convirtio una exportacion real de 7 sets en un portafolio de 4.
    candidates = source.import_candidate_rows({str(member.set_name) for member in members})
    portable_by_name: dict[str, list[dict[str, Any]]] = {}
    for raw in header.get("portfolio_members") or []:
        if not isinstance(raw, dict):
            continue
        name = Path(str(raw.get("set_name") or raw.get("set_path") or raw.get("set_id") or "")).name.casefold()
        if name:
            portable_by_name.setdefault(name, []).append(raw)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_name.setdefault(Path(str(row.get("set_path") or "")).name.casefold(), []).append(row)

    resolved: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    ambiguous: list[str] = []
    for member in members:
        name = str(member.set_name)
        key = name.casefold()
        if key in resolved or key in {value.casefold() for value in unresolved + ambiguous}:
            continue
        matches = _matches_for_member(key, name, by_name, portable_by_name, header, source)
        if not matches:
            unresolved.append(name)
        elif len(matches) > 1:
            ambiguous.append(name)
        else:
            resolved[key] = matches[0]
    return resolved, unresolved, ambiguous

def _changed_verdict_notes(rows: list[dict[str, Any]]) -> list[str]:
    """Veredictos actuales que ya no son 'accepted', para avisar sin recortar."""
    changed_verdicts: list[str] = []
    for row in rows:
        robustness_status = str(row.get("robustness_status") or "")
        if row.get("historical_robustness_report_recovered"):
            robustness_status = "sin fila vigente; informe histórico recuperado"
        statuses = {
            "base": str(row.get("base_status") or ""),
            "robustez": robustness_status,
            "Final Tick": str(row.get("final_tick_status") or ""),
            "Final Tick 6M": str(row.get("final_tick_6m_status") or ""),
        }
        changed = [f"{stage}={status or 'sin evaluar'}" for stage, status in statuses.items() if status != "accepted"]
        if changed:
            changed_verdicts.append(
                f"{Path(str(row.get('set_path') or '')).name}: " + ", ".join(changed)
            )
    return changed_verdicts

def _import_resolution_warnings(
    unresolved: list[str], ambiguous: list[str], unmeasured: list[str],
) -> list[str]:
    """Lo que no se pudo medir, dicho sin inventar metricas."""
    notes: list[str] = []
    if unresolved:
        notes.append(
            "Sin candidato actual ni informes localizables; se conservaron sin métricas: "
            + ", ".join(unresolved)
        )
    if ambiguous:
        notes.append(
            "Nombre con varios candidatos posibles; se conservó sin elegir métricas al azar: "
            + ", ".join(ambiguous)
        )
    if unmeasured:
        notes.append(
            "Cálculo incompleto al importar: se conservaron composición, unidades y lotes, "
            "pero beneficio y drawdown no incluyen las estrategias sin informes: "
            + ", ".join(unmeasured)
        )
    return notes

def _imported_variant_key(header: dict[str, Any]) -> str:
    """Modo tomado de la cabecera cuando la columna PERFIL viene en blanco.

    ``save_proposal`` guarda un portafolio de una sola variante -una mejora, o
    cualquier mensual- con ``variant_key`` vacio: la variante es la fila entera,
    no una de tres. Sin este respaldo la variante caia en ``variant_1``, que no
    es ningun modo conocido, y el guardado perdia el tipo.
    """
    return next(
        (
            value for value in (
                str(header.get("improvement_portfolio_type") or "").strip().lower(),
                str(header.get("portfolio_type") or "").strip().lower(),
            )
            if value in TYPE_LABELS
        ),
        "",
    )

def _imported_target_month(header: dict[str, Any]) -> int | None:
    """El mes objetivo no es un campo del resumen: viaja en el nombre.

    `save_proposal` compone «Moderado | Mes 08 | …» para el mensual, así que el
    nombre exportado lo lleva. Sin él, un mensual importado se evaluaria sobre la
    curva completa y sus números no serían los del mes guardado.
    """
    match = re.search(r"\bMes\s+(\d{1,2})\b", str(header.get("name") or ""), re.IGNORECASE)
    if not match:
        return None
    month = int(match.group(1))
    return month if 1 <= month <= 12 else None
