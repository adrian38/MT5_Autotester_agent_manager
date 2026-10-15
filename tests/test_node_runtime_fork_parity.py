"""Parity between manager rules and each reachable forked node runtime."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from .node_runtime_parity_base import (
        FORK_CANDIDATES, MANAGER_ROOT, NodeRuntimeForkParityBase,
        fork_node_source, manager_node_source,
    )
except ImportError:
    from node_runtime_parity_base import (
        FORK_CANDIDATES, MANAGER_ROOT, NodeRuntimeForkParityBase,
        fork_node_source, manager_node_source,
    )


class NodeRuntimeForkParityTests(NodeRuntimeForkParityBase):
    def test_ictrading_executes_portfolio_alias_writes_in_its_real_runtime(self) -> None:
        """The manager endpoint is insufficient: IC owns and writes its SQLite DB."""
        ic_project = FORK_CANDIDATES[0]
        ic_rules_path = ic_project / "manager_node_runtime" / "portfolio_save.py"
        ic_node_path = ic_project / "manager_node_runtime" / "node.py"
        if not ic_rules_path.is_file() or not ic_node_path.is_file():
            self.skipTest(f"La copia ICTrading no está montada: {ic_project}")
        # La ruta del alias vive en el modulo de rutas de portafolio desde que
        # manager.py se partio; lo que se comprueba es el criterio, no el fichero.
        manager_routes = (
            MANAGER_ROOT / "mt5_manager" / "manager_portfolio_routes.py"
        ).read_text(encoding="utf-8")
        ic_rules = ic_rules_path.read_text(encoding="utf-8", errors="replace")
        ic_node = ic_node_path.read_text(encoding="utf-8", errors="replace")
        ic_tests = (ic_project / "tests" / "test_manager_node_portfolio_save.py").read_text(
            encoding="utf-8", errors="replace"
        )

        self.assertIn('action == "alias"', manager_routes)
        self.assertIn("def set_portfolio_alias_payload", ic_rules)
        self.assertIn('"/api/v1/portfolios/alias"', ic_node)
        self.assertIn("test_alias_is_additional_editable_and_removable", ic_tests)

    """Cada prueba compara una regla concreta, no el fichero entero.

    Las copias divergen a propósito (el agente notifica por Telegram, el manager
    no), así que un `diff` completo sería ruido permanente. Lo que no puede
    divergir es el criterio de negocio.
    """

    def test_manual_portfolios_reach_every_reachable_fork(self) -> None:
        # «Solo portafolio» no tiene nada en `mt5_manager/node.py`: el manager
        # sólo reenvía el lote. Quien guarda los `.set`, registra la composición
        # en su memoria UBS y encola el histórico OHLC es el runtime del agente,
        # así que aquí el manager no es la referencia del criterio. Sin el port,
        # el botón «Crear portafolio» devuelve el 404 del nodo y «Generar
        # histórico OHLC» no tiene dónde ejecutarse.
        routes = (
            "/api/v1/portfolios/manual",
            "/api/v1/portfolios/manual-defaults",
            "/api/v1/portfolios/manual-history",
        )
        forwarded = (MANAGER_ROOT / "mt5_manager" / "manager_portfolio_routes.py").read_text(
            encoding="utf-8"
        )
        # Las dos últimas se arman con el nombre del endpoint, así que la cadena
        # literal no aparece: lo que se ancla es el reenvío, no su formato.
        self.assertIn('"/api/v1/portfolios/manual"', forwarded)
        self.assertIn('f"/api/v1/portfolios/{endpoint}"', forwarded)

        def check(project: Path, _source: str) -> None:
            node_source = fork_node_source(project)
            for route in routes:
                self._assert_present(
                    node_source,
                    re.escape(route),
                    f"{project}: falta la ruta {route} en manager_node_runtime/node.py. "
                    "Portar «Solo portafolio» (manual_portfolio.py, "
                    "manual_portfolio_history.py y la etapa `manual_history` de la cola) "
                    "y duplicar la prueba en tests/test_manager_node_manual_portfolio.py. "
                    "Hay que reiniciar la aplicación del agente.",
                )
            # La compactación de la cola (`_coalesce_manual_history_queue`) todavía
            # no llegó a RoboForex; está anotada como pendiente en
            # ai_context/node_runtime_is_forked_per_agent.md y se añade aquí en
            # cuanto se porte, no antes: una guarda roja de serie no avisa de nada.
            tests = project / "tests" / "test_manager_node_manual_portfolio.py"
            self.assertTrue(
                tests.is_file(),
                msg=(
                    f"{project}: falta tests/test_manager_node_manual_portfolio.py; "
                    "el port no está terminado sin su prueba en el proyecto del agente."
                ),
            )

        self._assert_on_every_fork(check, "portafolios manuales e histórico OHLC")

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
        self.assertIn("def resolve_pool_candidate", self.manager_source)
        self.assertIn("def pool_member_payload", self.manager_source)

    def test_run_history_pagination_reaches_every_reachable_fork(self) -> None:
        manager_node = manager_node_source()
        for token in ('limit ? offset ?', '"pagination": {', '"next_offset"', 'query.get("offset"'):
            self.assertIn(token, manager_node, f"El nodo fuente del manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            node_source = fork_node_source(project)
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

    def test_pool_exclusion_reaches_every_reachable_fork(self) -> None:
        # «Gestión por símbolo» excluye un set que no tiene por qué estar en
        # ningún portafolio. La copia sin portar aborta en su primera línea con
        # «Falta el portafolio que contiene las estrategias», así que no hay
        # forma de sacar del pool un set que ningún portafolio use: el usuario
        # tiene que esperar a que aparezca en uno. No hay 404 que lo delate, sólo
        # un 400 que pide un dato que esa pantalla no tiene.
        self._assert_present(
            self.manager_source,
            r'node_payload\["pool_member"\] = PortfolioSource\(node\)\.pool_member_payload',
            "El manager dejó de resolver el candidato antes de pedir al nodo una "
            "exclusión sin portafolio; sin eso el nodo no puede escribirla.",
        )

        def check(project: Path, source: str) -> None:
            self._assert_present(
                source,
                r'pool_member = payload\.get\("pool_member"\)',
                f"{project}: `exclude_portfolio_members_payload` sigue exigiendo un "
                "`portfolio_id`, así que «Gestión por símbolo» devuelve 400 en ese "
                "agente y no se puede excluir del pool un set que ningún portafolio "
                "use. Portar la rama `pool_member` desde "
                "manager_node_runtime/portfolio_save.py de una copia ya portada y "
                "duplicar la prueba en tests/test_manager_node_portfolio_save.py. "
                "Hay que reiniciar la aplicación del agente.",
            )
            self._assert_present(
                source,
                re.escape("Excluida manualmente desde la gestión por símbolo"),
                f"{project}: falta el texto de la exclusión de pool; es el único hilo "
                "que permite encontrar esta regla en la copia del agente.",
            )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "pool_member" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre la exclusión de pool; "
                    "duplicar allí la cobertura de `pool_member`."
                ),
            )

        self._assert_on_every_fork(check, "exclusión de un set fuera de todo portafolio")

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
                ("live_mismatch", "el motivo «no funciona en real con este bróker»"),
                ("mark_candidates", "el veredicto que rechaza el candidato entero"),
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
            node_source = fork_node_source(project)
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
            "Excluida porque no funciona en las condiciones reales de este bróker: candidato rechazado",
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
            node_source = fork_node_source(project)
            self._assert_present(
                node_source,
                re.escape('payload["run_regression"] = bool(payload.get("run_regression", True))'),
                f"{project}: `_normalize_repair` no lee `run_regression`, así que la casilla "
                "«Prueba regresiva» del diálogo de Reparar no hace nada en ese agente. "
                "Portar el cambio a manager_node_runtime/node.py y duplicar la prueba en "
                "tests/test_manager_node_regression.py.",
            )
            # Sin el `if ... :`: la condición vive dentro de la comprensión que
            # arma las etapas de cada run, y lo que no puede desaparecer es la
            # condición, no su formato.
            self._assert_present(
                node_source,
                re.escape('run_regression and run_modes[run_id] == "production"'),
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

    def test_two_phase_repair_reaches_every_reachable_fork(self) -> None:
        # Quien parte la reparación en dos fases es el nodo, y el nodo real es la
        # copia del agente. Sin portarlo, el manager manda
        # `repair_phase2_max_workers`, el agente lo ignora y sigue reparando en una
        # sola pasada: el campo nuevo del diálogo no haría nada y no hay 404 que lo
        # delate. La clave de etapa también tiene que llevar la fase; si no, la
        # segunda pasada pisa el código de retorno y el recuento de la primera.
        manager_node = manager_node_source()
        for token in (
            'payload.get("repair_phase2_max_workers")',
            "for phase, workers in enumerate(",
            'phase_part = f"phase_{phase}_" if phase is not None else ""',
        ):
            self.assertIn(token, manager_node, f"El manager perdió `{token}`.")

        def check(project: Path, _source: str) -> None:
            node_source = fork_node_source(project)
            for token, hint in (
                ('payload.get("repair_phase2_max_workers")', "el límite de terminales de la fase 2"),
                ("for phase, workers in enumerate(", "las dos fases del pipeline de reparación"),
                (
                    'phase_part = f"phase_{phase}_" if phase is not None else ""',
                    "la fase en la clave de etapa",
                ),
                ('self.state["current_phase"] = step.get("phase")', "la fase publicada al manager"),
            ):
                self._assert_present(
                    node_source,
                    re.escape(token),
                    f"{project}: falta {hint} (`{token}`) en manager_node_runtime/node.py. "
                    "Portar el cambio desde mt5_manager/node.py: sin él la reparación "
                    "sigue siendo una sola pasada y el campo «Terminales fase 2» del "
                    "diálogo no cambia nada. Duplicar la prueba en "
                    "tests/test_manager_node_repair_phases.py y reiniciar la aplicación "
                    "del agente.",
                )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "repair_phase2_max_workers" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre las dos fases de la "
                    "reparación; duplicar allí la cobertura del reparto de terminales."
                ),
            )

        self._assert_on_every_fork(check, "reparación en dos fases")

    def test_the_portfolio_detail_publishes_set_path_on_every_reachable_fork(self) -> None:
        # El detalle del nodo es lo único que el auditor tiene para localizar
        # cada `.set`. En un portafolio UBS da igual qué clave se publique:
        # `set_id` y `set_path` son la misma ruta `/data/<broker>/…`. En uno
        # manual no: `set_id` es el nombre pelado del fichero y la ruta vive
        # sólo en `set_path`. Un fork que proyecte `set_id` y `set_name` pero no
        # `set_path` entrega «DaytradePro_CHFJPY.set» y el auditor acaba en el
        # rglob por nombre de `resolve_portfolio_set`, que con homónimos
        # distintos se niega a elegir. No hay 404 ni traza que lo delate: la
        # auditoría falla en preparación con «Se encontraron varios sets
        # distintos para …: N coincidencias», que parece un problema de datos.
        token = '"set_path": str(raw.get("set_path") or raw.get("set_id") or "")'
        self.assertIn(
            token,
            manager_node_source(),
            f"El nodo fuente del manager dejó de publicar `set_path`: {token}",
        )

        def check(project: Path, _source: str) -> None:
            node_source = fork_node_source(project)
            self._assert_present(
                node_source,
                r'"set_path"\s*:\s*str\(raw\.get\("set_path"\) or raw\.get\("set_id"\) or ""\)',
                f"{project}: `portfolio_detail` no reexporta `set_path` en "
                "manager_node_runtime/node_jobs_api.py, así que auditar un portafolio "
                "manual falla con «Se encontraron varios sets distintos para …». "
                "Portar la clave desde mt5_manager/node_portfolio_api.py y duplicar la "
                "prueba en tests/test_manager_node_manual_portfolio.py. Hay que "
                "reiniciar la aplicación del agente.",
            )
            covered = [
                path for path in sorted((project / "tests").glob("test_manager_node_*.py"))
                if "set_path" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(
                covered,
                msg=(
                    f"{project}: ninguna prueba del nodo cubre la ruta del set en el "
                    "detalle; duplicar allí la cobertura de `set_path`."
                ),
            )

        self._assert_on_every_fork(check, "la ruta del set en el detalle del portafolio")


if __name__ == "__main__":
    unittest.main()
