"""Nadie lanza un sondeo mientras el manager recoge el anterior de ese nodo.

El agente publica **una** tanda y sólo sirve los informes de la visible
(`account_snapshot_artifact`). Mientras el manager los baja, el agente ya
soltó las terminales y contesta que está libre, así que ni su `is_running` ni
la pregunta por su trabajo protegen nada: lanzar encima sustituye el estado y
los informes que faltaban desaparecen a media recogida.

Pasó el 2026-11-18 a las 10:41. El manager acababa de reiniciarse con
`SERIES_VERSION` cambiada y estaba recogiendo otra vez las tandas guardadas;
el usuario pulsó «relectura ahora» y la de RoboForex se cerró con tres cuentas
archivadas y `77056129@RoboForex-ECN: El nodo devolvió 404`.

Las dos citas de cron no podían provocarlo —`live_account_watch.tick` recoge y
lanza nodo por nodo en el mismo hilo, y la vuelta del patrimonio va detrás en
ese mismo hilo—, así que lo que se comprueba aquí son los dos botones, que
entran por un hilo de HTTP mientras esa vuelta corre.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import (
    live_account_collect_lock, live_account_inventory, live_account_watch,
    live_account_wealth_watch,
)
from mt5_manager.live_audit.live_audit_run_state import LiveAuditRunState
from mt5_manager.manager import manager_account_routes

NODE = {"id": "axi", "name": "AXI Standard", "url": "http://127.0.0.1:1"}
KEY = "60090762@Axi-US50-Live"
NOW = datetime(2026, 11, 18, 9, 41, 54, tzinfo=timezone.utc)
ACCOUNT = {
    "account_key": KEY, "login": "60090762", "server": "Axi-US50-Live",
    "portfolios": [], "audit_ids": ["1"], "autolot": False, "block_reason": "",
}
READING = SimpleNamespace(
    account_key=KEY, login="60090762", server="Axi-US50-Live",
    owner_node_id="axi", terminal="mt5", account={},
)


class GuardBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.runs = LiveAuditRunState(root / "state.json")
        self.launched: list[str] = []
        self.server = SimpleNamespace(
            nodes=[NODE], live_account_runs=self.runs,
            live_account_stop=SimpleNamespace(is_set=lambda: False),
            live_account_wakeup=SimpleNamespace(set=lambda: None),
            live_account_scheduler_enabled=False,
        )
        # El candado es del proceso, así que una prueba que lo deje echado se
        # lleva por delante a las siguientes: se suelta pase lo que pase.
        self.addCleanup(live_account_collect_lock._COLLECTING.discard, "axi")

    def fake_launch(self, _server, _node, node_id, *_args, **_extra) -> None:
        self.launched.append(node_id)


class WealthButtonTests(GuardBase):
    """«Relectura ahora», que es el botón que disparó el 404."""

    def run_now(self) -> dict[str, Any]:
        with mock.patch.object(
            live_account_wealth_watch, "summing_readings", return_value=[READING],
        ), mock.patch.object(
            live_account_wealth_watch.manager_http, "node_request",
            return_value=(200, {"job": {"status": "idle", "job_type": "generation"}}),
        ), mock.patch.object(live_account_watch, "launch", self.fake_launch):
            return live_account_wealth_watch.run_now(self.server)

    def test_it_launches_when_nothing_is_being_collected(self) -> None:
        result = self.run_now()
        self.assertEqual(result["launched"], ["axi"])
        self.assertEqual(result["skipped"], [])

    def test_it_does_not_launch_while_the_manager_collects_that_node(self) -> None:
        live_account_collect_lock._COLLECTING.add("axi")
        result = self.run_now()
        self.assertEqual(result["launched"], [])
        self.assertEqual(
            result["skipped"],
            [{"node_id": "axi", "reason": live_account_collect_lock.COLLECTING_REASON}],
        )
        self.assertEqual(self.launched, [])

    def test_an_idle_agent_does_not_open_the_lock(self) -> None:
        """El agente dice que sí, y por eso preguntarle no bastaba.

        La recogida corre con las terminales ya sueltas: `is_running` es
        falso y su trabajo está en `idle`. Si el candado se mirase antes que
        la pregunta al agente —que tarda hasta diez segundos— la recogida
        podría empezar justo dentro de esa ventana.
        """
        live_account_collect_lock._COLLECTING.add("axi")
        with mock.patch.object(
            live_account_wealth_watch.manager_http, "node_request",
            return_value=(200, {"job": {"status": "idle", "job_type": "generation"}}),
        ):
            reason = live_account_wealth_watch.node_busy(self.server, NODE, "axi")
        self.assertEqual(reason, live_account_collect_lock.COLLECTING_REASON)


class DailyButtonTests(GuardBase):
    """«Sondear ahora» de la portada, que tiene la misma puerta abierta."""

    def snapshot_now(self) -> dict[str, Any]:
        handler = SimpleNamespace(server=self.server)
        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=[ACCOUNT],
        ), mock.patch.object(
            live_account_watch, "node_snapshot",
            return_value=({"snapshot_id": "20261118_102549_000001",
                           "status": "completed"}, "", ""),
        ), mock.patch.object(live_account_watch, "launch", self.fake_launch):
            return manager_account_routes._snapshot_now(handler)

    def test_it_launches_when_nothing_is_being_collected(self) -> None:
        result = self.snapshot_now()
        self.assertEqual(result["launched"], ["axi"])
        self.assertEqual(result["skipped"], [])

    def test_it_does_not_launch_while_the_manager_collects_that_node(self) -> None:
        live_account_collect_lock._COLLECTING.add("axi")
        result = self.snapshot_now()
        self.assertEqual(result["launched"], [])
        self.assertEqual(
            result["skipped"],
            [{"node_id": "axi", "reason": live_account_collect_lock.COLLECTING_REASON}],
        )
        self.assertEqual(self.launched, [])


class LockLifetimeTests(GuardBase):
    """Que el candado esté echado **mientras** `collect` baja los informes.

    Es lo que de verdad se comprobó mal el 2026-11-18: no basta con que la
    pregunta tenga respuesta, tiene que tenerla en el instante en el que la
    recogida está a medias.
    """

    def tick(self, collect) -> None:
        with mock.patch.object(
            live_account_inventory, "node_accounts", return_value=[ACCOUNT],
        ), mock.patch.object(
            live_account_watch, "node_snapshot",
            return_value=({"snapshot_id": "20261118_102549_000001",
                           "status": "completed"}, "", ""),
        ), mock.patch.object(
            live_account_watch, "record_node_issue", lambda *_args: None,
        ), mock.patch.object(
            live_account_watch.live_account_expectation_refresh,
            "refresh_expectations", lambda *_args: None,
        ), mock.patch.object(
            live_account_watch.live_account_ingest, "collect", collect,
        ):
            live_account_watch.tick(self.server, NOW)

    def test_the_lock_is_held_while_the_reports_are_downloaded(self) -> None:
        seen: list[str] = []

        def collect(_server, _node, node_id, _state):
            seen.append(live_account_collect_lock.collecting_reason(node_id))
            return None

        self.assertEqual(live_account_collect_lock.collecting_reason("axi"), "")
        self.tick(collect)
        self.assertEqual(seen, [live_account_collect_lock.COLLECTING_REASON])
        self.assertEqual(live_account_collect_lock.collecting_reason("axi"), "")

    def test_a_broken_collection_does_not_leave_the_lock_shut(self) -> None:
        """Una recogida rota ya no paraba la vuelta; tampoco puede parar esto.

        Sin el `finally`, el nodo cuyo informe reventó se quedaría sin poder
        sondearse nunca más hasta reiniciar el manager, que es peor que la
        carrera que el candado viene a cerrar.
        """
        def collect(*_args):
            raise ValueError("el informe no se dejó leer")

        with mock.patch.object(live_account_watch.sys, "stderr", mock.Mock()):
            self.tick(collect)
        self.assertEqual(live_account_collect_lock.collecting_reason("axi"), "")


if __name__ == "__main__":
    unittest.main()
