"""Lo que el manager hace con un sondeo que ya terminó en el agente.

El nodo publica materia prima —un informe nativo por cuenta— y **un solo
sondeo**: el siguiente sustituye su estado. Así que lo que no se recoja aquí se
pierde, y es la misma trampa que dejó sin archivar las auditorías con autolote
(`ai_context/live_audit_cron_and_bell.md`). Por eso la recogida es idempotente
por `snapshot_id` y la primera vuelta tras arrancar el manager rescata lo que
encuentre.

Dos cosas se recogen, y son distintas:

1. **La serie de cada cuenta**, bajando su informe, comprobando el sha256 que
   el agente publicó y metiéndola en el archivo.
2. **La media estacional de cada portafolio**, que no sale del sondeo sino de
   los informes del backtest en la memoria del agente. Se recalcula aquí, una
   vez al día como mucho, porque la portada no puede parsear trece informes
   cada minuto.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import (
    live_account_expectation, live_account_inventory, live_account_month_lots,
    live_account_presentation, live_account_report, live_audit_autolot,
    manager_http,
)
from .common import safe_int, utc_now
from .live_account_archive import stored_balances
from .live_account_series import month_key, previous_month
from .live_account_snapshot import RUNNING_SNAPSHOT_STATUSES
from .portfolio_identity import _resolve_source_path
from .portfolio_report_cache import cached_report
from .portfolio_service import PortfolioSource


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

EXPECTATION_MAX_AGE_HOURS = 24
"""Cada cuánto se vuelve a medir el backtest de un portafolio.

No es por si cambia el backtest —no cambia— sino por si cambia el portafolio:
una mejora, un arreglo o una exclusión mueven la promesa. La firma decide si
hay que reescribir; esto decide cada cuánto se comprueba la firma, que es lo
que cuesta (abrir la memoria del agente).
"""

SNAPSHOT_KEY = "__snapshot__"
"""La clave del estado del cron de cuentas: es por nodo, no por cuenta.

Un sondeo lee todas las cuentas del agente de una vez, así que el turno, los
intentos y el resultado son del nodo.
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
    found, balances = live_account_report.account_from_html(body, symbol_lots)
    server.live_accounts.merge(
        node_id, account_key, login=row.get("login"), server=row.get("server"),
        currency=row.get("currency"), movements=found,
        period_start=period[0], period_end=period[1],
        balance=row.get("balance"), equity=row.get("equity"), balances=balances,
        normalization_version=version if fully_rewritten else None,
    )
    return len(found)


def symbol_lots(server: Any, node_id: str) -> dict[str, dict[str, float]]:
    """A qué lote presenta cada símbolo, por cuenta del nodo.

    Lo deciden los usos que marcaron una columna en la simulación de lotes, y
    lo dejó escrito la promesa de cada portafolio al medirse. Sin promesa
    todavía —la primera noche— no hay mapa y la serie entra observada; en la
    vuelta siguiente la versión ya no coincide y se relee entera, que es el
    mismo camino que repara cualquier otro cambio de criterio.
    """
    return {
        str(account.get("account_key") or ""):
            live_account_presentation.account_symbols(server, node_id, account)
        for account in live_account_inventory.node_accounts(server, node_id)
    }


def _period(state: dict[str, Any]) -> tuple[str, str]:
    """El periodo que el sondeo cubrió, en días, como lo manda el archivo."""
    return (
        str(state.get("period_start") or "")[:10],
        str(state.get("period_end") or "")[:10],
    )


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
    lots = symbol_lots(server, node_id)
    collected_key = live_account_report.ingestion_key(
        snapshot_id,
        (
            live_account_report.series_version(lots.get(str(account_key)))
            for account_key in (state.get("accounts") or {})
        ),
    )
    if str(runs.record(key).get("ingested") or "") == collected_key:
        return None
    period = _period(state)
    read, failed, stale, movements = 0, [], [], 0
    for account_key, row in (state.get("accounts") or {}).items():
        if not isinstance(row, dict) or str(row.get("status") or "") != "completed":
            failed.append(f"{account_key}: {row.get('error') or 'sin leer'}")
            continue
        try:
            movements += _merge_account(
                server, node, node_id, snapshot_id, account_key, row, period,
                lots.get(str(account_key)) or {},
            )
            read += 1
        except ScaleChanged as exc:
            stale.append(f"{account_key}: {exc}")
        except INGEST_ERRORS as exc:
            failed.append(f"{account_key}: {exc}")
            sys.stderr.write(f"[live-account-ingest] {node_id}/{account_key}: {exc}\n")
    # `failed`, `partial` y `stale` los lee `live_account_watch`: son los que
    # dejan la cita sin cumplir pese a este `completed_at`, y los que ponen en
    # marcha otra lectura. Distinguirlos no es decorativo: con `stale` no ha
    # fallado nada, así que el turno vuelve a empezar de cero en vez de gastar
    # un reintento en una captura que nunca se va a poder usar.
    outcome = _outcome(read, failed, stale)
    runs.update(
        key, ingested=collected_key, last_status=status,
        completed_at=str(state.get("finished_at") or utc_now()),
        outcome=outcome, **({"attempts": 0} if outcome == "stale" else {}),
    )
    return {
        "snapshot_id": snapshot_id, "accounts": read, "movements": movements,
        "failed": failed, "stale": stale, "period": period, "status": status,
        "outcome": outcome,
    }


