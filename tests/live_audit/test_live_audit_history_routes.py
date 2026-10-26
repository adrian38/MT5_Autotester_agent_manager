"""La ruta que sirve la serie y el vigilante que la archiva.

Separado de `test_live_audit_history`, que comprueba el archivo en si:
aqui esta lo que entra en la serie desde la pantalla y desde la cita.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_executions, live_audit_watch
from mt5_manager.live_audit_archive import LiveAuditArchive
from tests.test_live_audit_autolot import SET_STEM, audit_payload, audit_profile
from tests.live_audit.live_audit_cron_base import FakeServer as CronServer, audit_state
from tests.live_audit.live_audit_history_base import detail, trade


def completed_audit() -> dict[str, object]:
    return {
        "status": "completed", "audit_id": "e-1",
        "last_result": {
            "portfolio_id": 11, "portfolio_type": "aggressive",
            "completed_at": "2026-10-10T23:00:00+00:00",
            "period_start": "2026-10-03T00:00:00+00:00",
            "period_end": "2026-10-10T23:59:59+00:00",
            "account": {"currency": "EUR"},
            "comparison_detail": detail(
                "S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 4.0)],
            ),
        },
    }


class FakeHandler:
    """Lo que la ruta necesita del handler: un nodo, los perfiles y responder."""

    def __init__(self, archive: LiveAuditArchive) -> None:
        self.sent: list[tuple[int, object]] = []
        settings = type("Settings", (), {"state": staticmethod(
            lambda _node_id: {"profiles": {"a-1": {"deployment_name": "Cartera viva"}}},
        )})()
        self.server = type("Server", (), {
            "live_audit_archive": archive, "live_audit_settings": settings,
        })()

    @staticmethod
    def _node(node_id: str) -> dict[str, object]:
        return {"id": node_id, "name": "Nodo N"}

    def _send_json(self, status: int, payload: object) -> None:
        self.sent.append((status, payload))


class HistoryRouteTests(unittest.TestCase):
    """La ruta que alimenta la tabla, incluida la ejecución que se está viendo."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = LiveAuditArchive(Path(self.temporary.name) / "history")

    def _get(self, handler: FakeHandler) -> bool:
        from unittest import mock

        from mt5_manager import manager_schedule_routes

        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(200, {"audit": completed_audit()}),
        ):
            return manager_schedule_routes.handle_get(
                handler, type("Parsed", (), {"path": "", "query": ""})(),
                ["api", "nodes", "n", "live-audits", "a-1", "history"],
            )

    def test_it_archives_what_it_serves_so_the_series_never_misses_today(self) -> None:
        handler = FakeHandler(self.archive)
        self.assertTrue(self._get(handler))
        status, payload = handler.sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["audits"], 1)
        self.assertEqual(payload["portfolios"], [11])
        self.assertEqual(payload["node"]["name"], "Nodo N")
        self.assertTrue(self.archive.knows("n", "a-1", "e-1"))
        # Y servirla otra vez no la duplica.
        self._get(FakeHandler(self.archive))
        self.assertEqual(len(self.archive.index("n")), 1)

    def test_it_replaces_a_stale_copy_of_the_execution_it_serves(self) -> None:
        older = completed_audit()
        older["audit_id"] = "e-0"
        older["last_result"]["completed_at"] = "2026-10-02T23:00:00+00:00"
        older["last_result"]["period_start"] = "2026-09-25T00:00:00+00:00"
        older["last_result"]["period_end"] = "2026-10-02T23:59:59+00:00"
        live_audit_executions.archive_execution(
            FakeHandler(self.archive).server, {}, "n", "a-1", older,
            {"portfolio_id": 11},
        )
        stale = completed_audit()
        stale_detail = detail(
            "S:1", [trade("EURUSD", "2026-10-05T10:00:00+00:00", 0.0)],
        )
        stale_detail["operation_comparisons"][0]["real"] = None
        stale["last_result"]["comparison_detail"] = stale_detail
        live_audit_executions.archive_execution(
            FakeHandler(self.archive).server, {}, "n", "a-1", stale,
            {"portfolio_id": 11},
        )

        handler = FakeHandler(self.archive)
        self.assertTrue(self._get(handler))

        payload = handler.sent[0][1]
        execution = next(
            row for row in payload["executions"] if row["execution_id"] == "e-1"
        )
        comparison = execution["comparison_detail"]["operation_comparisons"][0]
        self.assertIsNotNone(comparison["real"])
        self.assertEqual(payload["audits"], 2)
        self.assertEqual(
            {row["execution_id"] for row in self.archive.index("n")}, {"e-0", "e-1"},
        )

    def test_the_analysis_revision_distinguishes_the_same_execution(self) -> None:
        audit = completed_audit()
        live_audit_executions.archive_execution(
            FakeHandler(self.archive).server, {}, "n", "a-1", audit,
            {"portfolio_id": 11},
        )
        revision = live_audit_executions.analysis_revision(audit["last_result"])
        self.assertTrue(self.archive.knows("n", "a-1", "e-1", "", revision))
        changed = completed_audit()["last_result"]
        changed["comparison_detail"]["operation_comparisons"][0]["real"] = None
        self.assertFalse(
            self.archive.knows(
                "n", "a-1", "e-1", "", live_audit_executions.analysis_revision(changed),
            )
        )

    def test_the_dashboard_variant_reads_the_archive_without_asking_the_node(self) -> None:
        """`?source=archive`: la portada no provoca el análisis de cada uso.

        Se repinta cada minuto y por cada tarjeta; un viaje al agente por uso
        sería pagar el análisis entero para enseñar una lista. Lo que hay
        archivado ya es lo que la última auditoría dejó dicho.
        """
        from unittest import mock

        from mt5_manager import manager_schedule_routes

        self._get(FakeHandler(self.archive))  # Deja la ejecución archivada.
        handler = FakeHandler(self.archive)
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            side_effect=AssertionError("la portada no habla con el nodo"),
        ):
            served = manager_schedule_routes.handle_get(
                handler, type("Parsed", (), {"path": "", "query": "source=archive"})(),
                ["api", "nodes", "n", "live-audits", "a-1", "history"],
            )
        self.assertTrue(served)
        status, payload = handler.sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["audits"], 1)
        self.assertEqual(payload["node"]["name"], "Nodo N")
        self.assertTrue(payload["executions"][0]["comparison_detail"])


