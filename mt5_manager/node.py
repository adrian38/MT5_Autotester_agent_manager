"""Nodo remoto del manager. ATENCIÓN: NO es el nodo que corre en los brokers.

Los equipos broker ejecutan una copia bifurcada y renombrada en
`manager_node_runtime/` del proyecto del agente, embebida en `app_ui.py` vía
`manager_node_lifecycle.py`. Este módulo y `run_node.bat` solo sirven para
ejecutar un nodo desde este repositorio.

Consecuencia: cambiar aquí una regla de comportamiento del nodo (guardado,
exclusión, escritura en la memoria UBS) **no tiene ningún efecto** sobre los
agentes. La copia del agente reimplementa esas reglas en
`manager_node_runtime/portfolio_save.py` con otros nombres de función, así que no
la encuentra ni el grafo ni una búsqueda por símbolo: hay que buscarla por el
texto del mensaje al usuario. Ver `ai_context/node_runtime_is_forked_per_agent.md`
y `tests/test_node_runtime_fork_parity.py`, que falla si las copias divergen.
"""

from __future__ import annotations

# El nodo esta repartido en modulos por dependencia; este fichero es la fachada
# para que `python -m mt5_manager.node`, `run_node.bat`, las pruebas y la guarda
# de paridad sigan encontrando aqui los mismos nombres.
from .node_settings import (  # noqa: F401
    CLEANUP_STAGES,
    SCORE_OPTIONS,
    VALUE_OPTIONS,
    _load_universe_rows,
    _universe_paths,
    build_historical_cleanup_command,
    cleanup_after_run_enabled,
    historical_cleanup_scripts,
    memory_path,
    read_settings,
    setting,
    setting_bool,
)
from .node_commands import (  # noqa: F401
    build_generation_command,
    build_pipeline_stage_command,
)
from .node_snapshots import (  # noqa: F401
    _table_exists,
    completed_runs_snapshot,
    database_snapshot,
    pipeline_stage_pending_count,
)
from .node_statuses import (  # noqa: F401
    ACTIVE_STATUSES,
    CONTROL_LOCK_TIMEOUT,
    RESUMABLE_STATUSES,
)
from .node_jobs import JobController  # noqa: F401
from .node_http import NodeHandler, NodeServer, main  # noqa: F401


if __name__ == "__main__":
    raise SystemExit(main())
