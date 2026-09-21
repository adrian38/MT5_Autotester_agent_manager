"""Parity checks for node lifecycle and live-audit runtime behavior."""
from __future__ import annotations

import re
from pathlib import Path

try:
    from .node_runtime_parity_base import (
        FORK_CANDIDATES, MANAGER_ROOT, NodeRuntimeForkParityBase,
    )
except ImportError:
    from node_runtime_parity_base import (
        FORK_CANDIDATES, MANAGER_ROOT, NodeRuntimeForkParityBase,
    )


class NodeRuntimeForkParityLiveTests(NodeRuntimeForkParityBase):
    def test_stop_without_the_lock_reaches_every_reachable_fork(self) -> None:
        # Quien retiene el bloqueo es el nodo del agente, así que ahí es donde
        # detener se quedaba esperando. Un nodo sin portar acepta el POST, tarda
        # minutos en atenderlo y deja el trabajo corriendo: la pantalla dice que
        # falló y el pipeline sigue. No hay 404 que lo delate.
        manager_node = (MANAGER_ROOT / "mt5_manager" / "node.py").read_text(encoding="utf-8")
        for token in (
            "self.stop_requested = True",
            "self.lock.acquire(timeout=CONTROL_LOCK_TIMEOUT)",
            "def _honour_stop_request",
        ):
            self.assertIn(token, manager_node, f"El manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            node_source = (project / "manager_node_runtime" / "node.py").read_text(
                encoding="utf-8", errors="replace"
            )
            for token, hint in (
                ("self.stop_requested = True", "la petición de parada fuera del bloqueo"),
                (
                    "self.lock.acquire(timeout=CONTROL_LOCK_TIMEOUT)",
                    "la espera acotada por el bloqueo",
                ),
                ("def _honour_stop_request", "el cierre del pipeline entre etapas"),
                (
                    "if self.stop_requested or self.pause_requested:",
                    "la comprobación dentro del bucle que descarta etapas",
                ),
            ):
                self._assert_present(
                    node_source,
                    re.escape(token),
                    f"{project}: falta {hint} (`{token}`) en manager_node_runtime/node.py. "
                    "Portar el cambio desde mt5_manager/node.py: sin él, «Detener» no "
                    "hace nada mientras el pipeline descarta etapas sin pendientes, que "
                    "en una reparación de cien runs son minutos seguidos. Duplicar la "
                    "prueba en tests/test_manager_node_stop.py y reiniciar la aplicación "
                    "del agente.",
                )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "stop_requested" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre detener con el bloqueo "
                    "ocupado; duplicar allí la cobertura de `stop_requested`."
                ),
            )

        self._assert_on_every_fork(check, "detener sin depender del bloqueo")

    def test_application_restart_reaches_every_embedded_node_fork(self) -> None:
        manager_node = (MANAGER_ROOT / "mt5_manager" / "node.py").read_text(encoding="utf-8")
        self.assertIn("/api/v1/application/restart", manager_node)
        self.assertIn("application_restart", manager_node)

        def check(project: Path, _source: str) -> None:
            node_source = (project / "manager_node_runtime" / "node.py").read_text(
                encoding="utf-8", errors="replace"
            )
            lifecycle_source = (project / "manager_node_lifecycle.py").read_text(
                encoding="utf-8", errors="replace"
            )
            lifecycle_tests = (project / "tests" / "test_manager_node_lifecycle.py").read_text(
                encoding="utf-8", errors="replace"
            )
            for token in (
                "/api/v1/application/restart",
                "application_restart",
                "request_application_restart",
            ):
                self.assertIn(token, node_source, msg=f"{project}: falta `{token}` en el nodo real")
            for token in (
                "restart_callback",
                "consume_restart_request",
                "sync_origin_before_relaunch",
                "git pull --ff-only origin",
                "git push origin",
                "relaunch_application",
            ):
                self.assertIn(token, lifecycle_source, msg=f"{project}: falta `{token}` en el ciclo de vida")
            self.assertIn(
                "/api/v1/application/restart",
                lifecycle_tests,
                msg=f"{project}: el reinicio completo no tiene prueba en el proyecto del agente",
            )

        self._assert_on_every_fork(check, "reinicio completo de la aplicacion")

    def _assert_manager_auditor_restore_contract(self) -> None:
        manager_engine = (MANAGER_ROOT / "mt5_manager" / "live_audit_engine.py").read_text(encoding="utf-8")
        manager_node = (MANAGER_ROOT / "mt5_manager" / "node.py").read_text(encoding="utf-8")
        self.assertIn('"live_audit_restore_account": True', manager_node)
        for token in (
            "def _restore_tester_login",
            "def _remember_real_account_terminal",
            "def _tester_terminal_pool",
            '"tester_execution"',
            '"workers": str(workers)',
            "def _close_terminal_pids_gracefully",
            "remember_for=str(request[\"audit_key\"])",
            'request["restore_login"]',
            'request["restore_password"]',
            'request["restore_server"]',
            '"finalizing"',
        ):
            self.assertIn(token, manager_engine, f"El manager perdió `{token}`.")

    def _check_auditor_restore_on_fork(self, project: Path, _source: str) -> None:
        engine = project / "manager_node_runtime" / "live_audit.py"
        if not engine.is_file():
            print(f"\n[paridad] auditor real: {project} no tiene manager_node_runtime/live_audit.py")
            return
        source = engine.read_text(encoding="utf-8", errors="replace")
        node_source = (project / "manager_node_runtime" / "node.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn(
            '"live_audit_restore_account": True', node_source,
            f"{project}: el runtime nuevo no anuncia al manager la restauración independiente.",
        )
        for token, hint in (
            ("def _restore_tester_login", "la restauración de la cuenta de pruebas"),
            ("def _remember_real_account_terminal", "el registro de terminales con la cuenta real"),
            ("def _tester_terminal_pool", "el reparto del tester entre terminales habilitadas"),
            ('"tester_execution"', "la evidencia del modo y del pool de terminales ejecutado"),
            ('"workers": str(workers)', "el número efectivo de terminales del tester"),
            ("def _close_terminal_pids_gracefully", "el cierre que deja a MT5 guardar la cuenta"),
            ('request["restore_login"]', "el login final independiente de la cuenta tester"),
            ('request["restore_password"]', "la credencial final independiente de la cuenta tester"),
            ('request["restore_server"]', "el servidor final independiente de la cuenta tester"),
            ('"finalizing"', "el estado que impide publicar un fin antes de restaurar las terminales"),
        ):
            self._assert_present(
                source,
                re.escape(token),
                f"{project}: falta {hint} (`{token}`) en manager_node_runtime/live_audit.py. "
                "Portar el cambio desde mt5_manager/live_audit_engine.py: sin él el "
                "terminal se queda en la cuenta real y el siguiente backtest del "
                "pipeline no usa la cuenta demo de pruebas.",
            )
        self._assert_absent(
            source, r"finally:\s*\n\s*if paused_by_auditor:",
            f"{project}: la copia del agente reanuda el pipeline sin restaurar antes la "
            "cuenta de pruebas del terminal.",
        )
        covered = [
            path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
            if "_restore_tester_login" in path.read_text(encoding="utf-8", errors="replace")
            or "terminal_restore" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertTrue(covered, msg=(
            f"{project}: ninguna prueba del nodo cubre en qué cuenta queda el terminal; "
            "duplicar allí la cobertura de `terminal_restore`."
        ))

    def test_the_auditor_leaves_the_configured_account_on_every_ported_fork(self) -> None:
        self._assert_manager_auditor_restore_contract()
        self._assert_on_every_fork(
            self._check_auditor_restore_on_fork,
            "cuenta que queda en el terminal tras auditar",
        )

    def test_ictrading_live_auditor_has_calendar_boundaries_and_effective_lot_rules(self) -> None:
        manager_engine = (MANAGER_ROOT / "mt5_manager" / "live_audit_engine.py").read_text(encoding="utf-8")
        ic_project = FORK_CANDIDATES[0]
        ic_engine_path = ic_project / "manager_node_runtime" / "live_audit.py"
        if not ic_engine_path.is_file():
            self.skipTest(f"La copia ICTrading no está montada: {ic_engine_path}")
        ic_engine = ic_engine_path.read_text(encoding="utf-8", errors="replace")
        for token in (
            "def _audit_period",
            "def _effective_price_tolerance",
            "def _pnl_comparison",
            '"pnl_policy": "adverse_shortfall_only"',
            '"pnl_adverse_delta"',
            '"indices": 10.5',
            '"gold": 2.05',
            '"silver": 0.02',
            '"jpy_fx": 0.05',
            '"fx": 0.0005',
            'period_mode == "fixed_dates"',
            "datetime.min.time()",
            "datetime.max.time()",
            "tester_lot = max(portfolio_lot, volume_min)",
            "configured_lot_below_broker_minimum",
            "lot_matches_effective_lot",
        ):
            self.assertIn(token, manager_engine, f"El manager perdió `{token}`.")
            self.assertIn(token, ic_engine, f"ICTrading no ejecutará la regla `{token}`.")

    def test_every_reachable_fork_ignores_fields_from_a_newer_manager(self) -> None:
        # El manager manda la tanda de riesgo por equity (`max_balance_dd_001`,
        # `max_equity_dd_001`, DD flotante, rendimiento reciente, rutas de informe)
        # y los campos de auditoría del resultado. El `portfolio_manager/ubs_portfolio.py`
        # de cada agente es una generación anterior y no los declara: con
        # `StrategyAllocation(**item)` el nodo moría con `unexpected keyword argument`,
        # devolvía un 500 con la traza en su consola y solo guardaba en el segundo
        # POST, el del reintento con `legacy_compatible_portfolio_save_payload`.
        # El reintento del manager es la red, no el arreglo: cada guardado dejaba
        # una traza que parecía una caída.
        self._assert_present(
            self.manager_source,
            r"StrategyAllocation\(\*\*_supported_dataclass_values\(",
            "El manager dejó de tolerar campos desconocidos al reconstruir las "
            "asignaciones; sin eso esta paridad no compara nada.",
        )

        def check(project: Path, source: str) -> None:
            for dataclass_name in (
                "StrategyAllocation",
                "OptimizationDecision",
                "UnusedSetInfo",
                "BootstrapDrawdownAnalysis",
                "PortfolioResult",
            ):
                self._assert_absent(
                    source,
                    rf"{dataclass_name}\(\*\*(?:item|stress|result_values)\)",
                    f"{project}: `_deserialize_proposals` sigue construyendo "
                    f"{dataclass_name} con el diccionario crudo del manager. "
                    "Portar `_supported_dataclass_values` de "
                    "mt5_manager/portfolio_service.py a "
                    "manager_node_runtime/portfolio_save.py: sin él, cada guardado "
                    "deja un TypeError y una traza en la consola del agente antes "
                    "de que el manager reintente con el payload heredado.",
                )
            self._assert_present(
                source,
                r"def _supported_dataclass_values",
                f"{project}: falta `_supported_dataclass_values` en "
                "manager_node_runtime/portfolio_save.py.",
            )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "max_balance_dd_001" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre un payload de un manager "
                    "más nuevo; duplicar allí la cobertura del filtro de campos."
                ),
            )

        self._assert_on_every_fork(check, "campos nuevos del manager en el guardado")

    def test_optional_cli_values_are_omitted_instead_of_stringified_on_every_fork(self) -> None:
        # `_add` es quien construye la línea de comandos de ubs_agent.py, y quien
        # la ejecuta es el nodo del agente. Con `str(None)` la semilla vacía se
        # convertía en `--random-seed None` y argparse mataba la generación con
        # código 2 antes de crear un solo candidato (2026-08-17, run #124 de
        # ICTrading). Arreglarlo solo aquí no habría cambiado nada para el usuario.
        manager_node = (MANAGER_ROOT / "mt5_manager" / "node.py").read_text(encoding="utf-8")
        self._assert_present(
            manager_node,
            r"def _add\(.*?\n(?:\s*#.*\n)*\s*if value is None:\s*\n\s*return",
            "El manager volvió a convertir un valor opcional en el texto \"None\" "
            "al construir la orden de ubs_agent.py.",
        )

        def check(project: Path, _source: str) -> None:
            node_source = (project / "manager_node_runtime" / "node.py").read_text(
                encoding="utf-8", errors="replace"
            )
            self._assert_present(
                node_source,
                r"def _add\(.*?\n(?:\s*#.*\n)*\s*if value is None:\s*\n\s*return",
                f"{project}: `_add` de manager_node_runtime/node.py sigue pasando "
                "el texto \"None\" como valor. Portar la guarda del manager "
                "(`if value is None: return`): sin ella, dejar la semilla "
                "reproducible vacía hace fallar la generación con código 2.",
            )

        self._assert_on_every_fork(check, "valores opcionales de la orden de ubs_agent.py")
