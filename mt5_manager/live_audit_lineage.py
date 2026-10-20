"""Genealogia que puede prestar historico a una auditoria nueva.

Una coincidencia de estrategia no basta: dos portafolios independientes pueden
usar el mismo candidato. Solo los padres registrados por una mejora o un
arreglo pueden prestar datos, y solo para los miembros que siguen presentes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .common import safe_int
from .live_audit_archive import composite
from .live_audit_core import _member_set_name, _member_strategy_id
from .live_audit_schedule import parse_timestamp


@dataclass(frozen=True)
class AuditLineage:
    portfolio_id: int
    ancestors: tuple[int, ...]
    strategies: tuple[str, ...]
    created_at: datetime | None = None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _lineage_ids(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    return {
        safe_int(row.get("portfolio_id") or row.get("id"), 0, minimum=0)
        for row in value if isinstance(row, dict)
    } - {0}


def ancestor_portfolio_ids(detail: dict[str, Any]) -> tuple[int, ...]:
    """IDs padre portables guardados por mejora, cadena o reparacion."""
    metrics = _mapping(detail.get("metrics"))
    inputs = _mapping(metrics.get("inputs"))
    seasonal = _mapping(metrics.get("seasonal_validation"))
    repair = _mapping(seasonal.get("portfolio_repair"))
    improvement = _mapping(seasonal.get("portfolio_improvement"))
    origin = _mapping(detail.get("improvement_origin"))
    sources = (inputs, repair, improvement, origin)
    found: set[int] = set()
    for source in sources:
        for key in (
            "repair_source_portfolio_id", "improvement_source_portfolio_id",
            "source_portfolio_id", "source_id", "improvement_root_portfolio_id",
            "root_portfolio_id", "root_id",
        ):
            value = safe_int(source.get(key), 0, minimum=0)
            if value:
                found.add(value)
        found.update(_lineage_ids(source.get("improvement_lineage") or source.get("lineage")))
    return tuple(sorted(found))


def member_strategies(detail: dict[str, Any], portfolio_type: str) -> tuple[str, ...]:
    """Identidades exactas de los miembros que conserva la variante auditada."""
    members = [row for row in detail.get("members") or [] if isinstance(row, dict)]
    variants = {str(row.get("variant_key") or "") for row in members if row.get("variant_key")}
    if variants:
        members = [row for row in members if str(row.get("variant_key") or "") == portfolio_type]
    # Las mismas dos primitivas que arman la comparacion
    # (`strategy_set_names` de `live_audit_lifecycle`): si aqui se dedujera el
    # nombre de otra forma, la identidad no casaria con la del archivo y la
    # herencia se caeria en silencio.
    found: dict[str, None] = {}
    for member in members:
        strategy = _member_strategy_id(member)
        set_name = _member_set_name(member)
        if strategy and set_name:
            found[composite(strategy, set_name)] = None
    return tuple(found)


def audit_profile(server: Any, node_id: str, audit_key: str) -> dict[str, Any]:
    """El perfil guardado del uso; vacio si el manager todavia no lo conoce."""
    settings = getattr(server, "live_audit_settings", None)
    if settings is None:
        return {}
    try:
        profiles = settings.state(node_id).get("profiles") or {}
    except (OSError, TypeError, ValueError):
        return {}
    profile = profiles.get(audit_key)
    return dict(profile) if isinstance(profile, dict) else {}


def audit_lineage(server: Any, node_id: str, profile: dict[str, Any]) -> AuditLineage:
    """Contexto guardado del portafolio; si no puede leerse, no inventa padres."""
    portfolio_id = safe_int(profile.get("portfolio_id"), 0, minimum=0)
    empty = AuditLineage(portfolio_id, (), ())
    portfolios = getattr(server, "portfolios", None)
    if not portfolio_id or portfolios is None:
        return empty
    try:
        response = portfolios.saved(node_id, "full_history", portfolio_id)
        detail = _mapping(response.get("portfolio"))
    except (KeyError, OSError, TypeError, ValueError):
        return empty
    ancestors = tuple(value for value in ancestor_portfolio_ids(detail) if value != portfolio_id)
    strategies = member_strategies(detail, str(profile.get("portfolio_type") or ""))
    return AuditLineage(
        portfolio_id, ancestors, strategies, parse_timestamp(detail.get("created_at")),
    )


def inherited_period_end(
    server: Any, node_id: str, profile: dict[str, Any],
    context: AuditLineage | None = None,
) -> datetime | None:
    """El corte mas antiguo que los padres dejaron pendiente.

    Se mira estrategia por estrategia y se devuelve el mas viejo: si una quedo
    auditada hasta el 10 y otra hasta el 3, arrancar el 10 dejaria una semana
    de la segunda sin auditar nunca. Las estrategias nuevas no aportan nada:
    su historico empieza con el uso, y mientras alguna hermana herede, la
    ventana ya las cubre. Sin nada que heredar manda el periodo configurado.
    """
    context = context or audit_lineage(server, node_id, profile)
    archive = getattr(server, "live_audit_archive", None)
    if archive is None or not context.ancestors or not context.strategies:
        return None
    entries = archive.related(
        node_id, "", list(context.strategies), context.ancestors,
    )
    ends: list[datetime] = []
    for strategy in context.strategies:
        inherited = [
            parse_timestamp(entry.get("period_end")) for entry in entries
            if strategy in set(entry.get("strategies") or [])
        ]
        valid = [value for value in inherited if value is not None]
        if valid:
            ends.append(max(valid))
    return min(ends) if ends else None


def audit_period_bounds(
    server: Any, node_id: str, profile: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    """El corte heredado y el dia en que nacio el portafolio, de una lectura.

    Los dos acotan por abajo la ventana que se le pide al nodo, y tiran en
    sentidos opuestos: la creacion es el suelo -antes de existir el portafolio
    no tiene operaciones reales con las que comparar al tester- y la herencia
    es el unico permiso para bajar de el, porque una mejora o un arreglo si
    puede rescatar el historico de las estrategias que conserva del padre.
    """
    context = audit_lineage(server, node_id, profile)
    return inherited_period_end(server, node_id, profile, context), context.created_at


def ancestors_of(server: Any, node_id: str, audit_key: str) -> tuple[int, ...]:
    """Padres portables del portafolio que audita ese uso, o vacio."""
    return audit_lineage(server, node_id, audit_profile(server, node_id, audit_key)).ancestors


__all__ = [
    "AuditLineage", "ancestor_portfolio_ids", "ancestors_of", "audit_lineage",
    "audit_period_bounds", "audit_profile", "inherited_period_end",
    "member_strategies",
]
