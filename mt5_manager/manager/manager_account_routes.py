"""Las rutas de la portada de cuentas y de su cron.

Mismo patrón que `manager_schedule_routes`: funciones que reciben el handler.
Va en su propio módulo y no ahí porque aquél ya ronda el techo de tamaño, y
porque son dos crones distintos: mezclarlos invitaría a compartir el registro,
que es justo lo que no comparten.

`/api/live-accounts` no consulta a ningún agente: lee la serie guardada y la
media que dejó el sondeo nocturno. La portada se repinta cada minuto y no puede
abrir la memoria del agente en cada vuelta.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from ..live_account import (
    live_account_alternate, live_account_dashboard, live_account_inventory,
    live_account_monthly, live_account_watch, live_account_wealth,
    live_account_wealth_watch,
)

ACCOUNTS_PATH = "/api/live-accounts"
ALTERNATE_PATH = "/api/live-account-alternate"
WEALTH_PATH = "/api/wealth"
WEALTH_SCHEDULER_PATH = "/api/wealth-scheduler-config"
WEALTH_NOW_PATH = "/api/wealth-snapshot-now"
SCHEDULER_PATH = "/api/live-account-scheduler-config"
SNAPSHOT_NOW_PATH = "/api/live-account-snapshot-now"
TOTAL_SELECTION_PATH = "/api/live-account-total-selection"


def _snapshot_now(handler) -> dict[str, Any]:
    """Lanza el sondeo a mano, en todos los nodos que tengan cuentas.

    Funciona con el interruptor apagado a propósito: es una orden del usuario,
    con alguien delante, que es la diferencia con una cita de madrugada. El
    agente sigue siendo quien decide si puede —`is_running` mira auditorías y
    sondeos—, así que esto nunca ocupa un terminal ya ocupado.
    """
    now = datetime.now(timezone.utc)
    server = handler.server
    launched: list[str] = []
    skipped: list[dict[str, str]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        if not live_account_inventory.node_accounts(server, node_id) and not (
            live_account_alternate.readings(server, node_id)
        ):
            continue
        state, issue, message = live_account_watch.node_snapshot(node, node_id)
        if state is None:
            skipped.append({"node_id": node_id, "reason": message or issue})
            continue
        if str(state.get("status") or "") in {"queued", "pausing", "extracting", "finalizing", "resuming"}:
            skipped.append({"node_id": node_id, "reason": "Ya hay un sondeo en marcha."})
            continue
        runs = server.live_account_runs
        record = runs.record(runs.key(node_id, live_account_watch.DAILY_KEY))
        live_account_watch.launch(
            server, node, node_id,
            str(record.get("window") or now.isoformat()),
            int(record.get("attempts") or 0) + 1, now,
        )
        launched.append(node_id)
    server.live_account_wakeup.set()
    return {"launched": launched, "skipped": skipped}


def _alternate(handler) -> dict[str, Any]:
    """Guarda o quita la cuenta que se sondea en una tarjeta.

    La tarjeta llega con la misma clave que usa «Sumar» (`<nodo>::<cuenta>`),
    que es la que la pantalla ya tiene en la mano. El agente va dentro del
    cuerpo porque es parte de lo que se configura: con qué terminal se entra.
    """
    body = handler._body()
    node_id, account_key = live_account_alternate.split_key(str(body.get("account") or ""))
    handler._node(node_id)
    store = handler.server.live_account_alternates
    if body.get("clear"):
        return store.remove(node_id, account_key)
    handler._node(str(body.get("node_id") or ""))
    return store.save(node_id, account_key, body)


def _handle_alternate_password(handler, parts: list[str]) -> bool:
    """Entrega al ojo la contraseña de una única tarjeta configurada."""
    if len(parts) != 3 or parts[:2] != ["api", "live-account-alternate-password"]:
        return False
    try:
        node_id, account_key = live_account_alternate.split_key(
            urllib.parse.unquote(parts[2])
        )
        handler._node(node_id)
        password = handler.server.live_account_alternates.revealed_password(
            node_id, account_key,
        )
        handler._send_json(200, {"password": password})
    except (KeyError, ValueError) as exc:
        handler._send_json(404, {"error": str(exc) or "No hay contraseña guardada"})
    return True


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    if _handle_alternate_password(handler, parts):
        return True
    if parsed.path == ACCOUNTS_PATH:
        handler._send_json(200, live_account_dashboard.snapshot(handler.server))
        return True
    # El patrimonio: la vida entera de cada cuenta, mes a mes. Va por su propia
    # ruta y no dentro de `/api/live-accounts` por lo mismo que el gráfico mes
    # a mes: la portada se repinta cada minuto y esto son todos los meses y
    # todos los apuntes de saldo de todas las cuentas.
    if parsed.path == WEALTH_PATH:
        handler._send_json(200, live_account_wealth.snapshot(handler.server))
        return True
    # La serie mes a mes de **una** cuenta: la pide el botón de su tarjeta al
    # abrirse, nunca el refresco de la portada. Son todos los movimientos de la
    # vida de la cuenta, así que no puede viajar cada minuto con las demás.
    if len(parts) == 5 and parts[:2] == ["api", "live-accounts"] and parts[4] == "monthly":
        try:
            node_id = urllib.parse.unquote(parts[2])
            handler._node(node_id)
            handler._send_json(200, live_account_monthly.monthly_history(
                handler.server, node_id, urllib.parse.unquote(parts[3]),
            ))
        except KeyError as exc:
            handler._send_json(404, {"error": str(exc)})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == SCHEDULER_PATH:
        handler._send_json(200, handler.server.live_account_scheduler_state())
        return True
    # La cita propia del patrimonio: la relectura completa, que es otra cosa
    # que el sondeo diario y por eso tiene su registro y su pantalla.
    if parsed.path == WEALTH_SCHEDULER_PATH:
        handler._send_json(200, live_account_wealth_watch.state(handler.server))
        return True
    return False


def _handle_stop(handler, parts: list[str]) -> None:
    """Le pide al agente que pare su sondeo, y traduce lo que conteste.

    Un 404 del nodo no es una ruta mal escrita: es un agente sin portar, y
    decirlo así es la diferencia entre reiniciarlo y buscar el fallo donde no
    está. Lo demás pasa tal cual, que es lo que el agente sabe y el manager
    no (no hay sondeo, las terminales ya se soltaron).
    """
    try:
        node_id = urllib.parse.unquote(parts[2])
        handler._send_json(200, live_account_watch.stop(
            handler.server, handler._node(node_id), node_id,
        ))
    except KeyError as exc:
        handler._send_json(404, {"error": str(exc)})
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    except (urllib.error.URLError, TimeoutError) as exc:
        handler._send_json(502, {"error": str(exc)})


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path == ALTERNATE_PATH:
        try:
            handler._send_json(200, _alternate(handler))
        except KeyError as exc:
            handler._send_json(404, {"error": str(exc)})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == TOTAL_SELECTION_PATH:
        try:
            state = handler.server.live_accounts.update_total_selection(handler._body())
            handler._send_json(200, state)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == SCHEDULER_PATH:
        try:
            handler._send_json(
                200, handler.server.update_live_account_scheduler(handler._body()),
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == SNAPSHOT_NOW_PATH:
        try:
            handler._send_json(202, _snapshot_now(handler))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    if parsed.path == WEALTH_SCHEDULER_PATH:
        try:
            handler._send_json(
                200, live_account_wealth_watch.update(handler.server, handler._body()),
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    # Parar el sondeo: por nodo, igual que se para una auditoría, porque lo
    # que se detiene es la tanda de **ese** agente y sus terminales.
    if (
        len(parts) == 6 and parts[:2] == ["api", "nodes"]
        and parts[3:] == ["live-accounts", "snapshot", "stop"]
    ):
        _handle_stop(handler, parts)
        return True
    if parsed.path == WEALTH_NOW_PATH:
        try:
            handler._send_json(202, live_account_wealth_watch.run_now(handler.server))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            handler._send_json(400, {"error": str(exc)})
        return True
    return False
