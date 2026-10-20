"""Las rutas de los avisos del dispositivo: clave publica, alta, baja y prueba.

Mismo patron que `manager_schedule_routes`: funciones que reciben el handler.
Van aparte de la campana porque son otra cosa: la campana es el diario que
cualquiera puede leer, y esto es la lista de dispositivos a los que el manager
despierta.

`/test` existe porque es el unico punto del camino que el usuario puede
comprobar. Entre pulsar «Activar» y recibir el aviso de un calculo de horas
hay una clave, un JWT, un cifrado y un servicio de push ajeno: si algo esta
mal, el sintoma llega de madrugada y no dice nada. Esta ruta lo entrega **en
el momento y en sincrono**, y devuelve lo que contesto cada dispositivo.
"""
from __future__ import annotations

import json
from typing import Any

from .web_push import message_from_event

CONFIG_PATH = "/api/web-push/config"

SUBSCRIBE_PATH = "/api/web-push/subscribe"

UNSUBSCRIBE_PATH = "/api/web-push/unsubscribe"

TEST_PATH = "/api/web-push/test"

PATHS = frozenset({CONFIG_PATH, SUBSCRIBE_PATH, UNSUBSCRIBE_PATH, TEST_PATH})


def _service(handler) -> Any:
    service = getattr(handler.server, "web_push", None)
    if service is None:
        raise ValueError("este manager no tiene avisos de dispositivo")
    return service


def _test_message() -> dict[str, Any]:
    """Un aviso de prueba con la forma exacta de uno de verdad.

    Si fuera un payload distinto, la prueba podria pasar con el service worker
    roto para los avisos reales.
    """
    return message_from_event({
        "id": 0, "level": "success", "kind": "web_push_test",
        "title": "Los avisos funcionan",
        "message": (
            "Este es un aviso de prueba del manager. Los de verdad llegan igual "
            "con el navegador cerrado."
        ),
        "link": "/",
    })


def handle_get(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path != CONFIG_PATH:
        return False
    try:
        handler._send_json(200, _service(handler).config())
    except (ValueError, OSError) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True


def handle_post(handler, parsed: Any, parts: list[str]) -> bool:
    if parsed.path not in PATHS:
        return False
    try:
        service = _service(handler)
        if parsed.path == SUBSCRIBE_PATH:
            body = handler._body()
            handler._send_json(200, service.subscribe(
                body.get("subscription") or body,
                handler.headers.get("User-Agent", ""),
            ))
        elif parsed.path == UNSUBSCRIBE_PATH:
            handler._send_json(
                200, service.unsubscribe(str(handler._body().get("endpoint") or "")),
            )
        elif parsed.path == TEST_PATH:
            summary = service.deliver(_test_message())
            handler._send_json(200, {**summary, **service.config()})
        else:
            handler._send_json(404, {"error": "ruta de avisos desconocida"})
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        handler._send_json(400, {"error": str(exc)})
    return True
