"""Borrar un portafolio manual tiene que llevarse también sus ficheros.

Los `.set` que sube el usuario viven en
`<runtime>/<nodo>/manual_portfolios/<uuid>/sets/`, fuera de SQLite. Mientras el
borrado sólo quitaba filas, esa carpeta quedaba huérfana y nadie volvía a
nombrarla. Aquí se fija lo contrario, y también los tres casos en los que no se
debe tocar nada: la carpeta ya no está, el `set_path` no es de un portafolio
manual, u otro portafolio sigue apuntando a la misma carpeta.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from mt5_manager.portfolio_manual_files import (
    discard_manual_portfolio_folders,
    manual_portfolio_folder,
    manual_portfolio_folders_only_for,
)
from mt5_manager.portfolio_service import PortfolioSource


def _node(project: Path) -> dict[str, str]:
    (project / "outputs").mkdir(exist_ok=True)
    (project / "assets").mkdir(exist_ok=True)
    (project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite").touch()
    return {
        "id": "ic", "portfolio_project_dir": str(project),
        "portfolio_broker": "ICTRADING", "portfolio_account_type": "STANDARD",
    }


def _manual_sets(project: Path, uuid: str, *names: str) -> list[Path]:
    folder = project / "runtime" / "ic-standard" / "manual_portfolios" / uuid / "sets"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_text("StartLots=0.01\n", encoding="utf-8")
        paths.append(path)
    return paths


def _insert(conn: sqlite3.Connection, set_paths: list[Path]) -> int:
    portfolio_id = int(conn.execute(
        """insert into portfolios(created_at,name,type,portfolio_type,portfolio_scope,capital,
           account_capital,target_valley_dd,target_point_dd,total_net_profit,total_lot,
           total_units,active_strategies,target_strategies,metrics_json)
           values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-10-18", "Manual", "balanced", "balanced", "full_history", 0, 0, 0, 0, 0,
         .01, len(set_paths), len(set_paths), len(set_paths),
         '{"inputs":{"manual_portfolio":true}}'),
    ).lastrowid)
    for path in set_paths:
        conn.execute(
            """insert into portfolio_allocations(portfolio_id,set_id,candidate_id,symbol,units,
               lot,net_profit_contribution,standalone_valley_dd,standalone_point_dd,set_path,
               timeframe,lot_size_step) values(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (portfolio_id, path.name, f"manual:{path.stem}", "EURUSD", 1, .01, 0, 0, 0,
             str(path), "H1", .01),
        )
    conn.commit()
    return portfolio_id


class ManualPortfolioFolderRuleTests(unittest.TestCase):
    def test_only_a_set_under_manual_portfolios_names_a_folder(self) -> None:
        root = Path(r"C:\agent\runtime").absolute()
        manual = root / "ic" / "manual_portfolios" / "abc123" / "sets" / "Scalp.set"
        self.assertEqual(manual.parent.parent, manual_portfolio_folder(root, manual))
        # El `set_path` de un portafolio UBS es la ruta del candidato, no una
        # carpeta nuestra: ni el abuelo se llama `manual_portfolios` ni cae bajo
        # la raiz de runtime.
        for outsider in (
            root / "ic" / "manual_portfolios" / "abc123" / "Scalp.set",
            root / "ic" / "otra_cosa" / "abc123" / "sets" / "Scalp.set",
            Path(r"C:\agent\data\ICTRADING\sets\Scalp.set"),
            Path("relativa/manual_portfolios/abc123/sets/Scalp.set"),
            "",
            None,
        ):
            self.assertIsNone(manual_portfolio_folder(root, outsider), msg=str(outsider))

    def test_windows_backslashes_and_repeated_sets_name_one_folder(self) -> None:
        root = Path(r"C:\agent\runtime").absolute()
        stored = [
            r"C:\agent\runtime\ic\manual_portfolios\abc123\sets\A.set",
            r"C:\agent\runtime\ic\manual_portfolios\abc123\sets\B.set",
        ]
        folders = {manual_portfolio_folder(root, value) for value in stored}
        self.assertEqual({root / "ic" / "manual_portfolios" / "abc123"}, folders)


class DeleteManualPortfolioFilesTests(unittest.TestCase):
    def test_deleting_the_portfolio_also_removes_its_sets_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = PortfolioSource(_node(project))
            paths = _manual_sets(project, "abc123", "Scalp.set", "Swing.set")
            folder = paths[0].parent.parent
            with source.connect(write=True) as conn:
                portfolio_id = _insert(conn, paths)
            source.delete_portfolio(portfolio_id, "full_history")
            self.assertFalse(folder.exists(), "la carpeta del portafolio sigue en disco")
            self.assertTrue(folder.parent.is_dir(), "no se toca `manual_portfolios`")
            self.assertEqual(0, source.saved_portfolios("full_history")["summary"]["total"])

    def test_a_folder_that_is_already_gone_does_not_break_the_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = PortfolioSource(_node(project))
            paths = _manual_sets(project, "abc123", "Scalp.set")
            folder = paths[0].parent.parent
            with source.connect(write=True) as conn:
                portfolio_id = _insert(conn, paths)
            paths[0].unlink()
            paths[0].parent.rmdir()
            folder.rmdir()
            source.delete_portfolio(portfolio_id, "full_history")
            self.assertEqual(0, source.saved_portfolios("full_history")["summary"]["total"])
            self.assertEqual([], discard_manual_portfolio_folders([folder]))

    def test_a_set_outside_manual_portfolios_keeps_its_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = PortfolioSource(_node(project))
            # Un portafolio UBS guardado: su `set_path` es el candidato del pool.
            pool = project / "data" / "ICTRADING" / "sets"
            pool.mkdir(parents=True)
            set_file = pool / "Candidato.set"
            set_file.write_text("Risk=1\n", encoding="utf-8")
            with source.connect(write=True) as conn:
                portfolio_id = _insert(conn, [set_file])
                self.assertEqual(
                    [], manual_portfolio_folders_only_for(
                        conn, project / "runtime", portfolio_id
                    ),
                )
            source.delete_portfolio(portfolio_id, "full_history")
            self.assertTrue(set_file.is_file(), "se borró el set de un portafolio UBS")
            self.assertTrue(pool.is_dir())

    def test_a_folder_that_another_portfolio_still_uses_survives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            source = PortfolioSource(_node(project))
            paths = _manual_sets(project, "abc123", "Scalp.set")
            folder = paths[0].parent.parent
            with source.connect(write=True) as conn:
                first = _insert(conn, paths)
                _insert(conn, paths)
            source.delete_portfolio(first, "full_history")
            self.assertTrue(folder.is_dir(), "la carpeta la sigue usando otro portafolio")
            self.assertTrue(paths[0].is_file())


if __name__ == "__main__":
    unittest.main()
