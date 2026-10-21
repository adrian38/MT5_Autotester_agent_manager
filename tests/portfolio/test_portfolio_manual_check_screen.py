"""Contrato visible de la comprobación manual de portafolios."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "mt5_manager" / "static"


class ScreenTests(unittest.TestCase):
    def test_the_header_chip_knows_about_manual_checks(self) -> None:
        script = (STATIC / "live_audit_schedule.js").read_text(encoding="utf-8")
        for expression in (
            "state.manual_checks || []",
            "function checkText(check)",
            "Comprobación manual #${check.portfolio_id}",
            "if (!running.length && !queue && checks.length === 1) return checks[0].percent;",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, script)

    def test_the_reports_button_lives_in_the_detail_bar_and_starts_disabled(self) -> None:
        page = (STATIC / "portfolios.html").read_text(encoding="utf-8")
        script = (STATIC / "portfolio_manual_check.js").read_text(encoding="utf-8")
        detail = (STATIC / "portfolios.js").read_text(encoding="utf-8")

        self.assertIn('id="detail-export-reports" type="button" class="secondary" disabled', page)
        self.assertIn(">Exportar reportes manuales</button>", page)
        self.assertIn("manual-check-latest", script)
        self.assertIn("window.refreshManualCheckReports = refreshManualCheckReports;", script)
        self.assertIn("refreshManualCheckReports()", detail)

    def test_the_button_and_its_script_are_on_the_ubs_screen(self) -> None:
        page = (STATIC / "portfolios.html").read_text(encoding="utf-8")
        script = (STATIC / "portfolio_manual_check.js").read_text(encoding="utf-8")

        self.assertIn('id="detail-manual-check"', page)
        self.assertIn("/portfolio_manual_check.js", page)
        self.assertIn('name="model_ohlc" type="checkbox" checked', script)
        self.assertIn('name="model_tick" type="checkbox" checked', script)
        self.assertIn('name="use_multiterminal" type="checkbox" checked', script)
        self.assertIn("manual-check-sets-download", script)
        self.assertIn("manual-check-download", script)

    def test_all_three_flows_default_to_automatic_save_and_manual_check(self) -> None:
        page = (STATIC / "portfolios.html").read_text(encoding="utf-8")
        improvement = (STATIC / "portfolio_improvement.js").read_text(encoding="utf-8")
        repair = (STATIC / "portfolio_repair.js").read_text(encoding="utf-8")
        script = (STATIC / "portfolio_manual_check.js").read_text(encoding="utf-8")
        builder = (STATIC / "portfolios.js").read_text(encoding="utf-8")

        self.assertIn('id="generation-auto-save" type="checkbox" checked', page)
        self.assertIn('id="generation-auto-save-key"', page)
        self.assertIn('id="generation-manual-check" type="checkbox" checked', page)
        self.assertIn('id="generation-manual-check-configure"', page)
        self.assertIn('id="improvement-auto-save" name="improvement_auto_save" type="checkbox" checked', improvement)
        self.assertIn('id="improvement-manual-check" type="checkbox" checked', improvement)
        self.assertIn('id="improvement-manual-check-configure"', improvement)
        self.assertIn("Conserva el modo del portafolio de origen.", improvement)
        self.assertIn('id="repair-auto-save" name="repair_auto_save" type="checkbox" checked', repair)
        self.assertIn('id="repair-manual-check" type="checkbox" checked', repair)
        self.assertIn('id="repair-manual-check-configure"', repair)
        self.assertIn("Conserva el modo del portafolio de origen.", repair)
        self.assertIn("manual-check-settings", script)
        self.assertIn("window.manualCheckAutomation", script)
        self.assertIn("controls.selector.disabled = !enabled", script)
        self.assertIn("controls.check.disabled = !enabled", script)
        self.assertIn("controls.button.hidden = !controls.check.checked", script)
        self.assertIn("controls.button.disabled = !enabled", script)
        self.assertNotIn("Hereda la configuración manual del agente.", improvement + repair)
        self.assertIn("automatic_save", improvement)
        self.assertIn("automatic_manual_check", repair)
        self.assertIn("automatic_manual_check", builder)
        self.assertIn("postManager('generate', formPayload(true))", builder)
