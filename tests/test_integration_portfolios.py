from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path

from mt5_manager import node_portfolio_api
from mt5_manager.manager import PULSE_JOB_KEYS, ManagerServer
from mt5_manager.node import JobController, NodeServer
from mt5_manager.portfolio_service import PortfolioSource, normalize_settings
from portfolio_manager.ubs_portfolio import PortfolioResult, StrategyAllocation
from tests.helpers import (
    ASYNC_TIMEOUT,
    SLOW_JOB_TIMEOUT,
    assert_event,
    wait_until,
)
from tests.integration_test_base import IntegrationTestCase


class PortfolioIntegrationTests(IntegrationTestCase):
    def test_export_folder_endpoint_opens_the_manager_picker(self) -> None:
        # El modo se fija aqui a proposito. Antes se heredaba del entorno y
        # MT5_MANAGER_EXPORT_MODE=download (lo que pone docker-compose) hacia que
        # el test fallara con 400 al ejecutarlo dentro del contenedor, aunque
        # pasara en la maquina anfitriona. Un test no debe depender del runner.
        self.manager.export_mode = "folder"
        with mock.patch(
            "mt5_manager.manager_config.choose_directory", return_value=r"D:\exports"
        ) as picker:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/choose-export-folder",
                {"scope": "full_history"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"folder": r"D:\exports", "cancelled": False})
        picker.assert_called_once_with(None)

    def test_download_mode_refuses_the_local_folder_picker(self) -> None:
        # Este es el modo que corre de verdad en produccion (docker-compose lo
        # fija a download) y no habia ninguna prueba que lo cubriera.
        self.manager.export_mode = "download"
        with mock.patch("mt5_manager.manager_config.choose_directory") as picker:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(
                    "/api/nodes/test-node/portfolio-manager/choose-export-folder",
                    {"scope": "full_history"},
                )

        self.assertEqual(caught.exception.code, 400)
        picker.assert_not_called()

    def test_the_environment_overrides_the_configured_export_mode(self) -> None:
        # Precedencia intencionada: el contenedor impone el modo por entorno
        # aunque manager.json diga otra cosa.
        nodes = [{"id": "n", "name": "N", "url": "http://127.0.0.1:1", "token": "t"}]
        with mock.patch.dict("os.environ", {"MT5_MANAGER_EXPORT_MODE": "download"}):
            server = ManagerServer(("127.0.0.1", 0), {"nodes": nodes, "export_mode": "folder"})
        try:
            self.assertEqual(server.export_mode, "download")
        finally:
            server.server_close()

        with mock.patch.dict("os.environ", {}, clear=True):
            server = ManagerServer(("127.0.0.1", 0), {"nodes": nodes})
        try:
            self.assertEqual(server.export_mode, "folder")
        finally:
            server.server_close()

    def test_the_live_audit_scheduler_stays_disarmed_unless_it_is_switched_on(self) -> None:
        # La auditoría pausa el pipeline del agente, corre y lo reanuda. Sin
        # nadie delante eso es una ejecución desatendida sobre terminales MT5
        # reales: el 2026-08-21 dejó un terminal sin cuenta y dos días de
        # discovery puntuando 0 supervivientes. Se lanza a mano hasta que el MVP
        # esté cerrado, así que por defecto ni se arranca el hilo.
        nodes = [{"id": "n", "name": "N", "url": "http://127.0.0.1:1", "token": "t"}]
        scheduler_file = str(self.root / "scheduler-safety-test.json")
        with mock.patch.dict("os.environ", {}, clear=True):
            server = ManagerServer(("127.0.0.1", 0), {
                "nodes": nodes, "live_audit_scheduler_settings_file": scheduler_file,
            })
        try:
            self.assertFalse(server.live_audit_scheduler_enabled)
            self.assertIsNone(server.live_audit_thread)
            # Y aunque se llame al barrido a mano, no sale ninguna petición.
            with mock.patch("mt5_manager.manager_http.node_request") as node_request:
                server._run_due_live_audits()
            node_request.assert_not_called()
        finally:
            server.server_close()

        for switch in ({"live_audit_scheduler_enabled": True}, {"live_audit_scheduler_enabled": "si"}):
            with mock.patch.dict("os.environ", {}, clear=True):
                server = ManagerServer(("127.0.0.1", 0), {
                    "nodes": nodes, "live_audit_scheduler_settings_file": scheduler_file, **switch,
                })
            try:
                self.assertTrue(server.live_audit_scheduler_enabled, msg=f"con {switch}")
                self.assertIsNotNone(server.live_audit_thread)
            finally:
                server.server_close()

        # El entorno también rearma, para el contenedor.
        with mock.patch.dict("os.environ", {"MT5_MANAGER_LIVE_AUDIT_SCHEDULER": "1"}, clear=True):
            server = ManagerServer(("127.0.0.1", 0), {
                "nodes": nodes, "live_audit_scheduler_settings_file": scheduler_file,
            })
        try:
            self.assertTrue(server.live_audit_scheduler_enabled)
        finally:
            server.server_close()

        # Un valor que no se reconoce NO arma nada: un typo no puede lanzar
        # auditorías desatendidas.
        with mock.patch.dict("os.environ", {}, clear=True):
            server = ManagerServer(("127.0.0.1", 0), {
                "nodes": nodes, "live_audit_scheduler_settings_file": scheduler_file,
                "live_audit_scheduler_enabled": "quizá",
            })
        try:
            self.assertFalse(server.live_audit_scheduler_enabled)
        finally:
            server.server_close()

    def test_the_scheduler_uses_its_single_global_interval_in_days(self) -> None:
        nodes = [{"id": "n", "name": "N", "url": "http://127.0.0.1:1", "token": "t"}]
        server = ManagerServer(("127.0.0.1", 0), {
            "nodes": nodes,
            "live_audit_scheduler_settings_file": str(self.root / "scheduler-global-days.json"),
        })
        state = {
            "configured_audit_ids": ["11"],
            "profiles": {"11": {
                "portfolio_id": 11, "portfolio_type": "aggressive",
                "source_login": "1", "audit_interval_days": 999,
            }},
        }
        completed_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

        def request(_node, method, path, payload=None, timeout=0):
            if path == "/api/v1/status":
                return 200, {"capabilities": {"live_audit_restore_account": True}}
            if method == "GET" and path == "/api/v1/live-audits":
                return 200, {"audits": {"11": {
                    "status": "completed", "last_result": {"completed_at": completed_at},
                }}}
            return 202, {"audit": {"status": "queued"}}

        try:
            server.live_audit_scheduler_enabled = True
            with (
                mock.patch.object(server.live_audit_settings, "state", return_value=state),
                mock.patch.object(server.live_audit_settings, "credentials", return_value={}),
                mock.patch.object(
                    server.live_audit_settings, "restore_credentials",
                    return_value={"restore_password": "saved"},
                ),
                mock.patch("mt5_manager.manager_http.node_request", side_effect=request) as node_request,
            ):
                server.live_audit_scheduler_settings["interval_days"] = 7
                server._run_due_live_audits()
                self.assertFalse(any(call.args[1] == "POST" for call in node_request.call_args_list))

                node_request.reset_mock()
                server.live_audit_scheduler_settings["interval_days"] = 1
                server._run_due_live_audits()
                posts = [call for call in node_request.call_args_list if call.args[1] == "POST"]
                self.assertEqual(len(posts), 1)
        finally:
            server.server_close()

    def test_export_download_returns_a_zip_attachment(self) -> None:
        archive = {
            "filename": "PORTAFOLIO_9_A_M_C.zip",
            "content": b"PK\x03\x04test",
            "exported": 2,
            "missing": ["missing.set"],
        }
        with mock.patch.object(
            self.manager.portfolios, "export_archive", return_value=archive
        ) as export_archive:
            request = urllib.request.Request(
                self.base + "/api/nodes/test-node/portfolio-manager/export-download",
                data=json.dumps({"scope": "full_history", "portfolio_id": 9}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "application/zip")
                self.assertIn("PORTAFOLIO_9_A_M_C.zip", response.headers["Content-Disposition"])
                self.assertEqual(response.headers["X-Exported-Sets"], "2")
                self.assertEqual(response.headers["X-Missing-Sets"], "1")

        self.assertEqual(body, archive["content"])
        export_archive.assert_called_once_with("test-node", "full_history", 9)

    def test_symbol_family_can_be_listed_and_downloaded_as_a_selected_zip(self) -> None:
        family = {"symbol": "NFLX", "total": 2, "sets": [{"set_path": "a.set"}, {"set_path": "b.set"}]}
        with mock.patch.object(
            self.manager.portfolios, "symbol_sets", return_value=family
        ) as symbol_sets:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/symbol-sets",
                {"scope": "full_history", "symbol": "NFLX"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload, family)
        symbol_sets.assert_called_once_with("test-node", "full_history", "NFLX")

        archive = {
            "filename": "SETS_NFLX.zip", "content": b"PK\x03\x04sets",
            "exported": 2, "missing": [],
        }
        with mock.patch.object(
            self.manager.portfolios, "export_symbol_archive", return_value=archive
        ) as export_symbol_archive:
            request = urllib.request.Request(
                self.base + "/api/nodes/test-node/portfolio-manager/export-symbol-download",
                data=json.dumps({
                    "scope": "full_history", "symbol": "NFLX",
                    "set_paths": ["a.set", "b.set"],
                }).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertIn("SETS_NFLX.zip", response.headers["Content-Disposition"])
                self.assertEqual(response.headers["X-Exported-Sets"], "2")

        self.assertEqual(body, archive["content"])
        export_symbol_archive.assert_called_once_with(
            "test-node", "full_history", "NFLX", ["a.set", "b.set"]
        )

    def test_portfolio_alias_is_saved_through_the_coordinator(self) -> None:
        with mock.patch.object(
            self.manager.portfolios, "set_alias", return_value="Londres estable"
        ) as set_alias:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/alias",
                {"scope": "full_history", "portfolio_id": 36, "alias": "Londres estable"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"portfolio_id": 36, "alias": "Londres estable"})
        set_alias.assert_called_once_with(
            "test-node", "full_history", 36, "Londres estable"
        )

    def test_batch_exclusion_is_forwarded_to_the_node_api(self) -> None:
        node_result = {
            "quarantine_ids": [4, 7],
            "deleted": True,
            "portfolio_id": 40,
            "scope": "full_history",
        }
        with (
            mock.patch.object(
                node_portfolio_api, "exclude_portfolio_members", return_value=node_result
            ) as node_exclude,
            mock.patch.object(
                self.manager.portfolios, "exclude", side_effect=AssertionError("no debe escribir directamente")
            ),
            mock.patch.object(self.manager.portfolios, "invalidate_after_exclusion") as invalidate,
        ):
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/exclude",
                {
                    "scope": "full_history",
                    "portfolio_id": 40,
                    "set_paths": ["one.set", "two.set"],
                },
            )

        self.assertEqual(status, 201)
        self.assertEqual(payload, node_result)
        node_exclude.assert_called_once_with(mock.ANY, {
            "portfolio_id": 40,
            "set_paths": ["one.set", "two.set"],
            "scope": "full_history",
        })
        invalidate.assert_called_once_with("test-node")

    def _create_grid_exclusion_portfolio(self) -> tuple[PortfolioSource, int, str]:
        # El paquete Grid no está en la memoria del nodo, sino en la base del
        # manager. El endpoint de exclusión del nodo exige un portfolio_id que
        # exista allí, así que reenviarle la exclusión devolvía "Falta el
        # portafolio que contiene las estrategias".
        self.manager.nodes[0].update({
            "portfolio_project_dir": str(self.root),
            "portfolio_memory_path": str(self.root / "portfolio-grid-exclude.sqlite"),
            "portfolio_broker": "TEST",
            "portfolio_account_type": "DEMO",
        })
        (self.root / "portfolio-grid-exclude.sqlite").touch()
        self.manager.portfolios.settings_path = self.root / "portfolio_settings.json"
        grid = self.manager.portfolios._persistence_source("test-node", "grid")
        set_path = str(self.root / "grid_member.set")
        with grid.connect(write=True) as conn:
            portfolio_id = int(conn.execute(
                "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                ("2026-08-06", "Grid A/M/C", "grid_bundle", "grid_bundle", "grid",
                 json.dumps({"portfolio_bundle": True})),
            ).lastrowid)
            conn.execute(
                """insert into portfolio_allocations(
                   portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
                   net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe
                   ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (portfolio_id, "balanced", "Moderado Grid", set_path, "TEST/DEMO:1",
                 "EURUSD", 1, .01, 100, 20, 10, set_path, "H1"),
            )
            conn.commit()
        return grid, portfolio_id, set_path

    def _exclude_grid_member(self, portfolio_id: int, set_path: str) -> dict[str, object]:
        candidate = {
            "set_path": set_path,
            "source_memory_path": str(self.root / "portfolio-grid-exclude.sqlite"),
            "account_type": "TEST/DEMO",
            "source_candidate_id": 1,
            "target_symbol": "EURUSD",
            "period": "H1",
        }
        with (
            mock.patch.object(
                node_portfolio_api,
                "exclude_portfolio_members",
                side_effect=AssertionError("el nodo no debe recibir la exclusión Grid"),
            ),
            mock.patch.object(PortfolioSource, "candidate_rows", return_value=[candidate]),
        ):
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/exclude",
                {"scope": "grid", "portfolio_id": portfolio_id, "set_path": set_path},
            )
        self.assertEqual(status, 201)
        return payload

    def _assert_grid_exclusion(
        self, grid: PortfolioSource, portfolio_id: int, set_path: str, payload: dict[str, object]
    ) -> None:
        self.assertGreater(payload["quarantine_id"], 0)
        # Excluir ya no borra el paquete guardado en ningun ambito.
        self.assertFalse(payload["deleted"])
        self.assertEqual(payload["portfolio_id"], portfolio_id)
        with grid.connect() as conn:
            self.assertIsNotNone(
                conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone()
            )
            self.assertEqual(
                conn.execute("select set_path from portfolio_quarantine").fetchone()[0], set_path
            )

    def test_grid_exclusion_stays_in_the_manager_instead_of_calling_the_node(self) -> None:
        grid, portfolio_id, set_path = self._create_grid_exclusion_portfolio()
        payload = self._exclude_grid_member(portfolio_id, set_path)
        self._assert_grid_exclusion(grid, portfolio_id, set_path, payload)

    def test_single_exclusion_is_forwarded_to_the_node_api(self) -> None:
        # A single exclusion must also run on the node (the DB owner), not be
        # written by the manager over CIFS. Otherwise the quarantine/delete lands
        # in a WAL that is not coherent across the share and the portfolio keeps
        # reappearing after a "successful" (201) exclusion.
        node_result = {"quarantine_id": 5, "portfolio_id": 49, "scope": "monthly"}
        with mock.patch.object(
            node_portfolio_api, "exclude_portfolio_members", return_value=node_result
        ) as node_exclude:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/exclude",
                {
                    "scope": "monthly",
                    "portfolio_id": 49,
                    "set_path": "/data/roboforex/proj/outputs/sets/USDJPY_M15.set",
                },
            )

        self.assertEqual(status, 201)
        self.assertEqual(payload, {"quarantine_id": 5})
        node_exclude.assert_called_once_with(mock.ANY, {
            "portfolio_id": 49,
            "set_path": "/data/roboforex/proj/outputs/sets/USDJPY_M15.set",
            "scope": "monthly",
        })

    def test_portfolio_task_status_endpoint_is_lightweight(self) -> None:
        task_state = {
            "job": {"status": "idle"},
            "task": {"id": "delete-39", "status": "completed", "operation": "delete", "portfolio_id": 39},
            "tasks": [],
        }
        with mock.patch.object(self.manager.portfolios, "task_state", return_value=task_state) as status_call:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/task?scope=full_history"
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, task_state)
        status_call.assert_called_once_with("test-node", "full_history")

    def test_portfolio_stop_endpoint_reaches_the_coordinator(self) -> None:
        job = {"id": "ubs-stop", "status": "stopping"}
        with mock.patch.object(self.manager.portfolios, "stop", return_value=job) as stop:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/stop",
                {"scope": "full_history"},
            )

        self.assertEqual(status, 202)
        self.assertEqual(payload, {"job": job})
        stop.assert_called_once_with("test-node", "full_history")

    def test_saved_portfolio_improvement_reaches_the_independent_job(self) -> None:
        job = {"id": "improve-33", "status": "running", "operation": "improve"}
        with mock.patch.object(
            self.manager.portfolios,
            "start_saved_operation",
            return_value=job,
        ) as start:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/improve",
                {
                    "scope": "full_history",
                    "portfolio_id": 33,
                    "improvement_portfolio_type": "conservative",
                    "improvement_additions": 2,
                    "improvement_exclude_used_sets": True,
                    "improvement_allow_same_symbol": True,
                },
            )

        self.assertEqual(status, 202)
        self.assertEqual(payload, {"job": job})
        start.assert_called_once_with(
            "test-node",
            "full_history",
            33,
            "improve",
            {
                "improvement_portfolio_type": "conservative",
                "improvement_additions": 2,
                "improvement_exclude_used_sets": True,
                "improvement_allow_same_symbol": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
