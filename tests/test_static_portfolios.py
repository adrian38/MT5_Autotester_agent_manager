from __future__ import annotations

import unittest
import math
import re
from pathlib import Path

from lxml import html


class NodeCardControlsTests(unittest.TestCase):
    def script(self) -> str:
        return (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "app.js"
        ).read_text(encoding="utf-8")

    def test_pause_and_resume_buttons_follow_the_job_state(self) -> None:
        script = self.script()
        # Pausar solo con algo en marcha; Reanudar solo desde un estado retomable.
        self.assertIn("onclick=\"pauseNode(", script)
        self.assertIn("onclick=\"resumeNode(", script)
        self.assertIn("RESUMABLE_STATES = ['paused', 'interrupted', 'failed']", script)
        self.assertIn("const resumable = isResumable(node)", script)
        self.assertIn("resumable ? `<button onclick=\"resumeNode(", script)
        self.assertIn("stepIndex < pipeline.length", script)
        self.assertIn("node?.capabilities?.failed_resume", script)
        # Detener sigue disponible sobre un pipeline pausado, para descartarlo.
        self.assertIn("state === 'running' || resumable", script)

    def test_pause_and_resume_call_their_own_manager_endpoints(self) -> None:
        script = self.script()
        self.assertIn("/pause`", script)
        self.assertIn("/resume`", script)
        for name in ("pauseNode", "resumeNode"):
            self.assertIn(f"window.{name} = {name};", script)

    def test_pause_warns_that_the_current_stage_is_cut(self) -> None:
        # El usuario tiene que saber que se pierde el trabajo en vuelo de la etapa.
        script = self.script()
        self.assertIn("Se corta la etapa en curso", script)

    def test_application_restart_button_is_capability_gated_and_accepts_paused_jobs(self) -> None:
        script = self.script()
        self.assertIn("node.capabilities?.application_restart", script)
        self.assertIn(
            "RESTARTABLE_STATES = ['idle', 'completed', 'failed', 'stopped', 'paused', 'interrupted']",
            script,
        )
        self.assertIn("onclick=\"restartNode(", script)
        self.assertIn("/restart`", script)
        self.assertIn("git pull --ff-only", script)
        self.assertIn("git push", script)
        self.assertIn("window.restartNode = restartNode", script)

    def test_manager_restart_button_uses_the_manager_endpoint_and_exact_sequence(self) -> None:
        root = Path(__file__).parents[1]
        page = (root / "mt5_manager" / "static" / "index.html").read_text(encoding="utf-8")
        script = self.script()
        self.assertIn('id="restart-manager"', page)
        self.assertIn("fetch('/api/manager/restart'", script)
        self.assertIn("git pull, git push y docker compose up -d --build manager", script)
        self.assertIn('id="manager-auth-dialog"', page)
        self.assertIn("https://github.com/login/device", page)
        self.assertIn("authentication_required", script)
        self.assertIn("window.restartManager = restartManager", script)


