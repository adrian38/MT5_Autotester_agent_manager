from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .common import load_json, save_json, utc_now
from .live_audit_settings_schema import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    DEFAULT_LIVE_AUDIT_SETTINGS,
    DEFAULT_TERMINAL_RESTORE_ACCOUNT,
    _audit_ids,
    _integer,
    _portfolio_ids,
    _public_legacy_profile,
    _require_complete_profile,
    normalize_live_audit_settings,
    normalize_terminal_restore_account,
)
_PROFILE_SECRET_KEYS = {"source_password", "tester_password"}
_RESTORE_SECRET_KEYS = {"restore_password"}
_RESTORE_CREDENTIAL_ID = "__terminal_restore__"
_ACCOUNT_REFERENCE_KEYS = {
    "source": "source_saved_account_id",
    "tester": "tester_saved_account_id",
}
_REQUEST_KEYS = {"selected_audit_ids", "selected_portfolio_ids", "profiles"}


@dataclass
class _SettingsUpdate:
    existing: dict[str, Any]
    account_sources: dict[str, dict[str, str]]
    profiles: dict[str, dict[str, Any]]
    node_credentials: dict[str, dict[str, str]]
    cipher: Fernet | None = None
    credentials_changed: bool = False


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
                restore_account = (
                    normalize_terminal_restore_account(record["restore_account"])
                    if isinstance(record.get("restore_account"), dict)
                    else None
                )
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
                    selected = []
                    profiles = {}
            except ValueError:
                continue
            if not profiles and restore_account is None:
                continue
            self.records[node_id] = {
                "selected_audit_ids": selected,
                "profiles": profiles,
                "updated_at": str(record.get("updated_at") or ""),
            }
            if restore_account is not None:
                self.records[node_id]["restore_account"] = restore_account

    def _load_credentials(self, stored: dict[str, Any]) -> None:
        cipher = self._cipher(create=False)
        for raw_node_id, raw_record in stored.items():
            if not isinstance(raw_record, dict):
                continue
            node_id = str(raw_node_id)
            # Migración del primer MVP: dos secretos compartidos por todos los IDs seleccionados.
            if _PROFILE_SECRET_KEYS.intersection(raw_record):
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
                secret_keys = (
                    _RESTORE_SECRET_KEYS if audit_id == _RESTORE_CREDENTIAL_ID else _PROFILE_SECRET_KEYS
                )
                for key in secret_keys:
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

    def _restore_account_state(self, node_id: str, record: dict[str, Any]) -> dict[str, Any]:
        account = {
            **DEFAULT_TERMINAL_RESTORE_ACCOUNT,
            **dict(record.get("restore_account") or {}),
        }
        encrypted = (
            (self.credential_records.get(node_id) or {}).get(_RESTORE_CREDENTIAL_ID) or {}
        )
        account["password_saved"] = bool(encrypted.get("restore_password"))
        account["configured"] = bool(
            account["login"] and account["server"] and account["password_saved"]
        )
        return account

    def _profile_account_candidates(self, node_id: str) -> list[dict[str, str]]:
        record = self.records.get(node_id) or {}
        encrypted = self.credential_records.get(node_id) or {}
        candidates: list[dict[str, str]] = []
        for audit_id, profile in (record.get("profiles") or {}).items():
            if not isinstance(profile, dict):
                continue
            stored = encrypted.get(str(audit_id)) or {}
            portfolio_id = int(profile.get("portfolio_id") or 0)
            deployment = str(profile.get("deployment_name") or "").strip()
            portfolio_label = deployment or (
                f"Portafolio #{portfolio_id}" if portfolio_id else f"Uso {audit_id}"
            )
            for role, password_key, role_label in (
                ("source", "source_password", "cuenta real"),
                ("tester", "tester_password", "cuenta de pruebas"),
            ):
                login = str(profile.get(f"{role}_login") or "").strip()
                server = str(profile.get(f"{role}_server") or "").strip()
                token = str(stored.get(password_key) or "")
                if login and server and token:
                    candidates.append({
                        "id": f"profile:{audit_id}:{role}",
                        "login": login,
                        "server": server,
                        "token": token,
                        "origin": f"{portfolio_label} · {role_label}",
                    })
        return candidates

    def _append_restore_candidate(
        self, node_id: str, candidates: list[dict[str, str]],
    ) -> None:
        record = self.records.get(node_id) or {}
        encrypted = self.credential_records.get(node_id) or {}
        restore = self._restore_account_state(node_id, record)
        token = str(
            (encrypted.get(_RESTORE_CREDENTIAL_ID) or {}).get("restore_password") or ""
        )
        if restore["login"] and restore["server"] and token:
            candidates.append({
                "id": "restore:terminal",
                "login": str(restore["login"]),
                "server": str(restore["server"]),
                "token": token,
                "origin": "Cuenta final de los terminales",
            })

    @staticmethod
    def _account_identity(candidate: dict[str, str]) -> tuple[str, str]:
        """La cuenta es el login en su servidor: el mismo par es la misma cuenta."""
        return candidate["login"].strip(), candidate["server"].strip().casefold()

    @classmethod
    def _account_catalog(
        cls, candidates: list[dict[str, str]],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
        """Una entrada por cuenta: el rol y el uso son procedencia, no identidad."""
        public: list[dict[str, Any]] = []
        sources: dict[str, dict[str, str]] = {}
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            identity = cls._account_identity(candidate)
            entry = merged.get(identity)
            if entry is not None:
                entry["uses"] += 1
                continue
            account_id = candidate["id"]
            sources[account_id] = {
                "login": candidate["login"],
                "server": candidate["server"],
                "token": candidate["token"],
            }
            entry = {
                "id": account_id,
                "login": candidate["login"],
                "server": candidate["server"],
                "origin": candidate["origin"],
                "uses": 1,
            }
            merged[identity] = entry
            public.append(entry)
        return public, sources

    def _saved_account_sources(
        self, node_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
        """Devuelve el catálogo público y sus fuentes cifradas reutilizables."""
        candidates = self._profile_account_candidates(node_id)
        self._append_restore_candidate(node_id, candidates)
        return self._account_catalog(candidates)

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
            saved_accounts, _account_sources = self._saved_account_sources(node_id)
            return {
                "selected_audit_ids": selected,
                "selected_portfolio_ids": selected_portfolios,
                "profiles": profiles,
                "defaults": dict(DEFAULT_LIVE_AUDIT_PROFILE),
                "restore_account": self._restore_account_state(node_id, record),
                "saved_accounts": saved_accounts,
                "credential_state": credential_state,
                "configured_audit_ids": configured_ids,
                "configured_portfolio_ids": configured_portfolios,
                "configured": bool(selected) and len(configured_ids) == len(selected),
                "updated_at": record.get("updated_at") or None,
                "phase": "configuration_only",
            }

    @staticmethod
    def _update_request(
        changes: dict[str, Any],
    ) -> tuple[list[str], dict[Any, Any], bool]:
        if not isinstance(changes, dict):
            raise ValueError("La configuración del auditor debe ser un objeto JSON")
        unknown = set(changes) - _REQUEST_KEYS
        if unknown:
            raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
        modern = "selected_audit_ids" in changes
        selected = (
            _audit_ids(changes.get("selected_audit_ids") or [])
            if modern else
            [str(value) for value in _portfolio_ids(changes.get("selected_portfolio_ids") or [])]
        )
        submitted = changes.get("profiles")
        if not isinstance(submitted, dict):
            raise ValueError("profiles debe ser un objeto por portafolio")
        return selected, submitted, modern

    def _update_context(self, node_id: str) -> _SettingsUpdate:
        existing = self.records.get(node_id) or {}
        _saved_accounts, account_sources = self._saved_account_sources(node_id)
        return _SettingsUpdate(
            existing=existing,
            account_sources=account_sources,
            profiles={
                str(portfolio_id): dict(profile)
                for portfolio_id, profile in (existing.get("profiles") or {}).items()
            },
            node_credentials={
                str(portfolio_id): dict(credentials)
                for portfolio_id, credentials
                in (self.credential_records.get(node_id) or {}).items()
            },
        )

    @staticmethod
    def _submitted_profile(
        audit_id: str, submitted: dict[Any, Any], modern: bool,
    ) -> dict[str, Any]:
        raw = submitted.get(audit_id)
        if raw is None and not modern and audit_id.isdigit():
            raw = submitted.get(int(audit_id))
        if not isinstance(raw, dict):
            if not modern and audit_id.isdigit():
                raise ValueError(f"Falta la configuración del portafolio #{audit_id}")
            raise ValueError(f"Falta la configuración del uso {audit_id}")
        profile = dict(raw)
        if not modern:
            profile["portfolio_id"] = int(audit_id)
            if profile.get("portfolio_type") not in {"aggressive", "balanced", "conservative"}:
                profile["portfolio_type"] = "balanced"
        return profile

    @staticmethod
    def _reuse_referenced_accounts(
        raw_profile: dict[str, Any], account_sources: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        reused: dict[str, str] = {}
        for role, reference_key in _ACCOUNT_REFERENCE_KEYS.items():
            account_id = str(raw_profile.pop(reference_key, "") or "").strip()
            if not account_id:
                continue
            account = account_sources.get(account_id)
            if account is None:
                raise ValueError(
                    f"La cuenta guardada seleccionada para {role} ya no está disponible; recarga la página"
                )
            raw_profile[f"{role}_login"] = account["login"]
            raw_profile[f"{role}_server"] = account["server"]
            reused[f"{role}_password"] = account["token"]
        return reused

    @staticmethod
    def _secret_changes(audit_id: str, raw_profile: dict[str, Any]) -> dict[str, str]:
        changes: dict[str, str] = {}
        for secret_key in _PROFILE_SECRET_KEYS:
            if secret_key not in raw_profile:
                continue
            value = raw_profile.pop(secret_key)
            if not isinstance(value, str):
                raise ValueError(f"{secret_key} del uso {audit_id} debe ser texto")
            if len(value) > 512:
                raise ValueError(f"{secret_key} del uso {audit_id} no puede superar 512 caracteres")
            if value:
                changes[secret_key] = value
        return changes

    def _update_profile(
        self, state: _SettingsUpdate, audit_id: str, raw_profile: dict[str, Any],
    ) -> None:
        reused = self._reuse_referenced_accounts(raw_profile, state.account_sources)
        secret_changes = self._secret_changes(audit_id, raw_profile)
        portfolio_id = int(raw_profile.get("portfolio_id") or 0)
        merged = dict(state.profiles.get(audit_id) or DEFAULT_LIVE_AUDIT_PROFILE)
        merged.update(raw_profile)
        normalized = normalize_live_audit_settings(merged)
        _require_complete_profile(audit_id, normalized)
        encrypted = dict(state.node_credentials.get(audit_id) or {})
        encrypted.update(reused)
        if not _PROFILE_SECRET_KEYS.issubset(set(encrypted) | set(secret_changes)):
            raise ValueError(
                f"Guarda las dos contraseñas del portafolio #{portfolio_id} ({audit_id})"
            )
        if secret_changes:
            state.cipher = state.cipher or self._cipher(create=True)
            for key, value in secret_changes.items():
                encrypted[key] = state.cipher.encrypt(value.encode("utf-8")).decode("ascii")
        if reused or secret_changes:
            state.node_credentials[audit_id] = encrypted
            state.credentials_changed = True
        state.profiles[audit_id] = normalized

    def _save_update(
        self, node_id: str, selected: list[str], state: _SettingsUpdate,
    ) -> dict[str, Any]:
        if state.credentials_changed:
            credential_records = dict(self.credential_records)
            credential_records[node_id] = state.node_credentials
            save_json(self.credentials_path, credential_records)
            self.credential_records = credential_records
        self.records[node_id] = {
            "selected_audit_ids": selected,
            "profiles": state.profiles,
            "updated_at": utc_now(),
        }
        if state.existing.get("restore_account"):
            self.records[node_id]["restore_account"] = dict(state.existing["restore_account"])
        save_json(self.path, self.records)
        return self.state(node_id)

    def update(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        selected, submitted, modern = self._update_request(changes)
        with self.lock:
            state = self._update_context(node_id)
            for audit_id in selected:
                raw_profile = self._submitted_profile(audit_id, submitted, modern)
                self._update_profile(state, audit_id, raw_profile)
            return self._save_update(node_id, selected, state)

    def update_restore_account(self, node_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Guarda la cuenta que debe quedar activa sin mezclarla con la del tester."""
        if not isinstance(changes, dict):
            raise ValueError("La cuenta de restauración debe ser un objeto JSON")
        unknown = set(changes) - {"login", "server", "password"}
        if unknown:
            raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
        password = changes.get("password", "")
        if not isinstance(password, str):
            raise ValueError("La contraseña de restauración debe ser texto")
        if len(password) > 512:
            raise ValueError("La contraseña de restauración no puede superar 512 caracteres")

        with self.lock:
            existing = self.records.get(node_id) or {}
            account = normalize_terminal_restore_account({
                **dict(existing.get("restore_account") or DEFAULT_TERMINAL_RESTORE_ACCOUNT),
                **{key: changes[key] for key in ("login", "server") if key in changes},
            })
            node_credentials = {
                str(key): dict(value)
                for key, value in (self.credential_records.get(node_id) or {}).items()
            }
            encrypted = dict(node_credentials.get(_RESTORE_CREDENTIAL_ID) or {})
            if password:
                cipher = self._cipher(create=True)
                encrypted["restore_password"] = cipher.encrypt(password.encode("utf-8")).decode("ascii")
                node_credentials[_RESTORE_CREDENTIAL_ID] = encrypted
                credential_records = dict(self.credential_records)
                credential_records[node_id] = node_credentials
                save_json(self.credentials_path, credential_records)
                self.credential_records = credential_records
            if not encrypted.get("restore_password"):
                raise ValueError("Falta la contraseña de la cuenta de restauración")

            self.records[node_id] = {
                "selected_audit_ids": list(existing.get("selected_audit_ids") or []),
                "profiles": {
                    str(key): dict(value)
                    for key, value in (existing.get("profiles") or {}).items()
                },
                "restore_account": account,
                "updated_at": utc_now(),
            }
            save_json(self.path, self.records)
            return self.state(node_id)

    def restore_credentials(self, node_id: str) -> dict[str, str]:
        """Devuelve al orquestador la cuenta de restauración con nombres del contrato del nodo."""
        with self.lock:
            state = self._restore_account_state(node_id, self.records.get(node_id) or {})
            encrypted = (
                (self.credential_records.get(node_id) or {}).get(_RESTORE_CREDENTIAL_ID) or {}
            )
            token = str(encrypted.get("restore_password") or "")
            if not state["configured"] or not token:
                return {}
            cipher = self._cipher(create=False)
            return {
                "restore_login": str(state["login"]),
                "restore_server": str(state["server"]),
                "restore_password": cipher.decrypt(token.encode("ascii")).decode("utf-8"),
            }

    def credentials(self, node_id: str, audit_id: str | int) -> dict[str, str]:
        """Devuelve secretos de un uso auditado solo al orquestador interno."""
        with self.lock:
            record = (self.credential_records.get(node_id) or {}).get(str(audit_id)) or {}
            if not record:
                return {}
            cipher = self._cipher(create=False)
            return {
                key: cipher.decrypt(token.encode("ascii")).decode("utf-8")
                for key, token in record.items() if key in _PROFILE_SECRET_KEYS
            }
