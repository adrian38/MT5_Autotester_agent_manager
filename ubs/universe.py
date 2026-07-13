from __future__ import annotations

import configparser
from pathlib import Path


def load_asset_universe(
    path: Path,
    *,
    disabled_symbols: set[str] | None = None,
    include_disabled: bool = False,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Read the broker universe needed by the copied portfolio engine."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if path.exists():
        parser.read(path, encoding="utf-8-sig")

    groups: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    disabled = {symbol.upper() for symbol in (disabled_symbols or set())}
    for section in parser.sections():
        if section == "CommonAliases":
            aliases = {key.upper(): value.strip() for key, value in parser[section].items()}
            continue
        symbols = [item.strip() for item in parser[section].get("symbols", "").split(",") if item.strip()]
        if not include_disabled:
            symbols = [symbol for symbol in symbols if symbol.upper() not in disabled]
        groups[section] = symbols
    return groups, aliases
