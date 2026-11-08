"""Lo que el pool de portafolios activos ofrece, para elegir qué se queda fuera.

La casilla `Solo estrategias de portafolios activos` convierte el pool en las
estrategias que el usuario opera en real. Qué estrategias son exactamente no se
puede saber desde el formulario: salen de los usos marcados con
`live_trading`, de sus miembros y, en un paquete A/M/C, sólo de la variante
activa. Esta lista es esa resolución, la misma que hace el trabajo al arrancar,
devuelta para que la pantalla la enseñe antes de calcular.

Quitar una de aquí no la excluye del agente ni la pone en cuarentena: sólo la
deja fuera de **este** pool, y se guarda en `active_portfolios_excluded` con el
nombre del `.set`, que es la identidad con la que compara el filtro.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .portfolio_generation import _set_identity
from .portfolio_scope import normalize_portfolio_scope


def _strategy_origin(member: dict[str, Any]) -> dict[str, Any]:
    """De qué uso en real viene esta fila."""
    return {
        "portfolio_id": member.get("_active_portfolio_id"),
        "portfolio_name": str(member.get("_active_portfolio_name") or ""),
        "variant_key": str(member.get("variant_key") or ""),
        "variant_label": str(member.get("variant_label") or ""),
    }


def _strategy_evidence(member: dict[str, Any]) -> str:
    """Con qué se va a evaluar: los informes del embudo o la pasada OHLC."""
    if member.get("is_report_path") and member.get("oos_report_path"):
        return "reports"
    if member.get("single_pass_report_path"):
        return "single_pass"
    return "none"


def active_strategy_rows(
    members: list[dict[str, Any]], excluded: Any = None,
) -> list[dict[str, Any]]:
    """Una fila por estrategia distinta, con todos sus usos en real dentro.

    Se agrupa por nombre del `.set` porque es lo que hace el filtro: la misma
    estrategia puede estar en varios usos —medido el 2026-11-08 sobre AXI, 64
    filas son 57 estrategias— y enseñarla repetida invitaría a quitarla de un
    sitio y dejarla en otro, que no es lo que ocurre.
    """
    out_of_pool = {
        _set_identity(value) for value in excluded or [] if str(value or "").strip()
    }
    rows: dict[str, dict[str, Any]] = {}
    for member in members:
        reference = str(member.get("set_path") or member.get("set_id") or "")
        identity = _set_identity(reference)
        if not identity:
            continue
        row = rows.get(identity)
        if row is None:
            row = {
                "set_name": Path(reference.replace("\\", "/")).name,
                "set_path": reference,
                "symbol": str(member.get("symbol") or ""),
                "timeframe": str(member.get("timeframe") or ""),
                "evidence": _strategy_evidence(member),
                "quarantined": bool(member.get("excluded")),
                "excluded": identity in out_of_pool,
                "origins": [],
            }
            rows[identity] = row
        row["origins"].append(_strategy_origin(member))
    return sorted(rows.values(), key=lambda row: (row["symbol"], row["set_name"]))


class PortfolioCoordinatorActivePoolMixin:
    def active_portfolio_strategies(self, node_id: str, scope: str) -> dict[str, Any]:
        """Las estrategias que el pool ofrecería hoy con la casilla puesta.

        Se resuelve en cada apertura, igual que al arrancar un cálculo: marcar
        o desmarcar un uso en real cambia la lista, y un formulario guardado no
        puede congelarla.
        """
        scope = normalize_portfolio_scope(scope)
        if scope != "full_history":
            raise ValueError("El pool de portafolios activos solo existe en Portafolio UBS")
        source = self._calculation_source(node_id, scope)
        settings = self.settings_for(node_id, scope)
        resolved = self._with_active_portfolio_members(
            node_id, source, {**settings, "active_portfolios_only": True},
        )
        return {
            "strategies": active_strategy_rows(
                resolved.get("_active_portfolio_members") or [],
                settings.get("active_portfolios_excluded"),
            ),
        }
