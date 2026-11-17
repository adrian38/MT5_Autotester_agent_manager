"""Las dos citas de cuentas reales no se pisan, aunque la tanda sea una.

El agente guarda **un** sondeo, así que `__snapshot__` es uno por nodo. Las
citas que lo piden son dos y no tienen nada que ver: el sondeo diario entra en
las cuentas de los portafolios activos y reescribe los últimos `overlap_days`;
la relectura del patrimonio entra en las marcadas «Sumar» y pide la vida
entera de cada una.

Hasta el 2026-11-17 las dos escribían su turno y sus intentos en ese registro
compartido, y pasó lo que tenía que pasar: la relectura de AXI volvió con una
cuenta sin leer, ese `partial` contó como fallo **del diario** y el diario
reintentó a las 11:16 y a las 11:26 con su periodo de tres días, volviendo a
entrar en las terminales cada diez minutos. En RoboForex pasó lo contrario y
peor: volvió entera y dio por cumplida la cita de la noche, que nunca llegó a
hacer su propia pasada.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import live_account_expectation_refresh, live_account_watch
from mt5_manager.live_account.live_account_ingest import DAILY_KEY, SNAPSHOT_KEY
from mt5_manager.live_account.live_account_wealth import WEALTH_KEY

from .live_account_fixtures import (
    REPORT, FakeServer, complete_snapshot_state, node_state, parsed_account,
    snapshot_state,
)

NOW = datetime(2026, 10, 21, 23, 30, tzinfo=ZoneInfo("Europe/Madrid"))


class TwoAppointmentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.requests: list[tuple[str, str, object]] = []
        self.server = FakeServer(Path(self.folder.name), node_state(), enabled=True)

    def _request(self, state: dict[str, object]):
        def node_request(node, method, path, payload=None, timeout=10):
            self.requests.append((method, path, payload))
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_account_snapshot": True}}
            if path == "/api/v1/live-accounts":
                return 200, {"snapshot": state}
            if path == "/api/v1/live-accounts/snapshot":
                return 202, {"snapshot": {"status": "queued"}}
            raise AssertionError(f"ruta inesperada {path}")

        def artifact(node, path, timeout=10):
            self.requests.append(("GET", path, None))
            return 200, REPORT, "text/html"

        return mock.patch.multiple(
            "mt5_manager.manager.manager_http",
            node_request=node_request, node_artifact_request=artifact,
        )

    def _wealth_asked_for_it(self) -> None:
        """La tanda que corre en el agente la pidió la relectura, no el cron."""
        runs = self.server.live_account_runs
        runs.update(
            runs.key("axi", SNAPSHOT_KEY),
            owner=WEALTH_KEY, outcome="running", last_status="queued",
        )
        runs.update(
            runs.key("axi", WEALTH_KEY),
            window=NOW.isoformat(), attempts=1, outcome="running",
        )

    def _come_back(self, state: dict[str, object], moment: datetime) -> None:
        with self._request(state), mock.patch.object(
            live_account_expectation_refresh, "refresh_expectations", return_value=0,
        ), mock.patch(
            "mt5_manager.live_account.live_account_report.account_from_html",
            return_value=parsed_account(),
        ):
            live_account_watch.tick(self.server, moment)

    def _record(self, key: str) -> dict[str, object]:
        runs = self.server.live_account_runs
        return runs.record(runs.key("axi", key))

    def test_an_incomplete_reread_does_not_spend_the_daily_retries(self) -> None:
        """Lo que volvió a medias es de quien lo pidió, no del otro cron."""
        self._wealth_asked_for_it()
        self._come_back(snapshot_state(), NOW + timedelta(minutes=15))
        self.assertEqual(self._record(WEALTH_KEY)["outcome"], "partial")
        # El diario no hereda ni el intento ni el fallo: si entra, entra por
        # su propia cita y como primer intento.
        self.assertEqual(int(self._record(DAILY_KEY).get("attempts") or 0), 1)

    def test_a_complete_reread_does_not_close_the_daily_appointment(self) -> None:
        """Otras cuentas y otro periodo: lo que leyó no cumple esta cita.

        Es el caso de RoboForex del 2026-11-17: la relectura volvió entera y
        la cita de las 23:00 se dio por hecha sin haber entrado nunca.
        """
        self._wealth_asked_for_it()
        self._come_back(complete_snapshot_state(), NOW + timedelta(minutes=15))
        self.assertEqual(self._record(WEALTH_KEY)["outcome"], "success")
        posts = [row for row in self.requests if row[1].endswith("/snapshot")]
        self.assertEqual(len(posts), 1)
        self.assertEqual(self._record(DAILY_KEY)["outcome"], "running")

    def test_the_daily_turn_is_still_its_own_when_nobody_claimed_the_run(self) -> None:
        """Sin dueño la tanda es del diario: es el estado de antes del cambio."""
        self._come_back(complete_snapshot_state(), NOW)
        self.assertEqual(self._record(SNAPSHOT_KEY)["outcome"], "success")
        self.assertEqual(self._record(DAILY_KEY)["outcome"], "success")


if __name__ == "__main__":
    unittest.main()
