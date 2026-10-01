"""Convierte la materia prima que observó el nodo en el veredicto de la auditoría.

El reparto es el que siempre debió ser: el nodo **ejecuta** —pausa el pipeline,
extrae el historial con la API de MT5, lanza el Strategy Tester, exporta los
informes nativos y restaura las cuentas— y publica lo que vio. Todo el
**criterio** vive aquí: filtro de pertenencia al portafolio, puerta de History
Quality, tolerancias y comparación operación por operación.

Dos consecuencias, que son el motivo del cambio:

- Una tolerancia nueva se aplica a una ejecución ya guardada sin volver a abrir
  un terminal ni parar el pipeline del agente.
- La regla existe una sola vez. Antes había que portarla a mano a la copia de
  cada broker, y bastaba olvidar una para que ese agente auditase con un
  criterio distinto sin que nada lo dijese.

Límite deliberado: se puede reanalizar con otras tolerancias, **no** con otro
periodo ni otros lotes de tester. Eso cambia lo que el Strategy Tester ejecutó,
así que exige repetir la auditoría. `analyze` toma de la configuración actual
solo lo que es criterio, y de la ejecución todo lo que es hecho observado.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .live_audit_engine import (
    LiveAuditController, _member_strategy_id, _trade_view,
)

# Lo que el usuario puede cambiar y volver a aplicar sobre una ejecución guardada.
REANALYSABLE_TOLERANCES = (
    "trade_time_tolerance_seconds",
    "price_tolerance_points",
    "volume_tolerance_pct",
    "pnl_deviation_warning_pct",
    "drawdown_deviation_warning_pct",
    "min_tick_history_quality_pct",
    "real_strategy_lots",
)
# Lo que describe la ejecución y no se puede reinterpretar sin repetirla.
EXECUTION_FACTS = (
    "audit_key", "portfolio_id", "portfolio_type",
    "period_mode", "period_days", "period_start_date", "period_end_date",
)


class PayloadError(ValueError):
    """La materia prima del nodo no permite analizar la ejecución."""


def _revive(trade: dict[str, Any] | None) -> dict[str, Any] | None:
    """Devuelve las marcas de tiempo a `datetime`; el transporte es JSON."""
    if not trade:
        return None
    revived = dict(trade)
    for key in ("open_time", "close_time"):
        value = revived.get(key)
        if isinstance(value, str) and value:
            revived[key] = datetime.fromisoformat(value)
    return revived


def _revive_all(trades: Any) -> list[dict[str, Any]]:
    return [revived for revived in (_revive(trade) for trade in (trades or [])) if revived]


def portfolio_signatures(
    members: list[dict[str, Any]],
    volume_rules: dict[str, tuple[float, float]],
    symbols_by_strategy: dict[str, set[str]],
    real_strategy_lots: dict[str, float],
) -> set[tuple[str, float]]:
    """Firmas `(símbolo, lote real)` que identifican los cierres del portafolio.

    El magic del terminal real puede no coincidir con el del `.set` importado,
    así que la pertenencia se decide por símbolo y lote. El lote esperado es el
    configurado para la cuenta real; si no hay, el efectivo del tester.
    """
    signatures: set[tuple[str, float]] = set()
    for member in members:
        strategy = _member_strategy_id(member)
        try:
            _configured, effective_lot, _minimum, _step, _units = LiveAuditController._tester_lot(
                member, volume_rules,
            )
        except (TypeError, ValueError):
            continue
        try:
            real_lot = float(real_strategy_lots.get(strategy, effective_lot))
        except (TypeError, ValueError):
            real_lot = float(effective_lot)
        symbols = symbols_by_strategy.get(strategy) or {
            str(member.get("symbol") or "").casefold()
        }
        signatures.update(
            (symbol, round(real_lot, 8)) for symbol in symbols if symbol and real_lot > 0
        )
    return signatures


def _symbols_by_strategy(
    tester_trades: list[dict[str, Any]], strategy_artifacts: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """El símbolo efectivo del broker sale del reporte, no del portafolio.

    El portafolio guarda `NAS100` y el broker ejecuta `NAS100.fs`; sin esta
    traducción el filtro de pertenencia no encontraría ningún cierre.
    """
    symbols: dict[str, set[str]] = {}
    for trade in tester_trades:
        strategy = str(trade.get("strategy") or "")
        symbol = str(trade.get("symbol") or "").casefold()
        if strategy and symbol:
            symbols.setdefault(strategy, set()).add(symbol)
    for artifact in strategy_artifacts or []:
        strategy = str(artifact.get("strategy") or "")
        symbol = str(artifact.get("report_symbol") or "").casefold()
        if strategy and symbol:
            symbols.setdefault(strategy, set()).add(symbol)
    return symbols


def _volume_rules(payload: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Normaliza las especificaciones del broker que publicó el nodo."""
    rules: dict[str, tuple[float, float]] = {}
    for symbol, raw in (payload.get("volume_rules") or {}).items():
        try:
            if isinstance(raw, dict):
                minimum = float(raw.get("volume_min") or 0.0)
                step = float(raw.get("volume_step") or minimum or 0.0)
            else:
                minimum, step = (float(value) for value in raw)
        except (TypeError, ValueError):
            continue
        if minimum > 0:
            rules[str(symbol).casefold()] = (minimum, step if step > 0 else minimum)
    return rules


