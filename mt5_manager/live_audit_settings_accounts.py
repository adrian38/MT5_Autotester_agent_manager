"""El catalogo de cuentas guardadas de un nodo, aparte del almacen.

Sale de `live_audit_settings.py`, que estaba justo en el techo de 600 lineas.
Es un bloque cohesionado: toma los perfiles y las credenciales cifradas que ya
tiene el almacen y responde que cuentas hay, de donde salen, cual es la que
debe quedar en los terminales y, cuando la pantalla la pide, cual es su
contrasena. No escribe nada.
"""
from __future__ import annotations

from typing import Any

from cryptography.fernet import InvalidToken

from .live_audit_settings_schema import DEFAULT_TERMINAL_RESTORE_ACCOUNT

_RESTORE_CREDENTIAL_ID = "__terminal_restore__"
_RESTORE_ACCOUNT_REFERENCE = "restore:terminal"
_PROFILE_ROLES = ("source", "tester")


def _secret_location(reference: str) -> tuple[str, str]:
    """Traduce un id del catálogo al registro y el campo que guardan su token.

    Son los mismos ids que `_account_catalog` publica, así que la pantalla pide
    la contraseña de una cuenta con lo que ya tiene en la mano: el valor del
    selector de cuentas o el del rol que está editando.
    """
    if reference == _RESTORE_ACCOUNT_REFERENCE:
        return _RESTORE_CREDENTIAL_ID, "restore_password"
    parts = reference.split(":")
    if len(parts) == 3 and parts[0] == "profile" and parts[2] in _PROFILE_ROLES:
        return parts[1], f"{parts[2]}_password"
    raise KeyError(f"Referencia de cuenta desconocida: {reference}")


class LiveAuditAccountsMixin:
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
                "id": _RESTORE_ACCOUNT_REFERENCE,
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

    def revealed_password(self, node_id: str, reference: str) -> str:
        """Descifra una contraseña guardada porque el usuario pide verla.

        Es lo único que devuelve un secreto a la pantalla, y sólo de uno en
        uno: el estado del uso sigue llevando el sí o el no de siempre. Quien
        abre el manager ya puede lanzar la auditoría con esa cuenta, así que lo
        que protege el cifrado es el disco, no a quien tiene la pantalla
        delante; teclear a ciegas una contraseña larga del bróker y descubrir
        la errata veinte minutos después no compensaba ese silencio.

        Un token que ya no descifra —clave cambiada o fichero copiado de otro
        equipo— sale como `ValueError` y no como fallo de `cryptography`: para
        quien pregunta es lo mismo que no tenerla.
        """
        credential_id, key = _secret_location(reference)
        with self.lock:
            stored = (self.credential_records.get(node_id) or {}).get(credential_id) or {}
            token = str(stored.get(key) or "")
            if not token:
                raise KeyError(f"No hay contraseña guardada para {reference}")
            try:
                return self._cipher(create=False).decrypt(token.encode("ascii")).decode("utf-8")
            except InvalidToken as exc:
                raise ValueError(
                    f"La contraseña guardada de {reference} ya no se puede descifrar",
                ) from exc
