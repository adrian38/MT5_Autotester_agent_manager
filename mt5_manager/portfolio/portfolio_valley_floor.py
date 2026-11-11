"""Elegibilidad del pool y el suelo de valle ejecutable.

Cuando el DD de valle pedido no admite ni el lote minimo del pool, la
generacion no falla: reintenta desde el primer valle que si es ejecutable y lo
dice en la propuesta. El ajuste no se persiste solo — viaja en la propuesta y
solo se guarda si el usuario elige esa.
"""
from __future__ import annotations

from typing import Any, Callable

from portfolio_manager.ubs_portfolio import MIN_RECENT_EQUITY_RECOVERY, filter_eligible_sets


def eligibility_counts(
    sets: list[Any],
    minimum_trades: int,
    *,
    apply_recent_recovery: bool = True,
) -> dict[str, int]:
    """Explain the shared UBS eligibility funnel stage by stage.

    ``filter_eligible_sets`` decide en una pasada y solo devuelve a los
    supervivientes: cuando el pool se queda vacío no dice qué filtro lo vació.
    Este recuento repite exactamente sus condiciones, en su orden, para que los
    tres ámbitos puedan nombrar la etapa culpable en vez de fallar en seco.

    ``apply_recent_recovery=False`` es para Grid, que desactiva a propósito la
    regla de recuperación reciente (``has_recent_performance=False`` en
    ``optimize_grid_portfolio``): contarla ahí daría un número de elegibles que
    no es el que el optimizador va a usar.
    """
    minimum = int(minimum_trades)
    accepted = [
        item for item in sets
        if str(getattr(item, "robustness_status", "")) == "accepted"
    ]
    not_used = [item for item in accepted if not bool(getattr(item, "already_used", False))]
    with_curve = [item for item in not_used if getattr(item, "curve_2020_2026_001", None)]
    with_trades = [item for item in with_curve if int(item.trades_2020_2026) > 0]
    enough_trades = [item for item in with_curve if int(item.trades_2020_2026) >= minimum]
    positive = [item for item in enough_trades if float(item.net_profit_2020_2026_001) > 0]
    recent_recovery = [
        item for item in positive
        if not apply_recent_recovery
        or not item.has_recent_performance
        or (
            float(item.recent_net_profit_001) / max(float(item.recent_equity_dd_001), 1.0)
        ) >= MIN_RECENT_EQUITY_RECOVERY
    ]
    return {
        "total": len(sets),
        "accepted": len(accepted),
        "not_used": len(not_used),
        "with_curve": len(with_curve),
        "with_trades": len(with_trades),
        "enough_trades": len(enough_trades),
        "positive": len(positive),
        "recent_recovery": len(recent_recovery),
        "eligible": len(recent_recovery),
    }

def strategy_unit_risk(strategy: Any) -> float:
    """Riesgo de una unidad con la misma regla que ``evaluate_portfolio``.

    La cartera se mide como ``max(DD cerrado combinado, flotante)``; con una
    sola unidad eso es el máximo entre su valle cerrado y su flotante.
    """
    return max(
        float(getattr(strategy, "max_floating_dd_001", 0.0) or 0.0),
        float(getattr(strategy, "valley_dd_2020_2026_001", 0.0) or 0.0),
    )

def _adjusted_valley_pcts(
    strategies: list[Any],
    *,
    capital: float,
    reserve_pct: float,
    requested_pct: float,
    risk_of: Callable[[Any], float] = strategy_unit_risk,
) -> list[float]:
    """Executable valley floors above the requested percentage.

    Si el valle pedido no llega ni al riesgo de la estrategia más pequeña del
    pool, no existe ninguna cartera: ni una sola unidad cabe. Devolver el error
    seco deja la pantalla vacía sin decir cuánto falta. Estos son los siguientes
    escalones -- uno por nivel de riesgo distinto, de menor a mayor -- y el
    primero que optimice es el mínimo ejecutable de ese pool.
    """
    if capital <= 0:
        return []
    reserve_factor = 1.0 - min(max(float(reserve_pct), 0.0), 99.0) / 100.0
    if reserve_factor <= 0:
        return []
    requested_limit = float(capital) * float(requested_pct) / 100.0 * reserve_factor
    risks = sorted({
        round(risk, 8)
        for risk in (float(risk_of(strategy)) for strategy in strategies)
        if risk > requested_limit + 1e-9
    })
    return [
        risk / float(capital) * 100.0 / reserve_factor + 1e-7
        for risk in risks
    ]

