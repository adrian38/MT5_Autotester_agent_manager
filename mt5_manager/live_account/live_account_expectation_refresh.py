"""La media estacional de cada portafolio, recalculada desde la vuelta del cron.

Sale de `live_account_ingest.py`, que estaba justo en el techo de 600 líneas y
cuyo propio encabezado ya decía que recogía **dos cosas distintas**: la serie
de cada cuenta, que es materia prima del sondeo, y esto, que no sale del
sondeo en absoluto sino de los informes del backtest que hay en la memoria UBS
del agente.

Es lo único de la vuelta que abre esa memoria, y por eso vive detrás de dos
frenos: la firma evita reescribir lo que no ha cambiado, y la edad
(`EXPECTATION_MAX_AGE_HOURS`) evita abrirla en cada vuelta para llegar a la
misma conclusión. La portada no puede hacer nada de esto: se repinta cada
minuto.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import (
    live_account_expectation, live_account_month_lots, live_account_presentation,
)
from ..live_audit import live_audit_autolot
from ..common import safe_int
from .live_account_archive import stored_balances
from .live_account_ingest import INGEST_ERRORS
from .live_account_series import month_key, previous_month
from ..portfolio.portfolio_identity import _resolve_source_path
from ..portfolio.portfolio_report_cache import cached_report
from ..portfolio.portfolio_service import PortfolioSource

EXPECTATION_MAX_AGE_HOURS = 24
"""Cada cuánto se vuelve a medir el backtest de un portafolio.

No es por si cambia el backtest —no cambia— sino por si cambia el portafolio:
una mejora, un arreglo o una exclusión mueven la promesa. La firma decide si
hay que reescribir; esto decide cada cuánto se comprueba la firma, que es lo
que cuesta (abrir la memoria del agente).
"""


def _needs_rebuild(
    stored: dict[str, Any], now: datetime, mode: str = "", autolot: str = "",
) -> bool:
    if not stored:
        return True
    # Encenderlo no cambia el criterio, ni el modo, ni `months_at` —vacío, y
    # la regla de abajo lo deja pasar—: un día al lote declarado (#148).
    if str(stored.get("autolot_source") or "") != str(autolot or ""):
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
    # Y lo mismo con lo que la tarjeta **dice**: los lotes de presentación y
    # sus avisos se guardan aquí, así que cambiar el criterio de la nota sin
    # caducar lo escrito deja la nota vieja en pantalla hasta que al uso le
    # toque por edad. La firma no lo cubre: resume lo medido, no lo redactado.
    if (
        str(stored.get("presentation_criterion") or "")
        != live_account_presentation.PRESENTATION_VERSION
    ):
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
            _autolot_source(server, node_id, portfolio),
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
    stored: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Los lotes de la cuenta este mes, el anterior y los ya medidos.

    Sólo con autolote: sin él `real_strategy_lots` ya describe el tamaño de
    toda la vida de la cuenta y no hay nada que corregir. Y nunca con la
    columna del tester marcada, porque entonces los cierres de la cuenta ya
    están escalados al lote del backtest y mover la promesa los separaría.

    Lo observado manda sobre la fórmula donde se sepa: `use.members` sale de
    lo que la auditoría vio ejecutar, que es un hecho, y la fórmula sólo
    rellena lo que no se pudo medir —con autolote, casi todo—.
    """
    profile = _profile(server, node_id, portfolio)
    if live_account_presentation.mode_of(profile) == live_account_presentation.TESTER_LEADS:
        return {}
    members = live_account_expectation.audited_members(saved, variant)
    balances = stored_balances(
        server.live_accounts.read(node_id, account["account_key"]),
    )
    volume_rules = live_audit_autolot.broker_volume_rules(
        source.project, getattr(source, "broker", ""),
    )
    found = _vantage_step_scenarios(
        server, source, node_id, account, portfolio, members, balances, moment,
    )
    autolot = profile.get("autolot") or {}
    if not autolot.get("enabled"):
        return found
    try:
        rules = live_audit_autolot.rules_by_strategy(autolot, members)
    except ValueError:
        # Un autolote mal configurado —activo sin Excel— no puede dejar a la
        # cartera sin promesa: se mide al lote declarado, como antes de que
        # los escenarios existieran, y la tarjeta sigue diciendo que está a un
        # lote fijo. Quien tiene que gritar es el veredicto, que es donde se
        # configuró; aquí sólo se deja de corregir.
        return found
    if not rules:
        return found
    label = month_key(moment)
    for target in (label, previous_month(label)):
        balance = live_account_month_lots.month_balance(balances, target)
        if balance <= 0:
            continue
        measured = live_account_month_lots.month_lots(
            members, rules, target, balance, volume_rules,
        )
        previous = found.get(target) or {}
        found[target] = {
            "basis": "mixed" if previous.get("lots") else live_account_month_lots.LOT_BASIS,
            "balance": measured.balance,
            "lots": {**measured.lots, **dict(previous.get("lots") or {}), **dict(use.members)},
            "without_rule": list(measured.without_rule),
        }
    return live_account_month_lots.carried_scenarios(stored or {}, found)


def _vantage_step_scenarios(
    server: Any, source: Any, node_id: str, account: dict[str, Any],
    portfolio: dict[str, Any], members: list[dict[str, Any]],
    balances: list[tuple[str, float]], moment: datetime,
) -> dict[str, dict[str, Any]]:
    if not _is_vantage_lotstep_account(node_id, account):
        return {}
    opening_lots = _opening_step_lots(server, node_id, portfolio, moment)
    return _step_scenarios(source, members, balances, opening_lots, moment)


