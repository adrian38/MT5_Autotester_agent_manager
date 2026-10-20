"""La identidad del manager ante el servicio de push: VAPID (RFC 8292).

El servicio de push de cada navegador —FCM para Chrome, Mozilla para Firefox,
Apple para Safari— no acepta un push anonimo: exige un JWT firmado con una
clave que el navegador vio al suscribirse. Esa clave es la identidad del
manager, **se conserva** y es la misma para todos los dispositivos.

No confundirla con la clave del sobre (`web_push_crypto`), que es de un solo
mensaje y se tira. Esta se guarda en `runtime/` y perderla invalida todas las
suscripciones: el navegador rechazaria el push por venir firmado por otro, y
cada dispositivo tendria que volver a suscribirse. Por eso se crea una vez y
no se regenera sola.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as asymmetric_utils

from .web_push_crypto import b64url, uncompressed_point

TOKEN_LIFETIME = 12 * 3600
"""Validez del JWT. El RFC prohibe pasar de 24 h; doce dejan margen de sobra
para un reloj desajustado sin acercarse al limite."""


def _segment(value: dict) -> bytes:
    return b64url(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    ).encode("ascii")


def audience(endpoint: str) -> str:
    """El origen del endpoint, que es lo que el `aud` del JWT tiene que decir.

    Un token con el `aud` de otro servicio se rechaza con 401, asi que se
    firma uno por origen y no uno por mensaje.
    """
    parts = urllib.parse.urlsplit(str(endpoint or ""))
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"endpoint de push no valido: {endpoint!r}")
    return f"{parts.scheme}://{parts.netloc}"


class VapidKey:
    """La clave de firma del manager, creada la primera vez y conservada."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._private = self._load() or self._create()

    def _load(self) -> ec.EllipticCurvePrivateKey | None:
        if not self.path.is_file():
            return None
        try:
            key = serialization.load_pem_private_key(self.path.read_bytes(), None)
        except (ValueError, TypeError):
            # Un fichero ilegible no se sobrescribe: regenerar la clave en
            # silencio dejaria sin avisos a todos los dispositivos y nadie
            # sabria por que. Se dice y se para.
            raise ValueError(
                f"la clave VAPID de {self.path} no se puede leer. Borrala a mano si "
                "quieres una nueva: todos los dispositivos tendran que volver a "
                "activar los avisos.",
            )
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError(f"la clave de {self.path} no es de curva eliptica")
        return key

    def _create(self) -> ec.EllipticCurvePrivateKey:
        key = ec.generate_private_key(ec.SECP256R1())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        return key

    @property
    def public_key(self) -> str:
        """La clave publica en base64url: el `applicationServerKey` del navegador."""
        return b64url(uncompressed_point(self._private.public_key()))

    def _token(self, endpoint: str, subject: str) -> bytes:
        claims = {
            "aud": audience(endpoint),
            "exp": int(time.time()) + TOKEN_LIFETIME,
            "sub": subject,
        }
        signed = (
            _segment({"typ": "JWT", "alg": "ES256"}) + b"." + _segment(claims)
        )
        # ES256 quiere la firma como `r || s` de 32 octetos cada uno;
        # `cryptography` la da en DER, que es mas larga y lleva etiquetas.
        raw = self._private.sign(signed, ec.ECDSA(hashes.SHA256()))
        numbers = asymmetric_utils.decode_dss_signature(raw)
        signature = b"".join(value.to_bytes(32, "big") for value in numbers)
        return signed + b"." + b64url(signature).encode("ascii")

    def authorization(self, endpoint: str, subject: str) -> str:
        """La cabecera `Authorization` completa del esquema vapid."""
        token = self._token(endpoint, subject).decode("ascii")
        return f"vapid t={token},k={self.public_key}"
