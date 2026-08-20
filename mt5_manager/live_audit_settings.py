from __future__ import annotations

import math
import re
import threading
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now


DEFAULT_LIVE_AUDIT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "deployment_name": "",
    "account_login": "",
    "account_server": "",
    "terminal_path": "",
    "period_days": 7,
    "sync_interval_minutes": 5,
    "daily_audit_time": "00:30",
    "heartbeat_timeout_minutes": 5,
    "tester_model": "real_ticks",
    "execution_delay_mode": "measured",
    "fixed_delay_ms": 0,
    "trade_time_tolerance_seconds": 60,
    "price_tolerance_points": 10.0,
    "volume_tolerance_pct": 1.0,
    "pnl_deviation_warning_pct": 10.0,
    "drawdown_deviation_warning_pct": 15.0,
}

_BOOL_KEYS = {"enabled"}
_TEXT_LIMITS = {
    "deployment_name": 120,
    "account_login": 32,
    "account_server": 160,
    "terminal_path": 1000,
}
_INT_LIMITS = {
    "period_days": (1, 3650),
    "sync_interval_minutes": (1, 1440),
    "heartbeat_timeout_minutes": (1, 1440),
    "fixed_delay_ms": (0, 600_000),
    "trade_time_tolerance_seconds": (0, 86_400),
}
_FLOAT_LIMITS = {
    "price_tolerance_points": (0.0, 1_000_000.0),
    "volume_tolerance_pct": (0.0, 100.0),
    "pnl_deviation_warning_pct": (0.0, 10_000.0),
    "drawdown_deviation_warning_pct": (0.0, 10_000.0),
}


def _text(value: Any, key: str, maximum: int) -> str:
    result = str(value or "").strip()
    if "\n" in result or "\r" in result:
        raise ValueError(f"{key} no puede contener saltos de línea")
    if len(result) > maximum:
        raise ValueError(f"{key} no puede superar {maximum} caracteres")
    return result


def _integer(value: Any, key: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} debe ser un entero")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} debe ser un entero") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"{key} debe estar entre {minimum} y {maximum}")
    return result


def _number(value: Any, key: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{key} debe ser numérico")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} debe ser numérico") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{key} debe estar entre {minimum:g} y {maximum:g}")
    return result


def normalize_live_audit_settings(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("La configuración del auditor debe ser un objeto JSON")
    unknown = set(value) - set(DEFAULT_LIVE_AUDIT_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")

    normalized = dict(DEFAULT_LIVE_AUDIT_SETTINGS)
    for key in _BOOL_KEYS:
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"{key} debe ser booleano")
            normalized[key] = value[key]
    for key, maximum in _TEXT_LIMITS.items():
        if key in value:
            normalized[key] = _text(value[key], key, maximum)
    for key, (minimum, maximum) in _INT_LIMITS.items():
        if key in value:
            normalized[key] = _integer(value[key], key, minimum, maximum)
    for key, (minimum, maximum) in _FLOAT_LIMITS.items():
        if key in value:
            normalized[key] = _number(value[key], key, minimum, maximum)

    if "daily_audit_time" in value:
        audit_time = _text(value["daily_audit_time"], "daily_audit_time", 5)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", audit_time):
            raise ValueError("daily_audit_time debe tener formato HH:MM")
        normalized["daily_audit_time"] = audit_time

    if "tester_model" in value:
        tester_model = _text(value["tester_model"], "tester_model", 32).lower()
        if tester_model != "real_ticks":
            raise ValueError("tester_model debe ser real_ticks en este MVP")
        normalized["tester_model"] = tester_model

    if "execution_delay_mode" in value:
        delay_mode = _text(value["execution_delay_mode"], "execution_delay_mode", 32).lower()
        if delay_mode not in {"none", "measured", "fixed"}:
            raise ValueError("execution_delay_mode debe ser none, measured o fixed")
        normalized["execution_delay_mode"] = delay_mode

    login = normalized["account_login"]
    if login and not login.isdigit():
        raise ValueError("account_login debe contener solo dígitos")
    if normalized["enabled"]:
        if not login:
            raise ValueError("Falta el login de la cuenta para habilitar el auditor")
        if not normalized["account_server"]:
            raise ValueError("Falta el servidor de la cuenta para habilitar el auditor")
    return normalized


class LiveAuditSettingsStore:
    """Configuración del futuro auditor, guardada únicamente en el manager."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            stored = load_json(self.path)
            for node_id, record in stored.items():
                if not isinstance(record, dict):
                    continue
                raw_settings = record.get("settings")
                if not isinstance(raw_settings, dict):
                    continue
                try:
                    settings = normalize_live_audit_settings(raw_settings)
                except ValueError:
                    continue
                self.records[str(node_id)] = {
                    "settings": settings,
                    "updated_at": str(record.get("updated_at") or ""),
                }

    def state(self, node_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.records.get(node_id) or {}
            settings = dict(record.get("settings") or DEFAULT_LIVE_AUDIT_SETTINGS)
            return {
                "settings": settings,
                "defaults": dict(DEFAULT_LIVE_AUDIT_SETTINGS),
                "updated_at": record.get("updated_at") or None,
                "configured": bool(settings.get("account_login") and settings.get("account_server")),
                "phase": "configuration_only",
            }

    def update(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            current = self.state(node_id)["settings"]
            current.update(changes)
            normalized = normalize_live_audit_settings(current)
            self.records[node_id] = {"settings": normalized, "updated_at": utc_now()}
            save_json(self.path, self.records)
            return self.state(node_id)
