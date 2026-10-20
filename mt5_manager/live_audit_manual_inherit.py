"""Reaplicar a la ejecución nueva el reporte manual que corrigió a la anterior.

Lo único que descarta un reporte manual es que la ejecución nueva ya traiga el
100 % de calidad por su cuenta. Mientras el backtest siga saliendo con el
historial de ticks incompleto, la corrección que el usuario aportó una vez vale
y se vuelve a aplicar sola: el 20 de octubre de 2026 la primera noche del cron
dejó tres estrategias de XAUUSD otra vez al 40 % y la pantalla volvió a pedir
los tres HTML que ya se habían subido el 13.

No hay nada que portar al agente. Se reenvía el HTML guardado al mismo endpoint
que usa el navegador (`POST /api/v1/live-audits/<uso>/reports`), así que el nodo
lo valida como cualquier carga: nombre exacto del `.set`, símbolo, timeframe y
History Quality 100 %. Si algo no cuadra, el nodo lo rechaza igual que
rechazaría al usuario.

La ventana sí la comprueba este lado, porque el nodo no mira fechas: un reporte
de otra ventana se aplicaría tan campante y el veredicto compararía días que la
auditoría no cubre. Cuando no coincide se deja dicho en la ficha de la
estrategia, para que la pantalla lo cuente en vez de pedir el archivo a secas.
"""
from __future__ import annotations

import base64
import sys
import threading
import urllib.error
import urllib.parse
from typing import Any

from . import live_audit_manual_memory as manual_memory
from . import manager_http

MINIMUM_QUALITY = 99.995
"""El mismo umbral que exige el nodo para aceptar un reporte manual."""

MAXIMUM_ATTEMPTS = 3
"""Reintentos por ejecución y proceso: el agente puede estar apagado un rato,
pero un bucle de cargas contra un nodo que siempre falla no ayuda a nadie."""

MAXIMUM_REPORT_BYTES = 500_000
MAXIMUM_TOTAL_BYTES = 700_000
MAXIMUM_REPORTS = 20
"""Los mismos límites por carga que aplica el nodo. Se respetan aquí porque un
uso con muchas estrategias corregidas los pasa sin esfuerzo -siete HTML ya son
700 KB- y el nodo rechaza **el lote entero**: se manda en varias cargas."""

REAPPLY_ERRORS = (KeyError, ValueError, OSError, urllib.error.URLError, TimeoutError)

_attempts: dict[tuple[str, str, str], int] = {}
_attempts_lock = threading.Lock()


def _state(value: Any) -> dict[str, Any]:
    nested = value.get("audit") if isinstance(value, dict) else None
    return nested if isinstance(nested, dict) else (value if isinstance(value, dict) else {})


def _payload(value: Any) -> dict[str, Any]:
    payload = _state(value).get("last_payload")
    return payload if isinstance(payload, dict) else {}


def _quality(artifact: dict[str, Any]) -> float | None:
    value = artifact.get("history_quality_pct")
    return float(value) if isinstance(value, (int, float)) else None


