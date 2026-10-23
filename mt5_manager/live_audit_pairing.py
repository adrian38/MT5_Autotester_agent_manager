"""El criterio que decide qué cierre real le toca a cada operación del tester.

Sale de `live_audit_comparison.py`, que tocaba su techo de 600 líneas. Es la
parte del auditado que más se revisa —`live_audit_pairing_20261004.md` y
`live_audit_pairing_20261023.md` son dos revisiones del mismo criterio— y es la
única que decide qué real entra en la curva de cada estrategia, así que se lee
mejor sola.

**El criterio vive sólo en el manager.** El nodo publica los cierres reales sin
filtrar y aquí se decide; la copia de `manager_node_runtime/live_audit*.py` de
cada agente conserva su propio `_compare`, pero no es el que produce los
informes. No hay nada que portar.
"""
from __future__ import annotations

from typing import Any

from .live_audit_core import _effective_price_tolerance, _expected_real_volume
from .live_audit_manual_pairs import tester_pair_key


def _stops_evidence(
    expected: dict[str, Any], actual: dict[str, Any], limit: float | None,
) -> int:
    """0 coinciden, 1 sin evidencia, 2 falla un nivel, 3 fallan los dos.

    El SL y el TP con que se coloca la orden son la huella de la operación:
    en el auditado del 2026-10-04 los dos XAUUSD que abren en el mismo
    segundo llevaban 4189,28/4133,78 y 4169,86/4123,54, y sólo uno de los
    dos cierres reales traía cada par. No siempre existen —una orden a
    mercado no declara niveles—, y entonces no dicen nada.

    **Cuántos fallan importa.** Era un sí/no, y con eso la huella dejaba de
    distinguir en cuanto un solo nivel se pasaba: en `audit-173` de
    RoboForex, `#T19` declaraba SL a 2,5 puntos del real —dentro del límite
    de 10,5— y TP a 14,7, fuera por 4,2; `#T21` declaraba 140,4 y 320,8.
    Las dos caían en el mismo cajón, una diferencia de 56× se perdía, y
    decidía el campo siguiente, el lote esperado, que con autolote es una
    simulación y no un hecho observado: se llevó la real la fila cuyo cierre
    estaba a 47.798 s en vez de la que coincidía al segundo. Un nivel que
    coincide y otro que no es evidencia más débil que las dos coincidiendo,
    pero más fuerte que ninguna.
    """
    levels = [
        (float(expected.get(key) or 0.0), float(actual.get(key) or 0.0))
        for key in ("sl", "tp")
    ]
    known = [(mine, theirs) for mine, theirs in levels if mine and theirs]
    if not known or limit is None:
        return 1
    apart = sum(1 for mine, theirs in known if abs(mine - theirs) > limit)
    return 1 + apart if apart else 0



def _pair_cost(
    expected: dict[str, Any], actual: dict[str, Any], request: dict[str, Any],
    points: dict[str, float],
) -> tuple[int, int, int, int, float] | None:
    """Lo lejos que está esta real de ser la del tester, o `None` si no lo es.

    Manda la huella de SL y TP, cuando las dos partes la declaran. Después
    el lote, que identifica a la estrategia dentro de la cuenta igual que en
    el filtro de pertenencia. Después la apertura dentro de tolerancia,
    después el cierre, y a igualdad la menor distancia total.

    Una fila que el usuario declaró **sin real** no admite ninguna pareja: es
    la única forma de deshacer un emparejado de más, que salía con desviaciones
    inventadas y metía en la curva Real un PnL ajeno. Se descarta aquí y no
    después para que la real que tenía vuelva al reparto y pueda irse con la
    fila a la que de verdad le toca.
    """
    if (
        actual["symbol"].casefold() != expected["symbol"].casefold()
        or actual["side"] != expected["side"]
        or tester_pair_key(expected) in (request.get("_unpaired_rows") or ())
    ):
        return None
    time_limit = request["trade_time_tolerance_seconds"]
    open_delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
    close_delta = abs((actual["close_time"] - expected["close_time"]).total_seconds())
    expected_volume, _tester_volume = _expected_real_volume(expected, request, actual)
    volume_limit = max(expected_volume, 1e-9) * request["volume_tolerance_pct"] / 100
    price_limit, _points, _rule = _effective_price_tolerance(
        actual["symbol"], points.get(actual["symbol"], 0.0),
        request["price_tolerance_points"],
    )
    stops_evidence = _stops_evidence(expected, actual, price_limit)
    complete_stops = stops_evidence == 0 and all(
        float(expected.get(key) or 0.0) and float(actual.get(key) or 0.0)
        for key in ("sl", "tp")
    )
    volume_outside = abs(float(actual["volume"]) - expected_volume) > volume_limit
    if (
        open_delta > time_limit and close_delta > time_limit
        and (not complete_stops or volume_outside)
    ):
        return None
    return (
        stops_evidence, int(volume_outside),
        int(open_delta > time_limit), int(close_delta > time_limit),
        open_delta + close_delta,
    )



def _assign_real_trades(
    tester: list[dict[str, Any]], real: list[dict[str, Any]], request: dict[str, Any],
    points: dict[str, float], forced: dict[int, int] | None = None,
) -> dict[int, int]:
    """Reparte los cierres reales entre las operaciones del tester.

    Elegir por tester, de uno en uno y sólo por la apertura, repartía mal
    dos operaciones abiertas en el mismo segundo: la primera se quedaba con
    el cierre de la segunda y ninguna de las dos quedaba dentro de
    tolerancia. El reparto se decide ahora sobre todas las parejas a la vez,
    de la mejor a la peor, y cada real se consume una sola vez.

    Las parejas que decidió el usuario entran antes que cualquier coste y
    consumen su real: lo que el auditor no supo ver no se vuelve a repartir.
    """
    costs = sorted(
        (*cost, tester_index, real_index)
        for tester_index, expected in enumerate(tester)
        for real_index, actual in enumerate(real)
        for cost in [_pair_cost(expected, actual, request, points)]
        if cost is not None
    )
    assignment: dict[int, int] = dict(forced or {})
    taken: set[int] = set(assignment.values())
    for *_cost, tester_index, real_index in costs:
        if tester_index in assignment or real_index in taken:
            continue
        assignment[tester_index] = real_index
        taken.add(real_index)
    return assignment
