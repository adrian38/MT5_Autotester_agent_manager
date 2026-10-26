"""Mandar el aviso al dispositivo aunque no haya ninguna pestana abierta.

Esto es lo que la campana no podia hacer. La API `Notification` del navegador
sirve mientras el manager esta abierto en una pestana con el script cargado:
en el equipo de delante vale, y en un movil no, porque el navegador congela
los temporizadores de una pestana en segundo plano y el sondeo de 60 s no corre.
Un calculo de portafolio dura horas, asi que el aviso que importa es justo el
que nadie esta mirando.

El reparto es: el manager firma (`web_push_vapid`), cifra (`web_push_crypto`) y
entrega al servicio de push del navegador, que despierta al service worker del
dispositivo. Nada de esto necesita que el dispositivo sea alcanzable desde el
manager, que es lo que hace que funcione en el movil fuera de casa.

Nunca deja escapar una excepcion hacia quien anota el aviso. `announce` se
llama desde el mismo `try` del trabajador que calcula el portafolio y desde el
vigilante del cron: un fallo de red al entregar el push marcaria como fallido
un calculo que habia terminado bien. Es la misma regla que `_announce_job` de
`portfolio_coordinator_core`.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import web_push_crypto
from .web_push_store import WebPushSubscriptionStore
from .web_push_vapid import VapidKey

TIMEOUT = 15
"""Espera por dispositivo. Corre en su propio hilo, asi que no retiene a nadie,
pero un servicio de push que no contesta no puede quedarse colgado."""

TTL = 86400
"""Cuanto guarda el aviso el servicio de push si el dispositivo esta apagado.
Un dia: el aviso de la auditoria del sabado por la noche sigue valiendo el
domingo, y mas alla ya esta en la campana."""

MAXIMUM_BODY = 2800
"""Techo del texto que viaja dentro del sobre. Los servicios de push aceptan
4 KB de cuerpo cifrado y el sobre anade unos 100 octetos; el resto del margen
es para no acercarse al limite, que se responde con 413."""

DEAD_STATUSES = (404, 410)
"""Suscripcion que ya no existe: el navegador se desinstalo, se reinstalo o
revoco el permiso. No es un fallo de red y no se reintenta: se borra."""

SKIPPED_KINDS = frozenset({"audit_started"})
"""Lo que no merece despertar un movil. Mismo criterio que el de la pagina en
`live_audit_schedule.js`: interesa que algo termino o fallo, no que arranco."""


def default_subject(domain: str = "", email: str = "") -> str:
    """Quien dice ser el manager ante el servicio de push.

    El RFC pide un `mailto:` o una URL por la que reclamar si los avisos
    molestan. Apple rechaza el JWT con 400 si no es una de las dos, asi que se
    prefiere el correo que ya hay en `.env` para Let's Encrypt y, si no lo
    hay, el dominio publico del proxy.
    """
    address = str(email or "").strip()
    if address:
        return f"mailto:{address}"
    host = str(domain or "").strip()
    return f"https://{host}" if host else "mailto:mt5-manager@localhost"


def message_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """El aviso de la campana, reducido a lo que se ve en el dispositivo.

    `tag` es lo que evita el aviso doble: si la pestana abierta ya enseno este
    mismo aviso por `Notification`, el push lo reemplaza en vez de sumarse.
    """
    identifier = int(event.get("id") or 0)
    return {
        "id": identifier,
        "tag": f"cron-{identifier}",
        "title": str(event.get("title") or "Aviso del manager"),
        "body": str(event.get("message") or "")[:MAXIMUM_BODY],
        "level": str(event.get("level") or "info"),
        "link": str(event.get("link") or "") or _audit_link(event),
        "unread": max(0, int(event.get("unread") or 0)),
    }


def _audit_link(event: dict[str, Any]) -> str:
    node = str(event.get("node_id") or "")
    audit = str(event.get("audit_id") or "")
    if not node or not audit:
        return "/"
    return (
        f"/live_audit_analysis.html?node={urllib.parse.quote(node)}"
        f"&audit={urllib.parse.quote(audit)}"
    )


class WebPushService:
    """La clave, los dispositivos y el envio, en una pieza que no revienta."""

    def __init__(
        self, key_path: str | Path, store_path: str | Path, subject: str = "",
    ) -> None:
        self.key_path = Path(key_path)
        self.store = WebPushSubscriptionStore(store_path)
        self.subject = subject or default_subject()
        self._key: VapidKey | None = None
        self._failure = ""

    @property
    def key(self) -> VapidKey | None:
        """La clave VAPID, creada la primera vez que hace falta.

        Perezosa a proposito: crear una clave al arrancar el manager escribiria
        en `runtime/` incluso en un equipo que nunca va a usar los avisos, y un
        fichero ilegible dejaria el manager sin arrancar. Aqui el fallo se
        cuenta en `config()` y lo demas sigue funcionando.
        """
        if self._key is None and not self._failure:
            try:
                self._key = VapidKey(self.key_path)
            except (ValueError, OSError) as exc:
                self._failure = str(exc)
        return self._key

    def config(self) -> dict[str, Any]:
        """Lo que el navegador necesita para suscribirse, y como va la cosa."""
        key = self.key
        return {
            "public_key": key.public_key if key else "",
            "subject": self.subject,
            "error": self._failure,
            **self.store.state(),
        }

    def subscribe(self, payload: Any, label: str = "") -> dict[str, Any]:
        self.store.add(payload, label)
        return self.config()

    def unsubscribe(self, endpoint: str) -> dict[str, Any]:
        self.store.remove(endpoint)
        return self.config()

    def _post(self, subscription: dict[str, Any], message: dict[str, Any]) -> int:
        key = self.key
        if key is None:
            raise ValueError(self._failure or "sin clave VAPID")
        endpoint = str(subscription.get("endpoint") or "")
        body = web_push_crypto.encrypt(
            json.dumps(message, ensure_ascii=False).encode("utf-8"),
            web_push_crypto.b64url_decode(subscription.get("p256dh")),
            web_push_crypto.b64url_decode(subscription.get("auth")),
        )
        request = urllib.request.Request(endpoint, data=body, method="POST")
        request.add_header("Authorization", key.authorization(endpoint, self.subject))
        request.add_header("Content-Encoding", "aes128gcm")
        request.add_header("Content-Type", "application/octet-stream")
        request.add_header("Content-Length", str(len(body)))
        request.add_header("TTL", str(TTL))
        request.add_header("Urgency", "normal")
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return int(response.status or 0)

    def deliver(self, message: dict[str, Any]) -> dict[str, Any]:
        """Manda el aviso a cada dispositivo vivo. No lanza nada.

        Un dispositivo que el servicio de push ya no reconoce se borra en el
        primer intento; lo demas se anota y se reintenta en el aviso siguiente.
        """
        sent, dropped, failed = 0, 0, {}
        for subscription in self.store.active():
            endpoint = str(subscription.get("endpoint") or "")
            try:
                self._post(subscription, message)
            except urllib.error.HTTPError as exc:
                if exc.code in DEAD_STATUSES:
                    self.store.remove(endpoint)
                    dropped += 1
                    continue
                failed[endpoint] = f"HTTP {exc.code}"
                self.store.record(endpoint, f"HTTP {exc.code} {exc.reason}")
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
                failed[endpoint] = str(exc)
                self.store.record(endpoint, str(exc))
            else:
                sent += 1
                self.store.record(endpoint)
        return {"sent": sent, "dropped": dropped, "failed": failed}

    def announce(self, event: dict[str, Any]) -> None:
        """El enganche de la campana: entrega en segundo plano y calla.

        Se llama con el candado del diario cogido, asi que no puede esperar a
        la red: el hilo deja que `add()` vuelva en el acto.
        """
        try:
            if str(event.get("kind") or "") in SKIPPED_KINDS:
                return
            if not self.store.active():
                return
            message = message_from_event(event)
            threading.Thread(
                target=self._deliver_quietly, args=(message,),
                name=f"web-push-{message['id']}", daemon=True,
            ).start()
        except Exception as exc:  # noqa: BLE001 - nunca hacia quien anota el aviso
            print(f"[web-push] aviso no entregado: {exc}", flush=True)

    def _deliver_quietly(self, message: dict[str, Any]) -> None:
        try:
            summary = self.deliver(message)
        except Exception as exc:  # noqa: BLE001 - es la cima de su propio hilo
            print(f"[web-push] entrega fallida: {exc}", flush=True)
            return
        if summary["failed"] or summary["dropped"]:
            print(f"[web-push] {summary}", flush=True)
