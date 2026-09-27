"""Importar un portafolio desde su exportación.

El caso real: se exporta, se borra del manager, y meses después hace falta que
sus sets sigan contando como usados para que la siguiente generación no los
repita. Lo que se comprueba aquí es que la fila importada es la misma que la de
un guardado normal — no una copia degradada del texto del resumen — y que sus
sets vuelven a bloquear el pool.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import portfolio_import
from mt5_manager.portfolio_service import (
    PortfolioCoordinator,
    PortfolioSource,
    _imported_target_month,
    build_import_proposals,
    save_proposal,
)
from portfolio_manager.ubs_portfolio import (
    PeriodReport,
    RobustStrategySet,
    build_robust_strategy_set,
)

from tests.portfolio_import_test_support import (
    BLANK_PROFILE_IMPROVEMENT_SUMMARY,
    CHAINED_IMPROVEMENT_SUMMARY,
    IMPROVEMENT_SUMMARY,
    SUMMARY,
    ImportRoundTripTestCase,
    period,
    strategy,
)


class ImportRoundTripTests(ImportRoundTripTestCase):
    """La fila importada tiene que ser la de un guardado normal.

    Lo único que aporta el resumen es la composición. Todo lo demás se recalcula
    con `evaluate_portfolio` desde los informes del candidato, así que aquí se
    inyectan estrategias ya construidas —el parseo del HTML de MT5 tiene sus
    propias pruebas— y se ejecuta de verdad el resto del camino, incluido
    `save_proposal`.
    """
    def test_exact_exported_candidate_disambiguates_repeated_set_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            header, members = portfolio_import.parse_summary(IMPROVEMENT_SUMMARY)
            header["portfolio_members"] = [{
                "set_name": "alpha.set",
                "candidate_id": "ICTRADING/STANDARD:77",
                "set_path": str(project / "alpha.set"),
            }, {
                "set_name": "beta.set",
                "candidate_id": "ICTRADING/STANDARD:2",
                "set_path": str(project / "beta.set"),
            }]
            candidates = self._candidates(project)
            duplicate = dict(candidates[0])
            duplicate["candidate_id"] = "ICTRADING/STANDARD:77"
            duplicate["source_candidate_id"] = 77
            candidates.append(duplicate)
            loaded_rows: list[dict] = []

            def load(rows, *_args, **_kwargs):
                loaded_rows.extend(rows)
                return ([
                    strategy(str(project / "alpha.set"), "EURUSD", 77, 900.0),
                    strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
                ], [])

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=candidates
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                side_effect=load,
            ):
                _proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            alpha = [
                row for row in loaded_rows
                if Path(str(row.get("set_path") or "")).name == "alpha.set"
            ]
            self.assertEqual([row["candidate_id"] for row in alpha], ["ICTRADING/STANDARD:77"])
            self.assertEqual(report["ambiguous"], [])

    def test_old_exported_set_contents_disambiguate_repeated_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            header, members = portfolio_import.parse_summary(IMPROVEMENT_SUMMARY)
            first_path = project / "run-1" / "alpha.set"
            second_path = project / "run-2" / "alpha.set"
            first_path.parent.mkdir()
            second_path.parent.mkdir()
            first_path.write_text("Risk=1", encoding="utf-8")
            second_path.write_text("Risk=2", encoding="utf-8")
            header["_set_sha256_by_name"] = {
                "alpha.set": [hashlib.sha256(first_path.read_bytes()).hexdigest()]
            }
            candidates = self._candidates(project)
            candidates[0]["set_path"] = str(first_path)
            duplicate = dict(candidates[0])
            duplicate["candidate_id"] = "ICTRADING/STANDARD:77"
            duplicate["source_candidate_id"] = 77
            duplicate["set_path"] = str(second_path)
            candidates.append(duplicate)
            loaded_rows: list[dict] = []

            def load(rows, *_args, **_kwargs):
                loaded_rows.extend(rows)
                return ([
                    strategy(str(first_path), "EURUSD", 1, 900.0),
                    strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
                ], [])

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=candidates
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                side_effect=load,
            ):
                _proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            alpha = [
                row for row in loaded_rows
                if Path(str(row.get("set_path") or "")).name == "alpha.set"
            ]
            self.assertEqual([row["candidate_id"] for row in alpha], ["ICTRADING/STANDARD:1"])
            self.assertEqual(report["ambiguous"], [])

    def test_import_inventory_includes_candidates_with_changed_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            with sqlite3.connect(source.memory) as conn:
                conn.executescript("""
                    create table candidates (
                        id integer primary key,set_path text,symbol text,target_symbol text,
                        period text,family text,report_path text,status text
                    );
                    create table candidate_robustness (
                        candidate_id integer,report_path text,status text
                    );
                    create table candidate_final_tick (
                        candidate_id integer,real_tick_report_path text,status text
                    );
                    create table candidate_final_tick_6m (
                        candidate_id integer,ohlc_report_path text,real_tick_report_path text,
                        from_date text,to_date text,status text,real_tick_metrics_json text
                    );
                """)
                conn.execute(
                    "insert into candidates values (1,?,?,?,?,?,?,?)",
                    (str(project / "alpha.set"), "EURUSD", "EURUSD", "H1", "", "base.htm", "accepted"),
                )
                conn.execute(
                    "insert into candidate_robustness values (?,?,?)",
                    (1, "robust.htm", "rejected"),
                )
                conn.execute(
                    "insert into candidate_final_tick values (?,?,?)",
                    (1, "tick.htm", "accepted"),
                )
                conn.execute(
                    "insert into candidate_final_tick_6m values (?,?,?,?,?,?,?)",
                    (1, "ohlc6m.htm", "tick6m.htm", "2026.01.01", "2026.06.30", "rejected", "{}"),
                )
                conn.commit()
            conn.close()

            rows = source.import_candidate_rows()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_candidate_id"], 1)
            self.assertEqual(rows[0]["robustness_status"], "rejected")
            self.assertEqual(rows[0]["final_tick_6m_status"], "rejected")

    def test_import_inventory_recovers_a_deleted_robustness_row_from_its_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            reports = project / "reports"
            reports.mkdir()
            recovered = reports / "robust_000001_alpha.htm"
            recovered.write_text("informe histórico", encoding="utf-8")
            with sqlite3.connect(source.memory) as conn:
                conn.executescript("""
                    create table candidates (
                        id integer primary key,set_path text,symbol text,target_symbol text,
                        period text,family text,report_path text,status text
                    );
                    create table candidate_robustness (
                        candidate_id integer,report_path text,status text
                    );
                """)
                conn.execute(
                    "insert into candidates values (1,?,?,?,?,?,?,?)",
                    (str(project / "alpha.set"), "XAUUSD", "XAUUSD", "H4", "", "base.htm", "rejected"),
                )
                conn.commit()
            conn.close()

            rows = source.import_candidate_rows()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["oos_report_path"], str(recovered))
            self.assertTrue(rows[0]["historical_robustness_report_recovered"])

    def test_the_import_inventory_only_prepares_the_sets_of_the_export(self) -> None:
        # Preparar la memoria entera para resolver las lineas de un resumen
        # costaba 7,5 s y decenas de miles de `is_file()` en RoboForex (70.065
        # candidatos) para acabar usando 18 filas. Acotar no puede cambiar el
        # resultado: las filas relevantes tienen que ser exactamente las mismas.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            reports = project / "reports"
            reports.mkdir()
            (reports / "robust_000002_beta.htm").write_text("histórico", encoding="utf-8")
            with sqlite3.connect(source.memory) as conn:
                conn.executescript("""
                    create table candidates (
                        id integer primary key,set_path text,symbol text,target_symbol text,
                        period text,family text,report_path text,status text
                    );
                    create table candidate_robustness (
                        candidate_id integer,report_path text,status text
                    );
                """)
                # Ruta guardada por un nodo Windows: en un manager Linux hay que
                # cortar por los dos separadores para reconocer el nombre.
                conn.execute(
                    "insert into candidates values (1,?,?,?,?,?,?,?)",
                    (r"C:\Users\nodo\outputs\alpha.set", "EURUSD", "EURUSD", "H1", "", "base.htm", "accepted"),
                )
                conn.execute(
                    "insert into candidates values (2,?,?,?,?,?,?,?)",
                    (r"C:\Users\nodo\outputs\beta.set", "GBPUSD", "GBPUSD", "H1", "", "base.htm", "accepted"),
                )
                conn.execute(
                    "insert into candidates values (3,?,?,?,?,?,?,?)",
                    (r"C:\Users\nodo\outputs\gamma.set", "USDJPY", "USDJPY", "H1", "", "base.htm", "accepted"),
                )
                conn.commit()
            conn.close()

            narrow = source.import_candidate_rows({"beta.set"})
            full = source.import_candidate_rows()

            self.assertEqual(len(full), 3)
            self.assertEqual([Path(row["set_path"]).name for row in narrow], ["beta.set"])
            relevant = [row for row in full if Path(row["set_path"]).name == "beta.set"]
            self.assertEqual(narrow, relevant)
            # Y el rescate del informe histórico sigue ocurriendo en la fila acotada.
            self.assertTrue(narrow[0]["historical_robustness_report_recovered"])
            self.assertEqual(source.import_candidate_rows(set()), [])

    def test_an_exported_bundle_comes_back_as_a_normal_saved_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            portfolio_id, report, selected_key = self._import_bundle(source, project)
            row, variants, members_saved = self._saved_bundle(source, portfolio_id)
            self._assert_imported_bundle(
                source, project, portfolio_id, report, selected_key,
                row, variants, members_saved,
            )

    def _import_bundle(self, source, project: Path):
        (project / "alpha.set").write_text("Risk=1\n", encoding="utf-8")
        (project / "beta.set").write_text("Risk=1\n", encoding="utf-8")
        strategies = [
            strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
            strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
        ]
        header, members = portfolio_import.parse_summary(SUMMARY)
        with patch.object(
            PortfolioSource, "import_candidate_rows", return_value=self._candidates(project),
        ), patch(
            "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
            return_value=(strategies, []),
        ):
            proposals, selected_key, report = build_import_proposals(
                source, "full_history", header, members,
            )
            self.assertTrue(all(
                item["inputs"]["portfolio_alias"] == "Londres estable"
                for item in proposals
            ))
            portfolio_id = save_proposal(
                source, proposals, selected_key, "full_history",
            )
        return portfolio_id, report, selected_key

    @staticmethod
    def _saved_bundle(source, portfolio_id: int):
        with source.connect() as conn:
            row = conn.execute(
                "select portfolio_type,capital,metrics_json from portfolios where id=?",
                (portfolio_id,),
            ).fetchone()
            variants = conn.execute(
                "select variant_key,set_path,units from portfolio_allocations "
                "where portfolio_id=? order by variant_key,set_path",
                (portfolio_id,),
            ).fetchall()
            members_saved = conn.execute(
                "select count(*) from portfolio_members where portfolio_id=?",
                (portfolio_id,),
            ).fetchone()[0]
        return row, variants, members_saved

    def _assert_imported_bundle(
        self, source, project, portfolio_id, report, selected_key,
        row, variants, members_saved,
    ) -> None:
        self.assertEqual(report["variants"], ["aggressive", "balanced", "conservative"])
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(selected_key, "balanced")
        self.assertEqual((row["portfolio_type"], row["capital"]), ("bundle", 10000.0))
        self.assertTrue(json.loads(row["metrics_json"])["portfolio_bundle"])
        detail = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
        self.assertEqual(detail["alias"], "Londres estable")
        exported = source.export_portfolio(portfolio_id, "full_history", str(project / "exported"))
        exported_header, _members, _sets = portfolio_import.read_export(exported["folder"])
        self.assertEqual(exported_header["portfolio_alias"], "Londres estable")
        saved = [(row["variant_key"], Path(row["set_path"]).name, row["units"]) for row in variants]
        self.assertEqual(saved, [
            ("aggressive", "alpha.set", 3), ("aggressive", "beta.set", 2),
            ("balanced", "alpha.set", 2), ("balanced", "beta.set", 1),
            ("conservative", "alpha.set", 1), ("conservative", "beta.set", 1),
        ])
        self.assertEqual(members_saved, len(variants))
        used = {Path(path).name for path in source.used_set_paths("full_history")}
        self.assertEqual(used, {"alpha.set", "beta.set"})

    def test_an_exported_improvement_keeps_all_its_visible_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(IMPROVEMENT_SUMMARY)

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, selected_key, report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")

            saved = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
            self.assertEqual(selected_key, "balanced")
            self.assertEqual(saved["portfolio_type"], "balanced")
            self.assertEqual(saved["improvement_origin"], {
                "source_id": 73,
                "mode": "balanced",
                "root_id": 73,
                "depth": 1,
                "priority": "stress",
                "priority_label": "Menor estrés",
                "added_count": 1,
                "label": "Mejora del portafolio #73 | modo Moderado",
            })
            self.assertFalse(saved["metrics"].get("portfolio_bundle", False))
            self.assertEqual(report["improvement_origin"], {
                "source_id": 73, "mode": "balanced",
            })
            for set_name in ("alpha.set", "beta.set"):
                (project / set_name).write_text("Risk=1\n", encoding="utf-8")
            exported = source.export_portfolio(
                portfolio_id, "full_history", str(project / "exported")
            )
            exported_header, _members, _sets = portfolio_import.read_export(
                exported["folder"]
            )
            self.assertEqual(exported_header["improvement_source_portfolio_id"], 73.0)
            self.assertEqual(exported_header["improvement_portfolio_type"], "balanced")
            self.assertEqual(exported_header["improvement_selection_priority"], "stress")
            self.assertEqual(exported_header["improvement_added_count"], 1.0)


if __name__ == "__main__":
    unittest.main()
