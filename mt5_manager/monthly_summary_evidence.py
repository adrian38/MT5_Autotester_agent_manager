"""El mes cerrado contra los meses del propio backtest, contándolos.

Aquí sólo hay aritmética sobre la media estacional que ya dejó medida el
sondeo nocturno (`live_account_expectation`). No abre informes, no toca la
memoria de ningún agente y no sabe de portafolios: recibe números y devuelve
números.

**El método es contar ventanas de la propia serie, no suponer una normal.** Es
el mismo gesto que `live_audit_degradation_evidence` hace con el beneficio por
operación, y por la misma razón: la distribución mensual de una cartera tiene
la cola izquierda gorda y rachas, así que un «está a −1,8 σ de su media» dice
mucho menos que «de los setenta y dos meses del backtest, nueve fueron
peores». Lo segundo se entiende, no asume nada y es exactamente lo que decide
si un mes malo es raro o es martes.

Qué se mide, y por qué estas dos cosas:

- **El beneficio**, contra todos los meses y contra los del mismo mes del año.
  Las dos hacen falta: octubre y abril no se parecen —por eso la promesa es
  estacional—, pero seis octubres son seis datos y con seis no se concluye.
  El rango general da muestra; el estacional da contexto.
- **El drawdown**, que es el que de verdad retira una cartera. Las reglas de
  retirada al uso miran la DD viva contra la del backtest (1,5× o 2× el máximo
  histórico es la heurística corriente), y hasta ahora esto no se medía en
  ningún sitio: lo dice la última sección de
  `ai_context/live_audit_degradation_evidence.md`. Se mide contando, igual que
  el beneficio, y el listón es el peor mes del backtest, que es un hecho de la
  serie y no un múltiplo elegido a ojo.

**El lote manda y se declara.** Estos meses están medidos al lote con el que
se construyó la promesa, que con autolote no es el declarado sino el que la
cuenta operó. Comparar la DD de la cuenta con una tabla medida a otro tamaño
compararía el volumen, no el comportamiento, así que quien llama pasa la tabla
que corresponde y la tarjeta dice a qué lote está.
"""
from __future__ import annotations

from typing import Any, Sequence

EXTREME_QUANTILE = 0.95
"""Desde dónde un drawdown deja de ser corriente sin llegar a ser inédito.

No es un listón de retirada: es el aviso. Por encima del peor mes del backtest
el hecho es categórico —nunca pasó— y ahí sí se pide decisión.
"""

MINIMUM_MONTHS = 6
"""Meses del backtest por debajo de los cuales no se cuenta nada.

Con tres meses, «uno de tres fue peor» es un 33 % que no significa nada. Sin
muestra se devuelve el recuento vacío y quien lo enseñe dirá que no concluye,
que es lo que hace `month_expectation` con los años de la media.
"""


