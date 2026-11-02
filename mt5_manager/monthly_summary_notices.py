"""Los avisos del resumen mensual: lista cerrada, y cada uno una decisión.

Esto no es un informe: es lo que hay que mirar el día 1 para decidir si una
cartera sigue, se vigila o se cambia. Por eso la lista es **corta y cerrada**,
y por eso cada aviso lleva su veredicto pegado. Un panel que lo cuenta todo no
se lee, y lo que no se lee no decide nada.

Seis avisos, y el porqué de cada uno:

| Aviso | Cuándo sale | Qué decide |
| --- | --- | --- |
| `month_closed` | siempre, una línea | Nada por sí solo. Es el dato: el mes contra lo que da un mes como ése, con la banda de lo normal detrás. |
| `seasonal_shortfall` | el mes se sale por abajo de esa banda | **VIGILAR.** Rindió el 45 % de su octubre: ya no es un mes cualquiera. |
| `below_streak` | N meses seguidos por debajo | **CAMBIAR.** Un mes flojo es ruido; la racha es la evidencia. |
| `drawdown_breach` | la DD del mes sale de lo que el backtest hizo nunca | **CAMBIAR** si es inédita, **VIGILAR** si es extrema. |
| `strategies_degraded` | hay DEGRADA o cruces sin confirmar | **CAMBIAR esas estrategias**, no la cartera. |
| `partial_month` | el portafolio nació durante el mes | **VIGILAR** el tramo, sin compararlo con meses completos. |
| `coverage_gap` | falta el tramo auditable, o la serie está vieja | **SIN CONCLUSIÓN.** Tapa a los demás. |

**Una comparación que no se sostiene no se enseña: se dice por qué.** Con
autolote el lote lo decide el balance del mes, y la serie de meses del backtest
está medida a los lotes declarados y no se remide. `monthly_summary_build` lo
marca en `promise_comparable` y `table_comparable`, y aquí eso silencia el
recuento de «N fueron peores» y el drawdown inédito, poniendo en su sitio la
frase que explica el porqué. Un número que compara volúmenes se lee como si
comparase comportamiento, que es la peor clase de dato en una pantalla que
decide si se retira una cartera.

Lo que **no** está aquí, a propósito:

- **«Dejó de operar»** (`rate.flagged`). Se sigue midiendo y se sigue
  guardando en el estado del cron, pero el usuario lo dejó fuera del resumen
  el 2026-10-24: es una señal rápida que ya se ve en la tarjeta de la portada
  y aquí sólo añadiría una línea más que leer cada mes.
- **«La cuenta no reprodujo al tester»**. Sí está en el resumen, pero no se
  calcula aquí: ese veredicto vive en `static/live_audit_reproduction.js` y lo
  comparten Análisis y la portada. Portarlo a Python sería un segundo criterio
  para el mismo número, que es justo lo que ese fichero existe para evitar. El
  panel lo pinta con la función original. Detalle en
  `ai_context/monthly_summary.md`.

Aquí no hay red, ni ficheros, ni `server`: entran hechos ya medidos y salen
avisos. Es lo que permite probar el criterio entero sin montar un manager.
"""
from __future__ import annotations

from typing import Any

from .monthly_summary_schedule import month_name, month_plural, month_word

CRITERION_VERSION = "seasonal-band-lot-aware-v3"
"""El criterio con el que se escribieron los avisos de un informe guardado.

Cubre **todo** el criterio —lo que mide `monthly_summary_build` y lo que dice
este módulo—, así que se sube al cambiar cualquiera de los dos.

Hace falta porque un resumen se guarda hecho y la pantalla pinta sus cifras en
vivo: tras un despliegue, los recuadros son del criterio de hoy y las frases de
debajo las del día en que se calculó. Eso ya se ha leído dos veces como una
contradicción del panel —«Comparación histórica: No comparable» encima de un
drawdown inédito; «Octubres de backtest 6» encima de «de los 77 meses»— cuando
lo que pasaba es que faltaba pulsar «Volver a calcular». Una foto vieja tiene
que decir que lo es; si no, no hay manera de distinguirla de un fallo.
"""

CHANGE, WATCH, KEEP, UNKNOWN = "change", "watch", "keep", "unknown"

