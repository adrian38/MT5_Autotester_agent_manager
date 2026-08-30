"""Importar un portafolio desde su exportación.

El caso real: se exporta, se borra del manager, y meses después hace falta que
sus sets sigan contando como usados para que la siguiente generación no los
repita. Lo que se comprueba aquí es que la fila importada es la misma que la de
un guardado normal — no una copia degradada del texto del resumen — y que sus
sets vuelven a bloquear el pool.
"""
from __future__ import annotations

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


SUMMARY = """Portafolio: A/M/C | Base Moderado | 2 sets | 09.08.2026 13:01
Tipo: bundle   Capital: 10,000
DD valle objetivo: 300.00
DD puntual objetivo: 300.00
DD valle usado: 254.31
DD puntual usado: 120.00
Net profit total 2020-2026: 4,120.55

Sets exportados: copia exacta del .set original probado.
No se modifica Risk, LotPerBalance_step, grid ni ningun otro parametro del EA.
UNID. y LOTE son la asignacion informativa calculada por el portafolio.

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
Agresivo     ICTRADING    EURUSD       H1          3    0.03   alpha.set
Agresivo     ICTRADING    GBPUSD       H1          2    0.02   beta.set
Moderado     ICTRADING    EURUSD       H1          2    0.02   alpha.set
Moderado     ICTRADING    GBPUSD       H1          1    0.01   beta.set
Conservador  ICTRADING    EURUSD       H1          1    0.01   alpha.set
Conservador  ICTRADING    GBPUSD       H1          1    0.01   beta.set
"""


class SummaryParsingTests(unittest.TestCase):
    def test_the_header_and_every_row_are_read_from_the_exported_summary(self) -> None:
        header, members = portfolio_import.parse_summary(SUMMARY)

        self.assertEqual(header["portfolio_type"], "bundle")
        self.assertEqual(header["capital"], 10000.0)
        self.assertEqual(header["target_valley_dd"], 300.0)
        self.assertEqual(header["total_net_profit"], 4120.55)
        self.assertEqual(len(members), 6)
        self.assertEqual(
            [(member.variant_label, member.set_name, member.units, member.lot) for member in members[:2]],
            [("Agresivo", "alpha.set", 3, 0.03), ("Agresivo", "beta.set", 2, 0.02)],
        )

    def test_a_truncated_profile_still_maps_to_its_variant(self) -> None:
        # El perfil se escribe truncado a 12 caracteres, asi que «Moderado Grid»
        # llega como «Moderado Gri»: comparar por igualdad perderia la variante.
        order = ["Moderado Gri", "Agresivo"]
        self.assertEqual(portfolio_import.variant_key_for("Moderado Gri", order), "balanced")
        self.assertEqual(portfolio_import.variant_key_for("Agresivo", order), "aggressive")
        self.assertEqual(portfolio_import.variant_key_for("Conservador", order), "conservative")

    def test_a_set_name_with_spaces_survives_the_fixed_width_columns(self) -> None:
        line = "Moderado     ICTRADING    EURUSD       H1          2    0.02   nombre con espacios.set"
        _header, members = portfolio_import.parse_summary(
            SUMMARY.rsplit("\n", 2)[0] + "\n" + line + "\n"
        )
        self.assertIn("nombre con espacios.set", [member.set_name for member in members])

    def test_a_real_export_folder_and_its_zip_read_the_same(self) -> None:
        # Los dos transportes son el reflejo de la exportación: carpeta con el
        # selector nativo, ZIP cuando el manager no puede abrir un diálogo.
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "PORTAFOLIO_7_A_M_C_20260809"
            folder.mkdir()
            (folder / "PORTAFOLIO_7_resumen.txt").write_text(SUMMARY, encoding="utf-8")
            (folder / "alpha.set").write_text("Risk=1", encoding="utf-8")
            (folder / "beta.set").write_text("Risk=1", encoding="utf-8")
            archive = Path(temp_dir) / "export.zip"
            with zipfile.ZipFile(archive, "w") as zip_file:
                for path in sorted(folder.iterdir()):
                    zip_file.write(path, Path(folder.name) / path.name)

            from_folder = portfolio_import.read_export(folder)
            from_zip = portfolio_import.read_export(archive)

            self.assertEqual(from_folder[0], from_zip[0])
            self.assertEqual(from_folder[1], from_zip[1])
            self.assertEqual(from_folder[2], ["alpha.set", "beta.set"])
            self.assertEqual(from_zip[2], ["alpha.set", "beta.set"])

    def test_a_file_that_is_not_a_zip_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resumen.txt"
            path.write_text(SUMMARY, encoding="utf-8")
            with self.assertRaises(portfolio_import.ImportError_) as raised:
                portfolio_import.read_export(path)
            self.assertIn("ZIP", str(raised.exception))

    def test_an_unknown_folder_is_reported_instead_of_crashing(self) -> None:
        with self.assertRaises(portfolio_import.ImportError_):
            portfolio_import.read_export(Path(__file__).parent / "no-existe-esta-carpeta")

    def test_a_folder_without_a_summary_says_what_it_expected(self) -> None:
        with self.assertRaises(portfolio_import.ImportError_) as raised:
            portfolio_import.read_export(Path(__file__).parent)
        self.assertIn("resumen", str(raised.exception))


