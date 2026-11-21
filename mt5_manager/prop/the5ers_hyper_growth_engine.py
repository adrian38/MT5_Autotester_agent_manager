"""Motor autónomo de evaluaciones The5ers Hyper Growth de una fase."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from .the5ers_hyper_growth_rules import HYPER_GROWTH_5K, HyperGrowthRules


@dataclass(frozen=True)
class GrowthDay:
    """PnL cerrado del día y equity observada en el rollover, si existe."""

    pnl: float
    midnight_equity: float | None = None


@dataclass(frozen=True)
class GrowthAttempt:
    passed: bool
    reason: str
    start_index: int
    end_index: int
    days: int
    final_balance: float
    peak_progress_pct: float
    daily_pauses: int

    @property
    def completed(self) -> bool:
        return self.reason in {"passed", "stop_out"}


@dataclass(frozen=True)
class GrowthCampaign:
    attempts: tuple[GrowthAttempt, ...]
    completed: int
    passed: int
    pass_rate: float
    median_days_to_pass: float
    stop_outs: int
    daily_pauses: int
    unresolved: int


def _day(value: float | GrowthDay) -> GrowthDay:
    return value if isinstance(value, GrowthDay) else GrowthDay(float(value))


def _progress(balance: float, rules: HyperGrowthRules) -> float:
    target_profit = rules.target_balance - rules.initial_balance
    return max((balance - rules.initial_balance) / target_profit * 100.0, 0.0)


def _daily_result(
    balance: float, observation: GrowthDay, rules: HyperGrowthRules,
) -> tuple[float, str]:
    equity = balance if observation.midnight_equity is None else observation.midnight_equity
    pause_base = max(balance, equity)
    pause_level = pause_base * (1.0 - rules.daily_pause_pct / 100.0)
    raw_balance = balance + observation.pnl
    first_protection = max(pause_level, rules.stop_out_level)
    if raw_balance > first_protection:
        return raw_balance, "traded"
    if rules.stop_out_level >= pause_level:
        return rules.stop_out_level, "stop_out"
    return pause_level, "daily_pause"


def simulate_evaluation(
    series: Sequence[float | GrowthDay], start: int = 0,
    rules: HyperGrowthRules = HYPER_GROWTH_5K, max_days: int | None = None,
) -> GrowthAttempt:
    """Simula una cuenta hasta aprobar, alcanzar stop-out o agotar evidencia."""
    if start < 0 or start >= len(series):
        raise ValueError("El inicio de la evaluación queda fuera del histórico")
    end = len(series) if max_days is None else min(len(series), start + max(max_days, 1))
    balance = rules.initial_balance
    peak = 0.0
    pauses = 0
    for cursor in range(start, end):
        balance, event = _daily_result(balance, _day(series[cursor]), rules)
        days = cursor - start + 1
        peak = max(peak, _progress(balance, rules))
        if event == "stop_out":
            return GrowthAttempt(
                False, "stop_out", start, cursor + 1, days, balance, peak, pauses,
            )
        pauses += int(event == "daily_pause")
        if balance + 1e-9 >= rules.target_balance and days >= rules.minimum_days:
            return GrowthAttempt(
                True, "passed", start, cursor + 1, days, balance, peak, pauses,
            )
    reason = "history_end" if end == len(series) else "observation_window"
    return GrowthAttempt(False, reason, start, end, end - start, balance, peak, pauses)


def simulate_campaign(
    series: Sequence[float | GrowthDay], *, start: int = 0,
    rules: HyperGrowthRules = HYPER_GROWTH_5K, max_attempts: int | None = None,
) -> GrowthCampaign:
    """Reinicia tras cada pass/fallo y nunca reutiliza un mismo día histórico."""
    attempts: list[GrowthAttempt] = []
    cursor = start
    while cursor < len(series) and (max_attempts is None or len(attempts) < max_attempts):
        attempt = simulate_evaluation(series, cursor, rules)
        attempts.append(attempt)
        if not attempt.completed:
            break
        cursor = attempt.end_index
    completed = sum(item.completed for item in attempts)
    passed = sum(item.passed for item in attempts)
    passed_days = [item.days for item in attempts if item.passed]
    return GrowthCampaign(
        attempts=tuple(attempts), completed=completed, passed=passed,
        pass_rate=(passed / completed * 100.0 if completed else 0.0),
        median_days_to_pass=(statistics.median(passed_days) if passed_days else 0.0),
        stop_outs=sum(item.reason == "stop_out" for item in attempts),
        daily_pauses=sum(item.daily_pauses for item in attempts),
        unresolved=sum(not item.completed for item in attempts),
    )
