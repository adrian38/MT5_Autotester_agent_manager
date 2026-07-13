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
        self.node = NodeServer(("127.0.0.1", 0), self.controller)
        self.node_thread = threading.Thread(target=self.node.serve_forever, daemon=True)
        self.node_thread.start()
        node_url = f"http://127.0.0.1:{self.node.server_address[1]}"
        self.preferences_path = self.root / "launch_preferences.json"
        self.manager = ManagerServer(("127.0.0.1", 0), {
            "nodes": [{"id": "test-node", "name": "Test Node", "url": node_url, "token": "integration-secret"}],
            "preferences_file": str(self.preferences_path),
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

        status, runs = self.request("/api/nodes/test-node/runs?limit=100")
        self.assertEqual(status, 200)
        self.assertEqual(runs["runs"], [])

        status, saved = self.request("/api/nodes/test-node/preferences", {
            "cycles": 3,
            "generation_mode": "discovery",
            "max_workers": 4,
            "repair_attempts": 3,
            "repair_after_generation": True,
            "run_robustness": True,
            "run_final_tick": True,
            "run_final_tick_6m": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["preferences"]["cycles"], 3)
        self.assertEqual(saved["preferences"]["repair_attempts"], 3)
        self.assertTrue(saved["preferences"]["repair_after_generation"])
        self.assertEqual(json.loads(self.preferences_path.read_text(encoding="utf-8"))["test-node"]["max_workers"], 4)

        status, payload = self.request("/api/nodes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["nodes"][0]["launch_preferences"]["generation_mode"], "discovery")

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

        portfolio_memory = self.root / "portfolio.sqlite"
        with closing(sqlite3.connect(portfolio_memory)) as conn:
            conn.executescript("""
                create table portfolios(
                    id integer primary key, created_at text, name text, type text, portfolio_type text,
                    account_capital real, capital real, actual_valley_dd real, target_valley_dd real,
                    valley_usage_pct real, actual_point_dd real, target_point_dd real, point_usage_pct real,
                    total_net_profit real, total_lot real, total_units integer, active_strategies integer,
                    target_strategies integer, stop_reason text, binding_constraint text,
                    portfolio_scope text, target_month integer, metrics_json text
                );
                create table portfolio_allocations(
                    id integer primary key, portfolio_id integer, variant_key text, variant_label text,
                    set_id text, candidate_id text, symbol text, timeframe text, units integer, lot real,
                    lot_size_step real, net_profit_contribution real, standalone_valley_dd real,
                    standalone_point_dd real, set_path text, margin_required real, margin_pct real
                );
                insert into portfolios values(
                    11,'2026-07-13','Normal','balanced','balanced',10000,10000,300,1000,30,120,400,30,
                    2400,0.03,3,2,3,'','','full_history',null,'{"stress_bootstrap":{"valley_dd_p95":420}}'
                );
                insert into portfolios values(
                    12,'2026-07-13','Julio','balanced','balanced',10000,10000,250,1000,25,90,400,22.5,
                    1800,0.02,2,1,2,'','','monthly',7,'{}'
                );
                insert into portfolio_allocations values(
                    1,11,'balanced','Moderado','set-1','42','EURUSD','H1',3,0.03,0.01,2400,300,120,
                    'C:/sets/eurusd.set',25,0.25
                );
            """)
            conn.commit()
        self.controller.config["memory_path"] = str(portfolio_memory)
        status, portfolios = self.request("/api/nodes/test-node/portfolios?scope=full_history")
        self.assertEqual(status, 200)
        self.assertEqual(portfolios["portfolios"][0]["id"], 11)
        status, detail = self.request("/api/nodes/test-node/portfolios/11?scope=full_history")
        self.assertEqual(status, 200)
        self.assertEqual(detail["portfolio"]["members"][0]["symbol"], "EURUSD")
        status, monthly = self.request("/api/nodes/test-node/portfolios?scope=monthly")
        self.assertEqual(status, 200)
        self.assertEqual(monthly["portfolios"][0]["target_month"], 7)
        with urllib.request.urlopen(self.base + "/portfolios.html?node=test-node&scope=monthly", timeout=3) as response:
            self.assertIn("Portafolios guardados", response.read().decode("utf-8"))

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
            mock.patch("mt5_manager.node.pipeline_stage_pending_count", return_value=1),
        ):
            state = self.controller.start({
                "cycles": 2,
                "execute_backtests": True,
                "run_robustness": True,
                "run_final_tick": True,
                "run_final_tick_6m": True,
                "repair_after_generation": True,
                "repair_attempts": 2,
            })
            repair_actions = [
                "result", "robustness", "final_tick", "final_tick_quality",
                "final_tick_6m", "final_tick_6m_quality",
            ]
            expected_actions = []
            for _cycle in (1, 2):
                expected_actions.append("generation")
                expected_actions.extend(repair_actions * 2)
            self.assertEqual([step["action"] for step in state["pipeline"]], expected_actions)
            self.assertTrue(state["request"]["repair_after_generation"])
            self.assertEqual(state["request"]["repair_attempts"], 2)
            self.assertTrue(all(
                step.get("max_workers") == 1
                for step in state["pipeline"] if step["action"] != "generation"
            ))
            deadline = time.time() + 5
            while time.time() < deadline and self.controller.status()["job"]["status"] == "running":
                time.sleep(.03)
        result = self.controller.status()["job"]
        self.assertEqual(result["status"], "completed")
        expected_stages = []
        for cycle in (1, 2):
            expected_stages.append(f"cycle_{cycle}_generation")
            expected_stages.extend(
                f"cycle_{cycle}_attempt_{attempt}_{action}"
                for attempt in (1, 2)
                for action in repair_actions
            )
        self.assertEqual(result["completed_stages"], expected_stages)

    def test_repair_runs_all_tests_per_selected_run_with_one_worker(self) -> None:
        fake_command = [sys.executable, str(self.root / "ubs_agent.py")]
        with (
            mock.patch("mt5_manager.node.build_pipeline_stage_command", return_value=(fake_command, self.root)),
            mock.patch("mt5_manager.node.pipeline_stage_pending_count", return_value=1),
        ):
            state = self.controller.start_repair({
                "run_ids": [7, 9], "repair_attempts": 2, "retry_low_quality": True,
            })
            self.assertEqual(state["request"]["max_workers"], 1)
            self.assertEqual(state["request"]["repair_attempts"], 2)
            self.assertEqual(len(state["pipeline"]), 24)
            deadline = time.time() + 5
            while time.time() < deadline and self.controller.status()["job"]["status"] == "running":
                time.sleep(.03)
        result = self.controller.status()["job"]
        self.assertEqual(result["status"], "completed")
        actions = ["result", "robustness", "final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"]
        expected = [
            f"run_{run_id}_attempt_{attempt}_{action}"
            for run_id in (7, 9)
            for attempt in (1, 2)
            for action in actions
        ]
        self.assertEqual(result["completed_stages"], expected)

    def test_repair_skips_empty_stages_without_spawning_a_process(self) -> None:
        with (
            mock.patch("mt5_manager.node.pipeline_stage_pending_count", return_value=0),
            mock.patch("mt5_manager.node.build_pipeline_stage_command") as build_command,
            mock.patch("mt5_manager.node.subprocess.Popen") as popen,
        ):
            state = self.controller.start_repair({"run_ids": [7], "retry_low_quality": True})
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completed_stages"], [])
        self.assertEqual(
            state["skipped_stages"],
            [
                "run_7_attempt_1_result", "run_7_attempt_1_robustness",
                "run_7_attempt_1_final_tick", "run_7_attempt_1_final_tick_quality",
                "run_7_attempt_1_final_tick_6m", "run_7_attempt_1_final_tick_6m_quality",
            ],
        )
        self.assertFalse(build_command.called)
        self.assertFalse(popen.called)
        self.assertIn("no hay candidatos pendientes", Path(state["log_path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
