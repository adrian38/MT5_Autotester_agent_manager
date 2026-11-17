"""La cita que relee la vida entera de cada cuenta, y su cola.

Dos cosas la separan del sondeo diario (`live_account_watch`), y las dos las
pidió el usuario el 2026-11-15:

1. **Lee el informe completo**, no los últimos días. Es lo único que trae todos
   los ingresos, las retiradas y los traspasos, que están repartidos por toda
   la vida de la cuenta; con la ventana de tres días del cron diario, el
   histórico no se completa nunca.
2. **Espera a que el agente esté libre.** Una relectura completa abre cada
   cuenta en una terminal y pausa el pipeline; lanzarla en mitad de unas
   pruebas es lo que dejó tres cuentas sin leer el 2026-11-15 («El terminal no
   tiene el historial de la cuenta»). Así que la cita vence y **se queda
   esperando**: cada vuelta mira si el agente sigue ocupado y entra en cuanto
   deja de estarlo, sin tocar lo que esté corriendo.

La espera tiene tope (`wait_hours`). Un agente que lleva día y medio ocupado no
está «ocupado un rato», y una cita que no caduca acabaría entrando el lunes a
media mañana sin que nadie la esperase.

Lo que esta cita **no** hace es recoger: de eso sigue encargándose
`live_account_ingest.collect` desde la vuelta del cron diario, porque el agente
guarda un solo sondeo y da igual quién lo haya pedido. Por eso aquí no hay
archivo ni serie: sólo decidir cuándo se pide y escribir cómo fue.
"""
from __future__ import annotations

import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import live_account_alternate, live_account_watch, live_account_wealth
from .live_account_ingest import SNAPSHOT_KEY
from .live_account_wealth import WEALTH_KEY  # noqa: F401  (API de este módulo)
from .live_account_wealth_schedule import (
    DEFAULT_WEALTH_SCHEDULER_SETTINGS, next_occurrence, normalize_wealth_scheduler_settings,
    pending_window, previous_occurrence, resolve_zone, schedule_description, schedule_label,
)
from ..common import load_json, save_json, utc_now
from ..manager import manager_http

BUSY_JOB_STATUSES = {"running", "stopping", "pausing", "resuming", "queued", "starting"}
"""Estados del trabajo del agente que significan «ahora no».

Se mira el trabajo entero y no sólo las pruebas: una generación, un arreglo o
una comprobación manual ocupan las mismas terminales.
"""


def configure(server: Any, settings_path: Path) -> None:
    """Deja la configuración leída en el servidor, junto a la del cron diario."""
    server.wealth_scheduler_path = settings_path.with_name("wealth_scheduler.json")
    try:
        stored = (
            load_json(server.wealth_scheduler_path)
            if server.wealth_scheduler_path.is_file() else {}
        )
        server.wealth_scheduler_settings = normalize_wealth_scheduler_settings(stored)
    except ValueError as exc:
        print(f"[wealth-scheduler] configuración ignorada: {exc}", flush=True)
        server.wealth_scheduler_settings = normalize_wealth_scheduler_settings({})


