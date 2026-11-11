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
- Que `tests/` no vuelva a acumular `test_*.py` suelto.
- Que `mt5_manager/` siga siendo un subpaquete por dominio: nada nuevo en la
  raiz del paquete, ninguna carpeta sin declarar y ningun `__init__.py` que
  reexporte su dominio, salvo el de `node` y por el fichero congelado.

No se anade un nombre a estas listas para que el test pase. Se elige la carpeta
a la que pertenece el fichero.
"""
from __future__ import annotations

import ast
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

#: Los subpaquetes de dominio de `mt5_manager/`. Cada uno esta en la tabla de
#: «El codigo se agrupa por dominio, no por prefijo» de `AGENTS.md`.
MT5_DOMAINS = (
    "experiment",
    "live_account",
    "live_audit",
    "manager",
    "manual_check",
    "monthly_summary",
    "node",
    "portfolio",
    "prop",
    "web_push",
)

#: Lo unico que puede vivir en la raiz del paquete, por no ser de ningun
#: dominio. `guided_batches` y `guided_controller` son los dos ficheros
#: identicos byte a byte al runtime de IC: no se mueven porque el
#: `from .node import` congelado de `guided_controller` solo resuelve aqui.
MT5_ROOT_MODULES = frozenset({
    "__init__",
    "common",
    "dev_branch",
    "docker_entrypoint",
    "guided_batches",
    "guided_controller",
})

#: Carpetas del paquete que no son dominios.
MT5_OTHER_DIRS = frozenset({"static", "__pycache__"})


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
    """Un subpaquete por dominio, y la raiz del paquete tambien cerrada.

    El prefijo del nombre agrupaba de mentira: `ls` devolvia 167 lineas y nada
    impedia el modulo 168 sin dominio. Ahora el agrupamiento es la carpeta, y
    esto es lo que lo mantiene.
    """

    PACKAGE = ROOT / "mt5_manager"

    def test_no_module_lives_in_the_package_root_without_a_reason(self) -> None:
        intruders = sorted(
            path.stem for path in self.PACKAGE.glob("*.py")
            if path.stem not in MT5_ROOT_MODULES
        )
        self.assertFalse(
            intruders,
            "La raiz de mt5_manager/ guarda solo lo que no es de ningun dominio. "
            f"Baja esto a su subpaquete; no amplies la lista: {intruders}\n"
            f"  dominios: {', '.join(MT5_DOMAINS)}",
        )

    def test_every_subpackage_is_a_declared_domain(self) -> None:
        undeclared = sorted(
            path.name for path in self.PACKAGE.iterdir()
            if path.is_dir() and path.name not in set(MT5_DOMAINS) | MT5_OTHER_DIRS
        )
        self.assertFalse(
            undeclared,
            "Subpaquete de mt5_manager/ sin declarar. Si es un dominio, entra en "
            f"MT5_DOMAINS y en la tabla de AGENTS.md: {undeclared}",
        )

    def test_the_declared_domains_exist_and_carry_an_init(self) -> None:
        """Un dominio sin `__init__.py` no lo empaqueta `setuptools`."""
        broken = sorted(
            name for name in MT5_DOMAINS
            if not (self.PACKAGE / name / "__init__.py").is_file()
        )
        self.assertEqual([], broken, f"dominios sin __init__.py: {broken}")

    def test_only_the_node_domain_re_exports_from_its_init(self) -> None:
        """El `__init__` de un dominio esta vacio: si reexportara, haria ciclos.

        `live_account_watch` importa `manager_http` y el manager importa
        `live_account_watch`; con un `__init__` cargado eso es un `ImportError`
        por paquete a medio inicializar. La excepcion es `node`, y su motivo es
        concreto: la linea congelada `from .node import read_settings,
        memory_path` de `guided_controller.py`, que no se puede tocar.
        """
        loaded = sorted(
            name for name in MT5_DOMAINS
            if any(
                isinstance(node, (ast.Import, ast.ImportFrom))
                for node in ast.parse(
                    (self.PACKAGE / name / "__init__.py").read_text(encoding="utf-8")
                ).body
            )
        )
        self.assertEqual(
            ["node"], loaded,
            "El `__init__.py` de un dominio no reexporta. Sólo `node` lo hace, "
            f"y por el fichero congelado: {loaded}",
        )

    def test_agents_md_lists_exactly_the_declared_domains(self) -> None:
        documented_domains = {
            name.split("/")[-1]
            for name in documented("### El código se agrupa por dominio, no por prefijo")
            if name.startswith("mt5_manager/")
        }
        self.assertEqual(
            set(MT5_DOMAINS) | {"static"}, documented_domains,
            "la tabla de dominios de AGENTS.md no coincide con MT5_DOMAINS",
        )


if __name__ == "__main__":
    unittest.main()
