"""La campana avisa también de los cálculos de portafolio.

Generar, arreglar o mejorar dura horas y termina sin nadie delante. Aquí se
comprueba que el aviso existe, que dice lo que pasó y que no puede tumbar el
cálculo que acaba de terminar.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager.live_audit_notifications import LiveAuditNotificationStore
from mt5_manager.portfolio_job_notices import job_notice, notice_link
from mt5_manager.portfolio_service import PortfolioCoordinator

NODES = [{"id": "test-node", "name": "Test Node", "portfolio_broker": "ICTRADING"}]


class JobNoticeTextTests(unittest.TestCase):
    def test_every_operation_has_its_own_title_in_the_three_outcomes(self) -> None:
        # El titulo es lo unico que se lee en el aviso del escritorio, asi que
        # tiene que decir que operacion termino, no solo que algo termino.
        expected = {
            ("generate", "completed"): "Portafolio generado: Portafolio UBS",
            ("generate", "failed"): "Falló la generación: Portafolio UBS",
            ("generate", "stopped"): "Generación detenida: Portafolio UBS",
            ("repair", "completed"): "Arreglo terminado: Portafolio UBS",
            ("improve", "completed"): "Mejora terminada: Portafolio UBS",
            ("reoptimize", "completed"): "Reoptimización terminada: Portafolio UBS",
            ("complete", "completed"): "Portafolio completado: Portafolio UBS",
        }
        for (operation, status), title in expected.items():
            notice = job_notice("full_history", {"operation": operation, "status": status})
            self.assertIsNotNone(notice, (operation, status))
            self.assertEqual(notice["title"], title)
            self.assertEqual(notice["kind"], f"portfolio_{operation}_{status}")

    def test_each_scope_says_which_screen_it_is(self) -> None:
        titles = {
            scope: job_notice(scope, {"operation": "generate", "status": "completed"})["title"]
            for scope in ("full_history", "monthly", "grid")
        }
        self.assertEqual(titles["full_history"], "Portafolio generado: Portafolio UBS")
        self.assertEqual(titles["monthly"], "Portafolio generado: Portafolio UBS mensual")
        self.assertEqual(titles["grid"], "Portafolio generado: Portafolio Grid UBS")

    def test_the_levels_separate_what_salio_bien_de_lo_que_no(self) -> None:
        levels = {
            status: job_notice("full_history", {"operation": "generate", "status": status})["level"]
            for status in ("completed", "failed", "stopped")
        }
        self.assertEqual(levels, {"completed": "success", "failed": "error", "stopped": "info"})

    def test_a_job_that_is_still_alive_is_not_news(self) -> None:
        for status in ("running", "stopping", ""):
            self.assertIsNone(
                job_notice("full_history", {"operation": "generate", "status": status}), status,
            )

    def test_an_operation_we_do_not_know_prefers_silence_to_an_invented_title(self) -> None:
        self.assertIsNone(
            job_notice("full_history", {"operation": "teleport", "status": "completed"}),
        )

    def test_the_message_counts_the_proposals_and_names_the_portfolio(self) -> None:
        one = job_notice("full_history", {
            "operation": "improve", "status": "completed",
            "proposal_count": 1, "portfolio_id": 12,
        })
        self.assertEqual(
            one["message"],
            "1 propuesta lista para revisar y guardar. Sobre el portafolio #12.",
        )
        self.assertEqual(one["portfolio_id"], 12)
        many = job_notice("full_history", {
            "operation": "generate", "status": "completed", "proposal_count": 3,
        })
        self.assertEqual(many["message"], "3 propuestas listas para revisar y guardar.")

    def test_a_failure_carries_the_error_and_a_stop_says_who_stopped_it(self) -> None:
        failed = job_notice("full_history", {
            "operation": "repair", "status": "failed", "error": "sin candidatos",
        })
        self.assertEqual(failed["message"], "sin candidatos")
        stopped = job_notice("full_history", {"operation": "repair", "status": "stopped"})
        self.assertIn("a mano", stopped["message"])

    def test_the_link_goes_to_the_screen_of_its_scope_with_the_node_chosen(self) -> None:
        self.assertEqual(
            notice_link("full_history", "ic-node"), "/portfolios.html?node=ic-node",
        )
        self.assertEqual(
            notice_link("monthly", "ic-node"), "/portfolios_monthly.html?node=ic-node",
        )
        self.assertEqual(
            notice_link("grid", "ic/node"), "/portfolios_grid.html?node=ic%2Fnode",
        )

    def test_only_a_finished_calculation_offers_the_proposals(self) -> None:
        completed = job_notice("full_history", {"operation": "generate", "status": "completed"})
        failed = job_notice("full_history", {"operation": "generate", "status": "failed"})
        self.assertEqual(completed["link_text"], "Ver propuestas")
        self.assertEqual(failed["link_text"], "Ver el cálculo")


class ExplodingStore:
    def add(self, *args: object, **kwargs: object) -> None:
        raise OSError("el diario no se puede escribir")


class CoordinatorNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.store = LiveAuditNotificationStore(root / "avisos.json")
        self.coordinator = PortfolioCoordinator(NODES, root / "portfolio_settings.json")
        self.coordinator.notifications = self.store
        self.key = self.coordinator._key("test-node", "full_history")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def seed(self, operation: str = "generate", portfolio_id: int | None = None) -> None:
        self.coordinator.jobs[self.key] = {
            "id": "20261020_000000", "status": "running", "operation": operation,
            "portfolio_id": portfolio_id, "stage_total": 5, "log_path": "",
        }

    def events(self) -> list[dict[str, object]]:
        return self.store.state(50)["events"]

    def test_a_finished_generation_leaves_its_notice_with_node_and_link(self) -> None:
        self.seed()
        self.coordinator._mark_job_completed(self.key, {}, [{"key": "balanced"}])
        event, = self.events()
        self.assertEqual(event["title"], "Portafolio generado: Portafolio UBS")
        self.assertEqual(event["level"], "success")
        self.assertEqual(event["kind"], "portfolio_generate_completed")
        self.assertEqual(event["node_id"], "test-node")
        self.assertEqual(event["node_name"], "Test Node")
        self.assertEqual(event["link"], "/portfolios.html?node=test-node")
        self.assertEqual(event["link_text"], "Ver propuestas")
        self.assertIn("1 propuesta lista", event["message"])

    def test_a_repair_and_an_improvement_are_announced_too(self) -> None:
        for operation, title in (
            ("repair", "Arreglo terminado: Portafolio UBS"),
            ("improve", "Mejora terminada: Portafolio UBS"),
        ):
            self.seed(operation, portfolio_id=7)
            self.coordinator._mark_job_completed(self.key, {}, [])
            self.assertEqual(self.events()[0]["title"], title)
            self.assertEqual(self.events()[0]["portfolio_id"], 7)

    def test_a_failure_and_a_stop_are_announced_with_their_own_level(self) -> None:
        self.seed()
        self.coordinator._mark_job_failed(self.key, ValueError("sin sets"), "test-node", "generate", None)
        failed = self.events()[0]
        self.assertEqual(failed["level"], "error")
        self.assertEqual(failed["message"], "sin sets")
        self.seed()
        self.coordinator._mark_job_stopped(self.key)
        self.assertEqual(self.events()[0]["level"], "info")
        self.assertEqual(self.events()[0]["kind"], "portfolio_generate_stopped")

    def test_a_coordinator_without_a_bell_still_finishes_its_job(self) -> None:
        # Los tests que montan el coordinador suelto no tienen que saber de
        # avisos, y el manager se lo presta al construirlo.
        self.coordinator.notifications = None
        self.seed()
        self.coordinator._mark_job_completed(self.key, {}, [])
        self.assertEqual(self.coordinator.jobs[self.key]["status"], "completed")

    def test_a_bell_that_fails_cannot_turn_a_good_calculation_into_a_failed_one(self) -> None:
        # `_mark_job_completed` corre dentro del `try` del worker: una
        # excepcion aqui saldria por su `except` y marcaria como fallido un
        # calculo que habia terminado bien.
        self.coordinator.notifications = ExplodingStore()
        self.seed()
        self.coordinator._mark_job_completed(self.key, {}, [])
        self.assertEqual(self.coordinator.jobs[self.key]["status"], "completed")


class BellWiringTests(unittest.TestCase):
    def test_the_manager_lends_its_bell_to_the_portfolio_coordinator(self) -> None:
        from mt5_manager.manager_server import ManagerServer

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            server = ManagerServer(("127.0.0.1", 0), {
                "nodes": [{"id": "test-node", "name": "Test Node", "url": "http://127.0.0.1:1"}],
                "preferences_file": str(root / "launch_preferences.json"),
                "live_audit_settings_file": str(root / "live_audit_settings.json"),
                "portfolio_settings_file": str(root / "portfolio_settings.json"),
            })
            try:
                self.assertIs(
                    server.portfolios.notifications, server.live_audit_notifications,
                )
            finally:
                server.server_close()


class PortfolioBellScreenTests(unittest.TestCase):
    """Las pantallas donde se genera también montan la campana.

    No hay runtime de JavaScript en este equipo, así que se comprueba el texto
    de los ficheros servidos.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.static = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"
        cls.script = (cls.static / "live_audit_schedule.js").read_text(encoding="utf-8")

    def test_the_three_portfolio_screens_load_the_bell(self) -> None:
        # Sin el script en la pantalla no hay aviso del escritorio, y la
        # pantalla del cálculo es justo la que el usuario deja abierta.
        for page in ("portfolios.html", "portfolios_grid.html", "portfolios_monthly.html"):
            text = (self.static / page).read_text(encoding="utf-8")
            for asset in ("/live_audit_schedule.css", "/live_audit_schedule.js"):
                self.assertIn(asset, text, f"{page} no carga {asset}")

    def test_an_event_can_bring_its_own_destination(self) -> None:
        # Un cálculo no tiene uso de auditoría al que enlazar: el manager le
        # pone el enlace, y el de la auditoría sigue deduciéndose.
        self.assertIn("event.link", self.script)
        self.assertIn("event.link_text", self.script)
        self.assertIn("/live_audit_analysis.html?node=", self.script)

    def test_the_bell_no_longer_says_it_is_only_about_audits(self) -> None:
        self.assertIn("Avisos del manager", self.script)
        self.assertNotIn("<h2>Avisos de auditoría</h2>", self.script)


if __name__ == "__main__":
    unittest.main()