VERDICT_LABELS = {
    CHANGE: "CAMBIAR",
    WATCH: "VIGILAR",
    KEEP: "MANTENER",
    UNKNOWN: "SIN CONCLUSIÓN",
}

_SEVERITY = {UNKNOWN: 3, CHANGE: 2, WATCH: 1, KEEP: 0}

COVERAGE_MINIMUM_PCT = 75.0
"""Cuánto del mes hay que haber auditado para que el resumen se moje.

Tres cuartos del mes dejan sitio a la cita semanal que cae a mitad de semana
—hasta seis días sueltos— sin dejar pasar un mes auditado a medias. Por debajo
la tarjeta no dice MANTENER: dice que no concluye.
"""

NAMED_STRATEGIES = 3
"""Estrategias que un aviso nombra; del resto da el número. Igual que la
campana (`live_audit_degradation_alerts.NAMED_IN_MESSAGE`)."""


def amount(value: Any, currency: str = "") -> str:
    """Un importe como se escribe aquí: 1.234,56 € y no 1,234.56."""
    if value is None:
        return "sin medir"
    text = f"{float(value):,.2f}".replace(",", "\x00").replace(".", ",")
    return f"{text.replace(chr(0), '.')} {currency}".strip()


def worse_than(standing: dict[str, Any], noun: str) -> str:
    """«de los 72 meses del backtest, 9 fueron peores», o por qué no se dice."""
    if not standing.get("measured"):
        return f"El backtest no tiene {noun} suficientes para situarlo."
    worse, total = int(standing["worse"]), int(standing["total"])
    if worse == 0:
        return f"Ninguno de los {total} {noun} del backtest fue peor."
    return (
        f"De los {total} {noun} del backtest, "
        f"{'1 fue peor' if worse == 1 else f'{worse} fueron peores'}."
    )


def _notice(kind: str, level: str, verdict: str, title: str, message: str) -> dict[str, Any]:
    return {
        "kind": kind, "level": level, "verdict": verdict,
        "title": title, "message": message,
    }


AUTOLOT_PROMISE = (
    "La cuenta opera con autolote y el backtest no está remedido a los lotes "
    "que corrió este mes: no hay promesa a su tamaño con la que compararlo, "
    "así que el importe de arriba es el resultado y nada más."
)
"""Por qué un mes con autolote puede quedarse sin promesa que enseñar."""

AUTOLOT_TABLE = (
    "No se sitúa entre los meses del backtest: la promesa está al lote que el "
    "autolote corrió este mes, pero la serie histórica sigue al lote "
    "declarado, y contar contra ella compararía el tamaño de la posición y no "
    "el comportamiento."
)
"""Por qué el recuento de «N fueron peores» calla cuando hay autolote."""


def comparable(facts: dict[str, Any], key: str) -> bool:
    """Una marca de comparabilidad, tolerante con los resúmenes ya guardados.

    Un resumen es la foto de un mes y se relee tal cual se guardó: los de
    antes de medir los lotes no traen estas marcas, y ausente tiene que seguir
    significando lo que significaba entonces. Sólo un `False` explícito calla
    una comparación.
    """
    return facts.get(key) is not False


_TAIL = {
    "below": "por debajo de lo normal",
    "above": "por encima de lo normal",
    "normal": "dentro de lo normal",
}


def band_sentence(facts: dict[str, Any], currency: str) -> str:
    """Si el mes respeta la media de los suyos, dicho como se lee.

    No cuenta posiciones: dice qué da un octubre normal y qué dio éste. El
    porcentaje es el titular —«el 45 %» se entiende sin leer el resto— y la
    banda está detrás para que se vea de dónde sale el juicio.
    """
    band = facts.get("profit_band") or {}
    label = str(facts.get("month") or "")
    if not band.get("measured"):
        return (
            f"El backtest no tiene {month_plural(label)} suficientes para decir "
            "qué es normal en uno, así que el importe de arriba no se juzga."
        )
    tail = _TAIL.get(str(band.get("verdict") or ""), "sin situar")
    ratio = band.get("ratio")
    verdict = (
        f"Éste hizo el {float(ratio):.0f} %: {tail}." if ratio is not None
        else f"Éste quedó {tail}."
    )
    return (
        f"Un {month_word(label)} normal da entre {amount(band['low'])} y "
        f"{amount(band['high'], currency)} (media "
        f"{amount(band['mean'], currency)}, {band['total']} "
        f"{month_plural(label)} del backtest). {verdict}"
    )


