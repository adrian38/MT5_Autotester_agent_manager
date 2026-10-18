"""Donde vive el proyecto de cada agente en el equipo que ejecuta esto.

La ruta del manager **no** la predice: `verify_project` derivaba la de IC de
`ROOT.parent`, asi que en un equipo con el manager en `I:\\TRADING` buscaba IC
en `I:\\TRADING\\MT5_Autotester_agent_IC\\...`, que no existe. El contrato focal
de IC se omitia siempre y el unico aviso era una linea `[OMITIDO]` al final de
una salida muy larga. Es el mismo desfase que documenta `CLAUDE.md` sobre
`mt5_manager/dev_branch.py` y las notas viejas de `ai_context/`.

Por eso aqui hay una **lista de candidatas** que se recorre, como ya hacia
`tests/node_runtime_parity_base.FORK_CANDIDATES`, y quien la use dice cuales
salto. Anadir un equipo es anadir una linea; derivarla de donde este el manager
vuelve a romperlo en silencio.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

IC_PROJECT_CANDIDATES: tuple[Path, ...] = (
    Path(r"C:\Users\13199\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent"),
    Path(r"C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent"),
    ROOT.parent / "MT5_Autotester_agent_IC" / "MT5_Autotester_agent",
)
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


def searched_ic_projects() -> str:
    """Las candidatas en una linea, para que una omision diga donde se busco."""
    return ", ".join(str(path) for path in IC_PROJECT_CANDIDATES)
