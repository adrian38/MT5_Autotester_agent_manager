"""Avisa cuando una estrategia deja de parecerse a su histórico.

La tabla «contra el histórico» de Análisis ya decidía que una estrategia
**DEGRADA**, pero había que abrir esa pantalla, uso por uso, para enterarse. El
veredicto que justifica retirar una estrategia era el único que no salía ni en
la portada ni en la campana.

Tres decisiones que no se deducen del código:

- **Se mide sobre el archivo, no sobre el nodo.** El material acumulado ya está
  en `live_audit_archive`, y el veredicto de la última ejecución también: volver
  a pedirle al agente su materia prima y reanalizarla sería repetir el trabajo
  que `archive_execution` acaba de hacer. Como efecto secundario, la primera
  vuelta después de un reinicio rescata sola la degradación de lo ya archivado,
  en vez de esperar a la cita siguiente.
- **Sólo avisa `DEGRADA`.** `AVISO` es, por definición del criterio,
  «mantener en vigilancia; aún no hay base suficiente para cambiarla»: llevarlo
  al móvil convertiría el aviso en ruido semanal. La portada sí lo cuenta.
- **Una estrategia se anuncia una vez.** Mientras siga degradando no se repite,
  porque una lista de avisos que no se puede vaciar deja de leerse
  (`ai_context/live_audit_reviewed_strategies.md`). Si sale de `DEGRADA` y
  vuelve a entrar, vuelve a avisar.

A diferencia de `record_transitions`, aquí la primera vuelta **sí** habla: «esta
estrategia degrada» es una afirmación sobre el estado de hoy, no una noticia de
que algo acaba de pasar, y callarla el primer día sería esconder justo lo que se
pidió enseñar.
"""
from __future__ import annotations

import sys
import urllib.parse
from typing import Any

from . import manager_live_audit_reports
from .common import safe_int, utc_now
from .live_audit_executions import audit_label

DEGRADED = "degraded"
"""El veredicto que dispara el aviso: `VERDICT_LABELS['degraded']` es DEGRADA."""

WATCHED = "warning"
"""`AVISO`: se cuenta en la tarjeta, no se lleva al dispositivo."""

NAMED_IN_MESSAGE = 3
"""Estrategias que el aviso nombra; del resto da el número."""

CRITERION = "evidence-2"
"""Con qué criterio se midió lo que hay guardado.

El candado es el `execution_id`, así que sin esto un cambio de criterio no
llega a la tarjeta hasta la auditoría siguiente: se quedaría enseñando un
DEGRADA que el criterio nuevo no firma, y una semana es mucho tiempo para una
fila que dice «retírala». Al cambiarlo, la primera vuelta tras el despliegue
vuelve a medir lo ya archivado, que es justo lo que hace la primera vuelta
después de reiniciar el manager."""

REPORT_ERRORS = (AttributeError, KeyError, OSError, TypeError, ValueError)
"""Lo que puede fallar al leer la memoria UBS del agente. Un histórico que no
se deja leer no puede tumbar la vuelta del vigilante ni inventar un veredicto:
se anota en el log y se vuelve a intentar en la ejecución siguiente."""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row(row: dict[str, Any]) -> dict[str, Any]:
    """Lo que la tarjeta necesita de una fila degradada, y nada más.

    El informe entero trae el histórico, las medias y la dispersión de cada
    estrategia: guardarlo engordaría el estado del cron sin que la portada
    pudiera enseñarlo.
    """
    return {
        "strategy": str(row.get("strategy") or ""),
        "strategy_set": str(row.get("strategy_set") or ""),
        "symbol": str(row.get("symbol") or ""),
        "verdict": str(row.get("verdict") or ""),
        "trades": safe_int(row.get("trades"), 0, minimum=0),
        "deviations": _number(row.get("deviations")),
        "difference_pct": _number(row.get("difference_pct")),
    }


def _crossed(row: dict[str, Any]) -> bool:
    """Si cruzó el umbral aunque la evidencia no lo confirmara."""
    evidence = row.get("evidence")
    return bool(isinstance(evidence, dict) and evidence.get("crossed"))


def _slow_row(row: dict[str, Any]) -> dict[str, Any]:
    """Lo que la tarjeta necesita de una que opera menos de lo que solía."""
    rate = row.get("rate") or {}
    return {
        **_row(row),
        "observed": safe_int(rate.get("observed"), 0, minimum=0),
        "expected": _number(rate.get("expected")),
    }


def _slow(row: dict[str, Any]) -> bool:
    rate = row.get("rate")
    return bool(isinstance(rate, dict) and rate.get("flagged"))


def flagged(report: dict[str, Any]) -> dict[str, Any]:
    """Lo que la portada tiene que saber de este informe.

    Tres listas y dos recuentos, porque son tres hechos distintos:

    - `degraded`: el veredicto que justifica retirar la estrategia. Es el
      único que va a la campana y al móvil.
    - `crossing`: cruzó −2 σ y la evidencia no lo confirmó. La tarjeta tiene
      que **nombrarlas**: desde que cruzar dejó de ser el veredicto, el
      recuento a secas las escondía —y sólo se pintaba si había alguna
      degradada, así que la portada no decía nada en absoluto—.
    - `slow`: opera mucho menos de lo que solía. No es el mismo hecho que
      rendir poco, y se ve mucho antes: dejar de operar también es dejar de
      ser la estrategia que se eligió.
    """
    rows = [row for row in report.get("strategies") or [] if isinstance(row, dict)]
    watched = [row for row in rows if str(row.get("verdict") or "") == WATCHED]
    return {
        "degraded": [_row(row) for row in rows if str(row.get("verdict") or "") == DEGRADED],
        "crossing": [_row(row) for row in watched if _crossed(row)],
        "slow": [_slow_row(row) for row in rows if _slow(row)],
        "watching": len(watched),
        "compared": len(rows),
    }


