"""El sobre cifrado de un aviso Web Push: `aes128gcm` de RFC 8291.

Un push no viaja en claro. El navegador entrega al manager dos secretos del
dispositivo —`p256dh`, su clave publica, y `auth`, un secreto compartido— y el
servicio de push (FCM, Mozilla, Apple) reenvia el cuerpo sin poder leerlo.

Se cifra el cuerpo en vez de mandar un push vacio a proposito. Un push sin
payload obliga al service worker a pedir el aviso al manager para saber que
ensenar, y eso falla justo cuando mas importa: sin red, o con el movil en otra
red que no llega al manager. Con el texto dentro, el aviso sale igual.

**El unico modo de saber que esto esta bien es el vector del RFC.** Un error de
derivacion no da ningun error aqui: produce un cuerpo que el dispositivo
descarta en silencio, y no hay traza de eso en ningun sitio. Por eso
`tests/test_web_push_crypto.py` reproduce el ejemplo de la seccion 5 del RFC
8291 campo a campo, y por eso `salt` y `as_private` se pueden inyectar: sin
eso, el cifrado seria irreproducible y no habria nada que comparar.
"""
from __future__ import annotations

import base64
import os
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
"""Tamano de registro del unico registro que mandamos. El aviso mas largo cabe
de sobra: los servicios de push aceptan 4 KB de cuerpo."""

PADDING_DELIMITER = b"\x02"
"""RFC 8188: el ultimo registro termina en 0x02, los intermedios en 0x01. Solo
hay uno, asi que siempre es el ultimo."""


def b64url(data: bytes) -> str:
    """Base64url sin relleno, que es como viaja todo en Web Push."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    """Lo contrario, tolerante con el relleno que el navegador no manda.

    Chrome entrega `p256dh` y `auth` sin `=` y Firefox los manda con el, asi
    que una suscripcion valida llega de las dos formas.
    """
    raw = str(text or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(raw + "=" * (-len(raw) % 4))


def _hkdf(salt: bytes, material: bytes, info: bytes, length: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=salt, info=info,
    ).derive(material)


def uncompressed_point(key: ec.EllipticCurvePublicKey) -> bytes:
    """Los 65 octetos `0x04 || X || Y`, la forma que exige Web Push."""
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )


def encrypt(
    plaintext: bytes,
    receiver_public: bytes,
    auth_secret: bytes,
    *,
    salt: bytes | None = None,
    sender_private: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """El cuerpo completo de un push: cabecera `aes128gcm` mas ciphertext.

    `receiver_public` es el `p256dh` del navegador ya decodificado y
    `auth_secret` su `auth`. La clave del emisor es de usar y tirar —una por
    mensaje, como pide el RFC— y viaja dentro de la cabecera; no es la clave
    VAPID, que identifica al manager y se conserva.
    """
    salt = salt or os.urandom(16)
    private = sender_private or ec.generate_private_key(ec.SECP256R1())
    sender_public = uncompressed_point(private.public_key())
    shared = private.exchange(
        ec.ECDH(),
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_public),
    )
    # RFC 8291 3.4: el secreto ECDH se combina primero con el `auth` del
    # dispositivo, y la etiqueta lleva las dos claves publicas para que el
    # material derivado sea de esta pareja y de nadie mas.
    material = _hkdf(
        auth_secret, shared,
        b"WebPush: info\x00" + receiver_public + sender_public, 32,
    )
    key = _hkdf(salt, material, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, material, b"Content-Encoding: nonce\x00", 12)
    header = salt + struct.pack("!LB", RECORD_SIZE, len(sender_public)) + sender_public
    return header + AESGCM(key).encrypt(nonce, plaintext + PADDING_DELIMITER, None)
