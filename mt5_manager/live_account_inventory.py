"""Qué cuentas reales hay que sondear, y cuáles no se pueden.

La tarjeta de la portada nueva es de la **cuenta**, no del portafolio: el saldo,
el beneficio del día y la DD del mes son de la cuenta. Hoy cada portafolio
activo tiene su propia cuenta —medido el 21 de octubre de 2026: nueve usos
`live_trading`, cuatro en AXI y cinco en RoboForex, cada uno con su login— pero
dos usos sobre el mismo login comparten un único saldo, y contarlo dos veces
duplicaría el total de arriba. Así que se agrupa por `login@servidor` y la
tarjeta nombra todos los portafolios que esa cuenta sirve.

El alcance es el mismo que el de «Portafolios activos» y el del cron de
auditoría: los usos marcados «operando en real». Se mira el **perfil**, no las
tarjetas abiertas en el formulario del nodo; confundir las dos cosas es el fallo
que ya dejó al cron semanal vigilando un solo uso de cuatro
(`ai_context/live_audit_cron_and_bell.md`).
"""
from __future__ import annotations

from typing import Any

from .common import safe_int
from .live_account_archive import account_key
from .live_audit_watch import live_audit_ids

REQUIRED_PROFILE_FIELDS = ("source_login", "source_server")


def _portfolio_row(audit_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "portfolio_id": safe_int(profile.get("portfolio_id"), 0, minimum=0),
        "portfolio_type": str(profile.get("portfolio_type") or ""),
        "name": str(profile.get("deployment_name") or "").strip(),
        "autolot": bool((profile.get("autolot") or {}).get("enabled")),
    }


def _block_reason(profile: dict[str, Any], credentials: dict[str, Any]) -> str:
    """Por qué esta cuenta no se puede leer, dicho en una línea.

    Se enseña en su tarjeta en vez de dejarla fuera: una cuenta real que no se
    sondea tiene que verse, o la portada miente por omisión.
    """
    missing = [
        label for key, label in (
            ("source_login", "login real"), ("source_server", "servidor real"),
        ) if not profile.get(key)
    ]
    if missing:
        return f"Falta configurar: {', '.join(missing)}."
    if not credentials.get("source_password_saved"):
        return "Falta la contraseña de la cuenta real."
    return ""


def node_accounts(server: Any, node_id: str) -> list[dict[str, Any]]:
    """Las cuentas reales de un nodo, cada una con los usos que la operan."""
    state = server.live_audit_settings.state(node_id)
    profiles = state.get("profiles") or {}
    credential_state = state.get("credential_state") or {}
    found: dict[str, dict[str, Any]] = {}
    for audit_id in sorted(live_audit_ids(state)):
        profile = dict(profiles.get(audit_id) or {})
        login = str(profile.get("source_login") or "").strip()
        server_name = str(profile.get("source_server") or "").strip()
        key = account_key(login or audit_id, server_name)
        entry = found.setdefault(key, {
            "account_key": key, "login": login, "server": server_name,
            "portfolios": [], "audit_ids": [], "real_strategy_lots": {},
            "autolot": False, "block_reason": "", "reasons": [],
        })
        entry["portfolios"].append(_portfolio_row(audit_id, profile))
        entry["audit_ids"].append(audit_id)
        entry["real_strategy_lots"].update(profile.get("real_strategy_lots") or {})
        entry["autolot"] = entry["autolot"] or bool(
            (profile.get("autolot") or {}).get("enabled")
        )
        entry["reasons"].append(
            _block_reason(profile, dict(credential_state.get(audit_id) or {}))
        )
    # Una cuenta la puede servir más de un uso, y la credencial es de la
    # **cuenta**: basta con que uno esté completo para poder leerla. Decidirlo
    # uso a uso dejaba la cuenta bloqueada por el orden en que se recorrían.
    for entry in found.values():
        reasons = entry.pop("reasons")
        entry["block_reason"] = "" if any(not reason for reason in reasons) else reasons[0]
    return [found[key] for key in sorted(found)]


def readable_accounts(server: Any, node_id: str) -> list[dict[str, Any]]:
    """Las que el cron puede sondear: con login, servidor y contraseña."""
    return [
        account for account in node_accounts(server, node_id)
        if not account["block_reason"] and account["login"] and account["server"]
    ]


def snapshot_accounts(server: Any, node_id: str) -> list[dict[str, Any]]:
    """Lo que viaja al nodo: la cuenta y su contraseña, nada más.

    La contraseña se descifra aquí y no se guarda en ningún sitio nuestro: la
    coge del almacén cifrado, igual que el lanzamiento de una auditoría.
    """
    payload: list[dict[str, Any]] = []
    for account in readable_accounts(server, node_id):
        password = ""
        for audit_id in account["audit_ids"]:
            password = str(
                server.live_audit_settings.credentials(node_id, audit_id).get(
                    "source_password", "",
                ) or ""
            )
            if password:
                break
        if not password:
            continue
        payload.append({
            "account_key": account["account_key"], "login": account["login"],
            "server": account["server"], "password": password,
        })
    return payload


def account_of(accounts: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """La entrada de esa cuenta en el inventario, o una vacía."""
    for account in accounts:
        if account.get("account_key") == key:
            return account
    return {}