def _name(row: dict[str, Any]) -> str:
    label = str(row.get("strategy_set") or row.get("strategy") or "")
    symbol = str(row.get("symbol") or "")
    return " · ".join(part for part in (symbol, label) if part) or "una estrategia"


def _described(row: dict[str, Any]) -> str:
    parts = []
    deviations = row.get("deviations")
    if deviations is not None:
        parts.append(f"{deviations:+.1f} σ".replace(".", ","))
    trades = safe_int(row.get("trades"), 0, minimum=0)
    if trades:
        parts.append(
            "1 operación acumulada" if trades == 1
            else f"{trades} operaciones acumuladas"
        )
    return f"{_name(row)} ({' · '.join(parts)})" if parts else _name(row)


def _message(rows: list[dict[str, Any]]) -> str:
    named = "; ".join(_described(row) for row in rows[:NAMED_IN_MESSAGE])
    rest = len(rows) - NAMED_IN_MESSAGE
    extra = f" y {rest} más" if rest > 0 else ""
    return (
        f"{named}{extra}. Rinde por debajo de lo que esta estrategia siempre "
        "hizo: míralo en Análisis antes de seguir."
    )


def _last_archived(archive: Any, node_id: str, audit_key: str) -> dict[str, Any]:
    """La última ejecución archivada de ese uso; el índice va de vieja a nueva."""
    own = [
        entry for entry in archive.index(node_id)
        if str(entry.get("audit_key") or "") == str(audit_key)
    ]
    return own[-1] if own else {}


def _report(
    server: Any, node: dict[str, Any], node_id: str, audit_key: str,
    detail: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        return manager_live_audit_reports.accumulated_degradation(
            server, node, node_id, audit_key, {"comparison_detail": detail},
        )
    except REPORT_ERRORS as exc:
        sys.stderr.write(f"[live-audit-degradation] {node_id}/{audit_key}: {exc}\n")
        return None


def _announce(
    server: Any, node: dict[str, Any], node_id: str, audit_key: str,
    profile: dict[str, Any], rows: list[dict[str, Any]],
) -> None:
    link = (
        f"/live_audit_analysis.html?node={urllib.parse.quote(node_id)}"
        f"&audit={urllib.parse.quote(audit_key)}"
    )
    server.live_audit_notifications.add(
        "warning",
        f"Estrategia{'' if len(rows) == 1 else 's'} degradada"
        f"{'' if len(rows) == 1 else 's'}: {audit_label(profile, audit_key)}",
        _message(rows), node_id=node_id, node_name=str(node.get("name") or node_id),
        audit_id=audit_key, portfolio_id=safe_int(profile.get("portfolio_id"), 0, minimum=0),
        kind="strategy_degraded", link=link, link_text="Ver análisis",
    )


def review(
    server: Any, node: dict[str, Any], node_id: str, audit_key: str,
    profile: dict[str, Any],
) -> None:
    """Mira una vez cada ejecución archivada y avisa de lo que empeoró.

    Deja el resultado en el estado del cron, que es de donde lo lee la portada:
    el informe abre la memoria UBS del agente y la tarjeta se repinta cada
    minuto, así que calcularlo en cada sondeo sería abrir esa base sesenta veces
    por hora y por uso.
    """
    runs = getattr(server, "live_audit_runs", None)
    archive = getattr(server, "live_audit_archive", None)
    if runs is None or archive is None:
        return
    entry = _last_archived(archive, node_id, audit_key)
    identifier = str(entry.get("execution_id") or "")
    measured = f"{identifier}@{CRITERION}" if identifier else ""
    key = runs.key(node_id, audit_key)
    record = runs.record(key)
    if not measured or str(record.get("degradation_execution") or "") == measured:
        return
    report = _report(server, node, node_id, audit_key, archive.detail(node_id, entry))
    if report is None:
        return
    found = flagged(report)
    announced = {
        str(row.get("strategy") or "")
        for row in record.get("degradation") or [] if isinstance(row, dict)
    }
    runs.update(
        key, degradation_execution=measured, degradation=found["degraded"],
        degradation_crossing=found["crossing"], degradation_slow=found["slow"],
        degradation_watching=found["watching"], degradation_compared=found["compared"],
        degradation_checked_at=utc_now(),
    )
    fresh = [row for row in found["degraded"] if row["strategy"] not in announced]
    if fresh:
        _announce(server, node, node_id, audit_key, profile, fresh)


def stored(server: Any, node_id: str, audit_key: str) -> dict[str, Any]:
    """Lo último que se midió, tal como lo enseña la tarjeta de la portada."""
    runs = getattr(server, "live_audit_runs", None)
    record = runs.record(runs.key(node_id, audit_key)) if runs is not None else {}
    return {
        "strategies": [
            row for row in record.get("degradation") or [] if isinstance(row, dict)
        ],
        "crossing": [
            row for row in record.get("degradation_crossing") or [] if isinstance(row, dict)
        ],
        "slow": [
            row for row in record.get("degradation_slow") or [] if isinstance(row, dict)
        ],
        "watching": safe_int(record.get("degradation_watching"), 0, minimum=0),
        "compared": safe_int(record.get("degradation_compared"), 0, minimum=0),
        "checked_at": str(record.get("degradation_checked_at") or ""),
    }


__all__ = [name for name in globals() if not name.startswith("_")]
