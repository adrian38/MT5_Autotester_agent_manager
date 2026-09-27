"""Trinquete de capas de las dos pilas grandes de `mt5_manager`.

`portfolio_service.py` eran 6.045 lineas y `manager.py` 1.585. Las dos son
ahora varios modulos y **el orden es el de dependencia**: cada uno solo importa
de los anteriores. Sin esta guarda, el primer import hacia arriba las vuelve a
convertir en un fichero unico con los nombres repartidos.

Dos imports no cuentan como dependencia hacia arriba:

- Los de `if TYPE_CHECKING:`, que no existen en ejecucion y son como los
  modulos de abajo declaran el tipo `PortfolioSource`.
- El de un modulo a si mismo: `manager_http` lo hace a proposito para que un
  doble puesto en `manager_http.node_request` alcance tambien a sus propias
  funciones.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PACKAGE = Path(__file__).resolve().parents[1] / "mt5_manager"

PORTFOLIO_STACK = [
    "portfolio_scope",
    "portfolio_schema",
    "portfolio_report_cache",
    "portfolio_identity",
    "portfolio_settings",
    "portfolio_transfer",
    "portfolio_persistence",
    "portfolio_valley_floor",
    "portfolio_antifiller",
    "portfolio_generation_search",
    "portfolio_generation",
    "portfolio_completion",
    "portfolio_proposals",
    "portfolio_saved",
    "portfolio_source_connection",
    "portfolio_source_inventory",
    "portfolio_source_quarantine",
    "portfolio_source_saved",
    "portfolio_source_reports",
    "portfolio_source",
    "portfolio_coordinator_core",
    "portfolio_coordinator_saved",
    "portfolio_import_match",
    "portfolio_import_build",
    "portfolio_service",
]

MANAGER_STACK = [
    "manager_config",
    "manager_http",
    "manager_pulse",
    "manager_live_audit_routes",
    "manager_portfolio_routes",
    "manager_handler",
    "manager_server",
    "manager",
]

NODE_STACK = [
    "node_statuses",
    "node_settings",
    "node_snapshots",
    "node_commands",
    "node_job_runtime",
    "node_job_starts",
    "node_job_queue",
    "node_portfolio_api",
    "node_jobs",
    "node_http",
    "node",
]

STACKS = {"portfolio": PORTFOLIO_STACK, "manager": MANAGER_STACK, "node": NODE_STACK}


def _runtime_imports(module: str, stack: set[str]) -> set[str]:
    """Modulos de la pila que `module` importa de verdad, no solo para el tipo."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    type_checking_only: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test).endswith("TYPE_CHECKING"):
            type_checking_only.update(id(child) for child in ast.walk(node))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if id(node) in type_checking_only:
            continue
        if node.module in stack:
            found.add(node.module)
        elif node.module is None:  # from . import x, y
            found.update(alias.name for alias in node.names if alias.name in stack)
    return found - {module}


class ModuleLayeringTest(unittest.TestCase):
    def test_every_module_of_each_stack_exists(self) -> None:
        missing = {
            name: [module for module in stack if not (PACKAGE / f"{module}.py").is_file()]
            for name, stack in STACKS.items()
        }
        offenders = {name: gone for name, gone in missing.items() if gone}
        self.assertFalse(
            offenders,
            "La lista nombra modulos que no existen. Si se renombro uno, actualiza la "
            f"pila y la seccion de AGENTS.md: {offenders}",
        )

    def test_no_module_imports_from_a_later_one(self) -> None:
        offenders: dict[str, list[str]] = {}
        for stack in STACKS.values():
            names = set(stack)
            for module in stack:
                upward = sorted(
                    dependency for dependency in _runtime_imports(module, names)
                    if stack.index(dependency) >= stack.index(module)
                )
                if upward:
                    offenders[module] = upward
        self.assertFalse(
            offenders,
            "Import hacia arriba. La definicion compartida baja de nivel; no se "
            "invierte la dependencia:\n"
            + "\n".join(f"  {module} -> {deps}" for module, deps in sorted(offenders.items())),
        )

    def test_the_facade_still_re_exports_what_it_moved_out(self) -> None:
        """Los llamantes de fuera siguen importando del modulo de siempre."""
        expected = {
            "portfolio_service.py": {
                "ensure_portfolio_schema", "cached_report", "normalize_settings",
                "normalize_portfolio_alias", "save_proposal", "build_import_proposals",
                "result_payload", "settings_inputs", "TYPE_LABELS", "PORTFOLIO_TYPES",
                "_optimize_without_recent_fillers", "_with_executable_valley_floor",
            },
            "manager.py": {
                "ManagerServer", "ManagerHandler", "PULSE_JOB_KEYS", "live_log_progress",
                "node_request", "choose_directory", "STATIC_FILES", "NODE_ACTION_TARGETS",
            },
            "node.py": {
                "JobController", "NodeServer", "NodeHandler", "main",
                "build_generation_command", "build_pipeline_stage_command",
                "pipeline_stage_pending_count", "database_snapshot", "memory_path",
                "read_settings", "setting", "setting_bool", "CLEANUP_STAGES",
            },
        }
        for filename, names in expected.items():
            tree = ast.parse((PACKAGE / filename).read_text(encoding="utf-8"))
            exported = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
            }
            self.assertEqual(
                set(), names - exported,
                f"{filename} dejo de reexportar nombres que sus llamantes importan de "
                f"el: {sorted(names - exported)}",
            )


if __name__ == "__main__":
    unittest.main()
