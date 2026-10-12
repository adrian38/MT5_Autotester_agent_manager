"""Static configuration-screen tests for live audit."""

from __future__ import annotations

import unittest
from pathlib import Path

from lxml import html
from mt5_manager.manager_handler import STATIC_FILES


class LiveAuditConfigurationScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        cls.page_text = (cls.static_dir / "live_audit.html").read_text(encoding="utf-8")
        cls.script = (cls.static_dir / "live_audit.js").read_text(encoding="utf-8")
        cls.autolot_script = (cls.static_dir / "live_audit_autolot.js").read_text(encoding="utf-8")
        cls.result_page_text = (cls.static_dir / "live_audit_result.html").read_text(encoding="utf-8")
        cls.result_script = (cls.static_dir / "live_audit_result.js").read_text(encoding="utf-8")
        cls.analysis_page_text = (cls.static_dir / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.analysis_script = (cls.static_dir / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.manager_script = (cls.static_dir / "app.js").read_text(encoding="utf-8")
        cls.page = html.fromstring(cls.page_text)
        cls.result_page = html.fromstring(cls.result_page_text)
        cls.analysis_page = html.fromstring(cls.analysis_page_text)

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

    def test_autolot_import_uses_the_excel_and_the_historical_account_balance(self) -> None:
        self.assertIn('src="/live_audit_autolot.js"', self.page_text)
        self.assertIn("live_audit_autolot.js", STATIC_FILES)
        self.assertIn("Autolote según saldo y riesgo", self.script)
        self.assertIn("data-autolot-enabled", self.autolot_script)
        self.assertIn("data-autolot-file", self.autolot_script)
        self.assertIn("live-audit-autolot-import", self.autolot_script)
        self.assertIn("riesgo % × 0,01 ÷ DD_0.01", self.autolot_script)
        self.assertIn("volume_expected_source === 'autolot'", self.result_script)

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
        self.assertIn('data-audit-action="analysis"', self.script)
        self.assertIn('data-audit-action="logs"', self.script)
        self.assertIn('role="progressbar"', self.script)
        for stage in ("Preparación", "Extracción real", "Strategy Tester", "Comparación", "Finalizado"):
            self.assertIn(stage, self.script)
        self.assertFalse(self.page.xpath('//dialog[@id="audit-result-dialog"]'))
        self.assertTrue(self.page.xpath('//dialog[@id="audit-log-dialog"]'))
        self.assertIn("/live_audit_result.html?node=", self.script)
        self.assertIn("/live_audit_analysis.html?node=", self.script)
        self.assertIn("window.open(url, '_blank')", self.script)
        self.assertIn("configuration_only", self.script)

    def test_marking_a_portfolio_again_recovers_its_own_saved_use(self) -> None:
        # Desmarcar no borra nada: el perfil y las contraseñas siguen en el
        # manager y la última auditoría en el agente. Pero al volver a marcar se
        # minaba un identificador nuevo y todo aquello quedaba huérfano, sin
        # forma de verlo desde la pantalla.
        self.assertIn("function savedAuditFor(portfolioId)", self.script)
        self.assertIn("const saved = savedAuditFor(id);", self.script)
        # La pertenencia la decide `portfolio_id` del perfil guardado: ni el
        # texto del identificador ni lo que estuviera marcado antes.
        self.assertIn(
            "Number(profiles[auditId]?.portfolio_id || 0) === Number(portfolioId)",
            self.script,
        )
        # Y nunca devuelve un uso que ya está marcado.
        self.assertIn("&& !selectedAuditIds.includes(auditId)", self.script)
        # Con varios huérfanos del mismo portafolio manda el más reciente.
        self.assertIn("function auditStamp(auditId)", self.script)
        self.assertIn("Se ha recuperado la configuración guardada", self.script)
        # Sin nada que recuperar se sigue creando un uso en blanco.
        self.assertIn("const auditId = newAuditId(id);", self.script)

    def test_the_analysis_tab_opens_whenever_the_agent_published_an_execution(self) -> None:
        # El manager retira la materia prima y calcula el veredicto al abrir el
        # detalle, así que el estado de la pantalla de configuración puede traer
        # `has_payload` sin `last_result`: con cualquiera de las dos hay análisis.
        self.assertIn("has_payload: Boolean(value.has_payload)", self.script)
        self.assertIn("runtime.last_result || runtime.has_payload", self.script)

    def test_analysis_tab_compares_global_and_per_strategy_curves(self) -> None:
        for text in (
            "Demo / Strategy Tester", "Cuenta real", "Curvas acumuladas",
            "Análisis por estrategia", "PnL cerrado", "Drawdown máximo",
            "Operaciones cerradas",
        ):
            self.assertIn(text, self.analysis_page_text + self.analysis_script)
        for token in (
            "operation_comparisons", "unmatched_real_operations", "cumulativeSeries",
            "renderChart", "strategyGroups", "data-strategy-key", "period_start",
        ):
            self.assertIn(token, self.analysis_script)
        for element_id in (
            "demo-summary", "real-summary", "global-chart", "global-count",
            "strategy-list", "strategy-chart", "strategy-heading", "strategy-count",
            "analysis-error",
        ):
            self.assertTrue(
                self.analysis_page.xpath(f'//*[@id="{element_id}"]'), element_id,
            )

    def test_the_analysis_groups_by_strategy_and_counts_the_unpaired_reals(self) -> None:
        # Dos estrategias pueden compartir el nombre del `.set`; agrupar por él
        # las fundiría en una fila y escondería la que peor reproduce.
        self.assertIn("String(row.strategy || tester.strategy", self.analysis_script)
        self.assertIn("label: row.strategy_set || key", self.analysis_script)
        # La curva real incluye las reales sin pareja: omitirlas falsearía la
        # ganancia de la cuenta, y el recuento deja ver cuántas son.
        self.assertIn("real: [...paired, ...extras]", self.analysis_script)
        self.assertIn("SIN PAREJA", self.analysis_script)

    def test_the_analysis_draws_over_the_audited_period_and_says_what_is_missing(self) -> None:
        # El eje de tiempo es el periodo auditado y no la extensión de las
        # operaciones: así las curvas de dos estrategias son comparables entre
        # sí, y una auditoría de una sola operación no se dibuja en los extremos.
        self.assertIn("periodWindow", self.analysis_script)
        self.assertIn("chartBounds([demo, real], window)", self.analysis_script)
        # Sin materia prima no se enseña un PnL de cero, que se leería como que
        # la cuenta no ganó nada; y un fallo del análisis se dice tal cual.
        self.assertIn("El manager no pudo analizar esta ejecución", self.analysis_script)
        self.assertIn(
            "no guardó la comparación operación por operación", self.analysis_script,
        )

    def test_the_analysis_simulates_another_lot_per_strategy_on_both_sides(self) -> None:
        self.assertTrue(self.analysis_page.xpath('//button[@id="open-lots"]'))
        self.assertTrue(self.analysis_page.xpath('//dialog[@id="lots-dialog"]'))
        self.assertTrue(self.analysis_page.xpath('//table/tbody[@id="lots-body"]'))
        for button in ("lots-apply", "lots-autolot", "lots-reset", "lots-cancel", "lots-close"):
            self.assertTrue(self.analysis_page.xpath(f'//button[@id="{button}"]'), button)
        self.assertTrue(self.analysis_page.xpath('//section[@id="simulation-notice"]'))
        # Los dos lados por estrategia, cada uno con su propio lote.
        self.assertIn("data-lot-side=\"${side}\"", self.analysis_script)
        self.assertIn("sideSeries(group.demo, group.strategy, 'demo')", self.analysis_script)
        self.assertIn("sideSeries(group.real, group.strategy, 'real')", self.analysis_script)
        # Se reescala operación a operación por su volumen: un factor único por
        # estrategia daría un PnL inventado cuando operó varios volúmenes.
        self.assertIn("return profit / volume * lot;", self.analysis_script)
        self.assertIn("function observedLots(trades)", self.analysis_script)

    def test_autolot_recalculates_each_demo_operation_with_its_historical_lot(self) -> None:
        self.assertIn("row.limits?.volume_expected_source === 'autolot'", self.analysis_script)
        self.assertIn("row.limits.volume_expected_real", self.analysis_script)
        self.assertIn("trade.autolot_volume", self.analysis_script)
        self.assertIn("applyLots({$mode: 'autolot'})", self.analysis_script)
        self.assertIn("las demás conservan el lote ejecutado", self.analysis_script)
        self.assertIn("La curva Demo usa el autolote del saldo real", self.analysis_script)
        # Las demo sin pareja entran en la simulación: el manager les publica el
        # lote esperado y la pantalla lo dice, en vez de dejarlas en el backtest.
        self.assertIn("operaciones demo sin pareja en real", self.analysis_script)
        self.assertIn("saldo de la cuenta real a la hora de su apertura", self.analysis_script)
        self.assertIn("function lotList(lots)", self.analysis_script)
        self.assertIn("function autolotTrades(group)", self.analysis_script)

    def test_the_verdict_declares_which_lot_it_expected_in_real(self) -> None:
        self.assertIn("tolerances.volume_basis === 'autolot_balance_risk'", self.result_script)
        self.assertIn("Lote esperado: autolote por saldo de la cuenta real y riesgo", self.result_script)
        self.assertIn("Lote esperado: el lote real configurado por estrategia", self.result_script)
        # Un faltante también enseña el lote que se esperaba y de dónde salió.
        self.assertIn(
            "volume_expected_basis === 'real_balance_at_tester_open_time'", self.result_script,
        )
        self.assertIn("leído a la hora de apertura del tester", self.result_script)

    def test_the_autolot_card_says_the_balance_is_the_real_account(self) -> None:
        # El tester corrió su backtest con un lote fijo: el autolote describe
        # sólo lo que la cuenta real habría usado.
        self.assertIn("saldo histórico de la cuenta real", self.autolot_script)
        self.assertIn("el tester ejecutó su backtest con un lote fijo", self.autolot_script)
        self.assertIn("saldo de la cuenta real × riesgo % × 0,01 ÷ DD_0.01", self.autolot_script)

    def test_the_simulation_recalculates_the_drawdown_and_never_touches_the_audit(self) -> None:
        # Con otro lote el drawdown del veredicto deja de valer, así que se
        # recalcula sobre la curva con el mismo criterio que `_drawdown`.
        self.assertIn("function curveDrawdown(series)", self.analysis_script)
        self.assertIn("curveDrawdown(cumulativeSeries(trades))", self.analysis_script)
        self.assertNotIn("result.tester_drawdown", self.analysis_script)
        self.assertNotIn("result.real_drawdown", self.analysis_script)
        # Una real sin pareja no es de ninguna estrategia: nunca se reescala.
        self.assertIn("nunca se reescalan", self.analysis_page_text + self.analysis_script)
        self.assertIn("profit: Number(trade.profit || 0)", self.analysis_script)
        # Es una simulación del navegador: no escribe en el perfil ni reanaliza.
        self.assertIn("live-audit-lots:${nodeId}:${auditId}", self.analysis_script)
        self.assertIn("SIMULACIÓN", self.analysis_script)
        self.assertIn("No cambia la auditoría", self.analysis_page_text)
        self.assertNotIn("real_strategy_lots", self.analysis_script)
        self.assertNotIn("method: 'POST'", self.analysis_script)
        # Y dice lo que el modelo lineal no sabe.
        self.assertIn("no modela deslizamiento, margen ni stop out", self.analysis_script)

    def test_the_analysis_compares_each_strategy_with_its_stored_history(self) -> None:
        self.assertTrue(self.analysis_page.xpath('//table/tbody[@id="degradation-body"]'))
        self.assertTrue(self.analysis_page.xpath('//*[@id="degradation-count"]'))
        self.assertTrue(self.analysis_page.xpath('//*[@id="degradation-warning"]'))
        for verdict in (
            "DEGRADA", "AVISO", "NORMAL", "INSUFICIENTE",
            "SIN OPERACIONES", "SIN HISTÓRICO", "SIN DISPERSIÓN HISTÓRICA",
            "MEJOR QUE EL HISTÓRICO",
        ):
            self.assertIn(verdict, self.analysis_script, verdict)
        # El histórico corre a 0,01 y la auditoría al lote del portafolio: sin
        # normalizar por lote se compararía el tamaño de la posición.
        self.assertIn("por lote", self.analysis_page_text)
        self.assertIn("/strategy-history", self.analysis_script)
        # Petición aparte: abre la base del agente y no puede retrasar las curvas.
        self.assertIn("async function loadDegradation()", self.analysis_script)
        self.assertIn("function degradationUnavailable(", self.analysis_script)

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

    def test_the_comparison_table_is_symmetric_between_tester_and_real(self) -> None:
        # La tabla estaba indexada por operación del tester, así que una real
        # que ningún resultado reclamó no podía salir nunca en ella: el veredicto
        # decía «3 reales» sin pareja y la tabla, los filtros, el contador y el
        # CSV sólo hablaban del otro sentido. Ahora son filas de la misma tabla.
        self.assertIn("function extraComparisonMarkup(row)", self.result_script)
        self.assertIn(
            "row.status === 'extra' ? extraComparisonMarkup(row) : comparisonMarkup(row)",
            self.result_script,
        )
        self.assertIn("REAL SIN TESTER", self.result_script)
        self.assertIn('data-status="extra"', self.result_script)
        self.assertIn(
            "comparisonRows = [...testerRows, ...(detail.unmatched_real_operations || [])]",
            self.result_script,
        )
        self.assertTrue(self.result_page.xpath('//button[@data-status-filter="extra"]'))
        self.assertEqual(
            self.result_page.xpath('//button[@data-status-filter="extra"]')[0].text,
            "Real sin tester",
        )
        # El aviso de ejecución antigua mira sólo las filas del tester: una real
        # suelta no demuestra que haya trazabilidad por operación.
        self.assertIn("warning.hidden = Boolean(testerRows.length)", self.result_script)
        # La sección dedicada se conserva y dice dónde están las mismas filas.
        self.assertTrue(self.result_page.xpath('//section[@id="extra-section"]'))
        self.assertIn("bajo el filtro «Real sin tester»", self.result_page_text)

    def test_result_can_download_the_complete_comparison_as_excel_compatible_csv(self) -> None:
        buttons = self.result_page.xpath('//button[@id="download-comparison"]')
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].text, "Descargar tabla CSV")
        self.assertIn("disabled", buttons[0].attrib)
        for token in (
            "function downloadComparisons()", "comparisonRows.map(csvRowFor)",
            "Validación / observaciones", "text/csv;charset=utf-8", "\\uFEFF",
            "link.download = `auditoria_", "URL.revokeObjectURL(url)",
            "#download-comparison').disabled = !comparisonRows.length",
        ):
            self.assertIn(token, self.result_script)
        self.assertIn("if (/^[=+\\-@]/.test(text))", self.result_script)
        # La descarga tiene que llevar lo mismo que la tabla: las reales sin
        # pareja también, o uno se baja la auditoría creyéndola completa.
        self.assertIn("function extraCsvRow(row)", self.result_script)
        self.assertIn(
            "row.status === 'extra' ? extraCsvRow(row) : comparisonCsvRow(row)",
            self.result_script,
        )

    def test_result_leads_with_mutually_exclusive_outcomes_and_hides_technical_noise(self) -> None:
        for text in (
            "1 · VEREDICTO", "Pertenencia al modo", "Cerradas correctas / reales abiertas",
            "Parejas con desviación", "Sin pareja", "Dónde está el problema",
            "Ver metodología, cuentas, origen MT5, lotes y reportes",
        ):
            self.assertIn(text, self.result_page_text + self.result_script)
        self.assertIn("let activeFilter = 'all'", self.result_script)
        self.assertIn("activeFilter === 'issues'", self.result_script)
        self.assertIn("['deviation', 'missing', 'extra', 'invalid'].includes", self.result_script)
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
