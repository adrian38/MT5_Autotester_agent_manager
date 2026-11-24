"""Contract specifications and observed prices for the Prop simulation."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from math import ceil, gcd, isfinite, lcm

from portfolio_manager.ubs_portfolio import portfolio_symbol_key


@dataclass(frozen=True)
class VolumeSpec:
    contract: float
    minimum: float
    step: float


@dataclass(frozen=True)
class PropContract:
    symbol: str
    volume: VolumeSpec
    quote: str
    leverage: float
    base: str = ''


# Published by The5ers. Index units use multiples of the published increment.
FOREX_CURRENCIES = frozenset(('AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'JPY', 'NZD', 'USD'))
PROP_METALS = {'XAUUSD': 100, 'XAGUSD': 5000}
PROP_INDICES = {
    '.USTECHCASH': ('NAS100', 'USD', 0.1), 'USTECH': ('NAS100', 'USD', 0.1),
    '.US30CASH': ('US30', 'USD', 0.5),
    '.US500CASH': ('SP500', 'USD', 0.1), 'SP500': ('SP500', 'USD', 0.1),
    '.DE40CASH': ('DAX40', 'EUR', 0.5),
    'DAX40': ('DAX40', 'EUR', 0.5), 'UK100': ('UK100', 'GBP', 0.2),
    '.JP225CASH': ('JPN225', 'JPY', 2), 'JPN225': ('JPN225', 'JPY', 2),
}
# ECN contract cards for the exact report symbol. Do not assume the size of an
# index from a symbol alias or copy the The5ers contract over to this broker.
ROBOFOREX_ECN_INDICES = {
    '.US500CASH': 1, '.USTECHCASH': 1, '.US30CASH': 1, '.DE40CASH': 1, '.JP225CASH': 100,
}


def forex_pair(symbol: str) -> bool:
    return len(symbol) == 6 and symbol[:3] in FOREX_CURRENCIES and symbol[3:] in FOREX_CURRENCIES


def destination_contract(symbol: str) -> PropContract | None:
    key = portfolio_symbol_key(symbol)
    if forex_pair(key):
        return PropContract(key, VolumeSpec(100000, 0.01, 0.01), key[3:], 30, key[:3])
    if key in PROP_METALS:
        return PropContract(key, VolumeSpec(PROP_METALS[key], 0.01, 0.01), 'USD', 25)
    if key in PROP_INDICES:
        name, currency, step = PROP_INDICES[key]
        return PropContract(name, VolumeSpec(1, 0.01, step), currency, 25)
    return None


def source_volume_spec(symbol: str, specs: dict, broker: str, account: str) -> VolumeSpec | None:
    row = specs.get(symbol.upper(), {})
    try:
        measured = VolumeSpec(*(float(row.get(field) or 0) for field in (
            'contract_size', 'volume_min', 'volume_step',
        )))
    except (TypeError, ValueError):
        return None
    if all(isfinite(value) and value > 0 for value in (measured.contract, measured.minimum, measured.step)):
        return measured
    if broker == 'ROBOFOREX' and account == 'ECN':
        key = portfolio_symbol_key(symbol)
        contract = (100000 if forex_pair(key)
                    else PROP_METALS.get(symbol.upper()) or ROBOFOREX_ECN_INDICES.get(symbol.upper()))
        if contract:
            return VolumeSpec(contract, 0.01, 0.01)
    return None


def equivalent_volume_unit(source: VolumeSpec, target: VolumeSpec) -> tuple[float, float]:
    """Smallest common exposure that both terminals can execute in whole lot steps."""
    source_step = Fraction(str(source.step)) * Fraction(str(source.contract))
    target_step = Fraction(str(target.step)) * Fraction(str(target.contract))
    exposure = Fraction(lcm(source_step.numerator, target_step.numerator),
                        gcd(source_step.denominator, target_step.denominator))
    minimum = max(Fraction(str(source.minimum)) * Fraction(str(source.contract)),
                  Fraction(str(target.minimum)) * Fraction(str(target.contract)))
    exposure *= max(1, ceil(minimum / exposure))
    return float(exposure / Fraction(str(source.contract))), float(exposure / Fraction(str(target.contract)))


class ObservedEuroRates:
    """Last observed rate to EUR, using USD as a bridge when necessary."""

    def __init__(self, strategies, reference_trades=()):
        samples = defaultdict(dict)
        for item in strategies:
            for trade in item.closed_trades:
                self._observe_trade(samples, trade)
        for trade in reference_trades:
            self._observe_trade(samples, trade)
        self.samples = {
            currency: (tuple(sorted(values)), values) for currency, values in samples.items()
        }

    def _observe_trade(self, samples, trade):
        key = portfolio_symbol_key(trade.symbol)
        if forex_pair(key) and {'EUR', 'USD'}.intersection((key[:3], key[3:])):
            self._observe(samples, key, trade.open_time, trade.open_price)
            self._observe(samples, key, trade.close_time, trade.close_price)

    @staticmethod
    def _observe(samples, key: str, when: datetime | None, price: float | None) -> None:
        if when and price and isfinite(price) and price > 0:
            base, quote = key[:3], key[3:]
            for pair, rate in (((base, quote), price), ((quote, base), 1 / price)):
                values = samples[pair]
                values[when] = max(rate, values.get(when, 0))

    def _observed(self, source: str, target: str, when: datetime) -> float | None:
        stamps, values = self.samples.get((source, target), ((), {}))
        index = bisect_right(stamps, when) - 1
        if index < 0 or when - stamps[index] > timedelta(days=7):
            return None
        return values[stamps[index]]

    def at(self, currency: str, when: datetime) -> float | None:
        if currency == 'EUR':
            return 1.0
        direct = self._observed(currency, 'EUR', when)
        if direct is not None or currency == 'USD':
            return direct
        to_usd = self._observed(currency, 'USD', when)
        usd_to_euro = self._observed('USD', 'EUR', when)
        return to_usd * usd_to_euro if to_usd and usd_to_euro else None

    def missing_references(self, currencies: set[str]) -> set[str]:
        missing = {'USD'} if ('USD', 'EUR') not in self.samples else set()
        return missing | {
            currency for currency in currencies - {'EUR', 'USD'}
            if (currency, 'EUR') not in self.samples and (currency, 'USD') not in self.samples
        }

    def quote_rate(self, contract: PropContract, price: float, when: datetime) -> float | None:
        if contract.quote == 'EUR':
            return 1.0
        if contract.base in ('EUR', 'USD'):
            base_rate = self.at(contract.base, when)
            return base_rate / price if base_rate is not None else None
        return self.at(contract.quote, when)
