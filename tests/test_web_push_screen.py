"""El lado del navegador de los avisos, comprobado por texto.

No hay runtime de JavaScript en este equipo, asi que lo que se exige es que
los ficheros existan, se sirvan, los carguen las pantallas que generan la
espera larga y digan lo que tienen que decir.

Las tres cosas que vigila son las tres por las que no llegaban los avisos:
Android no tiene el constructor `Notification`, iOS no tiene avisos fuera de
una web instalada, y una pagina sin service worker no puede recibir nada con
el navegador cerrado. Un cambio que las deshaga vuelve al sintoma exacto de
«sale en una PC y en el movil no».
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATIC = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"

PAGES = (
    "dashboard.html", "index.html", "live_audit.html",
    "portfolios.html", "portfolios_monthly.html", "portfolios_grid.html",
)
"""Las mismas que montan la campana. La pestana que el usuario deja puesta
mientras genera es la del portafolio, y la suscripcion se repone al cargar."""


class WebPushScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from mt5_manager.manager_handler import STATIC_FILES

        cls.static_files = STATIC_FILES
        cls.worker = (STATIC / "sw.js").read_text(encoding="utf-8")
        cls.client = (STATIC / "web_push.js").read_text(encoding="utf-8")
        cls.bell = (STATIC / "live_audit_schedule.js").read_text(encoding="utf-8")
        cls.manifest = json.loads((STATIC / "manifest.webmanifest").read_text("utf-8"))
        cls.pages = {name: (STATIC / name).read_text(encoding="utf-8") for name in PAGES}

    def test_everything_the_browser_asks_for_is_served(self) -> None:
        for name in (
            "sw.js", "web_push.js", "manifest.webmanifest",
            "icon-192.png", "icon-512.png", "icon-180.png",
        ):
            self.assertIn(name, self.static_files, f"{name} no se sirve")
            self.assertTrue((STATIC / name).is_file(), f"falta {name}")

    def test_every_page_with_the_bell_can_also_install_and_subscribe(self) -> None:
        for name, page in self.pages.items():
            self.assertIn("/web_push.js", page, f"{name} no carga el cliente")
            self.assertIn("/manifest.webmanifest", page, f"{name} no trae manifiesto")
            self.assertIn("/icon-180.png", page, f"{name} no trae icono de iOS")
            # El cliente tiene que estar antes: la campana lo usa al montarse.
            self.assertLess(page.index("/web_push.js"), page.index("/live_audit_schedule.js"))

    def test_the_manifest_is_installable_on_the_home_screen(self) -> None:
        """Sin `standalone` y sin iconos, iOS no lo instala y no hay avisos."""
        self.assertEqual("standalone", self.manifest["display"])
        self.assertEqual("/", self.manifest["start_url"])
        self.assertEqual("/", self.manifest["scope"])
        sizes = {icon["sizes"] for icon in self.manifest["icons"]}
        self.assertLessEqual({"192x192", "512x512"}, sizes)
        self.assertIn("maskable", {icon["purpose"] for icon in self.manifest["icons"]})

    def test_the_worker_lives_at_the_root_and_always_shows_something(self) -> None:
        # `userVisibleOnly` obliga a ensenar un aviso en cada push: si no sale
        # ninguno, el navegador ensena el suyo y acaba revocando el permiso.
        self.assertIn("registration.showNotification", self.worker)
        self.assertIn("FALLBACK", self.worker)
        self.assertIn("notificationclick", self.worker)
        self.assertIn("pushsubscriptionchange", self.worker)
        self.assertIn("/api/web-push/subscribe", self.worker)
        self.assertIn("clients.claim", self.worker)
        # No cachea nada: una pantalla vieja mintiendo sobre portafolios que ya
        # cambiaron seria peor que no tener worker.
        self.assertNotIn("caches.open", self.worker)

    def test_the_client_subscribes_against_the_key_of_this_manager(self) -> None:
        self.assertIn("navigator.serviceWorker.register", self.client)
        self.assertIn("userVisibleOnly: true", self.client)
        self.assertIn("applicationServerKey", self.client)
        self.assertIn("/api/web-push/config", self.client)
        self.assertIn("/api/web-push/subscribe", self.client)
        # La suscripcion vieja no sirve si la clave del manager cambio: el
        # servicio de push contestaria 403 y nadie lo veria.
        self.assertIn("sameKey", self.client)
        self.assertIn("subscription.unsubscribe()", self.client)

    def test_the_notice_of_the_open_tab_no_longer_uses_the_constructor(self) -> None:
        """En Android `new Notification(...)` es `Illegal constructor`.

        Esa excepcion moria en el `catch` del sondeo: el movil daba permiso y
        despues no avisaba de nada, sin ningun error a la vista.
        """
        self.assertIn("push().show(event.title, options)", self.bell)
        self.assertNotIn("new Notification(event.title", self.bell)
        self.assertIn("ManagerPush", self.bell)

    def test_the_user_can_check_the_whole_path_without_waiting_for_a_job(self) -> None:
        self.assertIn("cron-push-test", self.bell)
        self.assertIn("/api/web-push/test", self.client)
        self.assertIn("Ningún dispositivo suscrito", self.bell)


if __name__ == "__main__":
    unittest.main()