def _outcome(read: int, failed: list[str], stale: list[str]) -> str:
    """Cómo acabó la recogida, en los términos de la política de reintentos.

    `stale` manda sobre `success` aunque entren cuentas: mientras quede una
    serie a otra escala, la cita no está cumplida. Y manda sobre `failed`
    cuando no hay fallos de verdad, porque lo que hace falta es otra lectura,
    no insistir con ésta.
    """
    if failed:
        return "partial" if read else "failed"
    if stale:
        return "stale"
    return "success" if read else "failed"


def _needs_rebuild(stored: dict[str, Any], now: datetime, mode: str = "") -> bool:
    if not stored:
        return True
    # Cambiar la casilla de la simulación cambia a qué lote se presentan las
    # dos mitades de la tarjeta, así que tampoco espera al reloj: sin esto, el
    # usuario marca una columna y la portada sigue con la escala vieja hasta
    # un día después, con sondeos de por medio que no cambian nada.
    if str(stored.get("mode") or "") != str(mode or ""):
        return True
    # Un criterio nuevo no espera al reloj. La edad decide cada cuánto se
    # comprueba la firma —abrir la memoria del agente es lo que cuesta— pero
    # cuando cambia **lo que se mide**, lo guardado ya no es de este criterio y
    # no hay nada que esperar.
    if str(stored.get("criterion") or "") != live_account_expectation.CRITERION_VERSION:
        return True
    # Una promesa escrita antes de que existiera la identidad del portafolio
    # no tiene la clave, y sin ella el gráfico no puede marcar desde cuándo la
    # cuenta sirve a este portafolio. Se mira la **ausencia** de la clave y no
    # su valor vacío: vacío es una respuesta —ese portafolio no tiene fecha
    # guardada— y reconstruirlo por eso abriría la memoria UBS del agente en
    # cada vuelta, que es lo que el resto de este guardián existe para evitar.
    if "portfolio_created_at" not in stored:
        return True
    # Una promesa medida al lote de cada mes caduca al cambiar de mes: el día
    # 1 la tarjeta pide noviembre y lo guardado llega hasta octubre. Sin esto
    # enseñaría la media al lote declarado hasta la medianoche siguiente, que
    # es justo el número que los escenarios existen para no enseñar.
    months_at = stored.get("months_at") or {}
    if months_at and month_key(now) not in months_at:
        return True
    try:
        updated = datetime.fromisoformat(str(stored.get("updated_at") or ""))
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return now - updated > timedelta(hours=EXPECTATION_MAX_AGE_HOURS)


def refresh_expectations(
    server: Any, node: dict[str, Any], node_id: str,
    accounts: list[dict[str, Any]], now: datetime | None = None,
) -> int:
    """Recalcula la media estacional de los portafolios que lo necesiten.

    Abre la memoria UBS del agente y parsea los informes del backtest, así que
    se hace aquí y no en la portada. La firma evita reescribir lo que no ha
    cambiado; la edad evita abrir la memoria en cada vuelta.
    """
    moment = now or datetime.now(timezone.utc)
    archive = server.live_accounts
    pending = [
        (account, portfolio) for account in accounts
        for portfolio in account["portfolios"]
        if portfolio.get("portfolio_id")
        and _needs_rebuild(
            archive.read_expectation(node_id, portfolio["portfolio_id"]), moment,
            _mode(server, node_id, portfolio),
        )
    ]
    if not pending:
        return 0
    written = 0
    source = PortfolioSource(node)
    for account, portfolio in pending:
        try:
            written += int(
                _rebuild(server, source, node_id, account, portfolio, moment)
            )
        except INGEST_ERRORS as exc:
            sys.stderr.write(
                f"[live-account-expectation] {node_id}/#{portfolio['portfolio_id']}: {exc}\n"
            )
    return written


