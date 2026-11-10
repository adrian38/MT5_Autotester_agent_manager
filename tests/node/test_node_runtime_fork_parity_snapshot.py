"""Paridad del **sondeo de cuentas** con la copia de cada agente.

Vive aparte de `test_node_runtime_fork_parity_live.py` por lo mismo que
`fork_live_account_source` existe aparte de `fork_live_audit_source`: el
sondeo se mantiene sin un solo import del auditor para poder portarse idéntico,
y son dos familias de ficheros a propósito. Juntarlas también dejaba el módulo
del auditor por encima del techo de 600 líneas.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    from .node_runtime_parity_base import (
        MANAGER_ROOT, NodeRuntimeForkParityBase, fork_live_account_source,
    )
except ImportError:
    from tests.node.node_runtime_parity_base import (
        MANAGER_ROOT, NodeRuntimeForkParityBase, fork_live_account_source,
    )


class AccountSnapshotForkParityTests(NodeRuntimeForkParityBase):
    def test_the_snapshot_refuses_an_unloaded_history_on_every_fork(self) -> None:
        # `_synchronised_history` ya devolvía `history_empty_after_sync`, pero
        # el sondeo tiraba el resultado y exportaba el informe igual. Un HTML
        # sin una sola fila pasa la validación del terminal, así que la cuenta
        # salía `completed` con cero operaciones y, una vez fuera del nodo, ya
        # no se distingue de una semana en calma: el 2026-11-09 el merge del
        # manager podó con ella cuatro días ciertos de 52560280, y el
        # 2026-11-01 la serie entera de 77049426. La diferencia sólo se ve
        # aquí, con el terminal delante.
        tokens = (
            "def _require_loaded_history",
            "self._require_loaded_history(mt5, sync,",
            "history_empty_after_sync",
            "mt5.history_deals_get(end - timedelta(days=DEFAULT_HISTORY_DAYS), end)",
        )
        manager_snapshot = (MANAGER_ROOT / "mt5_manager" / "live_account_snapshot.py").read_text(
            encoding="utf-8",
        )
        for token in tokens:
            self.assertIn(token, manager_snapshot, f"El manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            for token in tokens:
                self._assert_present(
                    fork_live_account_source(project), re.escape(token),
                    f"{project}: el sondeo de cuentas sigue publicando una lectura "
                    "cuyo historial no llegó a bajar. Portar `_require_loaded_history` "
                    "desde mt5_manager/live_account_snapshot.py.",
                )

        self._assert_on_every_fork(check, "no publicar una lectura sin historial")
