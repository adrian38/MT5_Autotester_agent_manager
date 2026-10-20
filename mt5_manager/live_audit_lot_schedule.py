"""El lote de la cuenta real cuando el usuario lo cambia a mitad del periodo.

`real_strategy_lots` describe **un** lote por estrategia, y eso sólo vale si la
cuenta operó el mismo tamaño de principio a fin. En cuanto alguien lo sube a
mano deja de valer por los dos extremos:

- la banda de pertenencia aparta el cierre nuevo como ajeno —el `XAUUSD` de
  0,04 del 2026-10-16 contra una banda 0,01–0,03— y no llega ni a compararse;
- y subir el número configurado para admitirlo hace salir desviadas por volumen
  todas las operaciones anteriores al cambio, que ejecutaron el lote de antes.

Un tramo dice las dos cosas a la vez: **desde** cuándo rige un lote. El lote de
antes no se borra ni se sustituye; sigue describiendo su parte del periodo.

El autolote resuelve este mismo problema cuando el tamaño lo decide una fórmula
de riesgo sobre el saldo (`live_audit_autolot.py`). Esto es para cuando lo
decidió una persona: no hay fórmula que leer ni Excel que subir, y la fecha que
enlaza es la de la primera operación que llevó el lote nuevo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


MAX_SCHEDULED_STRATEGIES = 500
MAX_SCHEDULED_STEPS = 100
_MAX_KEY_LENGTH = 512
_FORBIDDEN = "\n\r"


def empty_lot_schedule() -> dict[str, Any]:
    return {}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_moment(value: Any) -> datetime | None:
    """El instante desde el que rige un tramo, siempre en UTC.

    El nodo publica las aperturas con `+00:00` y el HTML nativo las imprime sin
    sufijo; Python se niega a comparar un `datetime` con zona contra uno sin
    ella, así que aquí se normalizan los dos casos en vez de dejar que reviente
    al elegir el tramo vigente.
    """
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _normalize_step(strategy: str, raw: Any) -> dict[str, Any]:
    step = raw if isinstance(raw, dict) else {}
    moment = parse_moment(step.get("from"))
    if moment is None:
        raise ValueError(f"El tramo de lote de {strategy} necesita una fecha de inicio")
    try:
        lot = float(step.get("lot"))
    except (TypeError, ValueError):
        raise ValueError(f"El tramo de lote de {strategy} necesita un lote") from None
    if not 0.00000001 <= lot <= 1_000_000.0:
        raise ValueError(f"El lote del tramo de {strategy} está fuera de rango")
    marked_at = str(step.get("at") or "").strip()
    if len(marked_at) > 40 or any(char in marked_at for char in _FORBIDDEN):
        raise ValueError(f"La fecha de alta del tramo de {strategy} no es válida")
    return {"from": moment.isoformat(), "lot": round(lot, 8), "at": marked_at}


def normalize_lot_schedule(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Valida los tramos guardados y los deja ordenados por fecha.

    Ordenados aquí y no al leer: quien elige el tramo vigente recorre la lista
    de atrás hacia delante, y una lista desordenada le daría el lote de otro
    periodo sin que nada lo dijera.
    """
    if value is None:
        return empty_lot_schedule()
    if not isinstance(value, dict):
        raise ValueError("real_lot_schedule debe ser un objeto JSON")
    if len(value) > MAX_SCHEDULED_STRATEGIES:
        raise ValueError(
            f"real_lot_schedule admite como máximo {MAX_SCHEDULED_STRATEGIES} estrategias",
        )
    schedule: dict[str, list[dict[str, Any]]] = {}
    for raw_strategy, raw_steps in value.items():
        strategy = str(raw_strategy or "").strip()
        if not strategy or len(strategy) > _MAX_KEY_LENGTH or any(
            char in strategy for char in _FORBIDDEN
        ):
            raise ValueError("Cada tramo de lote debe nombrar una estrategia válida")
        steps = raw_steps if isinstance(raw_steps, list) else []
        if len(steps) > MAX_SCHEDULED_STEPS:
            raise ValueError(
                f"{strategy} no puede tener más de {MAX_SCHEDULED_STEPS} tramos de lote",
            )
        normalized = [_normalize_step(strategy, step) for step in steps]
        if normalized:
            schedule[strategy] = sorted(normalized, key=_step_moment)
    return schedule


def _step_moment(step: dict[str, Any]) -> datetime:
    return parse_moment(step.get("from")) or datetime.min.replace(tzinfo=timezone.utc)


def _steps(schedule: Any, strategy: str) -> list[dict[str, Any]]:
    """Los tramos de una estrategia, ordenados pase lo que pase.

    `normalize_lot_schedule` ya los ordena, pero quien elige el vigente se
    queda con el último de la lista: si alguna vez llega un mapa sin pasar por
    la normalización, devolvería el lote de otro periodo en silencio.
    """
    steps = (schedule or {}).get(str(strategy or ""))
    return sorted(
        (step for step in steps if isinstance(step, dict)), key=_step_moment,
    ) if isinstance(steps, list) else []


def scheduled_lot(schedule: Any, strategy: str, moment: Any) -> float | None:
    """El lote vigente para esa estrategia en ese instante, si hay tramo.

    Devuelve `None` —y no el primer tramo— cuando la operación es anterior a
    todos: antes del cambio mandaba el lote configurado de siempre, y darle el
    nuevo marcaría como desviadas las operaciones que la cuenta ejecutó bien.
    """
    comparable = parse_moment(moment)
    if comparable is None:
        return None
    vigente = [
        step for step in _steps(schedule, strategy) if _step_moment(step) <= comparable
    ]
    return float(vigente[-1]["lot"]) if vigente else None


def scheduled_lots(schedule: Any, strategy: str) -> list[float]:
    """Todos los lotes que esa estrategia llegó a operar, de menor a mayor.

    La banda de pertenencia los necesita **todos**, no sólo el vigente: es un
    prefiltro que se aplica al periodo entero y no sabe de qué instante es cada
    cierre. Quien decide si el volumen está bien es la comparación, que sí mira
    la apertura y usa `scheduled_lot`.
    """
    return sorted({float(step["lot"]) for step in _steps(schedule, strategy)})


def scheduled_steps(schedule: Any) -> int:
    """Cuántos tramos hay guardados, para que la metodología lo declare."""
    return sum(len(steps) for steps in (schedule or {}).values() if isinstance(steps, list))
