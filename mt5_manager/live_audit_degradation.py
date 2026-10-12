"""Compara lo que una estrategia hizo en el periodo auditado con su histórico.

El histórico de un candidato está en la memoria UBS del agente y se mide **por
operación**, no por periodo: `net_profit`, `trades`, `avg_trade` y `sqn` sobre
los cinco años del backtest. Para decidir si una semana concreta se sale de lo
normal hace falta la dispersión, y `sqn` la contiene:

    SQN = media_por_operacion / desviacion_por_operacion * raiz(operaciones)

así que la desviación se despeja. Con media y desviación históricas, la media
observada en el periodo es un contraste de medias al uso y el veredicto deja de
ser una opinión sobre dos números sueltos.

**Todo se normaliza a un lote.** El backtest del candidato corre a
`REFERENCE_LOT` (0,01: es el `StartLots` de los sets que genera el pipeline) y
la auditoría corre al lote del portafolio. Comparar el PnL crudo mediría el
tamaño de la posición, no el comportamiento de la estrategia.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

# `StartLots` de los sets generados por el pipeline UBS. El histórico de la
# memoria está medido a este lote; el periodo auditado, al del portafolio.
REFERENCE_LOT = 0.01

# Por debajo de esto no se opina: con pocas operaciones una sola mala arrastra
# la media y el contraste da «degrada» por ruido. No es un umbral estadístico
# sino el punto donde el aviso dejaría de ser accionable.
MINIMUM_TRADES = 8

# Desviaciones típicas de la media histórica. Dos es el umbral habitual de
# «esto ya no parece la misma estrategia»; una, el de «míralo».
WARNING_SIGMA = 1.0
DEGRADED_SIGMA = 2.0

VERDICT_LABELS = {
    "no_history": "SIN HISTÓRICO",
    "no_trades": "SIN OPERACIONES",
    "insufficient": "INSUFICIENTE",
    "no_dispersion": "SIN DISPERSIÓN HISTÓRICA",
    "degraded": "DEGRADA",
    "warning": "AVISO",
    "normal": "NORMAL",
    "better": "MEJOR QUE EL HISTÓRICO",
}

AVERAGE_DAYS_PER_MONTH = 30.436875


@dataclass(frozen=True)
class StrategyHistory:
    """Lo que la memoria UBS sabe del candidato, medido a `REFERENCE_LOT`."""

    trades: int
    net_profit: float
    average_trade: float
    sqn: float | None
    active_months: float | None
    drawdown: float | None
    profit_factor: float | None

    @property
    def trade_deviation(self) -> float | None:
        """Desviación por operación despejada del SQN.

        `SQN = media / desviacion * raiz(n)`, así que
        `desviacion = media * raiz(n) / SQN`. Sin SQN, sin media o con cero
        operaciones no hay dispersión que despejar y no se inventa.
        """
        if not self.sqn or self.trades <= 0 or not self.average_trade:
            return None
        deviation = abs(self.average_trade) * math.sqrt(self.trades) / abs(self.sqn)
        return deviation if deviation > 0 else None


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def history_from_metrics(metrics: dict[str, Any] | None) -> StrategyHistory | None:
    """Lee el `metrics_json` del candidato; `None` si no trae lo imprescindible.

    Se usa `raw_net_profit` cuando está: `net_profit` puede venir normalizado
    por nocional en algunos brokers y entonces no es dinero comparable con el
    de una cuenta.
    """
    if not isinstance(metrics, dict):
        return None
    trades = _number(metrics.get("trades"))
    if trades is None or trades <= 0:
        return None
    net = _number(metrics.get("raw_net_profit"))
    if net is None:
        net = _number(metrics.get("net_profit"))
    if net is None:
        return None
    average = _number(metrics.get("avg_trade"))
    if average is None:
        average = net / trades
    return StrategyHistory(
        trades=int(trades), net_profit=net, average_trade=average,
        sqn=_number(metrics.get("sqn")),
        active_months=_number(metrics.get("active_months")),
        drawdown=_number(metrics.get("drawdown")),
        profit_factor=_number(metrics.get("profit_factor")),
    )


def observed_per_lot(trades: list[dict[str, Any]]) -> tuple[float, float] | None:
    """PnL medio por lote y volumen total de las operaciones del periodo.

    Cada operación se lleva primero a un lote y después se promedian esos
    rendimientos. Así todos los cierres pesan igual que en la media histórica,
    aunque la estrategia haya operado volúmenes distintos durante el periodo.
    """
    profit_per_lot = 0.0
    volume = 0.0
    count = 0
    for trade in trades:
        size = _number(trade.get("volume"))
        gain = _number(trade.get("profit"))
        if size is None or gain is None or size <= 0:
            continue
        profit_per_lot += gain / size
        volume += size
        count += 1
    return (profit_per_lot / count, volume) if count else None


def expected_trades(history: StrategyHistory, period_days: float) -> float | None:
    """Operaciones que el histórico haría en un periodo de esa longitud."""
    months = history.active_months
    if not months or months <= 0 or period_days <= 0:
        return None
    return history.trades / (months * AVERAGE_DAYS_PER_MONTH) * period_days


def _verdict_for(deviations: float) -> str:
    if deviations <= -DEGRADED_SIGMA:
        return "degraded"
    if deviations <= -WARNING_SIGMA:
        return "warning"
    if deviations >= DEGRADED_SIGMA:
        return "better"
    return "normal"


def compare(
    history: StrategyHistory | None, trades: list[dict[str, Any]],
    period_days: float, reference_lot: float = REFERENCE_LOT,
) -> dict[str, Any]:
    """Veredicto de una estrategia en el periodo frente a su histórico.

    `trades` son las operaciones del tester tal como se ejecutaron: no se les
    aplica la simulación de lotes porque la comparación ya es por lote y el
    tamaño que se hubiera operado no cambia el comportamiento medido.
    """
    count = len(trades)
    base = {
        "trades": count, "reference_lot": reference_lot,
        "history": None, "expected_trades": None,
        "observed_per_lot": None, "history_per_lot": None,
        "observed_at_reference_lot": None, "history_at_reference_lot": None,
        "deviation_per_lot": None, "deviations": None,
        "difference_pct": None, "observed_volume": None,
    }
    if history is None:
        return {**base, "verdict": "no_history"}
    per_lot = history.average_trade / reference_lot if reference_lot > 0 else None
    base = {
        **base,
        "history": {
            "trades": history.trades, "net_profit": round(history.net_profit, 2),
            "average_trade": history.average_trade, "sqn": history.sqn,
            "active_months": history.active_months, "drawdown": history.drawdown,
            "profit_factor": history.profit_factor,
        },
        "expected_trades": expected_trades(history, period_days),
        "history_per_lot": per_lot,
        "history_at_reference_lot": history.average_trade,
    }
    observed = observed_per_lot(trades)
    if not count or observed is None:
        return {**base, "verdict": "no_trades"}
    base["observed_per_lot"], base["observed_volume"] = observed
    base["observed_at_reference_lot"] = observed[0] * reference_lot
    if per_lot:
        base["difference_pct"] = (observed[0] - per_lot) / abs(per_lot) * 100
    if count < MINIMUM_TRADES:
        return {**base, "verdict": "insufficient", "minimum_trades": MINIMUM_TRADES}
    deviation = history.trade_deviation
    if deviation is None or per_lot is None:
        return {**base, "verdict": "no_dispersion"}
    deviation_per_lot = deviation / reference_lot
    base["deviation_per_lot"] = deviation_per_lot
    deviations = (observed[0] - per_lot) / (deviation_per_lot / math.sqrt(count))
    base["deviations"] = deviations
    return {**base, "verdict": _verdict_for(deviations)}


def split_candidate_id(candidate_id: str) -> tuple[str, int] | None:
    """`AXI/STANDARD:3813` -> (`AXI/STANDARD`, 3813); `None` si no es del pool.

    La estrategia de una operación puede ser el magic de la cuenta o cualquier
    texto de una ejecución antigua: lo que no tenga esta forma no se busca.
    """
    label, separator, number = str(candidate_id or "").rpartition(":")
    if not separator or not label:
        return None
    try:
        return label, int(number)
    except ValueError:
        return None


def read_histories(source: Any, candidate_ids: list[str]) -> dict[str, StrategyHistory]:
    """Histórico de cada candidato, leído de la memoria UBS del agente.

    `source` es un `PortfolioSource`: aporta `memory_sources` —cada cuenta del
    broker con su base— y `connect_memory`, que ya resuelve el acceso de sólo
    lectura y las instantáneas de las bases remotas. Aquí no se abre nada a
    mano ni se escribe nunca.
    """
    wanted: dict[str, dict[int, str]] = {}
    for candidate_id in candidate_ids:
        parsed = split_candidate_id(candidate_id)
        if parsed is None:
            continue
        label, number = parsed
        wanted.setdefault(label, {})[number] = candidate_id
    histories: dict[str, StrategyHistory] = {}
    for label, memory in getattr(source, "memory_sources", []):
        rows = wanted.get(label)
        if not rows:
            continue
        histories.update(_read_memory(source, memory, rows))
    return histories


def _read_memory(
    source: Any, memory: Any, rows: dict[int, str],
) -> dict[str, StrategyHistory]:
    found: dict[str, StrategyHistory] = {}
    placeholders = ",".join("?" for _ in rows)
    try:
        with source.connect_memory(memory) as conn:
            selected = conn.execute(
                f"select id, metrics_json from candidates where id in ({placeholders})",
                tuple(rows),
            ).fetchall()
    except (sqlite3.Error, OSError, ValueError):
        # Una memoria ilegible no puede tumbar la pantalla: esas estrategias
        # salen como «sin histórico», que es exactamente lo que se sabe.
        return found
    for row in selected:
        try:
            metrics = json.loads(row[1] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        history = history_from_metrics(metrics)
        if history is not None:
            found[rows[int(row[0])]] = history
    return found


__all__ = [name for name in globals() if not name.startswith("_")]
