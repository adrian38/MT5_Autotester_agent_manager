"""La raiz es una lista cerrada, y cada carpeta tiene un dueno.

La raiz habia juntado un respaldo (`Caddyfile.bak`), dos JSON de despliegue de
una maquina concreta y dos lanzadores `.bat`, todos «porque era donde se veian
mejor». Nadie los puso ahi con mala intencion: sin lista, la raiz es el sitio
por defecto de cualquier cosa, y una vez dentro no vuelve a salir.

Esta guarda fija tres cosas:

- Que no aparezca en la raiz nada que no busque una herramienta ahi por nombre
  fijo. El mensaje dice el nombre y la carpeta que le toca.
- Que la tabla de `AGENTS.md` diga exactamente lo mismo que esta lista. El
  documento es lo que se lee para orientarse; si discrepan, miente sin que
  nadie se entere (la misma deriva que vigila `test_agents_md_stacks`).
- Que `tests/` no vuelva a acumular `test_*.py` suelto, y que todo modulo de
  `mt5_manager/` pertenezca a un dominio declarado.

No se anade un nombre a estas listas para que el test pase. Se elige la carpeta
a la que pertenece el fichero.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"

#: Lo unico que puede vivir en la raiz. El motivo de cada uno esta en la tabla
#: de `AGENTS.md`, y las trampas de moverlo en
#: `ai_context/layout_del_repositorio.md`.
ROOT_FILES = frozenset({
    "pyproject.toml",
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "Caddyfile",
    ".env",
    ".env.docker.example",
    ".gitignore",
    ".mcp.json",
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "agent.md",
    "manager.json",
    "node.json",
})

#: Las carpetas con dueno. Cada una esta en la segunda tabla de `AGENTS.md`.
ROOT_DIRS = frozenset({
    "mt5_manager",
    "portfolio_manager",
    "ubs",
    "tests",
    "tools",
    "ai_context",
    "config",
    "assets",
    "docs",
    "scripts",
    "runtime",
})

#: Carpetas de herramienta o generadas. No se documentan como responsabilidad
#: del proyecto porque no las escribe nadie a mano.
TOOLING_DIRS = frozenset({
    ".git", ".claude", ".codebase-memory", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".idea",
    ".vscode", "node_modules", "build", "dist",
})

#: A donde va lo que no es manifiesto, por si el mensaje tiene que decirlo.
DESTINOS = (
    (re.compile(r"\.bak$|\.orig$|\.log$|\.tmp$"), "runtime/ (desechable)"),
    (re.compile(r"\.deploy\.json$"), "config/ (configuracion por nodo)"),
    (re.compile(r"\.(bat|ps1|sh|cmd)$"), "scripts/"),
    (re.compile(r"^test_.*\.py$"), "tests/<dominio>/"),
    (re.compile(r"\.py$"), "mt5_manager/, portfolio_manager/ o tools/"),
    (re.compile(r"\.md$"), "ai_context/ (con su linea en el indice)"),
    (re.compile(r"\.(xlsx|csv|pdf)$"), "docs/"),
)

#: Los prefijos de dominio de `mt5_manager/`. Mientras el paquete siga plano,
#: es lo unico que mantiene el agrupamiento legible.
MT5_DOMAINS = (
    "live_audit",
    "live_account",
    "manager",
    "monthly_summary",
    "node",
    "portfolio",
    "experiment",
    "prop",
    "web_push",
    "guided",
    "manual_check",
)

#: Modulos de `mt5_manager/` anteriores a la regla, sin prefijo de dominio.
#: **Solo puede encoger**: cuando el paquete se reparta en subpaquetes, cada uno
#: entra en el suyo y sale de aqui. Anadir un nombre es una regresion.
MT5_SIN_DOMINIO = frozenset({
    "candidate_verdict",
    "common",
    "dev_branch",
    "docker_entrypoint",
    "stage_reports",
})


def destino(name: str) -> str:
    for pattern, folder in DESTINOS:
        if pattern.search(name):
            return folder
    return "una carpeta con dueno; ver la tabla de AGENTS.md"


def documented(heading: str) -> set[str]:
    """Los nombres entrecomillados de la primera columna de una tabla."""
    text = AGENTS.read_text(encoding="utf-8")
    start = text.index(heading) + len(heading)
    end = text.find("\n#", start)
    names: set[str] = set()
    for line in text[start: end if end != -1 else len(text)].splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        first = line.split("|")[1]
        names.update(re.findall(r"`([^`]+)`", first))
    return {name.rstrip("/") for name in names}


class RootIsAClosedListTest(unittest.TestCase):
    def test_no_file_lives_in_the_root_that_is_not_a_manifest(self) -> None:
        intruders = {
            path.name: destino(path.name)
            for path in ROOT.iterdir()
            if path.is_file() and path.name not in ROOT_FILES
        }
        self.assertFalse(
            intruders,
            "La raiz solo guarda manifiestos. Esto pertenece a otra carpeta:\n"
            + "\n".join(f"  {name} -> {folder}" for name, folder in sorted(intruders.items())),
        )

    def test_every_top_level_directory_has_a_declared_owner(self) -> None:
        undeclared = sorted(
            path.name for path in ROOT.iterdir()
            if path.is_dir() and path.name not in ROOT_DIRS | TOOLING_DIRS
        )
        self.assertFalse(
            undeclared,
            "Carpeta de primer nivel sin dueno declarado. Si es del proyecto, "
            "entra en ROOT_DIRS y en la tabla de AGENTS.md; si la genera una "
            f"herramienta, en TOOLING_DIRS y en .gitignore: {undeclared}",
        )

    def test_the_declared_directories_exist(self) -> None:
        """Una carpeta documentada que no existe es una promesa vacia."""
        missing = sorted(name for name in ROOT_DIRS if not (ROOT / name).is_dir())
        self.assertEqual([], missing, f"ROOT_DIRS nombra carpetas que no existen: {missing}")


class AgentsMdSaysTheSameTest(unittest.TestCase):
    def test_the_root_table_lists_exactly_the_allowed_files(self) -> None:
        self.assertEqual(
            set(ROOT_FILES), documented("### La raíz es una lista cerrada"),
            "la tabla de la raiz de AGENTS.md no coincide con ROOT_FILES",
        )

    def test_the_folder_table_lists_exactly_the_owned_directories(self) -> None:
        self.assertEqual(
            set(ROOT_DIRS), documented("### Una carpeta por responsabilidad"),
            "la tabla de carpetas de AGENTS.md no coincide con ROOT_DIRS",
        )


class TestsAreGroupedByOwnerTest(unittest.TestCase):
    def test_no_test_module_stays_at_the_root_of_tests(self) -> None:
        loose = sorted(path.name for path in (ROOT / "tests").glob("test_*.py"))
        self.assertFalse(
            loose,
            "Las pruebas se agrupan por dueno; la raiz de tests/ solo guarda "
            f"piezas compartidas. Baja estos a su carpeta: {loose}",
        )


class ManagerPackageDomainsTest(unittest.TestCase):
    def modules(self) -> list[str]:
        return sorted(
            path.stem for path in (ROOT / "mt5_manager").glob("*.py")
            if path.stem != "__init__"
        )

    def test_every_module_belongs_to_a_declared_domain(self) -> None:
        orphans = sorted(
            name for name in self.modules()
            if name not in MT5_SIN_DOMINIO
            and not any(name == d or name.startswith(f"{d}_") for d in MT5_DOMAINS)
        )
        self.assertFalse(
            orphans,
            "Modulo de mt5_manager/ sin dominio. Elige el dominio al que "
            f"pertenece y usa su prefijo; no amplies la lista: {orphans}\n"
            f"  dominios: {', '.join(MT5_DOMAINS)}",
        )

    def test_the_list_of_modules_without_a_domain_only_shrinks(self) -> None:
        stale = sorted(MT5_SIN_DOMINIO - set(self.modules()))
        self.assertFalse(
            stale,
            "MT5_SIN_DOMINIO nombra modulos que ya no estan. Borra su entrada: "
            f"{stale}",
        )


if __name__ == "__main__":
    unittest.main()
