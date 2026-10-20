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
from datetime import datetime, timedelta
from typing import Any

from . import live_audit_analysis
from . import live_audit_degradation
from . import live_audit_degradation_evidence
from . import live_audit_executions
from . import live_audit_history, live_audit_lineage
from . import manager_http
from .live_audit_schedule import parse_timestamp
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
    return accumulated_degradation(handler.server, node, node_id, audit_id, result)


def accumulated_degradation(
    server: Any, node: dict[str, Any], node_id: str, audit_key: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """El informe «contra el histórico» de toda la serie, no solo del periodo.

    Tres operaciones en una semana no concluyen nada y la tabla contestaba
    «INSUFICIENTE» en todas las filas.

    Es una sola primitiva porque la pantalla y el aviso de degradación tienen
    que decir lo mismo: dos cálculos serían dos criterios, y el de la campana
    acabaría avisando de algo que Análisis no enseña (o al revés).
    """
    accumulated = live_audit_history.accumulate(
        server.live_audit_archive, node_id, audit_key,
        result.get("comparison_detail") or {},
        inherited_portfolio_ids=live_audit_lineage.ancestors_of(
            server, node_id, audit_key,
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


RECENT_DAYS = 182.0
"""La ventana reciente: medio año.

La serie acumulada **diluye**. Medido sobre la serie real del backtest de
`AXI/STANDARD:10751`, con la cadencia semanal del cron y las dos operaciones
por semana que es la mediana del archivo: una estrategia que funciona un año y
se rompe después sólo se caza el 22 % de las veces mirando lo acumulado, y el
55 % mirando los últimos seis meses. Y cuanto más lleve vigilada, peor: el
tramo bueno pesa cada vez más. Así que se miden las dos cosas y degrada
cualquiera de ellas; las falsas alarmas pasan del 1,3 % al 4,1 % por
estrategia y bienio, que con trece estrategias es media falsa alarma cada dos
años. Detalle en `ai_context/live_audit_degradation_evidence.md`.
"""


def _backtest_of(source: Any, histories: dict[str, Any]) -> Any:
    """El cargador perezoso que recibe `confirm`, atado a esta fuente.

    `confirm` sólo lo llama para las filas donde la respuesta puede cambiar,
    así que el informe de una estrategia tranquila no se abre nunca.
    """
    def load(row: dict[str, Any]) -> Any:
        history = histories.get(str(row.get("strategy") or ""))
        if history is None:
            return None
        return live_audit_degradation_evidence.backtest(
            source, history, live_audit_degradation.REFERENCE_LOT,
        )
    return load


def _recent(
    grouped: dict[str, list[dict[str, Any]]], days: float,
) -> dict[str, list[dict[str, Any]]] | None:
    """Las operaciones de los últimos `RECENT_DAYS`, o `None` si sobran.

    El corte se toma del final de lo auditado, no del reloj: una serie que
    lleva dos semanas sin auditarse no debe perder operaciones por eso. Y si
    todo lo acumulado cabe en la ventana, no hay segunda medición que hacer:
    sería la misma prueba contada dos veces.
    """
    if days <= RECENT_DAYS:
        return None
    stamps = [
        parse_timestamp(trade.get("close_time"))
        for trades in grouped.values() for trade in trades
    ]
    latest = max((stamp for stamp in stamps if stamp), default=None)
    if latest is None:
        return None
    cutoff = latest - timedelta(days=RECENT_DAYS)
    return {
        strategy: [
            trade for trade in trades
            if (parse_timestamp(trade.get("close_time")) or latest) >= cutoff
        ]
        for strategy, trades in grouped.items()
    }


def _merge_recent(rows: list[dict[str, Any]], recent: list[dict[str, Any]]) -> None:
    """Lleva a cada fila lo que dice su ventana reciente, y quién decidió."""
    measured = {str(row.get("strategy") or ""): row for row in recent}
    for row in rows:
        other = measured.get(str(row.get("strategy") or ""))
        row["decided"] = "accumulated" if row.get("verdict") == "degraded" else ""
        if other is None:
            row["recent"] = None
            continue
        row["recent"] = {
            key: other.get(key) for key in (
                "trades", "deviations", "difference_pct", "period_days",
                "observed_at_reference_lot", "verdict", "evidence",
            )
        }
        if other.get("verdict") == "degraded" and row.get("verdict") != "degraded":
            row["verdict"] = "degraded"
            row["decided"] = "recent"


def degradation_report(
    source: Any, result: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]] | None = None, days: float | None = None,
) -> dict[str, Any]:
    """Compara cada estrategia con su historico en la memoria del agente.

    Por defecto mide el periodo auditado. `grouped` y `days` permiten medir la
    serie entera, que es lo que hace la pantalla: el criterio es el mismo y lo
    unico que cambia es el tamano de la muestra.

    El veredicto de `compare` es **provisional**: cruzar −2 σ es la condicion
    de entrada, no la prueba. `live_audit_degradation_evidence.confirm` la
    contrasta aqui, que es el unico sitio donde estan todas las estrategias
    comparadas y por tanto se sabe entre cuantas hay que repartir el nivel.

    Y se mide **dos veces**: la serie entera y los ultimos seis meses, porque
    lo acumulado diluye una rotura reciente (ver `RECENT_DAYS`). Degrada
    cualquiera de las dos, y la fila dice cual.
    """
    if grouped is None:
        grouped = live_audit_history.tester_trades_by_strategy(
            result.get("comparison_detail") or {},
        )
    if days is None:
        days = _period_days(result)
    histories = live_audit_degradation.read_histories(source, list(grouped))
    labels = _labels(result, grouped)
    backtest_of = _backtest_of(source, histories)
    strategies = _measured(grouped, histories, labels, days, backtest_of, rate=True)
    recent = _recent(grouped, days)
    _merge_recent(strategies, [] if recent is None else _measured(
        recent, histories, labels, RECENT_DAYS, backtest_of, rate=False,
    ))
    strategies.sort(key=lambda row: (row["symbol"], row["strategy_set"], row["strategy"]))
    return {
        "strategies": strategies, "period_days": days,
        "recent_days": RECENT_DAYS if recent is not None else None,
        "reference_lot": live_audit_degradation.REFERENCE_LOT,
        "minimum_trades": live_audit_degradation.MINIMUM_TRADES,
    }


def _labels(
    result: dict[str, Any], grouped: dict[str, list[dict[str, Any]]],
) -> dict[str, tuple[str, str]]:
    """Fichero y simbolo de cada estrategia, que no dependen de la ventana."""
    sets = {
        str(row.get("strategy") or ""): str(row.get("strategy_set") or "")
        for row in (result.get("comparison_detail") or {}).get("strategy_summary") or []
    }
    return {
        strategy: (sets.get(strategy, ""), str((trades[0] or {}).get("symbol") or ""))
        for strategy, trades in grouped.items()
    }


def _measured(
    grouped: dict[str, list[dict[str, Any]]], histories: dict[str, Any],
    labels: dict[str, tuple[str, str]], days: float, backtest_of: Any, rate: bool,
) -> list[dict[str, Any]]:
    """Una medicion completa de esa ventana, ya contrastada."""
    rows = [{
        "strategy": strategy,
        "strategy_set": labels.get(strategy, ("", ""))[0],
        "symbol": labels.get(strategy, ("", ""))[1],
        "period_days": days,
        **live_audit_degradation.compare(histories.get(strategy), trades, days),
    } for strategy, trades in grouped.items()]
    live_audit_degradation_evidence.confirm(
        rows, backtest_of, live_audit_degradation.DEGRADED_SIGMA, days if rate else 0.0,
    )
    return rows


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
