"""Laboratorio «Experimenta»: un millón en un año con los tres brokers.

Qué prueba
----------
La idea de fondo es que las tres demos (ICTrading, AXI y RoboForex) sigan
generando y validando estrategias cada una en su equipo, y que **una sola
cuenta real** ejecute la mezcla de las tres. Este módulo responde la única
pregunta que decide si eso sirve para algo: con el pool cruzado de hoy, ¿a
cuánto llega una cuenta en doce meses, con qué drawdown y cuánto capital haría
falta para terminar en el objetivo?

Qué NO es
---------
No es una variante del cálculo UBS ni del mensual, y no comparte nada con
ellos salvo primitivas de lectura. No escribe en la memoria de ningún agente:
todo vive en memoria del manager y muere con la pantalla. Si algún día una
composición de aquí tiene que guardarse, se guarda por los verbos que ya
existen, no ampliando este módulo.

Por qué la recomposición de lotes no es un truco
------------------------------------------------
Los EAs de este proyecto dimensionan por balance (``LotPerBalance_step``, ver
``MarginModel.lot_increments_for``), así que un lote que crece con la equity es
el comportamiento **nativo** del robot, no una capa nueva. La simulación lo
reproduce recalculando el multiplicador de lotes cada N meses; con
``rebalance_months=0`` se queda a lote fijo y se ve el mismo pool sin
capitalizar.

Convenio de unidades
--------------------
Una **unidad** es una posición al lote mínimo del símbolo, exactamente igual
que en UBS: la curva ``_001`` de los reportes ya está medida a ese lote
mínimo (ver ``load_symbol_specs``). El margen de una unidad sale de
``allocation_margin_required``, que usa el margen medido del terminal cuando
existe.

Drawdown relativo, no absoluto
------------------------------
UBS limita el valle en divisa contra un presupuesto fijo de capital. Aquí el
capital crece, así que el límite se mide como caída porcentual desde el máximo
de equity: es lo que ve una cuenta real y lo único comparable entre un mes con
10.000 y otro con 400.000.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable, Sequence

from portfolio_manager.ubs_portfolio import (
    MarginModel,
    RobustStrategySet,
    allocation_margin_required,
    daily_pnl_series,
    pearson_correlation,
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


def build_lab_strategies(
    strategies: Sequence[tuple[RobustStrategySet, str, str]],
    *,
    margin_model: MarginModel,
    portable_symbols: frozenset[str],
    window_months: int = DEFAULT_WINDOW_MONTHS,
    progress: ProgressCallback | None = None,
) -> tuple[list[LabStrategy], LabAxis, list[str]]:
    """Convierte estrategias cargadas en pool cruzado sobre un eje común.

    ``strategies`` llega como ``(estrategia, origen, nodo)``: el origen es el
    broker donde se generó y validó, y es lo único que distingue dos copias del
    mismo símbolo en dos memorias distintas.
    """
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
    axis = build_axis(series_by_key, window_months=window_months)
    if not axis.size:
        return [], axis, warnings

    pool: list[LabStrategy] = []
    day_positions = {day: position for position, day in enumerate(axis.days)}
    for index, (key, series) in enumerate(sorted(series_by_key.items()), start=1):
        if progress and index % 50 == 0:
            progress(f"Recortando series a la ventana: {index}/{len(series_by_key)}")
        values = [0.0] * axis.size
        observations = 0
        for day, value in series.items():
            position = day_positions.get(day)
            if position is None:
                continue
            values[position] += value
            observations += 1
        if observations < 1:
            continue
        strategy, origin, node_id = meta[key]
        symbol_key = portfolio_symbol_key(strategy.symbol)
        portable = symbol_key in portable_symbols if portable_symbols else True
        pool.append(LabStrategy(
            key=key,
            origin=origin,
            origin_node=node_id,
            set_id=strategy.set_id,
            symbol=strategy.symbol,
            symbol_key=symbol_key,
            timeframe=str(strategy.timeframe or ""),
            set_path=strategy.set_path,
            series=tuple(values),
            net=sum(values),
            valley_dd=valley_dd(values),
            active_days=observations,
            margin_per_unit=allocation_margin_required(
                strategy, 1, margin_profile=margin_model,
            ),
            portable=portable,
            portability=(
                "medido en el broker de destino" if portable
                else "el broker de destino no tiene medido este símbolo"
            ),
        ))
    return pool, axis, warnings


def _looks_like_day(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def simulate(
    daily: Sequence[float],
    axis: LabAxis,
    *,
    margin_required: float,
    config: LabConfig,
    keep_curve: bool = False,
) -> Simulation:
    """Doce meses de la cartera con recomposición de lotes y DD relativo.

    ``daily`` es el PnL diario de la cartera con los lotes base (multiplicador
    1). El multiplicador se recalcula al entrar en un mes nuevo cuando toca, y
    se recorta si el margen agregado se saldría del tope: una cuenta no puede
    abrir lo que no tiene margen para abrir, y el resultado tiene que decirlo
    en vez de suponer crédito infinito.
    """
    capital = max(float(config.capital), 0.0)
    if capital <= 0 or not axis.size:
        return Simulation(
            days=0, final_equity=capital, profit=0.0, return_pct=0.0,
            max_dd_pct=0.0, max_dd_amount=0.0, max_margin_pct=0.0,
            final_scale=0.0, ruined=capital <= 0,
        )
    forced = float(config.forced_scale or 0.0)
    rebalance = max(int(config.rebalance_months), 0)
    margin_cap_pct = max(float(config.max_margin_pct), 0.0)

    def scale_for(equity: float) -> float:
        # `forced_scale` multiplica los lotes; la proporción con el balance es
        # aparte y solo existe si hay recomposición.
        base = (equity / capital) if rebalance else 1.0
        if forced > 0:
            base *= forced
        if margin_required > 0 and margin_cap_pct > 0:
            allowed = equity * margin_cap_pct / 100.0 / margin_required
            base = min(base, max(allowed, 0.0))
        return max(base, 0.0)

    equity = capital
    peak = capital
    scale = scale_for(capital)
    worst_dd_pct = 0.0
    worst_dd_amount = 0.0
    worst_margin_pct = margin_required * scale / capital * 100.0 if capital else 0.0
    curve: list[float] = [capital] if keep_curve else []
    rebalances: list[dict[str, Any]] = [{
        "month": axis.days[0][:7], "scale": round(scale, 4), "equity": round(equity, 2),
    }]
    monthly: list[dict[str, Any]] = []
    month_start_equity = capital
    current_month = axis.month_index[0]
    ruined = False

    for position, value in enumerate(daily):
        month = axis.month_index[position]
        if month != current_month:
            monthly.append({
                "month": axis.days[position - 1][:7],
                "profit": round(equity - month_start_equity, 2),
                "equity": round(equity, 2),
                "scale": round(scale, 4),
            })
            month_start_equity = equity
            current_month = month
            if rebalance and month % rebalance == 0:
                scale = scale_for(equity)
                rebalances.append({
                    "month": axis.days[position][:7],
                    "scale": round(scale, 4),
                    "equity": round(equity, 2),
                })
        equity += value * scale
        if margin_required > 0 and equity > 0:
            worst_margin_pct = max(worst_margin_pct, margin_required * scale / equity * 100.0)
        if equity <= 0:
            ruined = True
            equity = 0.0
            if keep_curve:
                curve.append(0.0)
            worst_dd_pct = 100.0
            worst_dd_amount = max(worst_dd_amount, peak)
            break
        peak = max(peak, equity)
        drop = peak - equity
        if drop > 0:
            worst_dd_amount = max(worst_dd_amount, drop)
            worst_dd_pct = max(worst_dd_pct, drop / peak * 100.0)
        if keep_curve:
            curve.append(equity)
    else:
        monthly.append({
            "month": axis.days[-1][:7],
            "profit": round(equity - month_start_equity, 2),
            "equity": round(equity, 2),
            "scale": round(scale, 4),
        })

    return Simulation(
        days=axis.size,
        final_equity=equity,
        profit=equity - capital,
        return_pct=(equity - capital) / capital * 100.0,
        max_dd_pct=worst_dd_pct,
        max_dd_amount=worst_dd_amount,
        max_margin_pct=worst_margin_pct,
        final_scale=scale,
        ruined=ruined,
        equity_curve=_sample_curve(curve) if keep_curve else [],
        rebalances=rebalances,
        monthly_profit=monthly,
    )


def _sample_curve(curve: list[float], *, points: int = 260) -> list[float]:
    if len(curve) <= points:
        return [round(value, 2) for value in curve]
    step = len(curve) / points
    sampled = [curve[min(int(index * step), len(curve) - 1)] for index in range(points)]
    sampled[-1] = curve[-1]
    return [round(value, 2) for value in sampled]


def select_candidates(
    pool: Sequence[LabStrategy],
    config: LabConfig,
    *,
    require_portable: bool,
) -> tuple[list[LabStrategy], list[str]]:
    """Subconjunto diversificado: neto positivo, tope por símbolo y correlación.

    El orden es retorno sobre valle propio, no retorno absoluto: una estrategia
    que gana el doble pagando cuatro veces más drawdown empeora la cartera
    aunque su neto sea mayor.
    """
    notes: list[str] = []
    ranked = sorted(
        (item for item in pool if item.net > 0 and (item.portable or not require_portable)),
        key=lambda item: item.score,
        reverse=True,
    )
    discarded_negative = sum(1 for item in pool if item.net <= 0)
    if discarded_negative:
        notes.append(f"{discarded_negative} estrategias descartadas por neto negativo en la ventana")
    if require_portable:
        blocked = sum(1 for item in pool if item.net > 0 and not item.portable)
        if blocked:
            notes.append(
                f"{blocked} estrategias descartadas porque el broker de destino no tiene su símbolo medido"
            )
    selected: list[LabStrategy] = []
    per_symbol: dict[str, int] = {}
    rejected_corr = 0
    limit = max(int(config.pool_limit), 1)
    slot_limit = symbol_slot_limit(config)
    max_corr = float(config.max_pair_corr)
    # Cada candidata examinada se compara con todas las ya elegidas, así que un
    # pool de miles con muchas copias correlacionadas convierte esta criba en el
    # tramo más caro del cálculo. Se examinan las mejores por retorno/valle, que
    # son las que pueden entrar, y el corte se dice en voz alta.
    scan_limit = max(limit * 20, 200)
    for candidate in ranked[:scan_limit]:
        if len(selected) >= limit:
            break
        symbol_slots = per_symbol.get(candidate.symbol_key, 0)
        if symbol_slots >= slot_limit:
            continue
        if max_corr < 1.0 and _too_correlated(candidate, selected, max_corr):
            rejected_corr += 1
            continue
        selected.append(candidate)
        per_symbol[candidate.symbol_key] = symbol_slots + 1
    if rejected_corr:
        notes.append(f"{rejected_corr} estrategias descartadas por correlación > {max_corr:.2f}")
    if len(ranked) > scan_limit and len(selected) >= limit:
        notes.append(
            f"la criba examinó las {scan_limit} mejores de {len(ranked)} con neto positivo, "
            "ordenadas por retorno sobre su propio valle"
        )
    return selected, notes


def symbol_slot_limit(config: LabConfig) -> int:
    """Cuántas estrategias del mismo símbolo pueden coexistir en el pool.

    El tope de la pantalla se expresa en unidades por símbolo, que es lo que
    importa en la cuenta. Aquí se traduce a plazas: con 20 unidades por símbolo
    y 5 por estrategia caben cuatro estrategias del mismo símbolo, no veinte.
    """
    return max(
        int(config.max_units_per_symbol) // max(int(config.max_units_per_strategy), 1), 1,
    )


def _too_correlated(
    candidate: LabStrategy, selected: Sequence[LabStrategy], max_corr: float,
) -> bool:
    if len(candidate.series) < MIN_OBSERVATIONS:
        return False
    return any(
        abs(pearson_correlation(candidate.series, other.series)) > max_corr
        for other in selected
    )


@dataclass
class Allocation:
    units: dict[str, int]
    simulation: Simulation
    margin_required: float
    steps: int
    stop_reason: str

    @property
    def total_units(self) -> int:
        return sum(self.units.values())


def search_allocation(
    candidates: Sequence[LabStrategy],
    axis: LabAxis,
    config: LabConfig,
    *,
    progress: ProgressCallback | None = None,
) -> Allocation:
    """Reparto de unidades que más se acerca al objetivo sin pasarse del DD.

    Dos fases. La primera busca por bisección un número de unidades igual para
    todas: da una base equilibrada en un puñado de simulaciones. La segunda
    añade unidades de una en una a la estrategia que más aporta, que es donde
    se recupera la asimetría que la base uniforme aplana.
    """
    if not candidates or not axis.size:
        return Allocation({}, simulate([], axis, margin_required=0.0, config=config), 0.0, 0, "pool vacío")

    size = axis.size
    series = [candidate.series for candidate in candidates]
    margins = [candidate.margin_per_unit for candidate in candidates]

    def combine(units: Sequence[int]) -> tuple[list[float], float]:
        daily = [0.0] * size
        margin = 0.0
        for index, count in enumerate(units):
            if count <= 0:
                continue
            values = series[index]
            for position in range(size):
                daily[position] += values[position] * count
            margin += margins[index] * count
        return daily, margin

    def acceptable(simulation: Simulation, margin: float) -> bool:
        if simulation.ruined:
            return False
        if simulation.max_dd_pct > config.max_dd_pct:
            return False
        if config.max_margin_pct > 0 and margin > 0:
            if margin / max(config.capital, 1e-9) * 100.0 > config.max_margin_pct:
                return False
        return True

    per_strategy_cap = max(int(config.max_units_per_strategy), 1)
    total_cap = max(int(config.max_units_total), 1)
    uniform_cap = min(per_strategy_cap, max(total_cap // len(candidates), 0))

    best_uniform = 0
    low, high = 0, uniform_cap
    if progress:
        progress(f"Base uniforme: bisección hasta {uniform_cap} unidades por estrategia")
    while low <= high and high > 0:
        middle = (low + high) // 2
        if middle <= 0:
            break
        daily, margin = combine([middle] * len(candidates))
        simulation = simulate(daily, axis, margin_required=margin, config=config)
        if acceptable(simulation, margin):
            best_uniform = middle
            low = middle + 1
        else:
            high = middle - 1

    units = [best_uniform] * len(candidates)
    daily, margin = combine(units)
    simulation = simulate(daily, axis, margin_required=margin, config=config)
    if not acceptable(simulation, margin):
        units = [0] * len(candidates)
        daily, margin = combine(units)
        simulation = simulate(daily, axis, margin_required=margin, config=config)

    stop_reason = "sin margen para más unidades"
    steps = 0
    symbol_units: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        symbol_units[candidate.symbol_key] = symbol_units.get(candidate.symbol_key, 0) + units[index]

    for step in range(max(int(config.greedy_steps), 0)):
        if simulation.final_equity >= config.target_equity:
            stop_reason = "objetivo alcanzado"
            break
        if sum(units) >= total_cap:
            stop_reason = "tope de unidades totales"
            break
        if progress and step % 20 == 0:
            progress(
                f"Ajuste fino {step}/{config.greedy_steps} · "
                f"{sum(units)} unidades · {simulation.final_equity:,.0f}"
            )
        best_index = -1
        best_profit = simulation.final_equity
        best_state: tuple[list[float], float, Simulation] | None = None
        for index, candidate in enumerate(candidates):
            if units[index] >= per_strategy_cap:
                continue
            if symbol_units.get(candidate.symbol_key, 0) >= config.max_units_per_symbol:
                continue
            trial_daily = [
                daily[position] + candidate.series[position] for position in range(size)
            ]
            trial_margin = margin + candidate.margin_per_unit
            trial = simulate(trial_daily, axis, margin_required=trial_margin, config=config)
            if not acceptable(trial, trial_margin):
                continue
            if trial.final_equity > best_profit:
                best_profit = trial.final_equity
                best_index = index
                best_state = (trial_daily, trial_margin, trial)
        if best_index < 0 or best_state is None:
            break
        daily, margin, simulation = best_state
        units[best_index] += 1
        symbol = candidates[best_index].symbol_key
        symbol_units[symbol] = symbol_units.get(symbol, 0) + 1
        steps += 1
    else:
        if simulation.final_equity < config.target_equity:
            stop_reason = "agotados los pasos de ajuste"

    final_units = {
        candidates[index].key: units[index]
        for index in range(len(candidates)) if units[index] > 0
    }
    daily, margin = combine(units)
    simulation = simulate(daily, axis, margin_required=margin, config=config, keep_curve=True)
    return Allocation(final_units, simulation, margin, steps, stop_reason)


def build_verdict(
    allocation: Allocation,
    candidates: Sequence[LabStrategy],
    axis: LabAxis,
    config: LabConfig,
) -> Verdict:
    """El número honesto: con este retorno, qué capital termina en el objetivo.

    El retorno anual de una cartera con lote proporcional al balance no cambia
    al cambiar el capital: lo que cambia es el punto de llegada. Así que si el
    objetivo no se alcanza, la respuesta útil no es «imposible» sino el capital
    de partida que sí llega, y el multiplicador de lotes que haría falta para
    llegar desde el capital pedido, con el drawdown que ese multiplicador
    cuesta de verdad —simulado, no extrapolado—.
    """
    simulation = allocation.simulation
    target = float(config.target_equity)
    reached = simulation.final_equity >= target and not simulation.ruined
    growth = simulation.final_equity / config.capital if config.capital > 0 else 0.0
    capital_for_target = target / growth if growth > 0 else 0.0
    scale_for_target = 0.0
    dd_at_target = 0.0
    dd_ruined = False
    margin_at_target = 0.0
    note = ""
    if reached:
        note = "El pool cruzado llega al objetivo dentro del límite de drawdown."
    elif simulation.ruined:
        note = "La cuenta se queda a cero durante la ventana: la composición no es operable."
    elif not allocation.units:
        # No es lo mismo que un pool que pierde dinero, y confundirlos manda a
        # buscar mejores estrategias cuando lo que sobra es límite.
        note = (
            "La búsqueda no pudo asignar ni una unidad sin pasarse del límite de "
            f"drawdown ({config.max_dd_pct:,.0f}%) o de margen "
            f"({config.max_margin_pct:,.0f}%). Sube los límites o baja el capital exigido."
        )
    elif simulation.profit <= 0:
        note = "El pool no gana dinero en la ventana; el objetivo no depende del capital."
    else:
        needed_profit = target - config.capital
        scale_for_target = needed_profit / simulation.profit
        # El tope de margen se levanta en esta simulación a propósito: la
        # pregunta es qué riesgo tendría el multiplicador, no si el broker lo
        # dejaría abrir. Lo segundo se responde aparte, con `margin_at_target`,
        # y las dos cosas juntas son la respuesta completa. Dejar el tope
        # puesto recortaba la escala y devolvía un drawdown menor que el real,
        # que es exactamente el número que no se puede dar.
        forced = replace(config, forced_scale=scale_for_target, max_margin_pct=0.0)
        daily = [0.0] * axis.size
        margin = 0.0
        by_key = {candidate.key: candidate for candidate in candidates}
        for key, units in allocation.units.items():
            candidate = by_key.get(key)
            if candidate is None:
                continue
            for position in range(axis.size):
                daily[position] += candidate.series[position] * units
            margin += candidate.margin_per_unit * units
        scaled = simulate(daily, axis, margin_required=margin, config=forced)
        dd_at_target = scaled.max_dd_pct
        dd_ruined = scaled.ruined
        margin_at_target = (
            margin * scale_for_target / config.capital * 100.0 if config.capital > 0 else 0.0
        )
        note = (
            f"Con este retorno el objetivo sale de un capital inicial de "
            f"{capital_for_target:,.0f}. Desde {config.capital:,.0f} haría falta "
            f"multiplicar los lotes por {scale_for_target:,.1f}: eso lleva el "
            f"drawdown al {dd_at_target:,.1f}% y el margen al "
            f"{margin_at_target:,.0f}% del capital (límite {config.max_margin_pct:,.0f}%)."
            + (" A esa escala la cuenta se queda a cero durante la ventana." if dd_ruined else "")
        )
    return Verdict(
        reached=reached,
        target_equity=target,
        final_equity=simulation.final_equity,
        gap=target - simulation.final_equity,
        annual_return_pct=simulation.return_pct,
        capital_for_target=capital_for_target,
        scale_for_target=scale_for_target,
        dd_at_target_pct=dd_at_target,
        dd_at_target_ruined=dd_ruined,
        margin_at_target_pct=margin_at_target,
        note=note,
    )


def pool_summary(pool: Sequence[LabStrategy]) -> dict[str, Any]:
    by_origin: dict[str, dict[str, Any]] = {}
    for item in pool:
        entry = by_origin.setdefault(item.origin, {
            "strategies": 0, "positive": 0, "portable": 0, "symbols": set(),
        })
        entry["strategies"] += 1
        entry["positive"] += 1 if item.net > 0 else 0
        entry["portable"] += 1 if item.portable else 0
        entry["symbols"].add(item.symbol_key)
    return {
        origin: {
            "strategies": entry["strategies"],
            "positive": entry["positive"],
            "portable": entry["portable"],
            "symbols": len(entry["symbols"]),
        }
        for origin, entry in sorted(by_origin.items())
    }


def allocation_payload(
    allocation: Allocation,
    candidates: Sequence[LabStrategy],
    verdict: Verdict,
    axis: LabAxis,
    config: LabConfig,
) -> dict[str, Any]:
    by_key = {candidate.key: candidate for candidate in candidates}
    members = []
    for key, units in sorted(
        allocation.units.items(),
        key=lambda item: by_key[item[0]].net * item[1] if item[0] in by_key else 0.0,
        reverse=True,
    ):
        candidate = by_key.get(key)
        if candidate is None:
            continue
        members.append({
            "key": key,
            "origin": candidate.origin,
            "origin_node": candidate.origin_node,
            "symbol": candidate.symbol,
            "timeframe": candidate.timeframe,
            "units": units,
            "net_contribution": round(candidate.net * units, 2),
            "unit_net": round(candidate.net, 2),
            "unit_valley_dd": round(candidate.valley_dd, 2),
            "margin": round(candidate.margin_per_unit * units, 2),
            "portable": candidate.portable,
            "portability": candidate.portability,
            "set_path": candidate.set_path,
        })
    simulation = allocation.simulation
    return {
        "window": {
            "days": axis.size,
            "months": axis.months,
            "from": axis.days[0] if axis.days else "",
            "to": axis.days[-1] if axis.days else "",
        },
        "simulation": {
            "final_equity": round(simulation.final_equity, 2),
            "profit": round(simulation.profit, 2),
            "return_pct": round(simulation.return_pct, 2),
            "max_dd_pct": round(simulation.max_dd_pct, 2),
            "max_dd_amount": round(simulation.max_dd_amount, 2),
            "max_margin_pct": round(simulation.max_margin_pct, 2),
            "final_scale": round(simulation.final_scale, 3),
            "ruined": simulation.ruined,
            "equity_curve": simulation.equity_curve,
            "rebalances": simulation.rebalances,
            "monthly_profit": simulation.monthly_profit,
        },
        "allocation": {
            "strategies": len(members),
            "total_units": allocation.total_units,
            "margin_required": round(allocation.margin_required, 2),
            "margin_pct": round(
                allocation.margin_required / config.capital * 100.0 if config.capital else 0.0, 2,
            ),
            "steps": allocation.steps,
            "stop_reason": allocation.stop_reason,
            "members": members,
        },
        "verdict": {
            "reached": verdict.reached,
            "target_equity": round(verdict.target_equity, 2),
            "final_equity": round(verdict.final_equity, 2),
            "gap": round(verdict.gap, 2),
            "annual_return_pct": round(verdict.annual_return_pct, 2),
            "capital_for_target": round(verdict.capital_for_target, 2),
            "scale_for_target": round(verdict.scale_for_target, 3),
            "dd_at_target_pct": round(verdict.dd_at_target_pct, 2),
            "dd_at_target_ruined": verdict.dd_at_target_ruined,
            "margin_at_target_pct": round(verdict.margin_at_target_pct, 2),
            "note": verdict.note,
        },
    }
