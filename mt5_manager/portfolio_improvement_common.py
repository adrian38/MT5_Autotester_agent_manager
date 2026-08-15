from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from portfolio_manager.ubs_portfolio import (
    PortfolioEvaluation,
    PortfolioResult,
    RobustStrategySet,
    portfolio_symbol_key,
    strategy_correlation_pair,
)


@dataclass(frozen=True)
class ImprovementOptions:
    max_additions: int = 2
    exclude_used_sets: bool = True
    allow_same_symbol: bool = True
    min_efficiency_gain_pct: float = 3.0


def improvement_options(inputs: dict[str, Any]) -> ImprovementOptions:
    max_additions = int(inputs.get("improvement_additions") or 2)
    if not 1 <= max_additions <= 5:
        raise ValueError("La mejora admite un máximo de entre 1 y 5 estrategias nuevas")
    minimum_gain = float(inputs.get("improvement_min_efficiency_gain_pct") or 3.0)
    if not 0 <= minimum_gain <= 25:
        raise ValueError("La mejora mínima beneficio/DD debe estar entre 0 y 25 %")
    return ImprovementOptions(
        max_additions=max_additions,
        exclude_used_sets=bool(inputs.get("improvement_exclude_used_sets", True)),
        allow_same_symbol=bool(inputs.get("improvement_allow_same_symbol", True)),
        min_efficiency_gain_pct=minimum_gain,
    )


def unique_original_members(
    detail: dict[str, Any],
    preferred_variant: str = "",
) -> list[dict[str, Any]]:
    """Return one active row per original set, preferring the requested A/M/C variant."""
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    for member in detail.get("members") or []:
        if int(member.get("units") or 0) <= 0:
            continue
        set_id = str(member.get("set_path") or member.get("set_id") or "").strip()
        if not set_id:
            continue
        variant = str(member.get("variant_key") or "")
        priority = 0 if preferred_variant and variant == preferred_variant else 1
        key = str(Path(set_id)).replace("\\", "/").casefold()
        previous = selected.get(key)
        if previous is None or priority < previous[0]:
            selected[key] = (priority, dict(member))
    return [item[1] for item in selected.values()]


