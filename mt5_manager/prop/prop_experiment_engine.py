"""Motor puro: trials prop sobre series diarias y búsqueda por aprobación."""

from __future__ import annotations

import statistics
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Callable, Sequence

from portfolio_manager.grid_set import set_file_has_enabled_grid

from ..experiment.experiment_lab import LabAxis, LabStrategy
from .prop_profiles import PropPhase, PropProfile
from .the5ers_hyper_growth_budget import simulate_monthly_campaign
from .the5ers_hyper_growth_rules import rules_for_capital


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
    def score(self) -> tuple[int, float, float, float]:
        days = self.median_days if self.median_days else 1e9
        return self.passed, -days, self.pass_rate, self.mean_peak_progress_pct


@dataclass(frozen=True)
class SearchResult:
    allocations: dict[str, int]
    evaluation: Evaluation
    strategies: tuple[PropStrategy, ...]


def _is_grid_strategy(item: PropStrategy) -> bool:
    path = Path(item.lab.set_path)
    name_marks_grid = "grid" in path.stem.lower().replace("-", "_").split("_")
    return name_marks_grid or set_file_has_enabled_grid(path)


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
            "Prop: Grid habilitado manualmente; el filtro de drawdown cero sigue activo."
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
    series = combined_series(strategies, allocations)
    if profile.profile_id == "the5ers-growth":
        campaign = simulate_monthly_campaign(
            series, axis.days, rules_for_capital(capital),
            float(1000.0 if bankroll_eur is None else bankroll_eur),
            float(52.0 if evaluation_fee_eur is None else evaluation_fee_eur),
        )
        passed_days = [attempt.days for attempt in campaign.attempts if attempt.passed]
        completed = campaign.completed
        return Evaluation(
            trials=completed, passed=campaign.passed,
            pass_rate=(campaign.passed / completed * 100.0 if completed else 0.0),
            median_days=(statistics.median(passed_days) if passed_days else 0.0),
            mean_peak_progress_pct=campaign.mean_peak_progress_pct,
            failures={"stop_out": campaign.stop_outs} if campaign.stop_outs else {},
        )
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


def _eligible_additions(
    ranked: Sequence[PropStrategy], allocations: dict[str, int], capital: float,
) -> list[PropStrategy]:
    used_margin = sum(
        item.lab.margin_per_unit * allocations.get(item.lab.key, 0)
        for item in ranked
    )
    return [
        item for item in ranked
        if used_margin + item.lab.margin_per_unit <= capital + 1e-9
    ]


def search(
    strategies: Sequence[PropStrategy], axis: LabAxis, profile: PropProfile,
    settings: dict[str, float | int], progress: Callable[[str], None] | None = None,
    required_keys: set[str] | None = None,
) -> SearchResult:
    capital = float(settings["capital"])
    kwargs = dict(
        axis=axis, profile=profile, capital=capital,
        max_days=120, bankroll_eur=float(settings.get("bankroll_eur", 1000.0)),
        evaluation_fee_eur=float(settings.get("evaluation_fee_eur", 52.0)),
    )
    singles = []
    for index, item in enumerate(strategies, 1):
        singles.append((evaluate(strategies, {item.lab.key: 1}, **kwargs), item))
        if progress:
            progress(f"Evaluando estrategia {index}/{len(strategies)} · {item.lab.origin}")
    singles.sort(key=lambda pair: pair[0].score, reverse=True)
    ranked = [item for _evaluation, item in singles]
    required = required_keys or set()
    allocations: dict[str, int] = {
        item.lab.key: 1 for item in ranked if item.lab.key in required
    }
    missing = required - set(allocations)
    if missing:
        raise ValueError("Faltan estrategias obligatorias para recomponer el Portafolio Prop")
    required_margin = sum(
        item.lab.margin_per_unit for item in ranked if item.lab.key in allocations
    )
    if required_margin > capital + 1e-9:
        raise ValueError("Las estrategias supervivientes exceden el margen de la cuenta prop")
    current = evaluate(ranked, allocations, **kwargs)
    step = 0
    while True:
        choices = _eligible_additions(ranked, allocations, capital)
        if not choices:
            break
        evaluated = []
        for item in choices:
            proposal = dict(allocations)
            proposal[item.lab.key] = proposal.get(item.lab.key, 0) + 1
            evaluated.append((evaluate(ranked, proposal, **kwargs), item, proposal))
        best, item, proposal = max(evaluated, key=lambda row: row[0].score)
        if allocations and best.score <= current.score:
            break
        allocations, current = proposal, best
        step += 1
        if progress:
            progress(
                f"Búsqueda {step}: {current.passed} pasadas · "
                f"mediana {current.median_days:g} días · {current.pass_rate:.1f}% · "
                f"{sum(allocations.values())} unidades · {item.lab.origin}"
            )
    selected = tuple(item for item in ranked if allocations.get(item.lab.key, 0))
    return SearchResult(allocations, current, selected)


def _allocation_payload(item: PropStrategy, units: int) -> dict[str, object]:
    return {
        "key": item.lab.key, "origin": item.lab.origin,
        "origin_node": item.lab.origin_node, "source_kind": item.source_kind,
        "set_id": item.lab.set_id, "set_path": item.lab.set_path,
        "symbol": item.lab.symbol, "timeframe": item.lab.timeframe,
        "units": units, "lot": round(units * 0.01, 8),
        "risk_multiplier": item.risk_multiplier,
        "net_profit_contribution": sum(item.series) * units,
        "standalone_valley_dd": item.lab.valley_dd * item.risk_multiplier * units,
        "margin_required": item.lab.margin_per_unit * item.risk_multiplier * units,
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
    evaluation["attempts_per_pass"] = (
        100.0 / result.evaluation.pass_rate if result.evaluation.pass_rate else None
    )
    payload = {
        "evaluation": evaluation,
        "allocations": [
            _allocation_payload(item, result.allocations[item.lab.key])
            for item in result.strategies
        ],
    }
    if axis is not None:
        payload["portfolio"] = _portfolio_payload(result, axis, capital)
    return payload
