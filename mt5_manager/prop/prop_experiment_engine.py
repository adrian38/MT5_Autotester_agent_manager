"""Motor puro: trials prop sobre series diarias y búsqueda por aprobación."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Callable, Sequence

from ..experiment_lab import LabAxis, LabStrategy
from .prop_profiles import PropPhase, PropProfile


@dataclass(frozen=True)
class PropStrategy:
    lab: LabStrategy
    source_kind: str
    risk_multiplier: float
    series: tuple[float, ...]


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

    @property
    def score(self) -> tuple[float, float, float]:
        days = self.median_days if self.median_days else 1e9
        return self.pass_rate, self.mean_peak_progress_pct, -days


@dataclass(frozen=True)
class SearchResult:
    allocations: dict[str, int]
    evaluation: Evaluation
    strategies: tuple[PropStrategy, ...]


def risk_normalized_pool(
    pool: Sequence[LabStrategy], seed_keys: set[str], capital: float, risk_unit_pct: float,
) -> list[PropStrategy]:
    risk_amount = capital * risk_unit_pct / 100.0
    normalized: list[PropStrategy] = []
    for item in pool:
        multiplier = risk_amount / max(item.valley_dd, 1.0)
        normalized.append(PropStrategy(
            lab=item,
            source_kind="seed" if item.key in seed_keys else "candidate",
            risk_multiplier=multiplier,
            series=tuple(value * multiplier for value in item.series),
        ))
    return normalized


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
        peak_progress = max(peak_progress, (balance - capital) / max(target_amount, 1.0) * 100.0)
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
    profile: PropProfile, capital: float, max_days: int, stride: int, max_trials: int,
) -> Evaluation:
    series = combined_series(strategies, allocations)
    complete_window = max_days * len(profile.phases)
    last_start = max(len(series) - complete_window + 1, 1)
    starts = list(range(0, last_start, max(stride, 1)))[:max_trials]
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


def _eligible_additions(
    ranked: Sequence[PropStrategy], allocations: dict[str, int], max_strategies: int,
    max_units_per_strategy: int,
) -> list[PropStrategy]:
    active = sum(1 for units in allocations.values() if units)
    return [
        item for item in ranked
        if allocations.get(item.lab.key, 0) < max_units_per_strategy
        and (allocations.get(item.lab.key, 0) or active < max_strategies)
    ]


def search(
    strategies: Sequence[PropStrategy], axis: LabAxis, profile: PropProfile,
    settings: dict[str, float | int], progress: Callable[[str], None] | None = None,
) -> SearchResult:
    capital = float(settings["capital"])
    kwargs = dict(
        axis=axis, profile=profile, capital=capital,
        max_days=int(settings["max_days_per_phase"]), stride=int(settings["trial_stride_days"]),
        max_trials=int(settings["max_trials"]),
    )
    singles = [(evaluate(strategies, {item.lab.key: 1}, **kwargs), item) for item in strategies]
    singles.sort(key=lambda pair: pair[0].score, reverse=True)
    ranked = [item for _evaluation, item in singles[:int(settings["search_pool_limit"])]]
    allocations: dict[str, int] = {}
    current = evaluate(ranked, allocations, **kwargs)
    for step in range(int(settings["max_units_total"])):
        choices = _eligible_additions(
            ranked, allocations, int(settings["max_strategies"]),
            int(settings["max_units_per_strategy"]),
        )
        if not choices:
            break
        evaluated = []
        for item in choices:
            proposal = dict(allocations)
            proposal[item.lab.key] = proposal.get(item.lab.key, 0) + 1
            evaluated.append((evaluate(ranked, proposal, **kwargs), item, proposal))
        best, item, proposal = max(evaluated, key=lambda row: row[0].score)
        if step and best.score <= current.score:
            break
        allocations, current = proposal, best
        if progress:
            progress(
                f"Búsqueda {step + 1}: {current.pass_rate:.1f}% · "
                f"{sum(allocations.values())} unidades · {item.lab.origin}"
            )
    selected = tuple(item for item in ranked if allocations.get(item.lab.key, 0))
    return SearchResult(allocations, current, selected)


def result_payload(result: SearchResult) -> dict[str, object]:
    evaluation = asdict(result.evaluation)
    evaluation["attempts_per_pass"] = (
        100.0 / result.evaluation.pass_rate if result.evaluation.pass_rate else None
    )
    return {
        "evaluation": evaluation,
        "allocations": [
            {
                "key": item.lab.key, "origin": item.lab.origin,
                "origin_node": item.lab.origin_node, "source_kind": item.source_kind,
                "set_id": item.lab.set_id, "set_path": item.lab.set_path,
                "symbol": item.lab.symbol, "timeframe": item.lab.timeframe,
                "units": result.allocations[item.lab.key],
                "risk_multiplier": item.risk_multiplier,
            }
            for item in result.strategies
        ],
    }
