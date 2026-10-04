from __future__ import annotations

import math
from datetime import date
from typing import Any


DEFAULT_LIVE_AUDIT_PROFILE: dict[str, Any] = {
    "portfolio_id": 0,
    "portfolio_type": "",
    "real_strategy_lots": {},
    "deployment_name": "",
    "source_login": "",
    "source_server": "",
    "tester_login": "",
    "tester_server": "",
    "active_job_policy": "pause_resume",
    "period_mode": "rolling_days",
    "period_days": 7,
    "period_start_date": "",
    "period_end_date": "",
    "audit_interval_days": 1,
    "tester_model": "real_ticks",
    "min_tick_history_quality_pct": 80.0,
    "execution_delay_mode": "measured",
    "fixed_delay_ms": 0,
    "trade_time_tolerance_seconds": 120,
    "price_tolerance_points": 15.0,
    "volume_tolerance_pct": 1.0,
    "pnl_deviation_warning_pct": 10.0,
    "drawdown_deviation_warning_pct": 15.0,
}

DEFAULT_TERMINAL_RESTORE_ACCOUNT: dict[str, str] = {
    "login": "11637157",
    "server": "CapitalPointTrading-MT5-4",
}

# Alias conservado para consumidores de la primera versión del MVP.
DEFAULT_LIVE_AUDIT_SETTINGS = DEFAULT_LIVE_AUDIT_PROFILE

