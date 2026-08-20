from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .common import load_json, save_json, utc_now


DEFAULT_LIVE_AUDIT_PROFILE: dict[str, Any] = {
    "portfolio_id": 0,
    "portfolio_type": "",
    "deployment_name": "",
    "source_login": "",
    "source_server": "",
    "tester_login": "",
    "tester_server": "",
    "active_job_policy": "pause_resume",
    "period_days": 7,
    "audit_interval_days": 1,
    "tester_model": "real_ticks",
    "min_tick_history_quality_pct": 80.0,
    "execution_delay_mode": "measured",
    "fixed_delay_ms": 0,
    "trade_time_tolerance_seconds": 60,
    "price_tolerance_points": 10.0,
    "volume_tolerance_pct": 1.0,
    "pnl_deviation_warning_pct": 10.0,
    "drawdown_deviation_warning_pct": 15.0,
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
_SECRET_KEYS = {"source_password", "tester_password"}
_REQUEST_KEYS = {"selected_audit_ids", "selected_portfolio_ids", "profiles"}
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


def normalize_live_audit_settings(value: dict[str, Any]) -> dict[str, Any]:
    """Normaliza el perfil independiente de un portafolio."""
    if not isinstance(value, dict):
        raise ValueError("La configuración del portafolio debe ser un objeto JSON")
    # La primera versión confundía sincronización/heartbeat con la cadencia de
    # la auditoría. Se aceptan solo para poder cargar y reemplazar registros ya
    # guardados; nunca vuelven a formar parte del contrato público.
    value = {key: item for key, item in value.items() if key not in _LEGACY_SCHEDULE_KEYS}
    unknown = set(value) - set(DEFAULT_LIVE_AUDIT_PROFILE)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")

    normalized = dict(DEFAULT_LIVE_AUDIT_PROFILE)
    if "portfolio_id" in value:
        # Cero es el marcador de un perfil aún no asociado; update() exige un
        # ID real antes de persistir un uso seleccionado.
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

    if "tester_model" in value:
        tester_model = _text(value["tester_model"], "tester_model", 32).lower()
        if tester_model != "real_ticks":
            raise ValueError("tester_model debe ser real_ticks en este MVP")
        normalized["tester_model"] = tester_model

    if normalized["portfolio_type"]:
        normalized["portfolio_type"] = normalized["portfolio_type"].lower()
        if normalized["portfolio_type"] not in {"aggressive", "balanced", "conservative"}:
            raise ValueError("portfolio_type debe ser aggressive, balanced o conservative")

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


class LiveAuditSettingsStore:
    """Usos auditados y credenciales cifradas, independientes por cuenta y variante."""

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
        self.credential_records: dict[str, dict[str, dict[str, str]]] = {}
        if self.path.is_file():
            self._load_settings(load_json(self.path))
        if self.credentials_path.is_file():
            self._load_credentials(load_json(self.credentials_path))

    def _load_settings(self, stored: dict[str, Any]) -> None:
        for raw_node_id, record in stored.items():
            if not isinstance(record, dict):
                continue
            node_id = str(raw_node_id)
            try:
                if isinstance(record.get("profiles"), dict):
                    if "selected_audit_ids" in record:
                        selected = _audit_ids(record.get("selected_audit_ids") or [])
                        profiles = {
                            str(audit_id): normalize_live_audit_settings(profile)
                            for audit_id, profile in record["profiles"].items()
                            if isinstance(profile, dict)
                        }
                    else:
                        # Contrato anterior: un único uso por portfolio_id. Conservamos
                        # el identificador y la composición que antes elegía el nodo de
                        # forma implícita (Moderado era el valor histórico por defecto).
                        portfolio_ids = _portfolio_ids(record.get("selected_portfolio_ids") or [])
                        selected = [str(value) for value in portfolio_ids]
                        profiles = {}
                        for portfolio_id, profile in record["profiles"].items():
                            if not isinstance(profile, dict):
                                continue
                            numeric_id = _integer(portfolio_id, "portfolio_id", 1, 2_147_483_647)
                            migrated = {"portfolio_id": numeric_id, "portfolio_type": "balanced", **profile}
                            profiles[str(numeric_id)] = normalize_live_audit_settings(migrated)
                elif isinstance(record.get("settings"), dict):
                    raw_settings = record["settings"]
                    portfolio_ids = _portfolio_ids(raw_settings.get("selected_portfolio_ids") or [])
                    selected = [str(value) for value in portfolio_ids]
                    profile = _public_legacy_profile(raw_settings)
                    profiles = {
                        str(portfolio_id): normalize_live_audit_settings({
                            **profile, "portfolio_id": portfolio_id, "portfolio_type": "balanced",
                        })
                        for portfolio_id in portfolio_ids
                    }
                else:
                    continue
            except ValueError:
                continue
            self.records[node_id] = {
                "selected_audit_ids": selected,
                "profiles": profiles,
                "updated_at": str(record.get("updated_at") or ""),
            }

    def _load_credentials(self, stored: dict[str, Any]) -> None:
        cipher = self._cipher(create=False)
        for raw_node_id, raw_record in stored.items():
            if not isinstance(raw_record, dict):
                continue
            node_id = str(raw_node_id)
            # Migración del primer MVP: dos secretos compartidos por todos los IDs seleccionados.
            if _SECRET_KEYS.intersection(raw_record):
                selected = (self.records.get(node_id) or {}).get("selected_audit_ids") or []
                portfolio_records = {str(audit_id): raw_record for audit_id in selected}
            else:
                portfolio_records = raw_record
            clean_portfolios: dict[str, dict[str, str]] = {}
            for raw_audit_id, record in portfolio_records.items():
                if not isinstance(record, dict):
                    continue
                audit_id = str(raw_audit_id)
                if not audit_id or len(audit_id) > 120:
                    continue
                clean: dict[str, str] = {}
                for key in _SECRET_KEYS:
                    token = str(record.get(key) or "")
                    if not token:
                        continue
                    try:
                        cipher.decrypt(token.encode("ascii"))
                    except (InvalidToken, UnicodeEncodeError) as exc:
                        raise ValueError(f"Credencial cifrada inválida para {node_id}, uso {audit_id}") from exc
                    clean[key] = token
                if clean:
                    clean_portfolios[audit_id] = clean
            if clean_portfolios:
                self.credential_records[node_id] = clean_portfolios

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

    def _credential_flags(self, node_id: str, audit_id: str) -> dict[str, bool]:
        credentials = (self.credential_records.get(node_id) or {}).get(audit_id) or {}
        return {
            "source_password_saved": bool(credentials.get("source_password")),
            "tester_password_saved": bool(credentials.get("tester_password")),
        }

    def state(self, node_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.records.get(node_id) or {}
            selected = list(record.get("selected_audit_ids") or [])
            profiles = {
                str(audit_id): dict(profile)
                for audit_id, profile in (record.get("profiles") or {}).items()
            }
            credential_state = {
                audit_id: self._credential_flags(node_id, audit_id)
                for audit_id in set(profiles) | set(selected)
            }
            configured_ids = [
                audit_id for audit_id in selected
                if audit_id in profiles
                and all(credential_state[audit_id].values())
                and all(profiles[audit_id].get(key) for key in (
                    "portfolio_id", "portfolio_type", "source_login", "source_server", "tester_login", "tester_server"
                ))
            ]
            selected_portfolios = list(dict.fromkeys(
                int(profiles[audit_id]["portfolio_id"])
                for audit_id in selected if audit_id in profiles and profiles[audit_id].get("portfolio_id")
            ))
            configured_portfolios = list(dict.fromkeys(
                int(profiles[audit_id]["portfolio_id"]) for audit_id in configured_ids
            ))
            return {
                "selected_audit_ids": selected,
                "selected_portfolio_ids": selected_portfolios,
                "profiles": profiles,
                "defaults": dict(DEFAULT_LIVE_AUDIT_PROFILE),
                "credential_state": credential_state,
                "configured_audit_ids": configured_ids,
                "configured_portfolio_ids": configured_portfolios,
                "configured": bool(selected) and len(configured_ids) == len(selected),
                "updated_at": record.get("updated_at") or None,
                "phase": "configuration_only",
            }

    def update(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("La configuración del auditor debe ser un objeto JSON")
        unknown = set(changes) - _REQUEST_KEYS
        if unknown:
            raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
        modern_contract = "selected_audit_ids" in changes
        if modern_contract:
            selected = _audit_ids(changes.get("selected_audit_ids") or [])
        else:
            selected = [str(value) for value in _portfolio_ids(changes.get("selected_portfolio_ids") or [])]
        submitted_profiles = changes.get("profiles")
        if not isinstance(submitted_profiles, dict):
            raise ValueError("profiles debe ser un objeto por portafolio")

        with self.lock:
            existing = self.records.get(node_id) or {}
            profiles = {
                str(portfolio_id): dict(profile)
                for portfolio_id, profile in (existing.get("profiles") or {}).items()
            }
            node_credentials = {
                str(portfolio_id): dict(credentials)
                for portfolio_id, credentials in (self.credential_records.get(node_id) or {}).items()
            }
            credentials_changed = False
            cipher: Fernet | None = None

            for audit_id in selected:
                key = str(audit_id)
                raw_profile = submitted_profiles.get(key)
                if raw_profile is None and not modern_contract and key.isdigit():
                    raw_profile = submitted_profiles.get(int(key))
                if not isinstance(raw_profile, dict):
                    if not modern_contract and key.isdigit():
                        raise ValueError(f"Falta la configuración del portafolio #{key}")
                    raise ValueError(f"Falta la configuración del uso {audit_id}")
                raw_profile = dict(raw_profile)
                if not modern_contract:
                    raw_profile["portfolio_id"] = int(key)
                    if raw_profile.get("portfolio_type") not in {"aggressive", "balanced", "conservative"}:
                        raw_profile["portfolio_type"] = "balanced"
                portfolio_id = int(raw_profile.get("portfolio_id") or 0)
                secret_changes: dict[str, str] = {}
                for secret_key in _SECRET_KEYS:
                    if secret_key not in raw_profile:
                        continue
                    value = raw_profile.pop(secret_key)
                    if not isinstance(value, str):
                        raise ValueError(f"{secret_key} del uso {audit_id} debe ser texto")
                    if len(value) > 512:
                        raise ValueError(f"{secret_key} del uso {audit_id} no puede superar 512 caracteres")
                    if value:
                        secret_changes[secret_key] = value

                merged = dict(profiles.get(key) or DEFAULT_LIVE_AUDIT_PROFILE)
                merged.update(raw_profile)
                normalized = normalize_live_audit_settings(merged)
                _require_complete_profile(audit_id, normalized)

                encrypted = dict(node_credentials.get(key) or {})
                available_secrets = set(encrypted) | set(secret_changes)
                if not _SECRET_KEYS.issubset(available_secrets):
                    raise ValueError(f"Guarda las dos contraseñas del portafolio #{portfolio_id} ({audit_id})")
                if secret_changes:
                    cipher = cipher or self._cipher(create=True)
                    for secret_key, value in secret_changes.items():
                        encrypted[secret_key] = cipher.encrypt(value.encode("utf-8")).decode("ascii")
                    node_credentials[key] = encrypted
                    credentials_changed = True
                profiles[key] = normalized

            if credentials_changed:
                credential_records = dict(self.credential_records)
                credential_records[node_id] = node_credentials
                save_json(self.credentials_path, credential_records)
                self.credential_records = credential_records

            self.records[node_id] = {
                "selected_audit_ids": selected,
                "profiles": profiles,
                "updated_at": utc_now(),
            }
            save_json(self.path, self.records)
            return self.state(node_id)

    def credentials(self, node_id: str, audit_id: str | int) -> dict[str, str]:
        """Devuelve secretos de un uso auditado solo al orquestador interno."""
        with self.lock:
            record = (self.credential_records.get(node_id) or {}).get(str(audit_id)) or {}
            if not record:
                return {}
            cipher = self._cipher(create=False)
            return {
                key: cipher.decrypt(token.encode("ascii")).decode("utf-8")
                for key, token in record.items()
            }
