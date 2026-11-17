"""Lo que el manager hace con un sondeo que ya terminó en el agente.

El nodo publica materia prima —un informe nativo por cuenta— y **un solo
sondeo**: el siguiente sustituye su estado. Así que lo que no se recoja aquí se
pierde, y es la misma trampa que dejó sin archivar las auditorías con autolote
(`ai_context/live_audit_cron_and_bell.md`). Por eso la recogida es idempotente
por `snapshot_id` y la primera vuelta tras arrancar el manager rescata lo que
encuentre.

Lo que se recoge es **la serie de cada cuenta**, bajando su informe,
comprobando el sha256 que el agente publicó y metiéndola en el archivo. La
otra cosa que la vuelta del cron recalcula —la media estacional de cada
portafolio, que no sale del sondeo sino de los informes del backtest en la
memoria del agente— vive en `live_account_expectation_refresh`, que salió de
aquí al llegar este fichero al techo de 600 líneas.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.parse
from typing import Any

from . import live_account_alternate, live_account_presentation, live_account_report
from ..manager import manager_http
from ..common import utc_now
from .live_account_snapshot import RUNNING_SNAPSHOT_STATUSES


class ScaleChanged(ValueError):
    """La captura ya no sirve: la serie está a otro lote que el de ahora.

    No es un fallo del agente ni de la red: es que entre el lanzamiento y la
    recogida cambió a qué lote se presenta la cuenta, y pegar ese tramo encima
    dejaría la serie con dos escalas. Se cuenta aparte de los fallos porque
    gasta un reintento de balde y porque el aviso tiene que decir otra cosa:
    lo que hace falta es **otra lectura**, de toda la vida, no insistir con
    ésta.
    """


INGEST_ERRORS = (
    AttributeError, KeyError, OSError, TypeError, ValueError,
    urllib.error.URLError, TimeoutError,
)
"""Lo que puede fallar al bajar o leer un informe. Una cuenta que no se deja
leer no tumba la recogida de las demás: queda su aviso y se reintenta."""

SNAPSHOT_KEY = "__snapshot__"
"""La clave de **la tanda** que corre en el agente: es por nodo, no por cuenta.

Un sondeo lee todas las cuentas del agente de una vez, así que el resultado es
del nodo. Y el agente guarda **una** tanda, así que este registro es uno: aquí
viven el `ingested`, el estado que devolvió y si se le ve o no (`watch_*`).

Lo que **no** vive aquí es la cita que la pidió. De eso va `owner`.
"""

DAILY_KEY = "__daily__"
"""La clave de la cita del sondeo diario: su ventana, sus intentos, su final.

Hasta el 2026-11-17 la cita diaria no tenía registro propio y usaba el de la
tanda, que es el único que hay por nodo. Como la relectura del patrimonio
también lanza por `live_account_watch.launch`, las dos citas escribían en el
mismo sitio y la del diario se quedaba con lo que hiciera la otra:

- la relectura de AXI volvió con una cuenta sin leer y ese `partial` pasó por
  fallo **del diario**, que reintentó a las 11:16 y a las 11:26 con su periodo
  de tres días, volviendo a entrar en las terminales cada diez minutos;
- la de RoboForex volvió entera y dio por cumplida la cita de la noche, que
  nunca llegó a hacer su propia pasada.

