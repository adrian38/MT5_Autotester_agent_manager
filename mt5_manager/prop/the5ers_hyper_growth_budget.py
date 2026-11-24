"""Campaña Hyper Growth limitada por el bankroll que se repone cada mes."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from .the5ers_hyper_growth_engine import GrowthAttempt, GrowthDay, simulate_evaluation
from .the5ers_hyper_growth_rules import HyperGrowthRules


@dataclass(frozen=True)
class BudgetAttempt:
    result: GrowthAttempt
    charged_month: str
    fee_eur: float

    @property
    def passed(self) -> bool:
        return self.result.passed

    @property
    def days(self) -> int:
        return self.result.days


@dataclass(frozen=True)
class MonthlyCampaign:
    attempts: tuple[BudgetAttempt, ...]
    spent_by_month: dict[str, float]

    @property
    def completed(self) -> int:
        return sum(item.result.completed for item in self.attempts)

    @property
    def passed(self) -> int:
        return sum(item.result.passed for item in self.attempts)

    @property
    def stop_outs(self) -> int:
        return sum(item.result.reason == "stop_out" for item in self.attempts)

    @property
    def unresolved(self) -> int:
        return sum(not item.result.completed for item in self.attempts)

    @property
    def margin_rejections(self) -> int:
        return sum(item.result.margin_rejections for item in self.attempts)

    @property
    def peak_margin(self) -> float | None:
        return max((item.result.peak_margin for item in self.attempts
                    if item.result.peak_margin is not None), default=None)

    @property
    def minimum_free_margin(self) -> float | None:
        return min((item.result.minimum_free_margin for item in self.attempts
                    if item.result.minimum_free_margin is not None), default=None)

    @property
    def mean_peak_progress_pct(self) -> float:
        values = [min(item.result.peak_progress_pct, 100.0) for item in self.attempts]
        return statistics.fmean(values) if values else 0.0


def _next_month(days: Sequence[str], cursor: int) -> int:
    month = days[cursor][:7]
    while cursor < len(days) and days[cursor][:7] == month:
        cursor += 1
    return cursor


def simulate_monthly_campaign(
    series: Sequence[float | GrowthDay], days: Sequence[str], rules: HyperGrowthRules,
    bankroll_eur: float, fee_eur: float,
) -> MonthlyCampaign:
    """Compra pruebas consecutivas sin superar el presupuesto de su mes de inicio."""
    if len(series) != len(days):
        raise ValueError("La serie Hyper Growth y su calendario no tienen la misma longitud")
    attempts: list[BudgetAttempt] = []
    spent: dict[str, float] = defaultdict(float)
    cursor = 0
    while cursor < len(series):
        month = days[cursor][:7]
        if fee_eur <= 0 or spent[month] + fee_eur > bankroll_eur + 1e-9:
            cursor = _next_month(days, cursor)
            continue
        result = simulate_evaluation(series, cursor, rules)
        attempts.append(BudgetAttempt(result, month, fee_eur))
        spent[month] += fee_eur
        if not result.completed:
            break
        cursor = result.end_index
    return MonthlyCampaign(tuple(attempts), dict(spent))
