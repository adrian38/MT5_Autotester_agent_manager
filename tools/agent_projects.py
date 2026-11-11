"""Donde vive el proyecto de cada agente en el equipo que ejecuta esto.

La ruta del manager **no** la predice: `verify_project` derivaba la de IC de
`ROOT.parent`, asi que en un equipo con el manager en `I:\\TRADING` buscaba IC
en `I:\\TRADING\\MT5_Autotester_agent_IC\\...`, que no existe. El contrato focal
de IC se omitia siempre y el unico aviso era una linea `[OMITIDO]` al final de
una salida muy larga. Es el mismo desfase que tenia escrita a mano
`mt5_manager/dev_branch.py` y que siguen teniendo las notas viejas de
`ai_context/`.

Por eso hay una **lista de candidatas** que se recorre, como ya hacia
`tests/node/node_runtime_parity_base.FORK_CANDIDATES`, y quien la use dice cuales
salto. Anadir un equipo es anadir una linea; derivarla de donde este el manager
vuelve a romperlo en silencio.

La lista vive en `mt5_manager/dev_branch.py`, que la necesita para el candado de
la rama `dev`, y se reexporta aqui: la imagen Docker copia `mt5_manager` sin
copiar `tools/`, asi que la dependencia solo puede ir en este sentido.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # ejecutado como `python tools/agent_projects.py`
    sys.path.insert(0, str(ROOT))

from mt5_manager.dev_branch import DEV_PROJECT_CANDIDATES  # noqa: E402

IC_PROJECT_CANDIDATES: tuple[Path, ...] = tuple(Path(path) for path in DEV_PROJECT_CANDIDATES)
"""Checkouts de ICTrading conocidos, en orden de preferencia.

La ultima es la vecina del manager, que es lo que se asumia antes: se conserva
para los equipos donde si lo es, pero ya no es la unica.
"""


def ic_project(*required: str) -> Path | None:
    """La primera copia de IC que contiene **todo** lo que pide quien llama.

    `required` son rutas relativas dentro del proyecto. Pedirlas evita el fallo
    que de verdad duele: encontrar una carpeta con el nombre correcto y dar por
    ejecutado un contrato que no esta ahi.
    """
    for candidate in IC_PROJECT_CANDIDATES:
        try:
            if candidate.is_dir() and all((candidate / name).exists() for name in required):
                return candidate
        except OSError:
            continue
    return None


def agent_test_module(project: Path, topic: str) -> str | None:
    """El modulo de prueba de `topic` en esa copia, plana o repartida.

    IC repartio su `tests/` en subpaquetes el 2026-11-11:
    `tests/test_guided_node.py` bajo a `tests/guided/` y
    `tests/test_manager_node_repair_phases.py` a
    `tests/manager_node/test_repair_phases.py`. AXI, RoboForex y Vantage siguen
    planas, asi que no vale cambiar la lista por la nueva: hay que encontrar
    las dos formas. Un nombre de modulo escrito a mano pedia la plana y dejaba
    el contrato focal **omitido en silencio**, que es justo lo que esta lista de
    candidatas existia para evitar.

    Busca por el final del nombre, que es lo unico que las dos formas
    comparten, y devuelve el modulo tal y como hay que pasarselo a `unittest`.
    """
    root = project / "tests"
    try:
        paths = sorted(root.rglob("test_*.py"))
    except OSError:
        return None
    for path in paths:
        if path.stem.endswith(topic):
            return ".".join(path.relative_to(project).with_suffix("").parts)
    return None


def agent_test_modules(project: Path, topics: tuple[str, ...]) -> tuple[str, ...] | None:
    """Los modulos de **todos** los temas, o `None` si falta alguno.

    Todos o ninguno por el mismo motivo que `ic_project` exige sus `required`:
    media copia montada daria por ejecutado un contrato que no esta ahi.
    """
    found = tuple(
        module for module in (agent_test_module(project, topic) for topic in topics)
        if module is not None
    )
    return found if len(found) == len(topics) else None


def ic_contract_project(topics: tuple[str, ...]) -> tuple[Path, tuple[str, ...]] | None:
    """La primera copia de IC que tiene los temas pedidos, con sus modulos."""
    for candidate in IC_PROJECT_CANDIDATES:
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        modules = agent_test_modules(candidate, topics)
        if modules is not None:
            return candidate, modules
    return None


def searched_ic_projects() -> str:
    """Las candidatas en una linea, para que una omision diga donde se busco."""
    return ", ".join(str(path) for path in IC_PROJECT_CANDIDATES)
