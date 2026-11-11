from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from .experiment_lab_models import LabAxis, LabConfig, LabStrategy, Verdict
from .experiment_lab_search import Allocation
from .experiment_lab_simulation import simulate


@dataclass(frozen=True)
class _MissedTarget:
    scale: float = 0.0
    dd_pct: float = 0.0
    ruined: bool = False
    margin_pct: float = 0.0
    note: str = ""


def _target_scale_metrics(
    allocation: Allocation, candidates: Sequence[LabStrategy],
    axis: LabAxis, config: LabConfig, scale: float,
) -> tuple[float, bool, float]:
    forced = replace(config, forced_scale=scale, max_margin_pct=0.0)
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
    margin_pct = margin * scale / config.capital * 100.0 if config.capital > 0 else 0.0
    return scaled.max_dd_pct, scaled.ruined, margin_pct


def _missed_target(
    allocation: Allocation, candidates: Sequence[LabStrategy],
    axis: LabAxis, config: LabConfig, capital_for_target: float,
) -> _MissedTarget:
    simulation = allocation.simulation
    if simulation.ruined:
        return _MissedTarget(note=(
            "La cuenta se queda a cero durante la ventana: la composición no es operable."
        ))
    if not allocation.units:
        return _MissedTarget(note=(
            "La búsqueda no pudo asignar ni una unidad sin pasarse del límite de "
            f"drawdown ({config.max_dd_pct:,.0f}%) o de margen "
            f"({config.max_margin_pct:,.0f}%). Sube los límites o baja el capital exigido."
        ))
    if simulation.profit <= 0:
        return _MissedTarget(note=(
            "El pool no gana dinero en la ventana; el objetivo no depende del capital."
        ))
    scale = (float(config.target_equity) - config.capital) / simulation.profit
    dd_pct, ruined, margin_pct = _target_scale_metrics(
        allocation, candidates, axis, config, scale,
    )
    note = (
        f"Con este retorno el objetivo sale de un capital inicial de "
        f"{capital_for_target:,.0f}. Desde {config.capital:,.0f} haría falta "
        f"multiplicar los lotes por {scale:,.1f}: eso lleva el "
        f"drawdown al {dd_pct:,.1f}% y el margen al "
        f"{margin_pct:,.0f}% del capital (límite {config.max_margin_pct:,.0f}%)."
        + (" A esa escala la cuenta se queda a cero durante la ventana." if ruined else "")
    )
    return _MissedTarget(scale, dd_pct, ruined, margin_pct, note)


def build_verdict(
    allocation: Allocation,
    candidates: Sequence[LabStrategy],
    axis: LabAxis,
    config: LabConfig,
) -> Verdict:
    """Resume si llega y, si no, el capital y riesgo necesarios."""
    simulation = allocation.simulation
    target = float(config.target_equity)
    reached = simulation.final_equity >= target and not simulation.ruined
    growth = simulation.final_equity / config.capital if config.capital > 0 else 0.0
    capital_for_target = target / growth if growth > 0 else 0.0
    missed = (
        _MissedTarget(note="El pool cruzado llega al objetivo dentro del límite de drawdown.")
        if reached else
        _missed_target(allocation, candidates, axis, config, capital_for_target)
    )
    return Verdict(
        reached=reached, target_equity=target,
        final_equity=simulation.final_equity,
        gap=target - simulation.final_equity,
        annual_return_pct=simulation.return_pct,
        capital_for_target=capital_for_target,
        scale_for_target=missed.scale,
        dd_at_target_pct=missed.dd_pct,
        dd_at_target_ruined=missed.ruined,
        margin_at_target_pct=missed.margin_pct,
        note=missed.note,
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


def _allocation_members(
    allocation: Allocation, candidates: Sequence[LabStrategy],
) -> list[dict[str, Any]]:
    by_key = {candidate.key: candidate for candidate in candidates}
    members: list[dict[str, Any]] = []
    ranked = sorted(
        allocation.units.items(),
        key=lambda item: by_key[item[0]].net * item[1] if item[0] in by_key else 0.0,
        reverse=True,
    )
    for key, units in ranked:
        candidate = by_key.get(key)
        if candidate is None:
            continue
        members.append({
            "key": key, "origin": candidate.origin,
            "origin_node": candidate.origin_node, "symbol": candidate.symbol,
            "timeframe": candidate.timeframe, "units": units,
            "net_contribution": round(candidate.net * units, 2),
            "unit_net": round(candidate.net, 2),
            "unit_valley_dd": round(candidate.valley_dd, 2),
            "margin": round(candidate.margin_per_unit * units, 2),
            "portable": candidate.portable,
            "portability": candidate.portability, "set_path": candidate.set_path,
        })
    return members


def _simulation_payload(allocation: Allocation) -> dict[str, Any]:
    simulation = allocation.simulation
    return {
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
    }


def _allocation_payload(
    allocation: Allocation, members: list[dict[str, Any]], config: LabConfig,
) -> dict[str, Any]:
    return {
        "strategies": len(members),
        "total_units": allocation.total_units,
        "margin_required": round(allocation.margin_required, 2),
        "margin_pct": round(
            allocation.margin_required / config.capital * 100.0
            if config.capital else 0.0, 2,
        ),
        "steps": allocation.steps,
        "stop_reason": allocation.stop_reason,
        "members": members,
    }


def _verdict_payload(verdict: Verdict) -> dict[str, Any]:
    return {
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
    }
def allocation_payload(
    allocation: Allocation,
    candidates: Sequence[LabStrategy],
    verdict: Verdict,
    axis: LabAxis,
    config: LabConfig,
) -> dict[str, Any]:
    members = _allocation_members(allocation, candidates)
    return {
        "window": {
            "days": axis.size, "months": axis.months,
            "from": axis.days[0] if axis.days else "",
            "to": axis.days[-1] if axis.days else "",
        },
        "simulation": _simulation_payload(allocation),
        "allocation": _allocation_payload(allocation, members, config),
        "verdict": _verdict_payload(verdict),
    }

