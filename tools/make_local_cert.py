"""Genera la CA local del manager y su certificado de servidor.

Para que el navegador de otro equipo acepte avisos de escritorio -y en general
cualquier funcion que exija contexto seguro- el manager tiene que servirse por
`https`, y para que no salga el aviso rojo el certificado tiene que venir de
una autoridad en la que ese equipo confie. Aqui se crea esa autoridad **tuya**,
una vez, y con ella los certificados del manager.

    python -m tools.make_local_cert

Por defecto escribe en ``runtime/tls`` del repositorio, que es la carpeta que
el contenedor ve montada, e incluye ``localhost``, ``127.0.0.1``, el nombre del
equipo y las IPv4 de la maquina. Se le pueden anadir mas::

    python -m tools.make_local_cert 192.168.1.152 manager.casa

**La CA se reutiliza.** Volver a ejecutarlo renueva solo el certificado del
servidor, asi que no hay que volver a instalar nada en los dispositivos.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tools.source_files import ROOT

DEFAULT_DIRECTORY = ROOT / "runtime" / "tls"

CA_YEARS = 10

SERVER_DAYS = 825
"""Lo que aceptan los navegadores sin rechistar para un certificado propio."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _private(address: str) -> bool:
    """Sólo direcciones de casa.

    El nombre del equipo puede resolver a IP públicas -una VPN, una entrada en
    `hosts`-, y un certificado que dice ser `54.78.55.114` no pinta nada aquí.
    Si hace falta una pública, se pasa a mano.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_private or parsed.is_loopback or parsed.is_link_local


def local_addresses() -> list[str]:
    """Nombres y direcciones de casa por las que se abre este manager."""
    found = ["localhost", "127.0.0.1", "::1"]
    hostname = socket.gethostname()
    if hostname:
        found.append(hostname)
    try:
        found.extend(
            info[4][0] for info in socket.getaddrinfo(hostname, None, socket.AF_INET)
            if _private(info[4][0])
        )
    except OSError:
        pass
    try:
        # La IP con la que de verdad sale a la red local, que no siempre es la
        # primera que devuelve el nombre del equipo.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.1.1", 9))
            address = probe.getsockname()[0]
        if _private(address):
            found.append(address)
    except OSError:
        pass
    return list(dict.fromkeys(found))


def _subject_alternative_names(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for name in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return x509.SubjectAlternativeName(entries)


def _write(path: Path, data: bytes, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if private:
        try:
            path.chmod(0o600)
        except OSError:
            pass  # En Windows no aplica; el fichero queda en el perfil igual.


def _private_key_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_or_create_ca(directory: Path) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """La autoridad local. Se crea una vez y no se toca mas.

    Reemplazarla obligaria a volver a instalarla en cada dispositivo, asi que
    si ya existe se reutiliza aunque se renueve el certificado del servidor.
    """
    certificate_path = directory / "ca.crt"
    key_path = directory / "ca.key"
    if certificate_path.is_file() and key_path.is_file():
        return (
            x509.load_pem_x509_certificate(certificate_path.read_bytes()),
            serialization.load_pem_private_key(key_path.read_bytes(), password=None),
        )
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "MT5 Autotester Manager CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MT5 Autotester"),
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(days=1))
        .not_valid_after(_now() + dt.timedelta(days=365 * CA_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        # Sin estos identificadores, OpenSSL 3 y los navegadores rechazan la
        # cadena con «Missing Authority Key Identifier» aunque la CA este
        # instalada y la firma sea correcta.
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write(certificate_path, certificate.public_bytes(serialization.Encoding.PEM), False)
    _write(key_path, _private_key_bytes(key), True)
    return certificate, key


def issue_server_certificate(directory: Path, names: list[str]) -> list[str]:
    """Firma el certificado del manager con la CA local y lo escribe."""
    authority, authority_key = load_or_create_ca(directory)
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "MT5 Autotester Manager"),
        ]))
        .issuer_name(authority.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(days=1))
        .not_valid_after(_now() + dt.timedelta(days=SERVER_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(_subject_alternative_names(names), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False,
        )
        .sign(authority_key, hashes.SHA256())
    )
    _write(directory / "manager.crt", certificate.public_bytes(serialization.Encoding.PEM), False)
    _write(directory / "manager.key", _private_key_bytes(key), True)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="Nombres o IPs adicionales del manager")
    parser.add_argument("--out", default=str(DEFAULT_DIRECTORY), help="Carpeta de destino")
    arguments = parser.parse_args(argv)
    directory = Path(arguments.out).expanduser().resolve()
    names = list(dict.fromkeys([*local_addresses(), *arguments.names]))
    issue_server_certificate(directory, names)
    print(f"Certificado para: {', '.join(names)}")
    print(f"Escrito en {directory}")
    print(
        "\nFalta un paso, y es en cada dispositivo desde el que abras el manager:\n"
        f"  1. Copia {directory / 'ca.crt'} a ese dispositivo.\n"
        "  2. Windows: doble clic > Instalar certificado > Equipo local >\n"
        "     Entidades de certificacion raiz de confianza.\n"
        "     Android: Ajustes > Seguridad > Cifrado y credenciales > Instalar certificado > CA.\n"
        "     iOS: instala el perfil y activa la confianza en Ajustes > General > Informacion >\n"
        "     Certificados de confianza.\n"
        "  3. Reinicia el manager y abrelo por https.\n"
        "\nLa clave privada no sale de este equipo: ca.key y manager.key no se copian."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
