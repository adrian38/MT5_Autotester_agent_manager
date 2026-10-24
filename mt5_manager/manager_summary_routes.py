"""Las rutas del resumen mensual: el informe, su cita y la marca de leído.

Mismo patrón que `manager_schedule_routes`: funciones que reciben el handler.
Van juntas porque son el mismo material —el cron produce el informe y el panel
lo lee— y porque el panel pide las tres cosas en el mismo clic.

Todas son baratas: leen un JSON del disco del manager. La cara es
`/api/monthly-summary/run`, que recalcula un mes entero recorriendo el archivo
de auditorías; por eso es la única que es POST aunque no cambie ninguna
configuración, y por eso no la llama el panel al abrirse.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from . import monthly_summary_watch
from .monthly_summary_schedule import month_label, shift_month

SUMMARY_PATH = "/api/monthly-summary"
CONFIG_PATH = "/api/monthly-summary-config"
RUN_PATH = "/api/monthly-summary/run"
READ_PATH = "/api/monthly-summary/read"

_MONTH_LENGTH = 7


def _month(value: Any) -> str:
    """Un `YYYY-MM` válido, o cadena vacía. Nunca una fecha a medias.

    Sin esto, un `?month=2026` entraría como clave y devolvería un informe
    vacío que parece «ese mes no tuvo nada» en vez de «eso no es un mes».
    """
    text = str(value or "").strip()
    if len(text) != _MONTH_LENGTH or text[4] != "-":
        return ""
    year, _, month = text.partition("-")
    if not (year.isdigit() and month.isdigit() and 1 <= int(month) <= 12):
        return ""
    return text


def _requested_month(parsed: Any) -> str:
    query = urllib.parse.parse_qs(getattr(parsed, "query", "") or "")
    return _month((query.get("month") or [""])[0])


def _closed_month() -> str:
    """El último mes natural cerrado, que es lo que un cálculo a mano resume."""
    return shift_month(month_label(datetime.now(timezone.utc)), -1)


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path == SUMMARY_PATH:
        handler._send_json(
            200, handler.server.monthly_summaries.state(_requested_month(parsed)),
        )
        return True
    if parsed.path == CONFIG_PATH:
        handler._send_json(200, monthly_summary_watch.state(handler.server))
        return True
    return False


def _handle_run(handler) -> None:
    """Recalcula un mes a mano. Funciona con el interruptor apagado.

    Es una orden explícita, igual que «Lanzar recuperación ahora» del cron de
    auditoría: quien la pulsa está mirando el panel, así que no deja aviso en
    la campana —ya lo tiene delante— y tampoco toca la cuenta de reintentos
    del turno automático.
    """
    body = handler._body()
    label = _month(body.get("month")) or _closed_month()
    handler._send_json(200, {
        "report": monthly_summary_watch.run(handler.server, label, announce=False),
        **handler.server.monthly_summaries.state(label),
    })


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path == RUN_PATH:
        try:
            _handle_run(handler)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == READ_PATH:
        try:
            body = handler._body()
            handler._send_json(
                200, handler.server.monthly_summaries.mark_read(_month(body.get("month"))),
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == CONFIG_PATH:
        try:
            handler._send_json(
                200, handler.server.update_monthly_summary(handler._body()),
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    return False
