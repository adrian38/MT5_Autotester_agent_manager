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
        fork_node_source, module_source,
    )
except ImportError:
    from tests.node.node_runtime_parity_base import (
        MANAGER_ROOT, NodeRuntimeForkParityBase, fork_live_account_source,
        fork_node_source, module_source,
    )


class AccountSnapshotForkParityTests(NodeRuntimeForkParityBase):
    def test_mt4_terminals_are_found_and_closed_on_every_capable_fork(self) -> None:
        """`terminal.exe` no puede quedar fuera del cierre pensado para MT5."""
        process_token = "name='terminal64.exe' OR name='terminal.exe'"
        self.assertIn(process_token, module_source("live_audit_processes"))
        mt4_tokens = (
            "launched = _launch(controller, profile, config_path)",
            "controller._close_terminal_pids_gracefully(launched)",
        )
        for token in mt4_tokens:
            self.assertIn(token, module_source("live_account_mt4"))

        def check(project: Path, _source: str) -> None:
            runtime = project / "manager_node_runtime"
            processes = (runtime / "live_audit_processes.py").read_text(
                encoding="utf-8", errors="replace",
            )
            self.assertIn(process_token, processes, f"{project}: MT4 no entra en el cierre")
            mt4 = runtime / "live_account_mt4.py"
            if not mt4.is_file():
                return
            source = mt4.read_text(encoding="utf-8", errors="replace")
            for token in mt4_tokens:
                self.assertIn(token, source, f"{project}: MT4 queda abierto al terminar")

        self._assert_on_every_fork(check, "cerrar la terminal MT4 del sondeo")

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
        manager_snapshot = module_source("live_account_snapshot")
        for token in tokens:
            self.assertIn(token, manager_snapshot, f"El manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            for token in tokens:
                self._assert_present(
                    fork_live_account_source(project), re.escape(token),
                    f"{project}: el sondeo de cuentas sigue publicando una lectura "
                    "cuyo historial no llegó a bajar. Portar `_require_loaded_history` "
                    "desde mt5_manager/live_account/live_account_snapshot.py.",
                )

        self._assert_on_every_fork(check, "no publicar una lectura sin historial")

    def test_the_snapshot_can_be_stopped_on_every_fork(self) -> None:
        # El botón vive en las tres pantallas de real, pero quien para es el
        # agente: el manager sólo reenvía. Sin el port, «Detener» contesta el
        # 404 del nodo y el sondeo sigue dentro de las terminales.
        #
        # Y para es **entre cuentas**: dentro de una hay un login y una
        # exportación de MT5, así que la marca se mira al empezar cada cuenta y
        # la tanda sale por `_finish_account_snapshot`, que es lo que devuelve
        # cada terminal a su cuenta final y reanuda el pipeline pausado. Un
        # port que matase el hilo dejaría justo lo contrario.
        tokens = (
            "def stop_account_snapshot",
            "def _honour_snapshot_stop",
            "self._honour_snapshot_stop()",
            "self.snapshot_stop_requested = False",
            "class SnapshotStopped",
            "except SnapshotStopped as exc:",
        )
        manager_snapshot = module_source("live_account_snapshot")
        for token in tokens:
            self.assertIn(token, manager_snapshot, f"El manager perdió `{token}`.")
        # Lo que la pantalla llama: la ruta del agente y la capacidad que la
        # anuncia. Van en `node*.py`, no en el módulo del sondeo.
        routes = ("/api/v1/live-accounts/snapshot/stop", "live_account_snapshot_stop")

        def check(project: Path, _source: str) -> None:
            source = fork_live_account_source(project)
            for token in tokens:
                self._assert_present(
                    source, re.escape(token),
                    f"{project}: el sondeo de cuentas no se puede detener. Portar "
                    "mt5_manager/live_account/live_account_snapshot.py entero "
                    "(cambiando sólo el import de `common`) y reiniciar la "
                    "aplicación del agente.",
                )
            node = (Path(project) / "manager_node_runtime" / "node.py").read_text(
                encoding="utf-8", errors="replace",
            ) + fork_node_source(project)
            for token in routes:
                self._assert_present(
                    node, re.escape(token),
                    f"{project}: falta `{token}` en manager_node_runtime/. Sin la "
                    "ruta, «Detener» contesta el 404 del nodo; sin la capacidad, "
                    "el manager no puede decir que el agente aún no sabe pararlo.",
                )

        self._assert_on_every_fork(check, "detener el sondeo de cuentas")
