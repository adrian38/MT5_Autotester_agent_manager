"""Rutas del entorno de pruebas, activas solo en la rama ``dev``.

La rama ``dev`` es la de pruebas y en este equipo solo existe el agente local de
ICTrading. En ``dev`` se corrige la ruta de ese nodo para que apunte al proyecto
de este equipo. Los nodos de AXI y RoboForex siguen en la lista, con sus tarjetas
visibles y sus rutas de otra PC intactas: no son el objeto de las pruebas y
falla abrirlos igual que antes de este modulo.

Ademas, en ``dev`` la escritura queda acotada: ``assert_writable`` rechaza
cualquier escritura fuera del proyecto del agente de ICTrading (mas el estado
propio del manager y los temporales, ver ``writable_roots``). Aunque una tarjeta
de produccion se abriera, no puede tocar la memoria de la otra PC.

En cualquier otra rama —``main`` incluida— todas las funciones devuelven la
configuracion intacta. El merge a produccion arrastra este modulo, pero queda
inerte: nunca reescribe las rutas de produccion.

La deteccion lee ``.git/HEAD`` directamente en lugar de invocar ``git``, para
no depender de que el ejecutable este en el ``PATH`` ni pagar un subproceso en
el arranque. Sin ``.git`` visible (paquete instalado, imagen Docker) la
redireccion queda desactivada.
"""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Any


DEV_BRANCH = "dev"

# Agente de pruebas de este equipo: el usuario ``test`` que ya se usaba antes.
DEV_PROJECT_DIR = r"C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent"
DEV_BROKER = "ICTRADING"

# ``1``/``0`` fuerzan o desactivan la redireccion sin cambiar de rama; vacio o
# ausente deja decidir a la rama actual.
OVERRIDE_ENV = "MT5_MANAGER_DEV_OVERRIDE"
PROJECT_DIR_ENV = "MT5_MANAGER_DEV_PROJECT_DIR"

_TRUE = {"1", "true", "yes", "on", "si"}
_FALSE = {"0", "false", "no", "off"}


