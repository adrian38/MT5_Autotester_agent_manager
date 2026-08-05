"""Parsing and candidate filters for grid-enabled MT5 ``.set`` files.

The grid portfolio scope depends only on ``EnableGrid``. Internal expert
limits are deliberately not eligibility rules: portfolio risk is evaluated
from the resulting portfolio valley.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from ubs.path_utils import resolve_workspace_path


def _row_set_path(row: object) -> str:
    if isinstance(row, Mapping):
        return str(row.get("set_path") or "")
    return str(getattr(row, "set_path", "") or "")


def set_file_grid_enabled_value(set_path: str | Path) -> bool | None:
    """Read ``EnableGrid``; return ``None`` when absent or unreadable."""
    return _set_file_grid_enabled_value_cached(str(set_path))


@lru_cache(maxsize=32_768)
def _set_file_grid_enabled_value_cached(set_path: str) -> bool | None:
    path = resolve_workspace_path(set_path)
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() != "enablegrid":
            continue
        first_value = value.split("||", 1)[0].strip().lower()
        return first_value in {"true", "1", "yes", "y", "si", "sí"}
    return None


def set_file_has_enabled_grid(set_path: str | Path) -> bool:
    """Return ``True`` only for an explicit ``EnableGrid=true``."""
    return set_file_grid_enabled_value(set_path) is True


def _grid_flags(rows: Sequence[object]) -> tuple[list[str], dict[str, bool | None]]:
    paths = [_row_set_path(row) for row in rows]
    unique_paths = list(dict.fromkeys(path for path in paths if path))
    if not unique_paths:
        return paths, {}
    workers = min(16, len(unique_paths))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="grid-set") as executor:
        flags = dict(zip(unique_paths, executor.map(set_file_grid_enabled_value, unique_paths)))
    return paths, flags


def filter_rows_grid_off(rows: Sequence[object]) -> tuple[list[object], list[str]]:
    """Exclude only candidates whose set explicitly enables grid trading."""
    paths, flags = _grid_flags(rows)
    filtered = [row for row, path in zip(rows, paths) if flags.get(path) is not True]
    skipped = len(rows) - len(filtered)
    warnings = [f"Grid OFF: {skipped} candidato(s) omitido(s) por EnableGrid=true."] if skipped else []
    return filtered, warnings


def filter_rows_grid_on(rows: Sequence[object]) -> tuple[list[object], list[str]]:
    """Keep only candidates whose set explicitly contains ``EnableGrid=true``."""
    paths, flags = _grid_flags(rows)
    filtered = [row for row, path in zip(rows, paths) if flags.get(path) is True]
    disabled = sum(1 for path in paths if path and flags.get(path) is False)
    unknown = len(rows) - len(filtered) - disabled
    warnings: list[str] = []
    if disabled:
        warnings.append(f"Grid UBS: {disabled} candidato(s) omitido(s) por EnableGrid=false.")
    if unknown:
        warnings.append(
            f"Grid UBS: {unknown} candidato(s) omitido(s) porque EnableGrid no existe o el .set no es legible."
        )
    return filtered, warnings
