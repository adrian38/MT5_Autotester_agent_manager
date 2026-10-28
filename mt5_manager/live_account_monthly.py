"""La serie mes a mes de una cuenta real: beneficio contra drawdown.

Lo pide el botón «Mes a mes» de cada tarjeta de la portada, y no la portada
entera: son los movimientos de toda la vida de la cuenta, que es justo lo que
no puede entrar en un payload que se repinta cada minuto.

La aritmética ya existe —`live_account_series.monthly_rows`— y aquí no se
repite. Lo que añade este módulo son tres cosas:

- **El tramo continuo.** Un mes sin cierres es un cero medido, no un hueco: si
  faltara la etiqueta, la pantalla tendría que decidir otra vez si pinta un
  vacío o un cero, y acabaría pintando los meses pegados como si fueran
  seguidos. La cuenta 10010194 de AXI operó en noviembre de 2025 y no volvió
  hasta agosto de 2026: son ocho barras a cero, no una barra al lado de otra.
- **El drawdown del tramo activo**, que **no** es el máximo de los mensuales:
  una caída puede atravesar el cambio de mes, así que se vuelve a medir sobre
  los movimientos. Es la misma razón por la que el archivo guarda movimientos y
  no filas (`live_account_archive`).
- **Dónde empieza la gráfica**: en el primer cierre de la cuenta, no en el
  `created_at` del portafolio. Medido el 2026-10-23 contra los datos reales: la
  cuenta 77028839 de RoboForex opera desde el 2025-11-04 y el portafolio que
  sirve hoy —#183— se guardó el 2026-10-19, porque es la enésima mejora de su
  cadena. Cortar por esa fecha dejaría un gráfico de una sola barra y escondería
  once meses de operación real del mismo dinero. La tarjeta es de la **cuenta**
  (`live_account_inventory`), y la vida de la cuenta es su serie.

Las tarjetas superiores sí se cortan por el día exacto de entrada del
portafolio activo: el histórico anterior aporta contexto visual, no sus cifras.

Todo se mide en la moneda de la cuenta, como las filas de la tarjeta: la
conversión de los totales es de arriba y aquí no tiene nada que comparar.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from . import live_account_inventory
from .live_account_archive import ordered_movements
from .live_account_dashboard import expected_month, month_progress
from .live_account_series import drawdown, month_key, monthly_rows, row

MAXIMUM_MONTHS = 240
"""Meses devueltos como mucho: veinte años.

