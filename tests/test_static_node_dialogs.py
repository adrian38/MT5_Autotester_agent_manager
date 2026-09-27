from __future__ import annotations

import math
import re
import unittest
from pathlib import Path

from lxml import html


VALID_VALUES = {
    "cycles": (1, 100),
    "generations": (1, 1000),
    "variants": (1, 10, 10000),
    "max-seeds": (0, 30, 100000),
    "random-seed": (-7, 0, 20260812),
    "max-workers": (1, 64),
    "repair-workers": (1, 64),
    "repair-workers-phase2": (1, 64),
    "regression-workers": (1, 64),
    "generation-repair-workers": (1, 64),
    "generation-repair-workers-phase2": (1, 64),
    "generation-repair-attempts": (1, 20),
    "repair-attempts": (1, 20),
    "capital": (0.5, 5000, 10000.25),
    "valley_dd_pct": (0.5, 6, 6.05),
    "max_daily_dd": (0.5, 150, 150.25),
    "top_k_per_symbol": (1, 3, 20),
    "max_total_candidates": (1, 30, 100),
    "min_trades_2020_2026": (0, 15, 100),
    "min_strategy_recent_contribution_pct": (0, 5, 100),
    "max_units_per_set": (1, 30),
    "max_total_units": (1, 30),
    "max_units_per_symbol": (1, 30),
    "max_sets_per_symbol": (1, 3),
    "dd_reserve_pct": (0, 10, 99.5),
    "search_restarts": (0, 4),
    "max_margin_pct": (0.5, 100, 100.25),
    "max_open_overlap": (0.05, 0.6, 1),
    "max_pair_corr": (0, 0.35, 0.355, 1),
    "max_downside_corr": (0, 0.25, 0.255, 1),
    "max_dd_overlap": (0, 0.35, 0.355, 1),
    "max_portfolio_corr": (0, 0.5, 0.505, 1),
    "period_days": (1, 7, 3650),
    "min_tick_history_quality_pct": (0, 80, 99.9, 100),
    "fixed_delay_ms": (0, 125, 600000),
    "trade_time_tolerance_seconds": (0, 120, 86400),
    "price_tolerance_points": (0, 15, 15.5, 1000000),
    "volume_tolerance_pct": (0, 1, 1.5, 100),
    "pnl_deviation_warning_pct": (0, 10, 10.5, 10000),
    "drawdown_deviation_warning_pct": (0, 15, 15.5, 10000),
    "scheduler-interval-days": (1, 30, 3650),
    # Laboratorio «Experimenta». Los topes de la pantalla son los que
    # `experiment_service.normalize_settings` recorta al recibirlos.
    "target_equity": (1, 250000, 1000000.5),
    "horizon_months": (1, 12, 72),
    "max_dd_pct": (1, 35, 35.5, 95),
    "max_units_per_strategy": (1, 8, 200),
    "max_units_total": (1, 400, 20000),
    "pool_limit": (1, 60, 300),
    "greedy_steps": (0, 240, 2000),
    "max_candidates_per_node": (0, 300, 100000),
}


