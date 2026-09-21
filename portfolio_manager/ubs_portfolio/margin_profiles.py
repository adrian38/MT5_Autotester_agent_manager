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
    normalize_margin_profile,
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

MARGIN_PROFILE_LABELS: dict[str, str] = {
    "ictrading": "ICTrading",
    "axi": "AXI",
    "roboforex": "RoboForex",
    "ttp": "TTP",
}


MARGIN_PROFILES: tuple[str, ...] = tuple(MARGIN_PROFILE_LABELS)


def margin_profile_label(profile: str | MarginModel | None) -> str:
    return MARGIN_PROFILE_LABELS.get(normalize_margin_profile(profile), "RoboForex")


def margin_leverage_for_profile(
    symbol: str,
    *,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
) -> float:
    if isinstance(margin_profile, MarginModel):
        return margin_profile.leverage_for(symbol)
    profile = normalize_margin_profile(margin_profile)
    if profile == "ttp":
        return ttp_leverage_for(symbol)
    return stock_leverage if portfolio_group_key(symbol) == "Stocks" else default_leverage


def margin_contract_size_for_profile(
    symbol: str,
    *,
    margin_profile: str | MarginModel | None = "roboforex",
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    # Sigue siendo una aproximacion por grupo (acciones 100, resto 1). Solo se usa
    # cuando el perfil no aporta nocional medido; con ``symbol_notional`` presente
    # el margen no pasa por aqui.
    if isinstance(margin_profile, MarginModel):
        return margin_profile.contract_size_for(symbol)
    return stock_contract_size if portfolio_group_key(symbol) == "Stocks" else default_contract_size


def strategy_reference_price(strategy: RobustStrategySet) -> float:
    """Conservative price estimate from parsed MT5 closed trades."""
    prices: list[float] = []
    for report in (strategy.report_2020_2024, strategy.report_2025_2026):
        for trade in report.closed_trades:
            for price in (trade.open_price, trade.close_price):
                if price is not None and price > 0:
                    prices.append(float(price))
    if prices:
        return max(prices)
    return 1.0


def allocation_notional(
    strategy: RobustStrategySet,
    units: int,
    model: MarginModel,
) -> float:
    """Exposicion en divisa de cuenta de ``units`` unidades de una estrategia.

    Una unidad es una posicion al lote minimo ejecutable. Con nocional medido se
    multiplica directamente; si no, se recae en la estimacion antigua
    ``lote x contrato x precio`` con el precio maximo visto en los reportes.
    """
    measured = model.notional_for(strategy.symbol)
    if measured is not None:
        return units * measured
    return (
        model.lot_size_for(strategy.symbol, units)
        * model.contract_size_for(strategy.symbol)
        * strategy_reference_price(strategy)
    )
