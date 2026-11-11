"""La regla antirrelleno 6M: nadie ocupa sitio sin aportar beneficio reciente.

Vive aparte porque la usan el calculo normal, el mensual y las dos mejoras, y
porque su bucle es delicado: reabrir el pool entero en cada vuelta es el bucle
de doce horas de `ai_context/ubs_generation_repeated_tournaments.md`.
"""
from __future__ import annotations

from typing import Any, Callable

from portfolio_manager.ubs_portfolio import PortfolioResult


# Pasadas de reposicion de la ruta estandar. Cada una vuelve a seleccionar sobre
# el pool completo menos los rellenos ya descartados, asi que cuesta una
# optimizacion entera: el presupuesto acota el peor caso a minutos. Sin cota
# vuelve el bucle de `ai_context/ubs_generation_repeated_tournaments.md`.
STANDARD_ANTIFILLER_REFILL_PASSES = 8

def _underrepresented_recent_allocation_ids(
    result: PortfolioResult,
    minimum_pct: float,
) -> set[str]:
    """Return active sets whose final lot does not contribute enough recent profit."""
    threshold = max(float(minimum_pct), 0.0) / 100.0
    if threshold <= 0 or not result.allocations:
        return set()
    contributions = {
        allocation.set_id: (
            max(float(allocation.recent_net_profit_001), 0.0) * int(allocation.units)
            if allocation.has_recent_performance
            else 0.0
        )
        for allocation in result.allocations
        if allocation.units > 0
    }
    total = sum(contributions.values())
    if total <= 0:
        return set()
    return {
        set_id
        for set_id, contribution in contributions.items()
        if contribution + 1e-9 < total * threshold
    }

def _antifiller_summary(minimum_pct: float, removed: set[str], refilled: bool) -> str:
    return (
        "Regla antirrelleno 6M: aporte minimo "
        f"{float(minimum_pct):.1f}% por estrategia; "
        f"{len(removed)} estrategia(s) eliminada(s) y portafolio "
        + ("reoptimizado reponiendo desde el pool." if refilled else "reoptimizado.")
    )

def _best_recent_contributor(result: PortfolioResult) -> str:
    """El set activo que mas aporta al beneficio reciente.

    Cuando la cuota deja fuera a TODA la composicion hay que conservar uno, o
    no queda nada que reoptimizar.
    """
    contribution_by_id = {
        allocation.set_id: (
            max(float(allocation.recent_net_profit_001), 0.0) * int(allocation.units)
            if allocation.has_recent_performance
            else 0.0
        )
        for allocation in result.allocations
        if allocation.units > 0
    }
    return max(contribution_by_id, key=contribution_by_id.get)

def _antifiller_pool(
    pool: list[Any],
    active_ids: set[str],
    removed: set[str],
    underrepresented: set[str],
    minimum_pct: float,
    *,
    refill: bool,
) -> tuple[list[Any], str]:
    """El pool de la siguiente vuelta, y como contarselo al usuario.

    Reponiendo: el pool entero menos los rellenos ya descartados. Sin reponer:
    solo los supervivientes activos, que es lo que garantiza que cada vuelta
    encoge y el bucle termina.
    """
    if refill:
        pool = [strategy for strategy in pool if strategy.set_id not in removed]
        return pool, (
            "Regla antirrelleno 6M: "
            f"{len(underrepresented)} relleno(s) fuera del aporte mínimo "
            f"{float(minimum_pct):.1f}%; reponiendo sobre {len(pool)} "
            "candidato(s) del pool para reutilizar el DD liberado."
        )
    pool = [
        strategy for strategy in pool
        if strategy.set_id in active_ids and strategy.set_id not in removed
    ]
    return pool, (
        "Regla antirrelleno 6M: "
        f"{len(underrepresented)} estrategia(s) bajo el aporte mínimo "
        f"{float(minimum_pct):.1f}%; refinando {len(pool)} superviviente(s) "
        "de la composición seleccionada, sin reabrir el pool global."
    )

def _optimize_without_recent_fillers(
    raw_sets: list[Any],
    minimum_pct: float,
    optimize: Callable[[list[Any]], PortfolioResult],
    *,
    progress: Callable[[str], None] | None = None,
    refill_from_pool: bool = False,
) -> tuple[PortfolioResult, set[str]]:
    """Selecciona una vez y refina una composicion activa que solo encoge.

    Reabrir el pool entero tras cada eliminacion admite rellenos nuevos y puede
    repetir el torneo experimental cientos de veces. Aqui se reoptimiza solo
    sobre los supervivientes activos, conservando la politica de riesgo y
    validacion del llamante: cada vuelta quita al menos un set activo y ninguno
    inactivo puede entrar.

    ``refill_from_pool`` cambia eso por reponer: cada vuelta conserva el pool
    menos los rellenos ya descartados, asi el DD liberado se puede volver a
    gastar y la amplitud sobrevive a la cuota en vez de pagarse con ella. La
    cuota es una parte del beneficio reciente total, asi que una composicion
    ancha deja mas miembros por debajo del minimo; refinar solo supervivientes
    entrega entonces un portafolio mas pequeno de lo que el pool permite.

    Solo para llamantes cuyo ``optimize`` sea UNA optimizacion. Con el torneo
    experimental como callback esto es el bucle de doce horas de
    ``ai_context/ubs_generation_repeated_tournaments.md``, y ese motor ya repone
    rellenos dentro del torneo, donde conoce el lote ganador. Acotado por
    ``STANDARD_ANTIFILLER_REFILL_PASSES``: agotado el presupuesto, el refinado
    que solo encoge termina el trabajo, asi que el resultado nunca sale mas
    sucio —ni mas lento por mas de ese presupuesto— que sin la opcion.
    """
    pool = list(raw_sets)
    removed: set[str] = set()
    refill_budget = STANDARD_ANTIFILLER_REFILL_PASSES if refill_from_pool else 0
    refilled = False
    while True:
        result = optimize(pool)
        underrepresented = _underrepresented_recent_allocation_ids(result, minimum_pct)
        underrepresented -= removed
        if not underrepresented:
            if removed:
                result.warnings.insert(0, _antifiller_summary(minimum_pct, removed, refilled))
            return result, removed
        active_ids = {allocation.set_id for allocation in result.allocations if allocation.units > 0}
        if active_ids and active_ids <= underrepresented:
            underrepresented.discard(_best_recent_contributor(result))
            if not underrepresented:
                return result, removed
        removed.update(underrepresented)
        refilled = refilled or refill_budget > 0
        pool, message = _antifiller_pool(
            pool, active_ids, removed, underrepresented, minimum_pct,
            refill=refill_budget > 0,
        )
        refill_budget = max(refill_budget - 1, 0)
        if not pool:
            raise ValueError("La regla antirrelleno 6M no dejó una composición reoptimizable")
        if progress:
            progress(message)