def _scenarios(
    server: Any, source: Any, node_id: str, account: dict[str, Any],
    portfolio: dict[str, Any], saved: dict[str, Any], variant: str,
    use: live_account_presentation.UseLots, moment: datetime,
) -> dict[str, dict[str, Any]]:
    """Los lotes con los que la cuenta operó el mes en curso y el anterior.

    Sólo con autolote: sin él `real_strategy_lots` ya describe el tamaño de
    toda la vida de la cuenta y no hay nada que corregir. Y nunca con la
    columna del tester marcada, porque entonces los cierres de la cuenta ya
    están escalados al lote del backtest y mover la promesa los separaría.

    Lo observado manda sobre la fórmula donde se sepa: `use.members` sale de
    lo que la auditoría vio ejecutar, que es un hecho, y la fórmula sólo
    rellena lo que no se pudo medir —que con autolote es casi todo, porque
    ahí no hay «un» lote que observar—.
    """
    profile = _profile(server, node_id, portfolio)
    autolot = profile.get("autolot") or {}
    mode = live_account_presentation.mode_of(profile)
    if not autolot.get("enabled") or mode == live_account_presentation.TESTER_LEADS:
        return {}
    members = live_account_expectation.audited_members(saved, variant)
    try:
        rules = live_audit_autolot.rules_by_strategy(autolot, members)
    except ValueError:
        # Un autolote mal configurado —activo sin Excel— no puede dejar a la
        # cartera sin promesa: se mide al lote declarado, como antes de que
        # los escenarios existieran, y la tarjeta sigue diciendo que está a un
        # lote fijo. Quien tiene que gritar es el veredicto, que es donde se
        # configuró; aquí sólo se deja de corregir.
        return {}
    if not rules:
        return {}
    balances = stored_balances(
        server.live_accounts.read(node_id, account["account_key"]),
    )
    volume_rules = live_audit_autolot.broker_volume_rules(
        source.project, getattr(source, "broker", ""),
    )
    label = month_key(moment)
    found: dict[str, dict[str, Any]] = {}
    for target in (label, previous_month(label)):
        balance = live_account_month_lots.month_balance(balances, target)
        if balance <= 0:
            continue
        measured = live_account_month_lots.month_lots(
            members, rules, target, balance, volume_rules,
        )
        found[target] = {
            "basis": live_account_month_lots.LOT_BASIS,
            "balance": measured.balance,
            "lots": {**measured.lots, **dict(use.members)},
            "without_rule": list(measured.without_rule),
        }
    return found


def _identity(saved: dict[str, Any]) -> dict[str, Any]:
    """Cuándo y con qué nombre se guardó el portafolio, y de qué cadena viene.

    `improvement_origin.root_id` es la raíz de su cadena de mejoras: #105 es
    la mejora de #100, que lo es de #73, y la línea empezó a operar con la
    raíz. Se guarda para poder decirlo, no para cortar la serie por ahí: seis
    de los nueve usos en real no tienen cadena y se guardaron a mediados de
    octubre sobre cuentas que llevaban meses operando.
    """
    origin = saved.get("improvement_origin") or {}
    return {
        "portfolio_created_at": str(saved.get("created_at") or ""),
        "portfolio_name": str(saved.get("name") or ""),
        "portfolio_root_id": safe_int(origin.get("root_id"), 0, minimum=0),
    }


def _profile(server: Any, node_id: str, portfolio: dict[str, Any]) -> dict[str, Any]:
    """El perfil del uso, que es donde vive el autolote y su Excel."""
    audit_id = str(portfolio.get("audit_id") or "")
    if not audit_id:
        return {}
    profiles = (server.live_audit_settings.state(node_id) or {}).get("profiles") or {}
    return dict(profiles.get(audit_id) or {})