def analyze(payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Veredicto de una ejecución a partir de lo observado y del criterio actual.

    `payload` es lo que el nodo midió; `profile`, la configuración vigente del
    uso en el manager. Las tolerancias salen del perfil —por eso se puede
    reanalizar— y el periodo, las cuentas y los informes salen del payload,
    porque describen lo que realmente se ejecutó.
    """
    if not isinstance(payload, dict) or not payload.get("audit_id"):
        raise PayloadError("La ejecución no publicó materia prima analizable")
    executed = dict(payload.get("request") or {})
    request: dict[str, Any] = {
        **executed,
        **{key: profile[key] for key in REANALYSABLE_TOLERANCES if key in profile},
    }
    for key in EXECUTION_FACTS:
        if key in executed:
            request[key] = executed[key]

    period_start = datetime.fromisoformat(str(payload["period_start"]))
    period_end = datetime.fromisoformat(str(payload["period_end"]))
    real_trades = _revive_all(payload.get("real_trades"))
    tester_trades = _revive_all(payload.get("tester_trades"))
    open_positions = _revive_all(payload.get("open_positions_at_period_end"))
    request["real_positions_open_at_period_end"] = open_positions
    symbol_points = {
        str(symbol): float(point or 0.0)
        for symbol, point in (payload.get("symbol_points") or {}).items()
    }
    strategies = {
        str(strategy): int(count or 0)
        for strategy, count in (payload.get("strategies") or {}).items()
    }
    strategy_artifacts = list(payload.get("strategy_artifacts") or [])
    history_detail = dict(payload.get("real_history_detail") or {})
    history_detail["open_positions_at_period_end"] = [
        _trade_view(position) for position in open_positions
    ]

    signatures = portfolio_signatures(
        list(payload.get("selected_members") or []),
        _volume_rules(payload),
        _symbols_by_strategy(tester_trades, strategy_artifacts),
        request.get("real_strategy_lots") or {},
    )
    filter_detail: dict[str, Any] = {"applied": bool(signatures)}
    if signatures:
        before = len(real_trades)
        real_trades = [
            trade for trade in real_trades
            if (
                str(trade.get("symbol") or "").casefold(),
                round(float(trade.get("volume") or 0), 8),
            ) in signatures
        ]
        history_detail["portfolio_closures"] = len(real_trades)
        history_detail["foreign_closures_ignored"] = before - len(real_trades)
        filter_detail.update(
            signatures=sorted(signatures),
            closures_before=before,
            portfolio_closures=len(real_trades),
            foreign_closures_ignored=before - len(real_trades),
        )

    qualities = [float(value) for value in (payload.get("qualities") or []) if value is not None]
    quality = min(qualities) if qualities else None
    minimum_quality = float(request["min_tick_history_quality_pct"])
    result = LiveAuditController._result_base(
        request, period_start, period_end, real_trades, tester_trades, quality,
    )
    if quality is None or quality < minimum_quality:
        result.update(
            status="not_comparable", status_label="NO COMPARABLE", matched_trades=0,
            discrepancies=0, stalled_strategies=0,
            summary=(
                "MT5 no informó History Quality." if quality is None else
                f"History Quality {quality:.2f}% inferior al mínimo {minimum_quality:.2f}%."
            ),
        )
    else:
        comparison = LiveAuditController._compare(
            real_trades, tester_trades, symbol_points, request, strategies,
        )
        result.update(comparison)
        invalid_tester = sum(
            (comparison.get("comparison_detail") or {}).get("tester_data_issues", {}).values()
        )
        result["summary"] = (
            f"{comparison['matched_trades']} parejas alineadas, "
            f"{comparison['within_tolerance_trades']} dentro de todas las tolerancias y "
            f"{comparison['discrepancies']} discrepancias; "
            f"{comparison['stalled_strategies']} estrategia(s) sin continuidad"
            + (f"; {invalid_tester} operación(es) tester con tiempos inválidos." if invalid_tester else ".")
        )
        result["status"] = "completed"
        result["status_label"] = "COMPLETADA"

    result["audit_id"] = payload["audit_id"]
    result["account"] = dict(payload.get("account") or {})
    result["real_history_detail"] = history_detail
    result["portfolio_filter"] = filter_detail
    result["strategy_artifacts"] = strategy_artifacts
    result["tester_execution"] = dict(payload.get("tester_execution") or {})
    result["real_account_report"] = dict(payload.get("real_account_report") or {})
    result["terminal_restore"] = list(payload.get("terminal_restore") or [])
    # Quien lee el resultado tiene que poder distinguir lo observado de lo
    # decidido ahora: si no, un reanálisis con otras tolerancias parece una
    # ejecución nueva.
    result["analysis"] = {
        "analysed_by": "manager",
        "executed_at": payload.get("completed_at"),
        "tolerances": {key: request.get(key) for key in REANALYSABLE_TOLERANCES},
    }
    return result
