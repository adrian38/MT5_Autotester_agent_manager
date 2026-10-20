"""El https opcional del manager y la CA local que lo hace creíble.

Existe por una razón concreta: el navegador sólo da avisos de escritorio en
`localhost` o en `https`. Abierto por la IP de la red local, `http` no es
contexto seguro y no hay ajuste del manager que lo cambie.
"""
from __future__ import annotations

import contextlib
import io
import ipaddress
import json
import os
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography import x509

from mt5_manager import manager_tls
from mt5_manager.docker_entrypoint import docker_config
from mt5_manager.manager import ManagerServer
from tools.make_local_cert import issue_server_certificate, load_or_create_ca, main

NODES = [{"id": "n", "name": "N", "url": "http://127.0.0.1:1", "token": "t"}]

MANAGER_VARIABLES = (
    "MT5_MANAGER_TLS", "MT5_MANAGER_TLS_CERT", "MT5_MANAGER_TLS_KEY",
    "MT5_MANAGER_LIVE_AUDIT_SCHEDULER",
)


@contextlib.contextmanager
def without_manager_variables(**extra: str):
    """Quita las variables del manager **sin vaciar `os.environ`**.

    `mock.patch.dict(..., clear=True)` aqui no vale: en Windows, la primera
    inicializacion de OpenSSL del proceso necesita el entorno del sistema y
    sin el `ssl.SSLContext()` falla con «unknown error (0xa080024)». Costo una
    hora de perseguir un fallo que no estaba en el manager.
    """
    with mock.patch.dict("os.environ", extra):
        for name in MANAGER_VARIABLES:
            if name not in extra:
                os.environ.pop(name, None)
        yield


class LocalCertificateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "tls"

    def test_it_issues_a_certificate_for_the_names_the_manager_answers_to(self) -> None:
        issue_server_certificate(self.directory, ["localhost", "127.0.0.1", "192.168.1.152"])
        certificate = x509.load_pem_x509_certificate((self.directory / "manager.crt").read_bytes())
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        ).value
        self.assertEqual(names.get_values_for_type(x509.DNSName), ["localhost"])
        self.assertEqual(
            names.get_values_for_type(x509.IPAddress),
            [ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("192.168.1.152")],
        )
        self.assertTrue((self.directory / "manager.key").is_file())

    def test_the_authority_survives_a_new_server_certificate(self) -> None:
        # Si cambiara, habria que volver a instalarla en cada dispositivo.
        issue_server_certificate(self.directory, ["localhost"])
        first = (self.directory / "ca.crt").read_bytes()
        first_server = (self.directory / "manager.crt").read_bytes()
        issue_server_certificate(self.directory, ["localhost", "192.168.1.152"])
        self.assertEqual(first, (self.directory / "ca.crt").read_bytes())
        self.assertNotEqual(first_server, (self.directory / "manager.crt").read_bytes())

    def test_it_only_claims_addresses_from_the_local_network(self) -> None:
        # El nombre del equipo resolvía a dos IP públicas de AWS —una VPN— y el
        # certificado decía ser 54.78.55.114. Una pública se pasa a mano.
        from tools.make_local_cert import local_addresses

        for address in local_addresses():
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue  # Es un nombre, no una dirección.
            self.assertTrue(
                parsed.is_private or parsed.is_loopback or parsed.is_link_local,
                msg=f"{address} no es de la red local",
            )

    def _issue(self, *names: str) -> list[str]:
        """Ejecuta la herramienta entera y devuelve los nombres del certificado."""
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--out", str(self.directory), *names])
        certificate = x509.load_pem_x509_certificate((self.directory / "manager.crt").read_bytes())
        alternative = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        ).value
        return [
            *alternative.get_values_for_type(x509.DNSName),
            *(str(address) for address in alternative.get_values_for_type(x509.IPAddress)),
        ]

    def test_a_renewal_without_arguments_keeps_the_names_given_by_hand(self) -> None:
        # El dominio por el que se entra desde fuera no sale de esta máquina:
        # renovar sin argumentos lo perdía, y el manager dejaba de valer por ese
        # nombre sin un solo mensaje.
        self._issue("casa.ddns.net")
        self.assertIn("casa.ddns.net", self._issue())

    def test_it_forgets_a_name_removed_from_the_file(self) -> None:
        self._issue("casa.ddns.net")
        (self.directory / "names.txt").write_text("# sin nombres\n", encoding="utf-8")
        self.assertNotIn("casa.ddns.net", self._issue())

    def test_the_server_certificate_is_signed_by_that_authority(self) -> None:
        authority, _key = load_or_create_ca(self.directory)
        issue_server_certificate(self.directory, ["localhost"])
        server = x509.load_pem_x509_certificate((self.directory / "manager.crt").read_bytes())
        self.assertEqual(server.issuer, authority.subject)


class TlsConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_without_certificates_the_manager_stays_on_http(self) -> None:
        with without_manager_variables():
            self.assertIsNone(manager_tls.paths({}, self.root))

    def test_a_server_built_by_hand_never_picks_up_a_stray_certificate(self) -> None:
        # Pasó de verdad: al generar el certificado de este equipo, las pruebas
        # que levantan un ManagerServer sin configuración empezaron a servir
        # https —el certificado estaba en el directorio de trabajo— y sus
        # clientes http se encontraron la conexión cortada. Quién sirve TLS lo
        # decide `--config`, nunca desde dónde se ejecuta.
        issue_server_certificate(self.root / "runtime" / "tls", ["localhost"])
        with without_manager_variables():
            self.assertIsNone(manager_tls.paths({}, None))
            self.assertIsNotNone(manager_tls.paths({}, self.root))

    def test_the_certificate_in_the_default_folder_is_enough(self) -> None:
        issue_server_certificate(self.root / "runtime" / "tls", ["localhost"])
        with without_manager_variables():
            found = manager_tls.paths({}, self.root)
        self.assertIsNotNone(found)
        self.assertEqual(found[0].name, "manager.crt")

    def test_the_environment_can_force_plain_http(self) -> None:
        # Un vigilante externo que sondea por http no puede quedarse fuera sin
        # una forma de volver atrás.
        issue_server_certificate(self.root / "runtime" / "tls", ["localhost"])
        with without_manager_variables(MT5_MANAGER_TLS="off"):
            self.assertIsNone(manager_tls.paths({}, self.root))

    def test_the_container_looks_in_the_mounted_volume(self) -> None:
        self.assertEqual(
            docker_config({"nodes": NODES})["tls_dir"], "/app/runtime/tls",
        )


class HttpsServerTests(unittest.TestCase):
    """De punta a punta: un navegador que confía en la CA entra sin avisos."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _serve(self, config: dict[str, object]) -> ManagerServer:
        with without_manager_variables():
            server = ManagerServer(("127.0.0.1", 0), {
                "nodes": NODES, "config_dir": str(self.root),
                # Cada servidor con su propio estado: por defecto cuelga del
                # directorio de trabajo y la campana traeria los avisos que
                # hayan dejado otras pruebas.
                "live_audit_settings_file": str(self.root / "live_audit_settings.json"),
                "live_audit_scheduler_settings_file": str(self.root / "scheduler.json"),
                **config,
            })
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server

    def test_the_manager_serves_https_with_the_local_authority(self) -> None:
        directory = self.root / "runtime" / "tls"
        issue_server_certificate(directory, ["localhost", "127.0.0.1"])
        server = self._serve({})
        self.assertEqual(server.scheme, "https")
        trust = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        url = f"https://127.0.0.1:{server.server_address[1]}/api/live-audit-notifications"
        with urllib.request.urlopen(url, timeout=10, context=trust) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["total"], 0)

    def test_it_serves_its_authority_so_other_devices_can_trust_it(self) -> None:
        # Desde otro PC o desde el móvil, el `ca.crt` no está a mano: llegar al
        # disco del manager no es una instrucción, es un problema.
        directory = self.root / "runtime" / "tls"
        issue_server_certificate(directory, ["localhost", "127.0.0.1"])
        server = self._serve({})
        trust = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        url = f"https://127.0.0.1:{server.server_address[1]}/ca.crt"
        with urllib.request.urlopen(url, timeout=10, context=trust) as response:
            body = response.read()
            self.assertEqual(response.headers["Content-Type"], "application/x-x509-ca-cert")
        self.assertEqual(body, (directory / "ca.crt").read_bytes())
        # Lo que nunca se publica es la clave.
        self.assertNotIn(b"PRIVATE KEY", body)
        for path in ("/ca.key", "/manager.key", "/tls/ca.key"):
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(
                    f"https://127.0.0.1:{server.server_address[1]}{path}",
                    timeout=10, context=trust,
                )
            self.assertEqual(refused.exception.code, 404)

    def test_without_a_certificate_there_is_no_authority_to_download(self) -> None:
        server = self._serve({})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/ca.crt", timeout=10,
            )
        self.assertEqual(refused.exception.code, 404)

    def test_without_certificates_it_keeps_answering_plain_http(self) -> None:
        server = self._serve({})
        self.assertEqual(server.scheme, "http")
        url = f"http://127.0.0.1:{server.server_address[1]}/api/live-audit-notifications"
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_a_failed_handshake_does_not_bring_the_server_down(self) -> None:
        # Una petición http contra el puerto https: esa conexión se cae y el
        # manager sigue atendiendo a quien venga detrás.
        directory = self.root / "runtime" / "tls"
        issue_server_certificate(directory, ["localhost", "127.0.0.1"])
        server = self._serve({})
        port = server.server_address[1]
        with self.assertRaises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10)
        trust = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        with urllib.request.urlopen(
            f"https://127.0.0.1:{port}/api/live-audit-notifications", timeout=10, context=trust,
        ) as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
