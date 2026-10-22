"""Detener una auditoría tiene que dejarla cerrada, no en el limbo.

El 2026-10-22, en `audit-109` de AXI, pulsar «Detener» cortó el tester, cerró
sus terminales y devolvió cada cuenta a su sitio… y la pantalla se quedó en
«PROBANDO» con el texto «Deteniendo la auditoría a petición del usuario» para
siempre. Volver a pulsar repetía lo mismo.

La carrera está en los propios logs del agente:

| Hora | Quién | Qué |
| --- | --- | --- |
| 14:05:49 | trabajador | recoge `AuditStopped` y pasa a `finalizing` |
| 14:05:50 | `stop_audit` | escribe el estado que leyó **antes** de cortar el tester: vuelve a `testing` |
| 14:06:03 | cierre | sólo cerraba desde `finalizing`/`resuming`, así que no fija nada |

Dos contratos, uno por cada mitad del arreglo: detener no mueve el estado hacia
atrás, y el cierre cierra desde cualquier estado en curso.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.live_audit.live_audit_engine_base import LiveAuditEngineTestCase, request


class StopDoesNotRewindTests(LiveAuditEngineTestCase):
    def test_stopping_never_moves_the_status_backwards(self) -> None:
        # Cortar el tester y cerrar sus terminales tarda segundos, y en ese
        # hueco el trabajador ya ha pasado a `finalizing`. Se reproduce
        # adelantándolo dentro del cierre de terminales.
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            controller.states["9"] = {"status": "testing", "progress_text": "", "log_lines": []}
            controller._persist = lambda: None

            def slow_close(_paths: list[str]) -> int:
                controller._update(
                    "9", "finalizing", "Detenida a petición del usuario.", None,
                )
                return 0

            controller._close_tester_terminals = slow_close
            controller.stop("9")
            self.assertEqual(controller.states["9"]["status"], "finalizing")
            self.assertIn(
                "Deteniendo la auditoría", controller.states["9"]["progress_text"],
            )

    def test_stopping_still_says_what_it_did(self) -> None:
        # No tocar el estado no puede costar el parte: el usuario tiene que
        # leer que se cortó el tester y que las cuentas volvieron a su sitio.
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            controller.states["9"] = {"status": "testing", "progress_text": "", "log_lines": []}
            controller._persist = lambda: None
            controller._close_tester_terminals = lambda _paths: 2
            controller.stop("9")
            self.assertEqual(controller.states["9"]["status"], "testing")
            self.assertIn(
                "cerradas 2 terminal(es)", controller.states["9"]["log_lines"][-1],
            )


class ClosingTests(LiveAuditEngineTestCase):
    def _closed_from(self, status: str) -> dict:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            controller.states["9"] = {"status": status, "progress_text": "", "log_lines": []}
            controller._persist = lambda: None
            controller._finish_audit(request(), "run-1", False, "stopped")
            return controller.states["9"]

    def test_it_closes_from_any_status_still_in_progress(self) -> None:
        # Si algo pisó el estado mientras se restauraban las terminales, no
        # cerrar aquí deja la auditoría viva para siempre.
        for status in ("testing", "comparing", "finalizing", "resuming"):
            with self.subTest(status=status):
                self.assertEqual(self._closed_from(status)["status"], "stopped")

    def test_it_does_not_overwrite_a_terminal_status_already_published(self) -> None:
        # Eso es lo que la guarda protege, y sigue protegiéndolo.
        for status in ("completed", "failed", "stopped"):
            with self.subTest(status=status):
                self.assertEqual(self._closed_from(status)["status"], status)

    def test_closing_always_stamps_when_it_ended(self) -> None:
        self.assertTrue(self._closed_from("testing")["finished_at"])


class StoppedRunTests(LiveAuditEngineTestCase):
    def test_a_stopped_audit_ends_stopped_and_not_in_limbo(self) -> None:
        # De punta a punta: el tester se detiene a mitad y la auditoría sale
        # por su `finally` de siempre hasta un estado terminal.
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")

            def stopping(*_args):
                controller.stop_requested = True
                controller._honour_stop_request()
                raise AssertionError("_honour_stop_request tenía que cortar aquí")

            controller._run_tester = stopping
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "stopped")
            self.assertIsNone(state.get("error"))


if __name__ == "__main__":
    unittest.main()
