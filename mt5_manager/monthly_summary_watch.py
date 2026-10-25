"""La vuelta del resumen mensual: calcularlo una vez al mes y anunciarlo.

Mismo reparto que `live_audit_watch` y `live_account_watch`, con una
diferencia que lo cambia todo: **aquí no hay nada que lanzar**. Aquellos dos
separan «recoger» de «lanzar» porque lanzar entra en un agente, le cambia la
cuenta a un terminal y le pausa el pipeline; éste sólo lee lo que ya está
archivado en el disco del manager. Por eso no lleva el candado doble: el
interruptor decide si el resumen se calcula solo, no si algo peligroso ocurre.

Lo que sí comparte es la política de turnos y reintentos, importada en vez de
reescrita: un turno nuevo siempre calcula y empieza con la cuenta a cero;
dentro del mismo turno sólo se repite lo que falló, con su espera y hasta
agotar los reintentos. Un cálculo falla cuando el disco no se deja leer, que
es raro pero no imposible, y entonces insistir una hora después es lo correcto.

**La cita vencida se recupera.** Si el manager estuvo apagado el día 1, la
primera vuelta después de arrancar calcula el resumen de ese mes igualmente:
un aviso que sólo existe si había alguien mirando no sirve para decidir sobre
el mes que cerró.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from . import monthly_summary_build, monthly_summary_notices
from .common import utc_now
from .live_audit_schedule import due_decision, parse_timestamp
from .monthly_summary_schedule import (
    month_name, next_occurrence, pending_window, previous_occurrence,
    schedule_description, schedule_label, resolve_zone, target_month,
)

KEY = "__monthly_summary__"
"""La única entrada del estado: el resumen es del manager entero, no de un uso."""

BUILD_ERRORS = (AttributeError, KeyError, OSError, TypeError, ValueError)
"""Lo que puede fallar al leer el archivo o la serie de una cuenta. Un resumen
que no se deja calcular no puede tumbar el hilo ni inventarse: deja aviso y se
reintenta."""

LINK = "/monthly_summary.html"
"""A dónde lleva el aviso: la pantalla del resumen, que es una ventana propia.

El aviso nombra su mes en el enlace (`?month=`) para que al llegar desde el
móvil tres días después se abra el mes que se anunció y no el último guardado,
que para entonces puede ser otro."""


def _announce(server: Any, report: dict[str, Any]) -> None:
    """Un aviso en la campana —y por tanto en el móvil— por resumen nuevo.

    Uno solo, y no uno por cuenta: el resumen existe justamente para que las
    doce tarjetas se lean de una vez. Lo que el aviso dice es cuántas piden
    decisión, que es lo que hace levantarse a mirarlo.
    """
    counts = report.get("counts") or {}
    change = int(counts.get(monthly_summary_notices.CHANGE) or 0)
    watch = int(counts.get(monthly_summary_notices.WATCH) or 0)
    unknown = int(counts.get(monthly_summary_notices.UNKNOWN) or 0)
    total = len(report.get("accounts") or [])
    parts = [
        f"{change} piden decisión" if change else "",
        f"{watch} a vigilar" if watch else "",
        f"{unknown} sin concluir" if unknown else "",
    ]
    detail = ", ".join(part for part in parts if part) or "nada que decidir"
    server.live_audit_notifications.add(
        "warning" if change else "info",
        f"Resumen de {month_name(str(report.get('month') or ''))}",
        f"{total} cuenta{'' if total == 1 else 's'} en real: {detail}.",
        kind="monthly_summary", link_text="Ver resumen",
        link=f"{LINK}?month={report.get('month') or ''}",
    )


def run(server: Any, label: str, announce: bool = True) -> dict[str, Any]:
    """Calcula el resumen de ese mes, lo guarda y lo anuncia. Devuelve el informe."""
    report = monthly_summary_build.build(server, label)
    stored = server.monthly_summaries.remember(report)
    if announce:
        _announce(server, stored)
    return stored


def _record_failure(server: Any, window: str, attempts: int, exc: Exception) -> None:
    message = f"No se pudo calcular el resumen del mes: {exc}"
    sys.stderr.write(f"[monthly-summary] {message}\n")
    server.monthly_summary_runs.update(
        KEY, window=window, attempts=attempts, outcome="failed",
        last_attempt_at=utc_now(), error=str(exc),
    )


def tick(server: Any) -> None:
    """Una vuelta: si la cita del mes está pendiente, calcula el resumen."""
    runs = getattr(server, "monthly_summary_runs", None)
    store = getattr(server, "monthly_summaries", None)
    if runs is None or store is None:
        return
    settings = dict(server.monthly_summary_settings)
    now = datetime.now(timezone.utc)
    record = runs.record(KEY)
    window, pending = pending_window(
        settings, parse_timestamp(record.get("completed_at")), now,
    )
    if not pending or not settings.get("enabled"):
        return
    decision = due_decision(settings, record, window, now)
    if decision == "exhausted":
        runs.update(KEY, window=window, outcome="exhausted")
        return
    if decision not in {"first", "retry"}:
        return
    attempts = (0 if decision == "first" else int(record.get("attempts") or 0)) + 1
    label = target_month(previous_occurrence(now, settings))
    try:
        run(server, label)
    except BUILD_ERRORS as exc:
        _record_failure(server, window, attempts, exc)
        return
    runs.update(
        KEY, window=window, attempts=attempts, outcome="completed",
        last_attempt_at=utc_now(), completed_at=utc_now(), month=label, error="",
    )


def state(server: Any) -> dict[str, Any]:
    """Contrato público del cron del resumen, con su alcance a la vista."""
    settings = dict(server.monthly_summary_settings)
    _zone, timezone_warning = resolve_zone(settings.get("timezone"))
    now = datetime.now(timezone.utc)
    record = server.monthly_summary_runs.record(KEY)
    upcoming = next_occurrence(now, settings)
    return {
        **settings,
        "schedule_label": schedule_label(settings),
        "description": schedule_description(settings),
        "next_run_at": upcoming.isoformat(),
        "next_month": target_month(upcoming),
        "previous_run_at": previous_occurrence(now, settings).isoformat(),
        "timezone_warning": timezone_warning,
        "last_month": str(record.get("month") or ""),
        "last_outcome": str(record.get("outcome") or ""),
        "last_error": str(record.get("error") or ""),
        "completed_at": str(record.get("completed_at") or ""),
        "months": server.monthly_summaries.months(),
        "unread": server.monthly_summaries.unread(),
    }
