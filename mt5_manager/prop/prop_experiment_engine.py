"""Motor puro: trials prop sobre series diarias y búsqueda por aprobación."""

from __future__ import annotations

import statistics
from pathlib import Path
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from heapq import merge
from typing import Any, Callable, Iterator, Sequence

from portfolio_manager.grid_set import set_file_has_enabled_grid
from portfolio_manager.ubs_portfolio import ClosedTrade

from ..experiment.experiment_lab import LabAxis, LabStrategy
from .prop_execution import PropContract, VolumeSpec
from .prop_position_book import PositionEvent
from .prop_profiles import PropPhase, PropProfile
from .prop_experiment_search import CompositionSearch, daily_margin_usage
from .prop_search_base import search_base_payload
from .the5ers_hyper_growth_budget import simulate_monthly_campaign
from .the5ers_hyper_growth_engine import GrowthClose, GrowthDay
from .the5ers_hyper_growth_rules import rules_for_capital


@dataclass(frozen=True)
class PropStrategy:
    lab: LabStrategy
    source_kind: str
    risk_multiplier: float
    series: tuple[float, ...]
    lot_per_unit: float = 0.01
    coverage: tuple[tuple[str, str], ...] | None = None
    closed_trades: tuple[ClosedTrade, ...] = ()
    closes: dict[int, tuple[GrowthClose, ...]] | None = None
    source_volume: VolumeSpec | None = None
    prop_contract: PropContract | None = None
    source_lot_per_unit: float = 0.0
    position_events: dict[int, tuple[PositionEvent, ...]] | None = None
    margin_by_day: tuple[float, ...] | None = None


@dataclass(frozen=True)
class TrialResult:
    passed: bool
    days: int
    reason: str
    peak_progress_pct: float


@dataclass(frozen=True)
class Evaluation:
    trials: int
    passed: int
    pass_rate: float
    median_days: float
    mean_peak_progress_pct: float
    failures: dict[str, int]
    margin_rejections: int = 0
    minimum_free_margin: float | None = None
    peak_margin: float | None = None

    @property
    def margin_feasible(self) -> bool:
        return self.margin_rejections == 0 and (self.minimum_free_margin or 0.0) >= -1e-9

    @property
    def score(self) -> tuple[bool, int, float, float, float]:
        days = self.median_days if self.median_days else 1e9
        return self.margin_feasible, self.passed, -days, self.pass_rate, self.mean_peak_progress_pct


@dataclass(frozen=True)
class SearchResult:
    allocations: dict[str, int]
    evaluation: Evaluation
    strategies: tuple[PropStrategy, ...]
    proposals_by_size: tuple[dict[str, Any], ...] = ()
    search_base: dict[str, Any] | None = None


def set_path_is_grid(value: Any) -> bool:
    """Grid por el nombre del fichero o por traer la rejilla activada.

    El inventario de la tarjeta y :func:`credible_pool` tienen que decidirlo
    con el mismo criterio. Mientras sólo lo miraba el pool ejecutable, la
    tarjeta prometía 1062 sets utilizables en RoboForex y la fase 1 descartaba
    201 nada más empezar.
    """
    path = Path(str(value or ""))
    name_marks_grid = "grid" in path.stem.lower().replace("-", "_").split("_")
    return name_marks_grid or set_file_has_enabled_grid(path)


def _is_grid_strategy(item: PropStrategy) -> bool:
    return set_path_is_grid(item.lab.set_path)


def executable_pool(
    pool: Sequence[LabStrategy], seed_keys: set[str], manual_keys: set[str],
) -> list[PropStrategy]:
    """Convierte cada estrategia en su posición mínima ejecutable real."""
    executable: list[PropStrategy] = []
    for item in pool:
        executable.append(PropStrategy(
            lab=item,
            source_kind=(
                "seed" if item.key in seed_keys
                else "manual" if item.key in manual_keys else "candidate"
            ),
            risk_multiplier=1.0,
            series=tuple(item.series),
        ))
    return executable


def credible_pool(
    strategies: Sequence[PropStrategy], include_grids: bool = False,
) -> tuple[list[PropStrategy], list[str]]:
    """Aplica únicamente la decisión explícita del checkbox Grid."""
    accepted: list[PropStrategy] = []
    grid = 0
    for item in strategies:
        if not include_grids and _is_grid_strategy(item):
            grid += 1
        else:
            accepted.append(item)
    warnings = []
    if grid:
        warnings.append(f"Prop: {grid} estrategia(s) Grid excluidas; requieren riesgo flotante.")
    if include_grids:
        warnings.append(
            "Prop: Grid habilitado; los cierres históricos no muestran la pérdida flotante."
        )
    return accepted, warnings


