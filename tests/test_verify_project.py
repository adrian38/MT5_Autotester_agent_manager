from __future__ import annotations

import unittest
from pathlib import Path

from tools.verify_project import command_specs, parse_args

ROOT = Path(__file__).resolve().parents[1]


class VerifyProjectTests(unittest.TestCase):
    def test_default_plan_contains_every_required_layer(self) -> None:
        names = [spec.name for spec in command_specs()]
        self.assertEqual(
            names,
            ["exports UBS", "nombres", "funciones", "ficheros", "contexto", "guardas",
             "suite manager", "contrato IC"],
        )

    def test_quick_plan_only_omits_the_repeated_full_suite(self) -> None:
        names = [spec.name for spec in command_specs(include_full_suite=parse_args(["--quick"]))]
        self.assertNotIn("suite manager", names)
        self.assertIn("guardas", names)
        self.assertIn("contrato IC", names)

    def test_unknown_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_args(["--silenciar-fallos"])

    def test_agents_contract_documents_the_enforced_workflow(self) -> None:
        rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for required in (
            "### Matriz de escritura por rama",
            "python -m tools.verify_project",
            "## Definición de terminado y entrega",
            "ALLOWED_STAR_IMPORTS",
        ):
            self.assertIn(required, rules)


if __name__ == "__main__":
    unittest.main()
