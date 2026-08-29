"""Paridad entre la regla del manager y la copia bifurcada que corre el agente.

`manager_node_runtime/portfolio_save.py` del agente reimplementa las reglas de
exclusión de `PortfolioSource`, con otros nombres de función. Cambiar solo el lado
del manager no tiene efecto para el usuario, y ese fallo se ha repetido tres veces
(pausa/reanudación, exclusión el 2026-07-20, exclusión múltiple mensual el
2026-08-09). Estas pruebas convierten el olvido en un fallo mecánico.

Alcance: solo comprueban las copias **presentes en este equipo**. Las unidades de
los otros brokers no están montadas casi nunca, así que su copia se informa como
omitida en lugar de fingir que está verificada.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

MANAGER_ROOT = Path(__file__).parents[1]
MANAGER_RULES = MANAGER_ROOT / "mt5_manager" / "portfolio_service.py"

# Copias conocidas, en el orden de `ai_context/node_runtime_is_forked_per_agent.md`.
# La primera es el nodo de ICTrading de este equipo, único destino que el
# invariante de la rama `dev` autoriza a escribir.
FORK_CANDIDATES = (
    Path(r"C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent"),
    Path(r"F:\TRADING\MT5_Autotester_agent_AXI"),
    Path(r"I:\TRADING\MT5_Autotester_agent_IC"),
    Path(r"G:\TRADING\MT5_Autotester_agent"),
)


def _reachable_forks() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for project in FORK_CANDIDATES:
        rules = project / "manager_node_runtime" / "portfolio_save.py"
        try:
            if rules.is_file():
                found.append((project, rules.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            # Unidad montada pero inaccesible: se trata como ausente.
            continue
    return found


class NodeRuntimeForkParityTests(unittest.TestCase):
    """Cada prueba compara una regla concreta, no el fichero entero.

    Las copias divergen a propósito (el agente notifica por Telegram, el manager
    no), así que un `diff` completo sería ruido permanente. Lo que no puede
    divergir es el criterio de negocio.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.manager_source = MANAGER_RULES.read_text(encoding="utf-8")
        cls.forks = _reachable_forks()

    def _assert_absent(self, source: str, pattern: str, message: str) -> None:
        # No usar assertNotRegex: volcaría el fichero completo del agente en el
        # fallo y taparía la instrucción de qué hay que portar.
        if re.search(pattern, source):
            self.fail(message)

    def _assert_present(self, source: str, pattern: str, message: str) -> None:
        if not re.search(pattern, source):
            self.fail(message)

    def _assert_on_every_fork(self, check, description: str) -> None:
        if not self.forks:
            self.skipTest(
                "Ninguna copia de manager_node_runtime/ es alcanzable en este equipo; "
                f"buscadas: {', '.join(str(path) for path in FORK_CANDIDATES)}"
            )
        for project, source in self.forks:
            with self.subTest(fork=str(project)):
                check(project, source)
        missing = [str(path) for path in FORK_CANDIDATES if not (path / "manager_node_runtime").is_dir()]
        if missing:
            print(
                f"\n[paridad] {description}: copias no verificadas por no estar montadas: "
                f"{', '.join(missing)}"
            )

    def test_the_manager_still_owns_the_rules_this_parity_check_tracks(self) -> None:
        # Si alguien renombra o reescribe el lado del manager, las pruebas de abajo
        # dejarían de comparar nada sin avisar. Esta ancla lo impide.
        self.assertIn("def remove_member_to_quarantine", self.manager_source)
        self.assertIn("def remove_members_to_quarantine", self.manager_source)
        self.assertIn('if not (is_bundle or scope == "monthly"):', self.manager_source)
        self.assertIn("def _quarantine_member", self.manager_source)
        self.assertIn("def _apply_candidate_verdict", self.manager_source)
        self.assertIn("def _assert_node_applied_verdict", self.manager_source)
        self.assertIn("def requalify_strategy", self.manager_source)
        self.assertIn("def _requalify_on_node", self.manager_source)
        self.assertIn("def write_needs_node", self.manager_source)
        self.assertIn("def _supported_dataclass_values", self.manager_source)

    def test_run_history_pagination_reaches_every_reachable_fork(self) -> None:
        manager_node = (MANAGER_ROOT / "mt5_manager" / "node.py").read_text(encoding="utf-8")
        for token in ('limit ? offset ?', '"pagination": {', '"next_offset"', 'query.get("offset"'):
            self.assertIn(token, manager_node, f"El nodo fuente del manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            node_path = project / "manager_node_runtime" / "node.py"
            node_source = node_path.read_text(encoding="utf-8", errors="replace")
            for token in ('limit ? offset ?', '"pagination": {', '"next_offset"', 'query.get("offset"'):
                self.assertIn(
                    token,
                    node_source,
                    msg=(
                        f"{project}: falta `{token}` en manager_node_runtime/node.py; "
                        "sin el port el botón «Cargar más» no puede superar la primera página."
                    ),
                )
            tests = sorted((project / "tests").glob("test_manager_node_*pagination*.py"))
            self.assertTrue(
                tests,
                msg=f"{project}: falta una prueba propia de paginación del historial de runs.",
            )

        self._assert_on_every_fork(check, "paginación del historial de runs")

    def test_batch_exclusion_accepts_monthly_on_every_reachable_fork(self) -> None:
        def check(project: Path, source: str) -> None:
            self._assert_absent(
                source,
                r'multiple and not \(\s*scope == "full_history" and is_bundle\s*\)',
                f"{project}: la copia del agente sigue reservando la exclusión múltiple "
                "a los bundles A/M/C de full_history. Portar el criterio "
                '`is_bundle or scope == "monthly"` a '
                "manager_node_runtime/portfolio_save.py y duplicar la prueba en "
                "tests/test_manager_node_portfolio_save.py.",
            )
            self._assert_present(
                source,
                r'multiple and not \(\s*is_bundle or scope == "monthly"\s*\)',
                f"{project}: falta el criterio de exclusión múltiple del manager en "
                "manager_node_runtime/portfolio_save.py.",
            )

        self._assert_on_every_fork(check, "exclusión múltiple mensual")

    def test_no_fork_deletes_the_saved_portfolio_when_excluding(self) -> None:
        # Excluir decide sobre el pool y, si hay veredicto, sobre los estados del
        # agente. El portafolio guardado no es un efecto colateral de eso: antes
        # se borraba entero (bundle, mes o exclusión múltiple) o se le quitaba la
        # asignación y se recalculaban sus métricas.
        self._assert_absent(
            self.manager_source,
            r"self\.delete_portfolio\(portfolio_id, scope\)",
            "El manager volvió a borrar el portafolio al excluir un miembro.",
        )

        def check(project: Path, source: str) -> None:
            for pattern, hint in (
                (r"delete_whole", "el borrado completo del portafolio"),
                (r"_recalculate_saved_portfolio", "el recálculo del portafolio guardado"),
            ):
                self._assert_absent(
                    source,
                    pattern,
                    f"{project}: la copia del agente sigue con {hint} al excluir. "
                    "Portar la regla del manager (`PortfolioSource._quarantine_member`): "
                    "la exclusión no toca el portafolio guardado.",
                )

        self._assert_on_every_fork(check, "el portafolio guardado sobrevive a la exclusión")

    def test_user_facing_exclusion_messages_match_on_every_reachable_fork(self) -> None:
        # El texto del mensaje es lo único que une las dos copias: los nombres de
        # función difieren. Si el texto se desincroniza, se pierde el único hilo
        # que permite encontrar la copia del agente al buscar por síntoma.
        expected = {
            "Excluida manualmente desde un portafolio A/M/C guardado",
            "Excluida manualmente desde un Portafolio UBS mensual guardado",
        }
        for text in expected:
            self.assertIn(text, self.manager_source, f"El manager perdió el texto: {text}")

        def check(project: Path, source: str) -> None:
            for text in sorted(expected):
                self.assertIn(
                    text,
                    source,
                    msg=f"{project}: la copia del agente no comparte el texto «{text}».",
                )

        self._assert_on_every_fork(check, "textos de cuarentena")

    def test_the_verdict_reason_codes_reach_every_reachable_fork(self) -> None:
        # Excluir por degradación o por OHLC ≠ every tick no retira la estrategia
        # del portafolio: declara que falló y escribe estados en la memoria del
        # agente, de donde salen score y pesos. Un nodo sin portar aceptaría el
        # motivo y no escribiría nada, así que la pantalla prometería un cambio
        # que no ocurre. `verdict_applied` es la confirmación que exige el manager.
        def check(project: Path, source: str) -> None:
            for token, hint in (
                ("reason_code", "el motivo de exclusión"),
                ("verdict_applied", "la confirmación del veredicto"),
                ("restore_json", "el respaldo que permite reintegrar"),
                ("mark_candidate_robustness", "el veredicto de degradación"),
                ("mark_candidate_final_tick", "el veredicto de Final Tick 6M"),
            ):
                self._assert_present(
                    source,
                    re.escape(token),
                    f"{project}: falta {hint} (`{token}`) en "
                    "manager_node_runtime/portfolio_save.py. Portar el cambio desde "
                    "mt5_manager/candidate_verdict.py y duplicar la prueba en "
                    "tests/test_manager_node_portfolio_save.py.",
                )

        self._assert_on_every_fork(check, "veredicto de exclusión")

    def test_changing_the_state_of_an_excluded_strategy_reaches_every_reachable_fork(self) -> None:
        # El manager no puede escribir la memoria de un nodo remoto: sobre CIFS o
        # sobre un bind mount de Docker, abrirla en modo WAL falla con «disk I/O
        # error» porque no hay `-shm` que la respalde. Por eso el cambio de estado
        # se delega al nodo, como ya se delegaban la exclusión y el borrado. Un
        # nodo sin portar devuelve 404 y el manager dice qué falta, pero el botón
        # no funciona hasta que la copia del agente tenga las dos piezas.
        def check(project: Path, source: str) -> None:
            self._assert_present(
                source,
                r"def requalify_portfolio_member_payload",
                f"{project}: falta `requalify_portfolio_member_payload` en "
                "manager_node_runtime/portfolio_save.py. Portar el orden de "
                "`PortfolioSource.requalify_strategy` (deshacer el veredicto vigente, "
                "fotografiar el estado restaurado, aplicar el nuevo) y duplicar la prueba "
                "en tests/test_manager_node_portfolio_save.py.",
            )
            node_runtime = project / "manager_node_runtime" / "node.py"
            try:
                node_source = node_runtime.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self.fail(f"{project}: no se puede leer {node_runtime}")
            self._assert_present(
                node_source,
                re.escape("/api/v1/portfolios/requalify"),
                f"{project}: falta la ruta /api/v1/portfolios/requalify en "
                "manager_node_runtime/node.py. Sin ella el manager recibe 404 y el botón "
                "«Cambiar estado» no funciona en ese agente. Hay que reiniciar la "
                "aplicación del agente después de portarla.",
            )
            # Paso 3 del procedimiento: el port no está terminado sin su prueba.
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "requalify" in path.read_text(encoding="utf-8", errors="replace").lower()
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre el cambio de estado; "
                    "duplicar allí la cobertura de requalify_portfolio_member_payload."
                ),
            )

        self._assert_on_every_fork(check, "cambiar el estado de una estrategia excluida")

    def test_the_verdict_texts_match_on_every_reachable_fork(self) -> None:
        expected = {
            "Excluida por degradación: rechazada en el test de robustez",
            "Excluida porque el OHLC no se parece al every tick: rechazada en Final Tick 6M",
        }
        manager_texts = (MANAGER_ROOT / "mt5_manager" / "candidate_verdict.py").read_text(encoding="utf-8")
        for text in expected:
            self.assertIn(text, manager_texts, f"El manager perdió el texto: {text}")

        def check(project: Path, source: str) -> None:
            for text in sorted(expected):
                self.assertIn(
                    text,
                    source,
                    msg=f"{project}: la copia del agente no comparte el texto «{text}».",
                )

        self._assert_on_every_fork(check, "textos del veredicto")

    def test_every_reachable_fork_keeps_its_own_manager_node_test(self) -> None:
        # Paso 3 del procedimiento de `ai_context/node_runtime_is_forked_per_agent.md`:
        # el port no está terminado sin su prueba en el proyecto del agente.
        def check(project: Path, _source: str) -> None:
            tests = sorted((project / "tests").glob("test_manager_node_*.py"))
            self.assertTrue(
                tests,
                msg=f"{project}: no hay ninguna prueba tests/test_manager_node_*.py que cubra el nodo.",
            )
            monthly = [
                path for path in tests
                if re.search(r"monthly", path.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE)
            ]
            self.assertTrue(
                monthly,
                msg=(
                    f"{project}: ninguna prueba del nodo menciona el ámbito mensual; "
                    "duplicar allí la cobertura de la exclusión múltiple mensual."
                ),
            )

        self._assert_on_every_fork(check, "pruebas del nodo en el agente")

    def test_optional_repair_regression_reaches_every_reachable_fork(self) -> None:
        # La etapa regresiva del flujo de Reparar solo existe en la copia del agente:
        # `mt5_manager/node.py` nunca la programó, así que aquí el manager no es la
        # referencia del criterio, solo el emisor de la casilla. Un nodo sin portar
        # acepta la petición, ignora `run_regression` y ejecuta la regresiva igual:
        # el usuario desmarca la casilla y no pasa nada. No hay 404 que lo delate.
        script = (MANAGER_ROOT / "mt5_manager" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("run_regression: runRegression", script)
        self.assertIn("repair_run_regression", script)

        def check(project: Path, _source: str) -> None:
            node_source = (project / "manager_node_runtime" / "node.py").read_text(
                encoding="utf-8", errors="replace"
            )
            self._assert_present(
                node_source,
                re.escape('payload["run_regression"] = bool(payload.get("run_regression", True))'),
                f"{project}: `_normalize_repair` no lee `run_regression`, así que la casilla "
                "«Prueba regresiva» del diálogo de Reparar no hace nada en ese agente. "
                "Portar el cambio a manager_node_runtime/node.py y duplicar la prueba en "
                "tests/test_manager_node_regression.py.",
            )
            self._assert_present(
                node_source,
                re.escape('if run_regression and run_modes[run_id] == "production":'),
                f"{project}: el flujo de Reparar sigue añadiendo la regresiva a todo run de "
                "producción sin consultar la casilla del diálogo.",
            )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "run_regression" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre `run_regression` en Reparar; "
                    "duplicar allí la cobertura de la casilla opcional."
                ),
            )

        self._assert_on_every_fork(check, "prueba regresiva opcional en Reparar")

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

    def test_the_auditor_leaves_the_configured_account_on_every_ported_fork(self) -> None:
        # El auditor real activa la cuenta real con `initialize(login=...)` y MT5
        # recuerda la última cuenta de cada terminal. Sin restaurar, el pipeline
        # reanudado seguiría probando cada estrategia contra la cuenta real. El
        # proceso que ejecuta esto es `manager_node_runtime/live_audit.py`, no la
        # copia de referencia del manager.
        manager_engine = (MANAGER_ROOT / "mt5_manager" / "live_audit_engine.py").read_text(encoding="utf-8")
        manager_node = (MANAGER_ROOT / "mt5_manager" / "node.py").read_text(encoding="utf-8")
        self.assertIn('"live_audit_restore_account": True', manager_node)
        for token in (
            "def _restore_tester_login",
            "def _remember_real_account_terminal",
            "def _close_terminal_pids_gracefully",
            "remember_for=str(request[\"audit_key\"])",
            'request["restore_login"]',
            'request["restore_password"]',
            'request["restore_server"]',
            '"finalizing"',
        ):
            self.assertIn(token, manager_engine, f"El manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            engine = project / "manager_node_runtime" / "live_audit.py"
            if not engine.is_file():
                # El auditor real solo se ha portado a ICTrading; una copia sin el
                # módulo no ha recibido la función, no una regresión que ocultar.
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
                source,
                r"finally:\s*\n\s*if paused_by_auditor:",
                f"{project}: la copia del agente reanuda el pipeline sin restaurar antes la "
                "cuenta de pruebas del terminal.",
            )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "_restore_tester_login" in path.read_text(encoding="utf-8", errors="replace")
                or "terminal_restore" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre en qué cuenta queda el terminal; "
                    "duplicar allí la cobertura de `terminal_restore`."
                ),
            )

        self._assert_on_every_fork(check, "cuenta que queda en el terminal tras auditar")

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


if __name__ == "__main__":
    unittest.main()
