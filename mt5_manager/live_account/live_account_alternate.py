"""La cuenta que de verdad se sondea en cada tarjeta, cuando no es la suya.

Petición del usuario (2026-11-17): cada tarjeta de «Cuentas en real» puede
guardar **otra** cuenta y el agente con el que leerla; a partir de ahí el
sondeo de esa tarjeta entra en esa cuenta y no en la del perfil, y la
relectura completa del patrimonio también, porque pide el mismo informe a la
misma gente. Sin cuenta guardada no cambia nada: se sigue leyendo la del
perfil.

Tres cosas que no son obvias, y son el motivo de que esto sea un módulo y no
un campo más del perfil:

- **El perfil no se toca.** La auditoría empareja por `source_login`, y
  cambiarlo ahí movería el emparejado, el historial y los lotes declarados.
  Lo que se desvía es la lectura, nada más: por eso el usuario dijo «esto
  afecta sólo a estas dos pantallas».
- **La lectura puede cambiar de agente.** Entrar en una cuenta de otro bróker
  necesita un terminal de ese bróker, así que quien la lee es el agente
  elegido: la tarjeta de un agente puede viajar en el sondeo de otro. Por eso
  lo que se resuelve aquí es «qué tiene que leer este agente», que no es lo
  mismo que «qué cuentas tiene» (`live_account_inventory.node_accounts`).
- **El archivo sigue siendo el de la tarjeta.** Lo leído entra en la serie de
  su tarjeta —`(nodo de la tarjeta, account_key)`— y no en la del agente que
  lo leyó, o ni la portada ni el patrimonio verían nada. De eso se encarga
  `owner_node_id`, que es lo que `live_account_ingest.collect` consulta antes
  de archivar.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from . import live_account_inventory
from ..common import load_json, save_json, utc_now

MAX_PASSWORD = 512
MAX_FIELD = 200


def entry_key(node_id: str, account_key: str) -> str:
    """La clave de la tarjeta: el nodo y la cuenta, separados por `::`.

    La misma forma que `live_account_wealth.selection_key` y que escribe
    `accounts.js`, porque es la misma tarjeta: lo que decide si suma en el
    patrimonio y lo que decide qué cuenta se le sondea tienen que nombrarla
    igual o una pantalla configuraría la de al lado.
    """
    return f"{node_id}::{account_key}"


def split_key(key: str) -> tuple[str, str]:
    """El nodo y la cuenta de una clave de tarjeta, o `ValueError`."""
    node_id, separator, account_key = str(key or "").strip().partition("::")
    if not separator or not node_id or not account_key or len(key) > 400:
        raise ValueError("La tarjeta indicada no es válida")
    return node_id, account_key


def _text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Falta {label} de la cuenta alternativa")
    if len(text) > MAX_FIELD:
        raise ValueError(f"{label.capitalize()} no puede superar {MAX_FIELD} caracteres")
    return text


class LiveAccountAlternateStore:
    """Las cuentas alternativas guardadas, con su contraseña cifrada.

    El cifrado lo presta el almacén del auditor (`secret_cipher`): una sola
    clave para los secretos del manager, que además es la que ya existe en el
    equipo. Aquí sólo se guarda la pareja «tarjeta → cuenta y agente».
    """

    def __init__(self, path: str | Path, cipher: Callable[..., Fernet]) -> None:
        self.path = Path(path).expanduser().resolve()
        self._cipher = cipher
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            self._load(load_json(self.path))

    def _load(self, stored: Any) -> None:
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            if isinstance(value, dict) and str(value.get("login") or "").strip():
                self.records[str(key)] = dict(value)

    def any_configured(self) -> bool:
        """Si hay alguna tarjeta desviada. Sin ninguna, nada cambia de sitio."""
        with self.lock:
            return bool(self.records)

    def public(self, node_id: str, account_key: str) -> dict[str, Any]:
        """Lo que la pantalla puede ver de esa tarjeta: nunca la contraseña."""
        with self.lock:
            record = dict(self.records.get(entry_key(node_id, account_key)) or {})
        return {
            "configured": bool(record),
            "node_id": str(record.get("node_id") or ""),
            "login": str(record.get("login") or ""),
            "server": str(record.get("server") or ""),
            "password_saved": bool(record.get("password")),
            "updated_at": str(record.get("updated_at") or ""),
        }

    def credentials(self, node_id: str, account_key: str) -> dict[str, str]:
        """La cuenta alternativa en claro, sólo para el orquestador interno.

        Vacío significa «esta tarjeta no está desviada». Un token que ya no
        descifra —clave cambiada, fichero traído de otro equipo— **sí** es un
        desvío, y vuelve con la contraseña vacía: lo que no puede pasar es que
        se lea la cuenta del perfil como si no hubiera nada configurado.
        """
        with self.lock:
            record = dict(self.records.get(entry_key(node_id, account_key)) or {})
            if not record:
                return {}
            token = str(record.get("password") or "")
            try:
                password = (
                    self._cipher(create=False).decrypt(token.encode("ascii")).decode("utf-8")
                    if token else ""
                )
            except (InvalidToken, ValueError):
                password = ""
        return {
            "node_id": str(record.get("node_id") or ""),
            "login": str(record.get("login") or ""),
            "server": str(record.get("server") or ""),
            "password": password,
        }

    def save(self, node_id: str, account_key: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Guarda qué cuenta se sondea en esa tarjeta y con qué agente.

        La contraseña sólo viaja al guardarla: si no viene, se conserva la que
        ya estaba, que es lo que permite cambiar de agente sin volver a
        teclearla.
        """
        if not isinstance(changes, dict):
            raise ValueError("La cuenta alternativa debe ser un objeto JSON")
        password = changes.get("password", "")
        if not isinstance(password, str):
            raise ValueError("La contraseña debe ser texto")
        if len(password) > MAX_PASSWORD:
            raise ValueError(f"La contraseña no puede superar {MAX_PASSWORD} caracteres")
        with self.lock:
            record = dict(self.records.get(entry_key(node_id, account_key)) or {})
            if password:
                record["password"] = (
                    self._cipher(create=True).encrypt(password.encode("utf-8")).decode("ascii")
                )
            if not record.get("password"):
                raise ValueError("Falta la contraseña de la cuenta alternativa")
            record.update({
                "node_id": _text(changes.get("node_id"), "el agente"),
                "login": _text(changes.get("login"), "el login"),
                "server": _text(changes.get("server"), "el servidor"),
                "updated_at": utc_now(),
            })
            self.records[entry_key(node_id, account_key)] = record
            save_json(self.path, self.records)
            return self.public(node_id, account_key)

    def remove(self, node_id: str, account_key: str) -> dict[str, Any]:
        """Quita el desvío: la tarjeta vuelve a sondear la cuenta del perfil."""
        with self.lock:
            if self.records.pop(entry_key(node_id, account_key), None) is not None:
                save_json(self.path, self.records)
            return self.public(node_id, account_key)