def combined_series(strategies: Sequence[PropStrategy], allocations: dict[str, int]) -> list[float]:
    if not strategies:
        return []
    values = [0.0] * len(strategies[0].series)
    for strategy in strategies:
        units = allocations.get(strategy.lab.key, 0)
        if not units:
            continue
        for index, value in enumerate(strategy.series):
            values[index] += value * units
    return values


def _scaled_closes(closes: tuple[GrowthClose, ...], units: int) -> Iterator[GrowthClose]:
    if units == 1:
        yield from closes
    else:
        for item in closes:
            yield GrowthClose(item.time, item.opened, item.pnl * units)


def growth_series(
    strategies: Sequence[PropStrategy], allocations: dict[str, int], axis: LabAxis,
) -> list[float] | list[GrowthDay]:
    """Combina cierres simultáneos en un mismo calendario, conservando su orden."""
    selected = [(item, allocations[item.lab.key]) for item in sorted(strategies, key=_strategy_key)
                if allocations.get(item.lab.key)]
    if selected and all(item.position_events is not None for item, _ in selected):
        return _position_days(selected, axis)
    if any(item.position_events is not None for item, _ in selected):
        raise ValueError("Faltan aperturas verificables para parte de la composición Prop")
    if not selected or any(item.closes is None for item, _units in selected):
        return combined_series(strategies, allocations)
    days: dict[int, list[Iterator[GrowthClose]]] = {}
    for item, units in selected:
        for index, closes in (item.closes or {}).items():
            days.setdefault(index, []).append(_scaled_closes(closes, units))
    return [
        GrowthDay(0.0, date=day, closes=_merge_day(days.get(index, [])))
        for index, day in enumerate(axis.days)
    ]


def _position_days(selected, axis):
    days = {}
    for item, units in selected:
        for index, events in item.position_events.items():
            days.setdefault(index, []).append(_scale_events(events, units))
    return [GrowthDay(0.0, date=day, positions=_merge_position_events(days.get(index, ())))
            for index, day in enumerate(axis.days)]


def _strategy_key(item):
    return item.lab.key


def _merge_position_events(sources):
    if len(sources) == 1:
        return tuple(sources[0])
    return tuple(merge(*sources, key=_event_time)) if sources else ()


def _event_time(event):
    return event.time


def _scale_events(events, units):
    if units == 1:
        return events
    return (PositionEvent(event.time, event.position, event.opening, units) for event in events)


def _merge_day(sources: list[Iterator[GrowthClose]]) -> tuple[GrowthClose, ...]:
    if len(sources) == 1:
        return tuple(sources[0])
    if sources:
        return tuple(merge(*sources, key=lambda item: item.time))
    return ()


def _phase_ready(
    phase: PropPhase, balance: float, initial: float, days: int,
    profitable_days: int, best_day: float,
) -> bool:
    profit = balance - initial
    if profit < initial * phase.target_pct / 100.0:
        return False
    if days < phase.min_trading_days or profitable_days < phase.min_profitable_days:
        return False
    if phase.consistency_pct and profit > 0:
        return best_day / profit * 100.0 <= phase.consistency_pct + 1e-9
    return True


def _max_loss_limit(phase: PropPhase, initial: float, highest_eod: float) -> float:
    base = highest_eod if phase.max_loss_mode == "eod_trailing" else initial
    return base - initial * phase.max_loss_pct / 100.0