_TEXT_LIMITS = {
    "portfolio_type": 32,
    "deployment_name": 120,
    "source_login": 32,
    "source_server": 160,
    "tester_login": 32,
    "tester_server": 160,
    "period_start_date": 10,
    "period_end_date": 10,
}
_INT_LIMITS = {
    "period_days": (1, 3650),
    "audit_interval_days": (1, 3650),
    "fixed_delay_ms": (0, 600_000),
    "trade_time_tolerance_seconds": (0, 86_400),
}
_FLOAT_LIMITS = {
    "min_tick_history_quality_pct": (0.0, 100.0),
    "price_tolerance_points": (0.0, 1_000_000.0),
    "volume_tolerance_pct": (0.0, 100.0),
    "pnl_deviation_warning_pct": (0.0, 10_000.0),
    "drawdown_deviation_warning_pct": (0.0, 10_000.0),
}
_LEGACY_SCHEDULE_KEYS = {
    "sync_interval_minutes",
    "daily_audit_time",
    "heartbeat_timeout_minutes",
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


def _real_strategy_lots(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("real_strategy_lots debe ser un objeto JSON")
    if len(value) > 500:
        raise ValueError("No se pueden configurar más de 500 lotes de estrategias")
    result: dict[str, float] = {}
    for raw_strategy, raw_lot in value.items():
        strategy = _text(raw_strategy, "identificador de estrategia", 512)
        if not strategy:
            raise ValueError("Cada lote real debe tener un identificador de estrategia")
        result[strategy] = _number(
            raw_lot, f"real_strategy_lots[{strategy}]", 0.00000001, 1_000_000.0,
        )
    return result


def _portfolio_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("selected_portfolio_ids debe ser una lista")
    if len(value) > 100:
        raise ValueError("No se pueden seleccionar más de 100 portafolios")
    result: list[int] = []
    for raw in value:
        portfolio_id = _integer(raw, "selected_portfolio_ids", 1, 2_147_483_647)
        if portfolio_id not in result:
            result.append(portfolio_id)
    return result


def _audit_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("selected_audit_ids debe ser una lista")
    if len(value) > 100:
        raise ValueError("No se pueden configurar más de 100 usos de portafolio")
    result: list[str] = []
    for raw in value:
        audit_id = str(raw or "").strip()
        if not audit_id or len(audit_id) > 120 or not all(char.isalnum() or char in "-_." for char in audit_id):
            raise ValueError("Cada selected_audit_id debe ser un identificador seguro de hasta 120 caracteres")
        if audit_id not in result:
            result.append(audit_id)
    return result


def normalize_terminal_restore_account(value: dict[str, Any]) -> dict[str, str]:
    """Valida la cuenta independiente que debe quedar activa en cada terminal usado."""
    if not isinstance(value, dict):
        raise ValueError("La cuenta de restauración debe ser un objeto JSON")
    unknown = set(value) - {"login", "server"}
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = dict(DEFAULT_TERMINAL_RESTORE_ACCOUNT)
    if "login" in value:
        normalized["login"] = _text(value["login"], "login de restauración", 32)
    if "server" in value:
        normalized["server"] = _text(value["server"], "servidor de restauración", 160)
    if not normalized["login"] or not normalized["login"].isdigit():
        raise ValueError("El login de restauración debe contener solo dígitos")
    if not normalized["server"]:
        raise ValueError("Falta el servidor de restauración")
    return normalized


def _normalize_scalar_fields(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_LIVE_AUDIT_PROFILE)
    if "portfolio_id" in value:
        normalized["portfolio_id"] = _integer(value["portfolio_id"], "portfolio_id", 0, 2_147_483_647)
    for key, maximum in _TEXT_LIMITS.items():
        if key in value:
            normalized[key] = _text(value[key], key, maximum)
    for key, (minimum, maximum) in _INT_LIMITS.items():
        if key in value:
            normalized[key] = _integer(value[key], key, minimum, maximum)
    for key, (minimum, maximum) in _FLOAT_LIMITS.items():
        if key in value:
            normalized[key] = _number(value[key], key, minimum, maximum)
    if "real_strategy_lots" in value:
        normalized["real_strategy_lots"] = _real_strategy_lots(value["real_strategy_lots"])
    return normalized


def _apply_legacy_tolerances(normalized: dict[str, Any], legacy_period_contract: bool) -> None:
    if legacy_period_contract and normalized["trade_time_tolerance_seconds"] == 60:
        normalized["trade_time_tolerance_seconds"] = 120
    if legacy_period_contract and normalized["price_tolerance_points"] == 10.0:
        normalized["price_tolerance_points"] = 15.0


def _normalize_enum_fields(value: dict[str, Any], normalized: dict[str, Any]) -> None:
    if "tester_model" in value:
        tester_model = _text(value["tester_model"], "tester_model", 32).lower()
        if tester_model != "real_ticks":
            raise ValueError("tester_model debe ser real_ticks en este MVP")
        normalized["tester_model"] = tester_model
    if "period_mode" in value:
        period_mode = _text(value["period_mode"], "period_mode", 32).lower()
        if period_mode not in {"rolling_days", "fixed_dates"}:
            raise ValueError("period_mode debe ser rolling_days o fixed_dates")
        normalized["period_mode"] = period_mode
    if normalized["portfolio_type"]:
        normalized["portfolio_type"] = normalized["portfolio_type"].lower()
        if normalized["portfolio_type"] not in {"aggressive", "balanced", "conservative"}:
            raise ValueError("portfolio_type debe ser aggressive, balanced o conservative")
    if "execution_delay_mode" in value:
        delay_mode = _text(value["execution_delay_mode"], "execution_delay_mode", 32).lower()
        if delay_mode not in {"none", "measured", "fixed"}:
            raise ValueError("execution_delay_mode debe ser none, measured o fixed")
        normalized["execution_delay_mode"] = delay_mode


def _validate_period(normalized: dict[str, Any]) -> None:
    parsed_dates: dict[str, date] = {}
    for key in ("period_start_date", "period_end_date"):
        raw = normalized[key]
        if not raw:
            continue
        try:
            parsed_dates[key] = date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{key} debe tener formato AAAA-MM-DD") from exc
    if normalized["period_mode"] != "fixed_dates":
        return
    if set(parsed_dates) != {"period_start_date", "period_end_date"}:
        raise ValueError("El periodo por calendario requiere fecha desde y fecha hasta")
    if parsed_dates["period_start_date"] > parsed_dates["period_end_date"]:
        raise ValueError("La fecha desde no puede ser posterior a la fecha hasta")
    if (parsed_dates["period_end_date"] - parsed_dates["period_start_date"]).days > 3650:
        raise ValueError("El periodo por calendario no puede superar 3650 días")


def _validate_fixed_policy_and_logins(value: dict[str, Any], normalized: dict[str, Any]) -> None:
    if "active_job_policy" in value and value["active_job_policy"] != "pause_resume":
        raise ValueError("active_job_policy debe ser pause_resume")
    normalized["active_job_policy"] = "pause_resume"
    for key in ("source_login", "tester_login"):
        login = normalized[key]
        if login and not login.isdigit():
            raise ValueError(f"{key} debe contener solo dígitos")


def normalize_live_audit_settings(value: dict[str, Any]) -> dict[str, Any]:
    """Normaliza el perfil independiente de un portafolio."""
    if not isinstance(value, dict):
        raise ValueError("La configuración del portafolio debe ser un objeto JSON")
    legacy_period_contract = "period_mode" not in value
    value = {key: item for key, item in value.items() if key not in _LEGACY_SCHEDULE_KEYS}
    unknown = set(value) - set(DEFAULT_LIVE_AUDIT_PROFILE)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    normalized = _normalize_scalar_fields(value)
    _apply_legacy_tolerances(normalized, legacy_period_contract)
    _normalize_enum_fields(value, normalized)
    _validate_period(normalized)
    _validate_fixed_policy_and_logins(value, normalized)
    return normalized


def _require_complete_profile(audit_id: str, profile: dict[str, Any]) -> None:
    portfolio_id = int(profile.get("portfolio_id") or 0)
    if not portfolio_id:
        raise ValueError(f"Falta el portafolio del uso {audit_id}")
    if not profile.get("portfolio_type"):
        raise ValueError(f"Selecciona Agresivo, Moderado o Conservador para el portafolio #{portfolio_id}")
    for key, label in (
        ("source_login", "login de la cuenta real"),
        ("source_server", "servidor de la cuenta real"),
        ("tester_login", "login de la cuenta de pruebas"),
        ("tester_server", "servidor de la cuenta de pruebas"),
    ):
        if not profile[key]:
            raise ValueError(f"Falta el {label} del portafolio #{portfolio_id} ({audit_id})")


def _public_legacy_profile(raw: dict[str, Any]) -> dict[str, Any]:
    legacy = dict(raw)
    legacy["source_login"] = legacy.get("source_login") or legacy.get("account_login") or ""
    legacy["source_server"] = legacy.get("source_server") or legacy.get("account_server") or ""
    for key in ("enabled", "selected_portfolio_ids", "account_login", "account_server", "terminal_path"):
        legacy.pop(key, None)
    return normalize_live_audit_settings(legacy)
