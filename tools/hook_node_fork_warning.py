"""Hook PostToolUse: avisa de la copia bifurcada del nodo al editar sus reglas.

El agente no ejecuta `mt5_manager/`: corre `manager_node_runtime/` de su propio
proyecto, con las reglas reimplementadas y renombradas. Cambiar solo el lado del
manager no tiene efecto para el usuario, y eso ya se olvidó tres veces. Este aviso
no depende de que nadie lea AGENTS.md ni `ai_context/`: salta al tocar el fichero.

Lee el JSON del hook por stdin y escribe en stdout un JSON con `systemMessage`
(para la persona) y `additionalContext` (para el modelo). Nunca falla: un hook que
rompe el turno es peor que un aviso perdido.
"""
from __future__ import annotations

import json
import re
import sys

# Reglas del nodo que viven duplicadas en el proyecto del agente.
MANAGER_RULES = re.compile(r"mt5_manager[/\\](portfolio_service|node)\.py$", re.IGNORECASE)
# La otra punta: al editar la copia del agente, recordar la paridad.
AGENT_RULES = re.compile(r"manager_node_runtime[/\\][^/\\]+\.py$", re.IGNORECASE)

MANAGER_WARNING = (
    "Has editado una regla que el agente NO ejecuta. Los equipos broker corren "
    "`manager_node_runtime/` de su propio proyecto, con las mismas reglas "
    "reimplementadas y con OTRO nombre de función (p. ej. "
    "`PortfolioSource.remove_members_to_quarantine` aquí ↔ "
    "`exclude_portfolio_members_payload` en `manager_node_runtime/portfolio_save.py`). "
    "Si el cambio altera el comportamiento del nodo: portarlo a la copia del agente "
    "buscándola por el TEXTO del mensaje al usuario (no por el nombre de la función), "
    "duplicar la prueba en `tests/test_manager_node_*.py` del agente, reiniciar la "
    "aplicación del agente y comprobar con "
    "`python -m unittest tests.test_node_runtime_fork_parity`."
)

AGENT_WARNING = (
    "Has editado la copia bifurcada del nodo. Comprobar que la regla equivalente del "
    "manager (`mt5_manager/portfolio_service.py`) no se ha quedado atrás y ejecutar "
    "`python -m unittest tests.test_node_runtime_fork_parity` en el manager."
)


def _target_path(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response") or {}
    for value in (tool_input.get("file_path"), tool_response.get("filePath")):
        if isinstance(value, str) and value:
            return value
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        path = _target_path(payload)
        if MANAGER_RULES.search(path):
            warning = MANAGER_WARNING
        elif AGENT_RULES.search(path):
            warning = AGENT_WARNING
        else:
            return 0
        print(json.dumps({
            "systemMessage": f"[nodo bifurcado] {warning}",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": warning,
            },
        }, ensure_ascii=False))
    except Exception:
        # Un aviso perdido es aceptable; romper el turno del usuario no lo es.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
