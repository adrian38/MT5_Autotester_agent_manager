from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Sequence

from portfolio_manager.ubs_portfolio import (
    MarginModel,
    RobustStrategySet,
    allocation_margin_required,
    daily_pnl_series,
    portfolio_symbol_key,
)


#: Ventana de historia que se toma como «el año» de la simulación.
DEFAULT_WINDOW_MONTHS = 12
#: Tope de estrategias que entran en la búsqueda. Más allá de esto el coste
#: crece sin mejorar el resultado: el pool cruzado ya está ordenado por
#: retorno sobre su propio valle.
DEFAULT_POOL_LIMIT = 60
#: Pasos de la fase golosa. Cada paso es una unidad más en una estrategia.
DEFAULT_GREEDY_STEPS = 240
MIN_OBSERVATIONS = 20

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class LabStrategy:
    """Una estrategia del pool cruzado, ya reducida a lo que la simulación usa."""

    key: str
    origin: str
    origin_node: str
    set_id: str
    symbol: str
    symbol_key: str
    timeframe: str
    set_path: str
    #: PnL por unidad y día de la ventana, alineado al eje común de días.
    series: tuple[float, ...]
    net: float
    valley_dd: float
    #: Días de la ventana con operaciones. No son trades: la curva de MT5 se
    #: agrega por día, así que esto mide presencia, no actividad.
    active_days: int
    margin_per_unit: float
    #: El símbolo existe medido en el broker de destino.
    portable: bool
    portability: str

    @property
    def score(self) -> float:
        """Retorno de la ventana sobre el valle que ese retorno costó."""
        if self.valley_dd <= 0:
            return self.net if self.net > 0 else 0.0
        return self.net / self.valley_dd


@dataclass(frozen=True)
class LabAxis:
    """Eje temporal común: los días con datos y a qué mes pertenece cada uno."""

    days: tuple[str, ...]
    month_index: tuple[int, ...]
    months: int

    @property
    def size(self) -> int:
        return len(self.days)


@dataclass(frozen=True)
class LabConfig:
    capital: float
    target_equity: float
    max_dd_pct: float
    max_margin_pct: float
    rebalance_months: int
    max_units_per_strategy: int
    max_units_per_symbol: int
    max_units_total: int
    max_pair_corr: float
    pool_limit: int = DEFAULT_POOL_LIMIT
    greedy_steps: int = DEFAULT_GREEDY_STEPS
    #: Multiplicador de lotes forzado, para responder «¿y si escalase esto?».
    forced_scale: float = 0.0


@dataclass
class Simulation:
    days: int
    final_equity: float
    profit: float
    return_pct: float
    max_dd_pct: float
    max_dd_amount: float
    max_margin_pct: float
    final_scale: float
    ruined: bool
    equity_curve: list[float] = field(default_factory=list)
    rebalances: list[dict[str, Any]] = field(default_factory=list)
    monthly_profit: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Verdict:
    reached: bool
    target_equity: float
    final_equity: float
    gap: float
    annual_return_pct: float
    #: Capital de partida que, con este mismo retorno, termina en el objetivo.
    capital_for_target: float
    #: Cuántas veces más lotes haría falta desde el capital pedido.
    scale_for_target: float
    #: Drawdown que costaría ese multiplicador, simulado de verdad.
    dd_at_target_pct: float
    dd_at_target_ruined: bool
    #: Margen que ese multiplicador exigiría, en % del capital pedido.
    margin_at_target_pct: float
    note: str


def month_key(day: str) -> str:
    return day[:7]


def build_axis(series_by_key: dict[str, dict[str, float]], *, window_months: int) -> LabAxis:
    """Eje de días de la ventana, tomado de la historia real del pool.

    El final de la ventana es el último día con operaciones de cualquier
    estrategia, no la fecha de hoy: si la última generación acabó en marzo, el
    «año» que se simula termina en marzo. Contar hasta hoy metería meses
    vacíos y el retorno anual saldría diluido sin que nada lo avise.
    """
    all_days = sorted({day for series in series_by_key.values() for day in series})
    if not all_days:
        return LabAxis(days=(), month_index=(), months=0)
    last = date.fromisoformat(all_days[-1])
    months = max(int(window_months), 1)
    # Primer día incluido: el mismo día de mes, `months` meses antes.
    start_month = last.month - months
    start_year = last.year
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    try:
        first = date(start_year, start_month, last.day)
    except ValueError:
        first = date(start_year, start_month, 28)
    days = tuple(day for day in all_days if date.fromisoformat(day) > first)
    keys: list[str] = []
    index: list[int] = []
    for day in days:
        key = month_key(day)
        if not keys or keys[-1] != key:
            keys.append(key)
        index.append(len(keys) - 1)
    return LabAxis(days=days, month_index=tuple(index), months=len(keys))


