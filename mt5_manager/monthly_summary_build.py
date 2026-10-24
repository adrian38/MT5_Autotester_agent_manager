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
    tables = [
        evidence.backtest_months(portfolio["expectation"]) for portfolio in portfolios
    ]
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


def _streak(
    months: dict[str, dict[str, Any]], portfolios: list[dict[str, Any]], label: str,
) -> dict[str, Any]:
    """Meses seguidos por debajo de su promesa, desde el mes cerrado hacia atrás."""
    flags: list[bool | None] = []
    labels: list[str] = []
    for step in range(STREAK_LOOKBACK):
        key = shift_month(label, -step)
        promise = _promise(portfolios, key)["profit"]
        measured = row(months, key) if key in months else None
        flags.append(
            None if measured is None
            else evidence.below_promise(measured["profit"], promise)
        )
        labels.append(key)
    total = evidence.streak(flags)
    return {"months": total, "labels": labels[:total]}


def _degradation(server: Any, node_id: str, portfolios: list[dict[str, Any]]) -> dict[str, Any]:
    """Lo que el vigilante ya midió de cada uso de esta cuenta, junto."""
    degraded: list[dict[str, Any]] = []
    crossing: list[dict[str, Any]] = []
    slow: list[dict[str, Any]] = []
    watching = compared = 0
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
        if found["checked_at"]:
            checked.append(found["checked_at"])
    return {
        "degraded": degraded, "crossing": crossing, "slow": slow,
        "watching": watching, "compared": compared,
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
    return coverage.audit_coverage(entries, label)


def account_card(
    server: Any, node: dict[str, Any], node_id: str, account: dict[str, Any],
    label: str, settings: dict[str, Any],
) -> dict[str, Any]:
    """Una tarjeta del resumen: los hechos del mes y los avisos que salen."""
    stored = server.live_accounts.read(node_id, account["account_key"])
    months = monthly_rows(ordered_movements(stored))
    portfolios = _portfolio_rows(server, node_id, account)
    closed = row(months, label)
    promise = _promise(portfolios, label)
    table = _combined_months(portfolios)
    facts = {
        "node_id": node_id, "node_name": str(node.get("name") or node_id),
        "account_key": account["account_key"], "login": account["login"],
        "server": account["server"],
        "currency": str(stored.get("currency") or ""),
        "autolot": bool(account.get("autolot")),
        "month": label, "closed": closed, "expected": promise,
        "below": evidence.below_promise(closed["profit"], promise["profit"]),
        "profit_standing": evidence.profit_standing(closed["profit"], table, label),
        "drawdown_standing": evidence.drawdown_standing(closed["drawdown"], table),
        "backtest_months": len(table),
        "streak_required": int(settings.get("below_streak_months") or 3),
        "started_inside": any(
            coverage.started_after(portfolio["created_day"], label)
            for portfolio in portfolios
        ),
        "coverage": _coverage(server, node_id, portfolios, label),
        "series": coverage.series_coverage(stored, label),
        **_degradation(server, node_id, portfolios),
    }
    facts["streak"] = _streak(months, portfolios, label)["months"]
    found = notices.notices(facts)
    return {
        **facts,
        "portfolios": [
            {key: value for key, value in portfolio.items() if key != "expectation"}
            for portfolio in portfolios
        ],
        "audit_ids": [portfolio["audit_id"] for portfolio in portfolios],
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
        "counts": notices.decided(cards),
        "display_currency": str(settings.get("display_currency") or "EUR"),
        "streak_required": int(settings.get("below_streak_months") or 3),
    }


_ORDER = {
    notices.UNKNOWN: 3, notices.CHANGE: 2, notices.WATCH: 1, notices.KEEP: 0,
}
"""Lo que pide decisión, arriba. Mismo orden que `notices.verdict`."""
