"""El patrimonio: cuánto dinero hay, de dónde vino y qué lo movió cada mes.

La portada de cuentas contesta «cómo va hoy». Esto contesta la otra pregunta:
**de todo el dinero que hay en real, cuánto puse yo y cuánto lo ha puesto
operar**. Son dos cosas que el saldo mezcla, porque sube igual ganando que
ingresando, y por eso esta pantalla no existía hasta que la serie guardó los
apuntes de saldo (`live_account_series.cash_movements`, v6 del archivo).

Tres criterios que no son obvios y que aquí se deciden una sola vez:

- **El resultado es el neto, no la columna Profit.** La tarjeta de la portada
  enseña Profit porque es lo que el informe de MT5 pone en grande; el saldo lo
  mueve `Profit + comisión + swap`. Con la columna visible, la suma de los
  meses no cuadraría nunca con el patrimonio y no habría forma de saber si la
  culpa es de un ingreso sin registrar o de la comisión.
- **El patrimonio de un mes es el saldo que el bróker dice**, no el que sale de
  acumular movimientos. Lo segundo es tentador —cuadra por construcción— y es
  falso en las cuentas que se presentan a otro lote: ahí los cierres están
  reescalados y el saldo no (`live_account_report.daily_balances`).
- **Un mes anterior a la primera lectura de una cuenta no cuenta esa cuenta.**
  El patrimonio de mayo no incluye una cuenta abierta en agosto, y arrastrar
  hacia atrás su saldo convertiría una cuenta nueva en patrimonio antiguo.

Nada de esto consulta a ningún agente: lee el archivo del manager, igual que
la portada. Lo que no puede hacer es viajar en `/api/live-accounts`: son todos
los movimientos de la vida de cada cuenta y la portada se repinta cada minuto.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Sequence

from . import live_account_inventory
from .live_account_archive import ordered_movements, stored_balances, stored_cash
from .live_account_monthly import month_span
from .live_account_performance import account_series, monthly_performance
from .live_account_report import cash_is_complete
from .live_account_schedule import convert
from .live_account_series import drawdown, month_key, row

REBATE = re.compile(r"rebate|^ir[ #]|bonus|bonif|cashback|comisi|commission", re.IGNORECASE)
"""Abonos del bróker: dinero que entra sin haberlo puesto ni ganado operando.

