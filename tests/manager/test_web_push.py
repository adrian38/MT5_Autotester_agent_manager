"""Los avisos que llegan con el navegador cerrado, de la campana al servicio.

Lo que estas pruebas vigilan no es el formato del JSON: es que el aviso salga
del manager en condiciones de ser entregado. Todo el camino falla en silencio
—un JWT mal firmado se responde con un 401 que nadie mira, y un cuerpo mal
cifrado lo descarta el dispositivo sin decir nada—, asi que lo unico que
distingue «funciona» de «no avisa» es comprobar aqui cada pieza.

La prueba fuerte es `test_the_device_can_decrypt_what_the_manager_sent`: monta
el dispositivo del otro lado, descifra el cuerpo que salio por la red y compara
el texto. Si eso pasa, el sobre esta bien hecho de verdad.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils as asymmetric_utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mt5_manager import manager_push_routes, web_push, web_push_crypto
from mt5_manager.live_audit_notifications import LiveAuditNotificationStore
from mt5_manager.web_push_store import (
    MAXIMUM_FAILURES,
    MAXIMUM_SUBSCRIPTIONS,
    WebPushSubscriptionStore,
    normalize,
)
from mt5_manager.web_push_vapid import VapidKey


def device(endpoint: str = "https://push.example.net/d/1") -> tuple[dict, ec.EllipticCurvePrivateKey, bytes]:
    """Una suscripcion como la que entrega el navegador, con su clave privada."""
    private = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    subscription = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": web_push_crypto.b64url(
                web_push_crypto.uncompressed_point(private.public_key()),
            ),
            "auth": web_push_crypto.b64url(auth),
        },
    }
    return subscription, private, auth


def decrypt(body: bytes, private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """Lo que hace el dispositivo: deshacer RFC 8291 con su clave."""
    salt, sender = body[:16], body[21:86]
    shared = private.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender),
    )
    receiver = web_push_crypto.uncompressed_point(private.public_key())
    material = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=auth,
        info=b"WebPush: info\x00" + receiver + sender,
    ).derive(shared)
    derive = lambda info, size: HKDF(  # noqa: E731 - dos claves de la misma forma
        algorithm=hashes.SHA256(), length=size, salt=salt, info=info,
    ).derive(material)
    plain = AESGCM(derive(b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
        derive(b"Content-Encoding: nonce\x00", 12), body[86:], None,
    )
    return plain.rstrip(b"\x02")


class _Response:
    def __init__(self, status: int = 201) -> None:
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exception) -> bool:
        return False


def _http_error(case: unittest.TestCase, code: int) -> urllib.error.HTTPError:
    """La respuesta de un servicio de push que rechaza el aviso.

    Se cierra al terminar porque `HTTPError` abre un temporal pase lo que pase
    -tambien con `fp` dado- y sin cerrarlo cada prueba deja un
    `ResourceWarning` por medio de la salida.
    """
    error = urllib.error.HTTPError(
        "https://push.example.net/d/1", code, "no", {}, io.BytesIO(b""),
    )
    case.addCleanup(error.close)
    return error


class VapidKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "web_push_key.pem"

    def test_the_key_is_created_once_and_survives_a_restart(self) -> None:
        """Regenerarla dejaria sin avisos a todos los dispositivos de golpe.

        El navegador guarda la clave publica al suscribirse y el servicio de
        push rechaza con 403 un aviso firmado por otra.
        """
        first = VapidKey(self.path).public_key
        self.assertTrue(self.path.is_file())
        self.assertEqual(first, VapidKey(self.path).public_key)
        self.assertEqual(65, len(web_push_crypto.b64url_decode(first)))
        self.assertEqual(4, web_push_crypto.b64url_decode(first)[0])

    def test_an_unreadable_key_is_reported_instead_of_replaced(self) -> None:
        self.path.write_text("esto no es una clave", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            VapidKey(self.path)
        self.assertIn("no se puede leer", str(caught.exception))
        # Y sigue ahi: el usuario puede decidir, y nadie pierde los avisos sin
        # enterarse.
        self.assertEqual("esto no es una clave", self.path.read_text(encoding="utf-8"))

    def test_the_header_carries_a_token_the_push_service_can_verify(self) -> None:
        key = VapidKey(self.path)
        header = key.authorization(
            "https://push.example.net/d/1?x=1", "mailto:yo@example.com",
        )
        self.assertTrue(header.startswith("vapid t="))
        token, sent_key = header[len("vapid t="):].split(",k=")
        self.assertEqual(key.public_key, sent_key)
        head, claims, signature = token.split(".")
        self.assertEqual({"typ": "JWT", "alg": "ES256"}, json.loads(
            web_push_crypto.b64url_decode(head),
        ))
        payload = json.loads(web_push_crypto.b64url_decode(claims))
        # El `aud` es el origen del endpoint, sin ruta ni query: un token por
        # servicio, no uno por mensaje.
        self.assertEqual("https://push.example.net", payload["aud"])
        self.assertEqual("mailto:yo@example.com", payload["sub"])
        self.assertLess(payload["exp"] - time.time(), 24 * 3600)
        self.assertGreater(payload["exp"] - time.time(), 3600)
        self._verify(sent_key, f"{head}.{claims}".encode("ascii"), signature)

    def _verify(self, public_key: str, signed: bytes, signature: str) -> None:
        """La firma, comprobada como la comprueba el servicio de push."""
        raw = web_push_crypto.b64url_decode(signature)
        self.assertEqual(64, len(raw))  # `r || s`, no DER
        ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), web_push_crypto.b64url_decode(public_key),
        ).verify(
            asymmetric_utils.encode_dss_signature(
                int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"),
            ),
            signed, ec.ECDSA(hashes.SHA256()),
        )


class SubscriptionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = WebPushSubscriptionStore(Path(temp.name) / "subs.json")

    def test_a_subscription_that_cannot_be_encrypted_is_refused_on_arrival(self) -> None:
        """Guardarla seria callar: el sintoma vendria luego, sin ningun error."""
        subscription, _private, _auth = device()
        for broken, reason in (
            ("no es un objeto", "no es un objeto"),
            ({**subscription, "endpoint": "http://push.example.net/d"}, "endpoint https"),
            ({**subscription, "keys": {**subscription["keys"], "p256dh": "AAAA"}}, "65 octetos"),
            ({**subscription, "keys": {**subscription["keys"], "auth": "AAAA"}}, "16 octetos"),
            ({**subscription, "keys": {}}, "octetos"),
        ):
            with self.assertRaises(ValueError) as caught:
                normalize(broken)
            self.assertIn(reason, str(caught.exception))

    def test_the_same_device_is_replaced_and_not_duplicated(self) -> None:
        subscription, _private, _auth = device()
        self.store.add(subscription, "Chrome")
        self.store.record(subscription["endpoint"], "HTTP 500")
        self.store.add(subscription, "Chrome")
        self.assertEqual(1, len(self.store.all()))
        # Volver a activarlo borra los fallos: es el gesto de quien dice que
        # ese dispositivo vuelve a valer.
        self.assertEqual(0, self.store.all()[0]["failures"])

    def test_a_device_that_keeps_failing_stops_being_tried(self) -> None:
        subscription, _private, _auth = device()
        self.store.add(subscription)
        for _ in range(MAXIMUM_FAILURES):
            self.store.record(subscription["endpoint"], "sin red")
        self.assertEqual([], self.store.active())
        self.assertEqual(1, self.store.state()["total"])
        self.assertEqual(0, self.store.state()["active"])
        self.assertIn("sin red", self.store.state()["devices"][0]["last_error"])

    def test_the_oldest_device_falls_off_the_end(self) -> None:
        for index in range(MAXIMUM_SUBSCRIPTIONS + 2):
            self.store.add(device(f"https://push.example.net/d/{index}")[0])
        endpoints = [item["endpoint"] for item in self.store.all()]
        self.assertEqual(MAXIMUM_SUBSCRIPTIONS, len(endpoints))
        self.assertNotIn("https://push.example.net/d/0", endpoints)

    def test_what_the_screen_sees_has_no_keys_in_it(self) -> None:
        self.store.add(device()[0], "Chrome en el portatil")
        state = self.store.state()
        self.assertEqual("Chrome en el portatil", state["devices"][0]["label"])
        self.assertNotIn("p256dh", json.dumps(state))
        self.assertNotIn("auth", json.dumps(state))

    def test_a_reload_finds_the_same_devices(self) -> None:
        self.store.add(device()[0], "Firefox")
        reloaded = WebPushSubscriptionStore(self.store.path)
        self.assertEqual(1, reloaded.state()["total"])


class DeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.service = web_push.WebPushService(
            root / "key.pem", root / "subs.json", "mailto:yo@example.com",
        )

    def test_the_device_can_decrypt_what_the_manager_sent(self) -> None:
        """De punta a punta: lo que sale por la red, abierto con la otra clave."""
        subscription, private, auth = device()
        self.service.subscribe(subscription, "Chrome")
        sent = []
        with mock.patch("urllib.request.urlopen", side_effect=lambda request, timeout=0: (
            sent.append(request) or _Response()
        )):
            summary = self.service.deliver(web_push.message_from_event({
                "id": 7, "level": "success", "title": "Portafolio generado",
                "message": "3 propuestas listas", "link": "/portfolios.html",
            }))

        self.assertEqual({"sent": 1, "dropped": 0, "failed": {}}, summary)
        request = sent[0]
        self.assertEqual("aes128gcm", request.get_header("Content-encoding"))
        self.assertTrue(request.get_header("Authorization").startswith("vapid t="))
        self.assertEqual(str(web_push.TTL), request.get_header("Ttl"))
        self.assertEqual(
            {
                "id": 7, "tag": "cron-7", "title": "Portafolio generado",
                "body": "3 propuestas listas", "level": "success",
                "link": "/portfolios.html",
                "unread": 0,
            },
            json.loads(decrypt(request.data, private, auth)),
        )

    def test_a_subscription_the_service_no_longer_knows_is_dropped(self) -> None:
        """404 y 410 no son un fallo de red: ese dispositivo ya no existe."""
        for code in (404, 410):
            self.service.subscribe(device()[0])
            with mock.patch("urllib.request.urlopen", side_effect=_http_error(self, code)):
                summary = self.service.deliver({"title": "x", "body": ""})
            self.assertEqual(1, summary["dropped"])
            self.assertEqual([], self.service.store.all())

    def test_a_failure_is_remembered_but_the_device_stays(self) -> None:
        subscription, _private, _auth = device()
        self.service.subscribe(subscription)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(self, 500)):
            summary = self.service.deliver({"title": "x", "body": ""})
        self.assertEqual(0, summary["sent"])
        self.assertEqual({subscription["endpoint"]: "HTTP 500"}, summary["failed"])
        self.assertEqual(1, self.service.store.all()[0]["failures"])

    def test_one_broken_device_does_not_stop_the_others(self) -> None:
        good, _private, _auth = device("https://push.example.net/d/bien")
        bad, _ignored, _secret = device("https://push.example.net/d/mal")
        self.service.subscribe(good)
        self.service.subscribe(bad)

        def answer(request, timeout=0):
            if request.full_url.endswith("mal"):
                raise urllib.error.URLError("sin red")
            return _Response()

        with mock.patch("urllib.request.urlopen", side_effect=answer):
            summary = self.service.deliver({"title": "x", "body": ""})
        self.assertEqual(1, summary["sent"])
        self.assertEqual(["https://push.example.net/d/mal"], list(summary["failed"]))

    def test_an_audit_that_only_started_does_not_wake_up_a_phone(self) -> None:
        self.service.subscribe(device()[0])
        with mock.patch.object(self.service, "deliver") as delivered:
            self.service.announce({"id": 1, "kind": "audit_started", "title": "Lanzada"})
        delivered.assert_not_called()

    def test_announcing_never_raises_towards_whoever_wrote_the_notice(self) -> None:
        """`add` se llama dentro del `try` del trabajador del portafolio.

        Una excepcion aqui marcaria como fallido un calculo que termino bien.
        """
        self.service.subscribe(device()[0])
        with mock.patch.object(
            self.service.store, "active", side_effect=RuntimeError("fichero roto"),
        ):
            self.service.announce({"id": 1, "title": "Terminado"})

    def test_the_link_of_an_audit_is_rebuilt_when_the_notice_has_none(self) -> None:
        message = web_push.message_from_event({
            "id": 3, "title": "Auditoría terminada", "node_id": "nodo a",
            "audit_id": "real/1",
        })
        self.assertEqual(
            "/live_audit_analysis.html?node=nodo%20a&audit=real/1", message["link"],
        )
        self.assertEqual("/", web_push.message_from_event({"id": 4})["link"])

    def test_the_subject_prefers_the_mail_that_is_already_configured(self) -> None:
        """Apple rechaza el JWT si no es un `mailto:` o una URL de verdad."""
        self.assertEqual(
            "mailto:yo@example.com", web_push.default_subject("casa.ddns.net", "yo@example.com"),
        )
        self.assertEqual("https://casa.ddns.net", web_push.default_subject("casa.ddns.net"))
        self.assertTrue(web_push.default_subject().startswith("mailto:"))


class BellHandoverTest(unittest.TestCase):
    """La campana es el unico sitio donde se engancha el aviso al dispositivo."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = LiveAuditNotificationStore(Path(temp.name) / "avisos.json")

    def test_every_notice_reaches_the_device_without_touching_who_writes_it(self) -> None:
        seen = []
        self.store.announce = seen.append
        self.store.add("success", "Portafolio generado", "3 propuestas", kind="generate")
        self.assertEqual(["Portafolio generado"], [event["title"] for event in seen])
        self.assertEqual(1, seen[0]["id"])

    def test_the_push_knows_the_bell_count_for_the_installed_app_badge(self) -> None:
        seen = []
        self.store.announce = seen.append
        self.store.add("success", "Primero")
        self.store.add("warning", "Segundo")
        self.assertEqual([1, 2], [event["unread"] for event in seen])

    def test_a_push_that_explodes_does_not_break_the_diary(self) -> None:
        def explode(_event):
            raise RuntimeError("sin clave")

        self.store.announce = explode
        event = self.store.add("error", "Auditoría fallida")
        self.assertEqual(1, event["id"])
        self.assertEqual(1, self.store.state()["unread"])