def seasonal_shortfall(facts: dict[str, Any]) -> dict[str, Any] | None:
    """El mes se salió por abajo de lo que varía un mes como ése.

    Es el juicio que el usuario pidió el 2026-11-02 y decide por sí solo: no
    hay que esperar a la racha para que una cartera que rindió el 45 % de su
    octubre deje de ser «MANTENER». Por debajo de lo normal no es lo mismo que
    por debajo de la media —la mitad de los meses quedan por debajo de la
    media y no pasa nada—: es haberse salido de lo que esta cartera hace.

    Calla cuando la racha ya habla. Decir «este octubre fue flojo» debajo de
    «tres meses seguidos por debajo» no añade una decisión, añade una línea.
    """
    if facts.get("started_inside") or not comparable(facts, "table_comparable"):
        return None
    if int(facts.get("streak") or 0) >= int(facts.get("streak_required") or 3):
        return None
    band = facts.get("profit_band") or {}
    if not band.get("measured") or str(band.get("verdict")) != "below":
        return None
    label = str(facts.get("month") or "")
    ratio = band.get("ratio")
    share = f": {float(ratio):.0f} % de la media" if ratio is not None else ""
    return _notice(
        "seasonal_shortfall", "warning", WATCH,
        f"{month_word(label).capitalize()} por debajo de lo normal{share}",
        (
            f"No es sólo que quedara por debajo de la media: se salió de lo que "
            f"varía un {month_word(label)} de esta cartera. Un mes así no la "
            "retira —eso lo decide la racha—, pero es el primero que hay que "
            "mirar."
        ),
    )


def _closed_message(facts: dict[str, Any], currency: str) -> str:
    """Lo que se puede decir del mes sin comparar lo que no se deja comparar."""
    closed = facts.get("closed") or {}
    if facts.get("started_inside"):
        return (
            f"Drawdown desde el alta: {amount(closed.get('drawdown'), currency)}. "
            "Es el resultado real de ese tramo, pero no se compara con meses "
            "completos del backtest."
        )
    if not comparable(facts, "promise_comparable"):
        return AUTOLOT_PROMISE
    promise = (facts.get("expected") or {}).get("profit")
    if promise is None:
        return (
            "No hay media del backtest para este mes, así que no hay con qué "
            "compararlo."
        )
    if not comparable(facts, "table_comparable"):
        difference = float(closed.get("profit") or 0.0) - float(promise)
        return (
            f"Prometía {amount(promise, currency)} "
            f"({'+' if difference >= 0 else '−'}{amount(abs(difference), currency)}"
            f" de diferencia). {AUTOLOT_TABLE}"
        )
    return band_sentence(facts, currency)


def month_closed(facts: dict[str, Any]) -> dict[str, Any]:
    """El dato del mes: lo que entró y lo que prometía ese mes del backtest."""
    currency = str(facts.get("currency") or "")
    closed = facts.get("closed") or {}
    label = month_name(str(facts.get("month") or ""))
    title = f"{label}: {amount(closed.get('profit'), currency)}"
    if facts.get("started_inside"):
        start = str((facts.get("coverage") or {}).get("eligible_start") or "el alta")
        title = f"Desde {start}: {amount(closed.get('profit'), currency)}"
    settled = (
        not facts.get("started_inside")
        and comparable(facts, "promise_comparable")
        and (facts.get("expected") or {}).get("profit") is not None
        and not facts.get("below")
    )
    return _notice(
        "month_closed", "success" if settled else "info",
        "", title, _closed_message(facts, currency),
    )


def below_streak(facts: dict[str, Any]) -> dict[str, Any] | None:
    """La racha, que es lo que convierte un mal mes en una decisión."""
    if facts.get("started_inside"):
        return None
    months = int(facts.get("streak") or 0)
    needed = int(facts.get("streak_required") or 3)
    if months < needed:
        return None
    return _notice(
        "below_streak", "warning", CHANGE,
        f"{months} meses seguidos por debajo de lo prometido",
        (
            f"Un mes flojo es ruido; {months} seguidos ya no lo son. "
            "Es el momento de mirar si la cartera sigue siendo la que se "
            "eligió o toca mejorar su base."
        ),
    )


