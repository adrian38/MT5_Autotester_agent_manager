from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from mt5_manager.live_audit.live_audit_engine import LiveAuditController
from tests.live_audit.live_audit_engine_base import FakeOwner

MINE = r"C:\Vantage\terminal64.exe"
NEIGHBOUR = r"C:\RoboForex\terminal64.exe"


class TerminalScopeTests(unittest.TestCase):
    """Un agente no cierra la terminal de otro, que en este equipo son cuatro.

    El 2026-11-22 el agente de RoboForex cerró la terminal que el de Vantage
    estaba usando para exportar el HTML nativo: los dos arrancaron la suya en
    el mismo segundo y el primero que terminó cerró «todo lo aparecido desde
    que miré», que era `_terminal_pids()`, la máquina entera. La víctima se
    quedó esperando la ventana de un proceso muerto.
    """

    @staticmethod
    def _mine_appears():
        """La lectura por ruta: la mía aparece tras arrancarla, la del vecino ya está."""
        seen: list[str] = []

        def pids_for_path(path: str) -> set[int]:
            seen.append(str(path))
            if str(path) != MINE:
                return {202}
            return set() if seen.count(MINE) == 1 else {101}

        return pids_for_path

    def _controller(self) -> LiveAuditController:
        controller = LiveAuditController(FakeOwner("idle"), Path(tempfile.gettempdir()))
        controller._terminal_profiles = lambda: [
            ("Terminal.1", {"name": "MT5 principal", "mt5_path": MINE}),
        ]
        # Lo que de verdad corre en la máquina: la mía y la del vecino.
        controller._terminal_pids = lambda: {101, 202}
        controller._terminal_pids_for_path = self._mine_appears()
        return controller

    def _login(self, controller: LiveAuditController, *, confirms: bool):
        class FakeMt5:
            initialize = staticmethod(lambda **_kwargs: True)
            shutdown = staticmethod(lambda: None)
            last_error = staticmethod(lambda: "sin error")

        controller._activate_account = lambda *_args, **_kwargs: (
            ("55", "Vantage-Live", True, "") if confirms
            else ("", "", False, "el terminal no confirmó el login")
        )
        with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": FakeMt5}):
            return controller._login_terminal("55", "clave", "Vantage-Live")

    def test_the_pids_it_reports_are_only_those_of_its_own_installation(self) -> None:
        controller = self._controller()

        _mt5, section, _profile, pids = self._login(controller, confirms=True)

        self.assertEqual(section, "Terminal.1")
        self.assertEqual(pids, {101})

    def test_a_login_that_is_not_confirmed_leaves_the_neighbour_alone(self) -> None:
        controller = self._controller()
        closed: list[set[int]] = []
        controller._close_terminal_pids = closed.append

        with self.assertRaises(RuntimeError):
            self._login(controller, confirms=False)

        self.assertEqual(closed, [{101}])


if __name__ == "__main__":
    unittest.main()
