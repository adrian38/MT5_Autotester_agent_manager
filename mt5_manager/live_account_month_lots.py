"""Con qué lote operó la cuenta cada mes cuando lo decide el autolote.

La promesa del backtest se mide a **un** lote por estrategia
(`live_account_expectation.member_lot`), y eso vale mientras la cuenta opere
siempre el mismo tamaño. Con autolote no lo hace: el EA calcula el lote de
cada apertura desde el saldo, así que el tamaño sube y baja con la cuenta
mientras la promesa se queda clavada en el lote declarado. Las dos mitades de
la tarjeta dejan de estar a la misma escala y el badge pasa a medir volumen.

Medido el 2026-10-24 sobre la cuenta 77028839 de RoboForex, que sirve al
portafolio #173: `real_strategy_lots` declara 0,01 en las treinta y cinco
estrategias, pero con 6.203,14 € de saldo medio en octubre la fórmula del
autolote pide desde 0,01 hasta 0,09 según el `DD_0.01` de cada una. La media
de octubre prometía 117,24 € contra los −605,76 € que la cuenta llevaba, y
buena parte de esa distancia es el tamaño.

## El lote sale de la fórmula, no de lo ejecutado

Es la misma que aplica el veredicto (`live_audit_autolot.expected_volume`):

`saldo × (% riesgo / 100) × 0,01 / DD_0.01`, redondeada al paso del bróker.

Lo ejecutado también está medido —la auditoría lo compara operación a
operación— pero no sirve de criterio aquí: sólo cubre el periodo auditado y
las operaciones emparejadas, así que una estrategia que no operó ese mes se
quedaría sin lote. Y no es más fiable: en `audit-148` de RoboForex **65 de 94**
operaciones ejecutaron un lote distinto del calculado
(`ai_context/live_audit_autolot.md`).

## El saldo es el medio del mes

No el de hoy. El lote se mueve con el saldo **dentro** del mes, así que un
solo número tiene que representar al mes entero, y el de hoy inflaría o
deshincharía los días anteriores: en octubre de 77028839 el saldo fue de
5.366,63 € a 6.548,28 €. El medio también es lo que mantiene honesto al mes
anterior, que tiene que compararse con el tamaño que la cuenta tenía
entonces —3.208,51 € en septiembre, la mitad— y no con el de ahora.

## Una estrategia sin regla no se escala, y se dice

El Excel del autolote puede no nombrarlas todas. Esas conservan su lote
declarado, porque es lo único que se sabe de ellas; lo que no puede pasar es
que la tarjeta presente una media medio escalada sin decirlo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .live_audit_autolot import executable_lot

LOT_BASIS = "autolot_mean_balance"
"""Cómo se eligió el lote de ese mes. Lo guarda la promesa y lo dice la tarjeta."""


@dataclass(frozen=True)
class MonthLots:
    """El lote de cada miembro ese mes, y con qué saldo se calculó."""

    label: str
    balance: float
    lots: dict[str, float] = field(default_factory=dict)
    without_rule: tuple[str, ...] = ()


def month_balance(balances: Sequence[tuple[str, float]], label: str) -> float:
    """El saldo medio de los días de ese `YYYY-MM`, o cero si no se sabe.

    Cero no es «cuenta vacía»: es que la serie no llega a ese mes. Quien lo
    reciba no escala nada, porque un saldo inventado daría un lote inventado.
    """
    found = [
        float(value) for day, value in balances
        if str(day).startswith(f"{label}-") and float(value) > 0
    ]
    return sum(found) / len(found) if found else 0.0


def raw_lot(balance: float, rule: dict[str, float]) -> float:
    """La fórmula del autolote, antes del paso del bróker."""
    try:
        dd_001 = float(rule["dd_001"])
        risk_pct = float(rule["risk_pct"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if dd_001 <= 0:
        return 0.0
    return balance * risk_pct / 100.0 * 0.01 / dd_001


def month_lots(
    members: Sequence[dict[str, Any]], rules: dict[str, dict[str, float]],
    label: str, balance: float,
    volume_rules: dict[str, tuple[float, float]] | None = None,
) -> MonthLots:
    """Los lotes de ese mes, ya redondeados al paso de cada símbolo.

    `rules` viene de `live_audit_autolot.rules_by_strategy`, que es quien sabe
    enlazar el nombre del `.set` del Excel con el `candidate_id` del miembro:
    buscar la regla por el magic degrada todas las filas al lote manual.
    """
    specs = dict(volume_rules or {})
    lots: dict[str, float] = {}
    without: list[str] = []
    for member in members:
        candidate = str(member.get("candidate_id") or "").strip()
        label_of = str(member.get("set_name") or candidate or "?")
        rule = rules.get(candidate) if candidate else None
        lot = raw_lot(balance, rule) if rule else 0.0
        if lot <= 0:
            without.append(label_of)
            continue
        symbol = str(member.get("symbol") or "").strip().casefold()
        lots[candidate] = executable_lot(lot, specs.get(symbol))
    return MonthLots(
        label=label, balance=balance, lots=lots, without_rule=tuple(without),
    )
