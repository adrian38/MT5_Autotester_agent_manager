from __future__ import annotations

import unittest
import math
from pathlib import Path

from lxml import html


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
        self.assertIn("se borrará por completo el Portafolio UBS mensual", monthly_script)
        self.assertNotIn("se recalcularán sus métricas", monthly_script)
        self.assertNotIn("Cuarentena informativa", monthly_script)
        self.assertNotIn("no se excluyen del cálculo", monthly_script)
        self.assertIn("async function refreshMonthlyLog", monthly_script)
        self.assertIn("refreshMonthlyLog(true)", monthly_script)
        self.assertNotIn('name="target_month"', full_page)
        self.assertNotIn('name="max_daily_dd"', full_page)

    def test_saved_bundle_members_can_be_excluded(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("se borrará por completo el portafolio A/M/C", script)
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
        self.assertIn("await waitForPortfolioRemoval(affectedPortfolioId)", script)

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
        self.assertIn("'Borrando portafolio A/M/C'", script)
        self.assertIn("'Excluyendo estrategia'", script)
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

    def test_regression_button_is_scoped_to_ictrading_and_uses_its_own_job(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        script = (static_dir / "app.js").read_text(encoding="utf-8")
        page = (static_dir / "index.html").read_text(encoding="utf-8")

        self.assertIn("broker === 'ICTRADING'", script)
        self.assertIn("openRegression", script)
        self.assertIn("/regression`,", script)
        self.assertIn("JSON.stringify({run_ids: runIds})", script)
        self.assertIn('id="regression-dialog"', page)
        self.assertIn("Ejecutar prueba regresiva", page)

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
        self.assertIn(".repair-select-row", styles)

    def test_every_html_number_input_accepts_representative_backend_values(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        fields = {}
        for path in static_dir.glob("*.html"):
            page = html.fromstring(path.read_text(encoding="utf-8"))
            for field in page.xpath('//input[@type="number"]'):
                key = field.get("name") or field.get("id")
                self.assertIsNotNone(key, f"Input numérico sin name/id en {path.name}")
                fields.setdefault(key, []).append((path.name, field))

        valid_values = {
            "cycles": (1, 100),
            "generations": (1, 1000),
            "variants": (1, 10, 10000),
            "max-seeds": (0, 30, 100000),
            "max-workers": (1, 64),
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
            "max_pair_corr": (0, 0.35, 0.355, 1),
            "max_downside_corr": (0, 0.25, 0.255, 1),
            "max_dd_overlap": (0, 0.35, 0.355, 1),
            "max_portfolio_corr": (0, 0.5, 0.505, 1),
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


if __name__ == "__main__":
    unittest.main()