Son el 87 % de los apuntes de 77028839 y el 1,6 % de su importe: 228 filas de
céntimos («IB rebate (ltdn) #4699», «IR #126 VIP») contra 86,45 € en total.
Contarlos como aportación haría leer el «puesto por mí» como si se hubieran
ingresado doscientas veces.
"""

TRANSFER = re.compile(r"transfer|traspas", re.IGNORECASE)
"""Traspasos entre cuentas. El bróker los escribe con el número de la otra.

No son aportación ni retirada del patrimonio: el dinero sale de una cuenta y
entra en otra. Si las dos están aquí se anulan en el total, y si la otra no
está, el patrimonio cambia de verdad. La pantalla los enseña aparte en vez de
decidirlo por el usuario: con `Transfer to 77028840` no se puede saber si esa
cuenta es suya.
"""

KINDS = ("deposit", "withdrawal", "transfer_in", "transfer_out", "rebate")


def cash_kind(amount: float, kind: str, comment: str) -> str:
    """Qué clase de apunte es, según lo que el bróker escribió en el concepto.

    Clasificar por texto es frágil y aquí es lo único que hay: MT5 publica el
    tipo `balance` para un depósito, un traspaso y un rebate por igual. Por eso
    la clasificación **no decide ningún total exacto**: el flujo neto es la
    suma de todos los apuntes, salga como salga el reparto, y la pantalla
    enseña el detalle fila a fila para que un concepto mal leído se vea en vez
    de desaparecer.
    """
    text = str(comment or "")
    if REBATE.search(text) or str(kind or "") in {"credit", "bonus", "credito", "crédito"}:
        return "rebate"
    if TRANSFER.search(text):
        return "transfer_in" if amount >= 0 else "transfer_out"
    return "deposit" if amount >= 0 else "withdrawal"


def empty_cash() -> dict[str, Any]:
    return {kind: 0.0 for kind in KINDS} | {"net": 0.0, "rows": 0}


def cash_by_month(stored: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Los apuntes de esa cuenta repartidos por mes y por clase."""
    found: dict[str, dict[str, Any]] = {}
    for stamp, amount, kind, comment in stored_cash(stored):
        label = str(stamp)[:7]
        bucket = found.setdefault(label, empty_cash())
        bucket[cash_kind(amount, kind, comment)] += amount
        bucket["net"] += amount
        bucket["rows"] += 1
    return found


def cash_detail(stored: dict[str, Any], minimum: float = 1.0) -> list[dict[str, Any]]:
    """Los apuntes que vale la pena enseñar uno a uno, de nuevo a viejo.

    Los rebates de céntimos no: son cientos y su importe entero cabe en una
    línea del resumen. El tope es por importe y no por clase para que un
    bróker cuyo concepto no se sepa leer siga saliendo en la lista.
    """
    found = [
        {
            "day": str(stamp)[:10], "amount": amount,
            "kind": cash_kind(amount, kind, comment), "comment": comment,
        }
        for stamp, amount, kind, comment in stored_cash(stored)
        if abs(amount) >= minimum
    ]
    found.sort(key=lambda item: item["day"], reverse=True)
    return found


def month_end_balance(
    balances: Sequence[tuple[str, float]], label: str,
) -> float | None:
    """El último saldo conocido de ese mes, o `None` si la cuenta aún no había.

    `None` no es cero: una cuenta que todavía no se leía no es una cuenta a
    cero, y sumarla como tal haría que el patrimonio de hace un año incluyera
    las cuentas de este. Dentro del mes sí se arrastra el último día con saldo,
    porque el informe nativo no publica uno por día natural.
    """
    found: float | None = None
    for day, value in balances:
        if str(day)[:7] > label:
            break
        found = float(value)
    return found


def selection_key(node_id: str, account_key: str) -> str:
    """La clave con la que la portada guarda si esa cuenta suma.

    Es la misma que escribe `accounts.js` (`accountSelectionKey`) y la que
    valida `LiveAccountArchive.update_total_selection`: el nodo y la cuenta,
    separados por `::`. Se construye aquí y no en cada consumidor porque el
    día que cambie de forma, las dos pantallas tienen que cambiar juntas o una
    de ellas sumaría lo que la otra no.
    """
    return f"{node_id}::{account_key}"


def account_wealth(
    server: Any, node: dict[str, Any], node_id: str, account: dict[str, Any],
) -> dict[str, Any]:
    """Lo que esa cuenta aporta al patrimonio, mes a mes y en su moneda."""
    stored = server.live_accounts.read(node_id, account["account_key"])
    ordered, profits = account_series(stored)
    _visible, net_months = monthly_performance(ordered, profits)
    balances = stored_balances(stored)
    apuntes = stored_cash(stored)
    return {
        "node_id": node_id, "node_name": str(node.get("name") or node_id),
        "node_broker": node.get("portfolio_broker"),
        "selection_key": selection_key(node_id, account["account_key"]),
        "account_key": account["account_key"], "login": account["login"],
        "server": account["server"],
        "currency": str(stored.get("currency") or ""),
        "balance": float(stored.get("balance") or 0.0),
        "equity": float(stored.get("equity") or 0.0),
        "portfolios": [
            {
                "portfolio_id": portfolio.get("portfolio_id"),
                "name": portfolio.get("name"), "autolot": portfolio.get("autolot"),
            }
            for portfolio in account.get("portfolios") or []
        ],
        "block_reason": str(account.get("block_reason") or ""),
        "last_read": str(stored.get("updated_at") or ""),
        # La cuenta empieza en lo primero que se sepa de ella, y casi nunca es
        # su primer cierre: 60090762 cierra desde el 2026-03-20 y su depósito
        # de fundación es del 2025-11-14. Cortar por el primer cierre dejaba
        # ese ingreso fuera del tramo, y lo que queda fuera no está en ninguna
        # suma: desaparece sin que nada lo diga.
        "first_month": min(
            [
                value for value in (
                    str(stored.get("first_day") or "")[:7],
                    str(balances[0][0])[:7] if balances else "",
                    str(apuntes[0][0])[:7] if apuntes else "",
                ) if value
            ],
            default="",
        ),
        "cash_known": cash_is_complete(stored),
        "net_months": net_months,
        "cash_months": cash_by_month(stored),
        "balances": balances,
        "cash_detail": cash_detail(stored),
        "cash_rows": len(apuntes),
        "trade_drawdown": drawdown([amount for _moment, amount in ordered_movements(stored)]),
    }


def _converted(
    value: float | None, currency: str, settings: dict[str, Any],
) -> float | None:
    return None if value is None else convert(value, currency, settings)


def _month_of_account(
    account: dict[str, Any], label: str, settings: dict[str, Any], current: str,
) -> dict[str, Any]:
    """Resultado, flujos y saldo de una cuenta en un mes, ya convertidos."""
    started = bool(account["first_month"]) and account["first_month"] <= label
    net = row(account["net_months"], label)["profit"] if started else 0.0
    cash = account["cash_months"].get(label) or empty_cash()
    balance = (
        account["balance"] if label == current and started
        else month_end_balance(account["balances"], label)
    )
    currency = account["currency"]
    return {
        "started": started,
        # Una cuenta que ese mes todavía no existía no puede estar
        # escondiendo flujos suyos en él: su saldo tampoco entra en el
        # patrimonio del mes, así que dejarla fuera de las dos cosas es lo
        # coherente. Lo que falte por releer oscurece los meses en los que ya
        # estaba, y sólo ésos.
        "cash_known": account["cash_known"] or not started,
        "profit": _converted(net, currency, settings),
        "balance": _converted(balance, currency, settings) if started else None,
        # Una cuenta que todavía no se ha releído entera no tiene flujos
        # «cero»: tiene flujos desconocidos. Sumarla como cero convertiría
        # 37.775 € de depósitos ciertos —lo medido el 2026-11-15 sobre el
        # archivo real— en un patrimonio que nadie puso.
        "cash": {
            key: _converted(cash[key], currency, settings)
            if account["cash_known"] else None
            for key in (*KINDS, "net")
        },
        "rows": cash["rows"],
    }


def _sum(values: Sequence[float | None]) -> float:
    return sum(value for value in values if value is not None)


def _month_total(
    accounts: Sequence[dict[str, Any]], label: str, settings: dict[str, Any],
    current: str,
) -> dict[str, Any]:
    """La fila del mes: patrimonio al cierre, resultado y flujos de caja.

    Los flujos de un mes se saben cuando están releídas **las cuentas que ya
    existían ese mes**, no todas. Exigirlas todas dejaba el patrimonio entero
    sin ingresos, sin retiradas, sin traspasos, sin abonos y sin cuadre
    —catorce meses en «—»— porque faltaba por releer una cuenta abierta en
    agosto: una cuenta que en 2025 no existía no puede esconder los apuntes
    de 2025. Medido el 2026-11-15: nueve de diez cuentas releídas y diez de
    los catorce meses recuperados.
    """
    rows = [_month_of_account(account, label, settings, current) for account in accounts]
    present = [item for item in rows if item["started"]]
    known = all(item["cash_known"] for item in rows)
    cash = {
        key: _sum([item["cash"][key] for item in rows]) if known else None
        for key in (*KINDS, "net")
    }
    return {
        "label": label,
        "balance": _sum([item["balance"] for item in rows]) if present else None,
        "profit": _sum([item["profit"] for item in present]),
        "accounts": len(present),
        "cash": cash,
        "cash_known": known,
        "contributed": None if not known else cash["net"] - cash["rebate"],
        "movements": sum(int(item["rows"]) for item in rows),
        "per_account": {
            account["account_key"]: item["profit"]
            for account, item in zip(accounts, rows) if item["started"]
        },
    }


def _reconcile(months: list[dict[str, Any]]) -> None:
    """Cuánto de la variación de cada mes no lo explican resultado y flujos.

    No es decoración: es la única comprobación que esta pantalla puede hacer
    de sí misma. Si un mes mueve 400 € que ni se ganaron ni se ingresaron,
    falta un apunte —o la cuenta se presenta a otro lote— y más vale que lo
    diga la pantalla que creérselo.

    El primer mes no se cuadra: no hay patrimonio anterior con el que
    comparar, y tratar el cero como patrimonio anterior convertiría el saldo
    inicial entero en un descuadre.

    Y un mes al que le falte por releer una de **sus** cuentas tampoco se
    cuadra: el descuadre sería justo el dinero ingresado que todavía no está
    en el archivo —37.775 € el 2026-11-15— y se leería como un error de la
    pantalla. Los meses anteriores a esa cuenta sí se cuadran, que es de lo
    que se ocupa `_month_total`.
    """
    previous: float | None = None
    for month in months:
        balance = month["balance"]
        if previous is None or balance is None:
            month["variation"] = None
            month["unexplained"] = None
        else:
            month["variation"] = balance - previous
            month["unexplained"] = (
                month["variation"] - month["profit"] - month["cash"]["net"]
                if month["cash_known"] else None
            )
        if balance is not None:
            previous = balance


def _wealth_drawdown(months: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """La mayor caída del patrimonio desde un máximo, en dinero y en por cien.

    Es la caída del **patrimonio**, no la de operar: una retirada lo baja y no
    es una pérdida. Se dicen las dos —aquí y `trade_drawdown` por cuenta—
    porque confundirlas es leer un traspaso como un mal mes.
    """
    peak = worst = 0.0
    worst_pct = 0.0
    for month in months:
        balance = month["balance"]
        if balance is None:
            continue
        peak = max(peak, balance)
        fall = peak - balance
        if fall > worst:
            worst, worst_pct = fall, (fall / peak * 100 if peak else 0.0)
    return {"amount": worst, "percent": worst_pct, "peak": peak}


def _extreme(months: Sequence[dict[str, Any]], best: bool) -> str:
    traded = [month for month in months if month["accounts"]]
    if not traded:
        return ""
    chosen = (max if best else min)(traded, key=lambda month: month["profit"])
    return str(chosen["label"])


def _totals(
    months: list[dict[str, Any]], accounts: Sequence[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """El resumen de arriba: lo que hay, lo que puse y lo que puso operar."""
    profit = sum(month["profit"] for month in months)
    contributed = _sum([month["contributed"] for month in months])
    rebates = _sum([month["cash"]["rebate"] for month in months])
    balance = next(
        (month["balance"] for month in reversed(months) if month["balance"] is not None),
        0.0,
    )
    unexplained = [month["unexplained"] for month in months if month["unexplained"] is not None]
    known = [account for account in accounts if account["cash_known"]]
    return {
        "balance": balance,
        # Con cuentas sin releer, lo aportado es un mínimo, no un total: se
        # dice cuántas cuentas lo sostienen para que la pantalla lo rotule.
        "cash_known": len(known) == len(accounts),
        "cash_accounts": len(known),
        "balance_known": balance is not None,
        "equity": _sum([
            _converted(account["equity"], account["currency"], settings)
            for account in accounts
        ]),
        "profit": profit,
        "contributed": contributed,
        "rebates": rebates,
        "deposits": _sum([month["cash"]["deposit"] for month in months]),
        "withdrawals": _sum([month["cash"]["withdrawal"] for month in months]),
        "transfers_in": _sum([month["cash"]["transfer_in"] for month in months]),
        "transfers_out": _sum([month["cash"]["transfer_out"] for month in months]),
        # Rentabilidad sobre lo aportado. Sin aportación conocida no hay
        # porcentaje: dividir por cero o por un cero inventado daría un número
        # enorme donde lo cierto es «todavía no se sabe». Con cuentas sin
        # releer tampoco, porque el denominador sería un trozo del aportado y
        # el porcentaje saldría disparado.
        "return_on_contributed": (
            profit / contributed * 100
            if contributed > 0 and len(known) == len(accounts) else None
        ),
        "months": len(months),
        "positive_months": len([month for month in months if month["profit"] > 0]),
        "negative_months": len([month for month in months if month["profit"] < 0]),
        "best_month": _extreme(months, True),
        "worst_month": _extreme(months, False),
        "drawdown": _wealth_drawdown(months),
        "unexplained": sum(unexplained),
        "worst_unexplained": max((abs(value) for value in unexplained), default=0.0),
        "accounts": len(accounts),
    }


def _contributor(
    account: dict[str, Any], months: list[dict[str, Any]],
) -> dict[str, Any]:
    """Una cuenta como fila de la tabla de aportación: su mes a mes y su total."""
    series = [month["per_account"].get(account["account_key"]) for month in months]
    known = [value for value in series if value is not None]
    return {
        "account_key": account["account_key"], "login": account["login"],
        "selection_key": account["selection_key"],
        "node_id": account["node_id"],
        "server": account["server"], "node_name": account["node_name"],
        "node_broker": account["node_broker"], "currency": account["currency"],
        "portfolios": account["portfolios"], "balance": account["balance"],
        "cash_known": account["cash_known"], "last_read": account["last_read"],
        "block_reason": account["block_reason"],
        "first_month": account["first_month"],
        "trade_drawdown": account["trade_drawdown"],
        "cash_detail": account["cash_detail"],
        "cash_rows": account["cash_rows"],
        "monthly": series,
        "profit": sum(known),
        "positive_months": len([value for value in known if value > 0]),
        "negative_months": len([value for value in known if value < 0]),
    }


def _warnings(
    accounts: Sequence[dict[str, Any]], months: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Lo que la pantalla tiene que decir antes de que nadie sume a mano."""
    return {
        # Una moneda sin cambio no se suma: sus importes se quedan fuera de
        # todas las filas y de los totales. Callarlo daría un patrimonio menor
        # que el real sin que nada lo dijera, que es lo mismo que mentir.
        "missing_rates": sorted({
            account["currency"] or "?" for account in accounts
            if convert(1.0, account["currency"], settings) is None
        }),
        "cash_pending": sorted(
            account["account_key"] for account in accounts if not account["cash_known"]
        ),
        "missing_balance": sorted(
            account["account_key"] for account in accounts
            if not account["balances"] and not account["block_reason"]
        ),
        "blocked": sorted(
            account["account_key"] for account in accounts if account["block_reason"]
        ),
        "months_without_balance": [
            month["label"] for month in months if month["balance"] is None
        ],
    }


def snapshot(server: Any, now: datetime | None = None) -> dict[str, Any]:
    """El payload entero de la pantalla de patrimonio.

    Suma **las cuentas marcadas «Sumar»** en la portada, que es la misma
    selección compartida que usan sus totales (`total_selection`). Sumarlas
    todas era el error de la primera versión: de las quince cuentas del equipo,
    cinco están desmarcadas —tres demo y dos de RoboForex— y metían en el
    patrimonio 12.600 € que no son dinero real.
    """
    moment = now or datetime.now(timezone.utc)
    settings = dict(server.live_account_scheduler_settings)
    current = month_key(moment)
    excluded = set(server.live_accounts.total_selection().get("excluded_accounts") or [])
    accounts: list[dict[str, Any]] = []
    left_out: list[dict[str, Any]] = []
    for node in server.nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        for account in live_account_inventory.node_accounts(server, node_id):
            row = account_wealth(server, node, node_id, account)
            (left_out if row["selection_key"] in excluded else accounts).append(row)
    accounts.sort(key=lambda account: (account["node_name"], account["login"]))
    left_out.sort(key=lambda account: (account["node_name"], account["login"]))
    first = min(
        (account["first_month"] for account in accounts if account["first_month"]),
        default="",
    )
    months = [
        _month_total(accounts, label, settings, current)
        for label in month_span(first, current)
    ]
    _reconcile(months)
    return {
        "generated_at": moment.isoformat(),
        "month": current,
        "display_currency": str(settings.get("display_currency") or "EUR"),
        "exchange_rates": dict(settings.get("exchange_rates") or {}),
        "rates_updated_at": str(settings.get("rates_updated_at") or ""),
        "months": months,
        "contributors": [_contributor(account, months) for account in accounts],
        "totals": _totals(months, accounts, settings),
        "warnings": _warnings(accounts, months, settings),
        # Las que no suman se nombran, no se esconden: un patrimonio que deja
        # fuera cinco cuentas sin decirlo se lee como el total de todo.
        "not_summed": [
            {
                "account_key": account["account_key"], "login": account["login"],
                "server": account["server"], "node_name": account["node_name"],
                "balance": account["balance"], "currency": account["currency"],
            }
            for account in left_out
        ],
    }
