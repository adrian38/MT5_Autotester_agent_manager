"""De lo archivado al resumen del mes, sin preguntarle nada a ningún agente.

Éste es el único módulo del resumen que conoce al `server`, y lo que hace con
él es **leer**: la serie guardada de cada cuenta, la media estacional que dejó
el sondeo nocturno, el índice del archivo de auditorías y el veredicto de
degradación que el vigilante ya dejó medido. Ni una petición al nodo, ni un
informe que abrir.

Esto no es una precaución de estilo. Las tres fuentes caras de este manager
—la memoria UBS del agente, los informes del backtest y la materia prima del
nodo— ya las paga alguien: el sondeo mide la promesa, el vigilante mide la
degradación y el archivo guarda las comparaciones. Un resumen que las volviera
a abrir el día 1 duplicaría el criterio además del coste, que es el fallo que
`AGENTS.md` lleva escrito desde la primera reincidencia.

**La unidad es la cuenta real, no el portafolio.** Lo pide la aritmética: el
beneficio y el drawdown del mes son de la cuenta, y repartirlos entre dos
portafolios que la comparten exigiría inventar un reparto. La tarjeta nombra
los portafolios que la operan y cuelga de ellos lo que sí es suyo —las
estrategias degradadas y la cobertura de su auditoría—, que es exactamente
como ya lo presenta la portada (`live_account_dashboard`).

Si la composición nació durante el mes, tampoco se le atribuye lo que hizo la
cuenta antes: beneficio y drawdown empiezan en el alta. Ese tramo no se compara
con meses completos del backtest ni alimenta rachas; se vigila hasta disponer
del primer mes natural comparable. Pero sí tiene objetivo, y es el del resumen
del portafolio: la media del mes prorrateada por los días vividos (`_tranche`),
la misma cuenta que `live_account_monthly`. Un tramo sin objetivo se leía como
un tramo sin medir.

Y **el lote manda sobre qué se puede comparar**. La serie de `months` está
medida a los lotes declarados y no se remide; con autolote el tamaño lo decide
el balance de cada mes, así que la tarjeta declara en dos marcas qué sigue en
pie: `promise_comparable` (hay media remedida a los lotes de ese mes) y
`table_comparable` (los meses con los que se sitúa están a esos lotes, que con
autolote exige el escenario del mes). Lo que no es comparable no se enseña como
número: se dice.
"""
from __future__ import annotations

from typing import Any

from . import live_account_inventory, live_audit_degradation_alerts
from . import monthly_summary_coverage as coverage
from . import monthly_summary_evidence as evidence
from . import monthly_summary_notices as notices
from .common import safe_int, utc_now
from .live_account_archive import ordered_movements
from .live_account_expectation import month_expectation
from .live_account_schedule import convert
from .live_account_series import monthly_rows, row
from .live_audit_schedule import parse_timestamp
from .monthly_summary_schedule import shift_month

STREAK_LOOKBACK = 12
"""Hasta cuántos meses hacia atrás se busca la racha. Un año basta: más allá
la cartera ya es otra, y la racha que importa es la que sigue viva."""


def _portfolio_rows(server: Any, node_id: str, account: dict[str, Any]) -> list[dict[str, Any]]:
    """Los portafolios de la cuenta, con lo que la promesa guardada sabe de ellos."""
    found: list[dict[str, Any]] = []
    for portfolio in account.get("portfolios") or []:
        portfolio_id = safe_int(portfolio.get("portfolio_id"), 0, minimum=0)
        stored = server.live_accounts.read_expectation(node_id, portfolio_id)
        created = str(stored.get("portfolio_created_at") or "")
        found.append({
            "portfolio_id": portfolio_id,
            "audit_id": str(portfolio.get("audit_id") or ""),
            "name": str(
                stored.get("portfolio_name") or portfolio.get("deployment_name") or ""
            ),
            "created_at": created,
            "created_day": created[:10],
            "expectation": stored,
        })
    return found