class PortfolioFormTests(unittest.TestCase):
    def test_capital_accepts_any_numeric_value_like_the_original_ubs_form(self) -> None:
        page = html.fromstring(
            (Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.html").read_text(
                encoding="utf-8"
            )
        )

        capital = page.xpath('//input[@name="capital"]')[0]
        self.assertEqual(capital.get("type"), "number")
        self.assertEqual(capital.get("step"), "any")
        self.assertIsNone(capital.get("min"))

    def test_portfolio_configuration_is_saved_after_field_changes(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("form.addEventListener('change', scheduleSettingsSave)", script)
        self.assertIn("postManager('settings', payload)", script)
        self.assertIn("if (!form.checkValidity()) return", script)
        self.assertIn("if (!form.reportValidity()) return", script)

    def test_completed_calculation_reloads_and_reveals_proposals(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios.js").read_text(encoding="utf-8")

        self.assertLess(page.index('id="proposal-area"'), page.index('class="portfolio-inventory"'))
        self.assertIn("managerState.job?.status === 'running' && data.job?.status !== 'running'", script)
        self.assertIn("await loadManagerState(data.job?.status === 'completed')", script)
        self.assertIn("loadManagerState(true)", script)
        self.assertIn("scrollIntoView({behavior: 'smooth', block: 'start'})", script)

    def test_full_history_can_be_stopped_and_monthly_generation_is_disabled(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        full_page = (static_dir / "portfolios.html").read_text(encoding="utf-8")
        full_script = (static_dir / "portfolios.js").read_text(encoding="utf-8")
        manager_script = (static_dir / "app.js").read_text(encoding="utf-8")
        monthly_page = html.fromstring(
            (static_dir / "portfolios_monthly.html").read_text(encoding="utf-8")
        )
        monthly_script = (static_dir / "portfolios_monthly.js").read_text(encoding="utf-8")

        self.assertIn('id="stop-calculation"', full_page)
        self.assertIn("postManager('stop', {scope})", full_script)
        self.assertIn("['running', 'stopping'].includes(job?.status)", full_script)
        monthly_button = monthly_page.xpath('//button[@id="generate-proposals"]')[0]
        self.assertEqual(monthly_button.get("disabled"), "disabled")
        self.assertIn("document.querySelector('#generate-proposals').disabled = true", monthly_script)
        self.assertNotIn("postManager('generate', formPayload())", monthly_script)
        self.assertIn(
            'disabled title="Portafolio UBS mensual congelado temporalmente">Portafolio mensual</button>',
            manager_script,
        )
        self.assertNotIn('href="/portfolios_monthly.html?node=', manager_script)

    def test_portfolio_risk_is_presented_as_maximum_not_addition(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios.js").read_text(encoding="utf-8")

        self.assertIn("DD riesgo máx.", page)
        self.assertIn("máx(cerrado", script)
        self.assertNotIn("cerrado ${number(result.actual_closed_valley_dd, 2)} + flotante", script)

    def test_daily_drawdown_is_labeled_as_visual_only(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios_monthly.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios_monthly.js").read_text(encoding="utf-8")

        self.assertIn("DD diario visual (no limita)", page)
        self.assertIn("diario visual", script)

    def test_monthly_builder_has_independent_assets_and_live_calculation_aids(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        full_page = (static_dir / "portfolios.html").read_text(encoding="utf-8")
        monthly_page = (static_dir / "portfolios_monthly.html").read_text(encoding="utf-8")
        full_script = (static_dir / "portfolios.js").read_text(encoding="utf-8")
        monthly_script = (static_dir / "portfolios_monthly.js").read_text(encoding="utf-8")

        self.assertIn('src="/portfolios_monthly.js"', monthly_page)
        self.assertIn("const scope = 'monthly'", monthly_script)
        self.assertIn("const scope = 'full_history'", full_script)
        self.assertIn('id="monthly-calculation-monitor"', monthly_page)
        self.assertIn('id="monthly-live-log"', monthly_page)
        self.assertIn("function stageFromProgress", monthly_script)
        self.assertIn("Number(job.stage || 0)", monthly_script)
        self.assertIn("No participan en futuras generaciones de Portafolio UBS mensual", monthly_script)
        self.assertIn("El Portafolio UBS mensual #${selectedId} no se modifica", monthly_script)
        self.assertNotIn("se recalcularán sus métricas", monthly_script)
        self.assertNotIn("Cuarentena informativa", monthly_script)
        self.assertNotIn("no se excluyen del cálculo", monthly_script)
        self.assertIn("async function refreshMonthlyLog", monthly_script)
        self.assertIn("refreshMonthlyLog(true)", monthly_script)
        self.assertIn('name="experimental_monthly_search"', monthly_page)
        self.assertIn("'experimental_monthly_search'", monthly_script)
        self.assertNotIn('name="experimental_monthly_search"', full_page)
        self.assertIn('name="experimental_full_search"', full_page)
        self.assertIn("'experimental_full_search'", full_script)
        self.assertNotIn('name="experimental_full_search"', monthly_page)
        self.assertNotIn('name="target_month"', full_page)
        self.assertNotIn('name="max_daily_dd"', full_page)

    def test_the_three_builders_share_the_stage_monitor_and_live_log(self) -> None:
        # El monitor de etapas y el log en vivo eran una ventaja solo del
        # mensual; los tres calculos numeran ya sus etapas «N/M».
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        for page_name, script_name, stages in (
            ("portfolios.html", "portfolios.js", 5),
            ("portfolios_grid.html", "portfolios_grid.js", 4),
        ):
            page = (static_dir / page_name).read_text(encoding="utf-8")
            script = (static_dir / script_name).read_text(encoding="utf-8")
            self.assertIn('id="calculation-monitor"', page, page_name)
            self.assertIn('id="live-log"', page, page_name)
            self.assertIn('id="stage-list"', page, page_name)
            self.assertEqual(
                page.count('<span data-stage="'), stages, page_name,
            )
            self.assertIn("function stageFromProgress", script, script_name)
            self.assertIn("async function refreshCalculationLog", script, script_name)
            self.assertIn(r"/^(\d+)\/(\d+)\s*[·-]/", script, script_name)
        monthly = (static_dir / "portfolios_monthly.html").read_text(encoding="utf-8")
        self.assertIn('id="monthly-calculation-monitor"', monthly)

    def test_the_three_builders_report_an_auto_adjusted_valley(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        for name in ("portfolios.js", "portfolios_monthly.js", "portfolios_grid.js"):
            script = (static_dir / name).read_text(encoding="utf-8")
            self.assertIn("proposal.auto_adjusted_valley", script, name)
            self.assertIn("objetivo ajustado", script, name)

    def test_grid_shows_the_reoptimization_diff_and_can_reset_its_form(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios_grid.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios_grid.js").read_text(encoding="utf-8")

        self.assertIn('id="proposal-diff-section"', page)
        self.assertIn('id="proposal-diff"', page)
        self.assertIn("proposal.diff || []", script)
        self.assertIn('id="reset-settings"', page)
        self.assertIn("#reset-settings", script)

    def test_the_open_exposure_overlap_is_reported_without_changing_the_risk(self) -> None:
        # La medida agregada es informativa: la tarjeta sigue enseñando
        # máx(cerrado, flotante) como riesgo aplicado.
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        for name in ("portfolios.js", "portfolios_monthly.js"):
            script = (static_dir / name).read_text(encoding="utf-8")
            self.assertIn("function overlapNote", script, name)
            self.assertIn("audit.overlap_detected", script, name)
            self.assertIn("(informativo)", script, name)
            self.assertIn("máx(cerrado ${number(result.actual_closed_valley_dd, 2)}", script, name)

    def test_saved_bundle_members_can_be_excluded(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("El portafolio #${selectedId} no se modifica", script)
        self.assertIn("onclick=\"excludeStrategy('detail',${index})\">Excluir</button>", script)
        self.assertNotIn("${isBundle ? '' : `<button type=\"button\" class=\"danger table-action\"", script)

    def test_saved_bundle_members_support_batch_selection(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios.js").read_text(encoding="utf-8")

        self.assertIn('id="detail-select-all"', page)
        self.assertIn('id="detail-exclude-selected"', page)
        self.assertIn("set_paths: members.map", script)
        self.assertIn("selectedDetailMembers = new Set", script)
        # Excluir ya no borra el portafolio guardado, ni entero ni por partes.
        self.assertNotIn("waitForPortfolioRemoval", script)
        self.assertIn("El portafolio A/M/C #${selectedId} no se modifica", script)

    def test_monthly_members_support_batch_selection_like_the_ubs_ones(self) -> None:
        # Las casillas no dependen de que el portafolio sea un bundle: un mes
        # guardado nunca lo es.
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios_monthly.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios_monthly.js").read_text(encoding="utf-8")

        self.assertIn('id="detail-select-all"', page)
        self.assertIn('id="detail-exclude-selected"', page)
        self.assertIn("set_paths: members.map", script)
        self.assertNotIn("waitForPortfolioRemoval", script)
        self.assertIn("onchange=\"toggleDetailSelection(${index},this.checked)\"", script)
        self.assertIn("onclick=\"excludeStrategy('detail',${index})\">Excluir</button>", script)
        self.assertNotIn("const selector = isBundle", script)
        self.assertNotIn("const excludeAction = isBundle", script)
        self.assertIn("El Portafolio UBS mensual #${selectedId} no se modifica", script)
        self.assertNotIn("portafolio A/M/C", script)

    def test_grid_members_can_be_excluded_and_released_like_the_ubs_ones(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios_grid.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios_grid.js").read_text(encoding="utf-8")

        self.assertIn('id="quarantine-rows"', page)
        self.assertIn('id="detail-select-all"', page)
        self.assertIn('id="detail-exclude-selected"', page)
        self.assertIn("onclick=\"excludeStrategy('proposal',${index})\">Excluir</button>", script)
        self.assertIn("onclick=\"excludeStrategy('detail',${index})\">Excluir</button>", script)
        # El botón de la tabla lo pinta ahora la primitiva compartida, que es la
        # misma para los tres ámbitos; la función sigue siendo de esta pantalla.
        self.assertIn("renderQuarantineTables(quarantine);", script)
        self.assertIn("async function requalifyStrategy(", script)
        self.assertIn(
            "onclick=\"requalifyStrategy(",
            (static_dir / "exclusion_reason.js").read_text(encoding="utf-8"),
        )
        self.assertIn("El paquete Grid A/M/C #${selectedId} no se modifica", script)
        self.assertIn("set_paths: members.map", script)
        self.assertIn("No participan en futuras generaciones de Portafolio Grid UBS", script)
        # La tabla de propuestas gana las columnas de acciones y escalera, y la
        # de guardados además la de selección: los colspan vacíos las siguen.
        self.assertIn('colspan="13">Esta variante no contiene sets.', script)
        self.assertIn("${bundle ? 14 : 13}", script)

    def test_grid_screen_shows_the_open_ladder_and_its_peak_margin(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = (static_dir / "portfolios_grid.html").read_text(encoding="utf-8")
        script = (static_dir / "portfolios_grid.js").read_text(encoding="utf-8")

        self.assertEqual(page.count("<th>Escalera abierta</th>"), 2)
        self.assertIn('id="proposal-risk"', page)
        self.assertIn('name="max_open_overlap"', page)
        self.assertIn("function ladderCell", script)
        self.assertIn("grid_peak_margin", script)
        self.assertIn("grid_open_exposure", script)
        self.assertIn("grid_peak_lots", script)
        self.assertIn("Flotante vinculante", script)
        self.assertIn("Peor día de exposición abierta", script)
        self.assertIn("Margen de pico", script)
        # El lote asignado es la pierna base, no lo que la cuenta llega a abrir.
        self.assertIn("lotes base", script)

    def test_explicit_save_actions_show_a_blocking_progress_overlay(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = html.fromstring((static_dir / "portfolios.html").read_text(encoding="utf-8"))
        script = (static_dir / "portfolios.js").read_text(encoding="utf-8")
        styles = (static_dir / "styles.css").read_text(encoding="utf-8")

        overlay = page.xpath('//*[@id="save-overlay"]')[0]
        self.assertEqual(overlay.get("role"), "dialog")
        self.assertIsNotNone(overlay.get("hidden"))
        self.assertIn("async function withSaveOverlay", script)
        self.assertIn("'Guardando configuración'", script)
        self.assertIn("'Guardando portafolio'", script)
        self.assertIn("'Enviando borrado'", script)
        self.assertIn("añadido a tareas pendientes", script)
        self.assertIn("handleTaskTransition(data.task || {})", script)
        self.assertIn("async function loadTaskState()", script)
        self.assertIn("portfolio-manager/task?scope=${scope}", script)
        self.assertIn("pollTimer = null; loadTaskState();", script)
        # Excluir ya no borra el portafolio guardado, así que el overlay lo dice.
        self.assertNotIn("'Borrando portafolio A/M/C'", script)
        self.assertIn("'Excluyendo estrategia'", script)
        self.assertIn("'Excluyendo estrategias'", script)
        self.assertIn("'Restaurando portafolio'", script)
        self.assertIn("guardado, pero no se pudo actualizar la vista", script)
        self.assertIn(".save-overlay{", styles)
        self.assertIn(".save-spinner{", styles)

    def test_delete_overlay_only_waits_for_background_task_submission(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")
        delete_handler = script.split("document.querySelector('#detail-delete')", 1)[1].split(
            "document.querySelector('#detail-export')", 1
        )[0]

        self.assertIn("postManager('delete'", delete_handler)
        self.assertIn("managerState.task = data.task", delete_handler)
        self.assertNotIn("await loadManagerState()", delete_handler)
        self.assertNotIn("await loadPortfolios", delete_handler)
        self.assertNotIn("await Promise.all", delete_handler)

    def test_export_uses_the_native_folder_picker(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")
        export_handler = script.split("document.querySelector('#detail-export')", 1)[1].split(
            "document.querySelector('#portfolio-refresh')", 1
        )[0]

        self.assertIn("postManager('choose-export-folder'", export_handler)
        self.assertIn("destination: selection.folder", export_handler)
        self.assertNotIn("prompt(", export_handler)

    def test_remote_export_downloads_a_zip_from_the_manager(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("async function downloadPortfolioExport", script)
        self.assertIn("managerState.capabilities?.export_mode === 'download'", script)
        self.assertIn("portfolio-manager/export-download", script)
        self.assertIn("link.download", script)

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
        self.assertIn("Terminales para reparación", page)
        self.assertIn("`${dialogName}_max_workers`", script)
        self.assertIn(".repair-select-row", styles)

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
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        fields = {}
        for path in static_dir.glob("*.html"):
            page = html.fromstring(path.read_text(encoding="utf-8"))
            for field in page.xpath('//input[@type="number"]'):
                key = field.get("name") or field.get("id")
                self.assertIsNotNone(key, f"Input numérico sin name/id en {path.name}")
                fields.setdefault(key, []).append((path.name, field))
        # Los controles del auditor se crean una vez por cada portafolio marcado.
        # Se audita también el HTML del template literal que los genera.
        live_audit_script = (static_dir / "live_audit.js").read_text(encoding="utf-8")
        for markup in re.findall(r'<input data-field="[^"]+" type="number"[^>]*>', live_audit_script):
            field = html.fragment_fromstring(markup)
            key = field.get("data-field")
            fields.setdefault(key, []).append(("live_audit.js", field))

        valid_values = {
            "cycles": (1, 100),
            "generations": (1, 1000),
            "variants": (1, 10, 10000),
            "max-seeds": (0, 30, 100000),
            "random-seed": (-7, 0, 20260812),
            "max-workers": (1, 64),
            "repair-workers": (1, 64),
            "regression-workers": (1, 64),
            "generation-repair-workers": (1, 64),
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
            "trade_time_tolerance_seconds": (0, 60, 86400),
            "price_tolerance_points": (0, 10, 10.5, 1000000),
            "volume_tolerance_pct": (0, 1, 1.5, 100),
            "pnl_deviation_warning_pct": (0, 10, 10.5, 10000),
            "drawdown_deviation_warning_pct": (0, 15, 15.5, 10000),
            "scheduler-interval-days": (1, 30, 3650),
        }
        self.assertEqual(set(fields), set(valid_values), "Actualiza la auditoría para los inputs numéricos")

        for key, values in valid_values.items():
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


class PortfolioImportScreenTests(unittest.TestCase):
    """Importar existe en los tres ámbitos y hereda el transporte de exportar."""

    PAGES = ("portfolios", "portfolios_monthly", "portfolios_grid")

    def static(self, name: str) -> str:
        return (
            Path(__file__).parents[1] / "mt5_manager" / "static" / name
        ).read_text(encoding="utf-8")

    def test_every_scope_offers_the_import_button(self) -> None:
        for name in self.PAGES:
            page, script = self.static(f"{name}.html"), self.static(f"{name}.js")
            with self.subTest(page=name):
                self.assertIn('id="portfolio-import"', page)
                self.assertIn('src="/portfolio_transfer.js"', page)
                self.assertIn("pickPortfolioImportSource(", script)
                self.assertIn("describePortfolioImport(data)", script)
                # El velo cubre la reconstruccion, nunca el selector de origen.
                self.assertLess(
                    script.index("pickPortfolioImportSource("),
                    script.index("portfolioImportProgress(label)"),
                )
                self.assertIn("portfolioImportProgress(label)", script)

    def test_the_import_mirrors_the_export_transport(self) -> None:
        # Con `export_mode=folder` el manager abre su selector nativo; con
        # `download` el ZIP viaja desde el navegador. Si la importación solo
        # cubriera uno, quedaría inservible en el otro despliegue.
        transfer = self.static("portfolio_transfer.js")
        self.assertIn("exportMode === 'download'", transfer)
        self.assertIn("'choose-import-folder'", transfer)
        self.assertIn("readAsDataURL", transfer)


class ExclusionReasonScreenTests(unittest.TestCase):
    """Las tres pantallas piden el motivo y reparten la cuarentena en tres tablas.

    Los tres ámbitos tienen interfaz separada a propósito, así que sin una prueba
    que los recorra a los tres una mejora se queda en la pantalla donde nació.
    """

    PAGES = ("portfolios", "portfolios_monthly", "portfolios_grid")

    def static(self, name: str) -> str:
        return (
            Path(__file__).parents[1] / "mt5_manager" / "static" / name
        ).read_text(encoding="utf-8")

    def test_every_scope_has_the_two_verdict_tables(self) -> None:
        for name in self.PAGES:
            page = self.static(f"{name}.html")
            with self.subTest(page=name):
                self.assertIn('id="quarantine-rows"', page)
                self.assertIn('id="quarantine-degradation-rows"', page)
                self.assertIn('id="quarantine-ohlc-rows"', page)
                self.assertIn('src="/exclusion_reason.js"', page)

    def test_the_two_verdict_panels_share_a_row_of_equal_columns(self) -> None:
        # Sueltos caían en las columnas 1fr/1.3fr del inventario y el de la
        # izquierda salía estrecho, partiendo la fecha en dos líneas.
        styles = self.static("styles.css")
        self.assertIn(".portfolio-inventory-verdicts{grid-column:1/-1", styles)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", styles)
        for name in self.PAGES:
            page = html.fromstring(self.static(f"{name}.html"))
            with self.subTest(page=name):
                wrappers = page.xpath('//div[@class="portfolio-inventory-verdicts"]')
                self.assertEqual(len(wrappers), 1)
                self.assertEqual(
                    [child.get("class") for child in wrappers[0]],
                    ["inventory-panel", "inventory-panel"],
                )

    def test_the_radio_of_each_option_escapes_the_global_input_rule(self) -> None:
        # `input,select{width:100%;padding:10px;border:...}` alcanza también a
        # los radios: cada uno era una caja ancha con el punto centrado dentro,
        # así que caía en una x distinta en cada fila según su texto.
        styles = self.static("styles.css")
        self.assertIn(".reason-option input{width:auto", styles)
        self.assertIn("padding:0;border:0", styles)

    def test_the_toast_wraps_long_windows_paths(self) -> None:
        # Los avisos llevan rutas de Windows, que no tienen espacios: sin
        # permitir el corte dentro de la palabra el texto se salía del recuadro.
        styles = self.static("styles.css")
        self.assertIn("overflow-wrap:anywhere", styles)
        self.assertIn("max-width:min(420px,calc(100vw - 48px))", styles)

    def test_the_panel_note_lives_inside_the_panel_padding(self) -> None:
        # El panel no tiene padding propio: lo pone .panel-title. Sin esto la
        # nota salía pegada al borde y desalineada con el título.
        self.assertIn(".quarantine-verdict-note{margin:0;padding:11px 15px 0", self.static("styles.css"))

    def test_every_scope_asks_for_the_reason_and_sends_its_code(self) -> None:
        for name in self.PAGES:
            script = self.static(f"{name}.js")
            with self.subTest(script=name):
                # Sin `reason_code` en las dos llamadas, la pantalla excluiría sin
                # veredicto: el nodo escribiría solo la cuarentena.
                self.assertEqual(script.count("reason_code: reasonCode"), 2)
                self.assertEqual(script.count("await askExclusionReason("), 2)
                self.assertIn("renderQuarantineTables(quarantine);", script)

    def test_the_three_reason_codes_match_the_python_side(self) -> None:
        script = self.static("exclusion_reason.js")
        from mt5_manager import candidate_verdict

        for code in candidate_verdict.REASON_CODES:
            self.assertIn(f"code: '{code}'", script)

    def test_the_table_button_offers_the_three_reasons_and_the_pool(self) -> None:
        # El botón de la tabla no es «Reintegrar» a secas: mueve la estrategia
        # entre los tres motivos y el pool, que son estados de la misma cosa.
        script = self.static("exclusion_reason.js")
        self.assertIn("code: 'pool'", script)
        self.assertIn("options: [...EXCLUSION_REASONS, POOL_TARGET]", script)
        # Excluir NO ofrece el pool: no es un motivo de exclusión.
        self.assertIn("options: EXCLUSION_REASONS,", script)
        self.assertIn('onclick="requalifyStrategy(', script)

    def test_every_scope_sends_the_requalification_to_its_own_endpoint(self) -> None:
        for name in self.PAGES:
            script = self.static(f"{name}.js")
            with self.subTest(script=name):
                self.assertIn("async function requalifyStrategy(", script)
                self.assertIn("postManager('requalify'", script)
                self.assertIn("reason_code: target", script)
                self.assertNotIn("postManager('release'", script)


if __name__ == "__main__":
    unittest.main()
