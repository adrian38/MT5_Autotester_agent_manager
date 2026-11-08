"""Lectura de los históricos OHLC generados para portafolios manuales."""
from __future__ import annotations

import re
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


def manual_report_stem(portfolio_id: Any, set_reference: Any) -> str:
    """El nombre del informe que el nodo escribe para un set manual.

    Copia exacta de `manager_node_runtime/manual_portfolio_history`, que es
    quien lo decide: el histórico corre sobre una copia renombrada del `.set`
    para no sustituir el informe del candidato homónimo del pool.
    """
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(_portable_set_name(set_reference)).stem)
    return f"manual_{int(portfolio_id):06d}_{stem}"


def report_for_set(
    project: Path, set_reference: Any, portfolio_id: Any = None,
) -> Path | None:
    """El informe OHLC de un set manual, en `<proyecto>/reports/`.

    Es **la** regla de emparejado entre un set manual y su histórico, y vive
    aquí sola a propósito: cualquiera que quiera ese histórico —el resumen del
    portafolio manual o la tabla «contra el histórico» de la auditoría— tiene
    que buscarlo igual. Dos reglas serían dos respuestas distintas a la misma
    pregunta.

    Se mira primero `manual_<portafolio>_<set>`, que es lo que escribe el nodo
    desde que el histórico corre sobre una copia renombrada, y después el
    nombre pelado del set, que es como quedaron los históricos anteriores. Sin
    el número de portafolio sólo queda la segunda forma: no se busca con un
    `glob`, porque `reports/` pasa de 850.000 ficheros y esto se consulta por
    estrategia.
    """
    name = _portable_set_name(set_reference)
    reports = project / "reports"
    if not name or not reports.is_dir():
        return None
    stems = [Path(name).stem]
    if portfolio_id is not None:
        stems.insert(0, manual_report_stem(portfolio_id, name))
    for stem in stems:
        matches = [
            path for suffix in (".htm", ".html")
            if (path := reports / f"{stem}{suffix}").is_file()
        ]
        if matches:
            return max(matches, key=lambda path: path.stat().st_mtime)
    return None


def _matching_report(
    project: Path, member: dict[str, Any], portfolio_id: Any = None,
) -> Path | None:
    return report_for_set(
        project, member.get("set_path") or member.get("set_id"), portfolio_id,
    )


def manual_history_summary(
    project: Path, members: list[dict[str, Any]], portfolio_id: Any = None,
) -> dict[str, Any]:
    """Relaciona cada set manual con el último reporte que dejó el nodo."""
    strategies = []
    for member in members:
        path = _matching_report(project, member, portfolio_id)
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
