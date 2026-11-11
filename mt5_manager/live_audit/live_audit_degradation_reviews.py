"""Las alarmas «contra el histórico» que el usuario ya miró, fuera de la lista.

`Portafolios activos` y el resumen del mes repetían auditoría tras auditoría las
mismas estrategias en `DEGRADA`, `AVISO FUERTE` u «opera menos», también las que
el usuario ya había abierto, analizado y decidido dejar como están. Es la misma
lección de `live_audit_reviewed_strategies.md`: una lista de avisos que no se
puede vaciar deja de leerse.

**Es su propia marca, no la de reproducción.** Validar en `Demo contra real`
dice «el real se parece al tester esta semana» y validar la serie dice «se
parece en todas»; esto dice «esta estrategia sigue haciendo lo de siempre», que
es otra pregunta sobre el mismo nombre. Una estrategia puede reproducir
perfectamente y estar degradándose, así que dejar que una marca valga por la
otra escondería justo la alarma que nadie ha mirado.

Dos ejes, porque son dos hechos distintos y la fila los enseña juntos:

- **El veredicto**, que sí se ordena: `AVISO` < `AVISO FUERTE` < `DEGRADA`. Una
  validada en aviso que cruza −2 σ a la semana siguiente vuelve sola.
- **Opera menos**, que no es un veredicto peor sino otra cosa. Se guarda aparte
  (`slow`): una validada cuando sólo rendía poco vuelve en cuanto además deja
  de operar.

Y como en la marca de reproducción, **empeorar sin cambiar de etiqueta también
la retira**: la marca guarda las sigmas que la fila tenía al validarla y
`SAME_LEVEL_MARGIN_SIGMA` es la holgura. No es cero a propósito: las sigmas se
recalculan en cada auditoría y se mueven solas sin que la estrategia haya
cambiado.

El criterio vive aquí, en Python, y no en la pantalla: lo decide el servidor al
montar el informe (`manager_live_audit_reports.accumulated_degradation` marca
cada fila) y al servir las alarmas (`live_audit_degradation_alerts.stored` las
esconde). Así la casilla de Análisis y lo que la portada deja de enseñar no
pueden discrepar, que es lo que pasaría con dos cálculos.
"""
from __future__ import annotations

from typing import Any

DEGRADED = "degraded"
"""`DEGRADA`: hay evidencia de deterioro. Es lo que justifica retirarla."""

CROSSED = "crossed"
"""`AVISO FUERTE`: cruzó −2 σ y la evidencia todavía no lo confirma."""

WARNING = "warning"
"""`AVISO` a secas: por debajo, sin cruzar el umbral."""

SEVERITY = {DEGRADED: 3, CROSSED: 2, WARNING: 1}
"""Lo mismo que ordena la tabla. Validar no silencia para siempre: la marca
deja de valer en cuanto la fila sube de nivel."""

REVIEW_LEVELS = frozenset(SEVERITY)

SAME_LEVEL_MARGIN_SIGMA = 0.5
"""Cuánto pueden caer las sigmas sin retirar la marca con el mismo veredicto.

Es el equivalente de `SAME_VERDICT_MARGIN_PCT` de `live_audit_reproduction.js`
en la unidad de esta tabla. Con tolerancia cero la primera re-ejecución
retiraría validaciones que nadie ha empeorado."""


def _sigma(value: Any) -> float | None:
    """Las sigmas de una fila o de una marca, o nada si no se pueden comparar."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    unusable = number != number or number in (float("inf"), float("-inf"))
    return None if unusable else number


def level_of(row: dict[str, Any]) -> str:
    """En qué nivel está esta fila del informe, o `""` si no avisa de nada.

    `AVISO FUERTE` no es un veredicto del servidor: es un `warning` cuya
    evidencia cruzó el umbral, y la tabla lo enseña aparte porque es lo que la
    portada nombra. Aquí hace falta distinguirlo o una validada en aviso
    taparía el cruce de la semana siguiente.
    """
    verdict = str(row.get("verdict") or "")
    if verdict == DEGRADED:
        return DEGRADED
    if verdict != WARNING:
        return ""
    evidence = row.get("evidence")
    crossed = isinstance(evidence, dict) and bool(evidence.get("crossed"))
    return CROSSED if crossed else WARNING


def is_slow(row: dict[str, Any]) -> bool:
    """Si la fila además opera mucho menos de lo que solía."""
    rate = row.get("rate")
    return isinstance(rate, dict) and bool(rate.get("flagged"))


def worse_within_level(mark: dict[str, Any], deviations: Any) -> bool:
    """Si cayó lo bastante como para retirar la marca sin cambiar de etiqueta."""
    before = _sigma(mark.get("deviations"))
    now = _sigma(deviations)
    if before is None or now is None:
        return False
    return now < before - SAME_LEVEL_MARGIN_SIGMA


def covers(mark: Any, level: str, deviations: Any) -> bool:
    """Si la marca tapa una alarma de ese nivel con esas sigmas.

    Mismo criterio que `LiveAuditReproduction.reviewed`: mejorar la mantiene
    fuera —ya se miró—, subir de nivel la retira, y con el mismo nivel decide
    la caída de sigmas.
    """
    if not isinstance(mark, dict) or not level:
        return False
    now = SEVERITY.get(level, 0)
    then = SEVERITY.get(str(mark.get("verdict") or ""), 0)
    if now != then:
        return now < then
    return not worse_within_level(mark, deviations)


def covers_slow(mark: Any) -> bool:
    """Si la marca se puso cuando la fila ya operaba menos."""
    return isinstance(mark, dict) and bool(mark.get("slow"))


def _mark_of(marks: Any, row: dict[str, Any]) -> Any:
    if not isinstance(marks, dict):
        return None
    return marks.get(str(row.get("strategy") or ""))


def reviewed_row(marks: Any, row: dict[str, Any]) -> bool:
    """Si esta fila entera está validada: su veredicto **y** su ritmo.

    Las dos cosas, porque la casilla es una sola y la fila las enseña juntas.
    Una validada cuando sólo rendía poco no puede tapar que además haya dejado
    de operar. Y una fila que ya no avisa de nada sigue marcada: que mejore la
    mantiene fuera, igual que en la tabla de reproducción.
    """
    mark = _mark_of(marks, row)
    if mark is None:
        return False
    level = level_of(row)
    if level and not covers(mark, level, row.get("deviations")):
        return False
    return not is_slow(row) or covers_slow(mark)


def annotate(rows: list[dict[str, Any]], marks: Any) -> list[dict[str, Any]]:
    """Deja en cada fila si está validada y con qué marca, para la pantalla.

    Se anota y no se esconde: Análisis sigue enseñando la fila con su color y
    su veredicto, porque lo que el usuario validó es que ya la miró.

    `review_level` viaja a propósito aunque la pantalla no lo pinte: es lo que
    ésta guarda al marcar la casilla, y deducirlo allí volvería a poner en el
    navegador el criterio que vive aquí.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        mark = _mark_of(marks, row)
        row["reviewed"] = reviewed_row(marks, row)
        row["review"] = dict(mark) if isinstance(mark, dict) else {}
        row["review_level"] = level_of(row)
        row["review_slow"] = is_slow(row)
    return rows


