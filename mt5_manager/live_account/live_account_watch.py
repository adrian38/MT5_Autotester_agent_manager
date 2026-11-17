"""La vuelta del sondeo de cuentas: recoger siempre, lanzar sólo con el interruptor.

Mismo reparto y mismas dos cautelas que `live_audit_watch`, por la misma razón:

1. **Recoger** es leer el estado del agente y meter en el archivo el sondeo que
   ya terminó. Son sólo GET y se hace siempre, porque el nodo guarda **un**
   sondeo y el siguiente lo sustituye: lo que no se recoja se pierde.
2. **Lanzar** sigue detrás del interruptor. Un sondeo entra en las cuentas
   reales, cambia la cuenta guardada de un terminal y pausa el pipeline: el
   2026-08-21 una ejecución desatendida dejó un terminal sin cuenta y dos días
   de discovery a cero.

Un agente por vuelta como mucho, y nunca dos sondeos a la vez en el mismo nodo:
el candado de verdad está en el agente (`is_running` mira auditorías y sondeos),
pero pedirlo igualmente evita contar como fallo un rechazo que ya sabíamos.
"""
from __future__ import annotations

import sys
import urllib.error
from datetime import datetime, timezone
from typing import Any

from . import (
    live_account_alternate,
    live_account_expectation_refresh,
    live_account_ingest,
    live_account_inventory,
    live_account_presentation,
    live_account_rates,
    live_account_report,
    live_account_schedule,
)
from ..manager import manager_http
from ..common import utc_now
from .live_account_ingest import DAILY_KEY, SNAPSHOT_KEY
from .live_account_snapshot import RUNNING_SNAPSHOT_STATUSES

_NODE_UNAVAILABLE_CONFIRMATION_POLLS = 3
"""Lo mismo que en el cron de auditoría: un timeout no es una desconexión."""

UNFINISHED_OUTCOMES = {"failed", "partial", "stale"}
"""Lo que deja la cita sin cumplir, aunque el agente diga que terminó.

`live_account_ingest.collect` escribe `completed_at` con el `finished_at` del
agente pase lo que pase, también cuando el sondeo vuelve con cuentas sin leer.
Sin esta lista, `pending_window` daba ese turno por cumplido y la política de
reintentos **ni siquiera se consultaba**: `retry_attempts` y
`retry_delay_minutes` sólo cubrían los fallos al lanzar —agente inalcanzable,
sin contraseña, HTTP de error—, que no son los que se ven. El 2026-10-22 el
sondeo de AXI volvió a las 23:06 con `60357303@Axi-US50-Live` sin leer y nadie
reintentó hasta que el usuario lo lanzó a mano a la mañana siguiente.

`exhausted` no está aquí a propósito: es el final del turno, no un fallo
pendiente, y meterlo volvería a lanzar el sondeo hasta la cita siguiente.
`stopped` tampoco, y por lo mismo visto del revés: lo paró el usuario, así que
volver a entrar en las terminales en cuanto se suelta el botón convertiría el
botón de parar en un botón de esperar un minuto.

`stale` sí está, y es el único que no viene de algo que saliera mal: la
captura llegó entera pero se presenta a otro lote que la serie guardada
(`live_account_ingest.ScaleChanged`). Hace falta otra lectura, de toda la
vida, así que la cita sigue pendiente; lo que no hace falta es gastar
reintentos, y por eso `collect` pone los intentos a cero en ese caso.
"""


