"""Lo que el manager deriva de una ejecución guardada, antes de enrutarla.

`manager_live_audit_routes.py` pasó de 600 líneas al añadir los tramos de
lote, y el corte natural es éste: aquí vive lo que **lee** una ejecución —le
aplica el criterio vigente y la compara con el histórico del agente— y allí
queda lo que resuelve una petición HTTP.

`analysed_live_audit` es el punto por el que el criterio del manager entra en
la materia prima del nodo, así que lo usan tanto las rutas de esta área como
el vigilante del cron (`manager_schedule_routes`). `manager_live_audit_routes`
reexporta los cuatro nombres: quien los importaba de allí no cambia.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Any

from . import live_audit_analysis
from . import live_audit_degradation
from . import live_audit_executions
from . import live_audit_history, live_audit_lineage
from . import manager_http
from .portfolio_service import PortfolioSource


def strategy_history(handler, node_id: str, audit_id: str) -> dict[str, Any]:
    """Veredicto de degradacion por estrategia del uso pedido.

    Se lee del nodo la ejecucion ya analizada y de la memoria UBS del agente el
    historico de cada candidato. Es una ruta aparte y no parte del detalle
    porque la pantalla de configuracion sondea el detalle en bucle y esto abre
    la base del agente.
    """
    node = handler._node(node_id)
    status, value = manager_http.node_request(
        node, "GET", f"/api/v1/live-audits/{urllib.parse.quote(audit_id, safe='')}", timeout=10
    )
    if status != 200:
        raise ValueError(str((value or {}).get("error") if isinstance(value, dict) else value))
    analysed = analysed_live_audit(handler, node_id, audit_id, value)
    state = analysed.get("audit") if isinstance(analysed.get("audit"), dict) else analysed
    result = (state or {}).get("last_result")
    if not isinstance(result, dict):
        return {"strategies": [], "reference_lot": live_audit_degradation.REFERENCE_LOT}
    # Acumulado, no solo el periodo: tres operaciones en una semana no
    # concluyen nada y la tabla contestaba «INSUFICIENTE» en todas las filas.
    accumulated = live_audit_history.accumulate(
        handler.server.live_audit_archive, node_id, audit_id,
        result.get("comparison_detail") or {},
        inherited_portfolio_ids=live_audit_lineage.ancestors_of(
            handler.server, node_id, audit_id,
        ),
    )
    report = degradation_report(
        PortfolioSource(node), result,
        grouped=accumulated["trades"], days=accumulated["days"],
    )
    report["accumulated"] = {
        key: accumulated[key]
        for key in ("audits", "days", "portfolios", "since", "until", "inherited")
    }
    return report


def _period_days(result: dict[str, Any]) -> float:
    start = str(result.get("period_start") or "")
    end = str(result.get("period_end") or "")
    try:
        span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, span.total_seconds() / 86400)


def degradation_report(
    source: Any, result: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]] | None = None, days: float | None = None,
) -> dict[str, Any]:
    """Compara cada estrategia con su historico en la memoria del agente.

    Por defecto mide el periodo auditado. `grouped` y `days` permiten medir la
    serie entera, que es lo que hace la pantalla: el criterio es el mismo y lo
    unico que cambia es el tamano de la muestra.
    """
    if grouped is None:
        grouped = live_audit_history.tester_trades_by_strategy(
            result.get("comparison_detail") or {},
        )
    if days is None:
        days = _period_days(result)
    histories = live_audit_degradation.read_histories(source, list(grouped))
    sets = {
        str(row.get("strategy") or ""): str(row.get("strategy_set") or "")
        for row in (result.get("comparison_detail") or {}).get("strategy_summary") or []
    }
    symbols = {
        strategy: str((trades[0] or {}).get("symbol") or "")
        for strategy, trades in grouped.items() if trades
    }
    strategies = [{
        "strategy": strategy, "strategy_set": sets.get(strategy, ""),
        "symbol": symbols.get(strategy, ""),
        **live_audit_degradation.compare(histories.get(strategy), trades, days),
    } for strategy, trades in grouped.items()]
    strategies.sort(key=lambda row: (row["symbol"], row["strategy_set"], row["strategy"]))
    return {
        "strategies": strategies, "period_days": days,
        "reference_lot": live_audit_degradation.REFERENCE_LOT,
        "minimum_trades": live_audit_degradation.MINIMUM_TRADES,
    }


def analysed_live_audit(
    handler, node_id: str, audit_id: str, value: Any, node: dict[str, Any] | None = None,
) -> Any:
    """Aplica el criterio vigente del manager a la materia prima del nodo.

    El trabajo esta en `live_audit_executions`, que es la misma primitiva que
    usa el vigilante del cron: dos formas de analizar eran dos criterios, y la
    del vigilante se quedo sin el saldo historico que pide el autolote.
    """
    if not live_audit_analysis.has_raw_material(value):
        return value
    settings = handler.server.live_audit_settings.state(node_id)
    return live_audit_executions.analysed_node_state(
        node or {}, value, (settings.get("profiles") or {}).get(audit_id) or {},
        memory=getattr(handler.server, "live_audit_manual_memory", None), node_id=node_id,
    )
