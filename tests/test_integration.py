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


class LocalIntegrationTests(IntegrationTestCase):
    def _assert_node_job_and_logs(self) -> None:
        status, payload = self.request("/api/nodes")
        self.assertEqual(status, 200)
        self.assertFalse(payload["nodes"][0].get("offline", False))

        status, job = self.request("/api/nodes/test-node/start", {
            "generations": 1, "variants_per_seed": 1, "max_seeds": 1,
            "execute_backtests": False, "dry_run": True,
        })
        self.assertEqual(status, 202)
        self.assertEqual(job["status"], "running")
        self.wait_for_job()
        self.assertEqual(self.controller.status()["job"]["status"], "completed")

        status, logs = self.request("/api/nodes/test-node/logs?lines=20")
        self.assertEqual(status, 200)
        self.assertIn("generation done", "\n".join(logs["lines"]))

        status, runs = self.request("/api/nodes/test-node/runs?limit=100")
        self.assertEqual(status, 200)
        self.assertEqual(runs["runs"], [])

    def _save_and_assert_preferences(self) -> None:
        status, saved = self.request("/api/nodes/test-node/preferences", {
            "cycles": 3,
            "generation_mode": "discovery",
            "max_workers": 4,
            "repair_max_workers": 2,
            "regression_max_workers": 3,
            "repair_attempts": 3,
            "repair_after_generation": True,
            "run_robustness": True,
            "run_final_tick": True,
            "run_final_tick_6m": False,
            "run_regression": True,
            "repair_run_regression": False,
            "cleanup_after_run": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["preferences"]["cycles"], 3)
        self.assertEqual(saved["preferences"]["max_workers"], 4)
        self.assertEqual(saved["preferences"]["repair_max_workers"], 2)
        self.assertEqual(saved["preferences"]["regression_max_workers"], 3)
        self.assertEqual(saved["preferences"]["repair_attempts"], 3)
        self.assertTrue(saved["preferences"]["repair_after_generation"])
        self.assertTrue(saved["preferences"]["run_regression"])

        # La casilla de Reparar se recuerda aparte de la de la nueva ejecución.
        self.assertFalse(saved["preferences"]["repair_run_regression"])
        self.assertTrue(saved["preferences"]["cleanup_after_run"])
        persisted = json.loads(self.preferences_path.read_text(encoding="utf-8"))["test-node"]
        self.assertEqual(persisted["max_workers"], 4)
        self.assertEqual(persisted["repair_max_workers"], 2)
        self.assertEqual(persisted["regression_max_workers"], 3)

        status, payload = self.request("/api/nodes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["nodes"][0]["launch_preferences"]["generation_mode"], "discovery")

    def _assert_universe_controls(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=3) as response:
            self.assertIn(b"MT5 Autotester Manager", response.read())

        status, universe = self.request("/api/nodes/test-node/universe")
        self.assertEqual(status, 200)
        self.assertEqual(universe["summary"]["total"], 2)
        self.assertTrue(universe["symbols"][0]["generation_enabled"])

        status, universe = self.request("/api/nodes/test-node/universe", {
            "symbols": ["EURUSD"], "generation_enabled": False, "seeds_enabled": True,
        })
        self.assertEqual(status, 200)
        eurusd = next(row for row in universe["symbols"] if row["symbol"] == "EURUSD")
        self.assertFalse(eurusd["generation_enabled"])
        self.assertTrue(eurusd["seeds_enabled"])
        policy = json.loads((self.root / "outputs" / "ubs_disabled_symbols_TEST_DEMO.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["disabled"], ["EURUSD"])
        self.assertEqual(policy["seed_enabled_when_disabled"], ["EURUSD"])

        status, universe = self.request("/api/nodes/test-node/universe", {
            "symbols": ["EURUSD"], "generation_enabled": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(universe["summary"]["generation_enabled"], 2)

        with urllib.request.urlopen(self.base + "/universe.html?node=test-node", timeout=3) as response:
            self.assertIn("UNIVERSO DE ACTIVOS", response.read().decode("utf-8"))

    def test_manager_reaches_node_starts_job_and_reads_log(self) -> None:
        self._assert_node_job_and_logs()
        self._save_and_assert_preferences()
        self._assert_universe_controls()

    def test_manager_proxies_only_current_live_audit_reports(self) -> None:
        run_id = "run_1"
        with self.controller.live_audits.lock:
            self.controller.live_audits.states["9"] = {
                "audit_key": "9", "audit_id": run_id, "status": "completed",
            }
        reports = self.controller.live_audits.runtime_dir / "audit_9" / run_id / "reports"
        reports.mkdir(parents=True)
        report = reports / "strategy.htm"
        report.write_text("<html><body>MT5 report</body></html>", encoding="utf-8")
        (reports / "strategy.set").write_text("StartLots=0.06", encoding="utf-8")

        with urllib.request.urlopen(
            self.base + "/api/nodes/test-node/live-audits/9/artifacts/run_1/strategy.htm",
            timeout=3,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/html")
            self.assertIn(b"MT5 report", response.read())
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                self.base + "/api/nodes/test-node/live-audits/9/artifacts/run_1/strategy.set",
                timeout=3,
            )
        self.assertEqual(caught.exception.code, 404)

    def _assert_initial_live_audit_config(self) -> dict[str, object]:
        status, initial = self.request("/api/nodes/test-node/live-audit-config")
        self.assertEqual(status, 200)
        self.assertEqual(initial["phase"], "connected")
        self.assertEqual(initial["audit_states"], {})
        self.assertFalse(initial["configured"])
        self.assertEqual(initial["node"]["name"], "Test Node")
        self.assertEqual(initial["restore_account"]["login"], "11637157")
        self.assertFalse(initial["restore_account"]["configured"])
        return initial

    def _save_restore_account_and_scheduler(self) -> None:
        status, restored = self.request("/api/nodes/test-node/live-audit-restore-account", {
            "login": "333", "server": "Broker-Live", "password": "restore-secret",
        })
        self.assertEqual(status, 200)
        self.assertTrue(restored["restore_account"]["configured"])
        self.assertNotIn("restore-secret", json.dumps(restored))

        status, scheduler = self.request("/api/live-audit-scheduler-config")
        self.assertEqual(status, 200)
        self.assertFalse(scheduler["effective_enabled"])
        status, scheduler = self.request("/api/live-audit-scheduler-config", {
            "enabled": False, "interval_days": 17,
        })
        self.assertEqual(status, 200)
        self.assertEqual(scheduler["interval_days"], 17)
        self.assertNotIn("check_interval_minutes", scheduler)
        self.assertNotIn("startup_delay_seconds", scheduler)
        scheduler_path = self.live_audit_settings_path.with_name("live_audit_scheduler.json")
        persisted_scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted_scheduler, {"enabled": False, "interval_days": 17})

    @staticmethod
    def _live_audit_profiles(defaults: dict[str, object]) -> dict[str, dict[str, object]]:
        return {
            "11": {
                **defaults,
                "source_login": "001111",
                "source_server": "Broker-Live",
                "source_password": "real-11",
                "tester_login": "009111",
                "tester_server": "Broker-Demo",
                "tester_password": "test-11",
                "period_days": 7,
            },
            "12": {
                **defaults,
                "source_login": "002222",
                "source_server": "Broker-Live-2",
                "source_password": "real-12",
                "tester_login": "009222",
                "tester_server": "Broker-Demo-2",
                "tester_password": "test-12",
                "period_days": 30,
            },
        }

    def _save_and_assert_live_audit_profiles(self, initial: dict[str, object]) -> None:
        status, saved = self.request("/api/nodes/test-node/live-audit-config", {
            "selected_portfolio_ids": [11, 12],
            "profiles": self._live_audit_profiles(initial["defaults"]),
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["selected_portfolio_ids"], [11, 12])
        self.assertEqual(saved["configured_portfolio_ids"], [11, 12])
        self.assertEqual(saved["profiles"]["11"]["source_login"], "001111")
        self.assertEqual(saved["profiles"]["12"]["source_login"], "002222")
        self.assertEqual(saved["profiles"]["11"]["period_days"], 7)
        self.assertEqual(saved["profiles"]["12"]["period_days"], 30)
        self.assertEqual(saved["profiles"]["11"]["audit_interval_days"], 1)
        self.assertEqual(saved["profiles"]["11"]["min_tick_history_quality_pct"], 80.0)
        self.assertTrue(saved["credential_state"]["11"]["source_password_saved"])
        self.assertTrue(saved["credential_state"]["12"]["tester_password_saved"])
        self.assertNotIn("source_password", saved)
        self.assertNotIn("tester_password", saved)
        persisted = json.loads(self.live_audit_settings_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["test-node"]["profiles"]["11"]["period_days"], 7)
        self.assertEqual(persisted["test-node"]["profiles"]["12"]["period_days"], 30)
        encrypted = (self.root / "live_audit_credentials.json").read_text(encoding="utf-8")
        for secret in ("restore-secret", "real-11", "test-11", "real-12", "test-12"):
            self.assertNotIn(secret, encrypted)

    def _assert_live_audit_assets(self) -> None:
        with urllib.request.urlopen(self.base + "/live_audit.html?node=test-node", timeout=3) as response:
            self.assertIn(b"Auditor de cuenta real", response.read())
        with urllib.request.urlopen(self.base + "/live_audit.js", timeout=3) as response:
            self.assertIn(b"live-audit-config", response.read())
        with urllib.request.urlopen(self.base + "/live_audit.css", timeout=3) as response:
            self.assertIn(b"live-audit-configs", response.read())
        with urllib.request.urlopen(self.base + "/live_audit_result.html?node=test-node&audit=11", timeout=3) as response:
            self.assertIn(b"Comparaci", response.read())
        with urllib.request.urlopen(self.base + "/live_audit_result.js", timeout=3) as response:
            self.assertIn(b"operation_comparisons", response.read())
        with urllib.request.urlopen(self.base + "/live_audit_result.css", timeout=3) as response:
            self.assertIn(b"audit-operation-table", response.read())

    def test_manager_serves_and_persists_live_audit_configuration_without_the_node(self) -> None:
        initial = self._assert_initial_live_audit_config()
        self._save_restore_account_and_scheduler()
        self._save_and_assert_live_audit_profiles(initial)
        self._assert_live_audit_assets()

    def test_pulse_projects_the_same_state_as_nodes_but_without_its_peso(self) -> None:
        """`/api/pulse` existe para poder sondear desde un móvil.

        Lo que se comprueba no es solo que responda: es que diga exactamente lo
        mismo que `/api/nodes` sobre el trabajo en curso —si divergen, el aviso
        de «terminó» llega cuando no toca— y que no arrastre el payload del
        panel, que es la razón de que el endpoint exista.
        """
        status, job = self.request("/api/nodes/test-node/start", {
            "generations": 1, "variants_per_seed": 1, "max_seeds": 1,
            "execute_backtests": False, "dry_run": True,
        })
        self.assertEqual(status, 202)
        self.wait_for_job()

        status, pulse = self.request("/api/pulse")
        self.assertEqual(status, 200)
        node = pulse["nodes"][0]
        self.assertEqual(node["id"], "test-node")
        self.assertEqual(node["name"], "Test Node")
        self.assertFalse(node["offline"])
        self.assertEqual(node["job"]["status"], "completed")
        self.assertEqual(node["job"]["job_type"], "generation")
        self.assertIsNotNone(node["job"]["job_id"])
        self.assertIsNotNone(node["job"]["finished_at"])

        # Sin portfolio_project_dir no hay motor central para ese nodo: la lista
        # va vacía en lugar de reventar.
        self.assertEqual(pulse["portfolios"], [])

        # El job del pulso es un recorte del de /api/nodes, no otra lectura.
        _status, full = self.request("/api/nodes")
        full_job = full["nodes"][0]["job"]
        # `task_queue` es {count, items}: contar el dict daría el nº de claves.
        self.assertEqual(node["queued"], full["nodes"][0]["task_queue"]["count"])
        self.assertEqual(set(node["job"]), set(PULSE_JOB_KEYS))
        for key in PULSE_JOB_KEYS:
            self.assertEqual(node["job"][key], full_job[key], key)

        # El recorte tiene que notarse: el comando, el pipeline y el snapshot de
        # la base son justo lo que no puede viajar en cada sondeo.
        self.assertLess(len(json.dumps(pulse)), len(json.dumps(full)) / 2)
        self.assertNotIn("command", json.dumps(pulse))

    def test_manager_remembers_every_generation_field_that_was_launched(self) -> None:
        launch = {
            "cycles": 2, "generations": 3, "variants_per_seed": 7, "max_seeds": 11,
            "generation_mode": "discovery", "max_workers": 10, "execute_backtests": True,
            "random_seed": 20260812,
            "run_robustness": True, "run_final_tick": True, "run_final_tick_6m": True,
            "run_regression": True, "repair_after_generation": True,
            "repair_max_workers": 6, "repair_attempts": 4, "cleanup_after_run": False,
            "dry_run": True,
        }
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(202, {"job_type": "generation", "status": "running"}),
        ):
            status, _job = self.request("/api/nodes/test-node/start", dict(launch))
        self.assertEqual(status, 202)

        stored = self.manager.preferences_for("test-node")
        for key, value in launch.items():
            self.assertEqual(stored[key], value, key)
        persisted = json.loads(self.preferences_path.read_text(encoding="utf-8"))["test-node"]
        self.assertEqual(persisted["max_workers"], 10)
        self.assertTrue(persisted["run_regression"])

        status, payload = self.request("/api/nodes")
        self.assertEqual(status, 200)
        node = payload["nodes"][0]
        self.assertEqual(node["launch_preferences"]["max_workers"], 10)
        self.assertEqual(node["launch_preferences"]["random_seed"], 20260812)
        self.assertTrue(node["launch_preferences"]["run_regression"])
        # El diálogo relee estos tres desde launch_defaults, no desde launch_preferences.
        self.assertEqual(node["launch_defaults"]["generations"], 3)
        self.assertEqual(node["launch_defaults"]["variants_per_seed"], 7)
        self.assertEqual(node["launch_defaults"]["max_seeds"], 11)

    def test_stage_terminals_never_overwrite_the_generation_workers(self) -> None:
        status, _saved = self.request("/api/nodes/test-node/preferences", {"max_workers": 10})
        self.assertEqual(status, 200)
        for action in ("repair", "regression"):
            with mock.patch(
                "mt5_manager.manager_http.node_request",
                return_value=(202, {"job_type": action, "status": "running"}),
            ):
                self.request(f"/api/nodes/test-node/{action}", {"run_ids": [7], "max_workers": 2})
            self.assertEqual(self.manager.preferences_for("test-node")["max_workers"], 10, action)

    def test_a_rejected_launch_is_not_remembered(self) -> None:
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(400, {"error": "La tarea ya no esta en la cola"}),
        ):
            with self.assertRaises(urllib.error.HTTPError):
                self.request("/api/nodes/test-node/start", {"max_workers": 10})
        self.assertEqual(self.manager.preferences_for("test-node"), {})

    def test_manager_proxies_regression_jobs_to_the_node(self) -> None:
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(202, {"job_type": "regression", "status": "running"}),
        ) as request_node:
            status, payload = self.request(
                "/api/nodes/test-node/regression",
                {"run_ids": [7, 9], "max_workers": 5, "cleanup_after_run": True},
            )

        self.assertEqual(status, 202)
        self.assertEqual(payload["job_type"], "regression")
        node, method, path, body = request_node.call_args.args
        self.assertEqual(node["id"], "test-node")
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/jobs/regression")
        self.assertEqual(
            body,
            {"run_ids": [7, 9], "max_workers": 5, "cleanup_after_run": True},
        )

    def test_manager_proxies_historical_cleanup_to_the_node(self) -> None:
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(202, {"job_type": "cleanup", "status": "running"}),
        ) as request_node:
            status, payload = self.request("/api/nodes/test-node/cleanup", {})

        self.assertEqual(status, 202)
        self.assertEqual(payload["job_type"], "cleanup")
        node, method, path, body = request_node.call_args.args
        self.assertEqual(node["id"], "test-node")
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/jobs/cleanup")
        self.assertEqual(body, {})

    def test_manager_reads_runs_with_extended_timeout(self) -> None:
        with mock.patch(
            "mt5_manager.manager_http.node_request",
            return_value=(200, {"runs": [{"id": 7}]}),
        ) as request_node:
            status, payload = self.request("/api/nodes/test-node/runs?limit=100&offset=200")

        self.assertEqual(status, 200)
        self.assertEqual(payload["runs"], [{"id": 7}])
        node, method, path = request_node.call_args.args
        self.assertEqual(node["id"], "test-node")
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/api/v1/runs?limit=100&offset=200")
        self.assertEqual(request_node.call_args.kwargs, {"timeout": 120})

    def test_manager_submits_repair_without_waiting_for_the_node_response(self) -> None:
        request_started = threading.Event()
        release_request = threading.Event()
        request_finished = threading.Event()

        def slow_request(*_args: object, **_kwargs: object) -> tuple[int, dict]:
            request_started.set()
            # Valvula de seguridad, no un plazo: el `finally` de abajo siempre
            # libera. Si vence antes de tiempo, `request_finished` se marca sola
            # y la comprobacion de que la peticion sigue en vuelo falla sin que
            # haya pasado nada malo.
            release_request.wait(ASYNC_TIMEOUT)
            request_finished.set()
            return 202, {"job_type": "repair", "status": "running"}

        try:
            with mock.patch(
                "mt5_manager.manager_http.node_request", side_effect=slow_request
            ) as request_node:
                status, payload = self.request(
                    "/api/nodes/test-node/repair",
                    {
                        "run_ids": [7], "max_workers": 6,
                        "repair_attempts": 4, "retry_low_quality": True,
                    },
                )
                self.assertEqual(status, 202)
                self.assertEqual(payload["status"], "submitting")
                self.assertFalse(payload["queued"])
                assert_event(
                    self, request_started, "el manager no llego a llamar al nodo",
                )
                self.assertFalse(request_finished.is_set())
                node, method, path, body = request_node.call_args.args
                self.assertEqual(node["id"], "test-node")
                self.assertEqual(method, "POST")
                self.assertEqual(path, "/api/v1/jobs/repair")
                self.assertEqual(
                    body,
                    {
                        "run_ids": [7], "max_workers": 6,
                        "repair_attempts": 4, "retry_low_quality": True,
                    },
                )
                self.assertEqual(request_node.call_args.kwargs, {"timeout": 3600})
        finally:
            release_request.set()
        assert_event(
            self, request_finished, "la peticion al nodo no termino tras liberarla",
        )

    def test_portfolio_delete_endpoint_accepts_a_background_task(self) -> None:
        task = {
            "id": "delete-37", "status": "pending", "operation": "delete", "portfolio_id": 37,
        }
        with mock.patch.object(self.manager.portfolios, "delete", return_value=task) as delete:
            status, payload = self.request(
                "/api/nodes/test-node/portfolio-manager/delete",
                {"scope": "full_history", "portfolio_id": 37},
            )

        self.assertEqual(status, 202)
        self.assertEqual(payload["task"], task)
        delete.assert_called_once_with("test-node", "full_history", 37)

    def test_pause_and_resume_reach_the_node_through_the_manager(self) -> None:
        with mock.patch.object(
            self.controller, "pause", return_value={"status": "pausing"}
        ) as pause:
            status, payload = self.request("/api/nodes/test-node/pause", {})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "pausing")
        pause.assert_called_once_with()

        with mock.patch.object(
            self.controller, "resume", return_value={"status": "running", "current_stage": "robustness"}
        ) as resume:
            status, payload = self.request("/api/nodes/test-node/resume", {})
        self.assertEqual(status, 202)
        self.assertEqual(payload["current_stage"], "robustness")
        resume.assert_called_once_with()

    def test_pausing_with_nothing_running_surfaces_the_node_error(self) -> None:
        # El nodo responde 409 a los conflictos de estado y el manager lo propaga
        # tal cual, para que la interfaz muestre el motivo real.
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/nodes/test-node/pause", {})
        self.assertEqual(caught.exception.code, 409)
        self.assertIn("pausar", json.loads(caught.exception.read())["error"])

    def test_application_restart_reaches_the_embedded_node(self) -> None:
        status, payload = self.request("/api/nodes/test-node/restart", {})

        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "restarting")
        assert_event(
            self, self.restart_requested, "el nodo embebido no recibio el reinicio",
        )

    def test_application_restart_preserves_a_paused_pipeline(self) -> None:
        with self.controller.lock:
            self.controller.state.update({
                "status": "paused",
                "pipeline": [{"action": "generation"}],
                "current_step_index": 0,
                "paused_at": "2026-08-20T20:36:39+02:00",
            })
            self.controller._persist()

        status, payload = self.request("/api/nodes/test-node/restart", {})

        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "restarting")
        assert_event(
            self, self.restart_requested, "el nodo embebido no recibio el reinicio",
        )
        self.assertEqual(self.controller.state["status"], "paused")
        self.assertEqual(self.controller.state["current_step_index"], 0)

    def test_manager_restart_has_its_own_async_endpoint_and_status(self) -> None:
        accepted = {"status": "starting", "step": "starting", "log": []}
        with mock.patch.object(self.manager.manager_restart, "start", return_value=accepted) as start:
            status, payload = self.request("/api/manager/restart", {})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "starting")
        start.assert_called_once_with()

        completed = {"status": "completed", "step": "completed", "log": ["ok"]}
        with mock.patch.object(
            self.manager.manager_restart, "status", return_value=completed
        ) as restart_status:
            status, payload = self.request("/api/manager/restart?lines=80")
        self.assertEqual(status, 200)
        self.assertEqual(payload["log"], ["ok"])
        restart_status.assert_called_once_with(log_lines=80)

    def test_application_restart_refuses_pending_work(self) -> None:
        with self.controller.lock:
            self.controller.queue.append({"id": "queued-test"})
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/nodes/test-node/restart", {})
            self.assertEqual(caught.exception.code, 409)
            self.assertIn("tareas pendientes", json.loads(caught.exception.read())["error"])
            self.assertFalse(self.restart_requested.is_set())
        finally:
            with self.controller.lock:
                self.controller.queue.clear()


if __name__ == "__main__":
    unittest.main()
