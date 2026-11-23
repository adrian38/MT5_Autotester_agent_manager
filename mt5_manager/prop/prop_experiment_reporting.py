"""Calendario de intentos financiados con un bankroll que se renueva cada mes."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .prop_experiment_engine import SearchResult, combined_series, simulate_trial
from .prop_profiles import PropProfile


def _empty_month(bankroll: float) -> dict[str, Any]:
    return {
        "bankroll_eur": bankroll, "spent_eur": 0.0, "remaining_eur": bankroll,
        "started": 0, "passed": 0, "failed": 0, "in_progress": 0,
        "failures": {},
    }


def _mark_outcome(month: dict[str, Any], status: str, reason: str) -> None:
    month[status] += 1
    if status == "failed":
        failures = month["failures"]
        failures[reason] = failures.get(reason, 0) + 1


def _next_month_index(days: tuple[str, ...], cursor: int) -> int:
    month = days[cursor][:7]
    while cursor < len(days) and days[cursor][:7] == month:
        cursor += 1
    return cursor


def _attempt_calendar(
    series: list[float], days: tuple[str, ...], profile: PropProfile,
    capital: float, bankroll: float, fee: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    months: dict[str, dict[str, Any]] = defaultdict(lambda: _empty_month(bankroll))
    calendar: list[dict[str, Any]] = []
    cursor = attempt = 0
    required_days = 120 * len(profile.phases)
    while cursor < len(days):
        start_month = days[cursor][:7]
        budget = months[start_month]
        if budget["remaining_eur"] + 1e-9 < fee:
            end = _next_month_index(days, cursor)
            calendar.extend(
                {"date": days[index], "status": "no_budget"}
                for index in range(cursor, end)
            )
            cursor = end
            continue
        attempt += 1
        budget["started"] += 1
        budget["spent_eur"] += fee
        budget["remaining_eur"] -= fee
        result = simulate_trial(series, cursor, profile, capital, 120)
        available = len(days) - cursor
        complete = result.passed or result.reason != "timeout" or available >= required_days
        status = "passed" if result.passed else "failed" if complete else "in_progress"
        finish = min(cursor + max(result.days, 1) - 1, len(days) - 1)
        calendar.extend({
            "date": days[index], "status": "in_progress", "attempt": attempt,
            "started": days[cursor],
        } for index in range(cursor, finish))
        calendar.append({
            "date": days[finish], "status": status, "attempt": attempt,
            "started": days[cursor], "days": result.days, "reason": result.reason,
            "peak_progress_pct": result.peak_progress_pct,
        })
        _mark_outcome(months[days[finish][:7]], status, result.reason)
        cursor = finish + 1
        if status == "in_progress":
            break
    rows = [{"month": month, **values} for month, values in sorted(months.items())]
    return calendar, rows


def attempt_analysis(
    result: SearchResult, axis: Any, profile: PropProfile, settings: dict[str, Any],
) -> dict[str, Any]:
    """Ejecuta intentos consecutivos sin gastar más del bankroll de cada mes."""
    bankroll = float(settings["bankroll_eur"])
    fee = float(settings["evaluation_fee_eur"])
    calendar, monthly = _attempt_calendar(
        combined_series(result.strategies, result.allocations), axis.days, profile,
        float(settings["capital"]), bankroll, fee,
    )
    totals = {
        key: sum(int(row[key]) for row in monthly)
        for key in ("started", "passed", "failed", "in_progress")
    }
    return {
        "calendar": calendar, "monthly": monthly, "totals": totals,
        "bankroll": {
            "monthly_eur": bankroll, "fee_per_attempt_eur": fee,
            "max_attempts_per_month": int(bankroll // fee) if fee > 0 else 0,
        },
    }