def _git_dir(start: Path) -> Path | None:
    """Devuelve el directorio ``.git`` que gobierna ``start``, o ``None``."""
    for candidate in (start, *start.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return marker
        if marker.is_file():
            # Worktree o submodulo: el fichero apunta al .git real.
            try:
                content = marker.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not content.startswith("gitdir:"):
                return None
            target = Path(content.split(":", 1)[1].strip())
            if not target.is_absolute():
                target = (candidate / target).resolve()
            return target if target.is_dir() else None
    return None


def current_branch(start: Path | None = None) -> str | None:
    """Rama activa segun ``.git/HEAD``; ``None`` si no hay repositorio o esta
    en ``HEAD`` desprendido."""
    base = Path(start).expanduser().resolve() if start else Path(__file__).resolve().parents[1]
    git_dir = _git_dir(base)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return None
    ref = head.split(":", 1)[1].strip()
    prefix = "refs/heads/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


def is_active(start: Path | None = None) -> bool:
    """``True`` cuando hay que aplicar las rutas de pruebas."""
    forced = str(os.environ.get(OVERRIDE_ENV) or "").strip().lower()
    if forced in _TRUE:
        return True
    if forced in _FALSE:
        return False
    return current_branch(start) == DEV_BRANCH


def project_dir() -> str:
    """Ruta del agente de pruebas, sobrescribible por entorno."""
    return str(os.environ.get(PROJECT_DIR_ENV) or "").strip() or DEV_PROJECT_DIR


def _report(message: str) -> None:
    print(f"[dev] {message}", flush=True)


def writable_roots() -> list[Path]:
    """Unicos arboles donde la rama de pruebas puede escribir.

    - El proyecto del agente de ICTrading: la unica direccion de nodo permitida.
    - ``runtime/`` de este repositorio: estado propio del manager (preferencias,
      configuracion de portafolios, base Grid, copias de lectura). No pertenece a
      ningun agente.
    - El temporal del sistema: copias de lectura cuando ``runtime/`` esta en un
      bind mount, y los proyectos ficticios de las pruebas.
    """
    return [
        Path(project_dir()).expanduser().absolute(),
        Path(__file__).resolve().parents[1] / "runtime",
        Path(tempfile.gettempdir()).absolute(),
    ]


def _root_variants(root: Path) -> list[Path]:
    """Formas comparables de una raiz permitida: absoluta y resuelta.

    Se resuelve solo la raiz, nunca el destino: resolver una ruta de red o de
    unidad mapeada que no responde bloquea segundos por llamada, y el candado
    corre en cada escritura. Una raiz permitida si existe, asi que resolverla es
    inmediato y cubre que venga por enlace o por letra mapeada.
    """
    absolute = root.expanduser().absolute()
    forms = [absolute]
    try:
        resolved = absolute.resolve()
    except OSError:
        return forms
    if resolved != absolute:
        forms.append(resolved)
    return forms


def _within(path: Path, root: Path) -> bool:
    for base in _root_variants(root):
        if path == base or base in path.parents:
            return True
    return False


def assert_writable(path: str | Path, what: str = "") -> Path:
    """Verifica que ``path`` sea escribible en la rama actual y lo devuelve.

    Fuera de ``dev`` no comprueba nada. En ``dev`` levanta ``ValueError`` —el
    error que manager y nodo traducen a 400 con el mensaje visible— si el destino
    cae fuera de ``writable_roots``.
    """
    target = Path(path).expanduser().absolute()
    if not is_active():
        return target
    if any(_within(target, root) for root in writable_roots()):
        return target
    detail = f"{what}: " if what else ""
    raise ValueError(
        f"{detail}la rama {DEV_BRANCH} solo puede escribir en {project_dir()}; "
        f"se ha intentado escribir en {target}"
    )


def apply_manager_config(config: dict[str, Any], start: Path | None = None) -> dict[str, Any]:
    """Corrige la ruta del nodo de ICTrading en ``manager.json`` cuando toca.

    Conserva la lista de nodos completa: las tarjetas de AXI y RoboForex siguen
    en el panel con sus rutas de produccion. Fuera de ``dev`` devuelve el mismo
    objeto sin copiar ni tocar nada. Si en ``dev`` no hubiera ningun nodo de
    ICTrading, no inventa uno: lo avisa y deja la configuracion como esta.
    """
    if not is_active(start):
        return config
    nodes = [node for node in (config.get("nodes") or []) if isinstance(node, dict)]
    local = [node for node in nodes if str(node.get("portfolio_broker") or "").strip().upper() == DEV_BROKER]
    if not local:
        _report(
            f"rama {DEV_BRANCH}: ningun nodo con portfolio_broker {DEV_BROKER}; "
            "se mantiene la configuracion original"
        )
        return config
    target = project_dir()
    updated = copy.deepcopy(config)
    corrected: list[str] = []
    for node in updated["nodes"]:
        if not isinstance(node, dict):
            continue
        if str(node.get("portfolio_broker") or "").strip().upper() != DEV_BROKER:
            continue
        node["portfolio_project_dir"] = target
        # Las rutas de memoria explicitas apuntan a la otra PC; el servicio las
        # deriva del project_dir cuando no estan.
        node.pop("portfolio_memory_path", None)
        node.pop("portfolio_memory_paths", None)
        corrected.append(str(node.get("id") or "?"))
    _report(f"rama {DEV_BRANCH}: agente local ({', '.join(corrected)}) en {target}")
    untouched = [
        str(node.get("id") or "?")
        for node in nodes
        if str(node.get("portfolio_broker") or "").strip().upper() != DEV_BROKER
    ]
    if untouched:
        _report(f"rama {DEV_BRANCH}: nodos de otra PC sin tocar: {', '.join(untouched)}")
    return updated


def apply_node_config(config: dict[str, Any], start: Path | None = None) -> dict[str, Any]:
    """Fuerza ``project_dir`` del nodo al agente de pruebas cuando toca."""
    if not is_active(start):
        return config
    target = project_dir()
    if str(config.get("project_dir") or "").strip() == target:
        return config
    updated = copy.deepcopy(config)
    updated["project_dir"] = target
    _report(f"rama {DEV_BRANCH}: project_dir del nodo forzado a {target}")
    return updated
