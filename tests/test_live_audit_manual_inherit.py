"""La corrección manual sobrevive a la ejecución siguiente.

El nodo guarda un payload por uso y sólo sirve artefactos de la ejecución
visible: sin la copia del manager, cada pasada del cron tiraba los HTML que el
usuario había subido y la pantalla los volvía a pedir.
"""
from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager import live_audit_executions, live_audit_manual_inherit
from mt5_manager.live_audit_manual_memory import ManualReportMemory, canonical_name
from mt5_manager.manager_live_audit_routes import _handle_manual_reports
from tests.test_live_audit_analysis import payload as analysis_payload, profile

SOURCE = "XAUUSD_H1_Other_sets_GOLD_Various_Ol_6ecd56ee_g002_s015_v005.set"
STRATEGY = "AXI/STANDARD:5628"
NODE = "axi-standard-192-168-1-152"
KEY = "audit-105-mv4perus-1"
HTML = b"<html>reporte manual al 100%</html>"
WINDOW = ("2026-10-01T00:00:00+00:00", "2026-10-11T23:59:59.999999+00:00")


def artifact(**overrides) -> dict:
    base = {
        "strategy": STRATEGY, "symbol": "XAUUSD", "report_symbol": "XAUUSD",
        "source_set": SOURCE, "report_file": f"audit_003_{SOURCE[:-4]}.htm",
        "tester_trades": 3, "history_quality_pct": 40.0,
    }
    base.update(overrides)
    return base


def manual_artifact() -> dict:
    return artifact(
        report_file=f"manual_{SOURCE[:-4]}.htm", history_quality_pct=100.0,
        report_source="manual_upload", manual_report_original_file=canonical_name(SOURCE),
        manual_report_uploaded_at="2026-10-13T20:43:00+00:00",
        manual_report_sha256=hashlib.sha256(HTML).hexdigest(),
    )


def payload(execution: str, artifacts: list[dict], span: tuple[str, str] = WINDOW) -> dict:
    return {
        "audit_id": execution, "audit_key": KEY,
        "period_start": span[0], "period_end": span[1],
        "strategy_artifacts": artifacts,
    }


def analysed_payload(artifacts: list[dict]) -> dict:
    """La materia prima completa que el análisis sabe leer, con estos artefactos."""
    return {**analysis_payload(), **payload("run-20", artifacts)}


def memory(root: Path) -> ManualReportMemory:
    store = ManualReportMemory(root / "live_audit_manual_reports")
    store.remember(
        NODE, KEY, payload("run-12", [manual_artifact()]),
        {canonical_name(SOURCE).casefold(): HTML},
    )
    return store


class ManualReportMemoryTests(unittest.TestCase):
    def test_the_manager_keeps_its_own_copy_of_the_html_and_its_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            record = store.lookup(NODE, KEY, SOURCE)
            self.assertEqual(record["execution_id"], "run-12")
            self.assertEqual((record["period_start"], record["period_end"]), WINDOW)
            self.assertEqual(store.content(NODE, KEY, SOURCE), HTML)
            # Y lo recuerda un manager recién arrancado, que es el caso del cron.
            revived = ManualReportMemory(Path(temp) / "live_audit_manual_reports")
            self.assertEqual(revived.content(NODE, KEY, SOURCE), HTML)

    def test_a_tampered_copy_is_refused_instead_of_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            Path(store.lookup(NODE, KEY, SOURCE)["path"]).write_bytes(b"otra cosa")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                store.content(NODE, KEY, SOURCE)


