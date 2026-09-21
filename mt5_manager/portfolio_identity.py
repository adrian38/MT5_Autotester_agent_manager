"""Identidad y rutas de un portafolio: lo que no depende de nada mas.

Es el fondo de la pila de `portfolio_service`: solo importa `common` y el tipo
de portafolio. Aqui viven el UID portable —que sobrevive a la exportacion
cuando el id numerico local cambia—, el linaje de las mejoras, el alias y la
traduccion de rutas escritas por un nodo Windows.

Los llamantes lo siguen viendo reexportado desde `portfolio_service`.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from portfolio_manager.ubs_portfolio import PortfolioType

from .common import safe_int


PORTFOLIO_TYPES = {
    "aggressive": PortfolioType.AGGRESSIVE,
    "balanced": PortfolioType.BALANCED,
    "conservative": PortfolioType.CONSERVATIVE,
}

TYPE_LABELS = {"aggressive": "Agresivo", "balanced": "Moderado", "conservative": "Conservador"}

# Etiquetas de la prioridad de seleccion de la mejora. Viven aqui, y no en
# `portfolio_improvement_service`, porque las necesita tambien el listado de
# portafolios guardados; ese modulo importa de este, no al reves. Un solo sitio
# para el nombre visible: el desplegable, la auditoria y la lista no pueden
# llamar distinto a lo mismo.
IMPROVEMENT_PRIORITY_LABELS = {
    "balanced": "Equilibrada",
    "efficiency": "Máxima eficiencia",
    "stress": "Menor estrés",
}

def _valid_portfolio_uid(value: Any) -> str:
    """Return a canonical portable UUID or an empty string for legacy data."""
    try:
        return str(uuid.UUID(str(value or "")))
    except (ValueError, TypeError, AttributeError):
        return ""

def _normalized_improvement_lineage(value: Any) -> list[dict[str, Any]]:
    """Keep the portable, display-safe subset of an improvement ancestry."""
    if not isinstance(value, list):
        return []
    lineage: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for raw in value[:32]:
        if not isinstance(raw, dict):
            continue
        portfolio_id = safe_int(raw.get("portfolio_id") or raw.get("id"), 0)
        portfolio_uid = _valid_portfolio_uid(raw.get("portfolio_uid") or raw.get("uid"))
        if portfolio_id <= 0 and not portfolio_uid:
            continue
        marker = (portfolio_id, portfolio_uid)
        if marker in seen:
            continue
        seen.add(marker)
        row: dict[str, Any] = {"portfolio_id": portfolio_id}
        if portfolio_uid:
            row["portfolio_uid"] = portfolio_uid
        label = str(raw.get("label") or "").strip()
        if label:
            row["label"] = label[:240]
        mode = str(raw.get("mode") or "").strip().lower()
        if mode in TYPE_LABELS:
            row["mode"] = mode
        lineage.append(row)
    return lineage

def _portable_portfolio_uid(detail: dict[str, Any]) -> str:
    """Identify a portfolio across exports even when its local numeric id changes."""
    metrics = detail.get("metrics") if isinstance(detail.get("metrics"), dict) else {}
    inputs = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
    audit = (metrics.get("seasonal_validation") or {}).get("portfolio_improvement") or {}
    stored = _valid_portfolio_uid(inputs.get("portfolio_uid") or audit.get("portfolio_uid"))
    if stored:
        return stored
    members = sorted(
        (
            Path(str(member.get("set_path") or member.get("set_id") or "")).name.casefold(),
            str(member.get("variant_key") or ""),
            safe_int(member.get("units"), 0),
        )
        for member in detail.get("members") or []
    )
    identity = json.dumps(
        {
            "legacy_id": safe_int(detail.get("id"), 0),
            "created_at": str(detail.get("created_at") or ""),
            "name": str(detail.get("name") or ""),
            "portfolio_type": str(detail.get("portfolio_type") or ""),
            "members": members,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mt5-ubs-portfolio:{identity}"))

LOCKED_VARIANTS = (
    ("aggressive", "Agresivo", PortfolioType.AGGRESSIVE),
    ("balanced", "Moderado", PortfolioType.BALANCED),
    ("conservative", "Conservador", PortfolioType.CONSERVATIVE),
)

def normalize_portfolio_alias(value: Any) -> str:
    """Normalize the optional human label without changing portfolio identity."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("El alias del portafolio debe ser texto")
    alias = " ".join(value.split())
    if len(alias) > 80:
        raise ValueError("El alias del portafolio no puede superar 80 caracteres")
    return alias

def _is_bundle_portfolio(detail: dict[str, Any]) -> bool:
    # A bundle (A/M/C) is deleted whole when a member is excluded, unlike a
    # single-objective portfolio which is recalculated. Detection must not
    # depend on the scope: monthly bundles exist too and the frontend already
    # renders the bundle controls for them regardless of scope, so gating this
    # on scope=='full_history' left monthly bundles unable to be deleted.
    return (
        str(detail.get("portfolio_type") or "").lower() == "bundle"
        or bool((detail.get("metrics") or {}).get("portfolio_bundle"))
    )

def _stored_path_name(value: Any) -> str:
    """Nombre de fichero de una ruta guardada, venga del SO que venga.

    Las rutas de la memoria las escribió un nodo Windows, así que en el manager
    Linux `Path(...).name` devolvería la ruta entera: aquí hay que cortar por
    los dos separadores antes de comparar nombres, igual que hace
    `_resolve_source_path` antes de reubicar.
    """
    return str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1].casefold()

def _resolve_source_path(value: Any, project: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # SQLite rows are produced by Windows nodes. Convert their separators so
    # a Linux manager container can identify known project-relative roots
    # (outputs/sets/reports/...) instead of appending a literal C:\\... name.
    path = Path(text.replace("\\", "/")).expanduser()
    parts = path.parts
    lowered = [part.lower() for part in parts]
    for root in ("outputs", "sets", "reports", "configs", "assets"):
        if root in lowered:
            candidate = project.joinpath(*parts[lowered.index(root):])
            # A DB produced on another PC stores that PC's drive letter. Once
            # a known project root is found, relocate it deterministically to
            # the manager's project. This MUST run before resolving an existing
            # path: on a mapped network drive (X:\) Path.resolve() rewrites the
            # drive letter to its UNC target (\\host\share\...), so the same set
            # resolved once as a raw node path (relocated, keeps X:\) and again
            # as its already-relocated path (exists -> resolve -> UNC) produced
            # two different strings. That non-idempotence broke quarantine
            # matching: excluded strategies reappeared on every generation
            # because the UNC-form quarantine path never equalled the
            # drive-letter candidate path. Relocating first keeps the mapping
            # idempotent and also avoids per-path SMB checks that make inventory
            # refreshes needlessly slow.
            return str(candidate.absolute())
    if path.exists():
        return str(path.resolve())
    if not path.is_absolute():
        candidate = project / path
        return str(candidate.absolute())
    return str(path)