class NodeDialogAndNumberInputTests(unittest.TestCase):
    def test_regression_card_features_follow_node_capabilities_and_use_their_own_job(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")

        self.assertIn("function supportsRegression(node)", script)
        self.assertIn("node.capabilities?.regression_runs", script)
        self.assertIn("hasOwn(node.launch_defaults, 'run_regression')", script)
        self.assertIn("hasOwn(node.database?.stages, 'regression')", script)
        self.assertIn("stageDefinitions.push(['Prueba regresiva', stages.regression, 4, 'regression'])", script)
        self.assertNotIn("broker === 'ICTRADING'", script)
        self.assertIn("openRegression", script)
        self.assertIn("/regression`,", script)
        self.assertIn("regression-workers", script)
        self.assertIn("max_workers: Number(document.querySelector('#regression-workers').value)", script)
        self.assertIn("settingsFor(node, id).regression_max_workers", script)
        self.assertIn('id="regression-dialog"', page)
        self.assertIn('id="regression-workers"', page)
        self.assertIn("Ejecutar prueba regresiva", page)

    def test_cards_expose_manual_and_automatic_historical_cleanup(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")
        styles = (static_dir / "styles.css").read_text(encoding="utf-8")

        self.assertIn("node.capabilities?.historical_cleanup", script)
        self.assertIn("cleanupNode", script)
        self.assertIn("/cleanup`,", script)
        self.assertIn("Eliminar históricos", script)
        self.assertIn("TODAS las terminales", script)
        self.assertIn("syncCleanupAfterRun", script)
        self.assertEqual(script.count("cleanup_after_run: true"), 1)
        self.assertIn('id="cleanup-after-run"', page)
        self.assertIn("Limpiar datos históricos al completar cada run", page)
        self.assertIn('id="repair-cleanup"', page)
        self.assertIn("Limpiar datos históricos después de cada run seleccionado", page)
        self.assertIn("settingsFor(node, id).cleanup_after_run", script)
        self.assertIn("cleanup_after_run: cleanupAfterRun", script)
        self.assertIn("setRepairCleanup", script)
        self.assertGreaterEqual(page.count("después de cada run seleccionado"), 2)
        self.assertIn(".card-cleanup-policy", styles)

    def test_repair_dialog_can_select_all_runs(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")
        styles = (static_dir / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="repair-select-all"', page)
        self.assertIn("Seleccionar todos", page)
        self.assertIn("function toggleRepairRuns", script)
        self.assertIn("function updateRepairSelectionState", script)
        self.assertIn("selectAll.indeterminate", script)
        self.assertIn("window.toggleRepairRuns = toggleRepairRuns", script)
        self.assertIn('id="repair-workers"', page)
        self.assertIn("max_workers: Number(document.querySelector('#repair-workers').value)", script)
        self.assertIn("settingsFor(node, id).repair_max_workers", script)
        self.assertIn("const RUN_PAGE_SIZE = 100", script)
        self.assertEqual(script.count("runs?limit=${RUN_PAGE_SIZE}&offset=${currentOffset}"), 2)
        self.assertNotIn("runs?limit=100", script)
        self.assertIn('id="repair-load-more"', page)
        self.assertIn("loadMoreRepairRuns()", page)
        self.assertIn("window.loadMoreRepairRuns = loadMoreRepairRuns", script)
        self.assertIn("pagination.has_more", script)
        self.assertIn('id="generation-repair-workers"', page)
        self.assertIn("repair_max_workers: Number(document.querySelector('#generation-repair-workers').value)", script)
        self.assertIn("Terminales reparación · fase 1", page)
        self.assertIn("`${dialogName}_max_workers`", script)
        self.assertIn(".repair-select-row", styles)

    def test_repair_dialogs_configure_the_terminals_of_both_phases(self) -> None:
        # Cada intento de reparación se ejecuta en dos fases sobre las mismas
        # etapas, y lo único que las diferencia es cuántos terminales usan a la vez.
        # Los dos diálogos y la tarjeta tienen que poder fijar las dos.
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="repair-workers-phase2"', page)
        self.assertIn('id="generation-repair-workers-phase2"', page)
        self.assertIn("Terminales MT5 · fase 2", page)
        self.assertIn("Terminales reparación · fase 2", page)
        self.assertIn(
            "repair_phase2_max_workers: Number(document.querySelector('#repair-workers-phase2').value)",
            script,
        )
        self.assertIn(
            "repair_phase2_max_workers: Number(document.querySelector('#generation-repair-workers-phase2').value)",
            script,
        )
        self.assertIn("setRepairPhase2Workers(Number(this.value))", page)
        self.assertIn("window.setRepairPhase2Workers = setRepairPhase2Workers", script)
        self.assertIn("'repair_phase2_max_workers',Number(this.value)", script)
        self.assertIn("settingsFor(node, id).repair_phase2_max_workers", script)
        # El estado en vivo distingue las dos pasadas: sin la fase, la tarjeta leería
        # el recuento de pendientes de la otra.
        self.assertIn("`phase_${job.current_phase}_`", script)
        self.assertIn("fase ${job.current_phase}/2", script)

    def test_repair_dialog_makes_the_regression_stage_optional(self) -> None:
        # La etapa regresiva de Reparar dejó de ser obligatoria: la decide una casilla
        # del propio diálogo, que solo aparece en nodos con la capacidad y se recuerda
        # aparte de `run_regression`, la de la nueva ejecución.
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")
        styles = (static_dir / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="repair-regression-option"', page)
        self.assertIn('id="repair-regression"', page)
        self.assertIn("setRepairRegression(this.checked)", page)
        self.assertIn("repair_run_regression", script)
        self.assertIn(
            "document.querySelector('#repair-regression-option').hidden = !supportsRegression(node)",
            script,
        )
        self.assertIn("run_regression: runRegression", script)
        self.assertIn("window.setRepairRegression = setRepairRegression", script)
        self.assertIn(".repair-regression", styles)

    def test_regression_dialog_can_select_all_runs(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="regression-select-all"', page)
        self.assertIn("toggleRegressionRuns(this.checked)", page)
        self.assertIn('id="regression-selected-count"', page)
        self.assertIn("function toggleRegressionRuns", script)
        self.assertIn("function updateRegressionSelectionState", script)
        self.assertIn("window.toggleRegressionRuns = toggleRegressionRuns", script)
        self.assertIn('id="regression-load-more"', page)
        self.assertIn("loadMoreRegressionRuns()", page)
        self.assertIn("window.loadMoreRegressionRuns = loadMoreRegressionRuns", script)

    def test_every_html_number_input_accepts_representative_backend_values(self) -> None:
        fields = self._number_fields()
        self.assertEqual(
            set(fields),
            set(VALID_VALUES),
            "Actualiza la auditoría para los inputs numéricos",
        )
        self._assert_representative_values(fields)

    def _number_fields(self) -> dict[str, list[tuple[str, object]]]:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        fields: dict[str, list[tuple[str, object]]] = {}
        for path in static_dir.glob("*.html"):
            page = html.fromstring(path.read_text(encoding="utf-8"))
            for field in page.xpath('//input[@type="number"]'):
                key = field.get("name") or field.get("id")
                self.assertIsNotNone(key, f"Input numérico sin name/id en {path.name}")
                fields.setdefault(key, []).append((path.name, field))
        self._append_live_audit_fields(fields, static_dir)
        return fields

    @staticmethod
    def _append_live_audit_fields(fields, static_dir: Path) -> None:
        script = (static_dir / "live_audit.js").read_text(encoding="utf-8")
        for markup in re.findall(r'<input data-field="[^"]+" type="number"[^>]*>', script):
            field = html.fragment_fromstring(markup)
            fields.setdefault(field.get("data-field"), []).append(("live_audit.js", field))

    def _assert_representative_values(self, fields) -> None:
        for key, values in VALID_VALUES.items():
            for path_name, field in fields[key]:
                for value in values:
                    self.assertTrue(
                        self._html_number_accepts(field, value),
                        f"{key} no acepta el valor válido {value} en {path_name}",
                    )

    @staticmethod
    def _html_number_accepts(field, value: float) -> bool:
        minimum = float(field.get("min")) if field.get("min") is not None else -math.inf
        maximum = float(field.get("max")) if field.get("max") is not None else math.inf
        if not minimum <= value <= maximum:
            return False
        step = field.get("step") or "1"
        if step == "any":
            return True
        base = float(field.get("min") or field.get("value") or 0)
        quotient = (value - base) / float(step)
        return math.isclose(quotient, round(quotient), abs_tol=1e-9)


if __name__ == "__main__":
    unittest.main()
