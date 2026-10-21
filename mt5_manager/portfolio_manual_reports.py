"""Lectura de los históricos OHLC generados para portafolios manuales."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from portfolio_manager.mt5_report import StrategyReport

from . import portfolio_curve_math as curve_math
from .portfolio_report_cache import cached_report


def _portable_set_name(value: Any) -> str:
    """Nombre final tanto si la memoria guarda barras Windows como POSIX."""
    return Path(str(value or "").replace("\\", "/")).name


def _maximum_drawdown(report: StrategyReport) -> float:
    balance = float(report.initial_deposit or 0)
    peak = balance
    maximum = 0.0
    for trade in sorted(report.trades, key=lambda item: item.close_time):
        balance += float(trade.profit_loss or 0)
        peak = max(peak, balance)
        maximum = max(maximum, peak - balance)
    return maximum


def _quality(report: StrategyReport) -> str:
    for key, value in report.metrics.items():
        if "historial" in key.lower() or "history quality" in key.lower():
            return str(value)
    return ""


def _closed_trades(report: StrategyReport) -> list[tuple[Any, float]]:
    return [
        (trade.close_time, float(trade.profit_loss or 0))
        for trade in report.trades if getattr(trade, "close_time", None) is not None
    ]


def _report_summary(report: StrategyReport, member: dict[str, Any]) -> dict[str, Any]:
    profits = [float(item.profit_loss or 0) for item in report.trades]
    wins = sum(1 for value in profits if value > 0)
    gross_profit = sum(value for value in profits if value > 0)
    gross_loss = abs(sum(value for value in profits if value < 0))
    # El mes es el del cierre de la operación, no el de la transacción que MT5
    # cuenta en `report.monthly`: así la tabla mensual suma exactamente el
    # beneficio neto que la misma pantalla enseña arriba.
    monthly = curve_math.monthly_from_trades(sorted(_closed_trades(report)))
    return {
        "set_id": str(member.get("set_id") or ""),
        "set_name": _portable_set_name(member.get("set_path") or member.get("set_id")),
        "report_path": str(report.path),
        "symbol": report.symbol or str(member.get("symbol") or ""),
        "timeframe": report.timeframe or str(member.get("timeframe") or ""),
        "period_start": report.period_start,
        "period_end": report.period_end,
        "initial_deposit": float(report.initial_deposit or 0),
        "net_profit": sum(profits),
        "max_drawdown": _maximum_drawdown(report),
        "trades": len(profits),
        "win_rate": wins * 100.0 / len(profits) if profits else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else 0.0,
        "history_quality": _quality(report),
        "monthly": monthly,
    }


def _matching_report(project: Path, member: dict[str, Any]) -> Path | None:
    stem = Path(_portable_set_name(member.get("set_path") or member.get("set_id"))).stem
    reports = project / "reports"
    if not stem or not reports.is_dir():
        return None
    matches = [path for suffix in (".htm", ".html") if (path := reports / f"{stem}{suffix}").is_file()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def manual_history_summary(project: Path, members: list[dict[str, Any]]) -> dict[str, Any]:
    """Relaciona cada set manual con el último reporte homónimo del nodo."""
    strategies = []
    for member in members:
        path = _matching_report(project, member)
        if path is None:
            continue
        try:
            strategies.append(_report_summary(cached_report(path), member))
        except (OSError, ValueError, TypeError):
            continue
    rows = []
    ordered_trades: list[tuple[Any, float]] = []
    for strategy in strategies:
        trades = sorted(_closed_trades(cached_report(Path(strategy["report_path"]))))
        ordered_trades.extend(trades)
        rows.append({
            "set_name": strategy["set_name"], "symbol": strategy["symbol"],
            "timeframe": strategy["timeframe"], "units": 1, "trades": trades,
        })
    ordered_trades.sort()
    return {
        "available": len(strategies), "expected": len(members), "strategies": strategies,
        "net_profit": sum(item[1] for item in ordered_trades),
        "max_drawdown": curve_math.drawdown(ordered_trades),
        "trades": len(ordered_trades),
        # Lo mismo que enseña el detalle de un portafolio UBS guardado, que es
        # lo que el usuario pidió traer aquí: la curva combinada y los cinco
        # descensos más hondos con su desglose.
        "curve": curve_math.daily_curve(ordered_trades),
        "valleys": curve_math.deepest_valleys(ordered_trades, rows),
    }
