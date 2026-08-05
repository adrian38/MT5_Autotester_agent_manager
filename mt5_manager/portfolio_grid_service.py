"""Independent orchestration for the Grid UBS portfolio scope."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from portfolio_manager.grid_portfolio import grid_floating_dd_001, optimize_grid_portfolio
from portfolio_manager.grid_set import filter_rows_grid_on
from portfolio_manager.ubs_portfolio import (
    PortfolioType,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    portfolio_display_symbol,
    portfolio_group_key,
    summarize_robust_rows,
)


GRID_VARIANTS = (
    ("aggressive", "Agresivo Grid", PortfolioType.AGGRESSIVE),
    ("balanced", "Moderado Grid", PortfolioType.BALANCED),
    ("conservative", "Conservador Grid", PortfolioType.CONSERVATIVE),
)


def _adjusted_grid_valley_pcts(
    raw_sets: list[Any],
    *,
    capital: float,
    reserve_pct: float,
    requested_pct: float,
    min_trades: int,
) -> list[float]:
    """Return executable Grid valley floors above the requested percentage."""
    if capital <= 0:
        return []
    reserve_factor = 1.0 - min(max(float(reserve_pct), 0.0), 99.0) / 100.0
    requested_limit = float(capital) * float(requested_pct) / 100.0 * reserve_factor
    risks = sorted({
        round(max(
            grid_floating_dd_001(strategy),
            float(strategy.valley_dd_2020_2026_001),
        ), 8)
        for strategy in raw_sets
        if strategy.robustness_status == "accepted"
        and not strategy.already_used
        and strategy.curve_2020_2026_001
        and strategy.trades_2020_2026 >= int(min_trades)
        and strategy.net_profit_2020_2026_001 > 0
        and max(
            grid_floating_dd_001(strategy),
            float(strategy.valley_dd_2020_2026_001),
        ) > requested_limit + 1e-9
    })
    return [
        risk / float(capital) * 100.0 / reserve_factor + 1e-7
        for risk in risks
    ]


def normalize_grid_settings(raw: dict[str, Any], broker: str = "ICTRADING") -> dict[str, Any]:
    """Normalize Grid without introducing internal EA risk limits."""
    from .portfolio_service import normalize_settings

    values = normalize_settings("full_history", raw, broker)
    values.update({
        "portfolio_scope": "grid",
        # Grid is dimensioned only by the portfolio valley. Historical saved
        # settings may still contain the former hidden one-unit cap, so clear
        # every unit ceiling explicitly instead of inheriting it from ``raw``.
        "max_units_per_set": None,
        "max_total_units": None,
        "max_units_per_symbol": None,
        "grid_off": False,
        "experimental_full_search": False,
        "experimental_monthly_search": False,
        "enforce_point_dd": False,
        "max_daily_dd": None,
        "daily_dd_full_history": False,
    })
    return values


def grid_inventory(source: Any, settings: dict[str, Any]) -> dict[str, Any]:
    rows = source.candidate_rows(include_quarantined=True)
    allowed = set(settings.get("allowed_asset_groups") or [])
    rows = [
        row for row in rows
        if portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        ) in allowed
    ]
    rows, warnings = filter_rows_grid_on(rows)
    quarantine = source.quarantine_rows()
    quarantined = {source._path_key(row.get("set_path")) for row in quarantine}
    used_paths = source.used_set_paths("grid") if settings.get("exclude_used_sets", True) else []
    used = {source._path_key(path) for path in used_paths}
    by_symbol: dict[str, dict[str, int]] = {}
    for row in rows:
        symbol = portfolio_display_symbol(str(row.get("target_symbol") or row.get("symbol") or ""))
        counts = by_symbol.setdefault(
            symbol, {"total": 0, "quarantined": 0, "used": 0, "available": 0},
        )
        counts["total"] += 1
        key = source._path_key(row.get("set_path"))
        is_quarantined = key in quarantined
        is_used = key in used
        counts["quarantined"] += int(is_quarantined)
        counts["used"] += int(is_used)
        counts["available"] += int(not is_quarantined and not is_used)
    symbol_rows = [{"symbol": symbol, **counts} for symbol, counts in sorted(by_symbol.items())]
    return {
        "scope": "grid",
        "total": sum(row["total"] for row in symbol_rows),
        "quarantined": sum(row["quarantined"] for row in symbol_rows),
        "used": sum(row["used"] for row in symbol_rows),
        "available": sum(row["available"] for row in symbol_rows),
        "symbols": len(symbol_rows),
        "by_symbol": symbol_rows,
        "quarantine": quarantine,
        "quarantine_excludes": True,
        "warnings": warnings,
    }


def generate_grid_proposals(
    source: Any,
    inputs: dict[str, Any],
    progress: Callable[[str], None] | None = None,
    *,
    exclude_portfolio_id: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from .portfolio_service import (
        _optimizer_kwargs,
        _reserve_pct,
        build_margin_model,
        cached_report,
    )

    settings = normalize_grid_settings(inputs, source.broker)
    settings["margin_model"] = build_margin_model(source, settings)
    if progress:
        progress("Grid 1/4 · Leyendo candidatos aceptados en las cuatro etapas")
    rows = source.candidate_rows(include_quarantined=False)
    if not rows:
        raise ValueError("No hay candidatos con Final Tick continuo y 6M aceptados")
    rows, warnings = filter_rows_grid_on(rows)
    if not rows:
        raise ValueError("No hay candidatos aceptados con EnableGrid=true explícito")
    if settings.get("require_3_positive_months_6m"):
        rows, found = filter_rows_by_recent_positive_months(
            rows,
            min_positive_months=3,
            window_months=6,
            parse=cached_report,
        )
        warnings.extend(found)
    allowed = set(settings["allowed_asset_groups"])
    group_counts: dict[str, int] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        group = portfolio_group_key(
            str(row.get("target_symbol") or row.get("symbol") or ""),
            universe_files=[source.universe],
        )
        group_counts[group] = group_counts.get(group, 0) + 1
        if group in allowed:
            filtered.append(row)
    rows = filtered
    if not rows:
        raise ValueError("No quedan candidatos grid en los grupos de activos seleccionados")
    used = (
        source.used_set_paths("grid", exclude_portfolio_id=exclude_portfolio_id)
        if settings.get("exclude_used_sets", True)
        else []
    )
    availability = asdict(summarize_robust_rows(rows, used))
    if progress:
        progress(f"Grid 2/4 · Cargando reportes de {len(rows)} candidatos")
    raw_sets, load_warnings = load_robust_sets_from_rows(
        rows, used, parse=cached_report, progress=progress,
    )
    warnings.extend(load_warnings)
    raw_sets = [
        strategy for strategy in raw_sets
        if portfolio_group_key(strategy.symbol, universe_files=[source.universe]) in allowed
    ]
    if not raw_sets:
        raise ValueError("No quedan estrategias grid cargadas después de los filtros")
    proposals: list[dict[str, Any]] = []
    variant_failures: list[str] = []
    configured_reserve = float(settings.get("dd_reserve_pct") or 0.0)
    for index, (key, label, objective_type) in enumerate(GRID_VARIANTS, start=1):
        if progress:
            progress(f"Grid 3/4 · Optimizando {label} ({index}/3)")
        reserve = _reserve_pct(configured_reserve, objective_type)
        existing = source.saved_curves(
            monthly=False,
            scope="grid",
            portfolio_type=objective_type,
            exclude_portfolio_id=exclude_portfolio_id,
        )
        variant_inputs = {
            **settings,
            "portfolio_type": key,
            "dd_reserve_pct": reserve,
        }
        auto_adjusted = False
        requested_valley_pct = float(variant_inputs["valley_dd_pct"])
        try:
            result = optimize_grid_portfolio(
                raw_sets,
                **_optimizer_kwargs(variant_inputs, objective_type, existing, reserve),
            )
        except ValueError as exc:
            first_error = exc
            result = None
            for adjusted_pct in _adjusted_grid_valley_pcts(
                raw_sets,
                capital=float(settings["capital"]),
                reserve_pct=reserve,
                requested_pct=requested_valley_pct,
                min_trades=int(settings["min_trades_2020_2026"]),
            ):
                adjusted_inputs = {**variant_inputs, "valley_dd_pct": adjusted_pct}
                try:
                    result = optimize_grid_portfolio(
                        raw_sets,
                        **_optimizer_kwargs(adjusted_inputs, objective_type, existing, reserve),
                    )
                except ValueError:
                    continue
                variant_inputs = adjusted_inputs
                auto_adjusted = True
                result.warnings.insert(
                    0,
                    f"El valle solicitado {requested_valley_pct:.3f}% no admite el lote mínimo "
                    f"de este pool. Esta propuesta usa el mínimo ejecutable {adjusted_pct:.3f}%.",
                )
                break
            if result is None:
                variant_failures.append(f"{label}: {first_error}")
                continue
        result.warnings[:0] = warnings
        proposals.append({
            "key": key,
            "label": label,
            "reserve_pct": reserve,
            "auto_adjusted_valley": auto_adjusted,
            "requested_valley_dd_pct": requested_valley_pct,
            "adjusted_valley_dd_pct": float(variant_inputs["valley_dd_pct"]),
            "inputs": variant_inputs,
            "result": result,
        })
    if not proposals:
        raise ValueError("; ".join(variant_failures))
    warnings.extend(variant_failures)
    for proposal in proposals:
        proposal["result"].warnings.extend(variant_failures)
    availability.update({
        "loaded_sets": len(raw_sets),
        "group_counts": group_counts,
        "warnings": warnings,
        "grid_only": True,
    })
    if progress:
        progress("Grid 4/4 · Propuestas listas")
    return availability, proposals


def run_grid_operation(
    source: Any,
    operation: str,
    portfolio_id: int | None,
    settings: dict[str, Any],
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if operation not in {"generate", "reoptimize", "complete"}:
        raise ValueError(f"Operación grid desconocida: {operation}")
    if operation in {"reoptimize", "complete"} and portfolio_id is None:
        raise ValueError("Falta el portafolio grid guardado")
    return generate_grid_proposals(
        source,
        settings,
        progress,
        exclude_portfolio_id=portfolio_id if operation != "generate" else None,
    )
