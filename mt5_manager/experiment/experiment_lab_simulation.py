from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .experiment_lab_models import LabAxis, LabConfig, Simulation


@dataclass
class _SimulationState:
    equity: float
    peak: float
    scale: float
    worst_dd_pct: float
    worst_dd_amount: float
    worst_margin_pct: float
    curve: list[float]
    rebalances: list[dict[str, Any]]
    monthly: list[dict[str, Any]]
    month_start_equity: float
    current_month: int
    ruined: bool = False


def _scale_function(
    capital: float, config: LabConfig, margin_required: float,
) -> Callable[[float], float]:
    forced = float(config.forced_scale or 0.0)
    rebalance = max(int(config.rebalance_months), 0)
    margin_cap_pct = max(float(config.max_margin_pct), 0.0)

    def scale_for(equity: float) -> float:
        base = (equity / capital) if rebalance else 1.0
        if forced > 0:
            base *= forced
        if margin_required > 0 and margin_cap_pct > 0:
            allowed = equity * margin_cap_pct / 100.0 / margin_required
            base = min(base, max(allowed, 0.0))
        return max(base, 0.0)

    return scale_for


def _initial_state(
    capital: float, axis: LabAxis, margin_required: float,
    scale_for: Callable[[float], float], keep_curve: bool,
) -> _SimulationState:
    scale = scale_for(capital)
    return _SimulationState(
        equity=capital, peak=capital, scale=scale,
        worst_dd_pct=0.0, worst_dd_amount=0.0,
        worst_margin_pct=margin_required * scale / capital * 100.0,
        curve=[capital] if keep_curve else [],
        rebalances=[{
            "month": axis.days[0][:7], "scale": round(scale, 4),
            "equity": round(capital, 2),
        }],
        monthly=[], month_start_equity=capital,
        current_month=axis.month_index[0],
    )


def _roll_month(
    state: _SimulationState, axis: LabAxis, position: int,
    rebalance: int, scale_for: Callable[[float], float],
) -> None:
    state.monthly.append({
        "month": axis.days[position - 1][:7],
        "profit": round(state.equity - state.month_start_equity, 2),
        "equity": round(state.equity, 2),
        "scale": round(state.scale, 4),
    })
    state.month_start_equity = state.equity
    state.current_month = axis.month_index[position]
    if rebalance and state.current_month % rebalance == 0:
        state.scale = scale_for(state.equity)
        state.rebalances.append({
            "month": axis.days[position][:7],
            "scale": round(state.scale, 4),
            "equity": round(state.equity, 2),
        })


def _apply_daily_value(
    state: _SimulationState, value: float, margin_required: float, keep_curve: bool,
) -> bool:
    state.equity += value * state.scale
    if margin_required > 0 and state.equity > 0:
        state.worst_margin_pct = max(
            state.worst_margin_pct,
            margin_required * state.scale / state.equity * 100.0,
        )
    if state.equity <= 0:
        state.ruined = True
        state.equity = 0.0
        if keep_curve:
            state.curve.append(0.0)
        state.worst_dd_pct = 100.0
        state.worst_dd_amount = max(state.worst_dd_amount, state.peak)
        return True
    state.peak = max(state.peak, state.equity)
    drop = state.peak - state.equity
    if drop > 0:
        state.worst_dd_amount = max(state.worst_dd_amount, drop)
        state.worst_dd_pct = max(state.worst_dd_pct, drop / state.peak * 100.0)
    if keep_curve:
        state.curve.append(state.equity)
    return False


def simulate(
    daily: Sequence[float],
    axis: LabAxis,
    *,
    margin_required: float,
    config: LabConfig,
    keep_curve: bool = False,
) -> Simulation:
    """Doce meses de la cartera con recomposición de lotes y DD relativo."""
    capital = max(float(config.capital), 0.0)
    if capital <= 0 or not axis.size:
        return Simulation(
            days=0, final_equity=capital, profit=0.0, return_pct=0.0,
            max_dd_pct=0.0, max_dd_amount=0.0, max_margin_pct=0.0,
            final_scale=0.0, ruined=capital <= 0,
        )
    scale_for = _scale_function(capital, config, margin_required)
    state = _initial_state(capital, axis, margin_required, scale_for, keep_curve)
    rebalance = max(int(config.rebalance_months), 0)
    for position, value in enumerate(daily):
        if axis.month_index[position] != state.current_month:
            _roll_month(state, axis, position, rebalance, scale_for)
        if _apply_daily_value(state, value, margin_required, keep_curve):
            break
    else:
        state.monthly.append({
            "month": axis.days[-1][:7],
            "profit": round(state.equity - state.month_start_equity, 2),
            "equity": round(state.equity, 2),
            "scale": round(state.scale, 4),
        })
    return Simulation(
        days=axis.size, final_equity=state.equity,
        profit=state.equity - capital,
        return_pct=(state.equity - capital) / capital * 100.0,
        max_dd_pct=state.worst_dd_pct,
        max_dd_amount=state.worst_dd_amount,
        max_margin_pct=state.worst_margin_pct,
        final_scale=state.scale, ruined=state.ruined,
        equity_curve=_sample_curve(state.curve) if keep_curve else [],
        rebalances=state.rebalances, monthly_profit=state.monthly,
    )



def _sample_curve(curve: list[float], *, points: int = 260) -> list[float]:
    if len(curve) <= points:
        return [round(value, 2) for value in curve]
    step = len(curve) / points
    sampled = [curve[min(int(index * step), len(curve) - 1)] for index in range(points)]
    sampled[-1] = curve[-1]
    return [round(value, 2) for value in sampled]
