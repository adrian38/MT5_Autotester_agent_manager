"""OHLC contra tick a tick: lo que dicen los dos informes de cada estrategia.

Una comprobación manual deja dos informes por estrategia, uno por tipo de
datos. Mirarlos abriendo los dos `.htm` y comparando números a ojo es
exactamente lo que esta pantalla evita: aquí se leen los dos, se recorta lo
que de verdad se compara y se devuelve junto.

**Las cifras son las del propio informe de MT5**, no un recálculo: si el .htm
dice 322,61 de beneficio neto, eso es lo que sale. Recalcularlas desde las
operaciones daría números parecidos y distintos, y nadie sabría cuál creer.
La curva sí se construye aquí, porque el informe no la publica como serie.

La cuenta de un tipo de datos puede faltar —se lanzó sólo uno, o el tester no
dejó informe de esa corrida—: eso se dice, no se rellena con ceros.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .portfolio_manual_check import REPORT_MODELS, check_dir, check_state
from .portfolio_report_cache import cached_report


METRIC_KEYS = (
    "Total de operaciones ejecutadas",
    "Total de transacciones",
    "Beneficio Neto",
    "Beneficio Bruto",
    "Reducción máxima del balance",
)
"""Las cinco cifras que el usuario compara, con el nombre que usa MT5.

Se leen por su literal del informe: el parser devuelve la tabla tal cual, y
traducir aquí los nombres sólo serviría para que la pantalla y el .htm dejaran
de decir lo mismo.
"""


def _report_path(work: Path, model: str, stem: str) -> Path | None:
    directory = work / "reports" / model
    if not directory.is_dir() or not stem:
        return None
    for suffix in (".htm", ".html"):
        candidate = directory / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _curve(report: Any) -> list[dict[str, Any]]:
    """PnL cerrado acumulado, una parada por operación cerrada."""
    closed = [
        trade for trade in report.trades
        if getattr(trade, "close_time", None) is not None
    ]
    closed.sort(key=lambda trade: trade.close_time)
    total = 0.0
    points: list[dict[str, Any]] = []
    for trade in closed:
        total += float(getattr(trade, "profit_loss", 0.0) or 0.0)
        points.append({
            "time": trade.close_time.isoformat(),
            "value": round(total, 2),
        })
    return points


def _model_analysis(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"available": False, "metrics": {}, "curve": [], "report": ""}
    report = cached_report(path)
    metrics = {key: str(report.metrics.get(key, "")) for key in METRIC_KEYS}
    curve = _curve(report)
    return {
        "available": True,
        "report": path.name,
        "metrics": metrics,
        "curve": curve,
        "closed_trades": len(curve),
        "net": curve[-1]["value"] if curve else 0.0,
        "symbol": str(getattr(report, "symbol", "") or ""),
        "timeframe": str(getattr(report, "timeframe", "") or ""),
    }


def _strategy_rows(work: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in request.get("strategies") or []:
        if not isinstance(strategy, dict):
            continue
        stem = str(strategy.get("report_stem") or "")
        models = {
            model: _model_analysis(_report_path(work, model, stem))
            for model in REPORT_MODELS
        }
        rows.append({
            "key": stem,
            "set_name": str(strategy.get("set_name") or stem),
            "symbol": str(strategy.get("symbol") or ""),
            "timeframe": str(strategy.get("timeframe") or ""),
            "candidate_id": str(strategy.get("candidate_id") or ""),
            "models": models,
            "comparable": all(models[model]["available"] for model in REPORT_MODELS),
        })
    return rows


def analysis(project: Path, check_id: str) -> dict[str, Any]:
    """Las dos lecturas de cada estrategia de esa comprobación."""
    work = check_dir(project, check_id)
    state = check_state(project, check_id)
    request = state.get("request") or {}
    rows = _strategy_rows(work, request)
    return {
        "check_id": state["check_id"],
        "portfolio_id": int(request.get("portfolio_id") or 0),
        "scope": str(request.get("scope") or "full_history"),
        "from_date": str(request.get("from_date") or ""),
        "to_date": str(request.get("to_date") or ""),
        "lot": request.get("lot"),
        "models": list(request.get("models") or REPORT_MODELS),
        "created_at": str(request.get("created_at") or ""),
        "metric_keys": list(METRIC_KEYS),
        "strategies": rows,
    }
