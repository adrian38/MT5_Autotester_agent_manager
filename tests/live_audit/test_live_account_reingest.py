"""Recoger dos veces la misma tanda: lo que se rehace y lo que no se repite.

El `ingested` guardado lleva el `snapshot_id` y las versiones de serie
(`live_account_report.ingestion_key`), así que cambiar el criterio por lote
obliga a releer los mismos informes y a rehacer el archivo. Lo que **no** se
rehace es el trabajo del agente: la cuenta en la que no pudo entrar sigue con
su error de entonces y nadie la ha reintentado.

Hasta el 2026-11-18 ese fallo volvía a sonar en la campana como si fuera
nuevo. Ese día AXI publicó dos veces, a las 10:27:40 y a las 10:41:43, el
mismo `60268704@Axi-US51-Live: ... (-6, 'Terminal: Authorization failed')` con
las mismas cifras —4 cuentas y 3142 cierres—: no eran dos fallos, era uno
releído, porque el manager se había reiniciado con `SERIES_VERSION` cambiada.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.live_account import live_account_report, live_account_watch

from .live_account_fixtures import (
    REPORT, FakeServer, node_state, parsed_account, snapshot_state,
)

NOW = datetime(2026, 10, 21, 23, 30, tzinfo=timezone.utc)
UNREAD = "60082303@Axi-US50-Live"
"""La cuenta que el agente dejó sin leer, con el error que él mismo escribió."""


class ReingestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer(Path(self.folder.name), node_state())
        self.artifact_status = 200

    def tick(self, version: str = "") -> None:
        """Una vuelta del cron, opcionalmente con otro criterio por lote.

        Cambiar `SERIES_VERSION` es exactamente lo que hizo el despliegue de
        las 10:41: la tanda del agente es la misma y su `snapshot_id` no se
        mueve, pero la clave de ingesta sí, así que hay que releerla.
        """
        state = snapshot_state("completed")

        def node_request(_node, _method, path, _payload=None, timeout=10):
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_account_snapshot": True}}
            if path == "/api/v1/live-accounts":
                return 200, {"snapshot": state}
            raise AssertionError(f"ruta inesperada {path}")

        def artifact(_node, _path, timeout=180):
            return self.artifact_status, REPORT, "text/html"

        with ExitStack() as stack:
            stack.enter_context(mock.patch.multiple(
                "mt5_manager.manager.manager_http",
                node_request=node_request, node_artifact_request=artifact,
            ))
            stack.enter_context(mock.patch(
                "mt5_manager.live_account.live_account_report.account_from_html",
                return_value=parsed_account(),
            ))
            stack.enter_context(mock.patch(
                "mt5_manager.live_account.live_account_expectation_refresh"
                ".refresh_expectations", return_value=0,
            ))
            stack.enter_context(mock.patch(
                "mt5_manager.live_account.live_account_rates.refresh_after_snapshot",
                return_value=None,
            ))
            if version:
                stack.enter_context(
                    mock.patch.object(live_account_report, "SERIES_VERSION", version),
                )
            live_account_watch.tick(self.server, NOW)

    def cards(self) -> list[dict[str, Any]]:
        """Los avisos de tanda recogida, del más viejo al más nuevo.

        Por `id` y no por `created_at`: la campana los devuelve del más nuevo
        al más viejo y los dos de una prueba caen en el mismo segundo, así que
        ordenar por la fecha deja el más viejo el último y `[-1]` mira la
        recogida equivocada.
        """
        rows = [
            event for event in self.server.events()
            if event.get("kind") == "account_snapshot_collected"
        ]
        return sorted(rows, key=lambda row: int(row.get("id") or 0))

    def outcome(self) -> str:
        runs = self.server.live_account_runs
        return str(runs.record(runs.key("axi", "__snapshot__")).get("outcome") or "")


class FirstCollectionTests(ReingestBase):
    def test_the_agent_failure_is_news_the_first_time(self) -> None:
        """Lo de siempre, que es lo que no puede cambiar.

        La primera vez nadie había avisado de esa cuenta, así que el aviso
        amarillo y su «Sin leer» son exactamente lo que hay que ver.
        """
        self.tick()
        card = self.cards()[-1]
        self.assertEqual(card["level"], "warning")
        self.assertIn("Sin leer:", card["message"])
        self.assertIn(UNREAD, card["message"])
        self.assertNotIn("ya se avisó", card["message"])

    def test_the_same_criterion_does_not_collect_twice(self) -> None:
        """La recogida es idempotente por clave de ingesta."""
        self.tick()
        self.tick()
        self.assertEqual(len(self.cards()), 1)


class ReCollectionTests(ReingestBase):
    def setUp(self) -> None:
        super().setUp()
        self.tick()
        self.first = self.cards()[-1]

    def test_a_new_criterion_rereads_the_same_tanda(self) -> None:
        self.tick("balance-series-v8-de-prueba")
        cards = self.cards()
        self.assertEqual(len(cards), 2)
        # El mismo trabajo rehecho: las mismas cuentas y los mismos cierres.
        self.assertIn("1 cuenta(s) y 1 cierre(s)", cards[-1]["message"])

    def test_a_failure_already_announced_does_not_paint_the_bell_again(self) -> None:
        self.tick("balance-series-v8-de-prueba")
        card = self.cards()[-1]
        self.assertEqual(card["level"], "info")
        self.assertNotIn("Sin leer:", card["message"])
        self.assertIn("siguen sin leer desde el aviso anterior", card["message"])

    def test_the_outcome_stays_partial_so_the_daily_still_retries(self) -> None:
        """El color de la campana no es la política de reintentos.

        La cita sigue sin cumplirse —hay una cuenta sin leer— y `partial` es
        lo que `live_account_watch.UNFINISHED_OUTCOMES` necesita ver para que
        el diario vuelva a entrar. Bajar el aviso a `info` no puede tocar eso.
        """
        self.tick("balance-series-v8-de-prueba")
        self.assertEqual(self.outcome(), "partial")

    def test_a_failure_of_this_pass_is_news_even_when_rereading(self) -> None:
        """El 404 del relevo es de esta pasada, no viene del agente.

        Es el otro fallo del 2026-11-18: el agente dejó de servir los informes
        de la tanda que dejó de ser la visible. Ése no se ha avisado nunca, así
        que tiene que salir con su nombre y pintando la campana.

        Sale `error` y no `warning` porque con el único informe caído no se
        archivó nada: es el color de `failed`, no el del perdón. Lo que esta
        prueba vigila es que el perdón no se lo lleve por delante.
        """
        self.artifact_status = 404
        self.tick("balance-series-v8-de-prueba")
        card = self.cards()[-1]
        self.assertEqual(card["level"], "error")
        self.assertIn("Sin leer:", card["message"])
        self.assertIn("60082017@Axi-US50-Live", card["message"])
        self.assertIn("siguen sin leer desde el aviso anterior", card["message"])


if __name__ == "__main__":
    unittest.main()