def drawdown_breach(facts: dict[str, Any]) -> dict[str, Any] | None:
    """La DD del mes contra lo que esta misma cartera hizo en el backtest.

    «Inédito» es una afirmación sobre la misma cartera al mismo tamaño. Sin la
    serie a los lotes que la cuenta corrió —con autolote nunca la hay— lo
    único que se mediría es cuánto creció la posición, así que calla.

    **Éste sí cuenta contra todos los meses, y lo dice.** El beneficio se sitúa
    entre los de su mes del año porque su promesa es estacional y tiene que
    compararse con la población que la produjo; la profundidad a la que cae
    esta cartera no tiene promesa estacional detrás, y seis octubres son seis
    datos con los que no se retira nada. Lo que no puede pasar es que la base
    quede implícita, así que el mensaje nombra cuántos meses son.
    """
    if facts.get("started_inside") or not comparable(facts, "table_comparable"):
        return None
    standing = facts.get("drawdown_standing") or {}
    verdict = str(standing.get("verdict") or "")
    if verdict not in {"beyond", "extreme"}:
        return None
    currency = str(facts.get("currency") or "")
    depth = (facts.get("closed") or {}).get("drawdown")
    worst = standing.get("worst")
    total = int(facts.get("backtest_months") or 0)
    scope = f"{total} meses" if total else "meses"
    if verdict == "beyond":
        return _notice(
            "drawdown_breach", "error", CHANGE,
            f"Drawdown inédito: {amount(depth, currency)}",
            (
                f"Más hondo que el peor de los {scope} del backtest "
                f"({amount(worst, currency)}): esta cartera nunca cayó tanto "
                "en un mes. No demuestra que esté rota, pero es el hecho que "
                "pide revisarla antes de seguir."
            ),
        )
    return _notice(
        "drawdown_breach", "warning", WATCH,
        f"Drawdown extremo: {amount(depth, currency)}",
        (
            f"Por encima del 95 % de los {scope} del backtest sin llegar al "
            f"peor ({amount(worst, currency)}). "
            f"{worse_than(standing.get('rank') or {}, 'meses')}"
        ),
    )


def _named(rows: list[dict[str, Any]]) -> str:
    def label(row: dict[str, Any]) -> str:
        parts = (str(row.get("symbol") or ""), str(
            row.get("strategy_set") or row.get("strategy") or "",
        ))
        return " · ".join(part for part in parts if part) or "una estrategia"
    named = "; ".join(label(row) for row in rows[:NAMED_STRATEGIES])
    rest = len(rows) - NAMED_STRATEGIES
    return f"{named} y {rest} más" if rest > 0 else named