Son dos poblaciones y dos contenidos —el diario entra en las cuentas de los
portafolios activos y reescribe los últimos `overlap_days`; la relectura entra
en las marcadas «Sumar» y pide la vida entera—, así que cada una lleva su
cuenta de intentos y se entera sólo de la tanda que pidió ella.
"""


def _artifact(node: dict[str, Any], snapshot_id: str, filename: str, expected: str) -> bytes:
    path = "/api/v1/live-accounts/{}/artifacts/{}".format(
        urllib.parse.quote(str(snapshot_id), safe=""),
        urllib.parse.quote(str(filename), safe=""),
    )
    status, body, _content_type = manager_http.node_artifact_request(node, path, timeout=180)
    if status != 200:
        raise ValueError(f"El nodo devolvió {status} al leer {filename}")
    if expected and hashlib.sha256(body).hexdigest() != str(expected).lower():
        raise ValueError(f"El informe {filename} no coincide con el que publicó el agente")
    return body


def _merge_account(
    server: Any, node: dict[str, Any], node_id: str, snapshot_id: str,
    account_key: str, row: dict[str, Any], period: tuple[str, str],
    symbol_lots: dict[str, float],
) -> int:
    """Archiva una cuenta leída en la serie de **su tarjeta**.

    `node` es quien la leyó y de quien se baja el informe; `node_id` es de
    quién es la tarjeta. Con una cuenta desviada a otro agente no son el
    mismo, y archivar bajo el lector dejaría a la portada y al patrimonio sin
    ver nada: la serie estaría guardada donde nadie la busca.
    """
    version = live_account_report.series_version(symbol_lots)
    stored = server.live_accounts.read(node_id, account_key)
    first_day = str(stored.get("first_day") or "")
    fully_rewritten = not first_day or not period[0] or period[0] <= first_day
    # Antes de bajar el informe: si hay que rechazarlo, no hay que bajarlo.
    if not fully_rewritten and str(stored.get("normalization_version") or "") != version:
        # Un tramo a la escala nueva encima de una serie a la vieja la dejaría
        # con dos escalas y sin nada que lo dijera: el mes saldría parte en una
        # y parte en otra. No se mezcla. El sondeo siguiente pide toda la vida
        # —`live_account_watch._period` ya ve que la versión no coincide— y
        # entonces sí entra entera y a una sola escala.
        raise ScaleChanged(
            "cambió el lote al que se presenta: hace falta leer la serie entera",
        )
    report = row.get("report") or {}
    body = _artifact(
        node, snapshot_id, str(report.get("filename") or ""), str(report.get("sha256") or ""),
    )
    found, profits, balances, cash = live_account_report.account_from_html(body, symbol_lots)
    server.live_accounts.merge(
        node_id, account_key, login=row.get("login"), server=row.get("server"),
        currency=row.get("currency"), movements=found, profits=profits,
        period_start=period[0], period_end=period[1],
        balance=row.get("balance"), equity=row.get("equity"), balances=balances,
        cash=cash, normalization_version=version if fully_rewritten else None,
    )
    return len(found)


def symbol_lots(
    server: Any, found: list[live_account_alternate.Reading],
) -> dict[str, dict[str, float]]:
    """A qué lote presenta cada símbolo, por cuenta leída en este sondeo.

    Lo deciden los usos que marcaron una columna en la simulación de lotes, y
    lo dejó escrito la promesa de cada portafolio al medirse. Sin promesa
    todavía —la primera noche— no hay mapa y la serie entra observada; en la
    vuelta siguiente la versión ya no coincide y se relee entera, que es el
    mismo camino que repara cualquier otro cambio de criterio.

    La presentación es de la **tarjeta**, no del agente que la leyó: una
    cuenta desviada se mide con los lotes de sus usos, que viven en su nodo.
    """
    return {
        reading.account_key: live_account_presentation.account_symbols(
            server, reading.owner_node_id, reading.account,
        )
        for reading in found
    }


def _period(state: dict[str, Any]) -> tuple[str, str]:
    """El periodo que el sondeo cubrió, en días, como lo manda el archivo."""
    return (
        str(state.get("period_start") or "")[:10],
        str(state.get("period_end") or "")[:10],
    )


def _merge_accounts(
    server: Any, node: dict[str, Any], node_id: str, snapshot_id: str,
    state: dict[str, Any], routes: dict[str, Any],
    lots: dict[str, dict[str, float]], period: tuple[str, str],
) -> tuple[int, list[str], list[str], int]:
    """Cuenta por cuenta: lo leído, lo que falló, lo rancio y los movimientos.

    Una cuenta que no se deja leer no tumba a las demás; cada una deja dicho
    por qué, que es lo que luego decide si la cita queda cumplida.
    """
    read, failed, stale, movements = 0, [], [], 0
    for account_key, row in (state.get("accounts") or {}).items():
        if not isinstance(row, dict) or str(row.get("status") or "") != "completed":
            failed.append(f"{account_key}: {row.get('error') or 'sin leer'}")
            continue
        reading = routes.get(str(account_key))
        try:
            movements += _merge_account(
                server, node, reading.owner_node_id if reading else node_id, snapshot_id,
                account_key, row, period, lots.get(str(account_key)) or {},
            )
            read += 1
        except ScaleChanged as exc:
            stale.append(f"{account_key}: {exc}")
        except INGEST_ERRORS as exc:
            failed.append(f"{account_key}: {exc}")
            sys.stderr.write(f"[live-account-ingest] {node_id}/{account_key}: {exc}\n")
    return read, failed, stale, movements


def collect(
    server: Any, node: dict[str, Any], node_id: str, state: dict[str, Any],
) -> dict[str, Any] | None:
    """Mete en el archivo el sondeo terminado que el agente enseña.

    Devuelve el resumen de lo recogido, o `None` si no había nada nuevo.
    Idempotente por `snapshot_id`: repetir la vuelta no duplica nada.
    """
    snapshot_id = str(state.get("snapshot_id") or "")
    status = str(state.get("status") or "")
    runs = server.live_account_runs
    key = runs.key(node_id, SNAPSHOT_KEY)
    if not snapshot_id or status in RUNNING_SNAPSHOT_STATUSES or status == "idle":
        return None
    routes = live_account_alternate.routed(server, node_id)
    lots = symbol_lots(server, list(routes.values()))
    collected_key = live_account_report.ingestion_key(
        snapshot_id,
        (
            live_account_report.series_version(lots.get(str(account_key)))
            for account_key in (state.get("accounts") or {})
        ),
    )
    run = runs.record(key)
    if str(run.get("ingested") or "") == collected_key:
        return None
    owner = str(run.get("owner") or DAILY_KEY)
    period = _period(state)
    read, failed, stale, movements = _merge_accounts(
        server, node, node_id, snapshot_id, state, routes, lots, period,
    )
    # `failed`, `partial` y `stale` los lee `live_account_watch`: son los que
    # dejan la cita sin cumplir pese a este `completed_at`, y los que ponen en
    # marcha otra lectura. Distinguirlos no es decorativo: con `stale` no ha
    # fallado nada, así que el turno vuelve a empezar de cero en vez de gastar
    # un reintento en una captura que nunca se va a poder usar.
    outcome = _outcome(read, failed, stale, status)
    settled = {
        "last_status": status,
        "completed_at": str(state.get("finished_at") or utc_now()),
        "outcome": outcome,
    }
    runs.update(key, ingested=collected_key, **settled)
    # Y lo mismo en la cita que pidió esta tanda, que es la única que se entera:
    # la otra no gasta intentos ni se da por cumplida con lo que leyó ésta.
    # Los intentos son suyos, así que el perdón del `stale` también va ahí.
    runs.update(
        runs.key(node_id, owner), **settled,
        **({"attempts": 0} if outcome == "stale" else {}),
    )
    return {
        "snapshot_id": snapshot_id, "accounts": read, "movements": movements,
        "failed": failed, "stale": stale, "period": period, "status": status,
        "outcome": outcome,
    }


def _outcome(
    read: int, failed: list[str], stale: list[str], status: str = "",
) -> str:
    """Cómo acabó la recogida, en los términos de la política de reintentos.

    `stale` manda sobre `success` aunque entren cuentas: mientras quede una
    serie a otra escala, la cita no está cumplida. Y manda sobre `failed`
    cuando no hay fallos de verdad, porque lo que hace falta es otra lectura,
    no insistir con ésta.

    El sondeo que paró el usuario manda sobre todo lo demás. Lo que leyó entra
    igual —es lo que ya costó entrar en esas cuentas—, pero las que quedaron
    sin leer no son un fallo que reintentar: contarlas como `partial` haría
    que el cron relanzase el sondeo dentro de diez minutos, y el botón de
    parar no pararía nada.
    """
    if status == "stopped":
        return "stopped"
    if failed:
        return "partial" if read else "failed"
    if stale:
        return "stale"
    return "success" if read else "failed"
