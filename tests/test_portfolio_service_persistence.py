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


class PortfolioPersistenceTests(unittest.TestCase):
    def test_recent_contribution_default_is_five_percent(self) -> None:
        settings = normalize_settings("full_history", {"allowed_asset_groups": ["Forex"]})
        self.assertEqual(settings["min_strategy_recent_contribution_pct"], 5.0)

    def test_excluding_a_bundle_member_quarantines_it_and_keeps_the_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })
            set_path = str(project / "strategy.set")
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                    ("2026-07-15", "A/M/C", "bundle", "bundle", "full_history", json.dumps({"portfolio_bundle": True})),
                ).lastrowid)
                conn.execute(
                    """insert into portfolio_allocations(
                       portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
                       net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe
                       ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, "conservative", "Conservador", set_path, "ICTRADING/STANDARD:7",
                     "EURUSD", 1, .01, 100, 20, 10, set_path, "H1"),
                )
                conn.commit()
            candidate = {
                "set_path": set_path,
                "source_memory_path": str(memory),
                "account_type": "ICTRADING/STANDARD",
                "source_candidate_id": 7,
                "target_symbol": "EURUSD",
                "period": "H1",
            }

            with patch.object(source, "candidate_rows", return_value=[candidate]), patch.object(
                source, "_recalculate_saved", side_effect=AssertionError("no debe recalcular")
            ):
                quarantine_id = source.remove_member_to_quarantine(
                    {"portfolio_id": portfolio_id, "set_path": set_path}, "full_history"
                )

            self.assertGreater(quarantine_id, 0)
            with source.connect() as conn:
                # El portafolio guardado no es un efecto colateral de una decision
                # sobre el pool: sigue ahi, con su asignacion intacta.
                self.assertIsNotNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())
                self.assertEqual(conn.execute(
                    "select count(*) from portfolio_allocations where portfolio_id=?", (portfolio_id,)
                ).fetchone()[0], 1)
                quarantine = conn.execute(
                    "select set_path,source_portfolio_id from portfolio_quarantine where id=?", (quarantine_id,)
                ).fetchone()
            self.assertEqual(quarantine["set_path"], set_path)
            self.assertEqual(quarantine["source_portfolio_id"], portfolio_id)

    def test_excluding_a_monthly_member_quarantines_it_and_keeps_the_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })
            set_path = str(project / "strategy.set")
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                    ("2026-07-20", "UBS Mensual", "balanced", "balanced", "monthly", json.dumps({})),
                ).lastrowid)
                conn.execute(
                    """insert into portfolio_allocations(
                       portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
                       net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe
                       ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, "default", "Equilibrada", set_path, "ICTRADING/STANDARD:7",
                     "USDJPY", 13, .13, 742, 252, 0, set_path, "H1"),
                )
                conn.commit()
            candidate = {
                "set_path": set_path,
                "source_memory_path": str(memory),
                "account_type": "ICTRADING/STANDARD",
                "source_candidate_id": 7,
                "target_symbol": "USDJPY",
                "period": "H1",
            }

            with patch.object(source, "candidate_rows", return_value=[candidate]), patch.object(
                source, "_recalculate_saved", side_effect=AssertionError("no debe recalcular")
            ):
                quarantine_id = source.remove_member_to_quarantine(
                    {"portfolio_id": portfolio_id, "set_path": set_path}, "monthly"
                )

            self.assertGreater(quarantine_id, 0)
            with source.connect() as conn:
                # El portafolio guardado no es un efecto colateral de una decision
                # sobre el pool: sigue ahi, con su asignacion intacta.
                self.assertIsNotNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())
                self.assertEqual(conn.execute(
                    "select count(*) from portfolio_allocations where portfolio_id=?", (portfolio_id,)
                ).fetchone()[0], 1)
                quarantine = conn.execute(
                    "select set_path,source_portfolio_id from portfolio_quarantine where id=?", (quarantine_id,)
                ).fetchone()
            self.assertEqual(quarantine["set_path"], set_path)
            self.assertEqual(quarantine["source_portfolio_id"], portfolio_id)

    def test_excluding_a_member_saved_under_a_foreign_project_root_still_matches(self) -> None:
        # Regression: portfolio 49 was saved while the manager ran in Docker, so
        # its members were stored under /data/roboforex/.../outputs/... . The
        # manager now runs on Windows (project on a mapped drive). The member
        # lookup compared a freshly resolved request against the raw stored path,
        # so it never matched and the exclusion aborted with "no se encontró la
        # estrategia". Both sides must be resolved to the current project first.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "outputs").mkdir()
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            source = PortfolioSource({
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            })
            # Stored under a Docker root that no longer exists on this manager.
            foreign_path = "/data/roboforex/TRADING/proj/outputs/sets/USDJPY_M15_strategy.set"
            resolved = _resolve_source_path(foreign_path, project)  # what candidate_rows yields now
            with source.connect(write=True) as conn:
                portfolio_id = int(conn.execute(
                    "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                    ("2026-07-20", "UBS Mensual", "balanced", "balanced", "monthly", json.dumps({})),
                ).lastrowid)
                conn.execute(
                    """insert into portfolio_allocations(
                       portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
                       net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe
                       ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, "default", "Equilibrada", foreign_path, "ICTRADING/STANDARD:7",
                     "USDJPY", 13, .13, 742, 252, 0, foreign_path, "H1"),
                )
                conn.commit()
            candidate = {
                "set_path": resolved,
                "source_memory_path": str(memory),
                "account_type": "ICTRADING/STANDARD",
                "source_candidate_id": 7,
                "target_symbol": "USDJPY",
                "period": "H1",
            }

            with patch.object(source, "candidate_rows", return_value=[candidate]), patch.object(
                source, "_recalculate_saved", side_effect=AssertionError("no debe recalcular")
            ):
                quarantine_id = source.remove_member_to_quarantine(
                    {"portfolio_id": portfolio_id, "set_path": foreign_path}, "monthly"
                )

            self.assertGreater(quarantine_id, 0)
            with source.connect() as conn:
                self.assertIsNotNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())

    def test_excluding_multiple_bundle_members_quarantines_all_and_keeps_the_bundle(self) -> None:
        source = object.__new__(PortfolioSource)
        source.project = Path(".")
        events: list[str] = []
        first_path = str(Path("first.set").absolute())
        second_path = str(Path("second.set").absolute())
        detail = {"portfolio": {
            "portfolio_type": "bundle",
            "metrics": {"portfolio_bundle": True},
            "members": [
                {"set_path": first_path, "set_id": first_path},
                {"set_path": second_path, "set_id": second_path},
            ],
        }}

        def quarantine(member: dict[str, object], *_args: object) -> int:
            events.append(f"exclude:{member['set_path']}")
            return len(events)

        with patch.object(source, "saved_portfolio_detail", return_value=detail), patch.object(
            source, "_quarantine_member", side_effect=quarantine
        ), patch.object(source, "delete_portfolio", side_effect=AssertionError("no debe borrar")):
            quarantine_ids = source.remove_members_to_quarantine(
                {"portfolio_id": 40, "set_paths": ["first.set", "second.set"]}, "full_history"
            )

        self.assertEqual(quarantine_ids, [1, 2])
        self.assertEqual(events, [f"exclude:{first_path}", f"exclude:{second_path}"])

    def test_excluding_multiple_monthly_members_is_allowed_and_keeps_the_month(self) -> None:
        # La exclusión múltiple vale aunque el tipo no sea 'bundle', y el mes
        # guardado sobrevive igual que el A/M/C.
        source = object.__new__(PortfolioSource)
        source.project = Path(".")
        events: list[str] = []
        first_path = str(Path("first.set").absolute())
        second_path = str(Path("second.set").absolute())
        detail = {"portfolio": {
            "portfolio_type": "aggressive",
            "target_month": 8,
            "metrics": {},
            "members": [
                {"set_path": first_path, "set_id": first_path},
                {"set_path": second_path, "set_id": second_path},
            ],
        }}
        reasons: list[str] = []

        def quarantine(member: dict[str, object], _portfolio_id: object, _payload: object,
                       _is_bundle: object, member_scope: str) -> int:
            events.append(f"exclude:{member['set_path']}")
            reasons.append(member_scope)
            return len(events)

        with patch.object(source, "saved_portfolio_detail", return_value=detail), patch.object(
            source, "_quarantine_member", side_effect=quarantine
        ), patch.object(source, "delete_portfolio", side_effect=AssertionError("no debe borrar")):
            quarantine_ids = source.remove_members_to_quarantine(
                {"portfolio_id": 88, "set_paths": ["first.set", "second.set"]}, "monthly"
            )

        self.assertEqual(quarantine_ids, [1, 2])
        self.assertEqual(events, [f"exclude:{first_path}", f"exclude:{second_path}"])
        self.assertEqual(reasons, ["monthly", "monthly"])

    def test_excluding_multiple_members_is_rejected_on_a_single_objective_portfolio(self) -> None:
        # Las casillas de selección solo se ofrecen en A/M/C y mensuales, así que
        # la exclusión múltiple sigue vetada en un full_history de objetivo único
        # aunque ya no haya ninguna asimetría de borrado detrás.
        source = object.__new__(PortfolioSource)
        source.project = Path(".")
        detail = {"portfolio": {"portfolio_type": "balanced", "metrics": {}, "members": []}}

        with patch.object(source, "saved_portfolio_detail", return_value=detail):
            with self.assertRaises(ValueError) as error:
                source.remove_members_to_quarantine(
                    {"portfolio_id": 12, "set_paths": ["first.set"]}, "full_history"
                )

        self.assertIn("A/M/C y mensuales", str(error.exception))

    def _grid_package(self, coordinator: PortfolioCoordinator, node_id: str, set_paths: list[str]) -> int:
        grid = coordinator._persistence_source(node_id, "grid")
        with grid.connect(write=True) as conn:
            portfolio_id = int(conn.execute(
                "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) values(?,?,?,?,?,?)",
                ("2026-08-06", "Grid A/M/C", "grid_bundle", "grid_bundle", "grid",
                 json.dumps({"portfolio_bundle": True, "grid_portfolio": True})),
            ).lastrowid)
            for index, set_path in enumerate(set_paths, start=1):
                conn.execute(
                    """insert into portfolio_allocations(
                       portfolio_id,variant_key,variant_label,set_id,candidate_id,symbol,units,lot,
                       net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,timeframe
                       ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, "balanced", "Moderado Grid", set_path, f"ICTRADING/STANDARD:{index}",
                     "EURUSD", index, index * .01, 100, 20, 10, set_path, "H1"),
                )
            conn.commit()
        return portfolio_id

    def test_excluding_a_grid_member_quarantines_it_in_the_manager_and_keeps_the_package(self) -> None:
        # El endpoint de exclusión del nodo exige un portfolio_id que exista en
        # su memoria ("Falta el portafolio que contiene las estrategias"), y el
        # paquete Grid solo existe en el manager. La cuarentena Grid se escribe
        # por eso junto a los paquetes, nunca en la memoria del broker.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            (project / "outputs").mkdir(parents=True)
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], Path(temp_dir) / "settings.json")
            set_path = str(project / "grid_strategy.set")
            portfolio_id = self._grid_package(coordinator, "ic", [set_path])
            candidate = {
                "set_path": set_path,
                "source_memory_path": str(memory),
                "account_type": "ICTRADING/STANDARD",
                "source_candidate_id": 1,
                "target_symbol": "EURUSD",
                "period": "H1",
            }
            for scope in ("full_history", "monthly", "grid"):
                coordinator.proposals[coordinator._key("ic", scope)] = ["obsoleta"]

            with patch.object(PortfolioSource, "candidate_rows", return_value=[candidate]):
                result = coordinator.exclude_grid("ic", {"portfolio_id": portfolio_id, "set_path": set_path})

            self.assertFalse(result["deleted"])
            self.assertEqual(result["portfolio_id"], portfolio_id)
            self.assertGreater(result["quarantine_id"], 0)
            with coordinator._persistence_source("ic", "grid").connect() as conn:
                quarantine = conn.execute(
                    "select set_path from portfolio_quarantine where id=?", (result["quarantine_id"],)
                ).fetchone()
                self.assertIsNotNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())
            self.assertEqual(quarantine["set_path"], set_path)
            # La memoria del broker no se toca: la exclusión Grid es de Grid.
            with PortfolioSource(node).connect() as conn:
                self.assertEqual(conn.execute(
                    "select count(*) from sqlite_master where type='table' and name='portfolio_quarantine'"
                ).fetchone()[0], 0)
            self.assertEqual(coordinator.proposals, {})
            # El pool Grid la ve en cuarentena y la reintegración la encuentra en
            # la base del manager a través de su clave de cuarentena.
            rows = coordinator._calculation_source("ic", "grid").quarantine_rows()
            self.assertEqual([row["set_path"] for row in rows], [set_path])
            coordinator.release("ic", "grid", str(rows[0]["quarantine_key"]))
            self.assertEqual(coordinator._calculation_source("ic", "grid").quarantine_rows(), [])

    def test_excluding_several_grid_members_quarantines_all_and_keeps_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            (project / "outputs").mkdir(parents=True)
            (project / "assets").mkdir()
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], Path(temp_dir) / "settings.json")
            paths = [str(project / "first_grid.set"), str(project / "second_grid.set")]
            portfolio_id = self._grid_package(coordinator, "ic", paths)
            candidates = [
                {
                    "set_path": path,
                    "source_memory_path": str(memory),
                    "account_type": "ICTRADING/STANDARD",
                    "source_candidate_id": index,
                    "target_symbol": "EURUSD",
                    "period": "H1",
                }
                for index, path in enumerate(paths, start=1)
            ]

            with patch.object(PortfolioSource, "candidate_rows", return_value=candidates):
                result = coordinator.exclude_grid(
                    "ic", {"portfolio_id": portfolio_id, "set_paths": paths}
                )

            self.assertEqual(len(result["quarantine_ids"]), 2)
            with coordinator._persistence_source("ic", "grid").connect() as conn:
                stored = [row[0] for row in conn.execute("select set_path from portfolio_quarantine order by id")]
                self.assertIsNotNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())
            self.assertEqual(stored, paths)

    def test_excluding_a_grid_member_rejects_a_set_outside_the_saved_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            (project / "outputs").mkdir(parents=True)
            (project / "assets").mkdir()
            (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
            node = {
                "id": "ic",
                "portfolio_project_dir": str(project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
            }
            coordinator = PortfolioCoordinator([node], Path(temp_dir) / "settings.json")
            portfolio_id = self._grid_package(coordinator, "ic", [str(project / "member.set")])

            with self.assertRaises(ValueError):
                coordinator.exclude_grid(
                    "ic", {"portfolio_id": portfolio_id, "set_path": str(project / "other.set")}
                )

            with coordinator._persistence_source("ic", "grid").connect() as conn:
                self.assertIsNotNone(conn.execute("select id from portfolios where id=?", (portfolio_id,)).fetchone())

    @staticmethod
    def _bundle_save_proposal(
        base_inputs: dict[str, object], key: str, label: str, units: int
    ) -> dict[str, object]:
        inputs = {
            **base_inputs,
            "portfolio_type": key,
            "composition_portfolio_type": "balanced",
        }
        allocation = StrategyAllocation(
            "same.set", "ICTRADING/STANDARD:1", "EURUSD", units, units * 0.01,
            100 * units, 20 * units, 10 * units, "H1", "same.set", "is.html", "oos.html", 0.01,
        )
        result = PortfolioResult(
            [allocation], [0, 100 * units], 100 * units, 20 * units, 10 * units,
            300, 300, 10, 5, units * 0.01, units, 1, "ok", [], [],
        )
        return {"key": key, "label": label, "reserve_pct": 10, "inputs": inputs, "result": result}

    def _prepare_bundle_save(self, project: Path) -> tuple[dict[str, object], PortfolioCoordinator, str]:
        (project / "outputs").mkdir()
        (project / "assets").mkdir()
        (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
        node = {
            "id": "ic", "portfolio_project_dir": str(project),
            "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD",
        }
        coordinator = PortfolioCoordinator([node], project / "settings.json")
        base_inputs = normalize_settings(
            "full_history", {"capital": 5000, "valley_dd_pct": 6, "account_leverage": 100},
            "ICTRADING",
        )
        key = coordinator._key("ic", "full_history")
        coordinator.proposals[key] = [
            self._bundle_save_proposal(base_inputs, "aggressive", "Agresivo", 3),
            self._bundle_save_proposal(base_inputs, "balanced", "Moderado", 2),
            self._bundle_save_proposal(base_inputs, "conservative", "Conservador", 1),
        ]
        coordinator.jobs[key] = {"status": "completed", "operation": "generate"}
        return node, coordinator, key

    def _assert_saved_bundle(
        self, saved: dict[str, object], portfolio_id: int,
        coordinator: PortfolioCoordinator, key: str,
    ) -> None:
        self.assertEqual(saved["id"], portfolio_id)
        self.assertEqual(saved["capital"], 5000)
        self.assertEqual(saved["portfolio_type"], "bundle")
        self.assertEqual(saved["metrics"]["inputs"]["account_leverage"], 100.0)
        self.assertEqual(
            {variant["inputs"]["account_leverage"] for variant in saved["metrics"]["variants"].values()},
            {100.0},
        )
        self.assertEqual(len(saved["members"]), 3)
        self.assertEqual({row["variant_key"] for row in saved["members"]}, {
            "aggressive", "balanced", "conservative",
        })
        self.assertNotIn(key, coordinator.proposals)
        self.assertEqual(coordinator.jobs[key]["last_saved_id"], portfolio_id)

    def test_save_selected_bundle_commits_and_is_readable_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            node, coordinator, key = self._prepare_bundle_save(Path(temp_dir))
            payload = coordinator.prepare_save("ic", "full_history", "balanced")
            confirmation = save_portfolio_payload(PortfolioSource(node), payload)
            portfolio_id = int(confirmation["portfolio_id"])
            retry = save_portfolio_payload(PortfolioSource(node), payload)
            self.assertEqual(retry["portfolio_id"], portfolio_id)
            self.assertTrue(retry["deduplicated"])
            coordinator.confirm_save(
                "ic", "full_history", str(confirmation["request_id"]), portfolio_id
            )
            saved = PortfolioSource(node).saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
            self._assert_saved_bundle(saved, portfolio_id, coordinator, key)

    def test_confirming_a_save_forces_the_next_read_to_recopy_the_node_memory(self) -> None:
        # El nodo escribe la fila en su memoria y el manager solo la ve a traves
        # de una copia que se reutiliza mientras tamano y mtime del original
        # parezcan iguales. Sobre un bind mount esos atributos van por detras del
        # contenido, asi que la lista se repintaba justo despues del guardado con
        # la copia anterior y el portafolio recien confirmado no aparecia.
        for scope in ("full_history", "monthly"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as temp_dir:
                project = Path(temp_dir) / "project"
                (project / "outputs").mkdir(parents=True)
                (project / "assets").mkdir()
                memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
                memory.touch()
                node = {
                    "id": "ic",
                    "portfolio_project_dir": str(project),
                    "portfolio_broker": "ICTRADING",
                    "portfolio_account_type": "STANDARD",
                }
                coordinator = PortfolioCoordinator([node], project / "settings.json")
                key = coordinator._key("ic", scope)
                coordinator.jobs[key] = {
                    "status": "completed",
                    "operation": "generate",
                    "save_request_id": "req-1",
                    "save_selected_key": "balanced",
                }
                snapshots = Path(temp_dir) / "snapshots"
                signature = snapshots / "ic" / f"{memory.name}.snapshot.json"
                signature.parent.mkdir(parents=True)
                signature.write_text("{}", encoding="utf-8")

                with patch.object(
                    PortfolioSource, "_snapshot_root", classmethod(lambda cls: snapshots)
                ):
                    coordinator.confirm_save("ic", scope, "req-1", 11)

                self.assertFalse(signature.exists())
                self.assertEqual(coordinator.jobs[key]["last_saved_id"], 11)

    def test_confirming_a_save_survives_a_node_without_a_local_memory(self) -> None:
        # Sin portfolio_project_dir el manager proxifica las lecturas al nodo: no
        # hay copia que invalidar y el guardado ya esta confirmado, de modo que
        # esto no puede convertirse en un error.
        coordinator = PortfolioCoordinator([{"id": "remote"}], Path("settings.json"))
        key = coordinator._key("remote", "full_history")
        coordinator.jobs[key] = {"status": "completed", "save_request_id": "req-1"}

        coordinator.confirm_save("remote", "full_history", "req-1", 11)

        self.assertEqual(coordinator.jobs[key]["last_saved_id"], 11)
