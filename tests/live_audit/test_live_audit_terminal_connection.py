"""Qué se espera y qué no mientras el terminal vuelve del escaneo de accesos.

El 2026-10-31 el sondeo de cuentas de RoboForex ECN perdió
`67315100@RoboForex-ECN` con «MT5 confirmó el login local, pero el terminal no
está conectado al broker». El Journal la autorizó y sincronizó sin un error, y
223 ms después abrió su «scanning network for access points»; el sondeo corto
de `account_probe_seconds` se agotó dentro de ese barrido.

Las dos mitades del criterio van juntas a propósito: esperar cuando la cuenta
ya es la pedida, y **no** esperar antes de conmutar, que es lo que en su día
costaba el plazo entero por cada terminal del pool.
"""
from __future__ import annotations

import unittest
import unittest.mock
from types import SimpleNamespace

from mt5_manager.live_audit_engine import LiveAuditController

LOGIN = "67315100"
SERVER = "RoboForex-ECN"
SETTLE = 30.0


class _Clock:
    """Reloj de mentira: el tiempo sólo avanza cuando el código duerme."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeMt5:
    """Un terminal que conecta cuando le toca, no cuando se le pregunta."""

    def __init__(self, clock: _Clock, login: str, connected_at: float) -> None:
        self._clock = clock
        self._login = login
        self._connected_at = connected_at
        self.logins: list[tuple[int, str]] = []

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(login=self._login, server=SERVER)

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(connected=self._clock.now >= self._connected_at)

    def login(self, login: int, password: str = "", server: str = "", timeout: int = 0) -> bool:
        self.logins.append((login, server))
        self._login = str(login)
        return True

    def last_error(self) -> str:
        return "sin error"


class TerminalConnectionWaitTests(unittest.TestCase):
    def _activate(self, mt5: _FakeMt5, clock: _Clock):
        patched = SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
        with unittest.mock.patch("mt5_manager.live_audit_terminals.time", patched):
            return LiveAuditController._activate_account(mt5, LOGIN, "secreto", SERVER, SETTLE)

    def test_the_account_asked_for_is_waited_for_until_it_reconnects(self) -> None:
        clock = _Clock()
        mt5 = _FakeMt5(clock, LOGIN, connected_at=6.0)

        actual_login, actual_server, connected, error = self._activate(mt5, clock)

        self.assertEqual((actual_login, actual_server), (LOGIN, SERVER))
        self.assertTrue(connected, "el barrido de puntos de acceso tumbó la lectura")
        self.assertIsNone(error)
        self.assertEqual(mt5.logins, [], "no había nada que conmutar: la cuenta ya era la pedida")

    def test_a_terminal_that_never_connects_still_gives_up_within_the_settle_window(self) -> None:
        clock = _Clock()
        mt5 = _FakeMt5(clock, LOGIN, connected_at=float("inf"))

        _actual_login, _actual_server, connected, error = self._activate(mt5, clock)

        self.assertFalse(connected, "sin conexión el llamante tiene que poder decirlo")
        self.assertIsNone(error)
        self.assertLessEqual(
            clock.now, LiveAuditController.account_probe_seconds + SETTLE,
            "la espera no puede crecer más allá del plazo de asentamiento",
        )

    def test_an_already_connected_terminal_pays_no_wait(self) -> None:
        clock = _Clock()
        mt5 = _FakeMt5(clock, LOGIN, connected_at=0.0)

        _actual_login, _actual_server, connected, error = self._activate(mt5, clock)

        self.assertTrue(connected)
        self.assertIsNone(error)
        self.assertEqual(clock.now, 0.0)

    def test_another_account_is_switched_without_paying_the_long_wait_first(self) -> None:
        clock = _Clock()
        mt5 = _FakeMt5(clock, "67188517", connected_at=0.0)

        actual_login, _actual_server, connected, error = self._activate(mt5, clock)

        self.assertEqual(mt5.logins, [(int(LOGIN), SERVER)])
        self.assertEqual(actual_login, LOGIN)
        self.assertTrue(connected)
        self.assertIsNone(error)
        self.assertLessEqual(
            clock.now, LiveAuditController.account_probe_seconds + 0.25,
            "esperar antes de conmutar cuesta el plazo entero en cada terminal del pool",
        )


if __name__ == "__main__":
    unittest.main()
