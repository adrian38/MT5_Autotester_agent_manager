"""Elige el motor de mejora UBS normal según la genealogía del portafolio destino.

Hay dos motores y son ficheros distintos a propósito:

- `portfolio_improvement_service.py` — mejora sobre una **base** (profundidad 1).
  Funciona y no se toca.
- `portfolio_improvement_chain_service.py` — mejora sobre una **mejora**
  (profundidad >= 2), donde la puerta de aporte 6M necesita control propio y
  reintento. Ver su docstring.

La decisión se toma aquí, en un único sitio, y nunca por el nombre del
portafolio: el #19 de ICTrading se guardó con nombre genérico A/M/C desde un nodo
con el código anterior, pero sus metadatos sí conservaban origen y modo. Se mira
la genealogía persistida, igual que `_lineage_from_parent`.

El mensual no pasa por aquí; sigue congelado con su propia orquestación.
"""

from __future__ import annotations

from typing import Any, Callable

from .portfolio_service import PortfolioSource


Progress = Callable[[str], None]


def is_chain_improvement(detail: dict[str, Any]) -> bool:
    """True si el portafolio destino ya es, él mismo, una mejora guardada.

    Se acepta cualquiera de las tres huellas que deja la persistencia, porque
    las carteras anteriores a cada cambio conservan sólo algunas: los inputs
    guardados, la auditoría de mejora y el resumen `improvement_origin` que
    calcula el listado. Basta una.
    """
    if not isinstance(detail, dict):
        return False
    metrics = detail.get("metrics") if isinstance(detail.get("metrics"), dict) else {}
    saved = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
    audit = (metrics.get("seasonal_validation") or {}).get("portfolio_improvement") or {}
    origin = detail.get("improvement_origin") if isinstance(detail.get("improvement_origin"), dict) else {}
    for value in (
        saved.get("improvement_source_portfolio_id"),
        audit.get("source_portfolio_id"),
        origin.get("source_id"),
    ):
        try:
            if int(value or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    for value in (saved.get("improvement_depth"), audit.get("depth")):
        try:
            if int(value or 0) >= 1:
                return True
        except (TypeError, ValueError):
            continue
    return False


def run_full_history_improvement(
    source: PortfolioSource,
    portfolio_id: int,
    inputs: dict[str, Any],
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Despacha al motor de base o al de cadena sin cambiar la firma del trabajo."""
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
    if is_chain_improvement(detail):
        from .portfolio_improvement_chain_service import (
            generate_full_history_chain_improvement,
        )

        if progress:
            progress(
                "Motor de mejora en cadena: el portafolio de partida ya es una mejora"
            )
        chain_inputs = dict(inputs)
        chain_inputs["improvement_disabled_symbols"] = inputs.get(
            "chain_improvement_disabled_symbols",
            inputs.get("improvement_disabled_symbols", inputs.get("disabled_symbols", [])),
        )
        return generate_full_history_chain_improvement(
            source, portfolio_id, chain_inputs, progress,
        )
    from .portfolio_improvement_service import generate_full_history_improvement

    return generate_full_history_improvement(source, portfolio_id, inputs, progress)
