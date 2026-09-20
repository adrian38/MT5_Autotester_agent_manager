"""Primitivas de lectura: filas de la base, rutas, fechas y lotes."""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re


def execution_units_from_step(capital: float, lot_size_step: float | int | None) -> int:
    if lot_size_step is None:
        return 0
    step_int = max(1, int(math.ceil(float(lot_size_step))))
    return int(math.floor(capital / step_int)) if capital > 0 else 0


def _parse_report_date(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_month_end(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value:
        return _parse_report_date(str(value))
    return None


def _latest_month_from_monthly(monthly: dict[int, dict[int, float]]) -> datetime | None:
    pairs = [
        (int(year), int(month))
        for year, months in monthly.items()
        for month in months
    ]
    if not pairs:
        return None
    year, month = max(pairs)
    return datetime(year, month, 1)


def _month_window(end_year: int, end_month: int, window_months: int) -> list[tuple[int, int]]:
    end_index = end_year * 12 + end_month - 1
    start_index = end_index - window_months + 1
    result: list[tuple[int, int]] = []
    for month_index in range(start_index, end_index + 1):
        year = month_index // 12
        month = month_index % 12 + 1
        result.append((year, month))
    return result


def _first_existing_report_path(row: object, *keys: str) -> Path | None:
    for key in keys:
        value = str(_row_value(row, key, default="") or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return path
    return None


def _to_float(value: str) -> float:
    text = str(value or "").split("(")[0].strip()
    text = text.replace(" ", "").replace("%", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def _logical_stem(set_path: str) -> str:
    stem = Path(set_path).stem
    return re.sub(r"^robust_\d{6}_", "", stem)


def _norm_path(value: str) -> str:
    try:
        return str(Path(value)).casefold()
    except (TypeError, ValueError):
        return str(value or "").casefold()


def _row_value(row: object, *keys: str, default: object = "") -> object:
    row_keys: set[str] | None = None
    try:
        row_keys = {str(key) for key in row.keys()}  # type: ignore[attr-defined]
    except Exception:
        row_keys = None
    for key in keys:
        if row_keys is not None and key not in row_keys:
            continue
        try:
            return row[key]  # type: ignore[index]
        except Exception:
            pass
        try:
            return getattr(row, key)
        except Exception:
            pass
    return default


def _row_int(row: object, *keys: str) -> int:
    try:
        return int(_row_value(row, *keys, default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _lot_size_step(capital: float, units: int) -> float | None:
    if units <= 0:
        return None
    return float(_step_for_max_units(capital, units))


def _step_for_max_units(capital: float, units: int) -> int:
    if capital <= 0 or units <= 0:
        return 1
    return max(1, int(math.floor(capital / (units + 1))) + 1)