def _mark_text(value: Any, key: str, maximum: int) -> str:
    result = str(value or "").strip()
    if "\n" in result or "\r" in result:
        raise ValueError(f"{key} no puede contener saltos de línea")
    if len(result) > maximum:
        raise ValueError(f"{key} no puede superar {maximum} caracteres")
    return result


def _normalized_mark(strategy: str, mark: Any) -> dict[str, Any]:
    """Una marca validada: el nivel, el ritmo, las sigmas y cuándo se puso.

    El nivel se valida contra `SEVERITY`, como el veredicto de
    `reviewed_strategies` se valida contra el suyo: un cliente que grabara una
    etiqueta desconocida dejaría a la portada decidiendo «no ha empeorado»
    sobre algo que no sabe ordenar, o sea escondiendo la fila para siempre.
    `""` es legítimo y significa «no avisaba de nada cuando la miré»: la fila
    estaba marcada sólo porque operaba menos.
    """
    field = "reviewed_degradation_strategies"
    if not isinstance(mark, dict):
        raise ValueError(f"{field}[{strategy}] debe ser un objeto JSON")
    verdict = _mark_text(mark.get("verdict"), f"{field}[{strategy}].verdict", 32)
    if verdict and verdict not in REVIEW_LEVELS:
        raise ValueError(f"{field}[{strategy}].verdict no es un nivel conocido")
    slow = mark.get("slow")
    if not isinstance(slow, bool) and slow is not None:
        raise ValueError(f"{field}[{strategy}].slow debe ser true o false")
    normalized: dict[str, Any] = {
        "verdict": verdict,
        "at": _mark_text(mark.get("at"), f"{field}[{strategy}].at", 40),
        "slow": bool(slow),
    }
    raw = mark.get("deviations")
    if raw is not None and raw != "":
        deviations = _sigma(raw)
        if deviations is None:
            raise ValueError(f"{field}[{strategy}].deviations debe ser un número")
        normalized["deviations"] = deviations
    return normalized


def normalize_reviewed_degradation_strategies(value: Any) -> dict[str, dict[str, Any]]:
    """Valida las alarmas «contra el histórico» que el usuario dio por miradas.

    Campo propio y no el de reproducción: validar que el real se parece al
    tester no dice nada sobre si la estrategia sigue haciendo lo de siempre.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("reviewed_degradation_strategies debe ser un objeto JSON")
    if len(value) > 500:
        raise ValueError("No se pueden validar más de 500 estrategias")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_strategy, raw_mark in value.items():
        strategy = _mark_text(raw_strategy, "identificador de estrategia", 512)
        if not strategy:
            raise ValueError("Cada estrategia validada debe tener un identificador")
        normalized[strategy] = _normalized_mark(strategy, raw_mark)
    return normalized


def _kept(rows: Any, marks: Any, level: str) -> list[dict[str, Any]]:
    return [
        row for row in rows or []
        if isinstance(row, dict)
        and not covers(_mark_of(marks, row), level, row.get("deviations"))
    ]


def _kept_slow(rows: Any, marks: Any) -> list[dict[str, Any]]:
    return [
        row for row in rows or []
        if isinstance(row, dict) and not covers_slow(_mark_of(marks, row))
    ]


def hide(found: dict[str, Any], marks: Any) -> dict[str, Any]:
    """Las tres listas sin lo validado, y cuántas estrategias se escondieron.

    El recuento no sobra: la portada dice «sin avisos» y un «sin avisos» que
    además esconde tres filas afirma más de lo que sabe.
    """
    kept = {
        "strategies": _kept(found.get("strategies"), marks, DEGRADED),
        "crossing": _kept(found.get("crossing"), marks, CROSSED),
        "slow": _kept_slow(found.get("slow"), marks),
    }
    before = {
        str(row.get("strategy") or "")
        for key in kept
        for row in found.get(key) or [] if isinstance(row, dict)
    }
    after = {
        str(row.get("strategy") or "")
        for rows in kept.values() for row in rows
    }
    return {**found, **kept, "reviewed": len(before - after)}


__all__ = [name for name in globals() if not name.startswith("_")]