def update(server: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Persiste y aplica la cita sin reiniciar el manager."""
    normalized = normalize_wealth_scheduler_settings(
        changes, server.wealth_scheduler_settings,
    )
    save_json(server.wealth_scheduler_path, normalized)
    server.wealth_scheduler_settings = normalized
    server.live_account_wakeup.set()
    return state(server)


def _record(server: Any, node_id: str) -> dict[str, Any]:
    runs = server.live_account_runs
    return runs.record(runs.key(node_id, WEALTH_KEY))


def _completion(record: dict[str, Any]) -> datetime | None:
    """Cuándo se cumplió la última cita, si es que se cumplió."""
    stamp = str(record.get("completed_at") or "")
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def node_busy(server: Any, node: dict[str, Any], node_id: str) -> str:
    """Por qué el agente no puede ahora, o cadena vacía si puede.

    Una pregunta por vuelta y sólo cuando la cita está pendiente: el resto del
    tiempo esto no habla con ningún agente.
    """
    runs = server.live_account_runs
    if str(_record_of(runs, node_id).get("outcome") or "") == "running":
        return "ya hay un sondeo de cuentas en marcha"
    try:
        status, value = manager_http.node_request(node, "GET", "/api/v1/status", timeout=10)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return f"no se pudo preguntar al agente: {exc}"
    if status >= 400 or not isinstance(value, dict):
        return f"el agente contestó {status} al preguntarle si está libre"
    job = value.get("job") if isinstance(value.get("job"), dict) else {}
    kind = str(job.get("job_type") or "trabajo")
    if str(job.get("status") or "") in BUSY_JOB_STATUSES:
        return f"el agente está ocupado ({kind})"
    return ""


def _record_of(runs: Any, node_id: str) -> dict[str, Any]:
    return runs.record(runs.key(node_id, SNAPSHOT_KEY))


def _settle(server: Any, node_id: str, record: dict[str, Any]) -> bool:
    """Marca la cita cumplida cuando el sondeo que lanzó ya terminó.

    No se marca al lanzar: entre la petición y el informe hay minutos, y dar
    la cita por cumplida ahí dejaría sin reintento una relectura que se cayó
    por el camino.
    """
    if str(record.get("status") or "") != "launched":
        return False
    snapshot = _record_of(server.live_account_runs, node_id)
    if str(snapshot.get("outcome") or "") == "running":
        return False
    runs = server.live_account_runs
    runs.update(
        runs.key(node_id, WEALTH_KEY), status="done",
        completed_at=str(snapshot.get("completed_at") or utc_now()),
        outcome=str(snapshot.get("outcome") or ""),
    )
    return True


def _expired(record: dict[str, Any], settings: dict[str, Any], now: datetime) -> bool:
    """Si la espera de esta cita ya pasó de su tope."""
    stamp = str(record.get("waiting_since") or "")
    if not stamp:
        return False
    try:
        since = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return now - since > timedelta(hours=int(settings.get("wait_hours", 36)))


def _wait(server: Any, node_id: str, window: str, reason: str, now: datetime) -> None:
    """Deja escrito que la cita está en cola, y desde cuándo."""
    runs = server.live_account_runs
    record = runs.record(runs.key(node_id, WEALTH_KEY))
    since = str(record.get("waiting_since") or "")
    runs.update(
        runs.key(node_id, WEALTH_KEY), status="waiting", window=window,
        waiting_since=since if since and str(record.get("window") or "") == window
        else now.isoformat(),
        waiting_reason=reason, last_attempt_at=utc_now(),
    )


def _give_up(server: Any, node: dict[str, Any], node_id: str, window: str) -> None:
    """La espera se agotó: se dice en la campana y la cita se cierra."""
    runs = server.live_account_runs
    name = str(node.get("name") or node_id)
    runs.update(
        runs.key(node_id, WEALTH_KEY), status="expired", window=window,
        completed_at=utc_now(), outcome="expired",
    )
    server.live_audit_notifications.add(
        "warning", f"Relectura del patrimonio no hecha en {name}",
        "El agente no quedó libre dentro del plazo de espera, así que la cita "
        "se cierra sin leer. Se vuelve a intentar en la siguiente, o a mano "
        "desde «Sondeo del patrimonio».",
        node_id=node_id, node_name=name, kind="wealth_snapshot_expired",
        link="/patrimonio.html", link_text="Ver el patrimonio",
    )


def _launch(
    server: Any, node: dict[str, Any], node_id: str, window: str, now: datetime,
) -> None:
    """Pide la relectura completa y deja la cita a la espera de su informe."""
    runs = server.live_account_runs
    live_account_watch.launch(
        server, node, node_id, window, 1, now,
        period=("", now.astimezone(timezone.utc).date().isoformat()),
        reason="relectura completa para el patrimonio",
    )
    outcome = str(_record_of(runs, node_id).get("outcome") or "")
    runs.update(
        runs.key(node_id, WEALTH_KEY),
        status="launched" if outcome == "running" else "failed",
        window=window, launched_at=utc_now(), waiting_since="", waiting_reason="",
        completed_at="" if outcome == "running" else utc_now(),
    )


def summing_readings(server: Any, node_id: str) -> list[live_account_alternate.Reading]:
    """Lo que ese agente tiene que releer para el patrimonio.

    Las desmarcadas no cuentan: una relectura completa cuesta una terminal y
    nueve megas de informe por cuenta, y lo que no entra en el patrimonio no
    justifica ese precio.

    Son lecturas y no tarjetas porque una tarjeta desviada se relee en el
    agente que se le guardó: es la misma cuenta que sondea la portada, que es
    justo lo que el usuario pidió el 2026-11-17. Lo que decide si suma sigue
    siendo la tarjeta, así que la exclusión se mira por su nodo.
    """
    excluded = set(server.live_accounts.total_selection().get("excluded_accounts") or [])
    return [
        reading for reading in live_account_alternate.readings(server, node_id)
        if live_account_wealth.selection_key(
            reading.owner_node_id, reading.account_key,
        ) not in excluded
    ]


def consider(
    server: Any, node: dict[str, Any], node_id: str, now: datetime,
) -> None:
    """Una vuelta de esta cita en un nodo: cerrar, esperar o lanzar."""
    record = _record(server, node_id)
    if _settle(server, node_id, record):
        return
    if not server.wealth_scheduler_settings.get("enabled"):
        return
    if not summing_readings(server, node_id):
        return
    settings = server.wealth_scheduler_settings
    window, pending = pending_window(settings, _completion(record), now)
    if not pending or str(record.get("status") or "") == "launched":
        return
    reason = node_busy(server, node, node_id)
    if not reason:
        _launch(server, node, node_id, window, now)
        return
    _wait(server, node_id, window, reason, now)
    if _expired(_record(server, node_id), settings, now):
        _give_up(server, node, node_id, window)


def tick(server: Any, now: datetime | None = None) -> None:
    """La vuelta entera: un nodo detrás de otro, sin parar por uno roto."""
    moment = now or datetime.now(timezone.utc)
    for node in server.nodes:
        if server.live_account_stop.is_set():
            return
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        consider(server, node, node_id, moment)


def run_now(server: Any) -> dict[str, Any]:
    """La relectura a mano. Respeta la cola: si el agente está ocupado, no entra.

    Es una orden del usuario, con alguien delante, así que funciona con el
    interruptor apagado; lo que no hace es saltarse la espera, porque el
    motivo de la espera —no entrar en mitad de unas pruebas— no cambia porque
    la pida una persona.
    """
    now = datetime.now(timezone.utc)
    launched: list[str] = []
    skipped: list[dict[str, str]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id or not summing_readings(server, node_id):
            continue
        reason = node_busy(server, node, node_id)
        if reason:
            skipped.append({"node_id": node_id, "reason": reason})
            continue
        _launch(server, node, node_id, now.isoformat(), now)
        launched.append(node_id)
    server.live_account_wakeup.set()
    return {"launched": launched, "skipped": skipped}


def _node_state(server: Any, node: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Cómo va la cita en ese nodo, sin preguntarle nada al agente."""
    record = _record(server, node_id)
    return {
        "node_id": node_id, "node_name": str(node.get("name") or node_id),
        "accounts": len(summing_readings(server, node_id)),
        "status": str(record.get("status") or ""),
        "window": str(record.get("window") or ""),
        "waiting_since": str(record.get("waiting_since") or ""),
        "waiting_reason": str(record.get("waiting_reason") or ""),
        "launched_at": str(record.get("launched_at") or ""),
        "completed_at": str(record.get("completed_at") or ""),
        "outcome": str(record.get("outcome") or ""),
    }


def state(server: Any) -> dict[str, Any]:
    """Contrato público de la cita, con su estado por nodo."""
    settings = dict(getattr(server, "wealth_scheduler_settings", None)
                    or DEFAULT_WEALTH_SCHEDULER_SETTINGS)
    _zone, timezone_warning = resolve_zone(settings.get("timezone"))
    now = datetime.now(timezone.utc)
    return {
        **settings,
        "schedule_label": schedule_label(settings),
        "description": schedule_description(settings),
        "next_run_at": next_occurrence(now, settings).isoformat(),
        "previous_run_at": previous_occurrence(now, settings).isoformat(),
        "timezone_warning": timezone_warning,
        "nodes": [
            _node_state(server, node, str(node.get("id") or ""))
            for node in server.nodes if str(node.get("id") or "")
        ],
    }