class ManualReportInheritanceTests(unittest.TestCase):
    def setUp(self) -> None:
        live_audit_manual_inherit._attempts.clear()

    def test_a_new_execution_at_40_percent_gets_the_stored_report_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            state = {"audit": {"last_payload": payload("run-20", [artifact()])}}
            corrected = {"audit": {"last_payload": payload("run-20", [manual_artifact()])}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
                return_value=(200, corrected),
            ) as request:
                fresh = live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state)

        self.assertEqual(fresh["last_payload"]["strategy_artifacts"][0]["history_quality_pct"], 100.0)
        self.assertEqual(request.call_args.args[2], f"/api/v1/live-audits/{KEY}/reports")
        sent = request.call_args.args[3]["reports"]
        self.assertEqual(sent[0]["filename"], canonical_name(SOURCE))
        self.assertEqual(base64.b64decode(sent[0]["content_base64"]), HTML)
        # La ficha pasa a apuntar a la ejecución nueva: la siguiente hereda de ahí.
        self.assertEqual(store.lookup(NODE, KEY, SOURCE)["execution_id"], "run-20")

    def test_nothing_is_sent_when_the_new_execution_already_reaches_100(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            state = {"audit": {"last_payload": payload(
                "run-20", [artifact(history_quality_pct=100.0)],
            )}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
            ) as request:
                self.assertIsNone(
                    live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state),
                )
        request.assert_not_called()

    def test_a_report_of_another_window_is_explained_instead_of_applied(self) -> None:
        other = ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00")
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            rows = [artifact()]
            state = {"audit": {"last_payload": payload("run-20", rows, other)}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
            ) as request:
                self.assertIsNone(
                    live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state),
                )
        request.assert_not_called()
        self.assertEqual(rows[0]["manual_report_other_window"]["period_start"], WINDOW[0])
        self.assertEqual(rows[0]["manual_report_other_window"]["period_end"], WINDOW[1])

    def test_a_node_that_refuses_the_report_is_not_retried_for_ever(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            state = {"audit": {"last_payload": payload("run-20", [artifact()])}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
                return_value=(400, {"error": "History Quality debe ser 100%"}),
            ) as request:
                for _ in range(live_audit_manual_inherit.MAXIMUM_ATTEMPTS + 2):
                    self.assertIsNone(
                        live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state),
                    )
        self.assertEqual(request.call_count, live_audit_manual_inherit.MAXIMUM_ATTEMPTS)


class ManualReportWiringTests(unittest.TestCase):
    """Los dos caminos que leen una ejecución heredan igual, y la carga se guarda."""

    def setUp(self) -> None:
        live_audit_manual_inherit._attempts.clear()

    def test_the_analysis_path_sees_the_corrected_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            state = {"audit": {"last_payload": analysed_payload([artifact()])}}
            corrected = {"audit": {"last_payload": analysed_payload([manual_artifact()])}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
                return_value=(200, corrected),
            ):
                analysed = live_audit_executions.analysed_node_state(
                    {"id": NODE}, state, profile(), memory=store, node_id=NODE,
                )
        artifacts = analysed["audit"]["last_result"]["strategy_artifacts"]
        self.assertEqual(artifacts[0]["report_source"], "manual_upload")

    def test_uploading_through_the_manager_stores_the_copy_it_will_inherit(self) -> None:
        body = {"reports": [{
            "filename": canonical_name(SOURCE).upper(),
            "content_base64": base64.b64encode(HTML).decode("ascii"),
        }]}
        with tempfile.TemporaryDirectory() as temp:
            store = ManualReportMemory(Path(temp) / "live_audit_manual_reports")

            class Handler:
                def __init__(self):
                    settings = SimpleNamespace(state=lambda _node: {"profiles": {KEY: profile()}})
                    self.server = SimpleNamespace(
                        live_audit_settings=settings, live_audit_manual_memory=store,
                    )
                    self.response = None

                def _node(self, _node_id):
                    return {"id": NODE}

                def _body(self):
                    return body

                def _send_json(self, status, value):
                    self.response = (status, value)

            handler = Handler()
            with patch(
                "mt5_manager.manager_live_audit_routes.manager_http.node_request",
                return_value=(200, {"audit": {"last_payload": payload(
                    "run-20", [manual_artifact()],
                )}}),
            ):
                _handle_manual_reports(
                    handler, ["api", "nodes", NODE, "live-audits", KEY, "reports"],
                )
            self.assertEqual(handler.response[0], 200)
            self.assertEqual(store.content(NODE, KEY, SOURCE), HTML)


if __name__ == "__main__":
    unittest.main()
