"""Medición de riesgo específica de estrategias Grid.

Una estrategia grid no se comporta como una estrategia direccional y por eso el
ámbito Grid necesita sus propias medidas. Dos hechos la separan del resto:

1. Cierra las ganadoras y mantiene abiertas las perdedoras, así que su curva de
   operaciones cerradas es suave y creciente por construcción. Todo lo que se
   calcule sobre esa curva (correlaciones, bootstrap) es ciego al riesgo real,
   que vive en las posiciones abiertas.
2. Escalona el lote internamente. Una "unidad" de cartera es una ejecución
   completa de la estrategia tal como se probó, con su escalera dentro: un set
   con pierna base 0.01 puede llegar a tener 0.95 lotes abiertos a la vez. El
   margen de una sola posición mínima no describe esa exposición.

Este módulo mide ambas cosas a partir de las operaciones cerradas, que sí traen
apertura, cierre y volumen. Nada de aquí lo usan los ámbitos UBS ni mensual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Sequence


# Umbral por defecto de solapamiento de exposición abierta entre dos grids.
# Por encima de él, dos estrategias tienden a estar bajo el agua los mismos
# días y su riesgo no se diversifica: se acumula.
DEFAULT_MAX_OPEN_OVERLAP = 0.60


@dataclass(frozen=True)
class GridExposure:
    """Exposición abierta y concurrencia de UNA unidad de estrategia grid."""

    set_id: str
    by_day: dict[str, float] = field(default_factory=dict)
    worst_day: str = ""
    worst_open_exposure: float = 0.0
    peak_legs: int = 0
    peak_lots: float = 0.0
    base_leg_lot: float = 0.0
    trades: int = 0

    @property
    def measured(self) -> bool:
        """Falso cuando el set no trae operaciones y no hay nada que medir."""
        return bool(self.by_day) or self.trades > 0

    @property
    def peak_exposure_ratio(self) -> float:
        """Cuántas posiciones base equivale el pico simultáneo de la escalera.

        Es el factor que le falta al margen: el modelo compartido cobra una
        posición mínima por unidad, y el grid llega a tener este múltiplo.
        """
        if self.base_leg_lot <= 0 or self.peak_lots <= 0:
            return 1.0
        return max(self.peak_lots / self.base_leg_lot, 1.0)


def strategy_closed_trades(strategy: Any) -> list[Any]:
    """Operaciones cerradas de una estrategia, tolerando fuentes incompletas."""
    trades = list(getattr(strategy, "closed_trades_2020_2026", None) or [])
    if trades:
        return trades
    for name in ("report_2020_2024", "report_2025_2026"):
        report = getattr(strategy, name, None)
        trades.extend(list(getattr(report, "closed_trades", None) or []))
    return trades


def strategy_grid_exposure(strategy: Any) -> GridExposure:
    """Mide exposición abierta diaria y concurrencia de una unidad.

    La exposición diaria usa el mismo proxy que el resto del proyecto: la
    pérdida final de cada operación perdedora pesa en cada día que estuvo
    abierta, porque el HTML de MT5 no publica una serie de equity con marca de
    tiempo. Las ganadoras no suman porque su excursión adversa no aparece en el
    informe, de modo que la medida se queda corta antes que pasarse.
    """
    set_id = str(getattr(strategy, "set_id", "") or "")
    trades = strategy_closed_trades(strategy)
    if not trades:
        return GridExposure(set_id=set_id)

    by_day: dict[str, float] = {}
    events: list[tuple[Any, int, float]] = []
    volumes: list[float] = []
    for trade in trades:
        close_time = getattr(trade, "close_time", None)
        if close_time is None:
            continue
        open_time = getattr(trade, "open_time", None) or close_time
        volume = abs(float(getattr(trade, "volume", 0.0) or 0.0))
        if volume > 0:
            volumes.append(volume)
            events.append((open_time, 1, volume))
            events.append((close_time, -1, -volume))
        risk = max(-float(getattr(trade, "net_profit", 0.0) or 0.0), 0.0)
        if risk <= 0:
            continue
        day = min(open_time.date(), close_time.date())
        last = max(open_time.date(), close_time.date())
        while day <= last:
            key = day.isoformat()
            by_day[key] = by_day.get(key, 0.0) + risk
            day += timedelta(days=1)

    peak_legs = 0
    peak_lots = 0.0
    open_legs = 0
    open_lots = 0.0
    events.sort(key=lambda item: (item[0], item[1]))
    for _stamp, delta, volume in events:
        open_legs += delta
        open_lots += volume
        peak_legs = max(peak_legs, open_legs)
        peak_lots = max(peak_lots, open_lots)

    worst_day, worst = max(by_day.items(), key=lambda item: item[1]) if by_day else ("", 0.0)
    return GridExposure(
        set_id=set_id,
        by_day=by_day,
        worst_day=worst_day,
        worst_open_exposure=float(worst),
        peak_legs=int(peak_legs),
        peak_lots=round(float(peak_lots), 4),
        base_leg_lot=round(min(volumes), 4) if volumes else 0.0,
        trades=len(trades),
    )


def declared_floating_dd(strategy: Any) -> float:
    """Excursión abierta declarada por unidad: DD de equity menos DD de balance."""
    return max(
        float(getattr(strategy, "max_equity_dd_001", 0.0) or 0.0)
        - float(getattr(strategy, "max_balance_dd_001", 0.0) or 0.0),
        0.0,
    )


def open_exposure_overlap(left: GridExposure, right: GridExposure) -> float:
    """Fracción de días bajo el agua que dos grids comparten (Jaccard).

    Es el `dd_overlap` que el filtro compartido no puede ver: aquél lo calcula
    sobre P/L cerrado diario, y en un grid ese P/L es positivo casi siempre.
    """
    days_left = {day for day, value in left.by_day.items() if value > 0}
    days_right = {day for day, value in right.by_day.items() if value > 0}
    union = days_left | days_right
    if not union:
        return 0.0
    return len(days_left & days_right) / len(union)


class GridExposureModel:
    """Serie diaria alineada de exposición abierta para un pool Grid.

    Alinear en el tiempo es lo único que distingue coincidencia de casualidad:
    el flotante de la cartera no es el de su peor estrategia (asume que las
    demás tienen cero abierto ese día) ni la suma de los peores de cada una
    (asume que todas tocan fondo el mismo día). Es lo que midan los días.
    """

    def __init__(self, strategies: Iterable[Any]) -> None:
        self.exposures: dict[str, GridExposure] = {}
        self.declared: dict[str, float] = {}
        for strategy in strategies:
            set_id = str(getattr(strategy, "set_id", "") or "")
            if not set_id or set_id in self.exposures:
                continue
            self.exposures[set_id] = strategy_grid_exposure(strategy)
            self.declared[set_id] = declared_floating_dd(strategy)
        days: set[str] = set()
        for exposure in self.exposures.values():
            days.update(day for day, value in exposure.by_day.items() if value > 0)
        self.days: list[str] = sorted(days)
        index = {day: position for position, day in enumerate(self.days)}
        self.vectors: dict[str, list[float]] = {}
        for set_id, exposure in self.exposures.items():
            vector = [0.0] * len(self.days)
            for day, value in exposure.by_day.items():
                if value > 0:
                    vector[index[day]] = float(value)
            self.vectors[set_id] = vector

    def declared_floating(self, allocations: dict[str, int]) -> float:
        """El `max()` entre estrategias que usaba Grid antes de medir los días."""
        return max((
            self.declared.get(set_id, 0.0) * max(int(units), 0)
            for set_id, units in allocations.items()
            if int(units) > 0
        ), default=0.0)

    def total_vector(self, allocations: dict[str, int]) -> list[float]:
        total = [0.0] * len(self.days)
        for set_id, units in allocations.items():
            count = max(int(units), 0)
            vector = self.vectors.get(set_id)
            if count <= 0 or not vector:
                continue
            for position, value in enumerate(vector):
                if value:
                    total[position] += value * count
        return total

    def worst_day_from_vector(self, total: Sequence[float]) -> tuple[str, float]:
        if not total:
            return "", 0.0
        position = max(range(len(total)), key=total.__getitem__)
        return self.days[position], float(total[position])

    def floating_from_vector(self, total: Sequence[float], allocations: dict[str, int]) -> float:
        """Flotante vinculante: nunca por debajo del máximo declarado.

        Si un set llega sin operaciones legibles su serie es cero, y la medida
        agregada sola lo dejaría fuera del riesgo. Tomar el máximo con el valor
        declarado conserva el suelo anterior y sólo sube cuando los días
        demuestran que varias estrategias coinciden bajo el agua.
        """
        _day, measured = self.worst_day_from_vector(total)
        return max(measured, self.declared_floating(allocations))

    def floating(self, allocations: dict[str, int]) -> float:
        return self.floating_from_vector(self.total_vector(allocations), allocations)

    def floating_without_one_unit(
        self, total: Sequence[float], allocations: dict[str, int], set_id: str
    ) -> float:
        """Flotante si a `set_id` se le quita una unidad, sin rehacer el vector."""
        vector = self.vectors.get(set_id)
        trial = {key: value for key, value in allocations.items() if int(value) > 0}
        if int(trial.get(set_id, 0)) <= 1:
            trial.pop(set_id, None)
        else:
            trial[set_id] = int(trial[set_id]) - 1
        if not vector or not total:
            return self.declared_floating(trial)
        measured = max(
            (value - vector[position] for position, value in enumerate(total)),
            default=0.0,
        )
        return max(float(measured), self.declared_floating(trial))

    def audit(self, allocations: dict[str, int]) -> dict[str, Any]:
        total = self.total_vector(allocations)
        worst_day, measured = self.worst_day_from_vector(total)
        declared = self.declared_floating(allocations)
        contributions = {
            set_id: round(self.vectors[set_id][self.days.index(worst_day)] * int(units), 2)
            for set_id, units in allocations.items()
            if worst_day and int(units) > 0 and self.vectors.get(set_id)
        } if worst_day else {}
        return {
            "worst_day": worst_day,
            "measured_open_exposure": round(measured, 2),
            "declared_floating_dd": round(declared, 2),
            "binding": round(max(measured, declared), 2),
            "coincident_sets": sum(1 for value in contributions.values() if value > 0),
            "contributions": {
                key: value for key, value in sorted(
                    contributions.items(), key=lambda item: item[1], reverse=True
                ) if value > 0
            },
            "measured_days": len(self.days),
        }


def peak_margin_summary(
    margin_summary: dict[str, Any],
    model: GridExposureModel,
    *,
    balance: float,
    max_margin_pct: float,
) -> dict[str, Any]:
    """Reescala el margen del optimizador por la escalera abierta de cada grid.

    El resumen compartido cobra una posición mínima por unidad. Un grid con la
    escalera desplegada tiene varias piernas abiertas a la vez, así que el
    margen real es el del pico simultáneo. Aquí sólo se multiplica el resultado
    ya calculado: la primitiva compartida no cambia.
    """
    by_set: dict[str, Any] = {}
    total = 0.0
    for set_id, entry in (margin_summary.get("by_set") or {}).items():
        exposure = model.exposures.get(set_id)
        ratio = exposure.peak_exposure_ratio if exposure else 1.0
        margin = float(entry.get("margin") or 0.0)
        units = int(entry.get("units") or 0)
        peak = margin * ratio
        total += peak
        by_set[set_id] = {
            "symbol": entry.get("symbol"),
            "units": units,
            "nominal_margin": round(margin, 2),
            "peak_margin": round(peak, 2),
            "peak_legs": exposure.peak_legs if exposure else 0,
            "peak_lots": round((exposure.peak_lots if exposure else 0.0) * units, 4),
            "base_leg_lot": exposure.base_leg_lot if exposure else 0.0,
            "peak_exposure_ratio": round(ratio, 2),
        }
    limit = float(balance) * float(max_margin_pct) / 100.0 if balance > 0 else 0.0
    return {
        "total": round(total, 2),
        "limit": round(limit, 2),
        "usage_pct": round(total / limit * 100.0, 2) if limit > 0 else 0.0,
        "free_margin_pct": round(max(balance - total, 0.0) / balance * 100.0, 2) if balance > 0 else 0.0,
        "exceeds_limit": bool(limit > 0 and total > limit),
        "by_set": by_set,
    }


def portfolio_peak_lots(model: GridExposureModel, allocations: dict[str, int]) -> float:
    """Lotes abiertos a la vez en el peor caso, sumando toda la cartera."""
    return round(sum(
        (model.exposures[set_id].peak_lots if set_id in model.exposures else 0.0) * max(int(units), 0)
        for set_id, units in allocations.items()
    ), 2)


def open_equity_curve(
    strategies: Sequence[Any],
    allocations: dict[str, int],
    model: GridExposureModel,
    daily_pnl: Any,
) -> list[float]:
    """Curva diaria de equity: cerrado acumulado menos exposición abierta.

    El bootstrap del proyecto trabaja sobre la curva de operaciones cerradas.
    En un grid esa curva es la parte amable del riesgo; restarle la exposición
    abierta de cada día devuelve algo parecido a la equity que vería la cuenta,
    que es sobre lo que tiene sentido estresar.
    """
    closed_by_day: dict[str, float] = {}
    for strategy in strategies:
        units = max(int(allocations.get(str(getattr(strategy, "set_id", "")), 0)), 0)
        if units <= 0:
            continue
        for day, value in (daily_pnl(strategy) or {}).items():
            closed_by_day[day] = closed_by_day.get(day, 0.0) + float(value) * units
    open_by_day: dict[str, float] = {}
    for set_id, units in allocations.items():
        count = max(int(units), 0)
        exposure = model.exposures.get(set_id)
        if count <= 0 or exposure is None:
            continue
        for day, value in exposure.by_day.items():
            open_by_day[day] = open_by_day.get(day, 0.0) + float(value) * count
    days = sorted(set(closed_by_day) | set(open_by_day))
    curve = [0.0]
    cumulative = 0.0
    for day in days:
        cumulative += closed_by_day.get(day, 0.0)
        curve.append(cumulative - open_by_day.get(day, 0.0))
    return curve


def prune_overlapping_sets(
    strategies: Sequence[Any],
    *,
    max_open_overlap: float = DEFAULT_MAX_OPEN_OVERLAP,
    model: GridExposureModel | None = None,
) -> tuple[list[Any], list[str]]:
    """Deja fuera del pool los grids que comparten sus días bajo el agua.

    Se conserva el más eficiente de cada grupo solapado (beneficio por unidad de
    excursión abierta). No sustituye a los filtros de correlación compartidos:
    los complementa midiendo lo que aquéllos no pueden ver en un grid.
    """
    if max_open_overlap is None or max_open_overlap >= 1.0:
        return list(strategies), []
    exposure_model = model or GridExposureModel(strategies)

    def efficiency(strategy: Any) -> float:
        exposure = exposure_model.exposures.get(str(getattr(strategy, "set_id", "")))
        risk = max(
            declared_floating_dd(strategy),
            exposure.worst_open_exposure if exposure else 0.0,
            1e-9,
        )
        return float(getattr(strategy, "net_profit_2020_2026_001", 0.0) or 0.0) / risk

    ordered = sorted(strategies, key=efficiency, reverse=True)
    kept: list[Any] = []
    warnings: list[str] = []
    for strategy in ordered:
        set_id = str(getattr(strategy, "set_id", "") or "")
        exposure = exposure_model.exposures.get(set_id)
        if exposure is None or not exposure.by_day:
            kept.append(strategy)
            continue
        clash = None
        for chosen in kept:
            other = exposure_model.exposures.get(str(getattr(chosen, "set_id", "") or ""))
            if other is None or not other.by_day:
                continue
            if open_exposure_overlap(exposure, other) > max_open_overlap:
                clash = (chosen, open_exposure_overlap(exposure, other))
                break
        if clash is None:
            kept.append(strategy)
        else:
            chosen, overlap = clash
            warnings.append(
                f"Grid: {_short(set_id)} descartada por solapar el "
                f"{overlap * 100:.0f}% de sus días con exposición abierta con "
                f"{_short(str(getattr(chosen, 'set_id', '')))}."
            )
    order = {str(getattr(strategy, "set_id", "")): position for position, strategy in enumerate(strategies)}
    kept.sort(key=lambda strategy: order.get(str(getattr(strategy, "set_id", "")), 0))
    return kept, warnings


def _short(set_id: str) -> str:
    return set_id.replace("\\", "/").rsplit("/", 1)[-1]
