from __future__ import annotations

import configparser
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now
from .mt5_native_history_report import NativeHistoryReportError, export_native_history_report

RUNNING_STATUSES = frozenset({
    "queued", "pausing", "extracting", "testing", "comparing", "finalizing", "resuming",
})
STATUS_LABELS = {
    "idle": "NO EJECUTADO", "queued": "EN COLA", "pausing": "PAUSANDO",
    "extracting": "EXTRAYENDO", "testing": "PROBANDO", "comparing": "COMPARANDO",
    "finalizing": "FINALIZANDO", "resuming": "REANUDANDO",
    "completed": "COMPLETADA", "not_comparable": "NO COMPARABLE",
    "failed": "FALLIDA",
}
PROGRESS = {
    "idle": ("idle", 0), "queued": ("preparing", 5), "pausing": ("preparing", 10),
    "extracting": ("extracting", 25), "testing": ("testing", 55),
    "comparing": ("comparing", 85), "finalizing": ("comparing", 95),
    "resuming": ("comparing", 98),
    "completed": ("completed", 100), "not_comparable": ("completed", 100),
    "failed": ("completed", 100),
}

# Pisos absolutos validados por el usuario. La tolerancia configurada en puntos
# sigue existiendo y puede ampliar estos límites, pero no reducirlos: un único
# número de puntos no representa la misma desviación económica en EURUSD,
# metales e índices con escalas de cotización distintas.
ADAPTIVE_PRICE_TOLERANCE_FLOORS = {
    "indices": 10.5,
    "nasdaq": 5.0,
    "nikkei": 5.0,
    "crypto_btc": 10.0,
    "gold": 2.05,
    "silver": 0.02,
    "jpy_fx": 0.05,
    "fx": 0.0005,
}
_INDEX_SYMBOL_PREFIXES = ("US30", "DE40", "USTEC", "USTECH")
_NIKKEI_SYMBOL_PREFIXES = ("JP225", "JPN225", "JP_225")
_FX_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"})

PORTFOLIO_MODES = ("aggressive", "balanced", "conservative")


def single_variant_mode(detail: dict[str, Any]) -> str:
    """Modo heredado por un portafolio guardado con una sola variante."""
    origin = detail.get("improvement_origin")
    candidates = (
        origin.get("mode") if isinstance(origin, dict) else None,
        detail.get("portfolio_type"),
    )
    for value in candidates:
        mode = str(value or "").strip().lower()
        if mode in PORTFOLIO_MODES:
            return mode
    return ""

