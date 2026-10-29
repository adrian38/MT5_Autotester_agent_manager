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
import math
from pathlib import Path
from typing import Any, Callable, Sequence

from .live_audit_autolot import executable_lot

LOT_BASIS = "autolot_mean_balance"
"""Cómo se eligió el lote de ese mes. Lo guarda la promesa y lo dice la tarjeta."""
STEP_LOT_BASIS = "balance_step_opening"
"""Lote Risk=2 del set, calculado con el saldo de apertura del mes."""


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


def month_opening_balance(balances: Sequence[tuple[str, float]], label: str) -> float:
    """Saldo al abrir el mes: último anterior, o primero del mes si no existe."""
    valid = sorted(
        (str(day), float(value)) for day, value in balances if float(value) > 0
    )
    previous = [value for day, value in valid if day < f"{label}-01"]
    if previous:
        return previous[-1]
    current = [value for day, value in valid if day.startswith(f"{label}-")]
    return current[0] if current else 0.0


def _set_values(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    if encoding == "utf-8-sig" and data[:4096].count(b"\x00") > 8:
        encoding = "utf-16-le"
    text = data.decode(encoding, errors="replace")
    return {
        key.strip(): value.split("||", 1)[0].strip()
        for line in text.splitlines() if "=" in line and not line.lstrip().startswith(";")
        for key, value in [line.split("=", 1)]
    }


def step_rules(
    members: Sequence[dict[str, Any]], resolve: Callable[[str], Path],
    observed: Sequence[str] = (),
) -> dict[str, dict[str, float]]:
    """Reglas por tramos, incluso si la auditoría normalizó `Risk` a cero."""
    found: dict[str, dict[str, float]] = {}
    observed_candidates = set(observed)
    for member in members:
        candidate = str(member.get("candidate_id") or "").strip()
        stored_path = str(member.get("set_path") or "").strip()
        if not candidate or not stored_path:
            continue
        try:
            values = _set_values(Path(resolve(stored_path)))
            risk = int(float(values.get("Risk") or 0))
            step = float(values.get("LotPerBalance_step") or 0.0)
            maximum = float(values.get("MaxLots") or 0.0)
        except (OSError, UnicodeError, TypeError, ValueError):
            continue
        if step > 0 and (risk == 2 or candidate in observed_candidates):
            found[candidate] = {"step": step, "max_lots": maximum}
    return found


def opening_real_lots(detail: dict[str, Any], label: str) -> dict[str, float]:
    """Primer lote real observado en el mes, por estrategia."""
    rows: list[tuple[str, str, float]] = []
    for comparison in detail.get("operation_comparisons") or []:
        real = comparison.get("real") or comparison.get("real_position_still_open") or {}
        candidate = str(comparison.get("strategy") or "").strip()
        opened = str(real.get("open_time") or "")
        try:
            lot = float(real.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        if candidate and opened.startswith(label) and lot > 0:
            rows.append((opened, candidate, lot))
    found: dict[str, float] = {}
    for _opened, candidate, lot in sorted(rows):
        found.setdefault(candidate, lot)
    return found


def step_month_lots(
    members: Sequence[dict[str, Any]], rules: dict[str, dict[str, float]],
    label: str, balance: float,
) -> MonthLots:
    """Lote Risk=2: 0,01 por cada tramo completo de balance."""
    lots: dict[str, float] = {}
    for member in members:
        candidate = str(member.get("candidate_id") or "").strip()
        rule = rules.get(candidate) if candidate else None
        if not rule:
            continue
        lot = math.floor(balance / rule["step"]) * 0.01
        maximum = float(rule.get("max_lots") or 0.0)
        lots[candidate] = min(lot, maximum) if maximum > 0 else lot
    return MonthLots(label=label, balance=balance, lots=lots)


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
