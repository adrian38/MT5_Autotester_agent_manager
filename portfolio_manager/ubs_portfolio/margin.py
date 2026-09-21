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
from .margin_profiles import (
    MARGIN_PROFILE_LABELS,
    MARGIN_PROFILES,
    margin_profile_label,
    margin_leverage_for_profile,
    margin_contract_size_for_profile,
    strategy_reference_price,
    allocation_notional,
)
from .margin_summary import (
    allocation_margin_required,
    portfolio_margin_summary,
    allocations_respect_margin_limit,
)
