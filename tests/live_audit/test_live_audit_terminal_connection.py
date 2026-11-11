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

from mt5_manager.live_account import live_account_snapshot
from mt5_manager.live_audit.live_audit_engine import LiveAuditController

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
        with unittest.mock.patch("mt5_manager.live_audit.live_audit_terminals.time", patched):
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


class ConfirmedAccountWaitTests(unittest.TestCase):
    """La relectura de después, que es donde se perdió `10068103` el 2026-11-01.

    `_active_account_settled` ya esperaba a que el terminal conectara, pero
    quien decide si la cuenta se lee es la comprobación de después, y ésa
    muestreaba `connected` una sola vez. Entre una y otra cabe el «scanning
    network for access points»: siete segundos desde «Entrando en 10068103»
    hasta el error, y el reintento la leyó sin que nadie tocara nada.
    """

    def setUp(self) -> None:
        self.clock = _Clock()
        self.patched = SimpleNamespace(monotonic=self.clock.monotonic, sleep=self.clock.sleep)

    def _confirm_snapshot(self, mt5: _FakeMt5):
        account = {"login": LOGIN, "server": SERVER}
        with unittest.mock.patch("mt5_manager.live_account.live_account_snapshot.time", self.patched):
            return live_account_snapshot._confirmed_account(mt5, account, SETTLE)

    def _confirm_audit(self, mt5: _FakeMt5):
        stub = SimpleNamespace(
            _settled_account=LiveAuditController._settled_account,
            tester_login_settle_seconds=SETTLE,
        )
        request = {"source_login": LOGIN, "source_server": SERVER}
        with unittest.mock.patch("mt5_manager.live_audit.live_audit_terminals.time", self.patched):
            return LiveAuditController._confirmed_real_account(stub, mt5, request)

    def test_the_snapshot_waits_out_the_access_point_scan(self) -> None:
        info, server = self._confirm_snapshot(_FakeMt5(self.clock, LOGIN, connected_at=6.0))

        self.assertEqual((str(info.login), server), (LOGIN, SERVER))

    def test_the_audit_waits_out_the_access_point_scan(self) -> None:
        info, server = self._confirm_audit(_FakeMt5(self.clock, LOGIN, connected_at=6.0))

        self.assertEqual((str(info.login), server), (LOGIN, SERVER))

    def test_a_connected_terminal_still_costs_nothing_in_either_path(self) -> None:
        self._confirm_snapshot(_FakeMt5(self.clock, LOGIN, connected_at=0.0))
        self._confirm_audit(_FakeMt5(self.clock, LOGIN, connected_at=0.0))

        self.assertEqual(self.clock.now, 0.0, "esperar no puede costarle nada a lo sano")

    def test_a_terminal_that_never_connects_is_still_reported_in_either_path(self) -> None:
        for confirm in (self._confirm_snapshot, self._confirm_audit):
            with self.subTest(confirm=confirm.__name__):
                mt5 = _FakeMt5(self.clock, LOGIN, connected_at=float("inf"))
                with self.assertRaises(RuntimeError) as raised:
                    confirm(mt5)
                self.assertIn("no está conectado al broker", str(raised.exception))

    def test_a_wrong_login_fails_without_waiting_for_a_connection(self) -> None:
        # El plazo es para el barrido, no para una cuenta que no es la pedida:
        # ahí no hay nada que esperar y el error tiene que salir en el acto.
        mt5 = _FakeMt5(self.clock, "67188517", connected_at=float("inf"))

        with self.assertRaises(RuntimeError):
            self._confirm_snapshot(mt5)
        self.assertEqual(self.clock.now, 0.0)


if __name__ == "__main__":
    unittest.main()
