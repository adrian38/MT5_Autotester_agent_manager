"""Static configuration-screen tests for live audit."""

from __future__ import annotations

import unittest
from pathlib import Path

from lxml import html


class LiveAuditConfigurationScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        cls.page_text = (cls.static_dir / "live_audit.html").read_text(encoding="utf-8")
        cls.script = (cls.static_dir / "live_audit.js").read_text(encoding="utf-8")
        cls.result_page_text = (cls.static_dir / "live_audit_result.html").read_text(encoding="utf-8")
        cls.result_script = (cls.static_dir / "live_audit_result.js").read_text(encoding="utf-8")
        cls.manager_script = (cls.static_dir / "app.js").read_text(encoding="utf-8")
        cls.page = html.fromstring(cls.page_text)
        cls.result_page = html.fromstring(cls.result_page_text)

    def test_every_node_card_links_to_the_page(self) -> None:
        self.assertIn('/live_audit.html?node=${encodeURIComponent(id)}', self.manager_script)

    def test_unrequested_enable_row_and_monthly_notice_are_gone(self) -> None:
        self.assertNotIn("Habilitar cuando el servicio esté disponible", self.page_text)
        self.assertNotIn("El portafolio mensual permanece deshabilitado", self.page_text)
        self.assertFalse(self.page.xpath('//*[@name="enabled"]'))

    def test_each_marked_portfolio_renders_an_independent_configuration(self) -> None:
        self.assertIn("Cada uso del portafolio conserva modo Agresivo, Moderado o Conservador", self.page_text)
        self.assertIn("ids.map(profileMarkup)", self.script)
        self.assertIn("data-profile-id", self.script)
        self.assertIn("profiles: Object.fromEntries", self.script)
        self.assertIn("credentialState[String(auditId)]", self.script)
        self.assertIn('data-field="portfolio_type"', self.script)
        self.assertIn("Añadir otro uso", self.script)

    def test_each_profile_has_two_accounts_passwords_period_and_tolerances(self) -> None:
        for field in (
            "source_login", "source_server", "source_password", "tester_login", "tester_server",
            "tester_password", "use_calendar_period", "period_days", "period_start_date", "period_end_date", "tester_model",
            "min_tick_history_quality_pct", "price_tolerance_points", "drawdown_deviation_warning_pct",
        ):
            self.assertIn(f'data-field="{field}"', self.script)
        self.assertIn("Tolerancia base de precio (puntos)", self.script)
        self.assertIn("Se amplía automáticamente según la escala y la familia del instrumento.", self.script)
        self.assertIn("Aviso por empeoramiento de PnL (%)", self.script)
        self.assertIn("Una mejora del resultado real frente al tester es admisible.", self.script)
        self.assertNotIn("terminal_path", self.page_text + self.script)
        self.assertIn("los logins pueden coincidir", self.script)
        self.assertNotIn("deben ser diferentes", self.script)

    def test_each_mode_exposes_editable_real_lots_per_strategy(self) -> None:
        self.assertIn("Lotes usados en la cuenta real", self.script)
        self.assertIn("Lote del portafolio", self.script)
        self.assertIn("Lote en cuenta real", self.script)
        self.assertIn("data-strategy-lot", self.script)
        self.assertIn("real_strategy_lots", self.script)
        self.assertIn("member.variant_key === profile.portfolio_type", self.script)
        self.assertIn("/portfolios/${encodeURIComponent(key)}?scope=full_history", self.script)
        self.assertIn("No se pueden mostrar los lotes.", self.script)
        self.assertIn("Desmárcalo arriba y selecciona un portafolio existente.", self.script)
        self.assertIn("portfolioDetailErrors[key] = message", self.script)

    def test_a_saved_improvement_keeps_the_mode_it_inherited_from_its_base(self) -> None:
        variant = self.script.split("function variantMembers", 1)[1].split(
            "function strategyLotsMarkup", 1,
        )[0]
        self.assertIn("!all.some(member => member.variant_key)", variant)
        self.assertIn(
            "singleVariant && fixed && fixed === profile.portfolio_type ? all : []", variant,
        )
        self.assertIn("row.improvement_origin?.mode || row.portfolio_type", self.script)
        self.assertIn("if (singleMode) profile.portfolio_type = singleMode;", self.script)
        self.assertIn('<select data-field="portfolio_type" disabled>', self.script)
        self.assertIn("su modo es el que heredó de la base y no se elige", self.script)
        self.assertIn("portfolio_type: fixedPortfolioMode(id)", self.script)
        self.assertIn("portfolio_type: fixedPortfolioMode(portfolioId)", self.script)

    def test_period_can_be_selected_with_native_calendar_inputs(self) -> None:
        self.assertIn('type="date"', self.script)
        self.assertIn("Usar calendario para elegir el periodo", self.script)
        self.assertIn('data-period-control="fixed_dates"', self.script)
        self.assertIn("marketDateTime", self.result_script)
        self.assertIn("hora MT5", self.result_script)

    def test_audit_now_saves_the_visible_calendar_period_before_starting(self) -> None:
        run = self.script.split("async function runAuditNow", 1)[1].split(
            "async function refreshAuditStates", 1
        )[0]
        self.assertIn("if (!form.reportValidity()) return", run)
        self.assertIn("await saveAuditSettings({apply: false})", run)
        self.assertLess(
            run.index("await saveAuditSettings({apply: false})"),
            run.index("/live-audits/${encodeURIComponent(id)}/run"),
        )
        self.assertGreater(run.index("applyState(savedSettings)"), run.index("/live-audits/${encodeURIComponent(id)}/run"))
        save = self.script.split("async function saveAuditSettings", 1)[1].split(
            "async function runAuditNow", 1
        )[0]
        self.assertIn("/live-audit-config", save)
        self.assertIn("JSON.stringify(payload())", save)

    def test_saved_accounts_can_be_selected_again_for_any_portfolio_use(self) -> None:
        self.assertIn("saved_accounts", self.script)
        self.assertIn('data-saved-account-role="source"', self.script)
        self.assertIn('data-saved-account-role="tester"', self.script)
        self.assertIn("source_saved_account_id", self.script)
        self.assertIn("tester_saved_account_id", self.script)
        self.assertIn("Cuenta para este uso", self.script)
        self.assertIn("Nueva cuenta · escribir login, servidor y contraseña", self.script)
        self.assertIn("la contraseña nunca vuelve al navegador", self.script)

    def test_each_saved_account_is_one_option_with_its_shared_uses(self) -> None:
        options = self.script.split("function savedAccountOptions", 1)[1]
        self.assertIn("${accountOriginLabel(account)}", options)
        label = self.script.split("function accountOriginLabel", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("Number(account.uses || 1) - 1", label)
        self.assertIn("uso${extra === 1 ? '' : 's'} más", label)

    def test_profile_only_asks_for_the_audited_period(self) -> None:
        self.assertIn("Días hacia atrás · incluye hoy", self.script)
        self.assertIn("Usar calendario para elegir el periodo", self.script)
        self.assertNotIn('data-field="audit_interval_days"', self.script)
        for obsolete in ("Sincronizar cada", "Auditoría diaria a las", "Heartbeat vencido"):
            self.assertNotIn(obsolete, self.script)

    def test_each_portfolio_has_manual_audit_progress_result_tab_and_logs(self) -> None:
        self.assertIn('data-audit-action="run"', self.script)
        self.assertIn('data-audit-action="result"', self.script)
        self.assertIn('data-audit-action="logs"', self.script)
        self.assertIn('role="progressbar"', self.script)
        for stage in ("Preparación", "Extracción real", "Strategy Tester", "Comparación", "Finalizado"):
            self.assertIn(stage, self.script)
        self.assertFalse(self.page.xpath('//dialog[@id="audit-result-dialog"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="audit-log-dialog"]'))
        self.assertIn("/live_audit_result.html?node=", self.script)
        self.assertIn("window.open(url, '_blank')", self.script)
        self.assertIn("configuration_only", self.script)

    def test_result_tab_explains_method_and_each_operation_in_tables(self) -> None:
        self.assertTrue(self.result_page.xpath('//table/tbody[@id="comparison-body"]'))
        self.assertTrue(self.result_page.xpath('//table/tbody[@id="strategy-body"]'))
        self.assertTrue(self.result_page.xpath('//table/tbody[@id="artifact-body"]'))
        self.assertTrue(self.result_page.xpath('//section[@id="extra-section"]'))
        for text in (
            "Cómo se decide cada emparejamiento", "Comparación tester ↔ real, fila por fila",
            "Qué archivo y qué lote ejecutó cada estrategia", "Lote del portafolio",
            "StartLots en set usado", "Volumen(es) del reporte",
            "Operaciones reales que ningún resultado del tester utilizó", "Diagnóstico técnico completo",
        ):
            self.assertIn(text, self.result_page_text)
        for field in (
            "operation_comparisons", "nearest_unused_real", "open_time_delta_seconds",
            "open_price_delta_points", "volume_delta_pct", "pnl_delta_pct", "strategy_summary",
            "strategy_artifacts", "real_account_report", "lot_matches_portfolio", "real_account_lot",
            "observed_trade_volumes", "report_volumes_match_start_lots", "tester_execution",
        ):
            self.assertIn(field, self.result_script)
        self.assertIn("sets ·", self.result_script)
        self.assertIn("terminales ·", self.result_script)
        self.assertIn("Resultado antiguo sin trazabilidad por operación", self.result_script)
        self.assertIn("Abrir reporte MT5", self.result_script)
        self.assertIn("Abrir HTML nativo de MT5", self.result_script)
        self.assertIn("· periodo ${auditedPeriod} ·", self.result_script)
        self.assertIn("Precio adaptativo por instrumento", self.result_script)
        self.assertIn("Límite absoluto", self.result_script)
        self.assertIn("adaptive_indices: 'índices'", self.result_script)
        self.assertIn("function pnlDelta(measurements, limit)", self.result_script)
        self.assertIn("A favor +", self.result_script)
        self.assertIn("PnL: alerta si el real empeora más de", self.result_script)

    def test_result_can_download_the_complete_comparison_as_excel_compatible_csv(self) -> None:
        buttons = self.result_page.xpath('//button[@id="download-comparison"]')
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].text, "Descargar tabla CSV")
        self.assertIn("disabled", buttons[0].attrib)
        for token in (
            "function downloadComparisons()", "comparisonRows.map(comparisonCsvRow)",
            "Validación / observaciones", "text/csv;charset=utf-8", "\\uFEFF",
            "link.download = `auditoria_", "URL.revokeObjectURL(url)",
            "#download-comparison').disabled = !comparisonRows.length",
        ):
            self.assertIn(token, self.result_script)
        self.assertIn("if (/^[=+\\-@]/.test(text))", self.result_script)

    def test_result_leads_with_mutually_exclusive_outcomes_and_hides_technical_noise(self) -> None:
        for text in (
            "1 · VEREDICTO", "Pertenencia al modo", "Cerradas correctas / reales abiertas",
            "Parejas con desviación", "Sin pareja", "Dónde está el problema",
            "Ver metodología, cuentas, origen MT5, lotes y reportes",
        ):
            self.assertIn(text, self.result_page_text + self.result_script)
        self.assertIn("let activeFilter = 'all'", self.result_script)
        self.assertIn("activeFilter === 'issues'", self.result_script)
        self.assertIn("['deviation', 'missing', 'invalid'].includes", self.result_script)
        self.assertIn("REAL ABIERTA", self.result_page_text + self.result_script)
        self.assertTrue(self.result_page.xpath('//button[@data-status-filter="open"]'))
        self.assertIn("Pendiente hasta el cierre real", self.result_script)
        self.assertIn("Este no es el resultado de la última ejecución", self.result_script)
        self.assertIn('id="stale-result-warning"', self.result_page_text)
        self.assertIn("33 de 33", self.result_script.replace("${portfolioClosures}", "33").replace("${real}", "33"))

    def test_the_result_says_in_which_account_the_terminal_was_left(self) -> None:
        # El auditor cambia la cuenta del terminal para leer la real; lo que el
        # usuario necesita comprobar es que la dejó en la de pruebas.
        self.assertIn("Terminal devuelto a la cuenta final configurada", self.result_script)
        self.assertIn("function terminalRestore(result)", self.result_script)
        self.assertIn("terminal_restore", self.result_script)
        self.assertIn("password_persisted", self.result_script)
        self.assertIn("reopened_without_password", self.result_script)
        self.assertIn("contraseña persistida · reapertura verificada", self.result_script)
        self.assertIn("terminal_validations", self.result_script)
        self.assertIn("Cuenta tester confirmada por terminal", self.result_script)
        # Sin fila no se afirma nada, y una restauración fallida se marca en rojo.
        self.assertIn("NO REGISTRADO", self.result_script)
        self.assertIn("SIN RESTAURAR", self.result_script)

    def test_restore_account_and_scheduler_have_explicit_editable_dialogs(self) -> None:
        self.assertTrue(self.page.xpath('//button[@id="open-restore-account"]'))
        self.assertTrue(self.page.xpath('//button[@id="open-scheduler"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="restore-account-dialog"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="scheduler-dialog"]'))
        for token in (
            "/live-audit-restore-account", "/api/live-audit-scheduler-config",
            "restoreAccount.configured", "todos los terminales usados",
            "interval_days", "scheduler-interval-days", "environment_override",
        ):
            self.assertIn(token, self.page_text + self.script)
        for obsolete in ("scheduler-check-minutes", "scheduler-startup-delay", "check_interval_minutes", "startup_delay_seconds"):
            self.assertNotIn(obsolete, self.page_text + self.script)

    def test_tick_quality_is_a_required_comparison_gate_per_portfolio(self) -> None:
        self.assertIn("Calidad de datos tick a tick", self.script)
        self.assertIn("no se realiza la comparación", self.script)
        self.assertIn("MT5 no acredita este porcentaje", self.script)
        self.assertIn("min_tick_history_quality_pct: number('min_tick_history_quality_pct')", self.script)

    def test_script_loads_full_history_portfolios_and_the_manager_endpoint(self) -> None:
        self.assertIn("/portfolios?scope=full_history", self.script)
        self.assertGreaterEqual(self.script.count("/live-audit-config`"), 2)
        self.assertIn("if (!form.reportValidity()) return", self.script)

    def test_status_poll_does_not_replace_open_form_controls(self) -> None:
        refresh = self.script.split("async function refreshAuditStates()", 1)[1].split(
            "async function loadSettings()", 1
        )[0]
        self.assertIn("renderAuditOperations(ids)", refresh)
        self.assertNotIn("renderProfiles()", refresh)
        self.assertNotIn("captureDrafts()", refresh)


if __name__ == "__main__":
    unittest.main()