def valley_dd(values: Sequence[float]) -> float:
    """Peor caída acumulada de una serie de incrementos, en divisa."""
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


def _dated_strategy_series(
    strategies: Sequence[tuple[RobustStrategySet, str, str]],
) -> tuple[
    dict[str, dict[str, float]],
    dict[str, tuple[RobustStrategySet, str, str]],
    list[str],
]:
    series_by_key: dict[str, dict[str, float]] = {}
    meta: dict[str, tuple[RobustStrategySet, str, str]] = {}
    warnings: list[str] = []
    for strategy, origin, node_id in strategies:
        key = f"{origin}:{strategy.set_id}"
        series = daily_pnl_series(strategy)
        if not series or not all(_looks_like_day(day) for day in series):
            # Sin eje de fechas la serie es un índice sintético y no se puede
            # cruzar con las demás: la mezcla de brokers solo tiene sentido en
            # el calendario.
            warnings.append(f"{origin} · {strategy.symbol}: sin fechas en la curva, fuera del pool")
            continue
        series_by_key[key] = series
        meta[key] = (strategy, origin, node_id)
    return series_by_key, meta, warnings


def _windowed_strategy(
    key: str,
    series: dict[str, float],
    meta: tuple[RobustStrategySet, str, str],
    axis: LabAxis,
    day_positions: dict[str, int],
    margin_model: MarginModel,
    portable_symbols: frozenset[str],
) -> LabStrategy | None:
    values = [0.0] * axis.size
    observations = 0
    for day, value in series.items():
        position = day_positions.get(day)
        if position is not None:
            values[position] += value
            observations += 1
    if observations < 1:
        return None
    strategy, origin, node_id = meta
    symbol_key = portfolio_symbol_key(strategy.symbol)
    portable = symbol_key in portable_symbols if portable_symbols else True
    return LabStrategy(
        key=key, origin=origin, origin_node=node_id, set_id=strategy.set_id,
        symbol=strategy.symbol, symbol_key=symbol_key,
        timeframe=str(strategy.timeframe or ""), set_path=strategy.set_path,
        series=tuple(values), net=sum(values), valley_dd=valley_dd(values),
        active_days=observations,
        margin_per_unit=allocation_margin_required(strategy, 1, margin_profile=margin_model),
        portable=portable,
        portability=(
            "medido en el broker de destino" if portable
            else "el broker de destino no tiene medido este símbolo"
        ),
    )


def build_lab_strategies(
    strategies: Sequence[tuple[RobustStrategySet, str, str]],
    *,
    margin_model: MarginModel,
    portable_symbols: frozenset[str],
    window_months: int = DEFAULT_WINDOW_MONTHS,
    progress: ProgressCallback | None = None,
) -> tuple[list[LabStrategy], LabAxis, list[str]]:
    """Convierte estrategias cargadas en pool cruzado sobre un eje común."""
    series_by_key, meta, warnings = _dated_strategy_series(strategies)
    axis = build_axis(series_by_key, window_months=window_months)
    if not axis.size:
        return [], axis, warnings
    pool: list[LabStrategy] = []
    day_positions = {day: position for position, day in enumerate(axis.days)}
    for index, (key, series) in enumerate(sorted(series_by_key.items()), start=1):
        if progress and index % 50 == 0:
            progress(f"Recortando series a la ventana: {index}/{len(series_by_key)}")
        candidate = _windowed_strategy(
            key, series, meta[key], axis, day_positions, margin_model, portable_symbols,
        )
        if candidate is not None:
            pool.append(candidate)
    return pool, axis, warnings


def _looks_like_day(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


