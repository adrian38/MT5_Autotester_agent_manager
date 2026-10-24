"""Los avisos del resumen mensual: lista cerrada, y cada uno una decisión.

Esto no es un informe: es lo que hay que mirar el día 1 para decidir si una
cartera sigue, se vigila o se cambia. Por eso la lista es **corta y cerrada**,
y por eso cada aviso lleva su veredicto pegado. Un panel que lo cuenta todo no
se lee, y lo que no se lee no decide nada.

Cinco avisos, y el porqué de cada uno:

| Aviso | Cuándo sale | Qué decide |
| --- | --- | --- |
| `month_closed` | siempre, una línea | Nada por sí solo. Es el dato: el mes contra lo que ese mes prometía, dicho contando meses del propio backtest. |
| `below_streak` | N meses seguidos por debajo | **CAMBIAR.** Un mes flojo es ruido; la racha es la evidencia. |
| `drawdown_breach` | la DD del mes sale de lo que el backtest hizo nunca | **CAMBIAR** si es inédita, **VIGILAR** si es extrema. |
| `strategies_degraded` | hay DEGRADA o cruces sin confirmar | **CAMBIAR esas estrategias**, no la cartera. |
| `coverage_gap` | falta mes por auditar, o la serie está vieja | **SIN CONCLUSIÓN.** Tapa a los demás. |

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

from .monthly_summary_schedule import month_name

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


def month_closed(facts: dict[str, Any]) -> dict[str, Any]:
    """El dato del mes: lo que entró y lo que prometía ese mes del backtest."""
    currency = str(facts.get("currency") or "")
    closed = facts.get("closed") or {}
    expected = facts.get("expected") or {}
    standing = (facts.get("profit_standing") or {}).get("overall") or {}
    promise = expected.get("profit")
    label = month_name(str(facts.get("month") or ""))
    if promise is None:
        message = (
            "No hay media del backtest para este mes, así que no hay con qué "
            "compararlo."
        )
    else:
        difference = float(closed.get("profit") or 0.0) - float(promise)
        message = (
            f"Prometía {amount(promise, currency)} "
            f"({'+' if difference >= 0 else '−'}{amount(abs(difference), currency)}"
            f" de diferencia). {worse_than(standing, 'meses')}"
        )
    return _notice(
        "month_closed",
        "success" if (promise is not None and not facts.get("below")) else "info",
        "", f"{label}: {amount(closed.get('profit'), currency)}", message,
    )


def below_streak(facts: dict[str, Any]) -> dict[str, Any] | None:
    """La racha, que es lo que convierte un mal mes en una decisión."""
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
    """La DD del mes contra lo que esta misma cartera hizo en el backtest."""
    standing = facts.get("drawdown_standing") or {}
    verdict = str(standing.get("verdict") or "")
    if verdict not in {"beyond", "extreme"}:
        return None
    currency = str(facts.get("currency") or "")
    depth = (facts.get("closed") or {}).get("drawdown")
    worst = standing.get("worst")
    if verdict == "beyond":
        return _notice(
            "drawdown_breach", "error", CHANGE,
            f"Drawdown inédito: {amount(depth, currency)}",
            (
                f"Más hondo que el peor mes del backtest "
                f"({amount(worst, currency)}): esta cartera nunca cayó tanto "
                "en un mes. No demuestra que esté rota, pero es el hecho que "
                "pide revisarla antes de seguir."
            ),
        )
    return _notice(
        "drawdown_breach", "warning", WATCH,
        f"Drawdown extremo: {amount(depth, currency)}",
        (
            f"Por encima del 95 % de los meses del backtest sin llegar al peor "
            f"({amount(worst, currency)}). "
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
    if facts.get("started_inside"):
        reasons.append(
            "el portafolio se guardó dentro de este mes, así que no tuvo el mes entero"
        )
    missing = float(coverage.get("missing_days") or 0.0)
    if not coverage.get("audits"):
        reasons.append("ninguna auditoría cubre este mes")
    elif float(coverage.get("pct") or 0.0) < COVERAGE_MINIMUM_PCT:
        reasons.append(f"quedan {missing:.0f} días del mes sin auditar")
    if series.get("never_read"):
        reasons.append("la cuenta real nunca se ha sondeado")
    elif series.get("stale"):
        reasons.append(f"la serie de la cuenta se leyó el {series.get('updated_at', '')[:10]}")
    if (facts.get("expected") or {}).get("profit") is None:
        reasons.append("el backtest no tiene media para este mes")
    return reasons


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


BUILDERS = (
    coverage_gap, drawdown_breach, below_streak, strategies_degraded, month_closed,
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
