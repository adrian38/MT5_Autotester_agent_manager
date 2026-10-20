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

from .live_audit_archive import composite, strategies_of, trimmed_detail
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


def tester_trades_by_identity(
    detail: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Igual que la anterior, pero con la identidad `candidato|fichero`.

    Es la que vale para mezclar auditorias de portafolios distintos: ver
    `live_audit_archive.composite`.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in detail.get("operation_comparisons") or []:
        tester = row.get("tester")
        if not isinstance(tester, dict) or not tester.get("close_time"):
            continue
        strategy = str(row.get("strategy") or tester.get("strategy") or "")
        grouped.setdefault(
            (composite(strategy, row.get("strategy_set")), strategy), [],
        ).append(tester)
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


def covered_span(entries: list[dict[str, Any]]) -> tuple[str, str]:
    """Primer dia auditado y ultimo, en ISO, o dos cadenas vacias.

    «11 dias auditados» no dice cuales: dos series del mismo tamano pueden ser
    de meses distintos, y la de hace medio ano no decide sobre hoy. Se toman el
    minimo y el maximo porque las entradas llegan ordenadas por `executed_at`,
    que no tiene por que seguir el orden de los periodos.
    """
    intervals = list(filter(None, (_interval(entry) for entry in entries)))
    if not intervals:
        return "", ""
    return (
        min(start for start, _ in intervals).isoformat(),
        max(end for _, end in intervals).isoformat(),
    )


def accumulate(
    archive: Any, node_id: str, audit_key: str, detail: dict[str, Any],
    entries: list[dict[str, Any]] | None = None,
    inherited_portfolio_ids: tuple[int, ...] | list[int] = (),
) -> dict[str, Any]:
    """Operaciones del tester por estrategia en toda la serie, sin repetir.

    `detail` es la comparacion vigente; `entries` las entradas del indice que
    hay que sumar, o las que decida el archivo si no se dan.
    """
    wanted = set(strategies_of(detail))
    rows = archive.related(
        node_id, audit_key, list(wanted), inherited_portfolio_ids,
    ) if entries is None else entries
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple] = set()
    portfolios: dict[int, None] = {}
    for entry in rows:
        for (identity, strategy), trades in tester_trades_by_identity(
            archive.detail(node_id, entry),
        ).items():
            if identity not in wanted:
                continue
            for trade in trades:
                key = _trade_key(identity, trade)
                if key in seen:
                    continue
                seen.add(key)
                grouped.setdefault(strategy, []).append(trade)
        portfolio = int(entry.get("portfolio_id") or 0)
        if portfolio:
            portfolios[portfolio] = None
    for (identity, strategy), trades in tester_trades_by_identity(detail).items():
        for trade in trades:
            key = _trade_key(identity, trade)
            if key not in seen:
                seen.add(key)
                grouped.setdefault(strategy, []).append(trade)
    since, until = covered_span(rows)
    return {
        "trades": grouped, "days": covered_days(rows), "audits": len(rows),
        "portfolios": sorted(portfolios),
        "since": since, "until": until,
        "inherited": sorted({
            int(entry.get("portfolio_id") or 0) for entry in rows if not entry.get("own")
        } - {0}),
    }


def _retained_detail(detail: dict[str, Any], wanted: set[str]) -> dict[str, Any]:
    """Recorta una ejecucion historica a las estrategias que siguen presentes."""
    trimmed = trimmed_detail(detail)
    for key in ("operation_comparisons", "strategy_summary"):
        trimmed[key] = [
            row for row in trimmed.get(key) or []
            if composite(
                row.get("strategy") or (row.get("tester") or {}).get("strategy"),
                row.get("strategy_set"),
            ) in wanted
        ]
    return trimmed


def series(
    archive: Any, node_id: str, audit_key: str, detail: dict[str, Any],
    inherited_portfolio_ids: tuple[int, ...] | list[int] = (),
) -> dict[str, Any]:
    """Lo que la pantalla necesita para la tabla «demo contra real» historica."""
    wanted = set(strategies_of(detail))
    entries = archive.related(
        node_id, audit_key, list(wanted), inherited_portfolio_ids,
    )
    executions = [{
        **{key: value for key, value in entry.items() if key != "file"},
        "comparison_detail": _retained_detail(archive.detail(node_id, entry), wanted),
    } for entry in entries]
    summary = accumulate(
        archive, node_id, audit_key, detail, entries, inherited_portfolio_ids,
    )
    return {
        "executions": executions,
        "audits": len(executions),
        "portfolios": summary["portfolios"],
        "inherited_portfolios": summary["inherited"],
        "covered_days": summary["days"],
        "covered_since": summary["since"],
        "covered_until": summary["until"],
    }
