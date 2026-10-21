"""Paridad de la comprobacion manual con la copia de cada agente.

Vive en su propio fichero y no en `test_node_runtime_fork_parity.py` porque
aquel ya rozaba las 600 lineas: la lista de criterios crece con cada port.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from .node_runtime_parity_base import MANAGER_ROOT, NodeRuntimeForkParityBase, fork_node_source
except ImportError:
    from node_runtime_parity_base import MANAGER_ROOT, NodeRuntimeForkParityBase, fork_node_source


class ManualCheckForkParityTests(NodeRuntimeForkParityBase):
    def _assert_node_route(self, project: Path) -> None:
        """La ruta que el manager reenvía tiene que existir en el nodo."""
        self._assert_present(
            fork_node_source(project),
            re.escape("/api/v1/jobs/manual-check"),
            f"{project}: falta la ruta /api/v1/jobs/manual-check en "
            "manager_node_runtime/node.py. Portar la comprobación manual "
            "(manual_check.py, `start_manual_check` de node_jobs_start.py, las "
            "etapas `manual_check_*` del pipeline y la cola) y duplicar la prueba "
            "en tests/test_manager_node_manual_check.py. Hay que reiniciar la "
            "aplicación del agente.",
        )

    def _assert_runtime_module(self, project: Path) -> None:
        """El criterio que el runtime del agente tiene que seguir."""
        module = project / "manager_node_runtime" / "manual_check.py"
        self.assertTrue(
            module.is_file(),
            msg=f"{project}: falta manager_node_runtime/manual_check.py.",
        )
        source = module.read_text(encoding="utf-8", errors="replace")
        for name in ("build_manual_check_command", "prepare_manual_check"):
            self._assert_present(
                source, rf"def {name}\b",
                f"{project}: falta `{name}` en manager_node_runtime/manual_check.py.",
            )
        # Los dos modelos del tester son el contrato con la pantalla: OHLC
        # M1 es `--model 1` y tick a tick `--model 4`. Si una copia los
        # cambia, el mismo botón mide cosas distintas en cada agente.
        self._assert_present(
            source, r'"ohlc": "1"',
            f"{project}: el modelo OHLC de la comprobación manual no es `1`.",
        )
        self._assert_present(
            source, r'"tick": "4"',
            f"{project}: el modelo tick a tick de la comprobación manual no es `4`.",
        )
        # El set que MT5 abre lo escribe el agente, así que la marca de cada
        # tick se pone ahí. Sin ella las dos corridas pueden mirar lo mismo
        # por dentro y la pantalla compara dos informes que no se diferencian
        # en lo que dice comparar.
        self._assert_present(
            source, r'"UseEveryTick", "true"',
            f"{project}: los sets de la comprobación manual no salen con "
            "`UseEveryTick=true`. Portar `_force_set_value` y sus dos "
            "llamadas de `_write_check_sets`. Hay que reiniciar la "
            "aplicación del agente.",
        )
        tests = project / "tests" / "test_manager_node_manual_check.py"
        self.assertTrue(
            tests.is_file(),
            msg=(
                f"{project}: falta tests/test_manager_node_manual_check.py; "
                "el port no está terminado sin su prueba en el proyecto del agente."
            ),
        )

    def _assert_retries(self, project: Path) -> None:
        """Los reintentos de lo que quede sin informe.

        Quien ve el código de salida de cada corrida del tester es el agente,
        así que el manager sólo manda el número. Sin esto, un bloqueo de MT5
        en **una** estrategia sigue tumbando la comprobación entera, que es
        justo lo que pasó con el #62 el 2026-10-21.
        """
        source = (project / "manager_node_runtime" / "manual_check.py").read_text(
            encoding="utf-8", errors="replace",
        )
        self._assert_present(
            source, r"def pending_set_files\b",
            f"{project}: falta `pending_set_files` en manual_check.py; sin ella "
            "un reintento repetiría también lo que ya dejó informe.",
        )
        self._assert_present(
            source, r"DEFAULT_RETRIES = 3",
            f"{project}: los reintentos por defecto de la comprobación manual "
            "no son 3. Hay que reiniciar la aplicación del agente.",
        )
        self._assert_present(
            fork_node_source(project), r"def _retry_manual_check\b",
            f"{project}: falta `_retry_manual_check` en el pipeline del nodo: "
            "la etapa no se reintenta y un bloqueo de MT5 en una estrategia "
            "sigue tumbando la comprobación entera.",
        )

    def test_manual_check_reaches_every_reachable_fork(self) -> None:
        # La comprobación manual no tiene nada en `mt5_manager/node.py`: el
        # manager decide qué estrategias son nuevas y reenvía la petición.
        # Quien copia los `.set`, llama a `run_tests.py` dos veces —una por
        # tipo de datos— y deja los informes con sus imágenes en
        # `runtime/<nodo>/manual_checks/` es el runtime del agente. Sin el
        # port, el botón «Comprobación manual» devuelve el 404 del nodo.
        forwarded = (MANAGER_ROOT / "mt5_manager" / "manager_portfolio_routes.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/api/v1/jobs/manual-check"', forwarded)

        def check(project: Path, _source: str) -> None:
            self._assert_node_route(project)
            self._assert_runtime_module(project)
            self._assert_retries(project)

        self._assert_on_every_fork(check, "la comprobación manual")


if __name__ == "__main__":
    unittest.main()
