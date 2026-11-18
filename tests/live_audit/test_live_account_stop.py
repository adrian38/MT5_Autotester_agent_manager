"""Parar el sondeo de cuentas desde la pantalla.

Lo que ejecuta la parada es el agente; aquí se comprueban las dos mitades que
sí son de este repositorio: el reenvío del manager —incluido qué dice cuando
el agente todavía no tiene el port— y que las tres pantallas de real montan el
panel que enseña lo que corre y ofrece el botón.

Las pantallas se comprueban por el texto de sus ficheros y no ejecutándolas:
en este equipo no hay runtime de JavaScript.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.manager import manager_account_routes
from tools.package_layout import static_source

STOP_PATH = "/api/nodes/axi/live-accounts/snapshot/stop"
STOP_PARTS = ["api", "nodes", "axi", "live-accounts", "snapshot", "stop"]


class Notifications:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def add(self, level: str, title: str, message: str, **meta: object) -> None:
        self.events.append({"level": level, "title": title, "message": message, **meta})


class FakeServer:
    def __init__(self) -> None:
        self.nodes = [{"id": "axi", "name": "AXI Standard", "url": "http://127.0.0.1:1"}]
        self.live_audit_notifications = Notifications()
        self.live_account_wakeup = threading.Event()


class StopRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.answer: dict[str, object] = {}
        self.handler = SimpleNamespace(
            server=self.server,
            _body=dict,
            _node=lambda node_id: next(
                node for node in self.server.nodes if node["id"] == node_id
            ),
            _send_json=lambda status, payload: self.answer.update(
                status=status, body=payload,
            ),
        )

    def _post(self, status: int, value: object) -> bool:
        with mock.patch(
            "mt5_manager.manager.manager_http.node_request",
            return_value=(status, value),
        ) as request:
            handled = manager_account_routes.handle_post(
                self.handler, SimpleNamespace(path=STOP_PATH), STOP_PARTS,
            )
        self.request = request
        return handled

    def test_the_stop_reaches_the_agent_and_is_written_in_the_bell(self) -> None:
        self.assertTrue(self._post(200, {"snapshot": {"status": "finalizing"}}))
        self.assertEqual(self.answer["status"], 200)
        self.assertEqual(
            self.request.call_args[0][2], "/api/v1/live-accounts/snapshot/stop",
        )
        event = self.server.live_audit_notifications.events[0]
        self.assertEqual(event["kind"], "account_snapshot_stopped")
        self.assertIn("terminal", event["message"])
        # El vigilante tiene que recoger lo que el sondeo alcanzó a leer sin
        # esperar a su próxima vuelta.
        self.assertTrue(self.server.live_account_wakeup.is_set())

    def test_an_agent_without_the_port_says_what_is_missing(self) -> None:
        self.assertTrue(self._post(404, {"error": "Ruta no encontrada"}))
        self.assertEqual(self.answer["status"], 400)
        self.assertIn("portar", self.answer["body"]["error"])
        self.assertFalse(self.server.live_audit_notifications.events)

    def test_an_agent_with_nothing_running_passes_its_reason_through(self) -> None:
        self.assertTrue(self._post(
            409, {"error": "No hay ningún sondeo de cuentas en curso que detener"},
        ))
        self.assertEqual(self.answer["status"], 400)
        self.assertIn("en curso", self.answer["body"]["error"])

    def test_an_agent_that_does_not_exist_is_a_404(self) -> None:
        self.handler._node = lambda _node_id: (_ for _ in ()).throw(
            KeyError("Agente desconocido"),
        )
        self.assertTrue(self._post(200, {}))
        self.assertEqual(self.answer["status"], 404)


class ScreenTests(unittest.TestCase):
    """El panel vive en las tres pantallas que leen cuentas reales."""

    SCREENS = ("accounts.html", "dashboard.html", "patrimonio.html")

    def setUp(self) -> None:
        self.monitor = static_source("process_monitor.js")

    def test_the_three_live_screens_load_the_panel(self) -> None:
        for name in self.SCREENS:
            with self.subTest(screen=name):
                self.assertIn(
                    '<script src="/process_monitor.js"></script>',
                    static_source(name),
                )

    def test_the_panel_hangs_from_main_and_not_from_the_header(self) -> None:
        """La cabecera ya tenía su chapa: esto es lo que se ve sin buscarlo."""
        self.assertIn("document.querySelector('main')", self.monitor)
        self.assertIn("main.insertBefore(panel, main.firstChild)", self.monitor)

    def test_the_stop_is_one_button_for_everything(self) -> None:
        """Petición del usuario (2026-11-17): uno, y para todo lo que corre.

        Uno por fila obligaba a pulsar tantas veces como cosas hubiera. Se
        comprueba que el botón se pinta en la cabecera del panel y que la
        parada recorre **todo** lo detenible, no la fila pulsada.
        """
        self.assertIn('id="process-stop"', self.monitor)
        self.assertIn("process-monitor-head", self.monitor)
        self.assertNotIn("data-stop", self.monitor)
        self.assertIn("for (const row of rows)", self.monitor)
        self.assertIn("function stoppable", self.monitor)

    def test_it_knows_how_to_stop_both_kinds_of_work(self) -> None:
        self.assertIn("/live-accounts/snapshot/stop", self.monitor)
        self.assertIn("/live-audits/", self.monitor)
        self.assertIn("window.confirm", self.monitor)

    def test_one_agent_that_refuses_does_not_leave_the_others_running(self) -> None:
        """Soltar las terminales de los demás no depende del que falló."""
        self.assertIn("failed.push", self.monitor)

    def test_it_reads_the_activity_route_and_nothing_else(self) -> None:
        """Comparte lectura con la chapa: ni ruta nueva ni agente consultado."""
        self.assertIn("/api/live-audit-activity", self.monitor)

    def test_it_tells_the_wealth_reread_from_the_nightly_snapshot(self) -> None:
        self.assertIn("'wealth'", self.monitor)
        self.assertIn("wealth_waiting", self.monitor)

    def test_it_keeps_showing_the_work_done_by_the_manager(self) -> None:
        self.assertIn("manager_account_processing", self.monitor)
        self.assertIn("Procesando cuentas reales ahora", self.monitor)
        self.assertIn("Actualizando patrimonio", self.monitor)

    def test_the_panel_wakes_up_with_the_click_that_launches_the_work(self) -> None:
        """Petición del usuario (2026-11-17): la barra sale al pulsar, no un
        minuto después.

        Con nada corriendo la vuelta es de treinta segundos, así que «Releer
        ahora» y «Sondear ahora» dejaban la pantalla quieta justo cuando hacía
        falta la señal. Quien lanza avisa y el panel acorta la cadencia hasta
        que el agente declara el trabajo.
        """
        self.assertIn("live-account-run-requested", self.monitor)
        self.assertIn("eagerUntil", self.monitor)
        self.assertIn("phase: 'INICIANDO'", self.monitor)
        self.assertIn("panel.hidden = false", self.monitor)
        # «Releer ahora» está dentro de un `<dialog>` modal, que va en la capa
        # superior y tapaba con su velo tanto la barra como el aviso.
        self.assertIn("cron.close();", static_source("patrimonio.js"))
        for name in ("patrimonio.js", "live_account_schedule.js"):
            with self.subTest(screen=name):
                self.assertIn(
                    "dispatchEvent(new CustomEvent('live-account-run-requested', {",
                    static_source(name),
                )

    def test_the_wake_up_does_not_leave_two_polling_chains(self) -> None:
        """Dos cadenas preguntarían el doble para siempre, y en silencio."""
        self.assertIn("clearTimeout(timer)", self.monitor)
        self.assertNotIn("setTimeout(poll, delay)", self.monitor)

    def test_patrimonio_refreshes_when_the_manager_archives_a_snapshot(self) -> None:
        self.assertIn("live-account-data-updated", self.monitor)
        self.assertIn(
            "live-account-data-updated", static_source("patrimonio.js"),
        )

    def test_the_finished_batch_stays_visible_long_enough_to_be_read(self) -> None:
        """Desaparecer al terminar no permite distinguir final de fallo."""
        self.assertIn("completionPending = true", self.monitor)
        self.assertIn("completionUntil = Date.now() + 20000", self.monitor)
        self.assertIn("phase: 'TERMINADO'", self.monitor)
        self.assertIn("percent: 100", self.monitor)
        self.assertIn("Proceso terminado", self.monitor)


if __name__ == "__main__":
    unittest.main()