Es un tope de seguridad, no una política. El archivo ya limita los movimientos
que conserva, así que con una serie sana nunca se alcanza; lo que evita es que
un instante corrupto en el JSON convierta el bucle en un gráfico infinito.
"""


def next_month(label: str) -> str:
    """El mes siguiente a `YYYY-MM`: el reflejo de `previous_month`."""
    year, month = (int(part) for part in label.split("-", 1))
    return f"{year + 1:04d}-01" if month == 12 else f"{year:04d}-{month + 1:02d}"


def month_span(first: str, last: str) -> list[str]:
    """Las etiquetas de `first` a `last`, los meses sin cierres incluidos."""
    if not first:
        return []
    labels = [first]
    while labels[-1] < last and len(labels) < MAXIMUM_MONTHS:
        labels.append(next_month(labels[-1]))
    return labels


def _extreme(months: Sequence[dict[str, Any]], best: bool) -> str:
    """La etiqueta del mejor o del peor mes, entre los que operaron.

    Un mes sin cierres es un cero en el gráfico, pero no es «el peor mes»: con
    los doce meses en la cuenta 10010194 de AXI —cuatro con operaciones y ocho
    sin ninguna, medido el 2026-10-23— el mínimo caía en diciembre de 2025, que
    no operó nunca. Un cero no compite con un resultado.
    """
    traded = [month for month in months if int(month["trades"]) > 0]
    if not traded:
        return ""
    chosen = (max if best else min)(traded, key=lambda month: month["profit"])
    return str(chosen["label"])


def totals(
    ordered: Sequence[tuple[datetime, float]], months: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """El resumen del tramo. `drawdown` se mide sobre la curva, no sobre filas."""
    profits = [float(month["profit"]) for month in months]
    return {
        "months": len(months),
        "traded_months": len([month for month in months if int(month["trades"]) > 0]),
        "trades": sum(int(month["trades"]) for month in months),
        "profit": sum(profits),
        "drawdown": drawdown([amount for _moment, amount in ordered]),
        "worst_month_drawdown": max(
            (float(month["drawdown"]) for month in months), default=0.0,
        ),
        "positive_months": len([value for value in profits if value > 0]),
        "negative_months": len([value for value in profits if value < 0]),
        "best_month": _extreme(months, True),
        "worst_month": _extreme(months, False),
    }


def active_totals(
    ordered: Sequence[tuple[datetime, float]], since: str, last: str,
) -> dict[str, Any]:
    """El resumen del portafolio activo, conservando aparte la serie completa."""
    if not since:
        active = list(ordered)
        rows = monthly_rows(active)
        first = min(rows) if rows else ""
    else:
        active = [item for item in ordered if item[0].date().isoformat() >= since]
        rows = monthly_rows(active)
        first = since[:7] if since[:7] <= last else ""
    months = [{"label": label, **row(rows, label)} for label in month_span(first, last)]
    return totals(active, months)


def tracked_portfolios(server: Any, node_id: str, account_key: str) -> list[dict[str, Any]]:
    """Los usos de esa cuenta con el día en que se guardó cada portafolio.

    El tramo **no** se corta por esa fecha, y es deliberado: medido el
    2026-10-24, seis de los nueve usos en real se guardaron a mediados de
    octubre sobre cuentas que llevaban meses operando, así que cortar dejaría
    seis gráficos de una sola barra. Lo que se hace es marcarla: las barras
    anteriores son de la misma cuenta y del mismo dinero, pero no de este
    portafolio, y el gráfico tiene que poder distinguirlas.
    """
    found: list[dict[str, Any]] = []
    for account in live_account_inventory.node_accounts(server, node_id):
        if str(account.get("account_key") or "") != str(account_key):
            continue
        for portfolio in account.get("portfolios") or []:
            stored = server.live_accounts.read_expectation(
                node_id, portfolio.get("portfolio_id"),
            )
            created = str(stored.get("portfolio_created_at") or "")
            if not created:
                continue
            found.append({
                "portfolio_id": portfolio.get("portfolio_id"),
                "name": str(stored.get("portfolio_name") or portfolio.get("name") or ""),
                "created_day": created[:10],
                "created_month": created[:7],
            })
    return sorted(found, key=lambda row: row["created_day"])


def account_portfolios(server: Any, node_id: str, account_key: str) -> list[dict[str, Any]]:
    """Los portafolios activos de la cuenta, tengan o no fecha de alta medida."""
    for account in live_account_inventory.node_accounts(server, node_id):
        if str(account.get("account_key") or "") == str(account_key):
            return list(account.get("portfolios") or [])
    return []


def add_month_expectations(
    server: Any, node_id: str, portfolios: list[dict[str, Any]],
    months: list[dict[str, Any]], current: str, progress: float,
) -> None:
    """Anota la media histórica y si cada mes completo del tramo la alcanzó."""
    for month in months:
        expected = expected_month(server.live_accounts, node_id, portfolios, month["label"])
        average = expected["profit"]
        target = average * progress if average is not None and month["label"] == current else average
        comparable = month["tracked"] and not month["partial"] and target is not None
        month["average_profit"] = average
        month["average_target"] = target
        month["average_years"] = expected["years"]
        month["meets_average"] = float(month["profit"]) >= target if comparable else None


def monthly_history(
    server: Any, node_id: str, account_key: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Beneficio y drawdown de cada mes de la cuenta, del primer cierre a hoy.

    El tramo llega hasta el mes de hoy aunque la cuenta no haya cerrado nada:
    un mes en blanco al final es un cero medido igual que los de en medio, y
    recortarlo haría leer la última barra como si fuera la de este mes.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stored = server.live_accounts.read(node_id, account_key)
    ordered = ordered_movements(stored)
    rows = monthly_rows(ordered)
    first = min(rows) if rows else ""
    last = max([month_key(moment), *rows])
    months = [{"label": label, **row(rows, label)} for label in month_span(first, last)]
    tracked = tracked_portfolios(server, node_id, account_key)
    since = tracked[0]["created_day"] if tracked else ""
    for month in months:
        # El mes en que entró el portafolio más antiguo queda a medias: parte
        # de sus cierres son de lo que la cuenta operaba antes. Se marca
        # aparte de los anteriores porque no es ni una cosa ni la otra.
        #
        # Sin fecha **no se marca nada**: `tracked=False` significa «este mes
        # es anterior al portafolio», no «no se sabe». Mientras la ingesta no
        # había reescrito `portfolio_created_at` —todas las cuentas reales el
        # 2026-10-24— salían los doce meses en falso, y el gráfico atenuaba la
        # serie entera y declaraba en la leyenda que la cuenta era anterior a
        # su propio portafolio.
        month["tracked"] = month["label"] > since[:7] if since else True
        month["partial"] = bool(since) and month["label"] == since[:7]
    add_month_expectations(
        server, node_id, account_portfolios(server, node_id, account_key),
        months, month_key(moment), month_progress(moment),
    )
    return {
        "node_id": node_id,
        "account_key": account_key,
        "login": str(stored.get("login") or ""),
        "server": str(stored.get("server") or ""),
        "currency": str(stored.get("currency") or ""),
        "first_day": str(stored.get("first_day") or ""),
        "last_day": str(stored.get("last_day") or ""),
        "last_read": str(stored.get("updated_at") or ""),
        "month": month_key(moment),
        "months": months,
        "portfolios": tracked,
        "tracked_since": since,
        "totals": active_totals(ordered, since, last),
    }
