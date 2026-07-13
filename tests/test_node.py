from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from mt5_manager.node import (
    build_generation_command,
    build_pipeline_stage_command,
    database_snapshot,
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
