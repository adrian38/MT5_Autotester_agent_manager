from __future__ import annotations

import re
import unittest
from pathlib import Path


MANAGER_ROOT = Path(__file__).parents[1]
MANAGER_RULES = tuple(
    MANAGER_ROOT / "mt5_manager" / name
    for name in (
        "portfolio_service.py",
        "portfolio_proposals.py",
        "portfolio_source_connection.py",
        "portfolio_source_saved.py",
        "portfolio_source_quarantine.py",
        "portfolio_coordinator_saved.py",
    )
)
FORK_CANDIDATES = (
    Path(r"C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent"),
    Path(r"F:\TRADING\MT5_Autotester_agent_AXI"),
    Path(r"I:\TRADING\MT5_Autotester_agent_IC"),
    Path(r"G:\TRADING\MT5_Autotester_agent"),
)


def _reachable_forks() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for project in FORK_CANDIDATES:
        rules = project / "manager_node_runtime" / "portfolio_save.py"
        try:
            if rules.is_file():
                found.append((project, rules.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return found


class NodeRuntimeForkParityBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manager_source = "\n".join(
            path.read_text(encoding="utf-8") for path in MANAGER_RULES
        )
        cls.forks = _reachable_forks()

    def _assert_absent(self, source: str, pattern: str, message: str) -> None:
        if re.search(pattern, source):
            self.fail(message)

    def _assert_present(self, source: str, pattern: str, message: str) -> None:
        if not re.search(pattern, source):
            self.fail(message)

    def _assert_on_every_fork(self, check, description: str) -> None:
        if not self.forks:
            self.skipTest(
                "Ninguna copia de manager_node_runtime/ es alcanzable en este equipo; "
                f"buscadas: {', '.join(str(path) for path in FORK_CANDIDATES)}"
            )
        for project, source in self.forks:
            with self.subTest(fork=str(project)):
                check(project, source)
        missing = [
            str(path) for path in FORK_CANDIDATES
            if not (path / "manager_node_runtime").is_dir()
        ]
        if missing:
            print(
                f"\n[paridad] {description}: copias no verificadas por no estar montadas: "
                f"{', '.join(missing)}"
            )
