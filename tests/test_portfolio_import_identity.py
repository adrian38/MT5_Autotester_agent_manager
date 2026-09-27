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


class ImportIdentityRoundTripTests(ImportRoundTripTestCase):
    """La fila importada tiene que ser la de un guardado normal.

    Lo único que aporta el resumen es la composición. Todo lo demás se recalcula
    con `evaluate_portfolio` desde los informes del candidato, así que aquí se
    inyectan estrategias ya construidas —el parseo del HTML de MT5 tiene sus
    propias pruebas— y se ejecuta de verdad el resto del camino, incluido
    `save_proposal`.
    """
    def test_an_improvement_without_profile_column_takes_its_mode_from_the_header(self) -> None:
        # El caso real de RoboForex #120 y #121: una mejora se guarda con
        # `variant_key` vacio, asi que su resumen no tiene perfil. Sin leer el
        # modo de la cabecera la variante caia en «variant_1» y la importacion
        # moria con «La identidad de mejora ... no coincide con su composicion».
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(BLANK_PROFILE_IMPROVEMENT_SUMMARY)
            self.assertEqual({member.variant_label for member in members}, {""})
            self.assertEqual([member.units for member in members], [3, 2])

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows", return_value=(strategies, [])
            ):
                proposals, selected_key, report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")

            self.assertEqual(selected_key, "aggressive")
            self.assertEqual(report["variants"], ["aggressive"])
            saved = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
            self.assertEqual(saved["portfolio_type"], "aggressive")
            self.assertEqual(saved["improvement_origin"]["source_id"], 104)
            self.assertEqual(saved["improvement_origin"]["mode"], "aggressive")
            self.assertEqual(
                {Path(member["set_path"]).name for member in saved["members"]},
                {"alpha.set", "beta.set"},
            )
            # Y la reexportacion ya no vuelve a perder el perfil.
            for set_name in ("alpha.set", "beta.set"):
                (project / set_name).write_text("Risk=1\n", encoding="utf-8")
            exported = source.export_portfolio(portfolio_id, "full_history", str(project / "exported"))
            _header, exported_members, _sets = portfolio_import.read_export(exported["folder"])
            self.assertEqual({member.variant_label for member in exported_members}, {"Agresivo"})

    def test_an_export_that_repeats_its_parent_uid_gets_its_own_identity(self) -> None:
        # PORTAFOLIO_121 de RoboForex venia con el UID de su origen #120 como
        # propio. Conservarlo dejaria dos filas con la misma identidad y una
        # mejora que se compara consigo misma.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(
                BLANK_PROFILE_IMPROVEMENT_SUMMARY.replace(
                    "Portafolio UID: 44444444-4444-4444-8444-444444444444",
                    "Portafolio UID: 11111111-1111-4111-8111-111111111111",
                )
            )

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows", return_value=(strategies, [])
            ):
                proposals, selected_key, _report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")

            self.assertNotEqual(
                proposals[0]["inputs"].get("portfolio_uid"),
                "11111111-1111-4111-8111-111111111111",
            )
            self.assertEqual(
                proposals[0]["inputs"]["improvement_parent_uid"],
                "11111111-1111-4111-8111-111111111111",
            )
            self.assertTrue(any(
                "identidad propia" in warning
                for warning in proposals[0]["result"].warnings
            ))
            saved = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
            self.assertEqual(saved["improvement_origin"]["source_uid"], "11111111-1111-4111-8111-111111111111")

    def test_a_chained_improvement_round_trip_keeps_label_lineage_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(CHAINED_IMPROVEMENT_SUMMARY)
            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows", return_value=(strategies, [])
            ):
                proposals, selected_key, _report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")

            saved = source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]
            self.assertEqual(saved["name"], "Mejora del portafolio #36 | modo Moderado")
            self.assertEqual(saved["improvement_origin"]["root_id"], 14)
            self.assertEqual(saved["improvement_origin"]["depth"], 2)
            self.assertEqual(len(saved["improvement_origin"]["lineage"]), 2)
            audit = saved["metrics"]["seasonal_validation"]["portfolio_improvement"]
            self.assertEqual(audit["source_snapshot"]["id"], 36)

            for set_name in ("alpha.set", "beta.set"):
                (project / set_name).write_text("Risk=1\n", encoding="utf-8")
            exported = source.export_portfolio(portfolio_id, "full_history", str(project / "exported"))
            exported_header, _members, _sets = portfolio_import.read_export(exported["folder"])
            self.assertEqual(exported_header["improvement_label"], saved["name"])
            self.assertEqual(exported_header["improvement_root_portfolio_id"], 14.0)
            self.assertEqual(exported_header["improvement_depth"], 2.0)
            self.assertEqual(exported_header["improvement_source_snapshot"]["id"], 36)

    def test_a_regular_export_also_carries_a_portable_uid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(SUMMARY)
            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows", return_value=(strategies, [])
            ):
                proposals, selected_key, _report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")
            for set_name in ("alpha.set", "beta.set"):
                (project / set_name).write_text("Risk=1\n", encoding="utf-8")

            exported = source.export_portfolio(portfolio_id, "full_history", str(project / "exported"))
            exported_header, _members, _sets = portfolio_import.read_export(exported["folder"])

            self.assertRegex(
                exported_header["portfolio_uid"],
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            )

    def test_an_old_improvement_export_recovers_added_count_from_its_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            with source.connect(write=True) as conn:
                source_id = int(conn.execute(
                    "insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,metrics_json) "
                    "values(?,?,?,?,?,?)",
                    ("2026-09-09", "Base", "bundle", "bundle", "full_history", "{}"),
                ).lastrowid)
                conn.execute(
                    "insert into portfolio_allocations(portfolio_id,variant_key,variant_label,set_id,"
                    "candidate_id,symbol,set_path,units,lot,net_profit_contribution,"
                    "standalone_valley_dd,standalone_point_dd) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (source_id, "balanced", "Moderado", str(project / "alpha.set"),
                     "ICTRADING/STANDARD:1", "EURUSD", str(project / "alpha.set"), 2, .02,
                     100.0, 10.0, 5.0),
                )
                conn.commit()
            old = IMPROVEMENT_SUMMARY.replace("#73", f"#{source_id}").replace(
                "Mejora origen: 73\nMejora modo: balanced\nMejora prioridad: stress\nMejora incorporaciones: 1\n",
                "",
            )
            header, members = portfolio_import.parse_summary(old)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, _selected_key, _report = build_import_proposals(
                    source, "full_history", header, members
                )

            audit = proposals[0]["result"].seasonal_validation["portfolio_improvement"]
            self.assertEqual(audit["source_portfolio_id"], source_id)
            self.assertEqual(audit["target_portfolio_type"], "balanced")
            self.assertEqual(audit["added_count"], 1)
            self.assertNotIn("selection_priority", audit)

    def test_the_numbers_are_recalculated_from_the_reports_not_copied_from_the_text(self) -> None:
        # El resumen dice net 4.120,55; las estrategias inyectadas dan otro
        # número. Si el importador copiara el texto, el guardado mentiría.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(PortfolioSource, "import_candidate_rows", return_value=self._candidates(project)), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, selected_key, _report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")

            with source.connect() as conn:
                saved = conn.execute(
                    "select total_net_profit,actual_valley_dd,metrics_json from portfolios where id=?",
                    (portfolio_id,),
                ).fetchone()
            self.assertNotEqual(saved["total_net_profit"], 4120.55)
            self.assertGreater(saved["total_net_profit"], 0)
            self.assertGreater(saved["actual_valley_dd"], 0)
            # La curva viene del cálculo, no del resumen, que no la lleva.
            self.assertGreater(len(json.loads(saved["metrics_json"])["equity_curve_2020_2026"]), 1)

    def test_a_set_without_any_reports_is_kept_and_marks_the_calculation_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            only_alpha = [self._candidates(project)[0]]
            strategies = [strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0)]
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(PortfolioSource, "import_candidate_rows", return_value=only_alpha), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            self.assertEqual(report["unresolved"], ["beta.set"])
            self.assertEqual(report["unmeasured"], ["beta.set"])
            self.assertFalse(report["calculation_complete"])
            self.assertEqual(report["strategies"], 2)
            self.assertTrue(all(
                {allocation.set_id for allocation in proposal["result"].allocations}
                == {str(project / "alpha.set"), "beta.set"}
                for proposal in proposals
            ))
            placeholder = next(
                allocation
                for allocation in proposals[0]["result"].allocations
                if allocation.set_id == "beta.set"
            )
            self.assertEqual(placeholder.units, 2)
            self.assertEqual(placeholder.lot, 0.02)
            self.assertIn("No reconstruido", placeholder.floating_dd_source)
            self.assertTrue(any("Cálculo incompleto" in warning for warning in report["warnings"]))

    def test_a_portfolio_without_any_reports_still_preserves_its_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(PortfolioSource, "import_candidate_rows", return_value=[]):
                proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            self.assertEqual(report["strategies"], 2)
            self.assertEqual(report["unmeasured"], ["alpha.set", "beta.set"])
            self.assertFalse(report["calculation_complete"])
            self.assertTrue(all(len(proposal["result"].allocations) == 2 for proposal in proposals))
            self.assertTrue(all(proposal["result"].total_net_profit == 0 for proposal in proposals))

    def test_a_matched_candidate_with_an_unreadable_report_is_kept_unmeasured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            candidates = self._candidates(project)
            only_alpha = [strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0)]
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=candidates
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                return_value=(only_alpha, ["1 candidato omitido: reporte ilegible"]),
            ):
                proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            self.assertEqual(report["unmeasured"], ["beta.set"])
            self.assertTrue(any("reporte ilegible" in warning for warning in report["warnings"]))
            self.assertTrue(all(len(proposal["result"].allocations) == 2 for proposal in proposals))

    def test_a_changed_current_verdict_warns_but_does_not_remove_the_exported_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            candidates = self._candidates(project)
            candidates[0].update({
                "base_status": "rejected", "robustness_status": "",
                "final_tick_status": "", "final_tick_6m_status": "",
                "historical_robustness_report_recovered": True,
            })
            candidates[1].update({
                "base_status": "accepted", "robustness_status": "accepted",
                "final_tick_status": "accepted", "final_tick_6m_status": "rejected",
            })
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(
                PortfolioSource, "import_candidate_rows", return_value=candidates
            ), patch(
                "mt5_manager.portfolio_import_build.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            self.assertEqual(report["strategies"], 2)
            self.assertTrue(all(len(proposal["result"].allocations) == 2 for proposal in proposals))
            self.assertTrue(any("exactamente desde el ZIP" in warning for warning in report["warnings"]))
            self.assertTrue(any("base=rejected" in warning for warning in report["warnings"]))
            self.assertTrue(any("informe histórico recuperado" in warning for warning in report["warnings"]))
            self.assertTrue(any("Final Tick 6M=rejected" in warning for warning in report["warnings"]))

    def test_a_monthly_export_recovers_its_target_month_from_the_name(self) -> None:
        # El mes no es un campo del resumen: viaja en el nombre. Sin él, el
        # mensual se evaluaría sobre la curva completa.
        header, _members = portfolio_import.parse_summary(
            SUMMARY.replace("A/M/C | Base Moderado | 2 sets", "Moderado | Mes 08 | 2 estrategias")
        )
        self.assertEqual(_imported_target_month(header), 8)
        self.assertIsNone(_imported_target_month({"name": "A/M/C | Base Moderado"}))


class ImportPersistenceRoutingTests(unittest.TestCase):
    def test_ubs_import_is_written_by_the_node_that_owns_the_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator(
                [{
                    "id": "ic", "url": "http://ic-node:8765", "token": "secret",
                    "portfolio_project_dir": temp_dir, "portfolio_broker": "ICTRADING",
                    "portfolio_account_type": "STANDARD",
                }],
                Path(temp_dir) / "portfolio-settings.json",
            )
            response = {"portfolio_id": 41}

            def node_save(_node, path, payload, timeout=60):
                self.assertEqual(path, "/api/v1/portfolios/save")
                self.assertEqual(timeout, 120)
                self.assertEqual(payload["scope"], "full_history")
                self.assertEqual(payload["operation"], "generate")
                self.assertEqual(payload["selected_key"], "balanced")
                response["request_id"] = payload["request_id"]
                return 201, response

            with patch.object(coordinator, "_post_to_node", side_effect=node_save) as post, patch(
                "mt5_manager.portfolio_service.save_portfolio_payload"
            ) as local_save:
                portfolio_id = coordinator._save_imported_ubs_proposals(
                    "ic", "full_history", [], "balanced"
                )

            self.assertEqual(portfolio_id, 41)
            post.assert_called_once()
            local_save.assert_not_called()

    def test_ubs_import_rejects_a_node_that_does_not_confirm_the_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator(
                [{
                    "id": "ic", "url": "http://ic-node:8765", "token": "secret",
                    "portfolio_project_dir": temp_dir, "portfolio_broker": "ICTRADING",
                    "portfolio_account_type": "STANDARD",
                }],
                Path(temp_dir) / "portfolio-settings.json",
            )
            with patch.object(
                coordinator, "_post_to_node", return_value=(201, {"portfolio_id": 41})
            ):
                with self.assertRaises(ValueError) as raised:
                    coordinator._save_imported_ubs_proposals(
                        "ic", "full_history", [], "balanced"
                    )

            self.assertIn("confirmó", str(raised.exception))

    def test_node_import_routing_is_rejected_outside_ubs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator(
                [{"id": "ic", "portfolio_project_dir": temp_dir}],
                Path(temp_dir) / "portfolio-settings.json",
            )
            for scope in ("monthly", "grid"):
                with self.subTest(scope=scope), self.assertRaises(ValueError):
                    coordinator._save_imported_ubs_proposals(
                        "ic", scope, [], "balanced"
                    )


if __name__ == "__main__":
    unittest.main()