def _rebuild(
    server: Any, source: Any, node_id: str, account: dict[str, Any],
    portfolio: dict[str, Any], moment: datetime,
) -> bool:
    """Mide un portafolio y lo guarda si la firma cambió.

    Se vuelve a guardar también cuando la firma es la misma: lo que cambia es
    `updated_at`, y sin eso se abriría la memoria del agente en cada vuelta
    para llegar a la misma conclusión.
    """
    portfolio_id = int(portfolio["portfolio_id"])
    detail = source.saved_portfolio_detail(portfolio_id, "full_history")
    stored = server.live_accounts.read_expectation(node_id, portfolio_id)
    saved = detail.get("portfolio") or {}
    variant = str(portfolio.get("portfolio_type") or "")
    use = _presented(server, node_id, portfolio, saved, variant)
    real_lots = _measured_lots(account, saved, variant, use)
    scenarios = _scenarios(
        server, source, node_id, account, portfolio, saved, variant, use, moment,
    )
    presentation = {
        "mode": _mode(server, node_id, portfolio),
        "presentation_lots": dict(use.symbols),
        "presentation_warnings": list(use.warnings),
        # Cuándo se guardó el portafolio que la cuenta sirve hoy. Se queda
        # escrito aquí, como los lotes de presentación, porque esto es lo
        # único que abre la memoria UBS del agente: la portada se repinta cada
        # minuto y no puede ir a buscarlo.
        **_identity(saved),
    }
    # El atajo de la firma sólo vale **dentro del mismo criterio**: la firma
    # resume las entradas, y con un criterio nuevo las mismas entradas dan otro
    # número. Sin esta condición, reescribir lo guardado le devolvía su
    # criterio viejo y `_needs_rebuild` volvía a pedirlo en la vuelta
    # siguiente: la medición «una sola vez» que promete `CRITERION_VERSION` no
    # terminaba nunca y abría la memoria UBS del agente cada minuto.
    if (
        str(stored.get("criterion") or "") == live_account_expectation.CRITERION_VERSION
        and stored.get("signature")
        and stored["signature"] == live_account_expectation.signature(
            saved, real_lots, variant, scenarios,
        )
    ):
        server.live_accounts.write_expectation(
            node_id, portfolio_id, {**stored, **presentation},
        )
        return False
    built = live_account_expectation.build(
        saved, read=lambda path: cached_report(Path(path)),
        resolve=lambda stored_path: _resolve_source_path(stored_path, source.project),
        real_lots=real_lots, portfolio_type=variant, scenarios=scenarios,
    )
    server.live_accounts.write_expectation(
        node_id, portfolio_id, {**built, **presentation},
    )
    return True


def _mode(server: Any, node_id: str, portfolio: dict[str, Any]) -> str:
    """Qué columna marcó el usuario en la simulación de lotes de ese uso."""
    audit_id = str(portfolio.get("audit_id") or "")
    if not audit_id:
        return ""
    profiles = (server.live_audit_settings.state(node_id) or {}).get("profiles") or {}
    return live_account_presentation.mode_of(profiles.get(audit_id) or {})


def _presented(
    server: Any, node_id: str, portfolio: dict[str, Any],
    saved: dict[str, Any], variant: str,
) -> live_account_presentation.UseLots:
    """A qué lote presenta este uso, resuelto con lo que la auditoría vio."""
    mode = _mode(server, node_id, portfolio)
    if not mode:
        return live_account_presentation.UseLots()
    return live_account_presentation.use_lots(
        mode, live_account_expectation.audited_members(saved, variant),
        live_account_presentation.latest_detail(
            server, node_id, str(portfolio.get("audit_id") or ""),
        ),
    )


def _measured_lots(
    account: dict[str, Any], saved: dict[str, Any], variant: str,
    use: live_account_presentation.UseLots | None = None,
) -> dict[str, Any]:
    """Los lotes con los que se mide la promesa, en el orden que manda.

    Manda el lote del perfil —lo que el usuario declaró que la cuenta ejecuta—
    salvo donde la simulación del portafolio dice otra cosa: ahí la serie real
    ya viene escalada a ese lote (`live_account_report.normalized_deal`), o
    bien lo que se corrige es el propio lote declarado, y medir la promesa a
    otro compararía dos escalas distintas.
    """
    return {
        **dict(account.get("real_strategy_lots") or {}),
        **dict((use or live_account_presentation.UseLots()).members),
    }
