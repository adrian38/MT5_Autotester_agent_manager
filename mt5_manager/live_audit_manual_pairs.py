"""Las parejas que el auditor no encontró y el usuario sí conoce.

`_assign_real_trades` empareja por símbolo, lado, lote y tiempo, y antes de eso
el filtro de pertenencia tira las reales cuyo lote no cae en la banda del
portafolio. Las dos reglas son conjeturas, y cuando fallan la fila del tester
sale «SIN REAL» con su real delante, en otra tabla de la misma pantalla.

En `audit-170` de RoboForex (2026-10-18) hay cuatro filas sin pareja y tres
tienen candidata evidente: el `.JP225Cash` del tester abre a las 20:12:59 y la
real `772550823` a las 20:13:00, descartada por lote —banda 0,14–0,14 contra
0,16 reales—; los dos `EURUSD` de 0,08 tienen sendas reales de 0,06 por el
mismo motivo. La cuarta, `AMZN`, no tiene ninguna: la cuenta no operó ese
símbolo en el periodo, ni cerrada ni abierta, y ahí no hay nada que elegir.

**No afloja ninguna tolerancia.** La pareja forzada se mide exactamente igual
que una encontrada por el auditor, así que el `.JP225Cash` sale con desviación
de volumen en vez de desaparecer del recuento. Lo único que cambia es quién
decide que esas dos operaciones son la misma.

La operación del tester no tiene ticket —el Strategy Tester no lo exporta— así
que se identifica por su huella: estrategia, símbolo, lado y los dos instantes.
El `#T6` de la pantalla no vale: es la posición en la lista y se mueve en
cuanto la auditoría se vuelve a ejecutar. La real sí tiene `position_id`, que
es el único identificador único de la materia prima; `live_audit_attribution.py`
usa el mismo y por el mismo motivo.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


MAX_MANUAL_PAIRS = 2000
_MAX_KEY_LENGTH = 256
_FORBIDDEN = "\n\r"


def empty_manual_pairs() -> dict[str, Any]:
    return {}


def _moment(value: Any) -> str:
    """El instante tal y como lo publica `_trade_view`, venga vivo o en JSON."""
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def tester_pair_key(trade: dict[str, Any]) -> str:
    """La huella de una operación del tester; la pantalla construye la misma."""
    return "|".join((
        str(trade.get("strategy") or ""),
        str(trade.get("symbol") or ""),
        str(trade.get("side") or ""),
        _moment(trade.get("open_time")),
        _moment(trade.get("close_time")),
    ))


def real_pair_id(trade: dict[str, Any] | None) -> str:
    """El identificador de una real: `position_id`, y `ticket` si no lo hay."""
    for key in ("position_id", "ticket"):
        digits = str((trade or {}).get(key) or "").strip()
        if digits.isdigit() and len(digits) <= 20:
            return digits
    return ""


def _normalize_pair(key: Any, raw: Any) -> tuple[str, dict[str, str]]:
    tester = str(key or "").strip()
    if not tester or len(tester) > _MAX_KEY_LENGTH or any(c in tester for c in _FORBIDDEN):
        raise ValueError("La huella de la operación del tester no es válida")
    pair = raw if isinstance(raw, dict) else {"position_id": raw}
    position = str(pair.get("position_id") or "").strip()
    if not position.isdigit() or len(position) > 20:
        raise ValueError(f"La pareja de {tester} debe nombrar una operación real")
    marked_at = str(pair.get("at") or "").strip()
    if len(marked_at) > 40 or any(c in marked_at for c in _FORBIDDEN):
        raise ValueError(f"La fecha de la pareja de {tester} no es válida")
    return tester, {"position_id": position, "at": marked_at}


def normalize_manual_pairs(value: Any) -> dict[str, dict[str, str]]:
    """Valida lo guardado. Una real no puede ser la pareja de dos filas.

    Dejar que lo fuera repartiría el mismo PnL real dos veces y la curva de la
    cuenta diría más de lo que la cuenta hizo, que es justo lo que el reparto
    global del auditor evita consumiendo cada real una sola vez.
    """
    if value is None:
        return empty_manual_pairs()
    if not isinstance(value, dict) or len(value) > MAX_MANUAL_PAIRS:
        raise ValueError(f"manual_pairs admite como máximo {MAX_MANUAL_PAIRS} parejas")
    pairs: dict[str, dict[str, str]] = {}
    claimed: dict[str, str] = {}
    for raw_key, raw_pair in value.items():
        tester, pair = _normalize_pair(raw_key, raw_pair)
        if claimed.setdefault(pair["position_id"], tester) != tester:
            raise ValueError(
                f"La operación real {pair['position_id']} ya es la pareja de otra fila",
            )
        pairs[tester] = pair
    return pairs


def rescued_position_ids(pairs: Any) -> set[str]:
    """Las reales que el filtro de lote ya no puede tirar: alguien las reclama.

    Sin esto el emparejamiento manual no alcanzaría al caso más frecuente: la
    real existe, pero el filtro de pertenencia la apartó antes de comparar y no
    está entre las que el reparto podía usar.
    """
    return {
        str((pair or {}).get("position_id") or "") for pair in (pairs or {}).values()
    } - {""}


def note_manual_pairs(
    detail: dict[str, Any], forced: dict[int, int], attributed: dict[int, int],
) -> None:
    """Dice en la metodología cuántas parejas no las encontró el auditor.

    Sin esta línea el informe presenta como alineación medida algo que en parte
    decidió una persona, y quien lo lea después no puede saberlo. Las dos
    procedencias se cuentan aparte: una la eligió el usuario en la pantalla y
    la otra sale de lo que ya había escrito en el Excel de Análisis.
    """
    detail["manual_pairs"] = len(forced)
    detail["attributed_pairs"] = len(attributed)
    if forced:
        detail["methodology"]["alignment"] += (
            f" {len(forced)} pareja(s) las decidió el usuario a mano sobre reales que"
            " el reparto no eligió o que el filtro de lote había apartado: se miden"
            " con las mismas tolerancias y su fila lo dice."
        )
    if attributed:
        detail["methodology"]["alignment"] += (
            f" {len(attributed)} pareja(s) salen del Excel de atribución: la real"
            " quedó libre y el usuario ya la había dado a esa misma estrategia."
            " También se miden con las mismas tolerancias."
        )


def _same_market(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Mismo instrumento y mismo lado, la condición que exige `_pair_cost`.

    Se escribe aparte y con el mismo nombre que allí para que se vea que es la
    misma regla: una pareja automática que no la cumpliera sería una que el
    auditor nunca habría formado, y la fila la mediría igual sin decirlo.
    """
    return (
        str(actual.get("symbol") or "").casefold()
        == str(expected.get("symbol") or "").casefold()
        and actual.get("side") == expected.get("side")
    )


