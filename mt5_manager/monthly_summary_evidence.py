"""El mes cerrado contra lo que da un mes como ése en el propio backtest.

Aquí sólo hay aritmética sobre la media estacional que ya dejó medida el
sondeo nocturno (`live_account_expectation`). No abre informes, no toca la
memoria de ningún agente y no sabe de portafolios: recibe números y devuelve
números.

**El método es juzgar contra la propia serie, y contra la parte de la serie
que se le parece.** Un octubre se mide con octubres: la media de los octubres
dice qué se esperaba y su desviación dice cuánto puede variar uno sin que pase
nada, así que la banda sale de la cartera y no de un umbral elegido a ojo.

Y es un **juicio**, no un recuento. «De los seis octubres, cuatro fueron
peores» dice dónde cae el mes; lo que la pantalla tiene que contestar es si la
cartera se porta como lo que se compró, y eso se dice con «hizo el 138 % de lo
que da un octubre» o «el 45 %». Esta versión sustituyó a dos que contaban: la
primera contra los setenta y siete meses, la segunda contra los seis octubres.

Qué se mide, y por qué estas dos cosas:

- **El beneficio, contra lo que da un mes como ése**, no contra dónde cae
  entre todos. Octubre y abril no se parecen —por eso la promesa es
  estacional—, así que la pregunta es si este octubre respeta la media de los
  octubres, y la banda de lo que puede variar un octubre la dan los octubres.
  Un recuento es una posición; esto es un juicio, que es lo que la pantalla
  tiene que contestar.
- **El drawdown, con la misma vara**. Es el que de verdad retira una cartera:
  las reglas al uso miran la DD viva contra la del backtest (1,5× o 2× el
  máximo histórico es la heurística corriente) y hasta ahora esto no se medía
  en ningún sitio —lo dice la última sección de
  `ai_context/live_audit_degradation_evidence.md`—. Aquí se mide como el
  beneficio: contra lo que le cuesta un mes como ése, no contra el peor mes de
  cualquier mes del año. El listón sale de la serie, no de un múltiplo a ojo.

**El lote manda y se declara.** Estos meses están medidos al lote con el que
se construyó la promesa, que con autolote no es el declarado sino el que la
cuenta operó. Comparar la DD de la cuenta con una tabla medida a otro tamaño
compararía el volumen, no el comportamiento, así que quien llama pasa la tabla
que corresponde y la tarjeta dice a qué lote está.
"""
from __future__ import annotations

import statistics
from typing import Any, Sequence

MINIMUM_MONTHS = 6
"""Meses del mismo mes del año por debajo de los cuales no se juzga nada.

Con tres octubres, una media y una desviación son dos números con la forma de
una medida y ninguna muestra detrás. Sin ella se devuelve la banda vacía y
quien la enseñe dirá que no puede juzgarlo, que es lo que hace
`month_expectation` con los años de la media.
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


def scenario_months(expectation: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    """Los meses de ese mes del año remedidos a los lotes que la cuenta operó.

    Sólo los hay con autolote y sólo del mes objetivo: `live_account_expectation`
    no guarda la tabla entera de cada escenario. Alcanza igual, porque el juicio
    es contra los meses del mismo mes del año y son exactamente éstos.
    """
    row = ((expectation or {}).get("months_at") or {}).get(label) or {}
    found: dict[str, dict[str, Any]] = {}
    for key, values in (row.get("months") or {}).items():
        if not isinstance(values, dict):
            continue
        found[str(key)] = {
            "profit": float(values.get("profit") or 0.0),
            "drawdown": float(values.get("drawdown") or 0.0),
            "trades": int(values.get("trades") or 0),
        }
    return found


def scenario_mean(expectation: dict[str, Any], label: str) -> dict[str, Any]:
    """`monthly_means` de ese escenario, ya promediado al guardarlo."""
    row = ((expectation or {}).get("months_at") or {}).get(label) or {}
    mean = row.get("monthly_mean") or {}
    total = int(mean.get("total") or 0)
    if total <= 0:
        return {"measured": False, "total": 0, "profit": None, "drawdown": None}
    return {
        "measured": True, "total": total,
        "profit": float(mean.get("profit") or 0.0),
        "drawdown": float(mean.get("drawdown") or 0.0),
    }


def season(months: dict[str, dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    """Sólo los meses del backtest que son el mismo mes del año que `label`."""
    number = label.split("-", 1)[-1]
    return {
        key: value for key, value in months.items()
        if key.split("-", 1)[-1] == number
    }


def monthly_means(months: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Lo que da y lo que cuesta un mes cualquiera del backtest.

    Contexto, no criterio: el juicio es contra los meses del mismo mes del
    año. Esto está al lado para saber de qué clase de mes se está hablando —un
    octubre que promete 711,97 cuando un mes corriente promete 621,27 es de
    los buenos de la cartera, y quedarse en 318,44 pesa distinto que en un mes
    flojo de por sí. Petición del usuario, 2026-11-02.
    """
    values = list(months.values())
    if not values:
        return {"measured": False, "total": 0, "profit": None, "drawdown": None}
    return {
        "measured": True, "total": len(values),
        "profit": statistics.fmean(row["profit"] for row in values),
        "drawdown": statistics.fmean(row["drawdown"] for row in values),
    }


