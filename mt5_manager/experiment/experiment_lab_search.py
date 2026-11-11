from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from portfolio_manager.ubs_portfolio import pearson_correlation

from .experiment_lab_models import (
    MIN_OBSERVATIONS,
    LabAxis,
    LabConfig,
    LabStrategy,
    ProgressCallback,
    Simulation,
)
from .experiment_lab_simulation import simulate


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


class _AllocationSearch:
    def __init__(
        self, candidates: Sequence[LabStrategy], axis: LabAxis, config: LabConfig,
        progress: ProgressCallback | None,
    ) -> None:
        self.candidates = candidates
        self.axis = axis
        self.config = config
        self.progress = progress
        self.size = axis.size
        self.series = [candidate.series for candidate in candidates]
        self.margins = [candidate.margin_per_unit for candidate in candidates]
        self.per_strategy_cap = max(int(config.max_units_per_strategy), 1)
        self.total_cap = max(int(config.max_units_total), 1)

    def combine(self, units: Sequence[int]) -> tuple[list[float], float]:
        daily = [0.0] * self.size
        margin = 0.0
        for index, count in enumerate(units):
            if count <= 0:
                continue
            for position in range(self.size):
                daily[position] += self.series[index][position] * count
            margin += self.margins[index] * count
        return daily, margin

    def acceptable(self, simulation: Simulation, margin: float) -> bool:
        if simulation.ruined or simulation.max_dd_pct > self.config.max_dd_pct:
            return False
        if self.config.max_margin_pct > 0 and margin > 0:
            return (
                margin / max(self.config.capital, 1e-9) * 100.0
                <= self.config.max_margin_pct
            )
        return True

    def uniform_units(self) -> int:
        cap = min(
            self.per_strategy_cap,
            max(self.total_cap // len(self.candidates), 0),
        )
        low, high, best = 0, cap, 0
        if self.progress:
            self.progress(f"Base uniforme: bisección hasta {cap} unidades por estrategia")
        while low <= high and high > 0:
            middle = (low + high) // 2
            if middle <= 0:
                break
            daily, margin = self.combine([middle] * len(self.candidates))
            simulation = simulate(
                daily, self.axis, margin_required=margin, config=self.config,
            )
            if self.acceptable(simulation, margin):
                best, low = middle, middle + 1
            else:
                high = middle - 1
        return best

    def initial_state(
        self,
    ) -> tuple[list[int], list[float], float, Simulation]:
        units = [self.uniform_units()] * len(self.candidates)
        daily, margin = self.combine(units)
        simulation = simulate(
            daily, self.axis, margin_required=margin, config=self.config,
        )
        if not self.acceptable(simulation, margin):
            units = [0] * len(self.candidates)
            daily, margin = self.combine(units)
            simulation = simulate(
                daily, self.axis, margin_required=margin, config=self.config,
            )
        return units, daily, margin, simulation

    def best_increment(
        self, units: list[int], daily: list[float], margin: float,
        simulation: Simulation, symbol_units: dict[str, int],
    ) -> tuple[int, tuple[list[float], float, Simulation] | None]:
        best_index, best_profit, best_state = -1, simulation.final_equity, None
        for index, candidate in enumerate(self.candidates):
            if units[index] >= self.per_strategy_cap:
                continue
            if symbol_units.get(candidate.symbol_key, 0) >= self.config.max_units_per_symbol:
                continue
            trial_daily = [
                daily[position] + candidate.series[position]
                for position in range(self.size)
            ]
            trial_margin = margin + candidate.margin_per_unit
            trial = simulate(
                trial_daily, self.axis, margin_required=trial_margin, config=self.config,
            )
            if self.acceptable(trial, trial_margin) and trial.final_equity > best_profit:
                best_index, best_profit = index, trial.final_equity
                best_state = (trial_daily, trial_margin, trial)
        return best_index, best_state

    def greedy(
        self, units: list[int], daily: list[float], margin: float,
        simulation: Simulation,
    ) -> tuple[list[int], list[float], float, Simulation, int, str]:
        reason, steps = "sin margen para más unidades", 0
        symbol_units: dict[str, int] = {}
        for index, candidate in enumerate(self.candidates):
            symbol_units[candidate.symbol_key] = (
                symbol_units.get(candidate.symbol_key, 0) + units[index]
            )
        for step in range(max(int(self.config.greedy_steps), 0)):
            if simulation.final_equity >= self.config.target_equity:
                reason = "objetivo alcanzado"
                break
            if sum(units) >= self.total_cap:
                reason = "tope de unidades totales"
                break
            if self.progress and step % 20 == 0:
                self.progress(
                    f"Ajuste fino {step}/{self.config.greedy_steps} · "
                    f"{sum(units)} unidades · {simulation.final_equity:,.0f}"
                )
            index, best = self.best_increment(
                units, daily, margin, simulation, symbol_units,
            )
            if index < 0 or best is None:
                break
            daily, margin, simulation = best
            units[index] += 1
            symbol = self.candidates[index].symbol_key
            symbol_units[symbol] = symbol_units.get(symbol, 0) + 1
            steps += 1
        else:
            if simulation.final_equity < self.config.target_equity:
                reason = "agotados los pasos de ajuste"
        return units, daily, margin, simulation, steps, reason


def search_allocation(
    candidates: Sequence[LabStrategy],
    axis: LabAxis,
    config: LabConfig,
    *,
    progress: ProgressCallback | None = None,
) -> Allocation:
    """Busca una base uniforme y la afina unidad a unidad."""
    if not candidates or not axis.size:
        empty = simulate([], axis, margin_required=0.0, config=config)
        return Allocation({}, empty, 0.0, 0, "pool vacío")
    search = _AllocationSearch(candidates, axis, config, progress)
    units, daily, margin, simulation = search.initial_state()
    units, daily, margin, simulation, steps, reason = search.greedy(
        units, daily, margin, simulation,
    )
    final_units = {
        candidates[index].key: units[index]
        for index in range(len(candidates)) if units[index] > 0
    }
    daily, margin = search.combine(units)
    simulation = simulate(
        daily, axis, margin_required=margin, config=config, keep_curve=True,
    )
    return Allocation(final_units, simulation, margin, steps, reason)