def _combined_months(portfolios: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Los meses del backtest de la cuenta: la suma de los de sus portafolios.

    Sólo los meses que **todos** aportan. Sumar sobre la unión mezclaría un mes
    con dos contribuciones y otro con una, y el rango diría que el segundo fue
    peor cuando lo que pasó es que faltaba medio.

    Los drawdowns se suman como ya los suma la portada (`_expected`), aunque la
    DD de una suma no sea la suma de las DD: es conservador —siempre mayor o
    igual— y, sobre todo, es el mismo número que el usuario ya ve ahí. Dos
    criterios para la misma promesa es lo único que no puede pasar.
    """
    return _combine([
        evidence.backtest_months(portfolio["expectation"]) for portfolio in portfolios
    ])


def _combine(tables: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Suma mes a mes las tablas que aportan algo, sobre los meses comunes."""
    tables = [table for table in tables if table]
    if not tables:
        return {}
    shared = set(tables[0])
    for table in tables[1:]:
        shared &= set(table)
    return {
        label: {
            "profit": sum(table[label]["profit"] for table in tables),
            "drawdown": sum(table[label]["drawdown"] for table in tables),
            "trades": sum(table[label]["trades"] for table in tables),
        }
        for label in sorted(shared)
    }


def _combined_scenario(
    portfolios: list[dict[str, Any]], label: str,
) -> dict[str, dict[str, Any]]:
    """Los meses de ese mes del año remedidos a los lotes que operó la cuenta.

    **Todos los portafolios o ninguno.** Media cuenta remedida no es una cuenta
    remedida: sumar el octubre de uno al lote que operó con el del otro al lote
    declarado daría una banda que no está a ningún lote.
    """
    tables = [
        evidence.scenario_months(portfolio["expectation"], label)
        for portfolio in portfolios
    ]
    return _combine(tables) if tables and all(tables) else {}


def _combined_scenario_mean(
    portfolios: list[dict[str, Any]], label: str,
) -> dict[str, Any]:
    """«Un mes cualquiera» de la cuenta, a esos mismos lotes.

    Se suman las medias, que es lo mismo que promediar la suma mientras todos
    promedien los mismos meses; si no coinciden, no se dice.
    """
    rows = [
        evidence.scenario_mean(portfolio["expectation"], label)
        for portfolio in portfolios
    ]
    totals = {row["total"] for row in rows}
    if not rows or not all(row["measured"] for row in rows) or len(totals) != 1:
        return {"measured": False, "total": 0, "profit": None, "drawdown": None}
    return {
        "measured": True, "total": totals.pop(),
        "profit": sum(row["profit"] for row in rows),
        "drawdown": sum(row["drawdown"] for row in rows),
    }


def _promise(portfolios: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """Lo que la cuenta prometía ese mes: la suma de sus portafolios.

    Mismo criterio que `live_account_dashboard._expected`, incluida la regla de
    que un portafolio sin media no resta: se cuenta cuántos se pudieron medir.
    """
    profit = drawdown = 0.0
    years: set[int] = set()
    measured = 0
    bases: set[str] = set()
    for portfolio in portfolios:
        month = month_expectation(portfolio["expectation"], label)
        if month["profit"] is None:
            continue
        measured += 1
        profit += month["profit"]
        drawdown += month["drawdown"]
        years |= set(month["years"])
        bases.add(str(month.get("basis") or ""))
    if not measured:
        return {"profit": None, "drawdown": None, "years": [], "portfolios": 0, "basis": ""}
    return {
        "profit": profit, "drawdown": drawdown, "years": sorted(years),
        "portfolios": measured,
        "basis": bases.pop() if len(bases) == 1 else ("mixed" if bases else ""),
    }


def _at_real_lots(promise: dict[str, Any], *, autolot: bool) -> bool:
    """Si esa promesa está medida al lote que la cuenta corrió ese mes.

    Sin autolote el lote es el declarado y las dos partes coinciden. Con
    autolote lo decide el balance del mes, así que sólo vale la fila que
    `live_account_expectation` remidió para él (`months_at`), que es la que
    llega con `basis` puesto. `mixed` es que unos usos la tienen y otros no:
    media promesa al lote bueno no es una promesa al lote bueno.
    """
    basis = str(promise.get("basis") or "")
    return not autolot or (bool(basis) and basis != "mixed")


def _comparability(
    promise: dict[str, Any], *, partial: bool, autolot: bool, remeasured: bool = False,
) -> dict[str, bool]:
    """Qué se puede comparar de este mes, y qué compararía sólo el volumen.

    La serie de `months` está **a los lotes declarados** y no se remide, así
    que con autolote situar el mes entre sus 77 meses compararía el tamaño de
    la posición y no el comportamiento —justo lo que `monthly_summary_evidence`
    dice en su cabecera que no puede pasar—.

    Lo que sí vale es el escenario del mes: `months_at` guarda los meses de ese
    mes del año remedidos a los lotes que la cuenta operó. Con ellos la banda
    está al mismo lote que el mes que juzga, que era la única objeción, y la
    tarjeta de autolote compara como las demás. `remeasured` dice si están.
    """
    if partial:
        return {"promise_comparable": False, "table_comparable": False}
    at_lots = _at_real_lots(promise, autolot=autolot)
    return {
        "promise_comparable": at_lots,
        "table_comparable": not autolot or (remeasured and at_lots),
    }


def _streak(
    months: dict[str, dict[str, Any]], portfolios: list[dict[str, Any]], label: str,
    *, autolot: bool = False,
) -> dict[str, Any]:
    """Meses seguidos por debajo de su promesa, desde el mes cerrado hacia atrás."""
    flags: list[bool | None] = []
    labels: list[str] = []
    creation_month = max(
        (str(row.get("created_day") or "")[:7] for row in portfolios),
        default="",
    )
    for step in range(STREAK_LOOKBACK):
        key = shift_month(label, -step)
        if creation_month and key <= creation_month:
            flags.append(None)
            labels.append(key)
            continue
        month = _promise(portfolios, key)
        promise = month["profit"] if _at_real_lots(month, autolot=autolot) else None
        measured = row(months, key) if key in months else None
        flags.append(
            None if measured is None
            else evidence.below_promise(measured["profit"], promise)
        )
        labels.append(key)
    total = evidence.streak(flags)
    return {"months": total, "labels": labels[:total]}


def _composition_start(
    portfolios: list[dict[str, Any]], label: str,
) -> Any:
    """Alta más reciente dentro del mes: desde ahí existe esta composición."""
    month_start, month_end = coverage.month_bounds(label)
    starts = [
        stamp for stamp in (
            parse_timestamp(portfolio.get("created_at")) for portfolio in portfolios
        ) if stamp is not None and month_start <= stamp < month_end
    ]
    return max(starts) if starts else None


def _tranche(
    closed: dict[str, Any], promise: dict[str, Any], start: Any, label: str,
    means: dict[str, Any],
) -> dict[str, Any]:
    """El objetivo del tramo, con la misma cuenta que el resumen del portafolio.

    `live_account_monthly.add_month_expectations` prorratea la media del mes
    por los días que el portafolio llevaba vivo y juzga el beneficio contra
    eso; el drawdown no se prorratea, porque un valle no se reparte por días.
    Aquí se repite esa cuenta y no otra parecida: dos pantallas con dos
    objetivos para el mismo tramo es lo único que no puede pasar.

    No sustituye al mes natural. Sigue sin decidir MANTENER o CAMBIAR —eso
    pide meses completos y una racha—, pero deja de callar contra qué se mide
    un tramo que el usuario ya ve medido en la otra pantalla.

    La media de un mes cualquiera viaja prorrateada por lo mismo: los recuadros
    de la tarjeta son los mismos siete siempre, así que los cuatro de
    referencia tienen que estar medidos sobre el mismo tramo que el resultado
    que acompañan. Un número a mes entero al lado de uno de diecisiete días
    compara dos cosas distintas con la misma cara.
    """
    days = coverage.month_days(label)
    lived = max(0, days - start.day + 1) if days else 0
    fraction = lived / days if days else 0.0
    profit, depth = promise.get("profit"), promise.get("drawdown")
    target = profit * fraction if profit is not None else None
    monthly = means["profit"] if means.get("measured") else None
    return {
        "measured": target is not None,
        "days": lived, "month_days": days, "fraction": fraction,
        "average": profit, "profit": target, "drawdown": depth,
        "monthly_profit": monthly * fraction if monthly is not None else None,
        "monthly_drawdown": means["drawdown"] if means.get("measured") else None,
        "meets_profit": (
            float(closed["profit"]) >= target if target is not None else None
        ),
        "meets_drawdown": (
            float(closed["drawdown"]) <= float(depth) if depth is not None else None
        ),
    }


def _performance(
    ordered: list[tuple[Any, float]], portfolios: list[dict[str, Any]], label: str,
    *, autolot: bool = False,
) -> dict[str, Any]:
    """Mide sólo el tramo en que ya existía la composición de la tarjeta."""
    partial_start = _composition_start(portfolios, label)
    measured = [
        item for item in ordered
        if partial_start is None or item[0] >= partial_start
    ]
    closed = row(monthly_rows(measured), label)
    promise = _promise(portfolios, label)
    scenario = _combined_scenario(portfolios, label) if autolot else {}
    lots = _comparability(
        promise, partial=partial_start is not None, autolot=autolot,
        remeasured=bool(scenario),
    )
    # La tabla que juzga es la que está al mismo lote que el mes juzgado: la de
    # siempre sin autolote, y el escenario del mes con él.
    declared = _combined_months(portfolios)
    table = scenario if scenario else declared
    means = (
        _combined_scenario_mean(portfolios, label) if scenario
        else evidence.monthly_means(table)
    )
    counted = means["total"] if scenario and means["measured"] else len(declared)
    # El tramo tiene objetivo mientras la promesa esté al lote que se corrió;
    # prorratear una media medida a otro tamaño daría un objetivo de volumen.
    tranche = (
        _tranche(closed, promise, partial_start, label, means)
        if partial_start is not None and _at_real_lots(promise, autolot=autolot)
        else {"measured": False}
    )
    return {
        "closed": closed, "expected": promise, "table": table, "tranche": tranche,
        "season": evidence.season(table, label),
        "months_total": counted,
        "started_inside": partial_start is not None, **lots,
        "below": evidence.below_promise(closed["profit"], promise["profit"])
        if lots["promise_comparable"] else None,
        "profit_band": evidence.profit_band(closed["profit"], table, label)
        if lots["table_comparable"] else {"measured": False, "verdict": ""},
        "drawdown_band": evidence.drawdown_band(closed["drawdown"], table, label)
        if lots["table_comparable"] else {"measured": False, "verdict": ""},
        "monthly_mean": means
        if lots["table_comparable"] else {"measured": False, "total": 0},
    }


def _degradation(server: Any, node_id: str, portfolios: list[dict[str, Any]]) -> dict[str, Any]:
    """Lo que el vigilante ya midió de cada uso de esta cuenta, junto.

    Sin lo que el usuario ya validó en «contra el histórico»: eso lo quita
    `live_audit_degradation_alerts.stored`, que es el mismo punto del que lee
    la portada. Lo escondido se cuenta, porque una tarjeta que calla tres
    avisos sin decirlo afirma más de lo que sabe.
    """
    degraded: list[dict[str, Any]] = []
    crossing: list[dict[str, Any]] = []
    slow: list[dict[str, Any]] = []
    watching = compared = reviewed = 0
    checked: list[str] = []
    for portfolio in portfolios:
        found = live_audit_degradation_alerts.stored(
            server, node_id, portfolio["audit_id"],
        )
        degraded.extend(found["strategies"])
        crossing.extend(found["crossing"])
        slow.extend(found["slow"])
        watching += int(found["watching"])
        compared += int(found["compared"])
        reviewed += int(found.get("reviewed") or 0)
        if found["checked_at"]:
            checked.append(found["checked_at"])
    return {
        "degraded": degraded, "crossing": crossing, "slow": slow,
        "watching": watching, "compared": compared,
        "degradation_reviewed": reviewed,
        "checked_at": max(checked) if checked else "",
    }


def _coverage(
    server: Any, node_id: str, portfolios: list[dict[str, Any]], label: str,
) -> dict[str, Any]:
    """Qué parte del mes auditaron los usos de esta cuenta, entre todos."""
    wanted = {portfolio["audit_id"] for portfolio in portfolios}
    entries = [
        entry for entry in server.live_audit_archive.index(node_id)
        if str(entry.get("audit_key") or "") in wanted
    ]
    partial_starts = [
        portfolio["created_day"] for portfolio in portfolios
        if coverage.started_within(portfolio["created_day"], label)
    ]
    return coverage.audit_coverage(
        entries, label, max(partial_starts) if partial_starts else "",
    )


def account_card(
    server: Any, node: dict[str, Any], node_id: str, account: dict[str, Any],
    label: str, settings: dict[str, Any],
) -> dict[str, Any]:
    """Una tarjeta del resumen: los hechos del mes y los avisos que salen."""
    stored = server.live_accounts.read(node_id, account["account_key"])
    ordered = ordered_movements(stored)
    months = monthly_rows(ordered)
    portfolios = _portfolio_rows(server, node_id, account)
    autolot = bool(account.get("autolot"))
    performance = _performance(ordered, portfolios, label, autolot=autolot)
    closed = performance["closed"]
    facts = {
        "node_id": node_id, "node_name": str(node.get("name") or node_id),
        "node_broker": str(node.get("portfolio_broker") or ""),
        "account_key": account["account_key"], "login": account["login"],
        "server": account["server"],
        "currency": str(stored.get("currency") or ""),
        "autolot": autolot,
        "month": label, "closed": closed, "expected": performance["expected"],
        "tranche": performance["tranche"],
        "below": performance["below"],
        "profit_band": performance["profit_band"],
        "drawdown_band": performance["drawdown_band"],
        "monthly_mean": performance["monthly_mean"],
        "backtest_months": performance["months_total"],
        # Los del mismo mes del año, que son con los que se cuenta: la promesa
        # ya es la de octubre y el recuento tiene que hablar de esos octubres.
        "seasonal_months": len(performance["season"]),
        "streak_required": int(settings.get("below_streak_months") or 3),
        "started_inside": performance["started_inside"],
        "promise_comparable": performance["promise_comparable"],
        "table_comparable": performance["table_comparable"],
        "started_after_month": any(
            coverage.started_later(portfolio["created_day"], label)
            for portfolio in portfolios
        ),
        "coverage": _coverage(server, node_id, portfolios, label),
        "series": coverage.series_coverage(stored, label),
        **_degradation(server, node_id, portfolios),
    }
    facts["streak"] = (
        0 if facts["started_inside"]
        else _streak(months, portfolios, label, autolot=autolot)["months"]
    )
    found = notices.notices(facts)
    return {
        **facts,
        "portfolios": [
            {key: value for key, value in portfolio.items() if key != "expectation"}
            for portfolio in portfolios
        ],
        "audit_ids": [portfolio["audit_id"] for portfolio in portfolios],
        "recovery": notices.recovery_needs(facts),
        "notices": found,
        "verdict": notices.verdict(found),
        "profit_display": convert(closed["profit"], str(stored.get("currency") or ""), settings),
    }


def build(server: Any, label: str) -> dict[str, Any]:
    """El resumen entero de ese mes: una tarjeta por cuenta real activa."""
    settings = {
        **dict(getattr(server, "live_account_scheduler_settings", {}) or {}),
        **dict(getattr(server, "monthly_summary_settings", {}) or {}),
    }
    cards: list[dict[str, Any]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        for account in live_account_inventory.node_accounts(server, node_id):
            cards.append(
                account_card(server, node, node_id, account, label, settings)
            )
    cards.sort(key=lambda card: (
        -_ORDER.get(str(card["verdict"]), 0), card["node_name"], card["login"],
    ))
    return {
        "month": label, "generated_at": utc_now(), "accounts": cards,
        # Con qué criterio se escribieron estos avisos. La pantalla lo compara
        # con el de hoy para avisar de que está leyendo una foto vieja.
        "criterion": notices.CRITERION_VERSION,
        "counts": notices.decided(cards),
        "display_currency": str(settings.get("display_currency") or "EUR"),
        "streak_required": int(settings.get("below_streak_months") or 3),
    }


_ORDER = {
    notices.UNKNOWN: 3, notices.CHANGE: 2, notices.WATCH: 1, notices.KEEP: 0,
}
"""Lo que pide decisión, arriba. Mismo orden que `notices.verdict`."""