def profit_band(
    profit: Any, months: dict[str, dict[str, Any]], label: str,
) -> dict[str, Any]:
    """Si el mes respeta la media de los suyos, con la banda de lo normal.

    Contar cuántos meses fueron peores dice **dónde cae** el mes; la pregunta
    de esta pantalla es si la cartera se está portando como lo que se compró,
    que es otra cosa. «4 de 6 octubres fueron peores» no la contesta: lo que
    la contesta es que este octubre hizo el 138 % de lo que da un octubre, o
    el 45 %.

    «Lo normal» es la media de los meses de ese mes del año más menos su
    desviación, y la banda sale de la dispersión de esos mismos meses: cuánto
    puede variar un octubre sin que pase nada lo dicen los octubres, no un
    umbral elegido a ojo. La media es exactamente la promesa de la tarjeta
    —`month_expectation` promedia los mismos años—, así que la frase no
    necesita repetirla como «diferencia».

    Petición del usuario, 2026-11-02, con la regla elegida por él.

    El mismo listón de muestra que el recuento: con menos de seis años no se
    dice qué es normal en un octubre. Y sin media positiva no hay porcentaje
    que valga —el 45 % de una pérdida media no significa nada—, así que
    `ratio` se queda en `None` y la frase lo dice con palabras.
    """
    values = [row["profit"] for row in season(months, label).values()]
    if profit is None or len(values) < MINIMUM_MONTHS:
        return {"measured": False, "total": len(values), "verdict": ""}
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    low, high = mean - deviation, mean + deviation
    value = float(profit)
    return {
        "measured": True, "total": len(values),
        "mean": mean, "deviation": deviation, "low": low, "high": high,
        "worst": min(values), "best": max(values),
        "ratio": (value / mean * 100.0) if mean > 0 else None,
        "verdict": "below" if value < low else ("above" if value > high else "normal"),
    }


def drawdown_band(
    drawdown: Any, months: dict[str, dict[str, Any]], label: str,
) -> dict[str, Any]:
    """Lo que le cuesta un mes como ése, y si éste se salió de ahí.

    Misma forma que `profit_band` y por la misma razón (petición del usuario,
    2026-11-02): la pregunta es si el mes respeta la media de los suyos, y eso
    vale igual para lo que gana que para lo que cae. Un octubre se compara con
    octubres también aquí; medirlo contra los setenta y siete dejaba el listón
    en manos de un agosto.

    `verdict` nombra el hecho, no la decisión:

    - `beyond`: más hondo que el peor de esos meses. Nunca pasó en uno.
    - `deeper`: por encima de la banda de lo normal sin llegar al peor.
    - `shallower`: por debajo de la banda, que en un drawdown es buena noticia.
    - `normal`: lo que suele costarle un mes así.

    La banda se recorta en cero porque un drawdown no es negativo: con una
    desviación mayor que la media, `media − desviación` daría un suelo que no
    existe y «por debajo de lo normal» dejaría de significar nada.
    """
    values = [row["drawdown"] for row in season(months, label).values()]
    if drawdown is None or len(values) < MINIMUM_MONTHS:
        return {"measured": False, "total": len(values), "verdict": ""}
    table = season(months, label)
    # Cuál fue, no sólo cuánto: «34,61 EUR» no se puede comprobar y «octubre de
    # 2025» sí, y además dice si el listón lo puso un mes reciente o uno viejo.
    worst_month = max(table, key=lambda key: table[key]["drawdown"])
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    low, high = max(0.0, mean - deviation), mean + deviation
    depth, worst = float(drawdown), table[worst_month]["drawdown"]
    return {
        "measured": True, "total": len(values),
        "mean": mean, "deviation": deviation, "low": low, "high": high,
        "worst": worst, "worst_month": worst_month,
        "ratio": (depth / mean * 100.0) if mean > 0 else None,
        "verdict": (
            "beyond" if depth > worst
            else "deeper" if depth > high
            else "shallower" if depth < low else "normal"
        ),
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