def _simulate_phase(
    series: Sequence[float], start: int, phase: PropPhase, capital: float, max_days: int,
) -> tuple[TrialResult, int]:
    balance = capital
    highest_eod = capital
    best_day = 0.0
    profitable_days = 0
    peak_progress = 0.0
    target_amount = capital * phase.target_pct / 100.0
    end = min(len(series), start + max_days)
    for cursor in range(start, end):
        pnl = series[cursor]
        day_limit = balance - capital * phase.daily_loss_pct / 100.0
        next_balance = balance + pnl
        if next_balance < day_limit:
            return TrialResult(False, cursor - start + 1, "daily_loss", peak_progress), cursor + 1
        if next_balance < _max_loss_limit(phase, capital, highest_eod):
            return TrialResult(False, cursor - start + 1, "max_loss", peak_progress), cursor + 1
        balance = next_balance
        highest_eod = max(highest_eod, balance)
        best_day = max(best_day, pnl)
        threshold = capital * phase.profitable_day_pct / 100.0
        if pnl > 0 and pnl + 1e-9 >= threshold:
            profitable_days += 1
        peak_progress = min(
            100.0,
            max(peak_progress, (balance - capital) / max(target_amount, 1.0) * 100.0),
        )
        days = cursor - start + 1
        if _phase_ready(phase, balance, capital, days, profitable_days, best_day):
            return TrialResult(True, days, "passed", peak_progress), cursor + 1
    return TrialResult(False, end - start, "timeout", peak_progress), end


def simulate_trial(
    series: Sequence[float], start: int, profile: PropProfile, capital: float, max_days: int,
) -> TrialResult:
    cursor = start
    total_days = 0
    peak = 0.0
    for phase in profile.phases:
        result, cursor = _simulate_phase(series, cursor, phase, capital, max_days)
        total_days += result.days
        peak = max(peak, result.peak_progress_pct)
        if not result.passed:
            return TrialResult(False, total_days, result.reason, peak)
    return TrialResult(True, total_days, "passed", peak)


def evaluate(
    strategies: Sequence[PropStrategy], allocations: dict[str, int], axis: LabAxis,
    profile: PropProfile, capital: float, max_days: int,
    bankroll_eur: float | None = None, evaluation_fee_eur: float | None = None,
) -> Evaluation:
    if profile.profile_id == "the5ers-growth":
        campaign = simulate_monthly_campaign(
            growth_series(strategies, allocations, axis), axis.days, rules_for_capital(capital),
            float(1000.0 if bankroll_eur is None else bankroll_eur),
            float(52.0 if evaluation_fee_eur is None else evaluation_fee_eur),
        )
        passed_days = [
            (date.fromisoformat(axis.days[attempt.result.end_index - 1])
             - date.fromisoformat(axis.days[attempt.result.start_index])).days + 1
            for attempt in campaign.attempts if attempt.passed
        ]
        completed = campaign.completed
        return Evaluation(
            trials=completed, passed=campaign.passed,
            pass_rate=(campaign.passed / completed * 100.0 if completed else 0.0),
            median_days=(statistics.median(passed_days) if passed_days else 0.0),
            mean_peak_progress_pct=campaign.mean_peak_progress_pct,
            failures={"stop_out": campaign.stop_outs} if campaign.stop_outs else {},
            margin_rejections=campaign.margin_rejections,
            minimum_free_margin=campaign.minimum_free_margin,
            peak_margin=campaign.peak_margin,
        )
    series = combined_series(strategies, allocations)
    complete_window = max_days * len(profile.phases)
    last_start = max(len(series) - complete_window + 1, 1)
    starts = list(range(last_start))
    results = [simulate_trial(series, start, profile, capital, max_days) for start in starts]
    passed_days = [result.days for result in results if result.passed]
    failures: dict[str, int] = {}
    for result in results:
        if not result.passed:
            failures[result.reason] = failures.get(result.reason, 0) + 1
    passed = len(passed_days)
    return Evaluation(
        trials=len(results), passed=passed,
        pass_rate=(passed / len(results) * 100.0 if results else 0.0),
        median_days=(statistics.median(passed_days) if passed_days else 0.0),
        mean_peak_progress_pct=(statistics.fmean(r.peak_progress_pct for r in results) if results else 0.0),
        failures=failures,
    )