def _is_vantage_lotstep_account(node_id: str, account: dict[str, Any]) -> bool:
    """La excepción LotStep solicitada; ninguna otra cuenta hereda la regla."""
    return (
        node_id == "vantage-raw-local"
        and str(account.get("login") or "") == "34933859"
        and str(account.get("server") or "").casefold() == "vantagemarkets-live 10"
    )


def _opening_step_lots(
    server: Any, node_id: str, portfolio: dict[str, Any], moment: datetime,
) -> dict[str, float]:
    detail = live_account_presentation.latest_detail(
        server, node_id, str(portfolio.get("audit_id") or ""),
    )
    return live_account_month_lots.opening_real_lots(detail, month_key(moment))


def _resolve_step_set(stored_path: str, project: Path) -> Path:
    """Conserva `runtime/.../manual_portfolios`, que el resolver UBS recorta."""
    direct = Path(stored_path)
    if direct.is_file():
        return direct
    parts = [part for part in stored_path.replace("\\", "/").split("/") if part]
    lowered = [part.casefold() for part in parts]
    if "runtime" in lowered:
        candidate = project.joinpath(*parts[lowered.index("runtime"):])
        if candidate.is_file():
            return candidate
    return Path(_resolve_source_path(stored_path, project))


def _step_scenarios(
    source: Any, members: list[dict[str, Any]],
    balances: list[tuple[str, float]], opening_lots: dict[str, float],
    moment: datetime,
) -> dict[str, dict[str, Any]]:
    rules = live_account_month_lots.step_rules(
        members, lambda path: _resolve_step_set(path, source.project),
        opening_lots,
    )
    if not rules:
        return {}
    label = month_key(moment)
    found: dict[str, dict[str, Any]] = {}
    for target in (label, previous_month(label)):
        balance = live_account_month_lots.month_opening_balance(balances, target)
        measured = live_account_month_lots.step_month_lots(members, rules, target, balance)
        lots = measured.lots if balance > 0 else {
            key: lot for key, lot in opening_lots.items() if key in rules
        } if target == label else {}
        if not lots:
            continue
        found[target] = {
            "basis": live_account_month_lots.STEP_LOT_BASIS,
            "balance": measured.balance, "lots": lots, "without_rule": [],
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
    real_lots = _measured_lots(_profile(server, node_id, portfolio), saved, variant, use)
    scenarios = _scenarios(
        server, source, node_id, account, portfolio, saved, variant, use,
        moment, stored,
    )
    presentation = {
        "mode": _mode(server, node_id, portfolio),
        "autolot_source": _autolot_source(server, node_id, portfolio),
        "presentation_lots": dict(use.symbols),
        "presentation_warnings": list(use.warnings),
        "presentation_criterion": live_account_presentation.PRESENTATION_VERSION,
        # Cuándo se guardó el portafolio que la cuenta sirve hoy. Se queda
        # escrito aquí, como los lotes de presentación, porque esto es lo
        # único que abre la memoria UBS del agente: la portada se repinta cada
        # minuto y no puede ir a buscarlo.
        **_identity(saved),
        **live_account_expectation.portfolio_valleys(saved, variant),
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


def _autolot_source(server: Any, node_id: str, portfolio: dict[str, Any]) -> str:
    """Huella del autolote: el sha del Excel, porque cambiar la tabla de
    riesgo cambia los lotes del mes y con ellos la promesa. Vacía sin él.
    """
    autolot = _profile(server, node_id, portfolio).get("autolot") or {}
    if not autolot.get("enabled"):
        return ""
    return str(autolot.get("source_sha256") or "") or "enabled"


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
    profile = _profile(server, node_id, portfolio)
    mode = live_account_presentation.mode_of(profile)
    if not mode:
        return live_account_presentation.UseLots()
    return live_account_presentation.use_lots(
        mode, live_account_expectation.audited_members(saved, variant),
        live_account_presentation.latest_detail(
            server, node_id, str(portfolio.get("audit_id") or ""),
        ),
        autolot=bool((profile.get("autolot") or {}).get("enabled")),
    )


def _measured_lots(
    profile: dict[str, Any], saved: dict[str, Any], variant: str,
    use: live_account_presentation.UseLots | None = None,
) -> dict[str, Any]:
    """Los lotes con los que se mide la promesa, en el orden que manda.

    Manda el lote del perfil —lo que el usuario declaró que la cuenta ejecuta—
    salvo donde la simulación del portafolio dice otra cosa: ahí la serie real
    ya viene escalada a ese lote (`live_account_report.normalized_deal`), o
    bien lo que se corrige es el propio lote declarado, y medir la promesa a
    otro compararía dos escalas distintas.

    Es el mapa **del uso**, no el de la cuenta. `node_accounts` fusiona los
    `real_strategy_lots` de todos los usos que comparten un login porque la
    tarjeta es de la cuenta, pero estas claves son de la cartera: las de un
    portafolio UBS llevan broker y candidato (`AXI/STANDARD:1437`) y no chocan
    nunca, mientras que las de uno manual son posicionales —`manual:1`,
    `manual:2`— y se repiten en cada cartera manual. Con el mapa fusionado,
    dos carteras manuales sobre la misma cuenta real se pisaban: la promesa de
    una se medía al lote declarado de la otra y `member_lot` lo presentaba
    como origen «real», o sea como un hecho declarado.
    """
    return {
        **dict(profile.get("real_strategy_lots") or {}),
        **dict((use or live_account_presentation.UseLots()).members),
    }
