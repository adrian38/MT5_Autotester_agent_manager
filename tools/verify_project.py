"""Verificación única del contrato de trabajo del proyecto.

Ejecuta las cuatro auditorías mecánicas, las guardas arquitectónicas y, cuando
está montado, el contrato guiado del runtime IC. El suite completo del manager
sólo se añade con ``--full``, después de una petición explícita del usuario.
También comprueba que ninguna otra sesión cambió HEAD ni el estado tracked de
los repositorios mientras duró la medición.

La copia de IC se busca en las candidatas de `tools.agent_projects`, nunca
junto al manager: derivarla de `ROOT.parent` omitía el contrato en silencio en
todo equipo donde el manager no es su vecino.

    python -m tools.verify_project
    python -m tools.verify_project --full
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from tools.agent_projects import ic_project, searched_ic_projects
except ImportError:  # ejecutado como `python tools/verify_project.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.agent_projects import ic_project, searched_ic_projects

ROOT = Path(__file__).resolve().parents[1]

IC_CONTRACT_TESTS = (
    "tests.test_prepared_candidates",
    "tests.test_guided_node",
    "tests.test_guided_http",
    "tests.test_manager_node_repair_phases",
)


def ic_contract_root() -> Path | None:
    """La copia de IC que **tiene** las cuatro pruebas focales, o `None`.

    Se exigen los cuatro modulos, no la carpeta: una copia a medio montar daria
    por ejecutado un contrato que no esta ahi.
    """
    return ic_project(*(f"tests/{name.split('.')[-1]}.py" for name in IC_CONTRACT_TESTS))


@dataclass(frozen=True)
class CommandSpec:
    name: str
    cwd: Path
    args: tuple[str, ...]
    skip_reason: str | None = None


def command_specs(*, include_full_suite: bool = False) -> list[CommandSpec]:
    python = sys.executable
    specs = [
        CommandSpec("exports UBS", ROOT, (python, "-m", "tools.sync_ubs_exports")),
        CommandSpec("nombres", ROOT, (python, "-m", "tools.undefined_names")),
        CommandSpec("funciones", ROOT, (python, "-m", "tools.function_length")),
        CommandSpec("ficheros", ROOT, (python, "-m", "tools.file_length")),
        CommandSpec("contexto", ROOT, (python, "-m", "tools.ai_context_index")),
        CommandSpec(
            "guardas",
            ROOT,
            (
                python, "-m", "unittest",
                "tests.repository.test_agents_md_stacks",
                "tests.repository.test_module_layering",
                "tests.repository.test_ubs_package_layering",
                "tests.test_guided_routing",
                "tests.repository.test_function_length",
                "tests.repository.test_file_length",
                "tests.repository.test_ai_context_index",
                "tests.repository.test_undefined_names",
                "tests.repository.test_verify_project",
            ),
        ),
    ]
    if include_full_suite:
        specs.append(CommandSpec(
            "suite manager", ROOT, (python, "-m", "unittest", "discover", "-s", "tests"),
        ))
    ic_root = ic_contract_root()
    specs.append(CommandSpec(
        "contrato IC",
        ic_root or ROOT,
        (python, "-m", "unittest", *IC_CONTRACT_TESTS),
        skip_reason=None if ic_root else (
            f"ninguna copia de ICTrading esta montada; buscadas: {searched_ic_projects()}"
        ),
    ))
    return specs


def repository_state(root: Path) -> tuple[str, str]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout
    return head, status


def watched_repositories() -> list[Path]:
    ic_root = ic_contract_root()
    return [ROOT] + ([ic_root] if ic_root and (ic_root / ".git").exists() else [])


def run_spec(spec: CommandSpec) -> bool:
    """Ejecuta el paso. Devuelve si paso; una omision no es un fallo."""
    if spec.skip_reason is not None:
        print(f"\n[OMITIDO] {spec.name}: {spec.skip_reason}", flush=True)
        return True
    where = "" if spec.cwd == ROOT else f" en {spec.cwd}"
    print(f"\n[VERIFICA] {spec.name}{where}", flush=True)
    return subprocess.run(spec.args, cwd=spec.cwd, check=False).returncode == 0


def parse_args(argv: list[str]) -> bool:
    if not argv or argv == ["--quick"]:
        return False
    if argv == ["--full"]:
        return True
    raise ValueError("uso: python -m tools.verify_project [--quick|--full]")


def main(argv: list[str] | None = None) -> int:
    try:
        include_full_suite = parse_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        print(exc)
        return 2
    repositories = watched_repositories()
    before = {root: repository_state(root) for root in repositories}
    specs = command_specs(include_full_suite=include_full_suite)
    skipped = [spec.name for spec in specs if spec.skip_reason is not None]
    failed = [spec.name for spec in specs if not run_spec(spec)]
    changed = [root for root in repositories if repository_state(root) != before[root]]
    for root in changed:
        print(f"[CAMBIO CONCURRENTE] HEAD o estado tracked cambió durante la verificación: {root}")
    if failed:
        print("[FALLO] " + ", ".join(failed))
    if failed or changed:
        return 1
    # La omision no pone el resultado en rojo —hay equipos sin IC montado— pero
    # va en la ultima linea: enterrada a mitad de la salida no la ve nadie.
    if skipped:
        print(f"\n[OK] contrato verificado SIN: {', '.join(skipped)}")
    elif not include_full_suite:
        print("\n[OK] contrato focal del proyecto verificado (suite manager no solicitada)")
    else:
        print("\n[OK] contrato completo del proyecto verificado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
