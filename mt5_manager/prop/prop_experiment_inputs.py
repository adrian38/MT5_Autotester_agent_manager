"""Assemble the current executable Prop pool with optional evidence reuse."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .prop_execution import ObservedEuroRates
from .prop_experiment_engine import credible_pool, set_path_is_grid
from .prop_experiment_periods import PHASE_ONE_FIRST_DAY
from .prop_experiment_pool import load_fx_reference, load_pool


def load_executable(settings, source_factory, checkpoint, active_members, symbol_settings, *, cache=None):
    parse = cache.read_period if cache else None
    pool, axis, warnings, origins = load_pool(
        settings, source_factory, checkpoint, active_members, symbol_settings, parse=parse,
    )
    if not pool or not axis.size:
        raise ValueError("El pool no tiene días operables en la ventana elegida")
    executable, credibility_warnings = credible_pool(
        pool, bool(settings["include_grids"]), is_grid=cache.is_grid if cache else set_path_is_grid,
    )
    if not executable:
        raise ValueError("El pool no contiene estrategias con riesgo cerrado medible")
    executable_keys = {item.lab.key for item in executable}
    rejected = Counter((item.lab.origin_node, item.source_kind, 'Grid deshabilitado')
                       for item in pool if item.lab.key not in executable_keys)
    rates = ObservedEuroRates(pool)
    required = {item.prop_contract.quote for item in executable if item.prop_contract}
    required.update(item.prop_contract.base for item in executable
                    if item.prop_contract and item.prop_contract.base == 'USD')
    required.update(trade.currency for item in executable for trade in item.closed_trades if trade.currency)
    missing = rates.missing_references(required)
    first_day = datetime.fromisoformat(PHASE_ONE_FIRST_DAY)
    preceding = {currency for currency in required if not rates.at(currency, first_day)} if not settings['historical'] else set()
    if missing or preceding:
        reference = load_fx_reference(
            source_factory, settings, checkpoint, missing, preceding=preceding, parse=parse,
        )
        rates = ObservedEuroRates(pool, reference)
    return executable, axis, warnings + credibility_warnings, origins, rates, rejected
