from __future__ import annotations

import math
import os
import re
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .common import load_json, save_json, utc_now


DEFAULT_LIVE_AUDIT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "selected_portfolio_ids": [],
    "source_login": "",
    "source_server": "",
    "tester_login": "",
    "tester_server": "",
    "active_job_policy": "pause_resume",
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
    "source_login": 32,
    "source_server": 160,
    "tester_login": 32,
    "tester_server": 160,
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
_SECRET_KEYS = {"source_password", "tester_password"}


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


def normalize_live_audit_settings(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("La configuración del auditor debe ser un objeto JSON")
    unknown = set(value) - set(DEFAULT_LIVE_AUDIT_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")

    normalized = dict(DEFAULT_LIVE_AUDIT_SETTINGS)
    normalized["selected_portfolio_ids"] = []
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
    if "selected_portfolio_ids" in value:
        normalized["selected_portfolio_ids"] = _portfolio_ids(value["selected_portfolio_ids"])

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

    if "active_job_policy" in value and value["active_job_policy"] != "pause_resume":
        raise ValueError("active_job_policy debe ser pause_resume")
    normalized["active_job_policy"] = "pause_resume"

    for key in ("source_login", "tester_login"):
        login = normalized[key]
        if login and not login.isdigit():
            raise ValueError(f"{key} debe contener solo dígitos")
    if normalized["source_login"] and normalized["source_login"] == normalized["tester_login"]:
        raise ValueError("La cuenta real y la cuenta de pruebas deben tener logins diferentes")
    if normalized["enabled"]:
        if not normalized["selected_portfolio_ids"]:
            raise ValueError("Selecciona al menos un portafolio para habilitar el auditor")
        for key, label in (
            ("source_login", "login de la cuenta real"),
            ("source_server", "servidor de la cuenta real"),
            ("tester_login", "login de la cuenta de pruebas"),
            ("tester_server", "servidor de la cuenta de pruebas"),
        ):
            if not normalized[key]:
                raise ValueError(f"Falta el {label} para habilitar el auditor")
    return normalized


def _migrate_legacy_settings(raw: dict[str, Any]) -> dict[str, Any]:
    legacy = {"deployment_name", "account_login", "account_server", "terminal_path"}
    if not legacy.intersection(raw):
        return raw
    migrated = {key: value for key, value in raw.items() if key not in legacy}
    migrated["source_login"] = raw.get("source_login") or raw.get("account_login") or ""
    migrated["source_server"] = raw.get("source_server") or raw.get("account_server") or ""
    # La configuración anterior no tenía cuenta de pruebas, portafolios ni secretos.
    migrated["enabled"] = False
    return migrated


class LiveAuditSettingsStore:
    """Configuración pública y credenciales cifradas del futuro auditor."""

    def __init__(
        self,
        path: str | Path,
        credentials_path: str | Path | None = None,
        key_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.credentials_path = (
            Path(credentials_path).expanduser().resolve()
            if credentials_path
            else self.path.with_name("live_audit_credentials.json")
        )
        self.key_path = (
            Path(key_path).expanduser().resolve()
            if key_path
            else self.path.with_name("live_audit_credentials.key")
        )
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        self.credential_records: dict[str, dict[str, str]] = {}
        if self.path.is_file():
            stored = load_json(self.path)
            for node_id, record in stored.items():
                if not isinstance(record, dict):
                    continue
                raw_settings = record.get("settings")
                if not isinstance(raw_settings, dict):
                    continue
                try:
                    settings = normalize_live_audit_settings(_migrate_legacy_settings(raw_settings))
                except ValueError:
                    continue
                self.records[str(node_id)] = {
                    "settings": settings,
                    "updated_at": str(record.get("updated_at") or ""),
                }
        if self.credentials_path.is_file():
            stored_credentials = load_json(self.credentials_path)
            cipher = self._cipher(create=False)
            for node_id, record in stored_credentials.items():
                if not isinstance(record, dict):
                    continue
                clean: dict[str, str] = {}
                for key in _SECRET_KEYS:
                    token = str(record.get(key) or "")
                    if not token:
                        continue
                    try:
                        cipher.decrypt(token.encode("ascii"))
                    except (InvalidToken, UnicodeEncodeError) as exc:
                        raise ValueError(f"Credencial cifrada inválida para {node_id}") from exc
                    clean[key] = token
                if clean:
                    self.credential_records[str(node_id)] = clean

    def _cipher(self, *, create: bool) -> Fernet:
        configured_key = str(os.environ.get("MT5_MANAGER_LIVE_AUDIT_KEY") or "").strip()
        if configured_key:
            return Fernet(configured_key.encode("ascii"))
        if self.key_path.is_file():
            return Fernet(self.key_path.read_bytes().strip())
        if not create:
            raise ValueError("Falta la clave de las credenciales del auditor")
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return Fernet(key)

    def _password_flags(self, node_id: str) -> dict[str, bool]:
        credentials = self.credential_records.get(node_id) or {}
        return {
            "source_password_saved": bool(credentials.get("source_password")),
            "tester_password_saved": bool(credentials.get("tester_password")),
        }

    def state(self, node_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.records.get(node_id) or {}
            settings = dict(record.get("settings") or DEFAULT_LIVE_AUDIT_SETTINGS)
            settings["selected_portfolio_ids"] = list(settings.get("selected_portfolio_ids") or [])
            flags = self._password_flags(node_id)
            identity_ready = all(settings.get(key) for key in (
                "selected_portfolio_ids", "source_login", "source_server", "tester_login", "tester_server"
            ))
            return {
                "settings": settings,
                "defaults": {**DEFAULT_LIVE_AUDIT_SETTINGS, "selected_portfolio_ids": []},
                "updated_at": record.get("updated_at") or None,
                "configured": bool(identity_ready and all(flags.values())),
                **flags,
                "phase": "configuration_only",
            }

    def update(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("La configuración del auditor debe ser un objeto JSON")
        with self.lock:
            changes = dict(changes)
            secret_changes: dict[str, str] = {}
            for key in _SECRET_KEYS:
                if key not in changes:
                    continue
                value = changes.pop(key)
                if not isinstance(value, str):
                    raise ValueError(f"{key} debe ser texto")
                if len(value) > 512:
                    raise ValueError(f"{key} no puede superar 512 caracteres")
                if value:
                    secret_changes[key] = value

            current = self.state(node_id)["settings"]
            current.update(changes)
            normalized = normalize_live_audit_settings(current)
            existing_credentials = dict(self.credential_records.get(node_id) or {})
            available_secrets = set(existing_credentials) | set(secret_changes)
            if normalized["enabled"] and not _SECRET_KEYS.issubset(available_secrets):
                raise ValueError("Guarda las contraseñas de la cuenta real y de pruebas antes de habilitar el auditor")

            if secret_changes:
                cipher = self._cipher(create=True)
                for key, value in secret_changes.items():
                    existing_credentials[key] = cipher.encrypt(value.encode("utf-8")).decode("ascii")
                credential_records = dict(self.credential_records)
                credential_records[node_id] = existing_credentials
                save_json(self.credentials_path, credential_records)
                self.credential_records = credential_records

            self.records[node_id] = {"settings": normalized, "updated_at": utc_now()}
            save_json(self.path, self.records)
            return self.state(node_id)

    def credentials(self, node_id: str) -> dict[str, str]:
        """Devuelve secretos solo al futuro orquestador interno, nunca a la API."""
        with self.lock:
            record = self.credential_records.get(node_id) or {}
            if not record:
                return {}
            cipher = self._cipher(create=False)
            return {
                key: cipher.decrypt(token.encode("ascii")).decode("utf-8")
                for key, token in record.items()
            }
