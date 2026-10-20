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


def manual_artifact(**overrides) -> dict:
    return artifact(
        report_file=f"manual_{SOURCE[:-4]}.htm", history_quality_pct=100.0,
        report_source="manual_upload", manual_report_original_file=canonical_name(SOURCE),
        manual_report_uploaded_at="2026-10-13T20:43:00+00:00",
        manual_report_sha256=hashlib.sha256(HTML).hexdigest(), **overrides,
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


BACKTEST = ("2026-10-01", "2026-10-31")
"""El rango con el que se lanzó el backtest guardado: mas ancho que la ventana."""


def memory(root: Path, covered: tuple[str, str] = BACKTEST) -> ManualReportMemory:
    """El almacen con el reporte del 13 de octubre ya dentro.

    `backtest_range` se dobla porque el HTML de prueba no es un informe de MT5
    de verdad; lo que se quiere probar aqui es la decision, no el parseo.
    """
    store = ManualReportMemory(root / "live_audit_manual_reports")
    with patch("mt5_manager.live_audit_manual_memory.backtest_range", return_value=covered):
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
            # La ruta guardada es relativa al almacén: el manager corre en Docker
            # y una ruta de Windows en el índice no se podría abrir desde dentro.
            self.assertNotIn(":", record["file"])
            # Y lo recuerda un manager recién arrancado, que es el caso del cron.
            revived = ManualReportMemory(Path(temp) / "live_audit_manual_reports")
            self.assertEqual(revived.content(NODE, KEY, SOURCE), HTML)

    def test_a_tampered_copy_is_refused_instead_of_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            (store.directory / store.lookup(NODE, KEY, SOURCE)["file"]).write_bytes(b"otra cosa")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                store.content(NODE, KEY, SOURCE)


class CoverageTests(unittest.TestCase):
    """La ventana no tiene que ser la misma: lo que cuentan son las operaciones."""

    def test_a_wider_backtest_serves_a_later_window(self) -> None:
        record = {"backtest_start": "2026-10-01", "backtest_end": "2026-10-31"}
        self.assertTrue(live_audit_manual_inherit.covers_window(
            record, ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00"),
        ))

    def test_a_backtest_that_stops_before_the_window_does_not(self) -> None:
        record = {"backtest_start": "2026-10-01", "backtest_end": "2026-10-11"}
        self.assertFalse(live_audit_manual_inherit.covers_window(
            record, ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00"),
        ))

    def test_without_a_readable_range_it_falls_back_to_the_same_window(self) -> None:
        record = {"period_start": WINDOW[0], "period_end": WINDOW[1]}
        self.assertTrue(live_audit_manual_inherit.covers_window(record, WINDOW))
        self.assertFalse(live_audit_manual_inherit.covers_window(
            record, ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00"),
        ))


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

    def test_a_legacy_upload_is_recovered_and_enters_the_manager_memory(self) -> None:
        """Las cargas anteriores al almacén no obligan al usuario a repetirlas."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = (
                root / "runtime" / NODE / "live_audits" / f"audit_{KEY}"
                / "run-12" / "reports" / f"manual_{Path(SOURCE).stem}.htm"
            )
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(HTML)
            store = ManualReportMemory(root / "manager-memory")
            state = {"audit": {"last_payload": payload("run-20", [artifact()])}}
            corrected = {"audit": {"last_payload": payload("run-20", [manual_artifact()])}}
            with patch(
                "mt5_manager.live_audit_manual_memory.backtest_range", return_value=BACKTEST,
            ), patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
                return_value=(200, corrected),
            ) as request:
                fresh = live_audit_manual_inherit.reapply(
                    {"portfolio_project_dir": str(root)}, NODE, store, state,
                )
            remembered = store.content(NODE, KEY, SOURCE)

        self.assertEqual(
            fresh["last_payload"]["strategy_artifacts"][0]["report_source"], "manual_upload",
        )
        self.assertEqual(base64.b64decode(
            request.call_args.args[3]["reports"][0]["content_base64"],
        ), HTML)
        self.assertEqual(remembered, HTML)

    def test_a_legacy_upload_from_another_window_is_not_recovered(self) -> None:
        later = ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = (
                root / "runtime" / NODE / "live_audits" / f"audit_{KEY}"
                / "run-12" / "reports" / f"manual_{Path(SOURCE).stem}.htm"
            )
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(HTML)
            store = ManualReportMemory(root / "manager-memory")
            state = {"audit": {"last_payload": payload("run-20", [artifact()], later)}}
            with patch(
                "mt5_manager.live_audit_manual_memory.backtest_range",
                return_value=("2026-10-01", "2026-10-11"),
            ), patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
            ) as request:
                self.assertIsNone(live_audit_manual_inherit.reapply(
                    {"portfolio_project_dir": str(root)}, NODE, store, state,
                ))
        request.assert_not_called()

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

    def test_a_later_window_inside_the_stored_backtest_is_inherited(self) -> None:
        """Lo que pidió el usuario: el backtest ancho aguanta las citas siguientes."""
        later = ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00")
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            rows = [artifact()]
            state = {"audit": {"last_payload": payload("run-20", rows, later)}}
            corrected = {"audit": {"last_payload": payload("run-20", [manual_artifact()], later)}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
                return_value=(200, corrected),
            ) as request:
                live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state)
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("manual_report_other_window", rows[0])

    def test_a_backtest_that_does_not_reach_these_dates_is_explained(self) -> None:
        other = ("2026-10-12T00:00:00+00:00", "2026-10-19T23:59:59+00:00")
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp), covered=("2026-10-01", "2026-10-11"))
            rows = [artifact()]
            state = {"audit": {"last_payload": payload("run-20", rows, other)}}
            with patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
            ) as request:
                self.assertIsNone(
                    live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state),
                )
        request.assert_not_called()
        self.assertEqual(rows[0]["manual_report_other_window"]["backtest_end"], "2026-10-11")

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


    def test_many_corrected_strategies_travel_in_several_uploads(self) -> None:
        """El nodo rechaza el lote entero por encima de 700 KB: se parte."""
        second = "XAUUSD_D1_GOLD_Goldbot_One__dailyK_g001_s016_v004.set"
        with tempfile.TemporaryDirectory() as temp:
            store = memory(Path(temp))
            store.remember(
                NODE, KEY, payload("run-12", [manual_artifact(source_set=second)]),
                {canonical_name(second).casefold(): HTML},
            )
            rows = [artifact(), artifact(source_set=second, strategy="AXI/STANDARD:1437")]
            state = {"audit": {"last_payload": payload("run-20", rows)}}
            corrected = {"audit": {"last_payload": payload("run-20", [manual_artifact()])}}
            with patch.object(live_audit_manual_inherit, "MAXIMUM_TOTAL_BYTES", len(HTML)), patch(
                "mt5_manager.live_audit_manual_inherit.manager_http.node_request",
                return_value=(200, corrected),
            ) as request:
                live_audit_manual_inherit.reapply({"id": NODE}, NODE, store, state)

        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            [call.args[3]["reports"][0]["filename"] for call in request.call_args_list],
            [canonical_name(SOURCE), canonical_name(second)],
        )


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
