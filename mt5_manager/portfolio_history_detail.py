"""El histórico OHLC de un portafolio guardado, estrategia a estrategia.

Es lo que la pantalla de portafolios manuales enseña para sus carteras, pero
leyendo lo que un portafolio UBS **ya tiene guardado**: no lanza nada ni toca
la comprobación manual.

De los cinco informes que un miembro conserva, el histórico OHLC son dos:
`is_report_path` (base, en muestra) y `oos_report_path` (robustez, fuera de
muestra). Son contiguos y disjuntos —medido en el #62 de ICTrading: 2020.01 →
2024.12 y 2025.01 → 2026.05—, así que juntos son la historia de la estrategia.
El `final_tick_report_path` queda fuera a propósito: son seis meses con ticks
reales, no histórico OHLC. Y `full_history_report_path`, el tramo continuo,
**no está guardado en ninguna cartera de este equipo** (0 de 16 en las tres
medidas el 2026-10-21), así que no se puede depender de él.

Las cifras van **escaladas por las unidades** que el portafolio asigna a cada
estrategia (petición del usuario): los informes se miden todos a lote 0,01 y
un miembro con `units=3` aporta el triple. Sin escalar, el total no sería el
del portafolio y contradiría al resto de su detalle.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import portfolio_curve_math as curve_math
from .common import safe_int
from .portfolio_report_cache import cached_report
from .portfolio_transfer import _resolve_source_path


HISTORY_STAGES = (
    ("is_report_path", "Base"),
    ("oos_report_path", "Robustez OOS"),
)
"""Los dos informes OHLC que, juntos, son el histórico de una estrategia."""

def _report_path(source: Any, raw: Any) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(_resolve_source_path(text, source.project))
    return path if path.is_file() else None


def _quality(report: Any) -> str:
    for key, value in (report.metrics or {}).items():
        if "historial" in key.lower() or "history quality" in key.lower():
            return str(value)
    return ""


def _worst_quality(values: list[str]) -> str:
    """La peor de las dos etapas: la calidad del histórico es un suelo."""
    numbers = []
    for value in values:
        digits = "".join(c for c in value if c.isdigit() or c == ".")
        if digits:
            try:
                numbers.append((float(digits), value))
            except ValueError:
                continue
    return min(numbers)[1] if numbers else (values[0] if values else "")


def _date_key(text: str) -> tuple[int, int, int]:
    """Ordena las fechas `dd.mm.yyyy` de MT5, que como texto no ordenan.

    Comparar «02.01.2025» con «08.01.2020» por cadena da el año 2025 como el
    menor y el periodo sale al revés: la base y la robustez son contiguas, y
    la pantalla enseñaba «02.01.2025 → 30.12.2024».
    """
    parts = str(text or "").split(".")
    if len(parts) != 3:
        return (0, 0, 0)
    try:
        day, month, year = (int(part) for part in parts)
    except ValueError:
        return (0, 0, 0)
    return (year, month, day)




def _strategy_row(source: Any, member: dict[str, Any]) -> dict[str, Any]:
    """Una estrategia con su histórico OHLC ya escalado por sus unidades."""
    units = max(safe_int(member.get("units"), 1, minimum=1), 1)
    trades: list[tuple[datetime, float]] = []
    missing: list[str] = []
    qualities: list[str] = []
    starts: list[str] = []
    ends: list[str] = []
    for key, label in HISTORY_STAGES:
        path = _report_path(source, member.get(key))
        if path is None:
            missing.append(label)
            continue
        report = cached_report(path)
        for trade in report.trades:
            if getattr(trade, "close_time", None) is None:
                continue
            trades.append((trade.close_time, float(trade.profit_loss or 0.0) * units))
        qualities.append(_quality(report))
        starts.append(str(report.period_start or ""))
        ends.append(str(report.period_end or ""))
    trades.sort(key=lambda item: item[0])
    monthly = curve_math.monthly_from_trades(trades)
    profits = [profit for _closed_at, profit in trades]
    wins = sum(1 for value in profits if value > 0)
    gross_profit = sum(value for value in profits if value > 0)
    gross_loss = abs(sum(value for value in profits if value < 0))
    return {
        "set_name": str(member.get("set_name") or Path(str(member.get("set_path") or "")).name),
        "set_path": str(member.get("set_path") or ""),
        "symbol": str(member.get("symbol") or ""),
        "timeframe": str(member.get("timeframe") or ""),
        "units": units,
        "lot": member.get("lot"),
        "period_start": min([value for value in starts if value], key=_date_key, default=""),
        "period_end": max([value for value in ends if value], key=_date_key, default=""),
        "trades": len(profits),
        "net_profit": sum(profits),
        "max_drawdown": curve_math.drawdown(trades),
        "win_rate": wins * 100.0 / len(profits) if profits else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else 0.0,
        "history_quality": _worst_quality(qualities),
        "monthly": {str(year): months for year, months in sorted(monthly.items())},
        "missing": missing,
        # Las operaciones no viajan al navegador: son decenas de miles y lo
        # único que se hace con ellas aquí es la curva y los valles. Se
        # retiran antes de devolver la respuesta.
        "_trades": trades,
    }





def _combined(strategies: list[dict[str, Any]]) -> dict[str, Any]:
    """El portafolio entero: curva, mensual, totales y los valles más hondos."""
    trades: list[tuple[datetime, float]] = []
    for strategy in strategies:
        trades.extend(strategy["_trades"])
    trades.sort(key=lambda item: item[0])
    # `deepest_valleys` lee las operaciones en `trades`, que en una fila de
    # estrategia es el **recuento**: por eso se le pasa una lista aparte en
    # vez de las filas tal cual.
    rows = [
        {
            "set_name": strategy["set_name"], "symbol": strategy["symbol"],
            "timeframe": strategy["timeframe"], "units": strategy["units"],
            "trades": strategy["_trades"],
        }
        for strategy in strategies
    ]
    monthly = curve_math.monthly_from_trades(trades)
    profits = [profit for _closed_at, profit in trades]
    return {
        "curve": curve_math.daily_curve(trades),
        "monthly": {str(year): months for year, months in sorted(monthly.items())},
        "net_profit": sum(profits),
        "max_drawdown": curve_math.drawdown(trades),
        "trades": len(profits),
        "period_start": trades[0][0].date().isoformat() if trades else "",
        "period_end": trades[-1][0].date().isoformat() if trades else "",
        "valleys": curve_math.deepest_valleys(trades, rows),
    }


def history_detail(source: Any, portfolio_id: int, scope: str) -> dict[str, Any]:
    """El histórico OHLC del portafolio, por estrategia y combinado."""
    detail = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
    members = [member for member in detail.get("members") or [] if not member.get("excluded")]
    strategies = [_strategy_row(source, member) for member in members]
    combined = _combined(strategies)
    for row in strategies:
        row.pop("_trades", None)
    return {
        "portfolio_id": int(portfolio_id),
        "scope": str(scope),
        "label": str(detail.get("alias") or detail.get("name") or f"#{portfolio_id}"),
        "stages": [label for _key, label in HISTORY_STAGES],
        "strategies": strategies,
        "portfolio": combined,
        "incomplete": [row["set_name"] for row in strategies if row["missing"]],
    }
