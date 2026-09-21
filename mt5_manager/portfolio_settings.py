"""Los ajustes del formulario: valores por defecto y validacion.

Encima de `portfolio_identity` y por debajo de todo lo que calcula. El orden de
los pasos de `normalize_settings` es el orden de los errores que ve el usuario,
asi que mover uno de sitio es un cambio de comportamiento.
"""
from __future__ import annotations

from typing import Any

from portfolio_manager.ubs_portfolio import (
    ACCOUNT_LEVERAGE_CHOICES,
    DEFAULT_ACCOUNT_LEVERAGE,
)

from .common import safe_float, safe_int
from .portfolio_identity import PORTFOLIO_TYPES
from .portfolio_scope import normalize_portfolio_scope


ASSET_GROUPS = ("Forex", "Metals", "Indices", "Energies", "Crypto", "Stocks", "Bonds", "Softs")

COMMON_DEFAULTS: dict[str, Any] = {
    "capital": 10000.0,
    "valley_dd_pct": 10.0,
    "point_dd_pct": 10.0,
    "portfolio_type": "balanced",
    "top_k_per_symbol": 3,
    "max_total_candidates": 30,
    "min_trades_2020_2026": 100,
    "max_units_per_set": None,
    "max_total_units": None,
    "max_units_per_symbol": None,
    "max_sets_per_symbol": 1,
    "run_local_search": True,
    "deep_optimization": True,
    "use_correlation": True,
    "require_3_positive_months_6m": False,
    "grid_off": False,
    "exclude_used_sets": True,
    "experimental_full_search": False,
    "min_strategy_recent_contribution_pct": 5.0,
    "dd_reserve_pct": 10.0,
    "search_restarts": 4,
    "max_pair_corr": 0.35,
    "max_downside_corr": 0.25,
    "max_dd_overlap": 0.35,
    "max_portfolio_corr": 0.50,
    "allowed_asset_groups": list(ASSET_GROUPS),
    "disabled_symbols": [],
    "margin_profile": "ictrading",
    "account_leverage": DEFAULT_ACCOUNT_LEVERAGE,
    "max_margin_pct": 100.0,
    "validate_margin": True,
    "enforce_point_dd": False,
}

MONTHLY_DEFAULTS: dict[str, Any] = {
    **COMMON_DEFAULTS,
    "portfolio_scope": "monthly",
    "target_month": 1,
    "min_trades_2020_2026": 15,
    "deep_optimization": False,
    "max_daily_dd": 150.0,
    "daily_dd_full_history": False,
    "exclude_monthly_used": False,
    "corr_with_monthly_portfolios": False,
    "strict_yearly_month_validation": False,
    "experimental_monthly_search": False,
}

def _optional_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    parsed = safe_int(value, -1)
    if parsed < 1:
        raise ValueError(f"{label} debe ser un entero mayor que 0")
    return parsed

