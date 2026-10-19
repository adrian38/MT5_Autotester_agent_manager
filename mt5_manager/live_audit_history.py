"""La serie de auditorias de un uso: lo que el archivo sabe, ya mezclado.

Dos consumidores, el mismo material:

- la tabla «demo contra real» historica, que necesita cada ejecucion entera
  para construir sus grupos con el mismo criterio que la del periodo;
- la tabla «contra el historico», que dejo de mirar solo el periodo: con tres
  operaciones en una semana ninguna conclusion se sostenia, y la pantalla
  contestaba «INSUFICIENTE» en todas las filas. Acumulando las operaciones del
  tester de todas las auditorias, la muestra crece sola cada semana.

Las operaciones se acumulan **por estrategia**, no por portafolio, y por eso un
portafolio arreglado hereda el historico del que sustituye: el portafolio viejo
sale de la lista de activos y no se vuelve a auditar, pero sus estrategias
siguen siendo las mismas y lo que se midio de ellas sigue contando.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .live_audit_archive import strategies_of, trimmed_detail
from .live_audit_schedule import parse_timestamp


def _trade_key(strategy: str, trade: dict[str, Any]) -> tuple:
    """Que hace unica a una operacion del tester entre auditorias.

    Los periodos se encadenan repitiendo el ultimo dia, asi que dos auditorias
    consecutivas traen las mismas operaciones de esa jornada. Sin esta clave,
    acumular las contaria dos veces.
    """
    return (
        strategy, str(trade.get("symbol") or ""), str(trade.get("close_time") or ""),
        str(trade.get("open_time") or ""), round(float(trade.get("volume") or 0), 4),
        round(float(trade.get("profit") or 0), 4),
    )


def tester_trades_by_strategy(detail: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Las operaciones del tester de una comparacion, agrupadas por candidato."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in detail.get("operation_comparisons") or []:
        tester = row.get("tester")
        if not isinstance(tester, dict) or not tester.get("close_time"):
            continue
        key = str(row.get("strategy") or tester.get("strategy") or "")
        grouped.setdefault(key, []).append(tester)
    return grouped


def _interval(entry: dict[str, Any]) -> tuple[datetime, datetime] | None:
    start = parse_timestamp(entry.get("period_start"))
    end = parse_timestamp(entry.get("period_end"))
    return (start, end) if start and end and end > start else None


def covered_days(entries: list[dict[str, Any]]) -> float:
    """Dias auditados en total, contando una sola vez lo que se solape.

    Sumar los periodos uno a uno inflaria el ritmo esperado: el encadenado
    repite a proposito el ultimo dia de la auditoria anterior.
    """
    intervals = sorted(
        filter(None, (_interval(entry) for entry in entries)), key=lambda pair: pair[0],
    )
    total, current = 0.0, None
    for start, end in intervals:
        if current and start <= current[1]:
            current = (current[0], max(current[1], end))
            continue
        if current:
            total += (current[1] - current[0]).total_seconds()
        current = (start, end)
    if current:
        total += (current[1] - current[0]).total_seconds()
    return total / 86400


def accumulate(
    archive: Any, node_id: str, audit_key: str, detail: dict[str, Any],
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Operaciones del tester por estrategia en toda la serie, sin repetir.

    `detail` es la comparacion vigente; `entries` las entradas del indice que
    hay que sumar, o las que decida el archivo si no se dan.
    """
    rows = archive.related(node_id, audit_key, strategies_of(detail)) if entries is None else entries
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple] = set()
    portfolios: dict[int, None] = {}
    for entry in rows:
        for strategy, trades in tester_trades_by_strategy(
            archive.detail(node_id, entry),
        ).items():
            for trade in trades:
                key = _trade_key(strategy, trade)
                if key in seen:
                    continue
                seen.add(key)
                grouped.setdefault(strategy, []).append(trade)
        portfolio = int(entry.get("portfolio_id") or 0)
        if portfolio:
            portfolios[portfolio] = None
    for strategy, trades in tester_trades_by_strategy(detail).items():
        for trade in trades:
            key = _trade_key(strategy, trade)
            if key not in seen:
                seen.add(key)
                grouped.setdefault(strategy, []).append(trade)
    return {
        "trades": grouped, "days": covered_days(rows), "audits": len(rows),
        "portfolios": sorted(portfolios),
        "since": str((rows[0] if rows else {}).get("period_start") or ""),
        "inherited": sorted({
            int(entry.get("portfolio_id") or 0) for entry in rows if not entry.get("own")
        } - {0}),
    }


def series(archive: Any, node_id: str, audit_key: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Lo que la pantalla necesita para la tabla «demo contra real» historica."""
    entries = archive.related(node_id, audit_key, strategies_of(detail))
    executions = [{
        **{key: value for key, value in entry.items() if key != "file"},
        "comparison_detail": trimmed_detail(archive.detail(node_id, entry)),
    } for entry in entries]
    summary = accumulate(archive, node_id, audit_key, detail, entries)
    return {
        "executions": executions,
        "audits": len(executions),
        "portfolios": summary["portfolios"],
        "inherited_portfolios": summary["inherited"],
        "covered_days": summary["days"],
    }
