"""Conversión del reporte nativo MT4 a las filas comunes del sondeo."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from lxml import html
from portfolio_manager.mt5_report import RawDeal


def is_mt4_report(contents: bytes) -> bool:
    folded = contents[:200_000].lower()
    return b"closed transactions" in folded or "Closed Transactions".encode("utf-16-le") in contents[:400_000]


def _rows(contents: bytes) -> list[list[str]]:
    document = html.parse(BytesIO(contents))
    return [
        [" ".join(cell.itertext()).replace("\xa0", " ").strip() for cell in row.xpath("./th|./td")]
        for row in document.xpath("//tr")
    ]


def _number(value: Any) -> float:
    text = re.sub(r"[^0-9,.'+\-]", "", str(value or "")).replace("'", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _moment(value: str) -> datetime | None:
    text = str(value or "").strip()
    for pattern in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _summary_value(rows: list[list[str]], label: str) -> float:
    wanted = label.casefold()
    for row in rows:
        for index, cell in enumerate(row[:-1]):
            if cell.strip().endswith(":") and cell.rstrip(":").strip().casefold() == wanted:
                return _number(row[index + 1])
    return 0.0


CASH_TYPES = frozenset({"balance", "credit"})
"""Las filas que no son un cierre: ingresos, retiradas y abonos del bróker."""


def _is_deal(row: list[str]) -> bool:
    """Si esa fila de la sección es un cierre o un apunte, y no otra cosa.

    El ancho es lo único que las separa. Un cierre trae las catorce columnas
    de la cabecera; una orden pendiente cancelada vive en la misma sección con
    once y no es nada que archivar. Un apunte de caja trae **cinco** —ticket,
    instante, tipo, concepto e importe, sin símbolo ni volumen— así que el
    ancho de catorce los descartaba todos: los catorce de la MT4 3083784 de
    Axi, 600 y 448,54 USD de ingreso entre ellos (`ai_context/patrimonio.md`).
    """
    if len(row) >= 14:
        return True
    return len(row) >= 5 and row[2].strip().casefold() in CASH_TYPES


def _closed_rows(rows: list[list[str]]) -> list[list[str]]:
    inside = False
    found: list[list[str]] = []
    for row in rows:
        folded = " ".join(row).casefold()
        if "closed transactions" in folded:
            inside = True
            continue
        if inside and any(title in folded for title in ("open trades", "working orders", "summary:")):
            break
        if inside and row and row[0].casefold() != "ticket" and _is_deal(row):
            found.append(row)
    return found


def _cash_deal(row: list[str]) -> RawDeal | None:
    """El apunte de caja estrecho: el importe es la última celda, no la 13.

    El concepto son las celdas de en medio, que es donde MT4 escribe lo único
    que distingue un ingreso de una retirada o de un traspaso.
    """
    moment = _moment(row[1])
    if moment is None:
        return None
    return RawDeal(
        timestamp=moment, ticket=row[0], symbol="",
        trade_type=row[2].strip().casefold(), direction="", volume=0.0,
        price=0.0, order=row[0], commission=0.0, swap=0.0,
        profit=_number(row[-1]), balance=0.0, comment=" ".join(row[3:-1]).strip(),
    )


def _deal(row: list[str]) -> RawDeal | None:
    kind = row[2].strip().casefold()
    balance_row = kind in CASH_TYPES
    if balance_row and len(row) < 14:
        return _cash_deal(row)
    moment = _moment(row[1] if balance_row else row[8])
    if moment is None:
        return None
    commission = _number(row[10]) + _number(row[11])
    return RawDeal(
        timestamp=moment, ticket=row[0], symbol="" if balance_row else row[4],
        trade_type=kind, direction="" if balance_row else "out",
        volume=0.0 if balance_row else _number(row[3]),
        price=0.0 if balance_row else _number(row[9]), order=row[0],
        commission=commission, swap=_number(row[12]), profit=_number(row[13]),
        balance=0.0, comment=row[4] if balance_row else (row[14] if len(row) > 14 else ""),
    )


def raw_deals_from_mt4(contents: bytes) -> list[RawDeal]:
    """Filas cerradas MT4 con saldo acumulado reconstruido desde el Summary.

    El saldo se rehace hacia atrás desde el del Summary, así que la cuenta de
    la que se resta **tiene que llevar los apuntes de caja**: sin ellos cada
    mes pasado sale dibujado con el dinero que todavía no se había ingresado.
    Medido el 2026-11-18 en la MT4 3083784 de Axi: catorce apuntes que suman
    3.914,19 USD quedaban fuera, y septiembre de 2025 salía con 3.941,17 USD
    cuando la cuenta acababa de abrirse con 600.
    """
    rows = _rows(contents)
    deals = [deal for row in _closed_rows(rows) if (deal := _deal(row)) is not None]
    deals.sort(key=lambda deal: (deal.timestamp, deal.ticket))
    final_balance = _summary_value(rows, "Balance")
    running = final_balance - sum(deal.net_profit for deal in deals)
    for deal in deals:
        running += deal.net_profit
        deal.balance = running
    return deals
