import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.portfolio_service import (
    STANDARD_ANTIFILLER_REFILL_PASSES,
    PortfolioCoordinator,
    PortfolioSource,
    _linux_path_needs_snapshot,
    _insert_decisions,
    _optimizer_kwargs,
    _optimize_without_recent_fillers,
    _resolve_source_path,
    _underrepresented_recent_allocation_ids,
    _adjusted_valley_pcts,
    _locked_full_proposals,
    describe_eligibility,
    eligibility_counts,
    generate_proposals,
    ensure_portfolio_schema,
    normalize_settings,
    save_portfolio_payload,
    scope_stage_count,
)
from mt5_manager.portfolio_monthly_service import (
    _monthly_proposals,
    generate_monthly_proposals,
    monthly_eligibility_counts,
)
from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    PortfolioAvailability,
    PortfolioCalculationCancelled,
    PortfolioResult,
    PortfolioType,
    StrategyAllocation,
    filter_eligible_sets,
    filter_rows_grid_off,
    evaluate_portfolio,
    load_robust_sets_from_rows,
    set_portfolio_cancellation_check,
)
from tests.helpers import ASYNC_TIMEOUT, assert_event, assert_until


class PortfolioCoordinationTests(unittest.TestCase):
    def test_portfolio_form_settings_survive_manager_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "portfolio_settings.json"
            nodes = [{"id": "test-node", "portfolio_broker": "ICTRADING"}]
            coordinator = PortfolioCoordinator(nodes, settings_path)

            saved = coordinator.update_settings(
                "test-node",
                "full_history",
                {
                    "capital": 5000,
                    "exclude_used_sets": False,
                    "disabled_symbols": ["EURUSD", "XAUUSD"],
                },
            )
            reloaded = PortfolioCoordinator(nodes, settings_path).settings_for(
                "test-node", "full_history"
            )

            self.assertEqual(saved["capital"], 5000)
            self.assertFalse(saved["exclude_used_sets"])
            self.assertEqual(saved["disabled_symbols"], ["EURUSD", "XAUUSD"])
            self.assertEqual(reloaded["capital"], 5000)
            self.assertFalse(reloaded["exclude_used_sets"])
            self.assertEqual(reloaded["disabled_symbols"], ["EURUSD", "XAUUSD"])

    def test_saving_the_form_returns_the_inventory_that_its_filters_produce(self) -> None:
        # Marcar o desmarcar un grupo permitido cambia lo que cuenta la tabla
        # «Sets disponibles por símbolo», y la pantalla no vuelve a pedir el
        # estado entero al guardar: si la respuesta no trae el inventario, la
        # tabla se queda con los grupos anteriores hasta pulsar Guardar.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            (project / "outputs").mkdir(parents=True)
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.executescript(
                    """
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
                    """
                )
                conn.commit()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], Path(temp_dir) / "settings.json")

            both = coordinator.apply_settings(
                "ic", "full_history", {"allowed_asset_groups": ["Forex", "Metals"]}
            )
            forex_only = coordinator.apply_settings(
                "ic", "full_history", {"allowed_asset_groups": ["Forex"]}
            )

            self.assertEqual(
                [row["symbol"] for row in both["inventory"]["by_symbol"]], ["EURUSD", "XAUUSD"]
            )
            self.assertEqual(both["inventory"]["available"], 2)
            self.assertEqual(
                [row["symbol"] for row in forex_only["inventory"]["by_symbol"]], ["EURUSD"]
            )
            self.assertEqual(forex_only["inventory"]["available"], 1)
            self.assertEqual(forex_only["settings"]["allowed_asset_groups"], ["Forex"])

    def test_saving_the_form_survives_an_inventory_that_cannot_be_read(self) -> None:
        # El proyecto del agente puede no estar montado. Los ajustes se guardan en
        # el manager y ya están escritos cuando falla la lectura remota: la
        # respuesta sale sin inventario en lugar de convertir un guardado correcto
        # en un error de guardado.
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator(
                [{"id": "ic", "portfolio_broker": "ICTRADING"}],
                Path(temp_dir) / "settings.json",
            )

            saved = coordinator.apply_settings("ic", "full_history", {"capital": 5000})

            self.assertEqual(saved["settings"]["capital"], 5000)
            self.assertNotIn("inventory", saved)
            self.assertEqual(coordinator.settings_for("ic", "full_history")["capital"], 5000)

    def test_monthly_job_exposes_its_log_before_the_worker_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")

            with patch("threading.Thread.start"):
                job = coordinator.start("ic", "monthly", {"target_month": 7})

            self.assertEqual(job["status"], "running")
            self.assertTrue(Path(job["log_path"]).is_file())
            log = coordinator.log("ic", "monthly")
            self.assertIn("Preparando cálculo mensual", "\n".join(log["lines"]))

    def test_every_scope_exposes_its_log_and_stage_count_before_the_worker_starts(self) -> None:
        # El log creado antes del hilo era una ventaja solo del mensual: en los
        # otros dos ambitos «Ver log» abria un dialogo vacio justo al arrancar.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            expected = {"full_history": 5, "monthly": 6, "grid": 4}

            for scope, stages in expected.items():
                changes = {"target_month": 7} if scope == "monthly" else {}
                with patch("threading.Thread.start"):
                    job = coordinator.start("ic", scope, changes)

                self.assertEqual(job["stage_total"], stages, scope)
                self.assertTrue(Path(job["log_path"]).is_file(), scope)
                self.assertIn(f"0/{stages}", "\n".join(coordinator.log("ic", scope)["lines"]), scope)
                coordinator.jobs[coordinator._key("ic", scope)]["status"] = "idle"

        self.assertEqual(scope_stage_count("full_history", "complete"), 3)

    def test_monthly_worker_dispatches_to_the_independent_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            settings = normalize_settings("monthly", {"target_month": 7}, "ICTRADING")
            with patch("threading.Thread.start"):
                coordinator.start("ic", "monthly", settings)
            with patch(
                "mt5_manager.portfolio_monthly_service.run_monthly_operation",
                return_value=({"loaded_sets": 0}, []),
            ) as monthly_run, patch.object(PortfolioSource, "notify"):
                coordinator._worker("ic", "monthly", settings)

            monthly_run.assert_called_once()
            self.assertEqual(coordinator.jobs["ic:monthly"]["status"], "completed")

    def test_full_history_stop_marks_the_job_and_worker_as_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            settings = normalize_settings("full_history", {}, "ICTRADING")
            with patch("threading.Thread.start"):
                coordinator.start("ic", "full_history", settings)

            stopping = coordinator.stop("ic", "full_history")
            self.assertEqual(stopping["status"], "stopping")
            with self.assertRaisesRegex(ValueError, "Ya hay un cálculo"):
                coordinator.start("ic", "full_history", settings)

            coordinator._worker("ic", "full_history", settings)
            job = coordinator.jobs["ic:full_history"]
            self.assertEqual(job["status"], "stopped")
            self.assertIsNone(job["error"])
            self.assertNotIn("ic:full_history", coordinator.cancellation_events)

    def test_stop_is_not_enabled_for_the_frozen_monthly_scope(self) -> None:
        coordinator = PortfolioCoordinator([{"id": "ic"}], Path("settings.json"))
        with self.assertRaisesRegex(ValueError, "solo está disponible"):
            coordinator.stop("ic", "monthly")

    def test_optimizer_hot_path_obeys_and_restores_the_cancellation_check(self) -> None:
        previous = set_portfolio_cancellation_check(lambda: True)
        try:
            with self.assertRaises(PortfolioCalculationCancelled):
                evaluate_portfolio([], {}, 100.0, 100.0)
        finally:
            set_portfolio_cancellation_check(previous)

        result = evaluate_portfolio([], {}, 100.0, 100.0)
        self.assertEqual(result.active_strategies, 0)

    def test_delete_is_queued_and_returns_before_the_database_work_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], project / "settings.json")
            started = threading.Event()
            release = threading.Event()

            def slow_delete(_source: PortfolioSource, _portfolio_id: int, _scope: str) -> None:
                started.set()
                # Valvula de seguridad, no un plazo: abajo se libera siempre. Si
                # vence antes de tiempo el borrado termina solo y la
                # comprobacion de que sigue "running" falla sin que haya pasado
                # nada malo.
                release.wait(ASYNC_TIMEOUT)

            with patch.object(PortfolioSource, "delete_portfolio", slow_delete):
                before = time.monotonic()
                task = coordinator.delete("ic", "full_history", 37)
                elapsed = time.monotonic() - before

                # Este plazo si mide: la gracia es que delete() vuelva sin
                # esperar al borrado, que aqui bloquea ASYNC_TIMEOUT segundos.
                self.assertLess(elapsed, 0.2)
                self.assertIn(task["status"], {"pending", "running"})
                assert_event(self, started, "el borrado no llego a arrancar")
                key = coordinator._key("ic", "full_history")
                with coordinator.lock:
                    self.assertEqual(coordinator.tasks[key][0]["status"], "running")

                release.set()

                def task_status() -> str:
                    with coordinator.lock:
                        return coordinator.tasks[key][0]["status"]

                assert_until(
                    self,
                    lambda: task_status() == "completed",
                    "la tarea de borrado no llego a completarse",
                )
                self.assertEqual(task_status(), "completed")

    def test_task_state_does_not_read_the_remote_inventory(self) -> None:
        coordinator = PortfolioCoordinator(
            [{"id": "ic", "portfolio_broker": "ICTRADING"}], Path("unused-settings.json")
        )
        key = coordinator._key("ic", "full_history")
        coordinator.tasks[key] = [{
            "id": "delete-39", "status": "completed", "operation": "delete", "portfolio_id": 39,
        }]

        with patch.object(PortfolioSource, "inventory", side_effect=AssertionError("no debe consultar inventario")):
            status = coordinator.task_state("ic", "full_history")

        self.assertEqual(status["task"]["id"], "delete-39")
        self.assertEqual(status["task"]["status"], "completed")

    def test_normalize_monthly_settings_keeps_month_specific_controls(self) -> None:
        settings = normalize_settings(
            "monthly",
            {
                "target_month": 7,
                "max_daily_dd": 125,
                "strict_yearly_month_validation": True,
                "allowed_asset_groups": ["Forex", "Metals"],
            },
            "ICTRADING",
        )
        self.assertEqual(settings["portfolio_scope"], "monthly")
        self.assertEqual(settings["target_month"], 7)
        self.assertEqual(settings["max_daily_dd"], 125)
        self.assertTrue(settings["strict_yearly_month_validation"])
        self.assertFalse(settings["enforce_point_dd"])

    def test_disabled_symbols_are_normalized_only_for_full_history(self) -> None:
        full = normalize_settings(
            "full_history",
            {"disabled_symbols": [" EURUSD ", "eurusd", "XAUUSD"]},
            "ICTRADING",
        )
        monthly = normalize_settings(
            "monthly",
            {
                "target_month": 7,
                "disabled_symbols": ["EURUSD"],
            },
            "ICTRADING",
        )

        self.assertEqual(full["disabled_symbols"], ["EURUSD", "XAUUSD"])
        self.assertEqual(monthly["disabled_symbols"], [])

    def test_symbol_family_lists_the_pool_and_exports_only_the_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            accepted = project / "NFLX_accepted.set"
            excluded = project / "NFLX_excluded.set"
            other = project / "NKE.set"
            for path in (accepted, excluded, other):
                path.write_text(f"name={path.stem}\n", encoding="utf-8")
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })
            # candidate_rows ya trae solo el pool de las cuatro etapas: la
            # degradada desapareció de ahí y solo vive en la cuarentena.
            rows = [
                {
                    "candidate_id": "ICTRADING/STANDARD:1", "set_path": str(accepted),
                    "symbol": "NFLX", "target_symbol": "NFLX", "period": "H1", "family": "stock",
                    "account_type": "ICTRADING/STANDARD",
                },
                {
                    "candidate_id": "ICTRADING/STANDARD:3", "set_path": str(other),
                    "symbol": "NKE", "target_symbol": "NKE", "period": "H1", "family": "stock",
                    "account_type": "ICTRADING/STANDARD",
                },
            ]
            quarantine = [{
                "set_path": str(excluded), "quarantine_key": "ICTRADING/STANDARD|7",
                "symbol": "NFLX", "timeframe": "H1", "source_account": "ICTRADING/STANDARD",
                "reason_code": "degradation", "reason_label": "Excluido por degradación",
            }]
            with patch.object(source, "candidate_rows", return_value=rows), patch.object(
                source, "quarantine_rows", return_value=quarantine
            ), patch.object(source, "used_set_paths", return_value=[str(accepted)]):
                family = source.symbol_sets("NFLX")
                exported = source.export_symbol_sets(
                    "NFLX", [str(accepted), str(excluded)], str(project / "exported")
                )
                with self.assertRaisesRegex(ValueError, "no pertenecen"):
                    source.export_symbol_sets("NFLX", [str(other)], str(project / "exported"))

            self.assertEqual(family["total"], 2)
            self.assertEqual(
                {row["set_name"]: row["state"] for row in family["sets"]},
                {"NFLX_accepted.set": "used", "NFLX_excluded.set": "excluded"},
            )
            excluded_row = next(row for row in family["sets"] if row["set_name"] == excluded.name)
            self.assertEqual(excluded_row["quarantine_key"], "ICTRADING/STANDARD|7")
            self.assertEqual(excluded_row["reason_code"], "degradation")
            self.assertEqual(exported["exported"], 2)
            output = Path(exported["folder"])
            self.assertEqual(
                {path.name for path in output.glob("*.set")},
                {"NFLX_accepted.set", "NFLX_excluded.set"},
            )

    def test_symbol_family_applies_the_same_filters_as_the_inventory_row(self) -> None:
        # La ventana se abre desde la fila del inventario, que ya descartó los
        # sets con EnableGrid=true cuando grid_off está activo. Sin ese filtro la
        # tabla enseñaba 84 sets de .DE40Cash frente a los 61 de la fila.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            plain = project / "NFLX_plain.set"
            grid = project / "NFLX_grid.set"
            plain.write_text("EnableGrid=false\n", encoding="utf-8")
            grid.write_text("EnableGrid=true\n", encoding="utf-8")
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })
            rows = [
                {
                    "candidate_id": "ICTRADING/STANDARD:1", "set_path": str(plain),
                    "symbol": "NFLX", "target_symbol": "NFLX", "period": "H1", "family": "stock",
                    "account_type": "ICTRADING/STANDARD",
                },
                {
                    "candidate_id": "ICTRADING/STANDARD:2", "set_path": str(grid),
                    "symbol": "NFLX", "target_symbol": "NFLX", "period": "H1", "family": "stock",
                    "account_type": "ICTRADING/STANDARD",
                },
            ]
            with patch.object(source, "candidate_rows", return_value=rows), patch.object(
                source, "quarantine_rows", return_value=[]
            ), patch.object(source, "used_set_paths", return_value=[]):
                everything = source.symbol_sets("NFLX")
                grid_off = source.symbol_sets("NFLX", settings={"grid_off": True})
                other_group = source.symbol_sets("NFLX", settings={"allowed_asset_groups": ["Stocks"]})
                with self.assertRaisesRegex(ValueError, "No se encontraron sets"):
                    source.symbol_sets("NFLX", settings={"allowed_asset_groups": ["Forex"]})

            self.assertEqual({row["set_name"] for row in everything["sets"]}, {plain.name, grid.name})
            self.assertEqual({row["set_name"] for row in grid_off["sets"]}, {plain.name})
            self.assertEqual(other_group["total"], 2)

    def test_symbol_family_leaves_out_candidates_that_never_reached_the_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            pending = project / "NFLX_pending.set"
            pending.write_text("Risk=1\n", encoding="utf-8")
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.execute(
                    """create table candidates(
                    id integer primary key,set_path text,symbol text,target_symbol text,
                    period text,family text,report_path text,status text)"""
                )
                conn.execute(
                    "insert into candidates values(1,?,?,?,?,?,?,?)",
                    (str(pending), "NFLX", "NFLX", "H1", "stock", "report.html", "accepted"),
                )
                conn.commit()
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })

            # El candidato existe en la memoria pero no tiene robustez ni Final
            # Tick: la ventana de familia ya no lo enseña, igual que no lo cuenta
            # la fila del inventario desde la que se abre.
            self.assertEqual(source.candidate_rows(include_quarantined=True), [])
            with patch.object(source, "quarantine_rows", return_value=[]), patch.object(
                source, "used_set_paths", return_value=[]
            ), self.assertRaisesRegex(ValueError, "No se encontraron sets"):
                source.symbol_sets("NFLX")

    @staticmethod
    def _create_pipeline_source(project: Path) -> PortfolioSource:
        (project / "outputs").mkdir()
        (project / "assets").mkdir()
        memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            conn.executescript(
                """
                    create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,period text,family text,report_path text,status text);
                    create table candidate_robustness(candidate_id integer,report_path text,status text);
                    create table candidate_final_tick(candidate_id integer,real_tick_report_path text,from_date text,to_date text,status text);
                    create table candidate_final_tick_6m(candidate_id integer,ohlc_report_path text,real_tick_report_path text,from_date text,to_date text,status text);
                    insert into candidates values(1,'sets/a.set','EURUSD','EURUSD','H1','f','reports/a.html','accepted');
                    insert into candidates values(2,'sets/b.set','GBPUSD','GBPUSD','H1','f','reports/b.html','accepted');
                    insert into candidates values(3,'sets/c.set','USDJPY','USDJPY','H1','f','reports/c.html','accepted');
                    insert into candidate_robustness values(1,'reports/a_oos.html','accepted');
                    insert into candidate_robustness values(2,'reports/b_oos.html','rejected');
                    insert into candidate_robustness values(3,'reports/c_oos.html','accepted');
                    insert into candidate_final_tick values(1,'reports/a_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick values(2,'reports/b_full.html','2020.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick values(3,'reports/c_full.html','2020.01.01','2026.06.30','rejected');
                    insert into candidate_final_tick_6m values(1,'','','2026.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick_6m values(2,'','','2026.01.01','2026.06.30','accepted');
                    insert into candidate_final_tick_6m values(3,'','','2026.01.01','2026.06.30','accepted');
                """
            )
            conn.commit()
        (project / "reports").mkdir()
        (project / "reports" / "a_full.html").touch()
        return PortfolioSource({
            "portfolio_project_dir": str(project),
            "portfolio_broker": "ICTRADING",
            "portfolio_account_type": "STANDARD",
        })

    def _assert_pipeline_candidate(self, source: PortfolioSource, project: Path) -> tuple[dict, dict]:
        rows = source.candidate_rows(include_quarantined=False)
        self.assertEqual([row["source_candidate_id"] for row in rows], [1])
        self.assertEqual(rows[0]["candidate_id"], "ICTRADING/STANDARD:1")
        self.assertEqual(rows[0]["final_ohlc_report_path"], "")
        self.assertEqual(rows[0]["final_tick_report_path"], "")
        self.assertEqual(rows[0]["full_history_report_path"], str(project / "reports" / "a_full.html"))
        settings = normalize_settings("full_history", {"allowed_asset_groups": ["Forex"]}, "ICTRADING")
        self.assertEqual(source.inventory("full_history", settings)["available"], 1)
        disabled = source.inventory(
            "full_history",
            normalize_settings(
                "full_history",
                {"allowed_asset_groups": ["Forex"], "disabled_symbols": ["EURUSD"]},
                "ICTRADING",
            ),
        )
        self.assertEqual(disabled["available"], 0)
        self.assertTrue(disabled["by_symbol"][0]["disabled"])
        return rows[0], settings

    def _assert_pipeline_quarantine(
        self, source: PortfolioSource, row: dict, settings: dict,
    ) -> None:
        quarantine_id = source.exclude_strategy({"set_path": row["set_path"]})
        self.assertEqual(source.candidate_rows(include_quarantined=False), [])
        self.assertEqual(
            [item["source_candidate_id"] for item in source.candidate_rows(include_quarantined=True)], [1]
        )
        excluded = source.inventory("full_history", settings)
        monthly_settings = normalize_settings(
            "monthly", {"allowed_asset_groups": ["Forex"]}, "ICTRADING"
        )
        monthly = source.inventory("monthly", monthly_settings)
        self.assertEqual(excluded["by_symbol"], [{
            "symbol": "EURUSD", "total": 1, "quarantined": 1, "used": 0,
            "available": 0, "disabled": False,
        }])
        self.assertEqual(monthly["available"], 0)
        self.assertTrue(monthly["quarantine_excludes"])
        source.release_strategy(quarantine_id)
        self.assertEqual(source.inventory("full_history", settings)["available"], 1)

    def test_portfolio_source_reads_only_full_pipeline_accepted_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._create_pipeline_source(project)
            row, settings = self._assert_pipeline_candidate(source, project)
            self._assert_pipeline_quarantine(source, row, settings)
