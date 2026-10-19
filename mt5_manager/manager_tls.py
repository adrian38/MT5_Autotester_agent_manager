"""TLS opcional del manager: apagado si no hay certificados.

Existe por una razon concreta: el navegador solo da avisos de escritorio -y
cualquier otra funcion que exija contexto seguro- en `localhost` o en `https`.
Abierto por la IP de la red local, `http://192.168.1.152:8750` no lo es, y no
hay ajuste del manager que lo cambie: lo decide el navegador.

Con el certificado puesto el manager sirve `https` y ese limite desaparece.
Sin el, sigue sirviendo `http` exactamente como siempre. `tools.make_local_cert`
genera la CA local y el certificado.
"""
from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import Any

CERTIFICATE_NAME = "manager.crt"

KEY_NAME = "manager.key"

HANDSHAKE_TIMEOUT_SECONDS = 15
"""El saludo TLS ocurre en el hilo que acepta conexiones, asi que un cliente
que se queda a medias no puede bloquear al resto."""


def _environment_path(name: str) -> Path | None:
    value = str(os.environ.get(name) or "").strip()
    return Path(value).expanduser() if value else None


def _truthy_off(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"0", "off", "false", "no"}


def paths(config: dict[str, Any], config_dir: Path | None) -> tuple[Path, Path] | None:
    """Certificado y clave a usar, o `None` para seguir en HTTP.

    Orden: el interruptor de entorno manda, despues las rutas explicitas
    -entorno y `manager.json`- y por ultimo `<config>/runtime/tls`, que es donde
    escribe la herramienta y lo que el contenedor ve montado.

    **Sin `config_dir` no se busca en ningun sitio.** Lo pone el arranque a
    partir de `--config`, asi que un `ManagerServer` creado a mano -una prueba-
    no puede acabar sirviendo https porque quien lo ejecuta tenga un
    certificado en su directorio de trabajo.
    """
    if _truthy_off(os.environ.get("MT5_MANAGER_TLS")):
        return None
    directory = config.get("tls_dir") or (
        config_dir / "runtime" / "tls" if config_dir else None
    )
    certificate = (
        _environment_path("MT5_MANAGER_TLS_CERT")
        or (Path(str(config["tls_cert_file"])) if config.get("tls_cert_file") else None)
        or (Path(str(directory)) / CERTIFICATE_NAME if directory else None)
    )
    key = (
        _environment_path("MT5_MANAGER_TLS_KEY")
        or (Path(str(config["tls_key_file"])) if config.get("tls_key_file") else None)
        or (Path(str(directory)) / KEY_NAME if directory else None)
    )
    if certificate is None or key is None:
        return None
    return (certificate, key) if certificate.is_file() and key.is_file() else None


def context(certificate: Path, key: Path) -> ssl.SSLContext:
    secure = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    secure.minimum_version = ssl.TLSVersion.TLSv1_2
    secure.load_cert_chain(certfile=str(certificate), keyfile=str(key))
    return secure


def wrap(connection: Any, secure: ssl.SSLContext) -> Any:
    """Envuelve una conexion aceptada, con el saludo acotado en el tiempo."""
    connection.settimeout(HANDSHAKE_TIMEOUT_SECONDS)
    wrapped = secure.wrap_socket(connection, server_side=True)
    wrapped.settimeout(None)
    return wrapped
