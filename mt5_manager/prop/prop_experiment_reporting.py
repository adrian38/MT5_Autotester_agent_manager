"""Presentación del calendario producido por la campaña Hyper Growth real."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .prop_experiment_engine import SearchResult, combined_series
from .prop_profiles import PropProfile
from .the5ers_hyper_growth_budget import BudgetAttempt, simulate_monthly_campaign
from .the5ers_hyper_growth_rules import rules_for_capital


def _empty_month(bankroll: float) -> dict[str, Any]:
    return {
        "bankroll_eur": bankroll, "spent_eur": 0.0, "remaining_eur": bankroll,
        "started": 0, "passed": 0, "failed": 0, "in_progress": 0,
        "daily_pauses": 0, "failures": {},
    }


def _calendar_rows(days: tuple[str, ...], attempts: tuple[BudgetAttempt, ...]) -> list[dict[str, Any]]:
    rows = [{"date": day, "status": "no_budget"} for day in days]
    for number, funded in enumerate(attempts, 1):
        attempt = funded.result
        for index in range(attempt.start_index, attempt.end_index):
            rows[index] = {
                "date": days[index], "status": "in_progress",
                "attempt": number, "started": days[attempt.start_index],
            }
        if attempt.completed:
            finish = attempt.end_index - 1
            rows[finish].update({
                "status": "passed" if attempt.passed else "failed",
                "days": attempt.days, "reason": attempt.reason,
                "peak_progress_pct": attempt.peak_progress_pct,
            })
    return rows


def _outcome_month(days: tuple[str, ...], attempt: BudgetAttempt) -> str:
    index = max(attempt.result.end_index - 1, attempt.result.start_index)
    return days[index][:7]


def _monthly_rows(
    days: tuple[str, ...], attempts: tuple[BudgetAttempt, ...],
    spent: dict[str, float], bankroll: float,
) -> list[dict[str, Any]]:
    months: dict[str, dict[str, Any]] = defaultdict(lambda: _empty_month(bankroll))
    for day in days:
        months[day[:7]]
    for month, value in spent.items():
        months[month]["spent_eur"] = value
        months[month]["remaining_eur"] = bankroll - value
    for funded in attempts:
        months[funded.charged_month]["started"] += 1
        outcome = months[_outcome_month(days, funded)]
        outcome["daily_pauses"] += funded.result.daily_pauses
        if funded.result.passed:
            outcome["passed"] += 1
        elif funded.result.completed:
            outcome["failed"] += 1
            outcome["failures"]["stop_out"] = outcome["failures"].get("stop_out", 0) + 1
        else:
            outcome["in_progress"] += 1
    return [{"month": month, **values} for month, values in sorted(months.items())]


def attempt_analysis(
    result: SearchResult, axis: Any, profile: PropProfile, settings: dict[str, Any],
) -> dict[str, Any]:
    """Usa exactamente la misma campaña financiada con la que se puntuó la cartera."""
    bankroll = float(settings["bankroll_eur"])
    fee = float(settings["evaluation_fee_eur"])
    campaign = simulate_monthly_campaign(
        combined_series(result.strategies, result.allocations), axis.days,
        rules_for_capital(float(settings["capital"])), bankroll, fee,
    )
    monthly = _monthly_rows(axis.days, campaign.attempts, campaign.spent_by_month, bankroll)
    return {
        "calendar": _calendar_rows(axis.days, campaign.attempts),
        "monthly": monthly,
        "totals": {
            "started": len(campaign.attempts), "passed": campaign.passed,
            "failed": campaign.stop_outs, "in_progress": campaign.unresolved,
            "daily_pauses": sum(item.result.daily_pauses for item in campaign.attempts),
        },
        "bankroll": {
            "monthly_eur": bankroll, "fee_per_attempt_eur": fee,
            "max_attempts_per_month": int(bankroll // fee) if fee > 0 else 0,
        },
    }