def _as_int(value: Any, name: str, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser un entero") from exc
    if result < minimum:
        raise ValueError(f"{name} debe ser como mínimo {minimum}")
    return result


def _as_float(value: Any, name: str, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser numérico") from exc
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{name} está fuera del rango permitido")
    return result


def _normalize_real_strategy_lots(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("real_strategy_lots debe ser un objeto JSON")
    if len(value) > 500:
        raise ValueError("No se pueden configurar más de 500 lotes de estrategias")
    result: dict[str, float] = {}
    for raw_strategy, raw_lot in value.items():
        strategy = str(raw_strategy or "").strip()
        if not strategy or len(strategy) > 512 or "\n" in strategy or "\r" in strategy:
            raise ValueError("Cada lote real debe tener un identificador de estrategia válido")
        result[strategy] = _as_float(
            raw_lot, f"real_strategy_lots[{strategy}]", 0.00000001, 1_000_000.0,
        )
    return result


def _member_strategy_id(member: dict[str, Any], fallback: str = "") -> str:
    candidate_id = str(member.get("candidate_id") or "").strip()
    if candidate_id:
        return candidate_id
    source = str(member.get("set_id") or member.get("set_path") or "").strip()
    return Path(source).stem if source else fallback


def _adaptive_price_tolerance_floor(symbol: str) -> tuple[float | None, str]:
    """Devuelve el piso absoluto validado para la familia del instrumento."""
    root = re.split(
        r"[^A-Z0-9]", re.sub(r"^[^A-Z0-9]+", "", str(symbol or "").upper()), maxsplit=1,
    )[0]
    if root.startswith(("NAS100", "US100")):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["nasdaq"], "adaptive_nasdaq"
    if root.startswith(_NIKKEI_SYMBOL_PREFIXES):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["nikkei"], "adaptive_nikkei"
    if root.startswith("BTC"):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["crypto_btc"], "adaptive_crypto_btc"
    if root.startswith(_INDEX_SYMBOL_PREFIXES):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["indices"], "adaptive_indices"
    if root.startswith("XAU"):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["gold"], "adaptive_gold"
    if root.startswith("XAG"):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["silver"], "adaptive_silver"
    if len(root) >= 6 and root[:3] in _FX_CURRENCIES and root[3:6] in _FX_CURRENCIES:
        family = "jpy_fx" if root[3:6] == "JPY" else "fx"
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS[family], f"adaptive_{family}"
    return None, "configured_points"


def _expected_real_volume(
    expected: dict[str, Any], real_lots: dict[str, float],
) -> tuple[float, float]:
    """Volumen esperado en real y volumen que usó el tester."""
    tester_volume = float(expected.get("volume") or 0.0)
    configured = real_lots.get(str(expected.get("strategy") or ""))
    try:
        expected_volume = float(configured) if configured is not None else tester_volume
    except (TypeError, ValueError):
        expected_volume = tester_volume
    return (expected_volume if expected_volume > 0 else tester_volume), tester_volume


def _matching_open_position(
    expected: dict[str, Any], still_open: list[dict[str, Any]],
    unused: set[int], time_limit: float,
) -> tuple[int, dict[str, Any]] | None:
    """Devuelve una posición real abierta alineada y su índice consumible."""
    best: tuple[float, int, dict[str, Any]] | None = None
    for index in unused:
        position = still_open[index]
        if str(position.get("symbol") or "").casefold() != str(expected["symbol"]).casefold():
            continue
        if position.get("side") != expected["side"]:
            continue
        open_time = position.get("open_time")
        if not isinstance(open_time, datetime):
            continue
        delta = abs((open_time - expected["open_time"]).total_seconds())
        if delta <= time_limit and (best is None or delta < best[0]):
            best = (delta, index, {**position, "open_time_delta_seconds": round(delta, 3)})
    return (best[1], best[2]) if best else None


def _open_position_comparison(
    expected: dict[str, Any], actual: dict[str, Any], tester_index: int,
    open_index: int, point: float, request: dict[str, Any],
    real_lots: dict[str, float], data_issues: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Valida lo observable sin inventar cierre ni PnL para una real abierta."""
    strategy = str(expected["strategy"])
    price_limit, price_points, price_rule = _effective_price_tolerance(
        str(actual.get("symbol") or expected["symbol"]), point,
        request["price_tolerance_points"],
    )
    expected_volume, _tester_volume = _expected_real_volume(expected, real_lots)
    volume_limit = max(expected_volume, 1e-9) * request["volume_tolerance_pct"] / 100
    price_delta = abs(float(actual.get("open_price") or 0) - float(expected["open_price"]))
    volume_delta = abs(float(actual.get("volume") or 0) - expected_volume)
    reasons: list[str] = []
    epsilon = max(point * 1e-6, 1e-12)
    if price_limit is not None and price_delta > price_limit and not math.isclose(
        price_delta, price_limit, rel_tol=0.0, abs_tol=epsilon,
    ):
        reasons.append("open_price")
    if volume_delta > volume_limit:
        reasons.append("volume")
    return ({
        "tester_index": tester_index, "open_real_index": open_index + 1,
        "status": "deviation" if reasons else "open", "strategy": strategy,
        "tester": _trade_view(expected), "real": _trade_view(actual),
        "nearest_unused_real": None, "real_position_still_open": _trade_view(actual),
        "measurements": {
            "open_time_delta_seconds": round(float(actual["open_time_delta_seconds"]), 3),
            "open_price_delta": round(price_delta, 10),
            "open_price_delta_points": round(price_delta / point, 3) if point > 0 else None,
            "volume_delta": round(volume_delta, 8),
            "volume_delta_pct": round(volume_delta / max(abs(expected_volume), 1e-9) * 100, 3),
        },
        "limits": {
            "open_time_seconds": request["trade_time_tolerance_seconds"],
            "open_price_points": round(price_points, 3) if price_points is not None else None,
            "open_price_absolute": round(price_limit, 10) if price_limit is not None else None,
            "open_price_configured_points": request["price_tolerance_points"],
            "open_price_rule": price_rule, "volume_pct": request["volume_tolerance_pct"],
            "volume_absolute": round(volume_limit, 8),
            "volume_expected_real": round(expected_volume, 8),
            "volume_expected_source": "configured_real_lot" if strategy in real_lots else "tester_lot",
        },
        "data_issues": data_issues,
        "reasons": ["real_position_still_open_at_period_end", *reasons],
    }, reasons)


def _effective_price_tolerance(
    symbol: str, point: float, configured_points: float,
) -> tuple[float | None, float | None, str]:
    """Combina el límite manual en puntos con el piso de cada instrumento."""
    configured_absolute = configured_points * point if point > 0 else None
    adaptive_absolute, adaptive_rule = _adaptive_price_tolerance_floor(symbol)
    available = [value for value in (configured_absolute, adaptive_absolute) if value is not None]
    if not available:
        return None, None, "unavailable"
    absolute = max(available)
    effective_points = absolute / point if point > 0 else None
    rule = (
        adaptive_rule
        if adaptive_absolute is not None and adaptive_absolute >= (configured_absolute or 0.0)
        else "configured_points"
    )
    return absolute, effective_points, rule


def _pnl_comparison(
    actual_profit: float, tester_profit: float, warning_pct: float,
) -> dict[str, float | str | bool]:
    """Mide por separado la diferencia total y el deterioro contra el tester."""
    actual = float(actual_profit)
    expected = float(tester_profit)
    basis = max(abs(expected), 1.0)
    limit = basis * warning_pct / 100
    change = actual - expected
    delta = abs(change)
    adverse_delta = max(-change, 0.0)
    epsilon = max(abs(limit) * 1e-12, 1e-12)
    outside_tolerance = (
        adverse_delta > limit
        and not math.isclose(adverse_delta, limit, rel_tol=0.0, abs_tol=epsilon)
    )
    if math.isclose(change, 0.0, rel_tol=0.0, abs_tol=1e-12):
        direction = "equal"
    else:
        direction = "favorable" if change > 0 else "unfavorable"
    return {
        "delta": delta,
        "delta_pct": delta / basis * 100,
        "change": change,
        "change_pct": change / basis * 100,
        "adverse_delta": adverse_delta,
        "adverse_delta_pct": adverse_delta / basis * 100,
        "limit": limit,
        "direction": direction,
        "outside_tolerance": outside_tolerance,
    }


def _request_identity(value: dict[str, Any]) -> tuple[str, str]:
    audit_key = str(value.get("audit_key") or value.get("portfolio_id") or "").strip()
    if not audit_key or len(audit_key) > 120 or not all(char.isalnum() or char in "-_." for char in audit_key):
        raise ValueError("audit_key no es un identificador válido")
    portfolio_type = str(value.get("portfolio_type") or "").strip().lower()
    if portfolio_type not in {"aggressive", "balanced", "conservative"}:
        raise ValueError("portfolio_type debe ser aggressive, balanced o conservative")
    return audit_key, portfolio_type


def _request_period(value: dict[str, Any]) -> str:
    period_mode = str(value.get("period_mode") or "rolling_days").strip().lower()
    if period_mode not in {"rolling_days", "fixed_dates"}:
        raise ValueError("period_mode debe ser rolling_days o fixed_dates")
    period_dates: dict[str, date] = {}
    for key in ("period_start_date", "period_end_date"):
        raw = str(value.get(key) or "").strip()
        if not raw:
            continue
        try:
            period_dates[key] = date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{key} debe tener formato AAAA-MM-DD") from exc
    if period_mode == "fixed_dates":
        if set(period_dates) != {"period_start_date", "period_end_date"}:
            raise ValueError("El periodo por calendario requiere fecha desde y fecha hasta")
        if period_dates["period_start_date"] > period_dates["period_end_date"]:
            raise ValueError("La fecha desde no puede ser posterior a la fecha hasta")
        if (period_dates["period_end_date"] - period_dates["period_start_date"]).days > 3650:
            raise ValueError("El periodo por calendario no puede superar 3650 días")
    return period_mode


def _validate_request_accounts(result: dict[str, Any]) -> None:
    for key in ("source_login", "tester_login", "restore_login"):
        if not result[key].isdigit():
            raise ValueError(f"{key} debe contener solo números")
    for key in (
        "source_server", "tester_server", "restore_server",
        "source_password", "tester_password", "restore_password",
    ):
        if not result[key]:
            raise ValueError(f"Falta {key}")


def normalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    audit_key, portfolio_type = _request_identity(value)
    period_mode = _request_period(value)
    result = {
        "audit_key": audit_key,
        "portfolio_id": _as_int(value.get("portfolio_id"), "portfolio_id", 1),
        "portfolio_type": portfolio_type,
        "real_strategy_lots": _normalize_real_strategy_lots(value.get("real_strategy_lots")),
        "deployment_name": str(value.get("deployment_name") or "").strip()[:120],
        "source_login": str(value.get("source_login") or "").strip(),
        "source_server": str(value.get("source_server") or "").strip(),
        "source_password": str(value.get("source_password") or ""),
        "tester_login": str(value.get("tester_login") or "").strip(),
        "tester_server": str(value.get("tester_server") or "").strip(),
        "tester_password": str(value.get("tester_password") or ""),
        "restore_login": str(value.get("restore_login") or "").strip(),
        "restore_server": str(value.get("restore_server") or "").strip(),
        "restore_password": str(value.get("restore_password") or ""),
        "period_mode": period_mode,
        "period_days": _as_int(value.get("period_days"), "period_days", 1),
        "period_start_date": str(value.get("period_start_date") or "").strip(),
        "period_end_date": str(value.get("period_end_date") or "").strip(),
        "min_tick_history_quality_pct": _as_float(
            value.get("min_tick_history_quality_pct"), "min_tick_history_quality_pct", 0, 100
        ),
        "trade_time_tolerance_seconds": _as_int(
            value.get("trade_time_tolerance_seconds"), "trade_time_tolerance_seconds", 0
        ),
        "price_tolerance_points": _as_float(value.get("price_tolerance_points"), "price_tolerance_points"),
        "volume_tolerance_pct": _as_float(value.get("volume_tolerance_pct"), "volume_tolerance_pct", 0, 100),
        "pnl_deviation_warning_pct": _as_float(value.get("pnl_deviation_warning_pct"), "pnl_deviation_warning_pct"),
        "drawdown_deviation_warning_pct": _as_float(
            value.get("drawdown_deviation_warning_pct"), "drawdown_deviation_warning_pct"
        ),
        "execution_delay_mode": str(value.get("execution_delay_mode") or "measured"),
        "fixed_delay_ms": _as_int(value.get("fixed_delay_ms", 0), "fixed_delay_ms", 0),
    }
    _validate_request_accounts(result)
    return result


def _audit_period(request: dict[str, Any], now: datetime | None = None) -> tuple[datetime, datetime]:
    """Devuelve límites UTC inclusivos de días completos para extracción y tester."""
    if request.get("period_mode") == "fixed_dates":
        start_date = date.fromisoformat(str(request["period_start_date"]))
        end_date = date.fromisoformat(str(request["period_end_date"]))
    else:
        current_date = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
        start_date = current_date - timedelta(days=int(request["period_days"]))
        end_date = current_date
    return (
        datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc),
        datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc),
    )


def _metric_number(metrics: dict[str, str], *names: str) -> float | None:
    folded = {str(key).casefold(): str(value) for key, value in metrics.items()}
    for name in names:
        raw = folded.get(name.casefold())
        if not raw:
            continue
        match = re.search(r"-?\d+(?:[.,]\d+)?", raw.replace(" ", ""))
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                pass
    return None


def _drawdown(trades: list[dict[str, Any]]) -> float:
    equity = peak = maximum = 0.0
    for trade in sorted(trades, key=lambda row: row["close_time"]):
        equity += float(trade.get("profit") or 0)
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _trade_view(trade: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convierte una operación interna en un registro JSON auditable."""
    if trade is None:
        return None
    result: dict[str, Any] = {}
    for key in (
        "strategy", "symbol", "side", "open_time", "close_time",
        "open_price", "close_price", "volume", "profit",
    ):
        value = trade.get(key)
        result[key] = value.isoformat() if isinstance(value, datetime) else value
    for key in ("position_id", "ticket"):
        if trade.get(key) is not None:
            result[key] = trade[key]
    return result


def _redact_runner_output(text: str, *secrets: str) -> str:
    """El runner imprime el INI; nunca permitir contraseñas en artefactos o errores."""
    result = str(text or "")
    for secret in secrets:
        if secret:
            result = result.replace(str(secret), "[REDACTED]")
    return re.sub(r"(?mi)^(\s*Password\s*=).*$", r"\1[REDACTED]", result)


def _read_set_text(path: Path) -> tuple[str, str]:
    """Lee .set UTF-8/UTF-16 sin convertir sus parámetros en texto con NUL."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data[:4096].count(b"\x00") > max(8, len(data[:4096]) // 8):
        return data.decode("utf-16-le"), "utf-16"
    return data.decode("utf-8-sig", errors="replace"), "utf-8"


def _redact_log_files(directory: Path, *secrets: str) -> None:
    """Sanea también los logs propios de run_tests.py, no solo su stdout."""
    if not directory.is_dir():
        return
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in {".log", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            redacted = _redact_runner_output(text, *secrets)
            if redacted != text:
                path.write_text(redacted, encoding="utf-8")
        except OSError:
            continue


def _safe_state(raw: dict[str, Any]) -> dict[str, Any]:
    status = str(raw.get("status") or "idle")
    stage, progress = PROGRESS.get(status, ("idle", 0))
    return {
        "audit_key": str(raw.get("audit_key") or raw.get("portfolio_id") or ""),
        "portfolio_id": int(raw.get("portfolio_id") or 0),
        "portfolio_type": str(raw.get("portfolio_type") or ""),
        "audit_id": raw.get("audit_id"),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.upper()),
        "stage": stage,
        "progress_pct": progress,
        "progress_text": str(raw.get("progress_text") or "Aún no se ha ejecutado ninguna auditoría."),
        "started_at": raw.get("started_at"),
        "finished_at": raw.get("finished_at"),
        "can_run": status not in RUNNING_STATUSES,
        "log_lines": list(raw.get("log_lines") or [])[-500:],
        "last_result": raw.get("last_result"),
        "terminal_restore": raw.get("terminal_restore"),
        "error": raw.get("error"),
    }


__all__ = [name for name in globals() if not name.startswith('__')]
