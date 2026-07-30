from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from mt5_manager.node import (
    CLEANUP_STAGES,
    JobController,
    build_historical_cleanup_command,
    build_generation_command,
    build_pipeline_stage_command,
    database_snapshot,
    historical_cleanup_scripts,
    pipeline_stage_pending_count,
)


class NodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ubs_agent.py").write_text("print('ok')\n", encoding="utf-8")
        (self.root / "tester_template.ini").write_text("[Tester]\n", encoding="utf-8")
        (self.root / "ui_settings.ini").write_text(
            """[Paths]
set_files_root=C:\\sets
ubs_generation_output=C:\\output
template_path={template}
ubs_ex5_file=C:\\experts\\ubs.ex5
mt5_path=C:\\MT5\\terminal64.exe
mt5_data_root=C:\\MT5Data

[General]
delay=5
ubs_broker=ICTRADING
ubs_account_type=STANDARD
ubs_generation_count=2
ubs_variants_per_seed=10
ubs_max_seeds=30
ubs_agent_execute=1
ubs_generation_mode=production
ubs_pass_min_net_profit=100
ubs_pass_min_profit_factor=1.2
ubs_pass_min_trades=50
ubs_pass_max_drawdown_pct=25
ubs_pass_min_recovery_factor=1.0
ubs_long_tf_min_trades_w1=11
ubs_long_tf_min_trades_mn=4
ubs_robust_from_date=2025.01.01
ubs_robust_to_date=2025.12.31
ubs_robust_pass_min_net_profit=20
ubs_robust_pass_min_profit_factor=1.2
ubs_robust_pass_min_trades=40
ubs_robust_pass_max_drawdown_pct=25
ubs_robust_pass_min_recovery_factor=1.0
ubs_robust_positive_bonus=70
ubs_robust_negative_bonus=-70
ubs_final_tick_from_date=2026.01.01
ubs_final_tick_to_date=2026.01.31
ubs_final_tick_6m_from_date=2026.01.01
ubs_final_tick_6m_to_date=2026.06.30
ubs_final_tick_min_history_quality=80
ubs_final_tick_min_ohlc_trades=5
ubs_final_tick_min_trades_w1=2
ubs_final_tick_min_trades_mn=1
ubs_final_tick_max_net_delta_pct=35
ubs_final_tick_max_pf_delta_pct=35
ubs_final_tick_max_dd_delta_pct=35
ubs_final_tick_max_trades_delta_pct=35

[Multiterminal]
enabled=0
""".format(template=self.root / "tester_template.ini"),
            encoding="utf-8",
        )
        self.config = {
            "node_id": "ic", "project_dir": str(self.root), "token": "secret",
            "broker": "ICTRADING", "account_type": "STANDARD", "python_executable": "python",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_generation_command_uses_settings_and_overrides(self) -> None:
        command, cwd = build_generation_command(self.config, {
            "generations": 3, "variants_per_seed": 7, "max_seeds": 12,
            "generation_mode": "discovery", "execute_backtests": True,
            "max_workers": 4,
            "from_date": "2025.01.01", "to_date": "2025.12.31",
        })
        self.assertEqual(cwd, self.root)
        self.assertIn(str(self.root / "ubs_agent.py"), command)
        self.assertEqual(command[command.index("--generations") + 1], "3")
        self.assertEqual(command[command.index("--generation-mode") + 1], "discovery")
        self.assertIn("--execute-backtests", command)
        self.assertIn("--expert", command)
        self.assertEqual(command[command.index("--min-trades-w1") + 1], "11")

    def test_pipeline_stage_commands_use_stage_dates_and_worker_override(self) -> None:
        robustness, _ = build_pipeline_stage_command(self.config, {"max_workers": 3}, "robustness", 17)
        self.assertIn("--evaluate-robustness", robustness)
        self.assertEqual(robustness[robustness.index("--robust-run-id") + 1], "17")
        self.assertEqual(robustness[robustness.index("--from-date") + 1], "2025.01.01")

        six_month, _ = build_pipeline_stage_command(self.config, {}, "final_tick_6m", 17)
        self.assertIn("--evaluate-final-tick", six_month)
        self.assertEqual(six_month[six_month.index("--final-tick-stage") + 1], "six_month")
        self.assertEqual(six_month[six_month.index("--to-date") + 1], "2026.06.30")

        short_quality, _ = build_pipeline_stage_command(self.config, {}, "final_tick_quality", 17)
        self.assertEqual(short_quality[short_quality.index("--final-tick-stage") + 1], "probe")
        self.assertIn("--final-tick-retry-pending-quality", short_quality)
        self.assertIn("--final-tick-skip-ohlc", short_quality)

        six_month_quality, _ = build_pipeline_stage_command(self.config, {}, "final_tick_6m_quality", 17)
        self.assertEqual(six_month_quality[six_month_quality.index("--final-tick-stage") + 1], "six_month")
        self.assertIn("--final-tick-retry-pending-quality", six_month_quality)

    def test_historical_cleanup_uses_the_same_two_agent_scripts(self) -> None:
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts_dir / filename).write_text("Write-Host clean\n", encoding="utf-8")

        scripts = historical_cleanup_scripts(self.config)
        tester_command, cwd = build_historical_cleanup_command(self.config, "cleanup_tester")
        verify_command, _ = build_historical_cleanup_command(self.config, "cleanup_verify")

        self.assertEqual(cwd, self.root)
        self.assertEqual(scripts["cleanup_tester"], scripts_dir / "cleanOldTest.ps1")
        self.assertEqual(scripts["cleanup_data"], scripts_dir / "cleanOlddata.ps1")
        self.assertEqual(tester_command[-1], str(scripts_dir / "cleanOldTest.ps1"))
        self.assertEqual(verify_command[:2], [sys.executable, "-c"])

    def test_each_completed_generation_cycle_ends_with_historical_cleanup(self) -> None:
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts_dir / filename).write_text("Write-Host clean\n", encoding="utf-8")
        config_path = self.root / "node.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        controller = JobController(self.config, config_path)

        with mock.patch.object(controller, "_launch_step"):
            state = controller.start({
                "cycles": 2,
                "execute_backtests": False,
                "run_robustness": False,
                "run_final_tick": False,
                "run_final_tick_6m": False,
            })

        self.assertTrue(state["request"]["cleanup_after_run"])
        self.assertEqual(
            [step["action"] for step in state["pipeline"]],
            ["generation", *CLEANUP_STAGES, "generation", *CLEANUP_STAGES],
        )

    def test_auto_repair_uses_an_independent_worker_limit(self) -> None:
        config_path = self.root / "node.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        controller = JobController(self.config, config_path)

        with mock.patch.object(controller, "_launch_step"):
            state = controller.start({
                "cycles": 1,
                "max_workers": 7,
                "repair_max_workers": 3,
                "repair_after_generation": True,
                "repair_attempts": 1,
                "run_robustness": True,
                "cleanup_after_run": False,
            })

        self.assertEqual(state["request"]["max_workers"], 7)
        self.assertEqual(state["request"]["repair_max_workers"], 3)
        repair_steps = [
            step for step in state["pipeline"]
            if step["action"] != "generation"
        ]
        self.assertTrue(repair_steps)
        self.assertTrue(all(step["max_workers"] == 3 for step in repair_steps))

    def test_manual_historical_cleanup_is_a_queueable_job(self) -> None:
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts_dir / filename).write_text("Write-Host clean\n", encoding="utf-8")
        config_path = self.root / "node.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        controller = JobController(self.config, config_path)

        with mock.patch.object(controller, "_launch_next_runnable"):
            state = controller.start_cleanup()

        self.assertEqual(state["job_type"], "cleanup")
        self.assertEqual([step["action"] for step in state["pipeline"]], list(CLEANUP_STAGES))

    def test_manual_repair_cleans_after_each_selected_run(self) -> None:
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts_dir / filename).write_text("Write-Host clean\n", encoding="utf-8")
        config_path = self.root / "node.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        controller = JobController(self.config, config_path)

        with mock.patch.object(controller, "_launch_next_runnable", return_value=True):
            state = controller.start_repair({
                "run_ids": [7, 9], "repair_attempts": 1, "cleanup_after_run": True,
            })

        actions = ["result", "robustness", "final_tick", "final_tick_quality",
                   "final_tick_6m", "final_tick_6m_quality"]
        expected = [
            *((run_id, action) for run_id in (7, 9) for action in (*actions, *CLEANUP_STAGES)),
        ]
        self.assertTrue(state["request"]["cleanup_after_run"])
        self.assertEqual(
            [(step["run_id"], step["action"]) for step in state["pipeline"]],
            expected,
        )
        self.assertEqual(
            controller._step_label({"action": "cleanup_tester", "cycle": None, "run_id": 7}),
            "run_7_cleanup_tester",
        )

    def test_manual_regression_cleans_after_each_selected_run(self) -> None:
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts_dir / filename).write_text("Write-Host clean\n", encoding="utf-8")
        config_path = self.root / "node.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        controller = JobController(self.config, config_path)

        with mock.patch.object(controller, "_launch_next_runnable", return_value=True):
            state = controller.start_regression({
                "run_ids": [11, 12], "max_workers": 4, "cleanup_after_run": True,
            })

        self.assertTrue(state["request"]["cleanup_after_run"])
        self.assertEqual(
            [(step["run_id"], step["action"]) for step in state["pipeline"]],
            [
                (11, "regression"),
                *((11, action) for action in CLEANUP_STAGES),
                (12, "regression"),
                *((12, action) for action in CLEANUP_STAGES),
            ],
        )

    def test_result_repair_uses_the_selected_run_original_dates(self) -> None:
        memory = self.root / "result_repair.sqlite"
        with closing(sqlite3.connect(memory)) as conn:
            conn.execute("create table runs(id integer primary key, config_json text)")
            conn.execute(
                "insert into runs values(17,?)",
                (json.dumps({"execution": {"from_date": "2024.02.01", "to_date": "2024.11.30"}}),),
            )
            conn.commit()
        self.config["memory_path"] = str(memory)

        result, _ = build_pipeline_stage_command(self.config, {"max_workers": 1}, "result", 17)

        self.assertIn("--retry-mismatch-run", result)
        self.assertEqual(result[result.index("--retry-run-id") + 1], "17")
        self.assertEqual(result[result.index("--from-date") + 1], "2024.02.01")
        self.assertEqual(result[result.index("--to-date") + 1], "2024.11.30")

    def test_pipeline_preflight_counts_only_candidates_needed_by_each_stage(self) -> None:
        memory = self.root / "memory.sqlite"
        set_paths = []
        for candidate_id in range(1, 6):
            path = self.root / "sets" / f"candidate_{candidate_id}.set"
            path.parent.mkdir(exist_ok=True)
            path.write_text("Lots=0.1\n", encoding="utf-8")
            set_paths.append(path)
        with closing(sqlite3.connect(memory)) as conn:
            conn.executescript("""
                create table candidates(
                    id integer primary key, run_id integer, generation integer,
                    status text, set_path text
                );
                create table candidate_robustness(
                    candidate_id integer primary key, run_id integer, status text
                );
                create table candidate_final_tick(
                    candidate_id integer primary key, run_id integer, status text,
                    from_date text, to_date text
                );
                create table candidate_final_tick_6m(
                    candidate_id integer primary key, run_id integer, status text,
                    from_date text, to_date text
                );
            """)
            conn.executemany(
                "insert into candidates values(?,7,1,'accepted',?)",
                [(index, str(path)) for index, path in enumerate(set_paths[:4], 1)],
            )
            conn.execute("insert into candidates values(5,7,1,'report_mismatch',?)", (str(set_paths[4]),))
            conn.executemany(
                "insert into candidate_robustness values(?,7,'accepted')", [(2,), (3,), (4,)],
            )
            conn.execute(
                "insert into candidate_final_tick values(3,7,'pending_history_quality','2026.01.01','2026.01.31')"
            )
            conn.execute(
                "insert into candidate_final_tick values(4,7,'accepted','2026.01.01','2026.01.31')"
            )
            conn.execute(
                "insert into candidate_final_tick_6m values(4,7,'pending_history_quality','2026.01.01','2026.06.30')"
            )
            conn.commit()
        self.config["memory_path"] = str(memory)

        self.assertEqual(pipeline_stage_pending_count(self.config, {}, "result", 7), 1)
        self.assertEqual(pipeline_stage_pending_count(self.config, {}, "robustness", 7), 1)
        self.assertEqual(pipeline_stage_pending_count(self.config, {}, "final_tick", 7), 1)
        self.assertEqual(pipeline_stage_pending_count(self.config, {}, "final_tick_quality", 7), 1)
        self.assertEqual(pipeline_stage_pending_count(self.config, {}, "final_tick_6m", 7), 0)
        self.assertEqual(pipeline_stage_pending_count(self.config, {}, "final_tick_6m_quality", 7), 1)

    def test_database_snapshot_reports_latest_run_and_stages(self) -> None:
        path = self.root / "memory.sqlite"
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript("""
                create table runs(id integer primary key, created_at text, generations integer, hidden integer default 0);
                create table candidates(id integer primary key, run_id integer, generation integer, status text);
                create table candidate_robustness(candidate_id integer primary key, run_id integer, status text);
                insert into runs values(1, '2026-07-11', 2, 0);
                insert into candidates values(1, 1, 1, 'accepted');
                insert into candidates values(2, 1, 2, 'rejected');
                insert into candidate_robustness values(1, 1, 'accepted');
            """)
            conn.commit()
        snapshot = database_snapshot(path)
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["latest_run"]["id"], 1)
        self.assertEqual(snapshot["max_generation"], 2)
        self.assertEqual(snapshot["stages"]["generation"], {"accepted": 1, "rejected": 1})
        self.assertEqual(snapshot["stages"]["robustness"], {"accepted": 1})

    def test_invalid_generation_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_generation_command(self.config, {"generation_mode": "random"})

    def test_legacy_branch_drops_new_cli_options_and_uses_legacy_memory(self) -> None:
        (self.root / "ubs_agent.py").write_text(
            '''# legacy parser\nOPTIONS = ["--source-dir", "--output-dir", "--memory", "--template", "--generations", "--variants-per-seed", "--max-seeds", "--delay", "--execute-backtests", "--expert", "--mt5-path", "--data-dir"]\n''',
            encoding="utf-8",
        )
        legacy = self.root / "outputs" / "ubs_memory.sqlite"
        legacy.parent.mkdir()
        legacy.touch()
        command, _ = build_generation_command(self.config, {"execute_backtests": False})
        self.assertNotIn("--broker", command)
        self.assertNotIn("--account-type", command)
        self.assertNotIn("--generation-mode", command)
        self.assertEqual(command[command.index("--memory") + 1], str(legacy))


if __name__ == "__main__":
    unittest.main()