def strategies_degraded(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Lo que ya decidió el criterio de degradación, traído al resumen.

    No se vuelve a medir: `live_audit_degradation_alerts` lo dejó archivado
    con su evidencia, y medirlo otra vez aquí sería un segundo criterio para
    el mismo veredicto.
    """
    degraded = list(facts.get("degraded") or [])
    crossing = list(facts.get("crossing") or [])
    if not degraded and not crossing:
        return None
    if degraded:
        return _notice(
            "strategies_degraded", "error", CHANGE,
            f"{len(degraded)} estrategia{'' if len(degraded) == 1 else 's'} degrada"
            f"{'' if len(degraded) == 1 else 'n'}",
            (
                f"{_named(degraded)}. Rinde por debajo de lo que siempre hizo, "
                "con la evidencia que exige el criterio. Lo que se cambia son "
                "esas estrategias, no la cartera entera."
            ),
        )
    return _notice(
        "strategies_degraded", "warning", WATCH,
        f"{len(crossing)} estrategia{'' if len(crossing) == 1 else 's'} en el límite",
        (
            f"{_named(crossing)}. Cruzó −2 σ pero la evidencia no lo confirma: "
            "con esta muestra todavía no hay base para retirarla."
        ),
    )


def _coverage_reasons(facts: dict[str, Any]) -> list[str]:
    """Todo lo que impide concluir sobre esta cuenta, dicho en una frase cada uno."""
    coverage = facts.get("coverage") or {}
    series = facts.get("series") or {}
    reasons: list[str] = []
    if facts.get("started_after_month"):
        reasons.append("el portafolio todavía no existía durante este mes")
    partial = bool(facts.get("started_inside"))
    pct_key = "eligible_pct" if partial else "pct"
    missing_key = "eligible_missing_days" if partial else "missing_days"
    missing = float(coverage.get(missing_key) or 0.0)
    if not coverage.get("audits"):
        reasons.append("ninguna auditoría cubre este mes")
    elif float(coverage.get(pct_key) or 0.0) < COVERAGE_MINIMUM_PCT:
        scope = "desde que nació" if partial else "del mes"
        reasons.append(f"quedan {missing:.0f} días {scope} sin auditar")
    if series.get("never_read"):
        reasons.append("la cuenta real nunca se ha sondeado")
    elif series.get("stale"):
        reasons.append(f"la serie de la cuenta se leyó el {series.get('updated_at', '')[:10]}")
    if not partial and (facts.get("expected") or {}).get("profit") is None:
        reasons.append("el backtest no tiene media para este mes")
    return reasons


def partial_month(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Conclusión prudente: el tramo es real, pero no comparable a un mes."""
    if not facts.get("started_inside"):
        return None
    coverage = facts.get("coverage") or {}
    start = str(coverage.get("eligible_start") or "la fecha de alta")
    return _notice(
        "partial_month", "warning", WATCH, "Mes parcial: VIGILAR",
        (
            f"El portafolio nació el {start} y las auditorías cubren el tramo "
            "exigible desde su alta. Beneficio y drawdown se miden sólo desde "
            "ese momento; no se decide MANTENER o CAMBIAR comparándolos con "
            "meses completos. El primer mes natural completo dará esa base."
        ),
    )


def coverage_gap(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Lo que falta por mirar. Tapa al resto: sin esto, un hueco parece calma."""
    reasons = _coverage_reasons(facts)
    if not reasons:
        return None
    return _notice(
        "coverage_gap", "warning", UNKNOWN, "El mes no está entero",
        (
            f"{'; '.join(reasons).capitalize()}. Los números de arriba son "
            "ciertos, pero miden menos mes del que parece: no decidas sobre "
            "esta cartera hasta cerrarlo."
        ),
    )


def recovery_needs(facts: dict[str, Any]) -> dict[str, bool]:
    """Qué evidencia puede obtener el botón sin inventar el resto del mes."""
    coverage = facts.get("coverage") or {}
    series = facts.get("series") or {}
    partial = bool(facts.get("started_inside"))
    permanent = bool(facts.get("started_after_month")) or (
        not partial and (facts.get("expected") or {}).get("profit") is None
    )
    pct_key = "eligible_pct" if facts.get("started_inside") else "pct"
    return {
        "account_snapshot": not permanent and bool(
            series.get("never_read") or series.get("stale")
        ),
        "audit": not permanent and float(coverage.get(pct_key) or 0.0)
        < COVERAGE_MINIMUM_PCT,
        "permanent": permanent,
    }


BUILDERS = (
    coverage_gap, partial_month, drawdown_breach, below_streak,
    seasonal_shortfall, strategies_degraded, month_closed,
)
"""El orden en que se leen: primero lo que impide concluir, después lo que
pide decisión, y el dato del mes al final. `month_closed` siempre sale."""


def notices(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Los avisos de una cuenta para ese mes, del que más pesa al que menos."""
    return [
        notice for notice in (builder(facts) for builder in BUILDERS)
        if notice is not None
    ]


def verdict(found: list[dict[str, Any]]) -> str:
    """El veredicto de la tarjeta: el más grave de sus avisos.

    SIN CONCLUSIÓN gana a CAMBIAR a propósito. Con medio mes sin auditar, un
    «cambia esta cartera» está sacado de la mitad de los datos, y el riesgo de
    retirar una cartera sana por falta de medición es tan real como el de
    mantener una rota.
    """
    verdicts = [
        str(notice.get("verdict") or "") for notice in found
        if notice.get("verdict")
    ]
    if not verdicts:
        return KEEP
    return max(verdicts, key=lambda value: _SEVERITY.get(value, 0))


def decided(cards: list[dict[str, Any]]) -> dict[str, int]:
    """Cuántas tarjetas hay de cada veredicto, para la chapa del botón."""
    counts = {key: 0 for key in VERDICT_LABELS}
    for card in cards:
        key = str(card.get("verdict") or KEEP)
        counts[key] = counts.get(key, 0) + 1
    return counts