def native_report_html() -> bytes:
    """El HTML nativo de MT5 del que sale el saldo histórico del autolote."""
    headers = [
        "Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price",
        "Order", "Commission", "Swap", "Profit", "Balance", "Comment",
    ]
    rows = [
        ["2026.09.22 09:00:00", "1", "", "balance", "", "0", "0", "", "0", "0",
         "1000", "1000", "deposit"],
        ["2026.09.22 10:00:00", "2", "EURUSD", "buy", "out", "0.1", "1.1", "1", "0", "0",
         "1000", "2000", ""],
    ]
    cell = lambda values: "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
    return ("<html><body>" + cell(["Deals"]) + cell(headers)
            + "".join(cell(row) for row in rows) + "</body></html>").encode()


def autolot_execution(report: dict[str, object]) -> dict[str, object]:
    """Un uso con autolote terminado, tal y como lo enseña el agente."""
    payload = audit_payload()
    payload["request"] = {**payload["request"], "audit_key": "11"}
    payload["real_account_report"] = report
    return {
        "status": "completed", "audit_id": payload["audit_id"],
        "finished_at": "2026-09-22T12:30:00+00:00",
        "last_result": None, "last_payload": payload,
    }


class WatchedArchiveTests(unittest.TestCase):
    """El vigilante archiva lo mismo que la pantalla, autolote incluido.

    Medido el 2026-10-20: de los usos con autolote (#109, #173, #170 y #148)
    ninguno había entrado en la serie, porque el vigilante analizaba sin el
    saldo histórico del informe nativo y el análisis se negaba a inventar un
    lote esperado. El nodo guarda un único payload por uso, así que esa semana
    se perdía para siempre en cuanto corría la cita siguiente.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        state = audit_state()
        state["profiles"]["11"] = {
            **audit_profile(), "portfolio_id": 11, "deployment_name": "Cartera viva",
            "live_trading": True, "autolot": {
                "enabled": True, "source_name": "riesgo.xlsx", "source_sha256": "a" * 64,
                "rules": {SET_STEM: {"dd_001": 10.0, "risk_pct": 10.0}},
            },
        }
        self.server = CronServer(root, state)
        self.server.live_audit_archive = LiveAuditArchive(root / "historico")

    def _tick(self, audit: dict[str, object], artifact: tuple) -> None:
        def request(_node, _method, path, payload=None, timeout=0):
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_audit_restore_account": True}}
            return 200, {"audits": {"11": audit}}

        with mock.patch("mt5_manager.manager_http.node_request", side_effect=request), \
                mock.patch(
                    "mt5_manager.manager_http.node_artifact_request", return_value=artifact,
                ):
            live_audit_watch.tick(self.server)

    def test_the_watcher_downloads_the_balance_the_autolot_needs(self) -> None:
        contents = native_report_html()
        self._tick(
            autolot_execution({
                "filename": "real.html", "sha256": hashlib.sha256(contents).hexdigest(),
            }),
            (200, contents, "text/html"),
        )
        self.assertTrue(self.server.live_audit_archive.knows("n", "11", "run-autolot"))
        entry = self.server.live_audit_archive.index("n")[0]
        self.assertEqual(entry["period_end"], "2026-09-22T23:59:59+00:00")
        self.assertEqual(entry["deployment_name"], "Cartera viva")

    def test_an_archive_that_cannot_be_done_leaves_a_trace_once(self) -> None:
        # Volver callando es lo que dejó la serie semanal a medias sin que
        # nadie pudiera enterarse hasta mirar la carpeta.
        self._tick(autolot_execution({}), (404, b"", "text/plain"))
        self.assertEqual(self.server.live_audit_archive.index("n"), [])
        events = [row for row in self.server.events() if row["kind"] == "archive_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("Cartera viva", events[0]["title"])
        self.assertIn("autolote", events[0]["message"])
        self._tick(autolot_execution({}), (404, b"", "text/plain"))
        self.assertEqual(
            1, len([row for row in self.server.events() if row["kind"] == "archive_failed"]),
        )


if __name__ == "__main__":
    unittest.main()