def pending_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Las estrategias cuyo backtest no acredita el 100 % por sí mismo."""
    pending = []
    for artifact in payload.get("strategy_artifacts") or []:
        if not isinstance(artifact, dict) or not artifact.get("source_set"):
            continue
        if str(artifact.get("report_source") or "") == manual_memory.MANUAL_SOURCE:
            continue
        quality = _quality(artifact)
        if quality is not None and quality < MINIMUM_QUALITY:
            pending.append(artifact)
    return pending


def covers_window(record: dict[str, Any], span: tuple[str, str]) -> bool:
    """¿El backtest guardado abarca la ventana que se está auditando?

    No hace falta que sea la misma ventana: lo que se compara son operaciones,
    y el análisis ya se queda sólo con las de la ventana auditada. Un backtest
    más ancho sirve igual, y así una corrección manual aguanta las citas
    siguientes del cron en vez de caducar cada noche.

    Lo que no sirve es un backtest que no llegue a esas fechas: ahí no hay
    operaciones que aportar, y pedir el reporte otra vez es lo correcto.
    """
    start = str(record.get("backtest_start") or "")
    end = str(record.get("backtest_end") or "")
    if not start or not end:
        # Una ficha sin rango -un HTML que no se dejó interpretar- sólo se
        # hereda sobre la misma ventana para la que se aceptó.
        return (str(record.get("period_start") or ""), str(record.get("period_end") or "")) == span
    return start <= span[0][:10] and end >= span[1][:10]


def _claim(node_id: str, audit_key: str, execution_id: str) -> bool:
    key = (node_id, audit_key, execution_id)
    with _attempts_lock:
        if _attempts.get(key, 0) >= MAXIMUM_ATTEMPTS:
            return False
        _attempts[key] = _attempts.get(key, 0) + 1
    return True


def _upload(
    memory: Any, node_id: str, audit_key: str, artifact: dict[str, Any],
    span: tuple[str, str], execution_id: str,
) -> dict[str, str] | None:
    """El HTML guardado de esa estrategia, listo para reenviar, si procede."""
    source_set = str(artifact.get("source_set") or "")
    record = memory.lookup(node_id, audit_key, source_set)
    if not record or str(record.get("execution_id") or "") == execution_id:
        return None
    if not covers_window(record, span):
        artifact["manual_report_other_window"] = {
            "backtest_start": str(record.get("backtest_start") or ""),
            "backtest_end": str(record.get("backtest_end") or ""),
            "period_start": str(record.get("period_start") or ""),
            "period_end": str(record.get("period_end") or ""),
            "uploaded_at": str(record.get("uploaded_at") or ""),
        }
        return None
    try:
        content = memory.content(node_id, audit_key, source_set)
    except REAPPLY_ERRORS as exc:
        artifact["manual_report_error"] = str(exc)
        return None
    if len(content) > MAXIMUM_REPORT_BYTES:
        artifact["manual_report_error"] = (
            f"El reporte guardado ocupa {len(content)} bytes y el nodo acepta "
            f"{MAXIMUM_REPORT_BYTES} como máximo"
        )
        return None
    return {
        "filename": manual_memory.canonical_name(source_set),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "bytes": len(content),
    }


def reapply(node: dict[str, Any], node_id: str, memory: Any, value: Any) -> dict[str, Any] | None:
    """Devuelve el estado del nodo ya corregido, o `None` si no había nada que hacer."""
    payload = _payload(value)
    audit_key = str(
        payload.get("audit_key") or (payload.get("request") or {}).get("audit_key") or ""
    )
    execution_id = str(payload.get("audit_id") or "")
    if memory is None or not audit_key or not execution_id:
        return None
    span = manual_memory.window(payload)
    reports = [
        upload for upload in (
            _upload(memory, node_id, audit_key, artifact, span, execution_id)
            for artifact in pending_artifacts(payload)
        ) if upload
    ]
    if not reports or not _claim(node_id, audit_key, execution_id):
        return None
    return _send(node, node_id, audit_key, memory, reports)


def _batches(reports: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cargas que el nodo puede aceptar: ni más de 20 ni más de 700 KB."""
    batches: list[list[dict[str, Any]]] = []
    total = 0
    for report in reports:
        if (
            not batches or len(batches[-1]) >= MAXIMUM_REPORTS
            or total + report["bytes"] > MAXIMUM_TOTAL_BYTES
        ):
            batches.append([])
            total = 0
        batches[-1].append(report)
        total += report["bytes"]
    return batches


def _send(
    node: dict[str, Any], node_id: str, audit_key: str, memory: Any, reports: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Reenvía los reportes al nodo y anota la ejecución donde quedaron."""
    path = f"/api/v1/live-audits/{urllib.parse.quote(audit_key, safe='')}/reports"
    applied: dict[str, Any] | None = None
    for batch in _batches(reports):
        body = {"reports": [
            {"filename": report["filename"], "content_base64": report["content_base64"]}
            for report in batch
        ]}
        try:
            status, value = manager_http.node_request(node, "POST", path, body, timeout=120)
        except REAPPLY_ERRORS as exc:
            sys.stderr.write(f"[live-audit-manual] {node_id}/{audit_key}: no se pudo reaplicar: {exc}\n")
            break
        if status != 200:
            detail = value.get("error") if isinstance(value, dict) else value
            sys.stderr.write(
                f"[live-audit-manual] {node_id}/{audit_key}: el nodo rechazó la herencia: {detail}\n"
            )
            break
        memory.remember(node_id, audit_key, _payload(value))
        applied = _state(value)
        sys.stderr.write(
            f"[live-audit-manual] {node_id}/{audit_key}: {len(batch)} reporte(s) manual(es) "
            "reaplicados a la ejecución nueva.\n"
        )
    return applied
