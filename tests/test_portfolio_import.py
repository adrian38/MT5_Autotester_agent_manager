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


SUMMARY = """Portafolio: A/M/C | Base Moderado | 2 sets | 09.08.2026 13:01
Alias: Londres estable
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

IMPROVEMENT_SUMMARY = """Portafolio: Mejora del portafolio #73 | modo Moderado
Tipo: balanced   Capital: 10,000
Mejora origen: 73
Mejora modo: balanced
Mejora prioridad: stress
Mejora incorporaciones: 1
DD valle objetivo: 300.00
DD puntual objetivo: 300.00

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
Moderado     ICTRADING    EURUSD       H1          2    0.02   alpha.set
Moderado     ICTRADING    GBPUSD       H1          1    0.01   beta.set
"""

CHAINED_IMPROVEMENT_SUMMARY = """Portafolio: Mejora del portafolio #36 | modo Moderado
Tipo: balanced   Capital: 10,000
Portafolio UID: 33333333-3333-4333-8333-333333333333
Mejora etiqueta: Mejora del portafolio #36 | modo Moderado
Mejora origen: 36
Mejora modo: balanced
Mejora origen UID: 22222222-2222-4222-8222-222222222222
Mejora raiz: 14
Mejora raiz UID: 11111111-1111-4111-8111-111111111111
Mejora nivel: 2
Mejora linaje JSON: [{"portfolio_id":14,"portfolio_uid":"11111111-1111-4111-8111-111111111111","label":"Portafolio #14","mode":"balanced"},{"portfolio_id":36,"portfolio_uid":"22222222-2222-4222-8222-222222222222","label":"Mejora del portafolio #14 | modo Moderado","mode":"balanced"}]
Mejora snapshot JSON: {"id":36,"portfolio_uid":"22222222-2222-4222-8222-222222222222","portfolio_type":"balanced","label":"Mejora del portafolio #14 | modo Moderado","total_net_profit":100,"actual_valley_dd":12,"active_strategies":1,"total_units":2,"total_lot":0.02,"members":[]}
Mejora prioridad: balanced
Mejora incorporaciones: 1
DD valle objetivo: 300.00
DD puntual objetivo: 300.00

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
Moderado     ICTRADING    EURUSD       H1          2    0.02   alpha.set
Moderado     ICTRADING    GBPUSD       H1          1    0.01   beta.set
"""


# Lo que exporta de verdad una mejora: `save_proposal` guarda sus miembros con
# `variant_key` vacio —la variante es la fila entera, no una de tres— y la
# columna PERFIL sale en blanco. Tomado de PORTAFOLIO_120/121 de RoboForex, que
# no se podian importar. El modo solo esta en la cabecera.
BLANK_PROFILE_IMPROVEMENT_SUMMARY = """Portafolio: Mejora del portafolio #104 | modo Agresivo
Tipo: aggressive   Capital: 10,000
Portafolio UID: 44444444-4444-4444-8444-444444444444
Mejora etiqueta: Mejora del portafolio #104 | modo Agresivo
Mejora origen: 104
Mejora modo: aggressive
Mejora origen UID: 11111111-1111-4111-8111-111111111111
Mejora raiz: 104
Mejora nivel: 1
Mejora prioridad: balanced
Mejora incorporaciones: 1
DD valle objetivo: 300.00
DD puntual objetivo: 300.00

PERFIL       CUENTA       SIMBOLO      TF      UNID.    LOTE   SET
             ICTRADING    EURUSD       H1          3    0.03   alpha.set
             ICTRADING    GBPUSD       H1          2    0.02   beta.set
"""


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
                "candidate_id": f"ICTRADING/STANDARD:{index}",
                "set_path": str(project / name), "source_memory_path": str(project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"),
                "account_type": "ICTRADING/STANDARD", "source_candidate_id": index,
                "target_symbol": symbol, "symbol": symbol, "period": "H1",
                "is_report_path": "", "oos_report_path": "",
            }
            for index, (name, symbol) in enumerate((("alpha.set", "EURUSD"), ("beta.set", "GBPUSD")), start=1)
        ]

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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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
            (project / "alpha.set").write_text("Risk=1\n", encoding="utf-8")
            (project / "beta.set").write_text("Risk=1\n", encoding="utf-8")
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
                self.assertTrue(all(
                    proposal["inputs"]["portfolio_alias"] == "Londres estable"
                    for proposal in proposals
                ))
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
                source.saved_portfolio_detail(portfolio_id, "full_history")["portfolio"]["alias"],
                "Londres estable",
            )
            exported = source.export_portfolio(
                portfolio_id, "full_history", str(project / "exported")
            )
            exported_header, _exported_members, _set_files = portfolio_import.read_export(
                exported["folder"]
            )
            self.assertEqual(exported_header["portfolio_alias"], "Londres estable")
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows", return_value=(strategies, [])
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows", return_value=(strategies, [])
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows", return_value=(strategies, [])
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows", return_value=(strategies, [])
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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

    def test_a_set_without_any_reports_is_kept_and_marks_the_calculation_incomplete(self) -> None:
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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
                "mt5_manager.portfolio_service.load_robust_sets_from_rows",
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
