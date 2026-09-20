"""Nombres de simbolo y grupo de activo, y los mapas del universo."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Iterable
import unicodedata

from ubs.path_utils import resolve_workspace_path
from ubs.universe import load_asset_universe


PORTFOLIO_SYMBOL_ALIASES = {
    "US30": ".US30CASH",
    ".US30CASH": ".US30CASH",
    "US500": ".US500CASH",
    ".US500CASH": ".US500CASH",
    "USTEC": ".USTECHCASH",
    "US100": ".USTECHCASH",
    "NAS100": ".USTECHCASH",
    ".USTECHCASH": ".USTECHCASH",
    "DAX": ".DE40CASH",
    "DE40": ".DE40CASH",
    "GER40": ".DE40CASH",
    ".DE40CASH": ".DE40CASH",
    "XTIUSD": "WTI",
    "USOIL": "WTI",
    "CRUDEOIL": "WTI",
    "WTI": "WTI",
}


PORTFOLIO_GROUP_BY_SYMBOL = {
    **{
        symbol: "Forex"
        for symbol in (
            "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "CHFJPY",
            "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "EURAUD", "EURCAD",
            "EURCHF", "EURGBP", "EURJPY", "EURNZD", "EURUSD", "NZDCAD", "NZDCHF", "NZDJPY",
            "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
        )
    },
    **{symbol: "Metals" for symbol in ("XAGUSD", "XAUUSD", "XAUEUR")},
    **{
        symbol: "Indices"
        for symbol in (".DE40CASH", ".JP225CASH", ".US500CASH", ".USTECHCASH", ".US30CASH")
    },
    **{symbol: "Energies" for symbol in ("BRENT", "WTI")},
    **{symbol: "Crypto" for symbol in ("BTCUSD", "ETHUSD")},
    **{
        symbol: "Stocks"
        for symbol in (
            "GOOGL", "MSFT", "IBM", "VZ", "INTC", "LLY", "HPE", "PFE", "JNJ", "EA", "BA",
            "ORCL", "NVDA", "CAT", "CSCO", "MMM", "ADBE", "GE", "TSLA", "NKE", "CMCSA",
            "GM", "DIS", "PM", "PG", "PEP", "FOXA", "KO", "AAPL", "AMZN", "UPS", "NFLX",
            "BRK.B", "MCD", "PRU", "SBUX", "PYPL", "GS", "WMT", "V", "DAL", "WFC", "C",
            "XOM", "CVX", "NEM", "JPM", "BAC", "EBAY", "META",
        )
    },
}


PORTFOLIO_UNIVERSE_FILES = (
    "assets/roboforex_assets.ini",
    "assets/axi_assets.ini",
    "assets/ictrading_assets.ini",
)


def _normalized_universe_group(group: str, symbol_key: str) -> str:
    if group == "Commodities":
        return "Softs"
    if group != "IndicesEnergies":
        return group
    energy_tokens = ("BRENT", "WTI", "OIL", "XTI", "XBR", "XNG", "GAS")
    return "Energies" if any(token in symbol_key for token in energy_tokens) else "Indices"


def _portfolio_universe_files_key(universe_files: Iterable[str | Path] | None = None) -> tuple[str, ...]:
    files = universe_files if universe_files is not None else PORTFOLIO_UNIVERSE_FILES
    return tuple(str(path) for path in files)


@lru_cache(maxsize=16)
def _portfolio_universe_group_maps_for_files(
    universe_files: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the portfolio classifier from the broker universe files."""
    canonical: dict[str, str] = {}
    exact: dict[str, str] = {}
    alias_rows: list[tuple[str, str]] = []
    for relative_path in universe_files:
        groups, aliases = load_asset_universe(
            resolve_workspace_path(relative_path),
            include_disabled=True,
        )
        for group, symbols in groups.items():
            for symbol in symbols:
                raw_key = str(symbol).strip().upper()
                symbol_key = portfolio_symbol_key(symbol)
                normalized_group = _normalized_universe_group(group, symbol_key)
                exact[raw_key] = normalized_group
                if group == "Stocks" and "." in raw_key:
                    canonical.setdefault(symbol_key, normalized_group)
                else:
                    canonical[symbol_key] = normalized_group
        alias_rows.extend(aliases.items())
    for alias, target in alias_rows:
        target_group = exact.get(str(target).strip().upper()) or canonical.get(portfolio_symbol_key(target))
        if target_group:
            exact[str(alias).strip().upper()] = target_group
            canonical[portfolio_symbol_key(alias)] = target_group
    return canonical, exact


