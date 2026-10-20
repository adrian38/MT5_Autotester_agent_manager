"""Lo que la portada de cuentas enseña, calculado en un solo sitio.

La pantalla pinta; el criterio está aquí. Es la misma razón por la que el
veredicto de reproducción vive en `live_audit_reproduction.js` y lo comparten
Análisis y «Portafolios activos»: dos implementaciones del mismo número acaban
diciendo cosas distintas.

Qué lleva cada tarjeta, y por qué:

- **La fila diaria no se compara con nada.** Es lo que se pidió: el beneficio y
  la DD del día, a secas.
- **La mensual sí**, contra la media de *ese* mes en el backtest
  (`live_account_expectation`), y contra el mes anterior.
- La media mensual se **prorratea por lo que va del mes** para decidir el
  color: el día 21 comparar veintiún días con la media de un mes completo
  pintaría de rojo un mes que va bien. La media completa se enseña igual, que
  es el objetivo del mes.
- La DD **no** se prorratea: una caída no es acumulativa como el beneficio, y
  que ya supere la media de un mes entero es un hecho que importa hoy.

Nada de esto consulta a ningún agente: lee la serie guardada y la media que
dejó el sondeo nocturno. La portada se refresca cada minuto y tiene que ser
barata.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Any

from .common import safe_int
from .live_account_archive import stored_movements
from .live_account_expectation import month_expectation
from .live_account_schedule import convert
from .live_account_series import (
    daily_rows, monthly_rows, month_key, previous_month, row,
)

STALE_AFTER_HOURS = 48
"""Cuándo una serie deja de ser «de hoy». Con el cron diario de lunes a
viernes, dos días cubren el fin de semana sin acusar a nadie en falso."""


def _ordered(stored: dict[str, Any]) -> list[tuple[datetime, float]]:
    return [
        (datetime.fromisoformat(stamp), amount)
        for stamp, amount in stored_movements(stored)
    ]


def month_progress(now: datetime) -> float:
    """Qué fracción del mes va, por días. Nunca cero y nunca más de uno."""
    moment = now.astimezone(timezone.utc)
    days = calendar.monthrange(moment.year, moment.month)[1]
    return min(max(moment.day / days, 1.0 / days), 1.0)


def tone(value: Any, reference: Any, *, higher_is_better: bool = True) -> str:
    """`good`, `bad` o nada. Sin referencia no hay color: no se inventa."""
    if value is None or reference is None:
        return ""
    better = float(value) >= float(reference)
    if not higher_is_better:
        better = float(value) <= float(reference)
    return "good" if better else "bad"


def _expected(archive: Any, node_id: str, portfolios: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """La media de ese mes para la cuenta: la suma de sus portafolios.

    Una cuenta con dos portafolios promete lo que prometen los dos. Los años se
    unen y se cuentan: la tarjeta tiene que poder decir que un octubre con dos
    años no es un octubre con seis.
    """
    profit = drawdown = 0.0
    years: set[int] = set()
    measured = 0
    for portfolio in portfolios:
        stored = archive.read_expectation(node_id, portfolio.get("portfolio_id"))
        month = month_expectation(stored, label)
        if month["profit"] is None:
            continue
        measured += 1
        profit += month["profit"]
        drawdown += month["drawdown"]
        years |= set(month["years"])
    if not measured:
        return {"profit": None, "drawdown": None, "years": [], "portfolios": 0}
    return {
        "profit": profit, "drawdown": drawdown, "years": sorted(years),
        "portfolios": measured,
    }


def _state(stored: dict[str, Any], account: dict[str, Any], now: datetime) -> tuple[str, str]:
    """En qué situación está la tarjeta, y qué le pasa dicho en una línea."""
    if account.get("block_reason"):
        return "blocked", str(account["block_reason"])
    updated = str(stored.get("updated_at") or "")
    if not updated:
        return "never_read", (
            "Todavía no se ha sondeado esta cuenta. El sondeo lo hace el cron "
            "de lunes a viernes, o el botón «Sondear ahora»."
        )
    try:
        moment = datetime.fromisoformat(updated)
    except ValueError:
        return "", ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if now - moment > timedelta(hours=STALE_AFTER_HOURS):
        return "stale", f"La última lectura es de {moment.date().isoformat()}."
    return "", ""


def account_card(
    server: Any, node: dict[str, Any], node_id: str, account: dict[str, Any],
    settings: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    """Una tarjeta: la cuenta, lo que lleva hoy, el mes y lo que prometía."""
    archive = server.live_accounts
    stored = archive.read(node_id, account["account_key"])
    ordered = _ordered(stored)
    days, months = daily_rows(ordered), monthly_rows(ordered)
    today = now.astimezone(timezone.utc).date().isoformat()
    label = month_key(now)
    earlier = previous_month(label)
    monthly = row(months, label)
    earlier_row = row(months, earlier)
    expected = _expected(archive, node_id, account["portfolios"], label)
    progress = month_progress(now)
    prorated = None if expected["profit"] is None else expected["profit"] * progress
    state, message = _state(stored, account, now)
    currency = str(stored.get("currency") or "")
    return {
        "node_id": node_id, "node_name": str(node.get("name") or node_id),
        "account_key": account["account_key"], "login": account["login"],
        "server": account["server"], "currency": currency,
        "balance": float(stored.get("balance") or 0.0),
        "portfolios": account["portfolios"], "autolot": bool(account.get("autolot")),
        "daily": {**row(days, today), "day": today},
        "monthly": {
            **monthly, "label": label, "progress": progress,
            "profit_tone": tone(monthly["profit"], prorated),
            "drawdown_tone": tone(
                monthly["drawdown"], expected["drawdown"], higher_is_better=False,
            ),
        },
        # El tono y la diferencia son de **este** mes contra el anterior, no
        # del mes anterior: pintarlos sobre su importe hacía leer un septiembre
        # en verde como «septiembre fue bueno» cuando lo que decían era que
        # octubre va mejor. La pantalla los enseña como nota, y así un color
        # sigue significando una sola cosa: contra la media de ese mes.
        "previous_month": {
            **earlier_row, "label": earlier,
            "profit_tone": tone(monthly["profit"], earlier_row["profit"]),
            "profit_delta": monthly["profit"] - earlier_row["profit"],
            "drawdown_tone": tone(
                monthly["drawdown"], earlier_row["drawdown"], higher_is_better=False,
            ),
            "drawdown_delta": monthly["drawdown"] - earlier_row["drawdown"],
        },
        "expected_month": {**expected, "profit_prorated": prorated},
        "expected_previous": _expected(archive, node_id, account["portfolios"], earlier),
        "last_read": str(stored.get("updated_at") or ""),
        "first_day": str(stored.get("first_day") or ""),
        "state": state, "message": message,
        "daily_display": convert(row(days, today)["profit"], currency, settings),
        "monthly_display": convert(monthly["profit"], currency, settings),
    }


def _totals(cards: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    """Los dos totales de arriba, y las monedas que no se pudieron sumar."""
    missing = sorted({
        str(card["currency"] or "?") for card in cards
        if card["daily_display"] is None or card["monthly_display"] is None
    })
    return {
        "daily": sum(
            card["daily_display"] for card in cards if card["daily_display"] is not None
        ),
        "monthly": sum(
            card["monthly_display"] for card in cards
            if card["monthly_display"] is not None
        ),
        "currency": str(settings.get("display_currency") or "EUR"),
        "accounts": len(cards),
        "missing_rates": missing,
    }


def snapshot(server: Any, now: datetime | None = None) -> dict[str, Any]:
    """El payload entero de la portada de cuentas."""
    from . import live_account_inventory

    moment = now or datetime.now(timezone.utc)
    settings = dict(server.live_account_scheduler_settings)
    cards: list[dict[str, Any]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        for account in live_account_inventory.node_accounts(server, node_id):
            cards.append(account_card(server, node, node_id, account, settings, moment))
    cards.sort(key=lambda card: (card["node_name"], card["login"]))
    return {
        "generated_at": moment.isoformat(),
        "day": moment.astimezone(timezone.utc).date().isoformat(),
        "month": month_key(moment),
        "previous_month": previous_month(month_key(moment)),
        "display_currency": str(settings.get("display_currency") or "EUR"),
        "exchange_rates": dict(settings.get("exchange_rates") or {}),
        "rates_updated_at": str(settings.get("rates_updated_at") or ""),
        "accounts": cards,
        "totals": _totals(cards, settings),
        "month_progress": month_progress(moment),
        "stale_after_hours": safe_int(STALE_AFTER_HOURS, 48, minimum=1),
    }
