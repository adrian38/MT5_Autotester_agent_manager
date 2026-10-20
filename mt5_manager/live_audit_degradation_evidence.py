"""Cuándo un cruce de −2 σ es evidencia y cuándo es ruido.

`live_audit_degradation.compare` contrasta la media observada con la del
backtest suponiendo operaciones normales, independientes y **una sola mirada**.
Ninguna de las tres se cumple aquí, y cada una infla los avisos falsos:

- **La cola es gorda y las operaciones van en racha.** El backtest de
  `AXI/STANDARD:10751` (NAS100.fs M30, 1.363 operaciones) tiene asimetría
  −1,46, exceso de curtosis +1,66 y autocorrelación de primer orden +0,16. Su
  cruce de −2,12 σ con nueve operaciones valía 1,7 % por la normal y 6,7 %
  contando cuántos tramos consecutivos de nueve del propio backtest fueron
  iguales o peores: 90 de 1.355, uno de cada quince.
- **Se comparan todas las estrategias del portafolio a la vez.** Con trece,
  que alguna cruce −2 σ por azar deja de ser raro.
- **Y se vuelve a mirar cada semana**, sobre la serie acumulada, avisando la
  primera vez que cruza. Repetir un contraste pensado para una sola mirada es
  lo que más infla: con veinte vistas, un 5 % nominal se acerca al 25 %.

Así que `DEGRADA` deja de ser el cruce y pasa a ser el cruce **más** lo que el
cruce no demuestra solo:

1. `sequential_p` sustituye la probabilidad normal por una que sigue valiendo
   aunque se mire en cada auditoría;
2. `window_rarity` la contrasta contra los tramos consecutivos del propio
   backtest, que ya traen su asimetría, su curtosis y sus rachas;
3. las dos se exigen a `alpha / estrategias comparadas`.

Lo que cruza y no se confirma se queda en `AVISO`, con los números a la vista:
esconder el cruce sería el error contrario.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from .portfolio_identity import _resolve_source_path
from .portfolio_report_cache import cached_report

#: Prior del contraste secuencial, en operaciones. Es `MINIMUM_TRADES`: la
#: frontera aprieta donde la muestra ya merece una opinión, y con este valor
#: pide 3,96 σ con nueve operaciones y 3,31 σ con cien al nivel de −2 σ
#: (2,28 %). No crece sin parar: toca suelo hacia las cien y vuelve a subir muy
#: despacio, que es lo que mantiene el aviso alcanzable.
PRIOR_TRADES = 8.0

#: Por debajo de esto el backtest no da tramos suficientes para medir rareza.
MINIMUM_WINDOWS = 30

WINDOW_ERRORS = (AttributeError, OSError, SyntaxError, TypeError, ValueError, ZeroDivisionError)
"""Lo que puede fallar al reconstruir la serie del backtest. Un informe que no
se deja leer no inventa un veredicto: deja la fila sin prueba de ventanas y lo
dice, igual que `read_histories` con una memoria ilegible."""


def one_sided_p(deviations: float) -> float:
    """Probabilidad normal de quedar igual o por debajo de esas sigmas."""
    return 0.5 * math.erfc(-deviations / math.sqrt(2.0))


def alpha_for(sigma: float) -> float:
    """El nivel que ya exigía el umbral vigente: `−2 σ` son 2,28 %.

    Se deriva del umbral en vez de escribir un número nuevo para que mover
    `DEGRADED_SIGMA` siga moviendo todo el criterio a la vez.
    """
    return one_sided_p(-abs(sigma))


def sequential_p(deviations: float, trades: int, prior: float = PRIOR_TRADES) -> float:
    """Probabilidad que aguanta que se mire en cada auditoría.

    Es la mezcla normal de Robbins: bajo la hipótesis de que la estrategia
    sigue siendo la del backtest, la razón de verosimilitud mezclada sólo
    supera `1/p` con probabilidad `p` **en toda la serie de miradas**, no en
    una. Por eso puede recalcularse cada semana sobre la muestra acumulada sin
    repartir el nivel entre vistas que todavía no se sabe cuántas serán.

    `ratio` es el peso que la mezcla da a la muestra frente al prior: con pocas
    operaciones pesa poco y la frontera se va alta, y con cien toca su mínimo.
    """
    if trades <= 0:
        return 1.0
    ratio = trades / (trades + prior)
    try:
        likelihood = math.sqrt(1.0 - ratio) * math.exp(deviations * deviations / 2 * ratio)
    except OverflowError:
        return 0.0
    return min(1.0, 1.0 / likelihood) if likelihood > 0 else 1.0


def window_rarity(series: list[float], observed: float, size: int) -> dict[str, Any] | None:
    """Cuántos tramos consecutivos del backtest fueron iguales o peores.

    Tramos **solapados** y en su orden original: así cada uno conserva las
    rachas, los días malos enteros y la cola izquierda que la normal no tiene.
    El `+1` del numerador y del denominador es la convención de los contrastes
    por remuestreo: con cero tramos peores la prueba no afirma «imposible»,
    afirma «menos de uno entre los que hay».

    `resolution` es justo eso, el p más pequeño que la serie puede demostrar.
    Un backtest corto no llega a los niveles que pide la corrección por
    estrategias, y entonces esta prueba no decide: lo dice en vez de bloquear.
    """
    if size <= 0 or len(series) < size + MINIMUM_WINDOWS - 1:
        return None
    total = sum(series[:size])
    worse = 1 if total / size <= observed else 0
    for index in range(size, len(series)):
        total += series[index] - series[index - size]
        if total / size <= observed:
            worse += 1
    windows = len(series) - size + 1
    return {
        "windows": windows, "worse": worse,
        "p": (worse + 1) / (windows + 1), "resolution": 1 / (windows + 1),
    }


def poisson_tail(observed: int, expected: float) -> float:
    """`P(X <= observed)` con media `expected`, en logaritmos.

    Es sólo la **criba** del contraste de ritmo: los recuentos de este pipeline
    están sobredispersos —mediana 1,22 de varianza/media sobre 60 informes
    reales, con cola hasta 23—, así que un Poisson a secas avisaría de más.
    Decide `count_rarity`, que no supone nada. Esto sirve para no abrir el
    informe de las doce estrategias que no tienen nada que mirar.
    """
    if expected <= 0:
        return 1.0
    if observed >= expected:
        return 1.0
    terms = []
    for count in range(0, observed + 1):
        terms.append(-expected + count * math.log(expected) - math.lgamma(count + 1))
    top = max(terms)
    return min(1.0, math.exp(top) * sum(math.exp(term - top) for term in terms))


def count_rarity(
    times: list[Any], days: float, observed: int,
) -> dict[str, Any] | None:
    """Ventanas de `days` días del backtest con tan pocas operaciones como ahora.

    El gemelo de `window_rarity` para el **ritmo**: en vez de preguntar si lo
    ganado por operación es raro, pregunta si operar tan poco lo es. Las
    ventanas arrancan un día tras otro y van en su orden, así que traen puestos
    los parones de agosto, las rachas y la estacionalidad que un Poisson
    ignora.
    """
    if not times or days <= 0:
        return None
    span = int((times[-1] - times[0]).days - days) + 1
    if span < MINIMUM_WINDOWS:
        return None
    length = timedelta(days=days)
    worse = 0
    for offset in range(span):
        start = times[0] + timedelta(days=offset)
        # Medio abierta, `[inicio, inicio + dias)`: con los dos extremos
        # cerrados una ventana de treinta dias se comia la operacion del dia
        # treinta y uno, y el recuento no cuadraba con el esperado.
        inside = bisect_left(times, start + length) - bisect_left(times, start)
        if inside <= observed:
            worse += 1
    return {
        "windows": span, "worse": worse, "days": days,
        "p": (worse + 1) / (span + 1), "resolution": 1 / (span + 1),
    }


@dataclass(frozen=True)
class Backtest:
    """Lo que el informe del candidato aporta: cuánto y cuándo."""

    series: list[float]
    times: list[Any]


def trade_series(report: Any, reference_lot: float) -> list[float]:
    """Resultado de cada operación del backtest, llevado al lote de referencia.

    El mismo gesto que `observed_per_lot` hace con las del tester: comparar
    tramos de euros crudos mediría el volumen, no el comportamiento.
    """
    return [
        trade.profit_loss / trade.size * reference_lot
        for trade in report.trades if trade.size > 0
    ]


def backtest(source: Any, history: Any, reference_lot: float) -> Backtest | None:
    """El backtest del candidato, o `None` si el informe no se deja leer.

    El informe es el que apunta el propio `metrics_json`, así que la serie y la
    media contra la que se contrasta salen de la misma medición. Si lo que se
    parsea no tiene las operaciones que la memoria dice, es otro informe: no se
    mide con él. `_resolve_source_path` traduce la ruta del nodo Windows a este
    equipo, que es lo que permite medir desde el contenedor del manager.
    """
    stored = str(getattr(history, "report_path", "") or "")
    if not stored:
        return None
    try:
        report = cached_report(Path(_resolve_source_path(stored, source.project)))
        series = trade_series(report, reference_lot)
        times = sorted(trade.close_time for trade in report.trades if trade.size > 0)
    except WINDOW_ERRORS:
        return None
    if len(series) != getattr(history, "trades", 0):
        return None
    return Backtest(series=series, times=times)


def _evidence(
    row: dict[str, Any], compared: int, alpha: float, required: float,
) -> dict[str, Any]:
    """La parte de la prueba que no necesita abrir el informe del backtest."""
    deviations = float(row.get("deviations") or 0.0)
    trades = int(row.get("trades") or 0)
    return {
        "compared": compared, "alpha": alpha, "required": required,
        "normal_p": one_sided_p(deviations),
        "sequential_p": sequential_p(deviations, trades),
        "windows": None, "confirmed": False, "crossed": False, "blocked_by": [],
    }


def _rate(
    row: dict[str, Any], required: float, days: float,
    backtest_of: Callable[[dict[str, Any]], Backtest | None],
) -> None:
    """Si la estrategia opera mucho menos de lo que solía, y cuánto de raro es.

    Va aparte del veredicto a propósito. El beneficio medio es carísimo de
    medir —distinguir +0,77 € de 0 pide unas 600 operaciones, seis años al
    ritmo real—; el recuento no, y «ha dejado de operar» también es dejar de
    ser la estrategia que se eligió. Se mide hasta en las filas sin desvío:
    es justo la que deja de operar la que no llega a tener muestra.
    """
    expected = row.get("expected_trades")
    observed = int(row.get("trades") or 0)
    if not expected or expected <= 0 or days <= 0:
        # Sin días auditados no hay ritmo que medir. También es como la ventana
        # reciente pide que no se repita esta prueba: la serie entera tiene más
        # muestra, y dos recuentos del mismo hecho serían dos avisos.
        return
    screen = poisson_tail(observed, float(expected))
    row["rate"] = {
        "expected": float(expected), "observed": observed, "days": days,
        "screen_p": screen, "windows": None, "flagged": False, "required": required,
    }
    if screen > required:
        return
    found = backtest_of(row)
    rarity = count_rarity(found.times, days, observed) if found else None
    row["rate"]["windows"] = rarity
    row["rate"]["flagged"] = bool(rarity and rarity["p"] <= required)


def _blocks(rarity: dict[str, Any] | None, required: float) -> bool:
    """Si los tramos del backtest dicen que no, y pueden demostrarlo.

    La única excusa es un backtest corto: cuando **ningún** tramo fue igual o
    peor pero la serie no da para bajar del nivel exigido, la prueba ya ha
    dicho todo lo que podía y no se interpone. En cuanto algún tramo fue igual
    o peor, lo que dice vale por sí solo y no hay excusa de resolución.
    """
    if rarity is None or rarity["p"] <= required:
        return False
    return rarity["worse"] > 0 or rarity["resolution"] <= required


def _confirm_row(
    row: dict[str, Any], evidence: dict[str, Any],
    backtest_of: Callable[[dict[str, Any]], Backtest | None],
) -> None:
    """Decide una fila que ya cruzó el umbral, y anota qué prueba la frenó.

    Las dos pruebas se nombran, no sólo la primera: cuando las dos fallan, la
    de ventanas es la que se entiende sin estadística —«uno de cada quince
    tramos del propio backtest fue igual o peor»— y la secuencial es la que
    explica por qué el número de la pantalla no basta.
    """
    evidence["crossed"] = True
    required = evidence["required"]
    found = backtest_of(row)
    rarity = window_rarity(found.series if found else [],
                           float(row.get("observed_at_reference_lot") or 0.0),
                           int(row.get("trades") or 0))
    evidence["windows"] = rarity
    blocked = []
    if evidence["sequential_p"] > required:
        blocked.append("sequential")
    if _blocks(rarity, required):
        blocked.append("windows")
    evidence["blocked_by"] = blocked
    evidence["confirmed"] = not blocked
    if blocked:
        row["verdict"] = "warning"


def confirm(
    rows: list[dict[str, Any]], backtest_of: Callable[[dict[str, Any]], Backtest | None],
    sigma: float, days: float = 0.0,
) -> list[dict[str, Any]]:
    """Degrada a `AVISO` los cruces que la evidencia no sostiene, y mide el ritmo.

    El informe del backtest sólo se abre para las filas que ya cruzaron y para
    las que la criba de Poisson señala: son las únicas donde la respuesta
    cambia, y son una o ninguna en una vuelta normal del vigilante.

    El nivel se reparte entre las estrategias que **se han contrastado de
    verdad** —las que tienen desvío—, no entre todas las filas de la tabla:
    una con tres operaciones no ha gastado nivel ninguno.
    """
    alpha = alpha_for(sigma)
    comparable = [row for row in rows if row.get("deviations") is not None]
    required = alpha / len(comparable) if comparable else alpha
    for row in rows:
        _rate(row, required, days, backtest_of)
    for row in comparable:
        evidence = _evidence(row, len(comparable), alpha, required)
        row["evidence"] = evidence
        if row.get("verdict") == "degraded":
            _confirm_row(row, evidence, backtest_of)
    return rows


__all__ = [name for name in globals() if not name.startswith("_")]