@dataclass(frozen=True)
class Reading:
    """Una cuenta que un agente tiene que leer, y de quién es la tarjeta.

    `owner_node_id` y `account_key` son la tarjeta —y la serie del archivo—;
    `login`, `server` y `password` son con qué se entra. Sin desvío los dos
    lados coinciden, que es el caso de siempre.
    """

    reader_node_id: str
    owner_node_id: str
    account_key: str
    account: dict[str, Any]
    login: str
    server: str
    password: str
    alternate: bool


def _reading(
    server: Any, store: LiveAccountAlternateStore, owner_node_id: str,
    account: dict[str, Any], reader_node_id: str,
) -> Reading | None:
    """Qué tiene que hacer este agente con esa tarjeta, si es que algo."""
    account_key = str(account["account_key"])
    desired = store.credentials(owner_node_id, account_key)
    if desired:
        if (desired["node_id"] or owner_node_id) != reader_node_id:
            return None
        if not desired["password"]:
            # Desviada pero ilegible. No se lee nada antes que leer la cuenta
            # que la tarjeta ya no usa: la portada la enseña sin sondear, que
            # es visible, y un número de otra cuenta no lo sería.
            return None
        return Reading(
            reader_node_id, owner_node_id, account_key, account,
            desired["login"], desired["server"], desired["password"], True,
        )
    if owner_node_id != reader_node_id or account["block_reason"]:
        return None
    if not account["login"] or not account["server"]:
        return None
    password = live_account_inventory.account_password(server, owner_node_id, account)
    return Reading(
        reader_node_id, owner_node_id, account_key, account,
        account["login"], account["server"], password, False,
    ) if password else None


def readings(server: Any, reader_node_id: str) -> list[Reading]:
    """Todo lo que ese agente tiene que leer, sea de quien sea la tarjeta.

    Sin ninguna tarjeta desviada no se mira más que el propio nodo: es el caso
    normal, y recorrer los demás sólo para no encontrar nada haría que cada
    vuelta del cron leyera el estado de todos los agentes.
    """
    store = server.live_account_alternates
    nodes = (
        [str(node.get("id") or "") for node in server.nodes]
        if store.any_configured() else [reader_node_id]
    )
    found: list[Reading] = []
    for owner_node_id in nodes:
        if not owner_node_id:
            continue
        for account in live_account_inventory.node_accounts(server, owner_node_id):
            reading = _reading(server, store, owner_node_id, account, reader_node_id)
            if reading is not None:
                found.append(reading)
    return found


def payload(found: list[Reading]) -> list[dict[str, str]]:
    """Lo que viaja al nodo: la cuenta y su contraseña, nada más.

    La contraseña se descifra al construir esto y no se guarda en ningún sitio
    nuestro, igual que en el lanzamiento de una auditoría. La clave que viaja
    es la de la **tarjeta**, no la de la cuenta alternativa: es con la que el
    agente devuelve su informe y con la que se archiva.
    """
    return [
        {
            "account_key": reading.account_key, "login": reading.login,
            "server": reading.server, "password": reading.password,
        }
        for reading in found
    ]


def routed(server: Any, reader_node_id: str) -> dict[str, Reading]:
    """De quién es cada cuenta que ese agente lee, por clave de tarjeta.

    Es lo que la recogida necesita para saber dónde archivar: el agente
    devuelve su informe con la clave que se le mandó, que es la de la
    tarjeta, y de ahí sale su dueño.
    """
    return {
        reading.account_key: reading for reading in readings(server, reader_node_id)
    }