MAX_VALLEY_FLOOR_ATTEMPTS = 5

def _proposals_are_empty(proposals: list[dict[str, Any]]) -> bool:
    """True when every proposal came back without una sola estrategia activa.

    Un valle inalcanzable no siempre lanza error. UBS completo sí lo hace -- la
    composición base no produce ningún set --, pero el mensual devuelve tres
    propuestas de cero estrategias y cero neto, que es exactamente el mismo
    fracaso presentado como resultado. Ambos casos disparan el suelo ejecutable.
    """
    active = [
        int(getattr(proposal.get("result"), "active_strategies", -1) or 0)
        for proposal in proposals
    ]
    return bool(active) and all(value == 0 for value in active)

def _valley_floor_attempt(
    build: Callable[[dict[str, Any]], list[dict[str, Any]]],
    inputs: dict[str, Any],
    adjusted_pct: float,
) -> list[dict[str, Any]] | None:
    """Un intento con el valle bajado, o ``None`` si fallo o salio vacio."""
    attempt_inputs = {**inputs, "valley_dd_pct": adjusted_pct, "point_dd_pct": adjusted_pct}
    try:
        proposals = build(attempt_inputs)
    except ValueError:
        return None
    return None if _proposals_are_empty(proposals) else proposals

def _with_executable_valley_floor(
    build: Callable[[dict[str, Any]], list[dict[str, Any]]],
    inputs: dict[str, Any],
    raw_sets: list[Any],
    *,
    minimum_trades: int,
    reserve_pct: float,
    warnings: list[str],
    risk_of: Callable[[Any], float] = strategy_unit_risk,
) -> tuple[list[dict[str, Any]], bool, float]:
    """Run ``build`` and, if the valley is unreachable, retry from its floor.

    Devuelve las propuestas, si hubo ajuste y el porcentaje realmente aplicado.
    El ajuste no se persiste solo: viaja en la propuesta y únicamente se guarda
    si el usuario elige esa propuesta, igual que en Grid.
    """
    requested_pct = float(inputs["valley_dd_pct"])
    first_error: ValueError | None = None
    empty_baseline: list[dict[str, Any]] = []
    try:
        proposals = build(inputs)
        if not _proposals_are_empty(proposals):
            return proposals, False, requested_pct
        empty_baseline = proposals
    except ValueError as exc:
        first_error = exc

    floors = _adjusted_valley_pcts(
        filter_eligible_sets(raw_sets, int(minimum_trades)),
        capital=float(inputs["capital"]),
        reserve_pct=float(reserve_pct),
        requested_pct=requested_pct,
        risk_of=risk_of,
    )
    attempts = floors[:MAX_VALLEY_FLOOR_ATTEMPTS]
    for adjusted_pct in attempts:
        proposals = _valley_floor_attempt(build, inputs, adjusted_pct)
        if proposals is None:
            continue
        warnings.insert(
            0,
            f"El valle solicitado {requested_pct:.3f}% no admite el lote mínimo de "
            f"este pool. Esta propuesta usa el mínimo ejecutable {adjusted_pct:.3f}%.",
        )
        return proposals, True, adjusted_pct
    if len(floors) > len(attempts):
        warnings.append(
            f"Se probaron los {len(attempts)} primeros valles ejecutables de "
            f"{len(floors)} posibles sin encontrar una cartera viable."
        )
    if first_error is not None:
        raise first_error
    # Ningún suelo dio cartera: se devuelve lo que había, que es lo que este
    # ámbito devolvía antes del reintento.
    warnings.insert(
        0,
        f"El valle solicitado {requested_pct:.3f}% no admite el lote mínimo de este "
        f"pool y ninguno de los {len(attempts)} valles ejecutables probados dio cartera.",
    )
    return empty_baseline, False, requested_pct

def describe_eligibility(counts: dict[str, int], minimum_trades: int) -> str:
    """Embudo en una línea para el progreso y para el error de pool vacío."""
    return (
        f"{counts['total']} cargada(s); {counts['accepted']} aceptada(s); "
        f"{counts['not_used']} sin usar; {counts['with_trades']} con operaciones; "
        f"{counts['enough_trades']} con >= {int(minimum_trades)} trades; "
        f"{counts['positive']} con neto positivo; "
        f"{counts['recent_recovery']} con recuperación reciente 6M; "
        f"{counts['eligible']} elegibles"
    )
