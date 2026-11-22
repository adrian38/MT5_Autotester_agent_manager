from __future__ import annotations

import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from mt5_manager.live_audit import live_audit_processes
from mt5_manager.live_audit.live_audit_engine import LiveAuditController
from mt5_manager.live_audit.live_audit_processes import _ProcessMixin
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


class ForeignInstallationFallbackTests(unittest.TestCase):
    """`[Paths] mt5_path` apunta al terminal del vecino, así que no es respaldo.

    En el `ui_settings.ini` de IC y en el de AXI esa clave sigue apuntando a la
    instalación de RoboForex, resto de la máquina vieja. Era lo que usaba
    `_terminal_profiles()` cuando el broker del nodo no tiene **ningún** perfil
    habilitado: el día que alguien deshabilitara el último, el agente abriría
    la instalación del vecino en vez de fallar. `agent_terminals()` ya lo
    rechazaba por su cuenta y decía por qué.
    """

    @staticmethod
    def _controller(root: Path, neighbour: Path, mine: Path) -> LiveAuditController:
        (root / "ui_settings.ini").write_text(
            "[Paths]\nmt5_path=" + str(neighbour) + "\n"
            "[Terminal.1]\nname=Vecino\nenabled=1\nbroker=ROBOFOREX\nmt5_path="
            + str(neighbour) + "\n"
            "[Terminal.2]\nname=Mia\nenabled=0\nbroker=VANTAGE\nmt5_path="
            + str(mine) + "\n",
            encoding="utf-8",
        )
        owner = FakeOwner("idle")
        owner.config.update(
            project_dir=str(root), settings_file="ui_settings.ini", broker="VANTAGE",
        )
        return LiveAuditController(owner, root / "runtime")

    def test_without_a_profile_of_its_own_it_fails_instead_of_opening_the_neighbour(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            neighbour, mine = root / "robo" / "terminal64.exe", root / "vtg" / "terminal64.exe"
            for path in (neighbour, mine):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            controller = self._controller(root, neighbour, mine)
            with self.assertRaises(ValueError) as raised:
                controller._terminal_profiles()
            # Una deshabilitada **suya** sigue mandando sobre el vecino.
            with_disabled = controller._terminal_profiles(include_disabled=True)

        self.assertIn("VANTAGE", str(raised.exception))
        self.assertNotIn(str(neighbour), str(raised.exception))
        self.assertEqual([profile[1]["name"] for profile in with_disabled], ["Mia"])


class TerminalCensusTests(unittest.TestCase):
    """El censo con el que se espera tiene que ver lo mismo que se cierra.

    `_terminal_pids_for_path` pregunta por `terminal64.exe` **y**
    `terminal.exe` desde que el sondeo del patrimonio lee cuentas MT4, pero
    `_terminal_pids()` seguía preguntando sólo por MT5. Y `_terminal_pids()`
    es con lo que `_close_terminal_pids_gracefully` comprueba si el proceso
    murió: `pids & self._terminal_pids()` salía siempre vacío para un PID de
    MT4, así que la espera volvía en la primera vuelta dando por cerrado lo
    que no había comprobado nadie, y no llegaba nunca al `/F` que remata a la
    terminal que ignora el WM_CLOSE.
    """

    @staticmethod
    def _executables(reader) -> set[str]:
        """Los ejecutables por los que pregunta una de las dos lecturas."""
        completed = unittest.mock.Mock(returncode=0, stdout="[]")
        with unittest.mock.patch.object(
            live_audit_processes.sys, "platform", "win32",
        ), unittest.mock.patch(
            "mt5_manager.live_audit.live_audit_core.subprocess.run",
            return_value=completed,
        ) as run:
            reader()
        return set(re.findall(r"name='([^']+)'", " ".join(run.call_args.args[0])))

    def test_the_wait_asks_for_the_same_terminals_that_the_scoped_reader_returns(self) -> None:
        census = self._executables(_ProcessMixin._terminal_pids)
        scoped = self._executables(
            lambda: _ProcessMixin._terminal_pids_for_path(r"C:\AXI\terminal.exe"),
        )

        self.assertEqual(census, scoped)
        self.assertIn("terminal.exe", census)


if __name__ == "__main__":
    unittest.main()
