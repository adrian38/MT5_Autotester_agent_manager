"""Verificación única del contrato de trabajo del proyecto.

Ejecuta las cuatro auditorías mecánicas, las guardas arquitectónicas, el suite
completo del manager y, cuando está montado, el contrato guiado del runtime IC.
También comprueba que ninguna otra sesión cambió HEAD ni el estado tracked de
los repositorios mientras duró la medición.

    python -m tools.verify_project
    python -m tools.verify_project --quick
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IC_ROOT = ROOT.parent / "MT5_Autotester_agent_IC" / "MT5_Autotester_agent"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    cwd: Path
    args: tuple[str, ...]
    optional_root: Path | None = None


def command_specs(*, include_full_suite: bool = True) -> list[CommandSpec]:
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
                "tests.test_agents_md_stacks", "tests.test_module_layering",
                "tests.test_guided_routing", "tests.test_function_length",
                "tests.test_file_length", "tests.test_ai_context_index",
                "tests.test_undefined_names", "tests.test_verify_project",
            ),
        ),
    ]
    if include_full_suite:
        specs.append(CommandSpec(
            "suite manager", ROOT, (python, "-m", "unittest", "discover", "-s", "tests"),
        ))
    specs.append(CommandSpec(
        "contrato IC",
        IC_ROOT,
        (
            python, "-m", "unittest", "tests.test_prepared_candidates",
            "tests.test_guided_node", "tests.test_guided_http",
            "tests.test_manager_node_repair_phases",
        ),
        optional_root=IC_ROOT,
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
    return [ROOT] + ([IC_ROOT] if (IC_ROOT / ".git").exists() else [])


def run_spec(spec: CommandSpec) -> bool:
    if spec.optional_root is not None and not spec.optional_root.is_dir():
        print(f"\n[OMITIDO] {spec.name}: {spec.optional_root} no está montado", flush=True)
        return True
    print(f"\n[VERIFICA] {spec.name}", flush=True)
    return subprocess.run(spec.args, cwd=spec.cwd, check=False).returncode == 0


def parse_args(argv: list[str]) -> bool:
    if not argv:
        return True
    if argv == ["--quick"]:
        return False
    raise ValueError("uso: python -m tools.verify_project [--quick]")


def main(argv: list[str] | None = None) -> int:
    try:
        include_full_suite = parse_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        print(exc)
        return 2
    repositories = watched_repositories()
    before = {root: repository_state(root) for root in repositories}
    failed = [spec.name for spec in command_specs(include_full_suite=include_full_suite)
              if not run_spec(spec)]
    changed = [root for root in repositories if repository_state(root) != before[root]]
    for root in changed:
        print(f"[CAMBIO CONCURRENTE] HEAD o estado tracked cambió durante la verificación: {root}")
    if failed:
        print("[FALLO] " + ", ".join(failed))
    if failed or changed:
        return 1
    print("\n[OK] contrato completo del proyecto verificado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