def period(symbol: str, name: str, start: int, end: int, *, net: float = 100.0) -> PeriodReport:
    return PeriodReport(
        period_name=name, start_year=start, end_year=end, symbol=symbol, timeframe="H1",
        pnl_curve_001=[0.0, net], net_profit_001=net, valley_dd_001=10.0, point_dd_001=4.0,
        profit_factor=2.0, return_dd_ratio=net / 10.0, trades=120,
        balance_dd_metric_001=6.0, equity_dd_metric_001=8.0,
    )


def strategy(set_path: str, symbol: str, candidate: int, net: float) -> RobustStrategySet:
    return build_robust_strategy_set(
        set_id=set_path, candidate_id=f"ICTRADING/STANDARD:{candidate}", symbol=symbol,
        timeframe="H1", strategy_family="", robustness_status="accepted", already_used=False,
        report_2020_2024=period(symbol, "2020_2024", 2020, 2024, net=net),
        report_2025_2026=period(symbol, "2025_2026", 2025, 2026, net=net / 2),
        set_path=set_path, is_report_path=f"{set_path}.is.html", oos_report_path=f"{set_path}.oos.html",
    )


class ImportRoundTripTests(unittest.TestCase):
    """La fila importada tiene que ser la de un guardado normal.

    Lo único que aporta el resumen es la composición. Todo lo demás se recalcula
    con `evaluate_portfolio` desde los informes del candidato, así que aquí se
    inyectan estrategias ya construidas —el parseo del HTML de MT5 tiene sus
    propias pruebas— y se ejecuta de verdad el resto del camino, incluido
    `save_proposal`.
    """

    def _source(self, project: Path):
        (project / "outputs").mkdir(parents=True, exist_ok=True)
        (project / "assets").mkdir(exist_ok=True)
        (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
        return PortfolioSource({
            "portfolio_project_dir": str(project),
            "portfolio_broker": "ICTRADING",
            "portfolio_account_type": "STANDARD",
        })

    def _candidates(self, project: Path) -> list[dict]:
        return [
            {
                "set_path": str(project / name), "source_memory_path": str(project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"),
                "account_type": "ICTRADING/STANDARD", "source_candidate_id": index,
                "target_symbol": symbol, "symbol": symbol, "period": "H1",
                "is_report_path": "", "oos_report_path": "",
            }
            for index, (name, symbol) in enumerate((("alpha.set", "EURUSD"), ("beta.set", "GBPUSD")), start=1)
        ]

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

    def test_an_exported_bundle_comes_back_as_a_normal_saved_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            candidates = self._candidates(project)
            strategies = [
                strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0),
                strategy(str(project / "beta.set"), "GBPUSD", 2, 600.0),
            ]
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(PortfolioSource, "import_candidate_rows", return_value=candidates), patch(
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, selected_key, report = build_import_proposals(
                    source, "full_history", header, members
                )
                portfolio_id = save_proposal(source, proposals, selected_key, "full_history")

            # Las tres variantes del resumen, con sus unidades propias.
            self.assertEqual(report["variants"], ["aggressive", "balanced", "conservative"])
            self.assertEqual(report["unresolved"], [])
            self.assertEqual(selected_key, "balanced")
            with source.connect() as conn:
                row = conn.execute("select portfolio_type,capital,metrics_json from portfolios where id=?", (portfolio_id,)).fetchone()
                variants = conn.execute(
                    "select variant_key,set_path,units from portfolio_allocations where portfolio_id=? order by variant_key,set_path",
                    (portfolio_id,),
                ).fetchall()
                members_saved = conn.execute(
                    "select count(*) from portfolio_members where portfolio_id=?", (portfolio_id,)
                ).fetchone()[0]
            self.assertEqual(row["portfolio_type"], "bundle")
            self.assertEqual(row["capital"], 10000.0)
            self.assertTrue(json.loads(row["metrics_json"])["portfolio_bundle"])
            self.assertEqual(
                [(item["variant_key"], Path(item["set_path"]).name, item["units"]) for item in variants],
                [
                    ("aggressive", "alpha.set", 3), ("aggressive", "beta.set", 2),
                    ("balanced", "alpha.set", 2), ("balanced", "beta.set", 1),
                    ("conservative", "alpha.set", 1), ("conservative", "beta.set", 1),
                ],
            )
            self.assertEqual(members_saved, len(variants))
            # Y lo que motivaba todo esto: sus sets vuelven a estar comprometidos.
            used = {Path(path).name for path in source.used_set_paths("full_history")}
            self.assertEqual(used, {"alpha.set", "beta.set"})

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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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

    def test_a_set_that_is_no_longer_a_candidate_is_named_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            only_alpha = [self._candidates(project)[0]]
            strategies = [strategy(str(project / "alpha.set"), "EURUSD", 1, 900.0)]
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(PortfolioSource, "import_candidate_rows", return_value=only_alpha), patch(
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            self.assertEqual(report["unresolved"], ["beta.set"])
            self.assertEqual(report["strategies"], 1)
            self.assertTrue(all(
                {allocation.set_id for allocation in proposal["result"].allocations}
                == {str(project / "alpha.set")}
                for proposal in proposals
            ))

    def test_nothing_reconstructible_is_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            header, members = portfolio_import.parse_summary(SUMMARY)

            with patch.object(PortfolioSource, "import_candidate_rows", return_value=[]):
                with self.assertRaises(ValueError) as raised:
                    build_import_proposals(source, "full_history", header, members)

            self.assertIn("candidato", str(raised.exception))

    def test_a_changed_current_verdict_warns_but_does_not_remove_the_exported_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = self._source(project)
            candidates = self._candidates(project)
            candidates[0].update({
                "base_status": "accepted", "robustness_status": "rejected",
                "final_tick_status": "", "final_tick_6m_status": "",
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
                return_value=(strategies, []),
            ):
                proposals, _selected, report = build_import_proposals(
                    source, "full_history", header, members
                )

            self.assertEqual(report["strategies"], 2)
            self.assertTrue(all(len(proposal["result"].allocations) == 2 for proposal in proposals))
            self.assertTrue(any("exactamente desde el ZIP" in warning for warning in report["warnings"]))
            self.assertTrue(any("robustez=rejected" in warning for warning in report["warnings"]))
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
