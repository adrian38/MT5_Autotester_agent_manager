"""El cifrado de un push, contra el vector de prueba del RFC 8291.

Esta prueba es la unica forma de saber que `web_push_crypto` esta bien. Una
derivacion mal hecha no lanza ninguna excepcion: produce un cuerpo que el
dispositivo descarta sin decir nada, y el fallo se ve como «el movil no avisa»,
que es exactamente el sintoma que no se puede depurar.

Los valores son los de la seccion 5 y el apendice A del RFC 8291, con los
espacios de presentacion quitados.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric import ec

from mt5_manager import web_push_crypto

RECEIVER_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx"
    "aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)

SENDER_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"

AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"

SALT = "DGv6ra1nlYgDCS1FRnbzlw"

PLAINTEXT = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"

EXPECTED_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def _sender_key() -> ec.EllipticCurvePrivateKey:
    raw = web_push_crypto.b64url_decode(SENDER_PRIVATE)
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


class WebPushCryptoTest(unittest.TestCase):
    def test_the_rfc_8291_example_comes_out_octet_for_octet(self) -> None:
        body = web_push_crypto.encrypt(
            web_push_crypto.b64url_decode(PLAINTEXT),
            web_push_crypto.b64url_decode(RECEIVER_PUBLIC),
            web_push_crypto.b64url_decode(AUTH_SECRET),
            salt=web_push_crypto.b64url_decode(SALT),
            sender_private=_sender_key(),
        )
        self.assertEqual(EXPECTED_BODY, web_push_crypto.b64url(body))

    def test_the_header_carries_the_salt_the_record_size_and_the_sender_key(self) -> None:
        """RFC 8188: 16 octetos de sal, el tamano de registro y la clave publica."""
        body = web_push_crypto.encrypt(
            b"hola", web_push_crypto.b64url_decode(RECEIVER_PUBLIC),
            web_push_crypto.b64url_decode(AUTH_SECRET),
            salt=web_push_crypto.b64url_decode(SALT),
        )
        self.assertEqual(web_push_crypto.b64url_decode(SALT), body[:16])
        self.assertEqual(b"\x00\x00\x10\x00", body[16:20])
        self.assertEqual(65, body[20])
        self.assertEqual(4, body[21])  # 0x04: punto sin comprimir
        # Cabecera de 86 octetos, el texto, su delimitador y la etiqueta GCM.
        self.assertEqual(86 + len(b"hola") + 1 + 16, len(body))

    def test_a_push_without_an_injected_salt_is_different_every_time(self) -> None:
        """La sal y la clave del emisor son de un solo mensaje.

        Repetirlas reutilizaria clave y nonce de AES-GCM, que es la forma
        clasica de romper el cifrado.
        """
        bodies = {
            web_push_crypto.encrypt(
                b"hola", web_push_crypto.b64url_decode(RECEIVER_PUBLIC),
                web_push_crypto.b64url_decode(AUTH_SECRET),
            )
            for _ in range(3)
        }
        self.assertEqual(3, len(bodies))

    def test_the_keys_of_the_subscription_are_read_with_or_without_padding(self) -> None:
        """Chrome entrega base64url sin relleno; Firefox lo manda con el."""
        self.assertEqual(
            web_push_crypto.b64url_decode(AUTH_SECRET),
            web_push_crypto.b64url_decode(AUTH_SECRET + "=="),
        )


if __name__ == "__main__":
    unittest.main()
