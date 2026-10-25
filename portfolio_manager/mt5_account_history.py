"""Lee el informe nativo de historial de cuenta que guarda el terminal MT5.

No es el reporte del Strategy Tester de `mt5_report.py`: es el que MT5 escribe
desde la pestaña History, con las secciones Posiciones, Órdenes, Transacciones
y Posiciones abiertas.

Existe porque `history_deals_get` miente cuando el terminal acaba de cambiar de
login: el 2026-10-24 devolvió 0 deals para la cuenta 77049426 en el periodo
18→24/10 mientras este mismo HTML, capturado por ese mismo terminal en esa
misma ejecución, traía 50 posiciones cerradas. El HTML es lo que el terminal
imprime de su propio historial, así que sirve de red cuando la API no da nada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from lxml import html


# Las secciones del informe, en los dos idiomas en que el terminal lo escribe.
CLOSED_TITLES = ("Posiciones", "Positions")
OPEN_TITLES = ("Posiciones abiertas", "Open Positions")
DEAL_TITLES = ("Transacciones", "Deals")
SECTION_TITLES = CLOSED_TITLES + OPEN_TITLES + DEAL_TITLES + (
    "Órdenes", "Ordenes", "Orders", "Órdenes de trabajo", "Ordenes de trabajo",
    "Working Orders", "Resultados", "Results",
)

# Las dos tablas de posiciones se leen por POSICIÓN y no por nombre de columna:
# repiten «Fecha/Hora» y «Precio» para la apertura y el cierre, así que un
# `dict(zip(cabecera, celdas))` se queda sólo con el cierre. El orden de las
# columnas lo fija el terminal y no cambia con el idioma.
CLOSED_COLUMNS = 13
OPEN_COLUMNS = 12


@dataclass(frozen=True)
class NativeAccountHistory:
    """Lo que el informe nativo dice del periodo auditado."""

    closed: list[dict] = field(default_factory=list)
    open_at_end: list[dict] = field(default_factory=list)


def read_native_account_history(
    path: Path, period_start: datetime, period_end: datetime,
) -> NativeAccountHistory:
    """Operaciones reales del periodo según el HTML nativo del terminal.

    Las horas del informe son las del servidor y se etiquetan UTC sin
    convertirlas, que es lo mismo que hace el camino de la API con
    `deal.time`: las dos copias comparan contra reportes del tester escritos
    en ese mismo reloj.
    """
    return _history(_report_rows(str(path)), period_start, period_end)


def read_native_account_history_bytes(
    content: bytes, period_start: datetime, period_end: datetime,
) -> NativeAccountHistory:
    """Lo mismo, con el informe en memoria.

    El manager lo baja del agente por HTTP para reanalizar una ejecucion ya
    guardada, asi que nunca llega a tener un fichero.
    """
    return _history(_report_rows(BytesIO(content)), period_start, period_end)


def _history(
    rows: list[list[str]], period_start: datetime, period_end: datetime,
) -> NativeAccountHistory:
    comments = _entry_comments(rows)
    positions = [
        trade for cells in _section(rows, CLOSED_TITLES)
        if (trade := _closed_position(cells, comments)) is not None
    ]
    closed = [
        trade for trade in positions
        if period_start <= trade["close_time"] <= period_end
    ]
    still_open = [
        opening for cells in _section(rows, OPEN_TITLES)
        if (opening := _open_position(cells)) is not None
    ]
    # Una posición que cerró DESPUÉS del periodo seguía abierta al acabarlo, y
    # el informe la lista entre las cerradas, no entre las abiertas.
    closed_later = [
        _opening_view(trade) for trade in positions
        if trade["close_time"] > period_end
    ]
    open_at_end = [
        opening for opening in still_open + closed_later
        if opening["open_time"] <= period_end
    ]
    open_at_end.sort(key=lambda row: (row["open_time"], row["position_id"]))
    return NativeAccountHistory(closed=closed, open_at_end=open_at_end)


def _report_rows(source: Any) -> list[list[str]]:
    """Celdas visibles de cada fila; MT5 mete separadores ocultos con colspan."""
    document = html.parse(source)
    rows: list[list[str]] = []
    for row in document.xpath("//tr"):
        rows.append([
            " ".join(cell.itertext()).replace("\xa0", " ").strip()
            for cell in row.xpath("./td|./th")
            if "hidden" not in (cell.get("class") or "")
        ])
    return rows


def _section(rows: list[list[str]], titles: tuple[str, ...]) -> list[list[str]]:
    """Filas de datos de una sección, sin su título ni su cabecera."""
    collected: list[list[str]] = []
    inside = False
    for cells in rows:
        title = cells[0] if len(cells) == 1 else ""
        if title in titles:
            inside = True
            continue
        if inside and title in SECTION_TITLES:
            break
        if inside and len(cells) > 1:
            collected.append(cells)
    return collected


def _closed_position(cells: list[str], comments: dict[tuple[str, str], str]) -> dict | None:
    """Una posición cerrada, con la forma que usa el auditor para las reales."""
    if len(cells) != CLOSED_COLUMNS:
        return None
    open_time, close_time = _moment(cells[0]), _moment(cells[8])
    position = _identifier(cells[1])
    if open_time is None or close_time is None or not position:
        return None
    comment = comments.get((cells[2].casefold(), cells[0]), "")
    return {
        "strategy": comment or str(position),
        "symbol": cells[2], "side": _side(cells[3]),
        "open_time": open_time, "close_time": close_time,
        "open_price": _decimal(cells[5]), "close_price": _decimal(cells[9]),
        "volume": _decimal(cells[4]),
        "profit": _decimal(cells[12]) + _decimal(cells[10]) + _decimal(cells[11]),
        "position_id": position, "comment": comment,
        "sl": _decimal(cells[6]), "tp": _decimal(cells[7]),
    }


def _open_position(cells: list[str]) -> dict | None:
    """Una posición que seguía viva cuando el terminal escribió el informe."""
    if len(cells) != OPEN_COLUMNS:
        return None
    open_time = _moment(cells[0])
    position = _identifier(cells[1])
    if open_time is None or not position:
        return None
    comment = cells[11]
    return {
        "strategy": comment or str(position),
        "symbol": cells[2], "side": _side(cells[3]),
        "open_time": open_time, "open_price": _decimal(cells[5]),
        "volume": _decimal(cells[4]), "position_id": position, "comment": comment,
    }


def _opening_view(trade: dict) -> dict:
    """La apertura de una posición cerrada, para contarla como abierta."""
    return {
        key: trade[key] for key in (
            "strategy", "symbol", "side", "open_time", "open_price",
            "volume", "position_id", "comment",
        )
    }


def _entry_comments(rows: list[list[str]]) -> dict[tuple[str, str], str]:
    """Comentario del EA en la transacción que abrió cada posición.

    Es la única huella que el EA deja en la operación real —en AXI nombra el
    `.set`—, y la tabla de posiciones no la trae. Las transacciones no dicen a
    qué posición pertenecen, así que se casan por símbolo y hora exacta de
    apertura. Una posición abierta antes del periodo no tiene su transacción en
    el informe y se queda sin comentario.
    """
    comments: dict[tuple[str, str], str] = {}
    headers: list[str] | None = None
    for cells in _section(rows, DEAL_TITLES):
        if headers is None or len(cells) != len(headers):
            headers = cells
            continue
        row = dict(zip(headers, cells))
        if _value(row, "Dirección", "Direccion", "Direction").casefold() != "in":
            continue
        comment = _value(row, "Comentario", "Comment")
        if comment:
            moment = _value(row, "Fecha/Hora", "Time")
            comments[(_value(row, "Símbolo", "Simbolo", "Symbol").casefold(), moment)] = comment
    return comments


def _value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if key in row:
            return row[key].strip()
    return ""


def _side(value: str) -> str:
    return "sell" if value.strip().casefold().startswith("sell") else "buy"


def _identifier(value: str) -> int:
    digits = value.replace(" ", "")
    return int(digits) if digits.isdigit() else 0


def _decimal(value: str) -> float:
    """Los importes llevan el millar separado por espacio: «2 319.95»."""
    try:
        return float(value.replace(" ", "").replace("−", "-") or 0.0)
    except ValueError:
        return 0.0


def _moment(value: str) -> datetime | None:
    for pattern in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
