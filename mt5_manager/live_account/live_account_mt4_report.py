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
        if inside and len(row) >= 14 and row[0].casefold() != "ticket":
            found.append(row)
    return found


def _deal(row: list[str]) -> RawDeal | None:
    kind = row[2].strip().casefold()
    balance_row = kind in {"balance", "credit"}
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
    """Filas cerradas MT4 con saldo acumulado reconstruido desde el Summary."""
    rows = _rows(contents)
    deals = [deal for row in _closed_rows(rows) if (deal := _deal(row)) is not None]
    deals.sort(key=lambda deal: (deal.timestamp, deal.ticket))
    final_balance = _summary_value(rows, "Balance")
    running = final_balance - sum(deal.net_profit for deal in deals)
    for deal in deals:
        running += deal.net_profit
        deal.balance = running
    return deals
