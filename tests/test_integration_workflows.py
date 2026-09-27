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


class NodeWorkflowIntegrationTests(IntegrationTestCase):
    def test_controller_runs_each_node_queue_in_order_and_persists_it(self) -> None:
        (self.root / "ubs_agent.py").write_text(
            "import time\ntime.sleep(.2)\n",
            encoding="utf-8",
        )
        base = {
            "cycles": 1, "generations": 1, "max_seeds": 1,
            "execute_backtests": False, "dry_run": True,
        }
        first = self.controller.start({**base, "variants_per_seed": 1})
        second = self.controller.start({**base, "variants_per_seed": 2})
        third = self.controller.start({**base, "variants_per_seed": 3})

        self.assertFalse(first["queued"])
        self.assertTrue(second["queued"])
        self.assertEqual(second["queue_item"]["position"], 1)
        self.assertEqual(third["queue_item"]["position"], 2)
        stored = json.loads(self.controller.queue_path.read_text(encoding="utf-8"))
        self.assertEqual([item["payload"]["variants_per_seed"] for item in stored], [2, 3])

        def drained() -> bool:
            state = self.controller.status()
            return state["job"]["status"] != "running" and state["task_queue"]["count"] == 0

        wait_until(drained)
        status = self.controller.status()
        self.assertEqual(status["job"]["status"], "completed")
        self.assertEqual(status["job"]["request"]["variants_per_seed"], 3)
        self.assertEqual(status["task_queue"]["count"], 0)
        self.assertEqual(json.loads(self.controller.queue_path.read_text(encoding="utf-8")), [])

    def test_controller_can_cancel_a_pending_node_task(self) -> None:
        (self.root / "ubs_agent.py").write_text(
            "import time\ntime.sleep(.25)\n",
            encoding="utf-8",
        )
        payload = {
            "cycles": 1, "generations": 1, "variants_per_seed": 1,
            "max_seeds": 1, "execute_backtests": False, "dry_run": True,
        }
        self.controller.start(payload)
        queued = self.controller.start({**payload, "variants_per_seed": 2})
        response_status, result = self.request(
            "/api/nodes/test-node/queue/cancel",
            {"task_id": queued["queue_item"]["id"]},
        )

        self.assertEqual(response_status, 200)
        self.assertEqual(result["task_queue"]["count"], 0)
        self.assertEqual(json.loads(self.controller.queue_path.read_text(encoding="utf-8")), [])

    def test_manager_reads_node_portfolios(self) -> None:
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
        with urllib.request.urlopen(self.base + "/portfolios_monthly.html?node=test-node", timeout=3) as response:
            self.assertIn("Portafolios guardados", response.read().decode("utf-8"))

    @staticmethod
    def _portfolio_proposal(
        settings: dict[str, object], key: str, label: str, units: int
    ) -> dict[str, object]:
        inputs = {
            **settings,
            "portfolio_type": key,
            "composition_portfolio_type": "balanced",
        }
        allocation = StrategyAllocation(
            "same.set", "TEST/DEMO:1", "EURUSD", units, units * 0.01,
            units * 100, units * 20, units * 10, "H1", "same.set",
            "is.html", "oos.html", 0.01,
        )
        result = PortfolioResult(
            [allocation], [0, units * 100], units * 100, units * 20, units * 10,
            300, 300, 10, 5, units * 0.01, units, 1, "ok", [], [],
        )
        return {
            "key": key, "label": label, "reserve_pct": 10,
            "inputs": inputs, "result": result,
        }

    def _prepare_full_history_save(self) -> tuple[Path, object, str]:
        portfolio_memory = self.root / "portfolio-save.sqlite"
        portfolio_memory.touch()
        self.controller.config["memory_path"] = str(portfolio_memory)
        settings = normalize_settings(
            "full_history", {"capital": 5000, "valley_dd_pct": 6}, "TEST"
        )

        coordinator = self.manager.portfolios
        state_key = coordinator._key("test-node", "full_history")
        coordinator.proposals[state_key] = [
            self._portfolio_proposal(settings, "aggressive", "Agresivo", 3),
            self._portfolio_proposal(settings, "balanced", "Moderado", 2),
            self._portfolio_proposal(settings, "conservative", "Conservador", 1),
        ]
        coordinator.jobs[state_key] = {
            "id": "integration-save", "status": "completed", "operation": "generate"
        }
        return portfolio_memory, coordinator, state_key

    def _assert_full_history_save(
        self, portfolio_memory: Path, coordinator: object, state_key: str, saved: dict[str, object]
    ) -> None:
        self.assertGreater(saved["portfolio_id"], 0)
        with closing(sqlite3.connect(portfolio_memory)) as conn:
            row = conn.execute(
                "select id,portfolio_type,capital from portfolios where id=?",
                (saved["portfolio_id"],),
            ).fetchone()
            variants = conn.execute(
                "select distinct variant_key from portfolio_allocations where portfolio_id=?",
                (saved["portfolio_id"],),
            ).fetchall()
        self.assertEqual(row, (saved["portfolio_id"], "bundle", 5000.0))
        self.assertEqual({value[0] for value in variants}, {
            "aggressive", "balanced", "conservative",
        })
        self.assertNotIn(state_key, coordinator.proposals)
        self.assertEqual(coordinator.jobs[state_key]["last_saved_id"], saved["portfolio_id"])

    def test_manager_saves_portfolio_exclusively_through_node_api(self) -> None:
        portfolio_memory, coordinator, state_key = self._prepare_full_history_save()
        status, saved = self.request(
            "/api/nodes/test-node/portfolio-manager/save",
            {"scope": "full_history", "proposal_key": "balanced"},
        )
        self.assertEqual(status, 201)
        self._assert_full_history_save(portfolio_memory, coordinator, state_key, saved)

    @staticmethod
    def _grid_proposal(
        settings: dict[str, object], key: str, label: str, symbol: str, units: int
    ) -> dict[str, object]:
        inputs = {**settings, "portfolio_type": key}
        allocation = StrategyAllocation(
            f"{key}.set", f"TEST/DEMO:{units}", symbol, units, units * 0.01,
            units * 100, units * 20, units * 10, "H1", f"{key}.set",
            "is.html", "oos.html", 0.01,
        )
        result = PortfolioResult(
            [allocation], [0, units * 100], units * 100, units * 20, units * 10,
            300, 300, 10, 5, units * 0.01, units, 1, "ok", [], [],
        )
        return {"key": key, "label": label, "reserve_pct": 10, "inputs": inputs, "result": result}

    def _prepare_grid_save(self) -> tuple[object, str]:
        portfolio_memory = self.root / "portfolio-grid-save.sqlite"
        portfolio_memory.touch()
        self.controller.config["memory_path"] = str(portfolio_memory)
        self.manager.nodes[0].update({
            "portfolio_project_dir": str(self.root),
            "portfolio_memory_path": str(portfolio_memory),
            "portfolio_broker": "TEST",
            "portfolio_account_type": "DEMO",
        })
        self.manager.portfolios.settings_path = self.root / "portfolio_settings.json"
        settings = normalize_settings(
            "grid", {"capital": 1000, "valley_dd_pct": 30, "min_trades_2020_2026": 1}, "TEST"
        )

        coordinator = self.manager.portfolios
        state_key = coordinator._key("test-node", "grid")
        coordinator.proposals[state_key] = [
            self._grid_proposal(settings, "aggressive", "Agresivo Grid", "EURUSD", 3),
            self._grid_proposal(settings, "balanced", "Moderado Grid", "GBPUSD", 2),
            self._grid_proposal(settings, "conservative", "Conservador Grid", "USDJPY", 1),
        ]
        coordinator.jobs[state_key] = {
            "id": "integration-grid-save", "status": "completed", "operation": "generate"
        }
        return coordinator, state_key

    def _assert_grid_save_database(self, saved: dict[str, object]) -> None:
        self.assertEqual(set(saved["portfolio_ids"]), {
            "aggressive", "balanced", "conservative",
        })
        self.assertEqual(saved["portfolio_id"], saved["portfolio_ids"]["balanced"])
        self.assertEqual(len(set(saved["portfolio_ids"].values())), 1)
        grid_memory = self.root / "grid_portfolios" / "test-node.sqlite"
        with closing(sqlite3.connect(grid_memory)) as conn:
            rows = conn.execute(
                "select id,portfolio_type,portfolio_scope from portfolios order by id"
            ).fetchall()
            members = conn.execute(
                "select variant_key,set_id,units from portfolio_allocations order by variant_key"
            ).fetchall()
        self.assertEqual(rows, [(saved["portfolio_id"], "grid_bundle", "grid")])
        self.assertEqual(set(members), {
            ("aggressive", "aggressive.set", 3),
            ("balanced", "balanced.set", 2),
            ("conservative", "conservative.set", 1),
        })

    def _assert_grid_save_api(self, saved: dict[str, object], state_key: str) -> None:
        status, detail = self.request(
            f"/api/nodes/test-node/portfolios/{saved['portfolio_id']}?scope=grid"
        )
        self.assertEqual(status, 200)
        self.assertEqual(detail["portfolio"]["metrics"]["variant_order"], [
            "aggressive", "balanced", "conservative",
        ])
        self.assertEqual(
            {member["variant_key"] for member in detail["portfolio"]["members"]},
            {"aggressive", "balanced", "conservative"},
        )
        status, state = self.request("/api/nodes/test-node/portfolio-manager?scope=grid")
        self.assertEqual(status, 200)
        self.assertEqual(state["inventory"]["scope"], "grid")
        self.assertNotIn(state_key, self.manager.portfolios.proposals)

    def test_manager_saves_all_grid_variants_with_different_compositions(self) -> None:
        coordinator, state_key = self._prepare_grid_save()
        status, saved = self.request(
            "/api/nodes/test-node/portfolio-manager/save",
            {"scope": "grid", "proposal_key": "balanced"},
        )
        self.assertEqual(status, 201)
        self._assert_grid_save_database(saved)
        self._assert_grid_save_api(saved, state_key)

    def test_settings_save_answers_with_the_inventory_its_filters_produce(self) -> None:
        # La tabla «Sets disponibles por símbolo» tiene que seguir a las casillas
        # de grupos permitidos sin recargar la pantalla, así que el guardado del
        # formulario responde con el inventario ya filtrado.
        memory = self.root / "outputs" / "ubs_memory_TEST_DEMO.sqlite"
        memory.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(memory)) as conn:
            conn.executescript("""
                create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,period text,family text,report_path text,status text);
                create table candidate_robustness(candidate_id integer,report_path text,status text);
                create table candidate_final_tick(candidate_id integer,real_tick_report_path text,from_date text,to_date text,status text);
                create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text);
                insert into candidates values(1,'sets/a.set','EURUSD','EURUSD','H1','f','reports/a.html','accepted');
                insert into candidates values(2,'sets/b.set','XAUUSD','XAUUSD','H1','f','reports/b.html','accepted');
                insert into candidate_robustness values(1,'reports/a_oos.html','accepted');
                insert into candidate_robustness values(2,'reports/b_oos.html','accepted');
                insert into candidate_final_tick values(1,'reports/a_full.html','2020.01.01','2026.06.30','accepted');
                insert into candidate_final_tick values(2,'reports/b_full.html','2020.01.01','2026.06.30','accepted');
                insert into candidate_final_tick_6m values(1,'','','2026.01.01','2026.06.30','accepted');
                insert into candidate_final_tick_6m values(2,'','','2026.01.01','2026.06.30','accepted');
            """)
            conn.commit()
        self.manager.nodes[0].update({
            "portfolio_project_dir": str(self.root),
            "portfolio_broker": "TEST",
            "portfolio_account_type": "DEMO",
        })
        self.manager.portfolios.settings_path = self.root / "portfolio_settings.json"

        status, both = self.request(
            "/api/nodes/test-node/portfolio-manager/settings",
            {"scope": "full_history", "allowed_asset_groups": ["Forex", "Metals"]},
        )
        forex_status, forex_only = self.request(
            "/api/nodes/test-node/portfolio-manager/settings",
            {"scope": "full_history", "allowed_asset_groups": ["Forex"]},
        )

        self.assertEqual((status, forex_status), (200, 200))
        self.assertEqual(
            [row["symbol"] for row in both["inventory"]["by_symbol"]], ["EURUSD", "XAUUSD"]
        )
        self.assertEqual(
            [row["symbol"] for row in forex_only["inventory"]["by_symbol"]], ["EURUSD"]
        )
        self.assertEqual(forex_only["settings"]["allowed_asset_groups"], ["Forex"])

    @staticmethod
    def _expected_selected_pipeline_actions(repair_actions: list[str]) -> list[str]:
        expected_actions = []
        for _cycle in (1, 2):
            expected_actions.append("generation")
            expected_actions.extend(["robustness", "final_tick", "final_tick_6m"])
            # Dos intentos y dos fases por intento sobre las mismas etapas.
            expected_actions.extend(repair_actions * 4)
        return expected_actions

    def _assert_completed_selected_pipeline(
        self, build_auto_repair_stage: mock.Mock, result: dict[str, object], repair_actions: list[str]
    ) -> None:
        self.assertEqual(result["status"], "completed")
        self.assertTrue(build_auto_repair_stage.call_args_list)
        # Lo unico que distingue las fases es el numero de terminales: la primera
        # hereda `max_workers` y la segunda usa su propio limite.
        expected_workers = []
        for _cycle in (1, 2):
            expected_workers.extend([4] * 3)
            expected_workers.extend(
                workers
                for _attempt in (1, 2)
                for workers in ([4] * len(repair_actions) + [2] * len(repair_actions))
            )
        self.assertEqual(
            [call.args[1]["max_workers"] for call in build_auto_repair_stage.call_args_list],
            expected_workers,
        )
        expected_stages = []
        for cycle in (1, 2):
            expected_stages.append(f"cycle_{cycle}_generation")
            expected_stages.extend(
                f"cycle_{cycle}_{action}"
                for action in ("robustness", "final_tick", "final_tick_6m")
            )
            expected_stages.extend(
                f"cycle_{cycle}_attempt_{attempt}_phase_{phase}_{action}"
                for attempt in (1, 2)
                for phase in (1, 2)
                for action in repair_actions
            )
        self.assertEqual(result["completed_stages"], expected_stages)

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
            mock.patch("mt5_manager.node_commands.build_generation_command", return_value=(fake_command, self.root)),
            mock.patch(
                "mt5_manager.node_commands.build_pipeline_stage_command",
                return_value=(fake_command, self.root),
            ) as build_auto_repair_stage,
            mock.patch("mt5_manager.node_snapshots.pipeline_stage_pending_count", return_value=1),
        ):
            state = self.controller.start({
                "cycles": 2,
                "execute_backtests": True,
                "run_robustness": True,
                "run_final_tick": True,
                "run_final_tick_6m": True,
                "repair_after_generation": True,
                "repair_attempts": 2,
                "max_workers": 4,
                "repair_phase2_max_workers": 2,
            })
            repair_actions = [
                "result", "robustness", "final_tick", "final_tick_quality",
                "final_tick_6m", "final_tick_6m_quality",
            ]
            expected_actions = self._expected_selected_pipeline_actions(repair_actions)
            self.assertEqual([step["action"] for step in state["pipeline"]], expected_actions)
            self.assertTrue(state["request"]["repair_after_generation"])
            self.assertEqual(state["request"]["repair_attempts"], 2)
            # Cada ciclo termina sus etapas normales antes de empezar a reparar.
            self.wait_for_job(SLOW_JOB_TIMEOUT)
        result = self.controller.status()["job"]
        self._assert_completed_selected_pipeline(build_auto_repair_stage, result, repair_actions)

    def test_repair_runs_all_tests_per_selected_run_with_requested_workers(self) -> None:
        fake_command = [sys.executable, str(self.root / "ubs_agent.py")]
        with (
            mock.patch(
                "mt5_manager.node_commands.build_pipeline_stage_command",
                return_value=(fake_command, self.root),
            ) as build_stage,
            mock.patch("mt5_manager.node_snapshots.pipeline_stage_pending_count", return_value=1),
        ):
            state = self.controller.start_repair({
                "run_ids": [7, 9], "max_workers": 3,
                "repair_phase2_max_workers": 1,
                "repair_attempts": 2, "retry_low_quality": True,
            })
            self.assertEqual(state["request"]["max_workers"], 3)
            self.assertEqual(state["request"]["repair_phase2_max_workers"], 1)
            self.assertEqual(state["request"]["repair_attempts"], 2)
            # 2 runs x 2 intentos x 2 fases x 6 etapas.
            self.assertEqual(len(state["pipeline"]), 48)
            self.wait_for_job(SLOW_JOB_TIMEOUT)
        result = self.controller.status()["job"]
        self.assertEqual(result["status"], "completed")
        self.assertTrue(build_stage.call_args_list)
        actions = ["result", "robustness", "final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"]
        # El reintento es por run: cada run agota sus dos intentos y sus dos fases
        # antes de que empiece el siguiente.
        self.assertEqual(
            [call.args[1]["max_workers"] for call in build_stage.call_args_list],
            [
                workers
                for _run_id in (7, 9)
                for _attempt in (1, 2)
                for workers in ([3] * len(actions) + [1] * len(actions))
            ],
        )
        expected = [
            f"run_{run_id}_attempt_{attempt}_phase_{phase}_{action}"
            for run_id in (7, 9)
            for attempt in (1, 2)
            for phase in (1, 2)
            for action in actions
        ]
        self.assertEqual(result["completed_stages"], expected)

    def test_repair_skips_empty_stages_without_spawning_a_process(self) -> None:
        with (
            mock.patch("mt5_manager.node_snapshots.pipeline_stage_pending_count", return_value=0),
            mock.patch("mt5_manager.node_commands.build_pipeline_stage_command") as build_command,
            mock.patch("mt5_manager.node_job_runtime.subprocess.Popen") as popen,
        ):
            state = self.controller.start_repair({"run_ids": [7], "retry_low_quality": True})
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completed_stages"], [])
        self.assertEqual(
            state["skipped_stages"],
            [
                f"run_7_attempt_1_phase_{phase}_{action}"
                for phase in (1, 2)
                for action in (
                    "result", "robustness", "final_tick", "final_tick_quality",
                    "final_tick_6m", "final_tick_6m_quality",
                )
            ],
        )
        self.assertFalse(build_command.called)
        self.assertFalse(popen.called)
        self.assertIn("no hay candidatos pendientes", Path(state["log_path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
