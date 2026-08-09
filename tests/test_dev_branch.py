from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mt5_manager import dev_branch


def _repo(root: Path, branch: str | None, *, worktree: bool = False) -> Path:
    """Crea un repositorio ficticio en ``root`` posicionado en ``branch``.

    ``branch`` a ``None`` simula HEAD desprendido.
    """
    root.mkdir(parents=True, exist_ok=True)
    head = "a" * 40 if branch is None else f"ref: refs/heads/{branch}"
    if worktree:
        real = root / "real_git" / "worktrees" / "wt"
        real.mkdir(parents=True)
        (real / "HEAD").write_text(head + "\n", encoding="utf-8")
        (root / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    else:
        git = root / ".git"
        git.mkdir()
        (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    package = root / "mt5_manager"
    package.mkdir(exist_ok=True)
    return package


def _manager_config() -> dict[str, Any]:
    return {
        "host": "127.0.0.1",
        "port": 8750,
        "nodes": [
            {
                "id": "ictrading-standard-test",
                "portfolio_project_dir": r"I:\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent",
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
                "portfolio_memory_path": r"I:\TRADING\otra_pc\outputs\memory.sqlite",
                "portfolio_memory_paths": [{"account_type": "STANDARD", "path": r"I:\otra_pc.sqlite"}],
                "url": "http://127.0.0.1:8761",
                "token": "secret",
            },
            {
                "id": "axi-standard-192-168-1-152",
                "portfolio_project_dir": r"Y:\TRADING\MT5_Autotester_agent_AXI",
                "portfolio_broker": "AXI",
                "portfolio_account_type": "STANDARD",
                "url": "http://192.168.1.152:8762",
                "token": "remote",
            },
            {
                "id": "roboforex-ecn-192-168-1-152",
                "portfolio_project_dir": r"X:\TRADING\MT5_Autotester_agent",
                "portfolio_broker": "ROBOFOREX",
                "portfolio_account_type": "ECN",
                "url": "http://192.168.1.152:8761",
                "token": "remote",
            },
        ],
    }


class DevBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        for key in (dev_branch.OVERRIDE_ENV, dev_branch.PROJECT_DIR_ENV):
            self.addCleanup(self._restore_env, key, os.environ.get(key))
            os.environ.pop(key, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    @staticmethod
    def _restore_env(key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    @staticmethod
    def _quiet(function, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(*args, **kwargs)

    def test_branch_is_read_from_head_including_worktrees(self) -> None:
        plain = _repo(self.root / "plain", "dev")
        detached = _repo(self.root / "detached", None)
        linked = _repo(self.root / "linked", "main", worktree=True)

        self.assertEqual(dev_branch.current_branch(plain), "dev")
        self.assertIsNone(dev_branch.current_branch(detached))
        self.assertEqual(dev_branch.current_branch(linked), "main")

    def test_without_repository_the_override_stays_off(self) -> None:
        loose = self.root / "sin_git" / "mt5_manager"
        loose.mkdir(parents=True)

        self.assertIsNone(dev_branch.current_branch(loose))
        self.assertFalse(dev_branch.is_active(loose))

    def test_dev_corrects_ictrading_and_keeps_the_other_cards(self) -> None:
        package = _repo(self.root / "dev", "dev")
        source = _manager_config()

        result = self._quiet(dev_branch.apply_manager_config, source, package)

        self.assertEqual(
            [node["id"] for node in result["nodes"]],
            ["ictrading-standard-test", "axi-standard-192-168-1-152", "roboforex-ecn-192-168-1-152"],
            "Las tarjetas de AXI y RoboForex tienen que seguir en el panel",
        )
        self.assertEqual(result["nodes"][0]["portfolio_project_dir"], dev_branch.DEV_PROJECT_DIR)
        self.assertNotIn("portfolio_memory_path", result["nodes"][0])
        self.assertNotIn("portfolio_memory_paths", result["nodes"][0])
        self.assertEqual(result["nodes"][0]["token"], "secret")
        self.assertEqual(result["port"], 8750)
        self.assertEqual(result["nodes"][1]["portfolio_project_dir"], r"Y:\TRADING\MT5_Autotester_agent_AXI")
        self.assertEqual(result["nodes"][2]["portfolio_project_dir"], r"X:\TRADING\MT5_Autotester_agent")
        self.assertEqual(
            source["nodes"][0]["portfolio_project_dir"],
            r"I:\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent",
            "La configuración original no debe mutar",
        )

    def test_main_never_touches_the_production_paths(self) -> None:
        package = _repo(self.root / "main", "main")
        source = _manager_config()

        result = self._quiet(dev_branch.apply_manager_config, source, package)

        self.assertIs(result, source)
        self.assertEqual(
            [node["portfolio_project_dir"] for node in result["nodes"]],
            [
                r"I:\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent",
                r"Y:\TRADING\MT5_Autotester_agent_AXI",
                r"X:\TRADING\MT5_Autotester_agent",
            ],
        )

    def test_env_switch_forces_and_disables_the_override(self) -> None:
        dev_package = _repo(self.root / "dev", "dev")
        main_package = _repo(self.root / "main", "main")

        os.environ[dev_branch.OVERRIDE_ENV] = "0"
        self.assertFalse(dev_branch.is_active(dev_package))
        source = _manager_config()
        self.assertIs(self._quiet(dev_branch.apply_manager_config, source, dev_package), source)

        os.environ[dev_branch.OVERRIDE_ENV] = "1"
        self.assertTrue(dev_branch.is_active(main_package))
        forced = self._quiet(dev_branch.apply_manager_config, _manager_config(), main_package)
        self.assertEqual(forced["nodes"][0]["portfolio_project_dir"], dev_branch.DEV_PROJECT_DIR)

    def test_project_dir_can_be_redirected_by_environment(self) -> None:
        package = _repo(self.root / "dev", "dev")
        os.environ[dev_branch.PROJECT_DIR_ENV] = r"D:\pruebas\agente"

        result = self._quiet(dev_branch.apply_manager_config, _manager_config(), package)

        self.assertEqual(result["nodes"][0]["portfolio_project_dir"], r"D:\pruebas\agente")

    def test_dev_without_ictrading_node_leaves_the_config_untouched(self) -> None:
        package = _repo(self.root / "dev", "dev")
        source = {"nodes": [{"id": "axi", "portfolio_broker": "AXI"}]}

        result = self._quiet(dev_branch.apply_manager_config, source, package)

        self.assertIs(result, source)

    def test_dev_only_writes_inside_the_ictrading_agent(self) -> None:
        os.environ[dev_branch.OVERRIDE_ENV] = "1"
        allowed = Path(dev_branch.DEV_PROJECT_DIR)

        self.assertEqual(
            dev_branch.assert_writable(allowed / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"),
            allowed / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite",
        )
        for forbidden in (
            r"Y:\TRADING\MT5_Autotester_agent_AXI\outputs\ubs_memory_AXI_STANDARD.sqlite",
            r"X:\TRADING\MT5_Autotester_agent\outputs\ubs_memory_ROBOFOREX_ECN.sqlite",
            r"\\192.168.1.152\TRADING\outputs\memory.sqlite",
            r"C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent\outputs\memory.sqlite",
        ):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError) as caught:
                    dev_branch.assert_writable(forbidden, "memoria UBS")
                self.assertIn(dev_branch.DEV_PROJECT_DIR, str(caught.exception))
                self.assertIn("memoria UBS", str(caught.exception))

    def test_the_export_folder_chosen_by_the_user_is_not_an_agent_write(self) -> None:
        # Exportar copia `.set` a donde diga el usuario. Tratar eso como una
        # escritura de agente dejaba la exportación inservible en `dev`: elegir
        # el Escritorio devolvía «la rama dev solo puede escribir en …».
        os.environ[dev_branch.OVERRIDE_ENV] = "1"
        allowed = Path(dev_branch.DEV_PROJECT_DIR)
        foreign = Path(r"Y:\TRADING\MT5_Autotester_agent_AXI")

        for destination in (
            r"C:\Users\Adrian\Desktop\PORTAFOLIO_8_A_M_C_20260809",
            r"E:\pendrive\portafolios",
        ):
            with self.subTest(destination=destination):
                self.assertEqual(
                    dev_branch.assert_export_destination(destination, allowed),
                    Path(destination),
                )
                # Da igual desde qué agente se exporte: el destino sigue siendo
                # una carpeta del usuario, no el árbol de ese agente.
                self.assertEqual(
                    dev_branch.assert_export_destination(destination, foreign),
                    Path(destination),
                )

    def test_exporting_inside_a_foreign_agent_tree_is_still_refused(self) -> None:
        # El destino por defecto es `<proyecto>/exports`: ahí sí manda la regla
        # de siempre, así que un nodo de producción no escribe en su propio árbol.
        os.environ[dev_branch.OVERRIDE_ENV] = "1"
        foreign = Path(r"Y:\TRADING\MT5_Autotester_agent_AXI")

        with self.assertRaises(ValueError) as caught:
            dev_branch.assert_export_destination(foreign / "exports" / "PORTAFOLIO_8", foreign)

        self.assertIn("carpeta de exportación", str(caught.exception))
        allowed = Path(dev_branch.DEV_PROJECT_DIR)
        self.assertEqual(
            dev_branch.assert_export_destination(allowed / "exports" / "PORTAFOLIO_8", allowed),
            allowed / "exports" / "PORTAFOLIO_8",
        )

    def test_outside_dev_the_export_guard_checks_nothing(self) -> None:
        os.environ[dev_branch.OVERRIDE_ENV] = "0"
        foreign = Path(r"Y:\TRADING\MT5_Autotester_agent_AXI")

        self.assertEqual(
            dev_branch.assert_export_destination(foreign / "exports", foreign),
            foreign / "exports",
        )

    def test_manager_own_state_and_temporary_files_stay_writable(self) -> None:
        os.environ[dev_branch.OVERRIDE_ENV] = "1"
        runtime = Path(__file__).resolve().parents[1] / "runtime"

        dev_branch.assert_writable(runtime / "portfolio_settings.json")
        dev_branch.assert_writable(runtime / "grid_portfolios" / "nodo.sqlite")
        dev_branch.assert_writable(self.root / "proyecto" / "outputs" / "memory.sqlite")

    def test_outside_dev_the_guard_lets_everything_through(self) -> None:
        os.environ[dev_branch.OVERRIDE_ENV] = "0"

        target = dev_branch.assert_writable(r"Y:\TRADING\MT5_Autotester_agent_AXI\outputs\memory.sqlite")

        self.assertEqual(target, Path(r"Y:\TRADING\MT5_Autotester_agent_AXI\outputs\memory.sqlite"))

    def test_the_guard_follows_the_redirected_project_dir(self) -> None:
        os.environ[dev_branch.OVERRIDE_ENV] = "1"
        os.environ[dev_branch.PROJECT_DIR_ENV] = str(self.root / "agente")

        dev_branch.assert_writable(self.root / "agente" / "outputs" / "memory.sqlite")
        with self.assertRaises(ValueError):
            dev_branch.assert_writable(Path(dev_branch.DEV_PROJECT_DIR) / "outputs" / "memory.sqlite")

    def test_node_project_dir_follows_the_branch(self) -> None:
        dev_package = _repo(self.root / "dev", "dev")
        main_package = _repo(self.root / "main", "main")
        source = {"node_id": "ictrading-standard-test", "project_dir": r"I:\otra_pc", "token": "secret"}

        on_dev = self._quiet(dev_branch.apply_node_config, source, dev_package)
        on_main = self._quiet(dev_branch.apply_node_config, source, main_package)

        self.assertEqual(on_dev["project_dir"], dev_branch.DEV_PROJECT_DIR)
        self.assertEqual(on_dev["token"], "secret")
        self.assertIs(on_main, source)
        self.assertEqual(source["project_dir"], r"I:\otra_pc", "La configuración original no debe mutar")


if __name__ == "__main__":
    unittest.main()
