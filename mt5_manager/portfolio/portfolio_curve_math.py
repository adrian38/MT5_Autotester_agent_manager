"""Curva, valles y mensual de una lista de operaciones cerradas.

Está abajo del todo de la pila y **no importa nada del paquete**: lo necesitan
el histórico de un portafolio UBS guardado (`portfolio_history_detail`) y el de
uno manual (`portfolio_manual_reports`), que viven en escalones distintos. Si
la cuenta estuviera en uno de los dos, el otro tendría que importar hacia
arriba o copiarla, y dos copias de la misma cuenta acaban dando dos cifras.

Una operación es `(cierre, beneficio)`, ya escalado por quien la pasa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


Trade = tuple[datetime, float]

TOP_VALLEYS = 5
"""Cuántos valles se desglosan, que es lo que el usuario pidió ver."""


def drawdown(trades: list[Trade]) -> float:
    """La caída máxima desde un máximo, sobre el acumulado de cierres."""
    balance = peak = worst = 0.0
    for _closed_at, profit in trades:
        balance += profit
        peak = max(peak, balance)
        worst = max(worst, peak - balance)
    return worst


def monthly_from_trades(trades: list[Trade]) -> dict[int, dict[int, float]]:
    """El mensual, contado sobre las mismas operaciones que el total.

    El `monthly` del informe lo construye MT5 sumando **transacciones** por su
    fecha, no operaciones casadas por su cierre. Medido en el #62, aquella
    tabla sumaba 20.053,46 y el beneficio neto 18.852,12: dos cifras que no
    cuadran en la misma pantalla no las cuadra nadie.
    """
    monthly: dict[int, dict[int, float]] = {}
    for closed_at, profit in trades:
        row = monthly.setdefault(closed_at.year, {})
        row[closed_at.month] = row.get(closed_at.month, 0.0) + profit
    return monthly


def daily_curve(trades: list[Trade]) -> list[dict[str, Any]]:
    """PnL cerrado acumulado, una parada por día con operaciones.

    Por operación serían decenas de miles de puntos para dibujar la misma
    línea —14.908 operaciones son 1.652 días—. Las cifras se siguen calculando
    sobre cada operación cerrada.
    """
    total = 0.0
    points: list[dict[str, Any]] = []
    for closed_at, profit in trades:
        total += profit
        day = closed_at.date().isoformat()
        if points and points[-1]["time"] == day:
            points[-1]["value"] = round(total, 2)
            continue
        points.append({"time": day, "value": round(total, 2)})
    return points


def valley_episodes(trades: list[Trade]) -> list[dict[str, Any]]:
    """Cada caída desde un máximo hasta que lo recupera, con su hondura.

    El último se queda abierto si nunca se recuperó, y eso se dice en vez de
    cerrarlo a la fuerza.
    """
    episodes: list[dict[str, Any]] = []
    balance = peak = 0.0
    current: dict[str, Any] | None = None
    for closed_at, profit in trades:
        balance += profit
        if balance >= peak:
            if current is not None:
                current["recovered_at"] = closed_at.isoformat()
                current["end"] = closed_at
                episodes.append(current)
                current = None
            peak = balance
            continue
        if current is None:
            current = {
                "started_at": closed_at.isoformat(), "start": closed_at,
                "peak": peak, "depth": 0.0, "trough_at": closed_at.isoformat(),
                "trough": closed_at, "recovered_at": "", "end": closed_at,
            }
        depth = peak - balance
        if depth > current["depth"]:
            current["depth"] = depth
            current["trough_at"] = closed_at.isoformat()
            current["trough"] = closed_at
        current["end"] = closed_at
    if current is not None:
        episodes.append(current)
    return episodes


def _breakdown(valley: dict[str, Any], strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quién cavó el valle: lo que aportó cada estrategia **hasta el fondo**.

    La ventana termina en el fondo y no en la recuperación a propósito. Con la
    recuperación dentro, lo que subió compensa a lo que bajó y el desglose
    contesta otra pregunta: una estrategia que perdió 40 y luego ganó 50
    aparecía con +10 como causante de un valle de 40.
    """
    rows = []
    for strategy in strategies:
        inside = [
            profit for closed_at, profit in strategy["trades"]
            if valley["start"] <= closed_at <= valley["trough"]
        ]
        if not inside:
            continue
        rows.append({
            "set_name": strategy["set_name"],
            "symbol": strategy.get("symbol", ""),
            "timeframe": strategy.get("timeframe", ""),
            "units": strategy.get("units", 1),
            "trades": len(inside),
            "net_profit": sum(inside),
        })
    rows.sort(key=lambda row: row["net_profit"])
    return rows


def deepest_valleys(
    trades: list[Trade], strategies: list[dict[str, Any]], limit: int = TOP_VALLEYS,
) -> list[dict[str, Any]]:
    """Los valles más hondos, cada uno con su desglose por estrategia.

    `strategies` son filas con `set_name` y `trades`; lo demás es opcional y
    viaja tal cual al desglose.
    """
    deepest = sorted(valley_episodes(trades), key=lambda item: item["depth"], reverse=True)
    return [
        {
            "started_at": valley["started_at"],
            "trough_at": valley["trough_at"],
            "recovered_at": valley["recovered_at"],
            "peak": round(valley["peak"], 2),
            "depth": round(valley["depth"], 2),
            "strategies": _breakdown(valley, strategies),
        }
        for valley in deepest[:limit]
    ]