def _portfolio_universe_group_maps(
    universe_files: Iterable[str | Path] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    return _portfolio_universe_group_maps_for_files(_portfolio_universe_files_key(universe_files))


def _portfolio_universe_symbol_base(symbol: str) -> str:
    value = str(symbol or "").strip()
    if value.endswith("+"):
        value = value[:-1]
    else:
        value = re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", value)
    return value.upper()


@lru_cache(maxsize=32)
def _portfolio_universe_display_maps_for_files(
    universe_files: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Map legacy logical symbols to the executable spelling in a broker universe."""
    exact: dict[str, str] = {}
    by_base: dict[str, str] = {}
    alias_rows: list[tuple[str, str]] = []
    for relative_path in universe_files:
        groups, aliases = load_asset_universe(
            resolve_workspace_path(relative_path),
            include_disabled=True,
        )
        for symbols in groups.values():
            for symbol in symbols:
                display = str(symbol).strip()
                if not display:
                    continue
                exact[display.upper()] = display
                by_base.setdefault(_portfolio_universe_symbol_base(display), display)
        alias_rows.extend(aliases.items())
    for alias, target in alias_rows:
        display = exact.get(str(target).strip().upper()) or by_base.get(
            _portfolio_universe_symbol_base(target)
        )
        if display:
            exact[str(alias).strip().upper()] = display
            by_base.setdefault(_portfolio_universe_symbol_base(alias), display)
    return exact, by_base


def _portfolio_universe_display_symbol(
    symbol: str,
    universe_files: Iterable[str | Path],
) -> str:
    display = str(symbol or "").strip()
    if not display:
        return display
    exact, by_base = _portfolio_universe_display_maps_for_files(
        _portfolio_universe_files_key(universe_files)
    )
    return exact.get(display.upper()) or by_base.get(
        _portfolio_universe_symbol_base(display)
    ) or display


def _portfolio_universe_group_by_symbol(
    universe_files: Iterable[str | Path] | None = None,
) -> dict[str, str]:
    return _portfolio_universe_group_maps(universe_files)[0]


def _ascii_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_symbol(symbol: str) -> str:
    value = (symbol or "").strip()
    if value.startswith("."):
        return value.upper()
    return re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", value).upper()


def portfolio_symbol_key(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    return PORTFOLIO_SYMBOL_ALIASES.get(normalized, normalized)


def portfolio_display_symbol(
    symbol: str,
    *,
    universe_files: Iterable[str | Path] | None = None,
) -> str:
    display = str(symbol or "").strip()
    if universe_files is not None:
        display = _portfolio_universe_display_symbol(display, universe_files)
    return display or portfolio_symbol_key(symbol)


def portfolio_group_key(
    symbol: str,
    *,
    universe_files: Iterable[str | Path] | None = None,
) -> str:
    raw_symbol = str(symbol or "").strip().upper()
    exact_group = _portfolio_universe_group_maps(universe_files)[1].get(raw_symbol)
    if exact_group:
        return exact_group
    symbol_key = portfolio_symbol_key(symbol)
    universe_group = _portfolio_universe_group_by_symbol(universe_files).get(symbol_key)
    if universe_group:
        return universe_group
    if symbol_key in PORTFOLIO_GROUP_BY_SYMBOL:
        return PORTFOLIO_GROUP_BY_SYMBOL[symbol_key]
    if _looks_like_forex_pair(symbol_key):
        return "Forex"
    return "Other"


def _looks_like_forex_pair(symbol: str) -> bool:
    currencies = {"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"}
    return len(symbol) == 6 and symbol[:3] in currencies and symbol[3:] in currencies
