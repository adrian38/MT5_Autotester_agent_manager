"""Paridad del `.set` que la auditoría real manda al Strategy Tester.

Vive en su propio fichero, como la de la comprobación manual: los dos de al
lado ya rozan las 600 líneas y la lista de criterios crece con cada port.

Quien escribe el set que MT5 abre es el runtime del agente, así que el
`UseEveryTick=true` del manager no audita nada por sí solo. Sin el port, la
auditoría sigue corriendo con el valor que traiga el set guardado —`false`,
que es como los escribe `guided_batches.py`— y nadie se entera: no hay 404
ni error, sólo un tester donde el experto mira el cierre de vela mientras la
pantalla dice que comparó ticks reales.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from .node_runtime_parity_base import (
        MANAGER_ROOT, NodeRuntimeForkParityBase, fork_live_audit_source,
    )
except ImportError:
    from tests.node.node_runtime_parity_base import (
        MANAGER_ROOT, NodeRuntimeForkParityBase, fork_live_audit_source,
    )


EVERY_TICK_TOKENS = (
    (
        'AUDIT_EVERY_TICK = ("UseEveryTick", "true")',
        "la marca de cada tick que lleva todo set auditado",
    ),
    (
        "self._set_value(text, *AUDIT_EVERY_TICK)",
        "la escritura de esa marca en la copia que MT5 abre",
    ),
)


class LiveAuditEveryTickParityTests(NodeRuntimeForkParityBase):
    def test_every_audited_set_is_marked_on_every_reachable_fork(self) -> None:
        # El manager primero: si aquí se pierde el criterio, la paridad
        # seguiría verde comprobando una regla que ya no existe.
        manager = (MANAGER_ROOT / "mt5_manager" / "live_audit_tester.py").read_text(
            encoding="utf-8"
        )
        for token, hint in EVERY_TICK_TOKENS:
            self.assertIn(token, manager, f"El manager perdió {hint} (`{token}`).")

        def check(project: Path, _source: str) -> None:
            source = fork_live_audit_source(project)
            for token, hint in EVERY_TICK_TOKENS:
                self._assert_present(
                    source, re.escape(token),
                    f"{project}: falta {hint} (`{token}`) en "
                    "manager_node_runtime/live_audit_tester.py. Portarlo desde "
                    "mt5_manager/live_audit_tester.py: sin él las auditorías de "
                    "este agente corren con el `UseEveryTick` que traiga el set "
                    "guardado. Hay que reiniciar la aplicación del agente.",
                )

        self._assert_on_every_fork(check, "cada tick marcado en los sets auditados")


if __name__ == "__main__":
    unittest.main()