def attributed_assignment(
    tester: list[dict[str, Any]],
    real: list[dict[str, Any]],
    resolved: dict[str, dict[str, Any]],
    assignment: dict[int, int],
) -> dict[int, int]:
    """Las parejas que el Excel de Análisis ya había decidido sin decirlo así.

    Atribuir una real a una estrategia en el Excel es nombrar a su dueño; si
    esa misma estrategia tiene además una operación del tester que se quedó sin
    pareja, pedir otra vez la correspondencia en el selector es pedir dos veces
    el mismo trabajo. Se empareja por cercanía de apertura, de la mejor pareja
    a la peor y consumiendo cada real una sola vez, igual que el reparto del
    auditor. **No afloja ninguna tolerancia**: la fila se mide igual y puede
    salir con desviación.

    **El lado sí filtra.** `_pair_cost` exige mismo símbolo y mismo lado, así
    que nada de lo que empareja el auditor puede cruzarlos, y por eso la fila
    emparejada no los vuelve a comparar: no hay ningún motivo que diga «lados
    opuestos». Sin esta condición, un `buy` del tester se emparejaba con un
    `sell` real y salía `DESVIACIÓN` con desviación de tiempo y de PnL, como
    si fuera la misma operación medida con holgura, metiendo en la curva Real
    de la estrategia el resultado de una operación contraria.

    **El símbolo filtra también.** Aquí sí, al revés que en el selector: para
    cuando esto corre, `_apply_symbol_aliases` ya ha canonizado el símbolo de
    cada real al nombre que usa el tester —por eso `_pair_cost` los compara
    literalmente—, así que exigirlo no cierra el caso de los alias
    (`live_audit_cross_account_symbol_aliases.md`). Sin exigirlo, un
    comentario compartido por varias estrategias —`p_xmas` nombra 47 reales de
    cinco símbolos en `Christmas_Axi`— emparejaba una fila `XAUUSD` con una
    real `GOOGL`, y salía `DESVIACIÓN` sin que ningún motivo dijera que son
    instrumentos distintos.

    El selector sigue ofreciendo otros símbolos a propósito: ahí decide una
    persona que está mirando la fila, y puede rescatar un alias que el mapa no
    cubrió. Una regla automática no puede juzgar eso.
    """
    if not resolved:
        return {}
    taken = set(assignment.values())
    owners = {}
    for index, trade in enumerate(real):
        rule = resolved.get(real_pair_id(trade)) or {}
        if index not in taken and rule.get("strategy") and not rule.get("excluded"):
            owners[index] = str(rule["strategy"])
    candidates = sorted(
        (abs((real[index]["open_time"] - tester[row]["open_time"]).total_seconds()), row, index)
        for row in range(len(tester)) if row not in assignment
        for index, strategy in owners.items()
        if strategy == str(tester[row].get("strategy") or "")
        and _same_market(real[index], tester[row])
    )
    derived: dict[int, int] = {}
    used: set[int] = set()
    for _distance, row, index in candidates:
        if row not in derived and index not in used:
            derived[row] = index
            used.add(index)
    return derived


def forced_assignment(
    tester: list[dict[str, Any]], real: list[dict[str, Any]], pairs: dict[str, Any],
) -> dict[int, int]:
    """Traduce las huellas guardadas a los índices de esta ejecución.

    Lo que no se deja traducir se ignora a propósito: una pareja decidida sobre
    una ejecución anterior puede nombrar una operación que ésta ya no tiene, y
    eso no es motivo para dejar sin análisis a las demás.
    """
    if not pairs:
        return {}
    by_position = {real_pair_id(trade): index for index, trade in enumerate(real)}
    by_position.pop("", None)
    forced: dict[int, int] = {}
    taken: set[int] = set()
    for index, trade in enumerate(tester):
        pair = pairs.get(tester_pair_key(trade))
        real_index = by_position.get(str((pair or {}).get("position_id") or ""))
        if real_index is None or real_index in taken:
            continue
        forced[index] = real_index
        taken.add(real_index)
    return forced