def _optional_corr(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    parsed = safe_float(value, -1.0)
    if not 0 <= parsed <= 1:
        raise ValueError(f"{label} debe estar entre 0 y 1")
    return parsed

CORRELATION_KEYS = (
    "max_pair_corr",
    "max_downside_corr",
    "max_dd_overlap",
    "max_portfolio_corr",
)

BOOLEAN_SETTINGS = (
    "run_local_search", "deep_optimization", "use_correlation",
    "require_3_positive_months_6m", "grid_off", "exclude_used_sets",
    "experimental_full_search",
    "validate_margin", "daily_dd_full_history", "exclude_monthly_used",
    "corr_with_monthly_portfolios", "strict_yearly_month_validation",
    "experimental_monthly_search",
)

def _normalize_capital_and_type(values: dict[str, Any]) -> None:
    values["capital"] = safe_float(values.get("capital"), 0)
    values["valley_dd_pct"] = safe_float(values.get("valley_dd_pct"), 0)
    values["point_dd_pct"] = values["valley_dd_pct"]
    values["enforce_point_dd"] = False
    if values["capital"] <= 0 or values["valley_dd_pct"] <= 0:
        raise ValueError("Capital y DD valle deben ser mayores que 0")
    type_key = str(values.get("portfolio_type") or "balanced").strip().lower()
    if type_key not in PORTFOLIO_TYPES:
        raise ValueError("portfolio_type debe ser aggressive, balanced o conservative")
    values["portfolio_type"] = type_key

def _normalize_search_limits(values: dict[str, Any]) -> None:
    for key, minimum in (("top_k_per_symbol", 1), ("max_total_candidates", 1), ("min_trades_2020_2026", 0), ("max_sets_per_symbol", 1), ("search_restarts", 0)):
        values[key] = safe_int(values.get(key), -1)
        if values[key] < minimum:
            raise ValueError(f"{key} debe ser >= {minimum}")
    for key in ("max_units_per_set", "max_total_units", "max_units_per_symbol"):
        values[key] = _optional_int(values.get(key), key)

def _normalize_risk_budget(values: dict[str, Any], broker: str) -> None:
    values["dd_reserve_pct"] = safe_float(values.get("dd_reserve_pct"), -1)
    if not 0 <= values["dd_reserve_pct"] < 100:
        raise ValueError("dd_reserve_pct debe estar entre 0 y menos de 100")
    values["min_strategy_recent_contribution_pct"] = safe_float(
        values.get("min_strategy_recent_contribution_pct"), -1
    )
    if not 0 <= values["min_strategy_recent_contribution_pct"] <= 100:
        raise ValueError("min_strategy_recent_contribution_pct debe estar entre 0 y 100")
    values["max_margin_pct"] = safe_float(values.get("max_margin_pct"), 0)
    if values["max_margin_pct"] <= 0:
        raise ValueError("max_margin_pct debe ser mayor que 0")
    values["margin_profile"] = str(values.get("margin_profile") or broker).strip().lower()
    # Apalancamiento de cuenta: hoy solo lo consume el perfil AXI y solo en el
    # portafolio UBS (full history). Se guarda igualmente para cualquier perfil
    # para que la preferencia sobreviva a un cambio de broker en el formulario.
    leverage = safe_float(values.get("account_leverage"), 0)
    if leverage not in ACCOUNT_LEVERAGE_CHOICES:
        leverage = DEFAULT_ACCOUNT_LEVERAGE
    values["account_leverage"] = leverage

def _normalize_flags(values: dict[str, Any], monthly: bool) -> None:
    for key in BOOLEAN_SETTINGS:
        values[key] = bool(values.get(key))
    if monthly:
        values["experimental_full_search"] = False
    else:
        values["experimental_monthly_search"] = False
    # Disabling correlation must not erase the configured thresholds. The
    # optimizer already ignores them while use_correlation is false. Older
    # persisted settings may have lost all four values, so restore defaults
    # when correlation is enabled again.
    if values["use_correlation"] and all(
        values[key] is None for key in CORRELATION_KEYS
    ):
        for key in CORRELATION_KEYS:
            values[key] = COMMON_DEFAULTS[key]

def _normalized_disabled_symbols(raw_symbols: Any, key: str) -> list[str]:
    if raw_symbols is None:
        raw_symbols = []
    if not isinstance(raw_symbols, (list, tuple, set)):
        raise ValueError(f"{key} debe ser una lista de símbolos")
    normalized: dict[str, str] = {}
    for raw_symbol in raw_symbols:
        if not isinstance(raw_symbol, str):
            raise ValueError("Cada símbolo deshabilitado debe ser texto")
        symbol = raw_symbol.strip()
        if not symbol:
            continue
        if len(symbol) > 64:
            raise ValueError("Un símbolo deshabilitado no puede superar 64 caracteres")
        normalized.setdefault(symbol.casefold(), symbol)
    return sorted(normalized.values(), key=str.casefold)

def _normalize_symbol_filters(values: dict[str, Any], monthly: bool) -> None:
    groups = [str(value) for value in values.get("allowed_asset_groups") or [] if str(value) in ASSET_GROUPS]
    if not groups:
        raise ValueError("Selecciona al menos un grupo de activos")
    values["allowed_asset_groups"] = sorted(set(groups))
    generation_disabled = _normalized_disabled_symbols(
        values.get("disabled_symbols"), "disabled_symbols"
    )
    # El control pertenece exclusivamente a UBS normal. El mensual tiene su
    # propia interfaz y orquestación, y no debe heredar silenciosamente el filtro.
    values["disabled_symbols"] = [] if monthly else generation_disabled

def _normalize_monthly(values: dict[str, Any]) -> None:
    values["target_month"] = safe_int(values.get("target_month"), 0)
    if not 1 <= values["target_month"] <= 12:
        raise ValueError("target_month debe estar entre 1 y 12")
    values["max_daily_dd"] = safe_float(values.get("max_daily_dd"), 0)
    if values["max_daily_dd"] <= 0:
        raise ValueError("max_daily_dd debe ser mayor que 0")

def normalize_settings(scope: str, raw: dict[str, Any], broker: str = "ICTRADING") -> dict[str, Any]:
    """Valida y completa los ajustes. El orden de los pasos es el de los errores.

    Cada paso valida lo suyo y muta ``values``: mover uno de sitio cambiaria que
    mensaje ve el usuario cuando el formulario trae dos campos mal a la vez.
    """
    scope = normalize_portfolio_scope(scope)
    if scope == "grid":
        from .portfolio_grid_service import normalize_grid_settings

        return normalize_grid_settings(raw, broker)
    monthly = scope == "monthly"
    values = dict(MONTHLY_DEFAULTS if monthly else COMMON_DEFAULTS)
    values["margin_profile"] = str(broker or "ICTRADING").strip().lower()
    values.update(raw)
    values["portfolio_scope"] = "monthly" if monthly else "full_history"
    _normalize_capital_and_type(values)
    _normalize_search_limits(values)
    _normalize_risk_budget(values, broker)
    for key in CORRELATION_KEYS:
        values[key] = _optional_corr(values.get(key), key)
    _normalize_flags(values, monthly)
    _normalize_symbol_filters(values, monthly)
    if monthly:
        _normalize_monthly(values)
    return values
