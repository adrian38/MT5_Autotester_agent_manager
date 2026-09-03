from __future__ import annotations

import json
import tempfile
import threading
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from mt5_manager.manager import ManagerServer


class SymbolSyncRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.server = ManagerServer(("127.0.0.1", 0), {
            "nodes": [{"id": "broker-test", "url": "http://127.0.0.1:1",
                       "portfolio_project_dir": str(self.project)}],
            "live_audit_settings_file": str(self.project / "audit.json"),
            "live_audit_scheduler_settings_file": str(self.project / "scheduler.json"),
        })
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, action, payload):
        url = f"http://127.0.0.1:{self.server.server_address[1]}/api/nodes/broker-test/{action}"
        request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_each_action_reaches_the_selected_node_without_saving_credentials(self):
        routes = {
            "universe-sync": "/api/v1/universe/sync",
            "universe-history-preview": "/api/v1/universe/history-preview",
            "universe-history": "/api/v1/jobs/universe-history",
            "universe-disable-preview": "/api/v1/universe/disable-preview",
            "universe-disable-no-history": "/api/v1/universe/disable-no-history",
            "universe-trade-disabled-preview": "/api/v1/universe/trade-disabled-preview",
            "universe-disable-trade-disabled": "/api/v1/universe/disable-trade-disabled",
        }
        for action, target in routes.items():
            if action == "universe-sync":
                payload = {"password": "test-only", "login": "123"}
            elif action == "universe-trade-disabled-preview":
                payload = {}
            else:
                payload = {"symbols": ["TEST"]}
            with self.subTest(action=action), mock.patch(
                "mt5_manager.manager.node_request", return_value=(202, {"status": "running"})
            ) as forward:
                self.assertEqual(self.post(action, payload), (202, {"status": "running"}))
                forward.assert_called_once_with(self.server.nodes[0], "POST", target, payload, timeout=120)
                self.assertEqual(self.server.preferences, {})

    def test_stopping_a_node_waits_longer_than_a_status_poll(self):
        # Detener mata la etapa en curso y espera hasta 8 segundos a que el proceso
        # muera; pausar, lo mismo. Con el timeout general de 5 segundos el POST
        # expiraba y la pantalla daba el botón por fallido aunque el nodo lo
        # hubiera aplicado. Se comprobó el 2026-09-03 con una reparación de 4800
        # etapas que siguió corriendo después de pulsar «Detener».
        for action, target in (
            ("stop", "/api/v1/jobs/stop"),
            ("pause", "/api/v1/jobs/pause"),
            ("resume", "/api/v1/jobs/resume"),
        ):
            with self.subTest(action=action), mock.patch(
                "mt5_manager.manager.node_request", return_value=(200, {"status": "stopping"})
            ) as forward:
                self.assertEqual(self.post(action, {}), (200, {"status": "stopping"}))
                forward.assert_called_once_with(
                    self.server.nodes[0], "POST", target, {}, timeout=30,
                )

    def test_busy_and_old_nodes_return_their_error_without_retry(self):
        for status in (409, 404):
            with self.subTest(status=status), mock.patch(
                "mt5_manager.manager.node_request", return_value=(status, {"error": "node error"})
            ) as forward:
                self.assertEqual(self.post("universe-sync", {}), (status, {"error": "node error"}))
                forward.assert_called_once()

    def test_timeout_does_not_retry_a_possible_completed_mutation(self):
        with mock.patch("mt5_manager.manager.node_request", side_effect=TimeoutError("timeout")) as forward:
            self.assertEqual(self.post("universe-sync", {})[0], 502)
            forward.assert_called_once()

    def test_dev_rejects_a_disallowed_agent_before_contacting_it(self):
        with mock.patch("mt5_manager.manager.dev_branch.assert_writable", side_effect=ValueError("denied")), mock.patch(
            "mt5_manager.manager.node_request"
        ) as forward:
            self.assertEqual(self.post("universe-history", {}), (400, {"error": "denied"}))
            forward.assert_not_called()

    def test_dev_rejects_unknown_destination(self):
        self.server.nodes[0].pop("portfolio_project_dir")
        with mock.patch("mt5_manager.manager.dev_branch.is_active", return_value=True), mock.patch(
            "mt5_manager.manager.node_request"
        ) as forward:
            self.assertEqual(self.post("universe-sync", {})[0], 400)
            forward.assert_not_called()

    def test_manager_reaches_ic_fork_syncs_and_launches_existing_probe(self):
        agent = Path(__file__).resolve().parents[2] / "MT5_Autotester_agent_IC" / "MT5_Autotester_agent"
        if not (agent / "manager_node_runtime/universe_service.py").is_file():
            self.skipTest("Copia ICTrading con universo remoto no montada")
        # The fork must run in its own interpreter: manager and agent have
        # separate ubs/ and portfolio_manager/ packages with the same names.
        fixture = r'''
import json, sys
from pathlib import Path
from unittest.mock import patch
root = Path(sys.argv[1])
from manager_node_runtime.node import JobController, NodeServer, VALUE_OPTIONS
from ubs.mt5_symbol_extract import SymbolExtractionResult, ExtractedSymbol
(root / 'assets').mkdir()
(root / 'assets/ictrading_assets.ini').write_text('[Forex]\nsymbols=EURUSD,OLD\n')
flags = sorted(VALUE_OPTIONS | {'--probe-universe-history', '--execute-backtests', '--probe-history-timeframe'})
(root / 'ubs_agent.py').write_text('FLAGS = ' + repr(flags) + '\nprint("Todos los backtests han terminado", flush=True)\n')
(root / 'ui_settings.ini').write_text('[Paths]\nubs_ex5_file=expert.ex5\nubs_generation_output=' + str(root / 'outputs') + '\n[General]\nubs_broker=ICTRADING\nubs_account_type=STANDARD\nubs_agent_from_date=2020.01.01\n')
config = {'project_dir':str(root), 'node_id':'ic', 'broker':'ICTRADING', 'account_type':'STANDARD', 'token':'fixture-token'}
controller = JobController(config, root / 'node.json')
server = NodeServer(('127.0.0.1', 0), controller)
(root / 'fixture-port.json').write_text(json.dumps(server.server_address[1]))
extraction = SymbolExtractionResult((ExtractedSymbol('EURUSD'), ExtractedSymbol('NEW')), None, None, '')
with patch('manager_node_runtime.universe_service.extract_symbols_from_mt5', return_value=extraction):
    server.serve_forever()
'''
        process = subprocess.Popen([sys.executable, "-u", "-c", fixture, str(self.project)],
                                   cwd=agent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def stop():
            process.terminate()
            process.wait(timeout=5)
        self.addCleanup(stop)
        port_file = self.project / "fixture-port.json"
        deadline = time.monotonic() + 10
        while not port_file.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(port_file.exists(), "El nodo fixture no pudo iniciar")
        node = self.server.nodes[0]
        node.update(url=f"http://127.0.0.1:{json.loads(port_file.read_text())}", token="fixture-token")
        status, result = self.post("universe-sync", {})
        self.assertEqual(status, 200)
        self.assertEqual((result["total"], result["added"], result["removed"]), (2, 1, 1))
        policy = json.loads((self.project / "outputs/ubs_disabled_symbols_ICTRADING_STANDARD.json").read_text())
        self.assertEqual(policy["disabled"], ["OLD"])
        status, preview = self.post("universe-history-preview", {})
        self.assertEqual((status, preview["pending"]), (200, 2))
        status, job = self.post("universe-history", {})
        self.assertEqual(status, 202)
        self.assertIn("--probe-universe-history", job["command"])
        state_file = self.project / "runtime/ic/state.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = json.loads(state_file.read_text())
            if state["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual(state["status"], "completed")
        self.assertIn("Todos los backtests han terminado", Path(job["log_path"]).read_text())


if __name__ == "__main__":
    unittest.main()
