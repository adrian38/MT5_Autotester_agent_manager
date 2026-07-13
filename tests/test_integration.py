from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import closing
from unittest import mock
from pathlib import Path

from mt5_manager.manager import ManagerServer
from mt5_manager.node import JobController, NodeServer


class LocalIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ubs_agent.py").write_text(
            "import time\nprint('generation started', flush=True)\ntime.sleep(.08)\nprint('generation done', flush=True)\n",
            encoding="utf-8",
        )
        (self.root / "tester_template.ini").write_text("[Tester]\n", encoding="utf-8")
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
        self.node = NodeServer(("127.0.0.1", 0), self.controller)
        self.node_thread = threading.Thread(target=self.node.serve_forever, daemon=True)
        self.node_thread.start()
        node_url = f"http://127.0.0.1:{self.node.server_address[1]}"
        self.manager = ManagerServer(("127.0.0.1", 0), {
            "nodes": [{"id": "test-node", "name": "Test Node", "url": node_url, "token": "integration-secret"}]
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

    def test_manager_reaches_node_starts_job_and_reads_log(self) -> None:
        status, payload = self.request("/api/nodes")
        self.assertEqual(status, 200)
        self.assertFalse(payload["nodes"][0].get("offline", False))

        status, job = self.request("/api/nodes/test-node/start", {
            "generations": 1, "variants_per_seed": 1, "max_seeds": 1,
            "execute_backtests": False, "dry_run": True,
        })
        self.assertEqual(status, 202)
        self.assertEqual(job["status"], "running")
        deadline = time.time() + 3
        while time.time() < deadline and self.controller.status()["job"]["status"] == "running":
            time.sleep(.03)
        self.assertEqual(self.controller.status()["job"]["status"], "completed")

        status, logs = self.request("/api/nodes/test-node/logs?lines=20")
        self.assertEqual(status, 200)
        self.assertIn("generation done", "\n".join(logs["lines"]))

        with urllib.request.urlopen(self.base + "/", timeout=3) as response:
            self.assertIn(b"MT5 Autotester Manager", response.read())

    def test_controller_runs_selected_pipeline_in_order(self) -> None:
        memory = self.root / "pipeline.sqlite"
        with closing(sqlite3.connect(memory)) as conn:
            conn.executescript("""
                create table runs(id integer primary key, created_at text, generations integer, hidden integer default 0);
                create table candidates(id integer primary key, run_id integer, generation integer, status text);
                insert into runs values(7, '2026-07-13', 1, 0);
            """)
            conn.commit()
        self.controller.config["memory_path"] = str(memory)
        fake_command = [sys.executable, str(self.root / "ubs_agent.py")]
        with (
            mock.patch("mt5_manager.node.build_generation_command", return_value=(fake_command, self.root)),
            mock.patch("mt5_manager.node.build_pipeline_stage_command", return_value=(fake_command, self.root)),
        ):
            state = self.controller.start({
                "cycles": 1,
                "execute_backtests": True,
                "run_robustness": True,
                "run_final_tick": True,
                "run_final_tick_6m": True,
            })
            self.assertEqual(
                [step["action"] for step in state["pipeline"]],
                ["generation", "robustness", "final_tick", "final_tick_6m"],
            )
            deadline = time.time() + 5
            while time.time() < deadline and self.controller.status()["job"]["status"] == "running":
                time.sleep(.03)
        result = self.controller.status()["job"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["completed_stages"],
            ["cycle_1_generation", "cycle_1_robustness", "cycle_1_final_tick", "cycle_1_final_tick_6m"],
        )


if __name__ == "__main__":
    unittest.main()
