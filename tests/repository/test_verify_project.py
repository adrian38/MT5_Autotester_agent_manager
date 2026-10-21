from __future__ import annotations

import unittest
from pathlib import Path

from tools.verify_project import command_specs, parse_args

ROOT = Path(__file__).resolve().parents[2]


class VerifyProjectTests(unittest.TestCase):
    def test_default_plan_does_not_run_the_full_suite(self) -> None:
        names = [spec.name for spec in command_specs()]
        self.assertEqual(
            names,
            ["exports UBS", "nombres", "funciones", "ficheros", "contexto", "guardas",
             "contrato IC"],
        )

    def test_full_suite_requires_the_explicit_flag(self) -> None:
        names = [spec.name for spec in command_specs(include_full_suite=parse_args(["--full"]))]
        self.assertIn("suite manager", names)
        self.assertIn("guardas", names)
        self.assertIn("contrato IC", names)

    def test_quick_remains_an_alias_for_the_default_plan(self) -> None:
        self.assertFalse(parse_args([]))
        self.assertFalse(parse_args(["--quick"]))

    def test_unknown_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_args(["--silenciar-fallos"])

    def test_agents_contract_documents_the_enforced_workflow(self) -> None:
        """Cada cadena exigida tiene detrás algo que falla solo.

        Exigía además `### Matriz de escritura por rama`, la tabla de qué
        checkout era escribible según la rama del manager. Esa tabla describía
        un equipo que ya no existe —el manager repartido entre dos PC, `dev` y
        `main`— y el usuario la retiró el 2026-10-16 al autorizar todos los
        checkouts montados. Detrás no quedaba ninguna guarda: la única
        referencia a la cadena en todo el repositorio era esta línea, así que
        pedía documentación de un flujo que nadie aplica. En su lugar se exige
        la paridad con el fork, que es la trampa de la que este repositorio
        avisa más veces y sí falla sola.
        """
        rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for required in (
            "tests/test_node_runtime_fork_parity.py",
            "python -m tools.verify_project",
            "python -m tools.verify_project --full",
            "sólo cuando el usuario lo pida explícitamente",
            "tests/repository/",
            "## Definición de terminado y entrega",
            "ALLOWED_STAR_IMPORTS",
        ):
            self.assertIn(required, rules)


if __name__ == "__main__":
    unittest.main()
