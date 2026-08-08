"""El hook que avisa de la copia bifurcada del nodo debe acertar a quién avisa.

Un hook que no salta es invisible, y uno que salta siempre se ignora por ruido.
Las rutas llegan con separadores de Windows desde Claude Code, así que se prueban
ambos separadores.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).parents[1]
sys.path.insert(0, str(REPO))

from tools.hook_node_fork_warning import AGENT_RULES, MANAGER_RULES  # noqa: E402

HOOK = REPO / "tools" / "hook_node_fork_warning.py"


class HookRoutingTests(unittest.TestCase):
    def test_manager_rule_files_are_flagged_with_both_separators(self) -> None:
        for path in (
            r"C:\Users\x\mt5_manager\portfolio_service.py",
            r"C:\Users\x\mt5_manager\node.py",
            "/srv/app/mt5_manager/portfolio_service.py",
            "/srv/app/mt5_manager/node.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(MANAGER_RULES.search(path))
                self.assertFalse(AGENT_RULES.search(path))

    def test_the_agent_fork_is_flagged_from_the_other_side(self) -> None:
        for path in (
            r"C:\a\MT5_Autotester_agent\manager_node_runtime\portfolio_save.py",
            "/a/MT5_Autotester_agent/manager_node_runtime/node.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(AGENT_RULES.search(path))
                self.assertFalse(MANAGER_RULES.search(path))

    def test_unrelated_files_stay_silent(self) -> None:
        # Si avisara de todo, el aviso se volvería ruido y dejaría de leerse.
        for path in (
            "mt5_manager/static/portfolios_monthly.js",
            "mt5_manager/manager.py",
            "mt5_manager/portfolio_monthly_service.py",
            "tests/test_portfolio_service.py",
            "mt5_manager/portfolio_service.pyc",
        ):
            with self.subTest(path=path):
                self.assertFalse(MANAGER_RULES.search(path))
                self.assertFalse(AGENT_RULES.search(path))


class HookProcessTests(unittest.TestCase):
    def _run(self, payload: object) -> str:
        completed = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload) if not isinstance(payload, str) else payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def test_editing_a_manager_rule_emits_context_for_the_model(self) -> None:
        output = self._run({
            "tool_name": "Edit",
            "tool_input": {"file_path": r"C:\x\mt5_manager\portfolio_service.py"},
        })
        emitted = json.loads(output)
        self.assertIn("manager_node_runtime", emitted["systemMessage"])
        self.assertEqual(
            emitted["hookSpecificOutput"]["hookEventName"], "PostToolUse"
        )
        self.assertIn(
            "test_node_runtime_fork_parity",
            emitted["hookSpecificOutput"]["additionalContext"],
        )

    def test_the_path_can_arrive_in_the_tool_response(self) -> None:
        output = self._run({
            "tool_name": "Write",
            "tool_input": {},
            "tool_response": {"filePath": "/x/mt5_manager/node.py"},
        })
        self.assertIn("manager_node_runtime", json.loads(output)["systemMessage"])

    def test_malformed_input_never_breaks_the_turn(self) -> None:
        # Prioridad: no romper el turno del usuario por un aviso.
        self.assertEqual(self._run("no es json"), "")
        self.assertEqual(self._run(""), "")
        self.assertEqual(self._run([1, 2, 3]), "")


if __name__ == "__main__":
    unittest.main()
