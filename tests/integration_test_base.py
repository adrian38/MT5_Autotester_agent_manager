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

class IntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ubs_agent.py").write_text(
            "import time\nprint('generation started', flush=True)\ntime.sleep(.08)\nprint('generation done', flush=True)\n",
            encoding="utf-8",
        )
        (self.root / "tester_template.ini").write_text("[Tester]\n", encoding="utf-8")
        (self.root / "assets").mkdir()
        (self.root / "assets" / "test_assets.ini").write_text(
            "[Forex]\nsymbols=EURUSD,GBPUSD\n\n[CommonAliases]\nEURUSD.A=EURUSD\n",
            encoding="utf-8",
        )
        (self.root / "ui_settings.ini").write_text(
            f"""[Paths]
set_files_root={self.root / 'sets'}
ubs_generation_output={self.root / 'outputs' / 'agent'}
template_path={self.root / 'tester_template.ini'}

[General]
delay=0
ubs_broker=TEST
ubs_account_type=DEMO
ubs_generation_count=1
ubs_variants_per_seed=1
ubs_max_seeds=1
ubs_agent_execute=0
ubs_generation_mode=production

[Multiterminal]
enabled=0
""",
            encoding="utf-8",
        )
        node_config = {
            "node_id": "test-node", "display_name": "Test Node", "project_dir": str(self.root),
            "broker": "TEST", "account_type": "DEMO", "token": "integration-secret",
        }
        config_path = self.root / "node.json"
        config_path.write_text(json.dumps(node_config), encoding="utf-8")
        self.controller = JobController(node_config, config_path)
        self.restart_requested = threading.Event()
        self.node = NodeServer(
            ("127.0.0.1", 0), self.controller, restart_callback=self.restart_requested.set
        )
        self.node_thread = threading.Thread(target=self.node.serve_forever, daemon=True)
        self.node_thread.start()
        node_url = f"http://127.0.0.1:{self.node.server_address[1]}"
        self.preferences_path = self.root / "launch_preferences.json"
        self.live_audit_settings_path = self.root / "live_audit_settings.json"
        self.manager = ManagerServer(("127.0.0.1", 0), {
            "nodes": [{"id": "test-node", "name": "Test Node", "url": node_url, "token": "integration-secret"}],
            "preferences_file": str(self.preferences_path),
            "live_audit_settings_file": str(self.live_audit_settings_path),
        })
        self.manager_thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.manager_thread.start()
        self.base = f"http://127.0.0.1:{self.manager.server_address[1]}"

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.manager.server_close()
        self.node.shutdown()
        self.node.server_close()
        self.temp.cleanup()

    def request(self, path: str, payload: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method="POST" if payload is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def wait_for_job(self, timeout: float = ASYNC_TIMEOUT) -> None:
        """Espera a que el job en curso deje de estar en 'running'.

        No afirma nada: cada test comprueba despues el estado que espera, que es
        lo que describe su intencion.
        """
        wait_until(
            lambda: self.controller.status()["job"]["status"] != "running", timeout,
        )
