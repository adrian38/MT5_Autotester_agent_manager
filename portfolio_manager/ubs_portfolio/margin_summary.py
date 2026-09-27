"""Modelos de margen por broker: apalancamiento, contract size y uso."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterable

from .symbols import (
    portfolio_group_key,
    portfolio_symbol_key,
)
from .models import RobustStrategySet


from .margin_models import (
    roboforex_margin_leverage,
    roboforex_contract_size,
    AXI_FALLBACK_GROUP_LEVERAGE,
    ACCOUNT_LEVERAGE_CHOICES,
    DEFAULT_ACCOUNT_LEVERAGE,
    ttp_leverage_for,
    MarginModel,
    margin_model_for_profile,
    resolve_margin_model,
)
from .margin_loaders import (
    _load_json_dict,
    load_symbol_specs,
    load_symbol_notional_from_specs,
    load_max_product_leverage,
    load_symbol_notional,
    load_unmeasured_symbols,
)
from .margin_profiles import (
    MARGIN_PROFILE_LABELS,
    MARGIN_PROFILES,
    margin_profile_label,
    margin_leverage_for_profile,
    margin_contract_size_for_profile,
    strategy_reference_price,
    allocation_notional,
)

def allocation_margin_required(
    strategy: RobustStrategySet,
    units: int,
    *,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    units = max(int(units), 0)
    if units <= 0:
        return 0.0
    model = resolve_margin_model(
        margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    measured = model.margin_for_one(strategy.symbol)
    if measured is not None:
        return units * measured
    leverage = model.leverage_for(strategy.symbol)
    if leverage <= 0:
        return float("inf")
    return allocation_notional(strategy, units, model) / leverage


@dataclass(frozen=True)
class _MarginMeasurement:
    by_set: dict[str, dict[str, float | str | int]]
    total: float
    total_notional: float
    unmeasured: list[str]


def _measure_margin_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    model: MarginModel,
) -> _MarginMeasurement:
    by_set: dict[str, dict[str, float | str | int]] = {}
    total = 0.0
    total_notional = 0.0
    unmeasured: list[str] = []
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        leverage = model.leverage_for(strategy.symbol)
        measured = model.margin_for_one(strategy.symbol)
        notional = allocation_notional(strategy, units, model)
        margin = allocation_margin_required(strategy, units, margin_profile=model)
        if measured is None and strategy.symbol not in unmeasured:
            unmeasured.append(strategy.symbol)
        total += margin
        total_notional += notional
        by_set[strategy.set_id] = {
            "symbol": strategy.symbol, "group": portfolio_group_key(strategy.symbol),
            "units": units, "lot": model.lot_size_for(strategy.symbol, units),
            "min_lot": model.min_lot_for(strategy.symbol), "leverage": leverage,
            "contract_size": model.contract_size_for(strategy.symbol),
            "price": strategy_reference_price(strategy), "notional": notional,
            "margin_measured": measured is not None,
            "notional_measured": model.notional_for(strategy.symbol) is not None,
            "margin": margin,
        }
    return _MarginMeasurement(by_set, total, total_notional, unmeasured)


def _margin_summary_payload(
    measured: _MarginMeasurement,
    model: MarginModel,
    balance: float,
    max_margin_pct: float,
) -> dict[str, object]:
    limit = float(balance) * float(max_margin_pct) / 100.0 if balance > 0 else 0.0
    applied_leverage: dict[str, float] = {}
    for entry in measured.by_set.values():
        applied_leverage.setdefault(str(entry["group"]), float(entry["leverage"]))
    symbols = {str(entry["symbol"]) for entry in measured.by_set.values()}
    measured_contract_sizes = sum(
        1 for symbol in symbols
        if model.symbol_contract_size.get(portfolio_symbol_key(symbol))
    )
    return {
        "enabled": True, "balance": float(balance),
        "max_margin_pct": float(max_margin_pct), "limit": limit,
        "total": measured.total, "notional": measured.total_notional,
        "group_leverage_applied": applied_leverage,
        "contract_size_measured": measured_contract_sizes,
        "symbol_count": len(symbols),
        "usage_pct": measured.total / limit * 100.0 if limit > 0 else 0.0,
        "profile": model.profile, "profile_label": margin_profile_label(model.profile),
        "account_leverage": float(model.account_leverage or 0.0),
        "reference_account_leverage": float(model.reference_account_leverage or 0.0),
        "margin_source": model.margin_source, "notional_source": model.notional_source,
        "unmeasured_symbols": measured.unmeasured,
        "stock_leverage": float(model.stock_leverage),
        "default_leverage": float(model.default_leverage),
        "stock_contract_size": float(model.stock_contract_size),
        "default_contract_size": float(model.default_contract_size),
        "by_set": measured.by_set,
    }


def portfolio_margin_summary(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float,
    max_margin_pct: float,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> dict[str, object]:
    model = resolve_margin_model(
        margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    measured = _measure_margin_allocations(sets, allocations, model)
    return _margin_summary_payload(measured, model, balance, max_margin_pct)


def allocations_respect_margin_limit(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> bool:
    if balance is None or max_margin_pct is None:
        return True
    summary = portfolio_margin_summary(
        sets,
        allocations,
        balance=float(balance),
        max_margin_pct=float(max_margin_pct),
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    return float(summary["total"]) <= float(summary["limit"]) + 1e-9