def backtest_months(expectation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Cada mes del backtest con su beneficio y su drawdown, por `YYYY-MM`.

    La media estacional guarda, dentro de cada mes del año, la fila de cada
    año que lo promedia (`live_account_expectation._seasonal`). Eso es la serie
    mensual completa del backtest, y es justo lo que hace falta para contar.
    """
    found: dict[str, dict[str, Any]] = {}
    for row in ((expectation or {}).get("months") or {}).values():
        if not isinstance(row, dict):
            continue
        for label, values in (row.get("months") or {}).items():
            if not isinstance(values, dict):
                continue
            found[str(label)] = {
                "profit": float(values.get("profit") or 0.0),
                "drawdown": float(values.get("drawdown") or 0.0),
                "trades": int(values.get("trades") or 0),
            }
    return found


def _season(months: dict[str, dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    """Sólo los meses del backtest que son el mismo mes del año que `label`."""
    number = label.split("-", 1)[-1]
    return {
        key: value for key, value in months.items()
        if key.split("-", 1)[-1] == number
    }


def _empty_rank() -> dict[str, Any]:
    return {"worse": 0, "total": 0, "pct": None, "measured": False}


def rank(
    value: Any, values: Sequence[float], *, lower_is_worse: bool = True,
) -> dict[str, Any]:
    """Cuántos de `values` fueron iguales o peores que `value`.

    `lower_is_worse` para el beneficio —menos es peor— y al revés para el
    drawdown, donde peor es más hondo. El empate cuenta como peor a propósito:
    un mes idéntico al peor del backtest no es mejor que él.
    """
    numbers = [float(item) for item in values]
    if value is None or len(numbers) < MINIMUM_MONTHS:
        return {**_empty_rank(), "total": len(numbers)}
    target = float(value)
    worse = sum(
        1 for item in numbers
        if (item <= target if lower_is_worse else item >= target)
    )
    return {
        "worse": worse, "total": len(numbers),
        "pct": worse * 100.0 / len(numbers), "measured": True,
    }


def quantile(values: Sequence[float], fraction: float) -> float | None:
    """El cuantil por rango más cercano, sin interpolar.

    Interpolar entre dos meses inventaría un mes que no existió, que es
    justamente lo que este módulo evita.
    """
    numbers = sorted(float(item) for item in values)
    if not numbers:
        return None
    position = max(0, min(len(numbers) - 1, round(fraction * (len(numbers) - 1))))
    return numbers[position]


def profit_standing(
    profit: Any, months: dict[str, dict[str, Any]], label: str,
) -> dict[str, Any]:
    """Dónde cae el beneficio del mes: entre todos y entre los de su mes."""
    overall = [row["profit"] for row in months.values()]
    seasonal = [row["profit"] for row in _season(months, label).values()]
    return {
        "overall": rank(profit, overall),
        # El estacional casi nunca llega a `MINIMUM_MONTHS` —seis años son seis
        # octubres—, así que se cuenta sin exigir muestra y se enseña como
        # contexto, nunca como la cifra que decide.
        "seasonal": {
            "worse": (
                0 if profit is None
                else sum(1 for item in seasonal if item <= float(profit))
            ),
            "total": len(seasonal),
            "measured": profit is not None and bool(seasonal),
        },
    }


def drawdown_standing(
    drawdown: Any, months: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """El drawdown del mes contra los drawdowns mensuales del backtest.

    `verdict` es uno de tres y nombra el hecho, no la decisión:

    - `beyond`: más hondo que el peor mes del backtest. Nunca pasó.
    - `extreme`: por encima del 95 % de ellos sin llegar al peor.
    - `normal`: dentro de lo que esta cartera ya hizo.

    Sin muestra suficiente no hay veredicto y se dice (`measured: False`): una
    cartera con tres meses de backtest no puede declarar nada inédito.
    """
    values = [row["drawdown"] for row in months.values()]
    standing = rank(drawdown, values, lower_is_worse=False)
    worst = max(values) if values else None
    extreme = quantile(values, EXTREME_QUANTILE)
    verdict = ""
    if standing["measured"] and drawdown is not None:
        depth = float(drawdown)
        if worst is not None and depth > worst:
            verdict = "beyond"
        elif extreme is not None and depth >= extreme:
            verdict = "extreme"
        else:
            verdict = "normal"
    return {
        "rank": standing, "worst": worst, "extreme": extreme,
        "verdict": verdict, "measured": standing["measured"],
    }


def below_promise(profit: Any, expected: Any) -> bool | None:
    """Si ese mes quedó por debajo de lo que prometía, o `None` sin promesa.

    `None` no es «no» y por eso no se devuelve `False`: un mes sin promesa
    medible no puede entrar en la racha ni romperla, porque nadie sabe de qué
    lado habría caído.
    """
    if profit is None or expected is None:
        return None
    return float(profit) < float(expected)


def streak(flags: Sequence[bool | None]) -> int:
    """Meses seguidos por debajo, contando desde el más reciente.

    `flags` llega del mes cerrado hacia atrás. Un mes sin promesa **corta** la
    cuenta en vez de saltarse: una racha con un agujero en medio no es una
    racha, es dos trozos, y presentarla como una sola justificaría un cambio
    con menos evidencia de la que dice tener.
    """
    total = 0
    for flag in flags:
        if flag is not True:
            break
        total += 1
    return total