class RouteHandler:
    def __init__(self, service, body=None, user_agent="Chrome") -> None:
        self.server = SimpleNamespace(web_push=service)
        self.body = body or {}
        self.headers = {"User-Agent": user_agent}
        self.sent = None

    def _body(self) -> object:
        return self.body

    def _send_json(self, status: int, payload: object) -> None:
        self.sent = (status, payload)


class PushRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.service = web_push.WebPushService(root / "key.pem", root / "subs.json")

    def post(self, path: str, body=None) -> tuple:
        handler = RouteHandler(self.service, body)
        self.assertTrue(manager_push_routes.handle_post(
            handler, SimpleNamespace(path=path), [],
        ))
        return handler.sent

    def test_the_browser_gets_the_public_key_it_needs_to_subscribe(self) -> None:
        handler = RouteHandler(self.service)
        self.assertTrue(manager_push_routes.handle_get(
            handler, SimpleNamespace(path=manager_push_routes.CONFIG_PATH), [],
        ))
        status, payload = handler.sent
        self.assertEqual(200, status)
        self.assertEqual(self.service.key.public_key, payload["public_key"])
        self.assertEqual(0, payload["total"])

    def test_subscribing_and_unsubscribing_moves_the_device_in_and_out(self) -> None:
        subscription, _private, _auth = device()
        status, payload = self.post(
            manager_push_routes.SUBSCRIBE_PATH, {"subscription": subscription},
        )
        self.assertEqual((200, 1), (status, payload["total"]))
        self.assertEqual("Chrome", payload["devices"][0]["label"])

        status, payload = self.post(
            manager_push_routes.UNSUBSCRIBE_PATH, {"endpoint": subscription["endpoint"]},
        )
        self.assertEqual((200, 0), (status, payload["total"]))

    def test_a_subscription_the_browser_sends_without_a_wrapper_also_works(self) -> None:
        """Lo que `pushManager.subscribe()` devuelve, tal cual."""
        status, payload = self.post(manager_push_routes.SUBSCRIBE_PATH, device()[0])
        self.assertEqual((200, 1), (status, payload["total"]))

    def test_a_broken_subscription_answers_400_and_says_what_falta(self) -> None:
        status, payload = self.post(
            manager_push_routes.SUBSCRIBE_PATH, {"subscription": {"endpoint": "nope"}},
        )
        self.assertEqual(400, status)
        self.assertIn("endpoint https", payload["error"])

    def test_the_test_push_says_how_it_went_on_every_device(self) -> None:
        """Es el unico punto del camino que el usuario puede comprobar."""
        self.service.subscribe(device()[0])
        with mock.patch("urllib.request.urlopen", return_value=_Response()):
            status, payload = self.post(manager_push_routes.TEST_PATH)
        self.assertEqual((200, 1), (status, payload["sent"]))

        with mock.patch("urllib.request.urlopen", side_effect=_http_error(self, 500)):
            status, payload = self.post(manager_push_routes.TEST_PATH)
        self.assertEqual(0, payload["sent"])
        self.assertEqual(["HTTP 500"], list(payload["failed"].values()))

    def test_a_manager_without_push_answers_instead_of_breaking(self) -> None:
        handler = RouteHandler(None)
        self.assertTrue(manager_push_routes.handle_get(
            handler, SimpleNamespace(path=manager_push_routes.CONFIG_PATH), [],
        ))
        self.assertEqual(400, handler.sent[0])

    def test_other_paths_are_left_to_the_next_module(self) -> None:
        handler = RouteHandler(self.service)
        self.assertFalse(manager_push_routes.handle_get(
            handler, SimpleNamespace(path="/api/nodes"), [],
        ))
        self.assertFalse(manager_push_routes.handle_post(
            handler, SimpleNamespace(path="/api/nodes"), [],
        ))


if __name__ == "__main__":
    unittest.main()
