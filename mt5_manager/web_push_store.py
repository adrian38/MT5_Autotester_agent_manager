"""Los dispositivos suscritos a los avisos, en un JSON al lado de la campana.

Una suscripcion es lo que el navegador entrega al pulsar «Activar»: la URL del
servicio de push de ese dispositivo y los dos secretos con los que se cifra el
sobre. No es una credencial del usuario ni sirve para nada mas que mandarle un
aviso, pero es de ese dispositivo: aqui se guarda tal cual y no se comparte.

Vive en el manager y no en el navegador porque el push lo manda el manager:
cuando el calculo del portafolio termina de madrugada no hay ninguna pestana
abierta a la que preguntar a quien avisar.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now
from .web_push_crypto import b64url_decode

MAXIMUM_SUBSCRIPTIONS = 24
"""Cuantos dispositivos se conservan. Cada navegador de cada equipo es uno, y
reinstalar el navegador crea otro: sin techo, el fichero solo crece. Al pasarse
se tira la mas antigua, que es la que ya nadie usa."""

MAXIMUM_FAILURES = 5
"""Fallos de red seguidos antes de dejar de insistir con un dispositivo. Un
404 o un 410 del servicio de push no cuenta aqui: eso es una suscripcion
muerta y se borra en el primer intento."""


def normalize(subscription: Any, label: str = "") -> dict[str, Any]:
    """Comprueba lo que manda el navegador y lo deja como se guarda.

    Las tres comprobaciones son las que evitan el fallo mas caro de depurar:
    una suscripcion a la que el cifrado no se le puede aplicar se guardaria
    igual, y el sintoma seria «ese dispositivo no avisa», sin ningun error.
    """
    if not isinstance(subscription, dict):
        raise ValueError("la suscripcion no es un objeto")
    endpoint = str(subscription.get("endpoint") or "").strip()
    if not endpoint.startswith("https://"):
        raise ValueError("la suscripcion no trae un endpoint https")
    keys = subscription.get("keys")
    keys = keys if isinstance(keys, dict) else {}
    try:
        receiver = b64url_decode(keys.get("p256dh"))
        auth = b64url_decode(keys.get("auth"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"las claves de la suscripcion no son base64url: {exc}")
    if len(receiver) != 65 or receiver[0] != 4:
        raise ValueError("`p256dh` no es un punto P-256 sin comprimir de 65 octetos")
    if len(auth) != 16:
        raise ValueError("`auth` no son 16 octetos")
    return {
        "endpoint": endpoint,
        "p256dh": keys.get("p256dh"),
        "auth": keys.get("auth"),
        "label": str(label or "")[:160],
        "created_at": utc_now(),
        "failures": 0,
        "last_error": "",
        "last_sent_at": "",
    }


class WebPushSubscriptionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.subscriptions: list[dict[str, Any]] = []
        if self.path.is_file():
            try:
                stored = load_json(self.path)
            except ValueError:
                stored = {}
            self.subscriptions = [
                dict(item) for item in (stored.get("subscriptions") or [])
                if isinstance(item, dict) and item.get("endpoint")
            ]

    def _save(self) -> None:
        save_json(self.path, {"subscriptions": self.subscriptions})

    def add(self, subscription: Any, label: str = "") -> dict[str, Any]:
        """Guarda un dispositivo, o repone el que ya estaba.

        El navegador vuelve a suscribirse al rotar la suscripcion o al volver
        a pulsar «Activar», y el endpoint es el mismo: reponerlo en su sitio
        borra los fallos acumulados y no duplica el aviso.
        """
        fresh = normalize(subscription, label)
        with self.lock:
            kept = [
                item for item in self.subscriptions
                if item.get("endpoint") != fresh["endpoint"]
            ]
            self.subscriptions = [*kept, fresh][-MAXIMUM_SUBSCRIPTIONS:]
            self._save()
            return dict(fresh)

    def remove(self, endpoint: str) -> bool:
        with self.lock:
            before = len(self.subscriptions)
            self.subscriptions = [
                item for item in self.subscriptions
                if item.get("endpoint") != str(endpoint or "")
            ]
            gone = len(self.subscriptions) != before
            if gone:
                self._save()
            return gone

    def all(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(item) for item in self.subscriptions]

    def record(self, endpoint: str, error: str = "") -> None:
        """Anota como fue el ultimo envio a un dispositivo.

        Sin esto, un dispositivo que ya no existe se reintenta para siempre y
        cada aviso tarda su tiempo de espera de red. Con el techo de fallos,
        deja de molestar y la campana puede decir por que.
        """
        with self.lock:
            for item in self.subscriptions:
                if item.get("endpoint") != endpoint:
                    continue
                item["last_error"] = str(error or "")[:300]
                item["failures"] = int(item.get("failures") or 0) + 1 if error else 0
                if not error:
                    item["last_sent_at"] = utc_now()
            self._save()

    def active(self) -> list[dict[str, Any]]:
        """Los que todavia vale la pena intentar."""
        return [
            item for item in self.all()
            if int(item.get("failures") or 0) < MAXIMUM_FAILURES
        ]

    def state(self) -> dict[str, Any]:
        """Lo que la interfaz ensena: cuantos hay y como les fue, sin secretos.

        Las claves de cada suscripcion no salen de aqui. No son una
        credencial del usuario, pero tampoco hacen falta en el navegador: ya
        las tiene el dispositivo que las genero.
        """
        items = self.all()
        return {
            "devices": [
                {
                    "label": item.get("label") or "dispositivo",
                    "created_at": item.get("created_at") or "",
                    "last_sent_at": item.get("last_sent_at") or "",
                    "failures": int(item.get("failures") or 0),
                    "last_error": item.get("last_error") or "",
                }
                for item in items
            ],
            "total": len(items),
            "active": len([
                item for item in items
                if int(item.get("failures") or 0) < MAXIMUM_FAILURES
            ]),
        }
