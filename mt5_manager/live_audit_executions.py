"""La ejecucion que publica el nodo, vista por el manager: nombre, veredicto y diario.

Los dos caminos que leen una ejecucion -la pantalla de Analisis y el vigilante
del cron- tienen que hacer **lo mismo** con ella, y por eso viven aqui las tres
piezas: como se llama el uso, como se convierte la materia prima en veredicto y
como se archiva ese veredicto en la serie historica.

Estaban separadas y se vio el 2026-10-20 con los datos reales: la ruta de
pantalla bajaba del agente el saldo historico del informe nativo de MT5 antes
de analizar, y el vigilante no. Con el autolote activo el analisis se niega -con
razon- a inventar el lote esperado sin ese saldo, asi que el archivado del
vigilante volvia en silencio y los usos con autolote (#109, #173, #170 y #148)
no entraron nunca en la serie. El nodo guarda **un** payload por uso, asi que
cada cita que pasaba borraba para siempre la semana anterior.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.parse
from typing import Any

from . import live_audit_analysis, live_audit_autolot, live_audit_manual_inherit, manager_http
from .common import safe_int

BALANCE_ERRORS = (KeyError, ValueError, OSError, urllib.error.URLError, TimeoutError)
"""Lo que puede fallar al bajar el informe nativo. No impide analizar: queda
anotado en el perfil y el analisis explica por que no hay lote esperado."""


def audit_label(profile: dict[str, Any], audit_id: str) -> str:
    name = str(profile.get("deployment_name") or "").strip()
    portfolio_id = safe_int(profile.get("portfolio_id"), 0, minimum=1)
    return name or (f"Portafolio #{portfolio_id}" if portfolio_id else f"Uso {audit_id}")


def _node_payload(value: Any) -> dict[str, Any]:
    state = value.get("audit") if isinstance(value, dict) and isinstance(
        value.get("audit"), dict,
    ) else value
    payload = state.get("last_payload") if isinstance(state, dict) else None
    return payload if isinstance(payload, dict) else {}


def balance_history(node: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    """El saldo historico del informe nativo de MT5 que el autolote necesita."""
    payload = _node_payload(value)
    if not payload:
        raise ValueError("La ejecución no publicó materia prima para calcular el autolote")
    report = payload.get("real_account_report") or {}
    filename = str(report.get("filename") or "")
    audit_key = str((payload.get("request") or {}).get("audit_key") or payload.get("audit_key") or "")
    execution_id = str(payload.get("audit_id") or "")
    if not filename or not audit_key or not execution_id:
        raise ValueError("La ejecución no publicó el HTML nativo necesario para calcular el autolote")
    encoded = [urllib.parse.quote(item, safe="") for item in (audit_key, execution_id, filename)]

    def load() -> bytes:
        status, body, _content_type = manager_http.node_artifact_request(
            node, f"/api/v1/live-audits/{encoded[0]}/artifacts/{encoded[1]}/{encoded[2]}",
            timeout=120,
        )
        if status != 200:
            raise ValueError(f"El nodo devolvió {status} al leer el HTML nativo para el autolote")
        expected_sha = str(report.get("sha256") or "").lower()
        if expected_sha and hashlib.sha256(body).hexdigest() != expected_sha:
            raise ValueError("El HTML nativo del autolote no coincide con la ejecución guardada")
        return body

    cache_key = str(report.get("sha256") or f"{audit_key}:{execution_id}:{filename}")
    return live_audit_autolot.cached_balance_history(cache_key, load)


def analysed_node_state(
    node: dict[str, Any], value: Any, profile: dict[str, Any],
    memory: Any = None, node_id: str = "",
) -> Any:
    """Aplica el criterio vigente del manager a la materia prima del nodo.

    Es la **unica** forma de analizar una ejecucion: con el autolote activo hay
    que bajar antes el saldo historico del agente, y un camino que se lo salte
    produce un analisis fallido en vez de un veredicto.

    Y es tambien donde se hereda el reporte manual, por lo mismo: la ejecucion
    nueva tiene que llegar corregida tanto a la pantalla como al archivado del
    vigilante, o la serie historica guardaria un veredicto peor del real.
    """
    if not live_audit_analysis.has_raw_material(value):
        return value
    fresh = live_audit_manual_inherit.reapply(node or {}, node_id, memory, value)
    if fresh is not None:
        value = (
            {**value, "audit": fresh}
            if isinstance(value, dict) and isinstance(value.get("audit"), dict) else fresh
        )
    profile = dict(profile)
    if (profile.get("autolot") or {}).get("enabled"):
        try:
            profile["_autolot_balance_history"] = balance_history(node or {}, value)
        except BALANCE_ERRORS as exc:
            profile["_autolot_balance_error"] = str(exc)
    return live_audit_analysis.analyse_node_state(value, profile)


def _execution_id(audit: dict[str, Any]) -> str:
    result = audit.get("last_result") if isinstance(audit.get("last_result"), dict) else {}
    payload = audit.get("last_payload") if isinstance(audit.get("last_payload"), dict) else {}
    return str(
        audit.get("audit_id") or payload.get("audit_id") or result.get("audit_id")
        or result.get("completed_at") or ""
    )


def _archive_entry(
    audit: dict[str, Any], profile: dict[str, Any], audit_key: str, execution_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "audit_key": audit_key, "execution_id": execution_id,
        "portfolio_id": safe_int(result.get("portfolio_id"), 0, minimum=0),
        "portfolio_type": str(result.get("portfolio_type") or ""),
        "deployment_name": str(profile.get("deployment_name") or ""),
        "executed_at": str(
            (result.get("analysis") or {}).get("executed_at") or result.get("completed_at") or ""
        ),
        "completed_at": str(result.get("completed_at") or ""),
        "period_start": str(result.get("period_start") or ""),
        "period_end": str(result.get("period_end") or ""),
        "currency": str((result.get("account") or {}).get("currency") or ""),
        "status": str(audit.get("status") or ""),
    }


def _archive_failure(
    server: Any, node: dict[str, Any], node_id: str, audit_key: str,
    profile: dict[str, Any], execution_id: str, reason: str,
) -> None:
    """Un archivado imposible deja rastro, una vez por ejecucion.

    Volver callando es lo que dejo la serie semanal a medias sin que nadie
    pudiera enterarse: el nodo sustituye su unico payload en la cita siguiente.
    """
    message = reason or "El análisis del manager no produjo una comparación que archivar."
    runs = getattr(server, "live_audit_runs", None)
    notifications = getattr(server, "live_audit_notifications", None)
    key = runs.key(node_id, audit_key) if runs is not None else ""
    if runs is not None and str(runs.record(key).get("archive_issue") or "") == execution_id:
        return
    sys.stderr.write(f"[live-audit-archive] {node_id}/{audit_key}: {message}\n")
    if runs is None or notifications is None:
        return
    runs.update(key, archive_issue=execution_id, archive_message=message)
    notifications.add(
        "warning", f"No se pudo archivar la auditoría: {audit_label(profile, audit_key)}",
        message, node_id=node_id, node_name=str(node.get("name") or node_id),
        audit_id=audit_key, portfolio_id=safe_int(profile.get("portfolio_id"), 0, minimum=0),
        kind="archive_failed",
    )


def archive_execution(
    server: Any, node: dict[str, Any], node_id: str, audit_key: str,
    audit: dict[str, Any], profile: dict[str, Any],
) -> None:
    """Guarda en el diario la ejecución terminada que el nodo enseña.

    Se guarda **el veredicto del manager**, no la materia prima: el Excel de
    atribución y las parejas decididas a mano ya están aplicados, y así la
    serie histórica enseña lo mismo que enseñó Análisis ese día. Por eso hace
    falta el `node`: con el autolote activo el veredicto no existe sin el saldo
    histórico que vive en el informe nativo del agente.

    Idempotente por ejecución, que es lo que la convierte también en la forma
    de rescatar lo que ya existía: la primera vuelta archiva la última
    auditoría de cada uso antes de que la siguiente la sustituya.
    """
    archive = getattr(server, "live_audit_archive", None)
    execution_id = _execution_id(audit)
    if archive is None or not execution_id:
        return
    if archive.knows(node_id, audit_key, execution_id):
        return
    analysed = analysed_node_state(
        node, {"audit": dict(audit)}, profile,
        memory=getattr(server, "live_audit_manual_memory", None), node_id=node_id,
    )
    state = (analysed or {}).get("audit") or {}
    result = state.get("last_result")
    if not isinstance(result, dict) or not result.get("comparison_detail"):
        _archive_failure(
            server, node, node_id, audit_key, profile, execution_id,
            str(state.get("analysis_error") or ""),
        )
        return
    archive.remember(
        node_id, _archive_entry(audit, profile, audit_key, execution_id, result),
        dict(result.get("comparison_detail") or {}),
    )