def node_snapshot(
    node: dict[str, Any], node_id: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """El sondeo que el agente enseña y, si no se pudo leer, por qué.

    Un agente sin portar **no es un error de red**: contestó, y lo que le falta
    es el port y un reinicio. Se distingue porque el aviso es distinto y porque
    no espera tres vueltas para contarlo.
    """
    try:
        status, state = manager_http.node_request(node, "GET", "/api/v1/status", timeout=10)
        capabilities = (
            state.get("capabilities") if status == 200 and isinstance(state, dict) else {}
        )
        if not isinstance(capabilities, dict) or not capabilities.get("live_account_snapshot"):
            message = (
                "El agente no sabe sondear cuentas todavía: falta portar "
                "`/api/v1/live-accounts` a su `manager_node_runtime/` y reiniciarlo."
            )
            return None, "old_runtime", message
        status, value = manager_http.node_request(
            node, "GET", "/api/v1/live-accounts", timeout=15,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None, "unreachable", f"No se pudo consultar el agente: {exc}"
    if status != 200 or not isinstance(value, dict):
        return None, "http_error", f"La consulta del sondeo devolvió HTTP {status}: {value}"
    snapshot = value.get("snapshot")
    return (snapshot if isinstance(snapshot, dict) else {}), "", ""


def record_node_issue(
    server: Any, node: dict[str, Any], node_id: str, code: str, message: str,
) -> None:
    """Confirma la caída y avisa una vez, hasta que el agente vuelva."""
    runs = server.live_account_runs
    key = runs.key(node_id, SNAPSHOT_KEY)
    record = runs.record(key)
    previous = str(record.get("watch_issue") or "")
    failures = int(record.get("watch_failures") or 0)
    name = str(node.get("name") or node_id)
    if code:
        if previous:
            return
        failures += 1
        runs.update(key, watch_failures=failures)
        if code != "old_runtime" and failures < _NODE_UNAVAILABLE_CONFIRMATION_POLLS:
            return
        runs.update(key, watch_issue=code, watch_message=message)
        server.live_audit_notifications.add(
            "warning" if code == "old_runtime" else "error",
            f"El sondeo de cuentas no puede consultar {name}", message,
            node_id=node_id, node_name=name, kind="account_snapshot_node_unavailable",
        )
        return
    if not previous and not failures:
        return
    runs.update(key, watch_issue="", watch_message="", watch_failures=0)
    if previous:
        server.live_audit_notifications.add(
            "success", f"El sondeo de cuentas vuelve a ver {name}",
            "El agente ya responde y las citas pendientes vuelven a poder lanzarse.",
            node_id=node_id, node_name=name, kind="account_snapshot_node_recovered",
        )


def stop(server: Any, node: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Le pide al agente que pare el sondeo que tiene en marcha.

    El agente corta **entre cuentas** y sale por su camino de siempre, así que
    las terminales vuelven a su cuenta final y el pipeline que pausó se
    reanuda. Lo que ya leyó se recoge en la vuelta siguiente: la tanda termina
    con `stopped`, que no es un fallo y por eso no gasta reintentos ni vuelve
    a entrar en las terminales esta misma noche.
    """
    name = str(node.get("name") or node_id)
    status, value = manager_http.node_request(
        node, "POST", "/api/v1/live-accounts/snapshot/stop", {}, timeout=30,
    )
    if status == 404:
        raise ValueError(
            "El agente todavía no sabe detener un sondeo de cuentas: falta portar "
            "`/api/v1/live-accounts/snapshot/stop` a su `manager_node_runtime/` y "
            "reiniciarlo."
        )
    if status >= 400:
        raise ValueError(
            str(value.get("error") if isinstance(value, dict) else value)
            or f"El agente contestó HTTP {status}"
        )
    server.live_audit_notifications.add(
        "warning", f"Sondeo de cuentas detenido en {name}",
        "Lo pidió el usuario. El agente termina la cuenta que tenía abierta, "
        "devuelve cada terminal a su cuenta final y reanuda lo que pausó. No "
        "se reintenta hasta la próxima cita.",
        node_id=node_id, node_name=name, kind="account_snapshot_stopped",
    )
    server.live_account_wakeup.set()
    return value if isinstance(value, dict) else {}


LEVELS_BY_OUTCOME = {
    "success": "success", "stale": "info", "partial": "warning",
    "failed": "error", "stopped": "warning",
}
"""Con qué color sale cada final en la campana.

`stale` es `info` a propósito: no ha fallado nada, el sondeo leyó las cuentas y
lo único que pasa es que su escala ya no es la de la serie guardada. Pintarlo
de rojo manda a mirar un fallo que no existe.
"""


def announce_collected(
    server: Any, node: dict[str, Any], node_id: str, collected: dict[str, Any],
) -> None:
    """Un aviso por sondeo terminado, con lo que entró y lo que no.

    Es el «ya está» del sondeo: entre el clic y esto pasa el rato que el agente
    tarda en entrar y salir de cada cuenta, y hasta aquí no hay serie nueva que
    mirar. Va siempre, también cuando no entró nada.
    """
    name = str(node.get("name") or node_id)
    failed, stale = collected["failed"], collected.get("stale") or []
    period = " a ".join(part for part in collected["period"] if part) or "toda la vida"
    detail = (
        f"{collected['accounts']} cuenta(s) y {collected['movements']} cierre(s), "
        f"periodo {period}."
    )
    if stale:
        detail += (
            f" {len(stale)} cuenta(s) esperan a leerse enteras porque cambió el "
            "lote al que se presentan; se pide en la lectura siguiente."
        )
    if failed:
        detail += " Sin leer: " + "; ".join(failed[:4])
    outcome = str(collected.get("outcome") or "")
    server.live_audit_notifications.add(
        LEVELS_BY_OUTCOME.get(outcome, "warning"),
        f"Sondeo de cuentas {'detenido' if outcome == 'stopped' else 'terminado'}: "
        f"{name}", detail,
        node_id=node_id, node_name=name, kind="account_snapshot_collected",
    )


SNAPSHOT_PHASES = {
    "queued": (4, "EN COLA"), "pausing": (10, "PAUSANDO"),
    "extracting": (15, "LEYENDO CUENTAS"), "finalizing": (94, "RESTAURANDO"),
    "resuming": (97, "REANUDANDO"),
}
"""Qué parte del sondeo va en cada fase, y cómo se llama en la chapa.

El tramo de lectura es el largo —una cuenta tras otra, entrando y saliendo del
terminal— así que es el único que se reparte: de 15 a 90 por cuentas leídas.
Las otras fases son instantes, y darles un porcentaje fino sería inventarlo.
"""


def running_row(node: dict[str, Any], node_id: str = "") -> dict[str, Any] | None:
    """El sondeo que ese agente tiene en marcha, para la chapa de la cabecera.

    Un sondeo dura lo que tarde en entrar en nueve cuentas y pausa el pipeline
    mientras: sin esto, la cabecera está igual de quieta cuando no pasa nada
    que cuando el agente lleva veinte minutos dentro de las terminales. Es lo
    mismo que ya hacen la auditoría y la comprobación manual.
    """
    identifier = node_id or str(node.get("id") or "")
    state, _issue, _message = node_snapshot(node, identifier)
    if not isinstance(state, dict):
        return None
    status = str(state.get("status") or "")
    if status not in RUNNING_SNAPSHOT_STATUSES:
        return None
    accounts = state.get("accounts") or {}
    total = max(1, len(accounts))
    done = sum(
        1 for value in accounts.values()
        if str((value or {}).get("status") or "") == "completed"
    )
    base, label = SNAPSHOT_PHASES.get(status, (0, status.upper()))
    return {
        "node_id": identifier, "node_name": str(node.get("name") or identifier),
        "status": status, "status_label": label,
        "percent": round(base + 75 * done / total) if status == "extracting" else base,
        "progress_text": str(state.get("progress_text") or ""),
        "started_at": str(state.get("started_at") or ""),
        "done": done, "total": total,
    }


def _period(
    server: Any, found: list[live_account_alternate.Reading], now: datetime,
) -> tuple[str, str]:
    """El rango que se pide: el más atrasado de lo que este sondeo va a leer.

    Un sondeo trae **un** periodo para todas, así que manda la que va más
    atrás. Y una cuenta sin serie lo lleva a «toda la vida», que es lo que hace
    que su media y su mes anterior existan desde la primera noche.

    Se mide sobre las lecturas y no sobre las tarjetas del nodo por dos
    motivos: una tarjeta desviada guarda su serie bajo su propio nodo, y una
    tarjeta que no se puede leer —sin contraseña— no viaja en el sondeo, así
    que su serie vacía no tiene por qué pedir toda la vida en cada vuelta.
    """
    days: list[str] = []
    for reading in found:
        node_id, key = reading.owner_node_id, reading.account_key
        stored = server.live_accounts.read(node_id, key)
        expected = live_account_report.series_version(
            live_account_presentation.account_symbols(server, node_id, reading.account),
        )
        days.append(
            "" if expected and stored.get("normalization_version") != expected
            else server.live_accounts.last_day(node_id, key)
        )
    return live_account_schedule.snapshot_period(
        server.live_account_scheduler_settings, "" if not days or "" in days else min(days), now,
    )


def _launch_failed(
    server: Any, key: str, base: dict[str, Any], common: dict[str, Any],
    title: str, detail: str,
) -> None:
    """El mismo final para las tres formas de no poder sondear.

    Las tres dejan el turno como fallido y lo dicen una vez en la campana, y
    estaban escritas palabra por palabra en cada rama: la política de
    reintentos lee ese `outcome`, así que olvidarlo en una de ellas dejaría el
    turno dado por bueno sin que nadie hubiera leído nada.
    """
    server.live_account_runs.update(key, **base, outcome="failed")
    server.live_audit_notifications.add("error", title, detail, **common)


def launch(
    server: Any, node: dict[str, Any], node_id: str,
    window: str, attempts: int, now: datetime,
    period: tuple[str, str] | None = None, reason: str = "",
    cita: str = DAILY_KEY,
) -> None:
    """Pide el sondeo al agente y deja escrito cómo fue.

    Lo que se le pide leer no se recibe: se resuelve aquí, porque es lo mismo
    para quien lo lance —el cron, el botón o la cita del patrimonio— y porque
    una tarjeta desviada se lee desde el agente elegido y no desde el suyo
    (`live_account_alternate`).

    `period` lo impone quien llama; sin él lo decide `_period`, que pide lo
    justo desde el último día guardado. La relectura completa del patrimonio
    pasa `("", hoy)` porque necesita toda la vida de cada cuenta, y `reason`
    es lo que la campana dice en vez de la cadencia del cron diario: el aviso
    tiene que distinguir qué cita entró en las terminales.

    `cita` es **el registro de quien lanza**, y por eso lo pasa quien llama:
    la tanda es una por agente, pero el turno y los intentos son de su cita.
    La tanda guarda quién la pidió (`owner`) para que `collect` le cuente a
    ésa cómo acabó, y sólo a ésa (`live_account_ingest.DAILY_KEY`).
    """
    runs = server.live_account_runs
    key = runs.key(node_id, cita)
    name = str(node.get("name") or node_id)
    base = {"window": window, "attempts": attempts, "last_attempt_at": utc_now()}
    common = {"node_id": node_id, "node_name": name, "kind": "account_snapshot_launch"}
    restore = server.live_audit_settings.restore_credentials(node_id)
    found = live_account_alternate.readings(server, node_id)
    payload_accounts = live_account_alternate.payload(found)
    if not restore or not payload_accounts:
        _launch_failed(
            server, key, base, common, f"No se pudo sondear {name}",
            "Falta la cuenta final de terminales o la contraseña de las cuentas reales."
            if not restore else "Ninguna cuenta real tiene su contraseña guardada.",
        )
        return
    start, end = period if period is not None else _period(server, found, now)
    try:
        status, response = manager_http.node_request(
            node, "POST", "/api/v1/live-accounts/snapshot", {
                "snapshot_key": window, "period_start_date": start,
                "period_end_date": end, **restore, "accounts": payload_accounts,
            }, timeout=30,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        _launch_failed(server, key, base, common, f"No se pudo sondear {name}", str(exc))
        return
    if status >= 400:
        _launch_failed(
            server, key, base, common, f"El agente rechazó el sondeo de {name}",
            f"HTTP {status}: {response}",
        )
        return
    _launched(
        server, node_id, key, cita, base, common,
        len(payload_accounts), (start, end), reason,
    )


def _launched(
    server: Any, node_id: str, key: str, cita: str, base: dict[str, Any],
    common: dict[str, Any], accounts: int, period: tuple[str, str], reason: str,
) -> None:
    """El agente aceptó: queda escrito de quién es la tanda, y se dice.

    Dos registros y no uno: el turno y los intentos son de la cita que lanzó,
    y la tanda —que es una por agente— sólo guarda quién la pidió, para que
    `live_account_ingest.collect` le cuente a ésa cómo acabó.
    """
    runs = server.live_account_runs
    runs.update(key, **base, outcome="running", last_status="queued")
    runs.update(
        runs.key(node_id, SNAPSHOT_KEY),
        owner=cita, outcome="running", last_status="queued",
    )
    start, end = period
    server.live_audit_notifications.add(
        "info", f"Sondeo de cuentas lanzado en {common['node_name']}",
        f"{accounts} cuenta(s), periodo {start or 'desde el principio'} a {end} · "
        f"{reason or live_account_schedule.schedule_label(server.live_account_scheduler_settings)}",
        **common,
    )


def retry_record(record: dict[str, Any]) -> dict[str, Any]:
    """El registro como lo lee la política compartida de reintentos.

    `due_decision` vive en el cron de auditoría, donde un sondeo parcial no
    existe y donde `stopped` —la auditoría que paró el usuario— no se reintenta
    a propósito. Ensanchar allí el criterio cambiaría aquello, así que la
    traducción se hace aquí: para esa política, un sondeo incompleto es un
    sondeo fallido.
    """
    return (
        {**record, "outcome": "failed"}
        if str(record.get("outcome") or "") in UNFINISHED_OUTCOMES else record
    )


def completion_of(record: dict[str, Any], state: dict[str, Any]) -> datetime | None:
    """Cuándo quedó cumplida la cita de este nodo, o `None` si no lo está.

    El `finished_at` del nodo sólo cuenta si **hay** un sondeo detrás: en
    `idle` es el de una tanda que el agente ya olvidó al reiniciarse, y tomarlo
    por cumplido dejaría la cita de hoy sin lanzar. Y un sondeo que volvió
    incompleto no cumple la cita por mucho que el agente diga cuándo terminó.
    """
    if str(record.get("outcome") or "") in UNFINISHED_OUTCOMES:
        return None
    published = (
        state.get("finished_at")
        if str(state.get("status") or "idle") not in {"", "idle"} else ""
    )
    return live_account_schedule.parse_timestamp(
        record.get("completed_at") or published,
    )


def _accounts_eligible_since(accounts: list[dict[str, Any]]) -> datetime | None:
    """Desde cuándo hay al menos una cuenta rastreada en este nodo.

    Un nodo sin cuentas no entra en `tick` ([`tick`]), así que su primera
    cuenta nunca tuvo una cita anterior que cumplir. Sin esto, `pending_window`
    veía `previous_completion=None` y daba por vencida la última ocasión
    pasada aunque la cuenta acabara de llegar: el 2026-10-24 un portafolio
    nuevo en ICTrading lanzó el sondeo el sábado, fuera de lunes-viernes,
    porque la cita de viernes parecía incumplida desde siempre.

    Se toma el mínimo entre las `audit_ids` de todas las cuentas del nodo, no
    el máximo: una cuenta vieja basta para que la cita de hoy siga valiendo
    igual que antes, y sólo cuando *todas* son recientes se aplaza al próximo
    turno.
    """
    timestamps = [
        created for account in accounts
        for audit_id in account.get("audit_ids") or []
        if (created := live_account_schedule.audit_id_created_at(audit_id)) is not None
    ]
    return min(timestamps) if timestamps else None


def launch_due(
    server: Any, node: dict[str, Any], node_id: str, accounts: list[dict[str, Any]],
    state: dict[str, Any], now: datetime,
) -> None:
    """Lanza el sondeo de este nodo si le toca y no hay otro corriendo.

    `state` es la tanda que el agente enseña, y sólo cumple **esta** cita si
    es suya: la relectura del patrimonio entra en otras cuentas y pide otro
    periodo, así que su `finished_at` no vale como cita diaria cumplida. Sin
    dueño es de aquí, que es lo que había antes de que hubiera dos citas.
    """
    runs = server.live_account_runs
    settings = server.live_account_scheduler_settings
    key = runs.key(node_id, DAILY_KEY)
    record = runs.record(key)
    run = runs.record(runs.key(node_id, SNAPSHOT_KEY))
    mine = state if str(run.get("owner") or DAILY_KEY) == DAILY_KEY else {}
    window, pending = live_account_schedule.pending_window(
        settings, completion_of(record, mine), now, _accounts_eligible_since(accounts),
    )
    if not pending:
        if str(record.get("window") or "") != window:
            runs.update(key, window=window, attempts=0, outcome="success")
        return
    decision = live_account_schedule.due_decision(
        settings, retry_record(record), window, now,
    )
    if decision == "exhausted":
        runs.update(key, window=window, outcome="exhausted")
        server.live_audit_notifications.add(
            "error", f"Reintentos agotados: sondeo de {node.get('name') or node_id}",
            "El sondeo falló o volvió con cuentas sin leer en todos los intentos "
            "de este turno. No se vuelve a intentar hasta la próxima cita.",
            node_id=node_id, node_name=str(node.get("name") or node_id),
            kind="account_snapshot_exhausted",
        )
        return
    if not decision:
        return
    launch(
        server, node, node_id, window,
        int(record.get("attempts") or 0) + 1 if decision == "retry" else 1, now,
    )


def tick(server: Any, now: datetime | None = None) -> None:
    """Una vuelta: una lectura por nodo, recogida siempre, lanzamiento si toca.

    Y, cuando algún nodo ha entregado, el refresco del cambio de divisa. Va al
    final y no dentro del bucle porque es uno para todos los nodos: lo que se
    convierte son los totales de la portada y del patrimonio, no las cifras de
    un agente. `refresh_after_snapshot` se encarga de mirar una vez al día.
    """
    moment = now or datetime.now(timezone.utc)
    delivered = False
    for node in server.nodes:
        if server.live_account_stop.is_set():
            return
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        accounts = live_account_inventory.node_accounts(server, node_id)
        # Un agente sin tarjetas propias puede tener trabajo igual: una tarjeta
        # desviada hacia él se lee con sus terminales (`live_account_alternate`).
        if not accounts and not live_account_alternate.readings(server, node_id):
            continue
        state, issue, message = node_snapshot(node, node_id)
        record_node_issue(server, node, node_id, issue, message)
        if state is None:
            continue
        try:
            collected = live_account_ingest.collect(server, node, node_id, state)
        except Exception as exc:  # una recogida rota no puede parar la vuelta
            sys.stderr.write(f"[live-account-scheduler] {node_id}: {exc}\n")
            collected = None
        if collected is not None:
            delivered = True
            announce_collected(server, node, node_id, collected)
        live_account_expectation_refresh.refresh_expectations(
            server, node, node_id, accounts, moment,
        )
        if str(state.get("status") or "") in RUNNING_SNAPSHOT_STATUSES:
            continue
        if not server.live_account_scheduler_enabled or server.live_account_stop.is_set():
            continue
        launch_due(server, node, node_id, accounts, state, moment)
    if not delivered or server.live_account_stop.is_set():
        return
    try:
        live_account_rates.refresh_after_snapshot(server, moment)
    except Exception as exc:  # el cambio no puede tumbar la vuelta del sondeo
        sys.stderr.write(f"[live-account-scheduler] cambio de divisa: {exc}\n")


def targets(server: Any) -> dict[str, Any]:
    """A quién alcanza el sondeo hoy, sin preguntarle nada a ningún nodo.

    Lo enseña el diálogo antes de activarlo, igual que el del cron de
    auditoría: lo bloqueado se ve, en vez de no pasar nada el lunes a las 23:00
    sin que nadie sepa por qué.
    """
    scheduled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        record = server.live_account_runs.record(
            server.live_account_runs.key(node_id, SNAPSHOT_KEY),
        )
        if record.get("watch_issue"):
            issues.append({
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "message": str(record.get("watch_message") or "El agente no responde."),
            })
        for account in live_account_inventory.node_accounts(server, node_id):
            # El desvío manda sobre el bloqueo: una tarjeta sin contraseña en su
            # perfil se lee igual si se le guardó otra cuenta, y el diálogo
            # tiene que decir cuál se lee o enseña una cuenta que nadie abre.
            alternate = server.live_account_alternates.public(
                node_id, account["account_key"],
            )
            reason = "" if alternate["configured"] else account["block_reason"]
            row = {
                "node_id": node_id, "node_name": str(node.get("name") or node_id),
                "account_key": account["account_key"], "login": account["login"],
                "server": account["server"],
                "portfolios": [
                    portfolio.get("name") or f"#{portfolio.get('portfolio_id')}"
                    for portfolio in account["portfolios"]
                ],
                "block_reason": reason, "alternate": alternate,
                "last_day": server.live_accounts.last_day(node_id, account["account_key"]),
            }
            (blocked if reason else scheduled).append(row)
    return {"accounts": scheduled, "blocked": blocked, "scheduler_issues": issues}
