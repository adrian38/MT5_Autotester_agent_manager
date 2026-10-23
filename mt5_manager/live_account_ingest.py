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

from . import live_account_expectation, live_account_report, manager_http
from .common import utc_now
from .live_account_snapshot import RUNNING_SNAPSHOT_STATUSES
from .portfolio_identity import _resolve_source_path
from .portfolio_report_cache import cached_report
from .portfolio_service import PortfolioSource

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
) -> int:
    report = row.get("report") or {}
    body = _artifact(
        node, snapshot_id, str(report.get("filename") or ""), str(report.get("sha256") or ""),
    )
    found = live_account_report.movements_from_html(body, account_key)
    version = live_account_report.normalization_version(account_key)
    stored = server.live_accounts.read(node_id, account_key)
    first_day = str(stored.get("first_day") or "")
    fully_rewritten = not first_day or not period[0] or period[0] <= first_day
    server.live_accounts.merge(
        node_id, account_key, login=row.get("login"), server=row.get("server"),
        currency=row.get("currency"), movements=found,
        period_start=period[0], period_end=period[1],
        balance=row.get("balance"), equity=row.get("equity"),
        normalization_version=version if fully_rewritten else None,
    )
    return len(found)


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
    collected_key = live_account_report.ingestion_key(
        snapshot_id, (state.get("accounts") or {}).keys(),
    )
    if str(runs.record(key).get("ingested") or "") == collected_key:
        return None
    period = _period(state)
    read, failed, movements = 0, [], 0
    for account_key, row in (state.get("accounts") or {}).items():
        if not isinstance(row, dict) or str(row.get("status") or "") != "completed":
            failed.append(f"{account_key}: {row.get('error') or 'sin leer'}")
            continue
        try:
            movements += _merge_account(
                server, node, node_id, snapshot_id, account_key, row, period,
            )
            read += 1
        except INGEST_ERRORS as exc:
            failed.append(f"{account_key}: {exc}")
            sys.stderr.write(f"[live-account-ingest] {node_id}/{account_key}: {exc}\n")
    # `failed` y `partial` los lee `live_account_watch.UNFINISHED_OUTCOMES`:
    # son los que dejan la cita sin cumplir pese a este `completed_at`, y los
    # que ponen en marcha los reintentos. Distinguirlos no es decorativo, pero
    # sí es sólo informativo: para la política de reintentos valen lo mismo.
    runs.update(
        key, ingested=collected_key, last_status=status,
        completed_at=str(state.get("finished_at") or utc_now()),
        outcome="success" if read and not failed else ("failed" if not read else "partial"),
    )
    return {
        "snapshot_id": snapshot_id, "accounts": read, "movements": movements,
        "failed": failed, "period": period, "status": status,
    }


def _needs_rebuild(stored: dict[str, Any], now: datetime) -> bool:
    if not stored:
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
        )
    ]
    if not pending:
        return 0
    written = 0
    source = PortfolioSource(node)
    for account, portfolio in pending:
        try:
            written += int(_rebuild(server, source, node_id, account, portfolio))
        except INGEST_ERRORS as exc:
            sys.stderr.write(
                f"[live-account-expectation] {node_id}/#{portfolio['portfolio_id']}: {exc}\n"
            )
    return written


def _rebuild(
    server: Any, source: Any, node_id: str, account: dict[str, Any],
    portfolio: dict[str, Any],
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
    real_lots = _measured_lots(account, saved, variant)
    if (
        stored.get("signature")
        and stored["signature"] == live_account_expectation.signature(
            saved, real_lots, variant,
        )
    ):
        server.live_accounts.write_expectation(node_id, portfolio_id, stored)
        return False
    built = live_account_expectation.build(
        saved, read=lambda path: cached_report(Path(path)),
        resolve=lambda stored_path: _resolve_source_path(stored_path, source.project),
        real_lots=real_lots, portfolio_type=variant,
    )
    server.live_accounts.write_expectation(node_id, portfolio_id, built)
    return True


def _measured_lots(
    account: dict[str, Any], saved: dict[str, Any], variant: str,
) -> dict[str, Any]:
    """Los lotes con los que se mide la promesa, en el orden que manda.

    Manda el lote del perfil —lo que la cuenta ejecuta— salvo en los símbolos
    que esa cuenta presenta a otra base: ahí la serie real ya viene escalada
    (`live_account_report.normalized_deal`), así que medir la promesa al lote
    ejecutado compararía dos escalas distintas.
    """
    return {
        **dict(account.get("real_strategy_lots") or {}),
        **live_account_report.base_lots(
            str(account.get("account_key") or ""),
            live_account_expectation.audited_members(saved, variant),
        ),
    }