def member_rows(
    members: Iterable[dict[str, Any]],
    *,
    resolve_path: Callable[[Any], str] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild the accepted-pipeline row shape without querying candidate status again."""
    resolve = resolve_path or (lambda value: str(value or ""))
    return [
        {
            "candidate_id": item.get("candidate_id"),
            "set_path": resolve(item.get("set_path") or item.get("set_id")),
            "symbol": item.get("symbol"),
            "target_symbol": item.get("symbol"),
            "period": item.get("timeframe"),
            "family": "",
            "is_report_path": resolve(item.get("is_report_path")),
            "oos_report_path": resolve(item.get("oos_report_path")),
            "final_tick_report_path": resolve(item.get("final_tick_report_path")),
            "full_history_report_path": resolve(item.get("full_history_report_path")),
            "max_balance_dd_001": item.get("max_balance_dd_001"),
            "max_equity_dd_001": item.get("max_equity_dd_001"),
            "floating_dd_source": item.get("floating_dd_source"),
            "recent_net_profit_001": item.get("recent_net_profit_001"),
            "recent_equity_dd_001": item.get("recent_equity_dd_001"),
            "has_recent_performance": item.get("has_recent_performance"),
        }
        for item in members
    ]


def allocation_units(
    detail: dict[str, Any],
    variant_key: str = "",
    *,
    resolve_path: Callable[[Any], str] | None = None,
) -> dict[str, int]:
    resolve = resolve_path or (lambda value: str(value or ""))
    units: dict[str, int] = {}
    for member in detail.get("members") or []:
        member_variant = str(member.get("variant_key") or "")
        if variant_key and member_variant != variant_key:
            continue
        set_id = resolve(member.get("set_path") or member.get("set_id"))
        if set_id and int(member.get("units") or 0) > 0:
            units[set_id] = int(member["units"])
    if not units and variant_key:
        return allocation_units(detail, resolve_path=resolve_path)
    return units


def used_paths_for_improvement(
    source: Any,
    current_scope: str,
    portfolio_id: int,
) -> list[str]:
    """Exclude sets used by any other normal or monthly UBS portfolio."""
    paths: set[str] = set()
    for scope in ("full_history", "monthly"):
        excluded = portfolio_id if scope == current_scope else None
        paths.update(source.used_set_paths(scope, exclude_portfolio_id=excluded))
    return sorted(paths)


def recent_positive_candidates(
    candidates: Iterable[RobustStrategySet],
    original_ids: set[str],
) -> list[RobustStrategySet]:
    return [
        strategy
        for strategy in candidates
        if strategy.set_id in original_ids
        or (
            strategy.has_recent_performance
            and float(strategy.recent_net_profit_001) > 0
        )
    ]


def portfolio_efficiency(net_profit: float, drawdown: float) -> float:
    if net_profit <= 0:
        return float(net_profit)
    return float(net_profit) / max(float(drawdown), 1e-9)


def _max_or_zero(values: Iterable[float]) -> float:
    return max((float(value) for value in values), default=0.0)


def validate_and_attach_improvement_audit(
    *,
    result: PortfolioResult,
    baseline: PortfolioEvaluation,
    all_sets: Sequence[RobustStrategySet],
    original_ids: Sequence[str],
    options: ImprovementOptions,
    inputs: dict[str, Any],
    scope: str,
    minimum_gain_pct: float | None = None,
) -> dict[str, Any]:
    active_ids = {
        allocation.set_id for allocation in result.allocations if allocation.units > 0
    }
    original_set = set(original_ids)
    missing_originals = sorted(original_set - active_ids)
    if missing_originals:
        raise ValueError(
            "La mejora intentó retirar estrategias originales: "
            + ", ".join(Path(value).name for value in missing_originals)
        )
    added_ids = sorted(active_ids - original_set)
    if not 1 <= len(added_ids) <= options.max_additions:
        raise ValueError(
            "La mejora debe añadir al menos una estrategia y no superar el máximo "
            f"de {options.max_additions}; añadió {len(added_ids)}"
        )

    by_id = {strategy.set_id: strategy for strategy in all_sets}
    originals = [by_id[set_id] for set_id in original_ids if set_id in by_id]
    active_sets = [by_id[set_id] for set_id in active_ids if set_id in by_id]
    max_pair = float(inputs.get("max_pair_corr") if inputs.get("max_pair_corr") is not None else 0.35)
    max_downside = float(
        inputs.get("max_downside_corr")
        if inputs.get("max_downside_corr") is not None
        else 0.25
    )
    max_overlap = float(
        inputs.get("max_dd_overlap")
        if inputs.get("max_dd_overlap") is not None
        else 0.35
    )
    candidate_audit: list[dict[str, Any]] = []
    for added_id in added_ids:
        strategy = by_id.get(added_id)
        if strategy is None:
            raise ValueError(f"No se pudo auditar la estrategia nueva {Path(added_id).name}")
        if not strategy.has_recent_performance or strategy.recent_net_profit_001 <= 0:
            raise ValueError(
                f"{Path(added_id).name} no aporta beneficio positivo en Final Tick 6M"
            )
        peers = [peer for peer in active_sets if peer.set_id != added_id]
        pairs = [strategy_correlation_pair(strategy, peer) for peer in peers]
        pearson = _max_or_zero(max(pair.pearson_corr, 0.0) for pair in pairs)
        downside = _max_or_zero(max(pair.downside_corr, 0.0) for pair in pairs)
        overlap = _max_or_zero(pair.dd_overlap for pair in pairs)
        if pearson > max_pair + 1e-9 or downside > max_downside + 1e-9 or overlap > max_overlap + 1e-9:
            raise ValueError(
                f"{Path(added_id).name} no justifica su diversificación: "
                f"corr {pearson:.2f}/{max_pair:.2f}, downside {downside:.2f}/{max_downside:.2f}, "
                f"solapamiento DD {overlap:.2f}/{max_overlap:.2f}"
            )
        same_symbol = any(
            portfolio_symbol_key(peer.symbol) == portfolio_symbol_key(strategy.symbol)
            for peer in originals
        )
        if same_symbol and not options.allow_same_symbol:
            raise ValueError(
                f"{Path(added_id).name} repite símbolo y la opción no está marcada"
            )
        candidate_audit.append(
            {
                "set_id": added_id,
                "set_name": Path(added_id).name,
                "symbol": strategy.symbol,
                "same_symbol_as_original": same_symbol,
                "recent_net_profit_001": round(float(strategy.recent_net_profit_001), 6),
                "max_pearson_corr": round(pearson, 6),
                "max_downside_corr": round(downside, 6),
                "max_dd_overlap": round(overlap, 6),
                "justification": (
                    "Mismo símbolo aceptado por baja dependencia"
                    if same_symbol
                    else "Nueva exposición aceptada por baja dependencia"
                ),
            }
        )

    baseline_efficiency = portfolio_efficiency(
        baseline.total_net_profit, baseline.valley_dd
    )
    improved_efficiency = portfolio_efficiency(
        result.total_net_profit, result.actual_valley_dd
    )
    if baseline_efficiency > 0:
        gain_pct = (improved_efficiency / baseline_efficiency - 1.0) * 100.0
    elif result.total_net_profit > baseline.total_net_profit:
        gain_pct = 100.0
    else:
        gain_pct = -100.0
    required_gain = (
        options.min_efficiency_gain_pct
        if minimum_gain_pct is None
        else float(minimum_gain_pct)
    )
    if gain_pct + 1e-9 < required_gain:
        raise ValueError(
            "La propuesta no mejora suficientemente beneficio/DD: "
            f"{gain_pct:.2f}% frente al mínimo {required_gain:.2f}%"
        )
    if result.actual_valley_dd > result.target_valley_dd + 1e-9:
        raise ValueError("La propuesta mejorada supera el DD valle permitido")

    audit = {
        "scope": scope,
        "verdict": "ACEPTADA",
        "originals_locked": True,
        "original_count": len(original_set),
        "removed_original_ids": [],
        "added_count": len(added_ids),
        "maximum_additions": options.max_additions,
        "added_set_ids": added_ids,
        "exclude_used_sets": options.exclude_used_sets,
        "allow_same_symbol": options.allow_same_symbol,
        "baseline": {
            "net_profit": round(float(baseline.total_net_profit), 6),
            "valley_dd": round(float(baseline.valley_dd), 6),
            "profit_dd": round(float(baseline_efficiency), 6),
        },
        "improved": {
            "net_profit": round(float(result.total_net_profit), 6),
            "valley_dd": round(float(result.actual_valley_dd), 6),
            "profit_dd": round(float(improved_efficiency), 6),
        },
        "efficiency_gain_pct": round(float(gain_pct), 6),
        "minimum_efficiency_gain_pct": round(float(required_gain), 6),
        "correlation_limits": {
            "pearson": max_pair,
            "downside": max_downside,
            "drawdown_overlap": max_overlap,
        },
        "candidates": candidate_audit,
    }
    result.seasonal_validation = dict(result.seasonal_validation or {})
    result.seasonal_validation["portfolio_improvement"] = audit
    result.warnings.insert(
        0,
        "Mejora controlada: "
        f"{len(original_set)} originales bloqueadas, {len(added_ids)} nueva(s), "
        f"beneficio/DD {gain_pct:+.2f}%.",
    )
    return audit
