"""Paridad entre la regla del manager y la copia bifurcada que corre el agente.

`manager_node_runtime/portfolio_save.py` del agente reimplementa las reglas de
exclusión de `PortfolioSource`, con otros nombres de función. Cambiar solo el lado
del manager no tiene efecto para el usuario, y ese fallo se ha repetido tres veces
(pausa/reanudación, exclusión el 2026-07-20, exclusión múltiple mensual el
2026-08-09). Estas pruebas convierten el olvido en un fallo mecánico.

Alcance: solo comprueban las copias **presentes en este equipo**. Las unidades de
los otros brokers no están montadas casi nunca, así que su copia se informa como
omitida en lugar de fingir que está verificada.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

MANAGER_ROOT = Path(__file__).parents[1]
MANAGER_RULES = MANAGER_ROOT / "mt5_manager" / "portfolio_service.py"

# Copias conocidas, en el orden de `ai_context/node_runtime_is_forked_per_agent.md`.
# La primera es el nodo de ICTrading de este equipo, único destino que el
# invariante de la rama `dev` autoriza a escribir.
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
            # Unidad montada pero inaccesible: se trata como ausente.
            continue
    return found


class NodeRuntimeForkParityTests(unittest.TestCase):
    """Cada prueba compara una regla concreta, no el fichero entero.

    Las copias divergen a propósito (el agente notifica por Telegram, el manager
    no), así que un `diff` completo sería ruido permanente. Lo que no puede
    divergir es el criterio de negocio.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.manager_source = MANAGER_RULES.read_text(encoding="utf-8")
        cls.forks = _reachable_forks()

    def _assert_absent(self, source: str, pattern: str, message: str) -> None:
        # No usar assertNotRegex: volcaría el fichero completo del agente en el
        # fallo y taparía la instrucción de qué hay que portar.
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
        missing = [str(path) for path in FORK_CANDIDATES if not (path / "manager_node_runtime").is_dir()]
        if missing:
            print(
                f"\n[paridad] {description}: copias no verificadas por no estar montadas: "
                f"{', '.join(missing)}"
            )

    def test_the_manager_still_owns_the_rules_this_parity_check_tracks(self) -> None:
        # Si alguien renombra o reescribe el lado del manager, las pruebas de abajo
        # dejarían de comparar nada sin avisar. Esta ancla lo impide.
        self.assertIn("def remove_member_to_quarantine", self.manager_source)
        self.assertIn("def remove_members_to_quarantine", self.manager_source)
        self.assertIn('if is_bundle or scope == "monthly":', self.manager_source)
        self.assertIn('if not (is_bundle or scope == "monthly"):', self.manager_source)

    def test_batch_exclusion_accepts_monthly_on_every_reachable_fork(self) -> None:
        def check(project: Path, source: str) -> None:
            self._assert_absent(
                source,
                r'multiple and not \(\s*scope == "full_history" and is_bundle\s*\)',
                f"{project}: la copia del agente sigue reservando la exclusión múltiple "
                "a los bundles A/M/C de full_history. Portar el criterio "
                '`is_bundle or scope == "monthly"` a '
                "manager_node_runtime/portfolio_save.py y duplicar la prueba en "
                "tests/test_manager_node_portfolio_save.py.",
            )
            self._assert_present(
                source,
                r'multiple and not \(\s*is_bundle or scope == "monthly"\s*\)',
                f"{project}: falta el criterio de exclusión múltiple del manager en "
                "manager_node_runtime/portfolio_save.py.",
            )

        self._assert_on_every_fork(check, "exclusión múltiple mensual")

    def test_whole_deletion_criteria_match_the_manager_on_every_reachable_fork(self) -> None:
        # El manager borra el portafolio entero cuando es bundle o mensual; el
        # agente añade el caso múltiple, que implica lo mismo.
        def check(project: Path, source: str) -> None:
            self._assert_present(
                source,
                r'delete_whole = multiple or is_bundle or scope == "monthly"',
                f"{project}: el criterio de borrado completo divergió del manager "
                "(bundle o mensual se borran enteros; el full_history de objetivo "
                "único se recalcula).",
            )

        self._assert_on_every_fork(check, "borrado completo")

    def test_user_facing_exclusion_messages_match_on_every_reachable_fork(self) -> None:
        # El texto del mensaje es lo único que une las dos copias: los nombres de
        # función difieren. Si el texto se desincroniza, se pierde el único hilo
        # que permite encontrar la copia del agente al buscar por síntoma.
        expected = {
            "Excluida manualmente de un portafolio A/M/C eliminado",
            "Excluida manualmente de un Portafolio UBS mensual eliminado",
        }
        for text in expected:
            self.assertIn(text, self.manager_source, f"El manager perdió el texto: {text}")

        def check(project: Path, source: str) -> None:
            for text in sorted(expected):
                self.assertIn(
                    text,
                    source,
                    msg=f"{project}: la copia del agente no comparte el texto «{text}».",
                )

        self._assert_on_every_fork(check, "textos de cuarentena")

    def test_every_reachable_fork_keeps_its_own_manager_node_test(self) -> None:
        # Paso 3 del procedimiento de `ai_context/node_runtime_is_forked_per_agent.md`:
        # el port no está terminado sin su prueba en el proyecto del agente.
        def check(project: Path, _source: str) -> None:
            tests = sorted((project / "tests").glob("test_manager_node_*.py"))
            self.assertTrue(
                tests,
                msg=f"{project}: no hay ninguna prueba tests/test_manager_node_*.py que cubra el nodo.",
            )
            monthly = [
                path for path in tests
                if re.search(r"monthly", path.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE)
            ]
            self.assertTrue(
                monthly,
                msg=(
                    f"{project}: ninguna prueba del nodo menciona el ámbito mensual; "
                    "duplicar allí la cobertura de la exclusión múltiple mensual."
                ),
            )

        self._assert_on_every_fork(check, "pruebas del nodo en el agente")


if __name__ == "__main__":
    unittest.main()