def search(
    strategies: Sequence[PropStrategy], axis: LabAxis, profile: PropProfile,
    settings: dict[str, float | int], progress: Callable[[str], None] | None = None,
    required_keys: set[str] | None = None,
    *, incumbent: dict[str, int] | None = None,
) -> SearchResult:
    capital = float(settings["capital"])
    kwargs = dict(
        axis=axis, profile=profile, capital=capital,
        max_days=120, bankroll_eur=float(settings.get("bankroll_eur", 1000.0)),
        evaluation_fee_eur=float(settings.get("evaluation_fee_eur", 52.0)),
    )
    by_key = {
        item.lab.key: item for item in strategies
        if isfinite(item.lab.margin_per_unit) and item.lab.margin_per_unit > 0
    }
    required = required_keys or set()
    if required - by_key.keys():
        raise ValueError("Faltan estrategias obligatorias para recomponer el Portafolio Prop")

    def evaluate_proposal(proposal):
        return evaluate(tuple(by_key[key] for key in proposal), proposal, **kwargs)

    daily_margins = {key: item.margin_by_day for key, item in by_key.items() if item.margin_by_day}
    optimizer = CompositionSearch(
        {key: item.lab.margin_per_unit for key, item in by_key.items()},
        capital, required, evaluate_proposal, progress,
        daily_margins=daily_margins if daily_margins.keys() == by_key.keys() else None,
    )
    allocations, measured = optimizer.run(incumbent)
    if not measured.margin_feasible:
        raise ValueError("Ninguna composición evaluada sostuvo todas sus posiciones con el margen libre")
    if progress:
        progress(
            f"{measured.passed} pasadas · mediana {measured.median_days:g} días · "
            f"{measured.pass_rate:.1f}% · {len(optimizer.scores)} composiciones comparadas"
        )
    return SearchResult(
        allocations, measured, tuple(by_key[key] for key in allocations), optimizer.summary(),
        search_base_payload(optimizer, by_key, axis),
    )


def _allocation_payload(item: PropStrategy, units: int) -> dict[str, object]:
    return {
        "key": item.lab.key, "origin": item.lab.origin,
        "origin_node": item.lab.origin_node, "source_kind": item.source_kind,
        "set_id": item.lab.set_id, "set_path": item.lab.set_path,
        "symbol": item.lab.symbol, "timeframe": item.lab.timeframe,
        "units": units, "lot": round(units * item.lot_per_unit, 8),
        "source_lot": round(units * (item.source_lot_per_unit or item.lot_per_unit), 8),
        "risk_multiplier": item.risk_multiplier,
        "net_profit_contribution": sum(item.series) * units,
        "standalone_valley_dd": item.lab.valley_dd * item.risk_multiplier * units,
        "margin_required": item.lab.margin_per_unit * item.risk_multiplier * units,
        **({"prop_symbol": item.prop_contract.symbol, "prop_contract_size": item.prop_contract.volume.contract,
            "source_contract_size": item.source_volume.contract, "prop_leverage": item.prop_contract.leverage}
           if item.prop_contract and item.source_volume else {}),
    }


def _portfolio_payload(
    result: SearchResult, axis: LabAxis, capital: float,
) -> dict[str, object]:
    series = combined_series(result.strategies, result.allocations)
    balance = peak = max_drawdown = 0.0
    curve: list[dict[str, object]] = []
    for day, pnl in zip(axis.days, series):
        balance += pnl
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)
        curve.append({"time": day, "value": balance})
    daily_margins = {item.lab.key: item.margin_by_day for item in result.strategies if item.margin_by_day}
    if daily_margins and daily_margins.keys() == result.allocations.keys():
        margin = max(daily_margin_usage(daily_margins, result.allocations))
    else:
        margin = sum(
            item.lab.margin_per_unit * item.risk_multiplier * result.allocations[item.lab.key]
            for item in result.strategies
        )
    return {
        "curve": curve, "net_profit": balance, "max_drawdown": max_drawdown,
        "best_day": max(series, default=0.0), "worst_day": min(series, default=0.0),
        "active_days": sum(1 for item in series if item), "margin_required": margin,
        "margin_pct": margin / capital * 100.0 if capital else 0.0,
    }


def result_payload(
    result: SearchResult, axis: LabAxis | None = None, capital: float = 0.0,
) -> dict[str, object]:
    evaluation = asdict(result.evaluation)
    evaluation['margin_feasible'] = result.evaluation.margin_feasible
    evaluation["attempts_per_pass"] = (
        100.0 / result.evaluation.pass_rate if result.evaluation.pass_rate else None
    )
    payload = {
        "valuation_currency": "EUR",
        "evaluation": evaluation,
        "proposals_by_size": list(result.proposals_by_size),
        "allocations": [
            _allocation_payload(item, result.allocations[item.lab.key])
            for item in result.strategies
        ],
    }
    if axis is not None:
        payload["portfolio"] = _portfolio_payload(result, axis, capital)
    if result.search_base is not None:
        payload["search_base"] = result.search_base
    return payload
