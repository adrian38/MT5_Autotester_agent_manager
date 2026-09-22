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


class SummaryParsingTests(unittest.TestCase):
    def test_the_header_and_every_row_are_read_from_the_exported_summary(self) -> None:
        header, members = portfolio_import.parse_summary(SUMMARY)

        self.assertEqual(header["portfolio_type"], "bundle")
        self.assertEqual(header["portfolio_alias"], "Londres estable")
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

    def test_improvement_identity_is_read_from_explicit_headers(self) -> None:
        header, _members = portfolio_import.parse_summary(IMPROVEMENT_SUMMARY)

        self.assertEqual(header["improvement_source_portfolio_id"], 73.0)
        self.assertEqual(header["improvement_portfolio_type"], "balanced")
        self.assertEqual(header["improvement_selection_priority"], "stress")
        self.assertEqual(header["improvement_added_count"], 1.0)

    def test_an_old_improvement_export_recovers_origin_and_mode_from_its_name(self) -> None:
        old = IMPROVEMENT_SUMMARY.replace(
            "Mejora origen: 73\nMejora modo: balanced\nMejora prioridad: stress\nMejora incorporaciones: 1\n",
            "",
        )

        header, _members = portfolio_import.parse_summary(old)

        self.assertEqual(header["improvement_source_portfolio_id"], 73.0)
        self.assertEqual(header["improvement_portfolio_type"], "balanced")
        self.assertNotIn("improvement_selection_priority", header)

    def test_a_chained_improvement_reads_portable_lineage_and_parent_snapshot(self) -> None:
        header, _members = portfolio_import.parse_summary(CHAINED_IMPROVEMENT_SUMMARY)

        self.assertEqual(header["portfolio_uid"], "33333333-3333-4333-8333-333333333333")
        self.assertEqual(header["improvement_root_portfolio_id"], 14.0)
        self.assertEqual(header["improvement_depth"], 2.0)
        self.assertEqual([row["portfolio_id"] for row in header["improvement_lineage"]], [14, 36])
        self.assertEqual(header["improvement_source_snapshot"]["id"], 36)

    def test_exact_exported_members_are_read_without_changing_the_visible_table(self) -> None:
        exact = [{
            "set_name": "alpha.set",
            "candidate_id": "ICTRADING/STANDARD:77",
            "set_path": "/data/ic/alpha.set",
            "oos_report_path": "/data/ic/reports/robust_000077_alpha.htm",
        }]
        summary = SUMMARY.replace(
            "Tipo: bundle   Capital: 10,000\n",
            "Tipo: bundle   Capital: 10,000\nMiembros JSON: "
            + json.dumps(exact, separators=(",", ":"))
            + "\n",
        )

        header, members = portfolio_import.parse_summary(summary)

        self.assertEqual(header["portfolio_members"], exact)
        self.assertEqual(len(members), 6)

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
            self.assertEqual(
                len(from_folder[0]["_set_sha256_by_name"]["alpha.set"]), 1
            )

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


if __name__ == "__main__":
    unittest.main()
