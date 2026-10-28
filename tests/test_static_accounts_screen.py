"""La portada de cuentas: su HTML, su enrutado y lo que su JS no recalcula.

En este equipo no hay runtime de JavaScript, así que las pantallas se
comprueban por el texto de sus ficheros. Lo que importa aquí es que la página
**pinte** y no decida: el tono, el prorrateo del mes y la conversión de moneda
los calcula el manager, y dos cálculos del mismo número acabarían diciendo
cosas distintas.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from lxml import html

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import manager_handler

STATIC = Path(__file__).resolve().parents[1] / "mt5_manager" / "static"
PAGE = STATIC / "accounts.html"
SCRIPT = (STATIC / "accounts.js").read_text(encoding="utf-8")
MOUNTED = (STATIC / "live_account_schedule.js").read_text(encoding="utf-8")
MONTHLY = (STATIC / "live_account_monthly.js").read_text(encoding="utf-8")
STYLE = (STATIC / "accounts.css").read_text(encoding="utf-8")
TREE = html.fromstring(PAGE.read_text(encoding="utf-8"))


def ids_of(script: str) -> set[str]:
    return set(re.findall(r"querySelector\('#([\w-]+)'\)", script))


class RoutingTests(unittest.TestCase):
    def test_the_root_serves_the_accounts_page(self) -> None:
        source = (
            Path(manager_handler.__file__).read_text(encoding="utf-8")
        )
        self.assertIn('if parsed.path in {"/", "/index.html"}:', source)
        self.assertRegex(
            source,
            r'if parsed\.path in \{"/", "/index\.html"\}:\s*\n\s*self\._send_file\('
            r'STATIC_DIR / "accounts\.html"\)',
        )

    def test_the_old_front_page_keeps_a_url_of_its_own(self) -> None:
        source = Path(manager_handler.__file__).read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r'if parsed\.path == "/live_portfolios\.html":\s*\n\s*self\._send_file\('
            r'STATIC_DIR / "dashboard\.html"\)',
        )

    def test_both_assets_are_servable(self) -> None:
        self.assertLessEqual(
            {"accounts.js", "accounts.css", "live_account_monthly.js"},
            manager_handler.STATIC_FILES,
        )

    def test_the_two_front_pages_link_to_each_other(self) -> None:
        dashboard = (STATIC / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('href="/">Cuentas en real</a>', dashboard)
        self.assertIn('href="/live_portfolios.html">Portafolios activos</a>',
                      PAGE.read_text(encoding="utf-8"))


class PageTests(unittest.TestCase):
    def test_every_element_the_script_looks_for_exists(self) -> None:
        missing = sorted(
            identifier for identifier in ids_of(SCRIPT)
            if not TREE.xpath(f'//*[@id="{identifier}"]')
        )
        self.assertEqual(missing, [], f"El JS busca ids que no existen: {missing}")

    def test_the_header_has_the_refresh_and_its_state(self) -> None:
        for identifier in ("refresh", "refresh-state"):
            self.assertTrue(TREE.xpath(f'//*[@id="{identifier}"]'), identifier)

    def test_the_manager_restart_closes_the_header_and_uses_the_real_endpoint(self) -> None:
        self.assertTrue(TREE.xpath('//*[@id="restart-manager"]'))
        self.assertIn("restartManagerButton.addEventListener('click', restartManager)", SCRIPT)
        self.assertIn("fetch('/api/manager/restart'", SCRIPT)
        self.assertIn("fetch('/api/manager/restart?lines=80'", SCRIPT)

    def test_the_snapshot_controls_belong_only_to_the_accounts_page(self) -> None:
        """La cabecera abre el sondeo y la acción manual vive en su diálogo."""
        page = PAGE.read_text(encoding="utf-8")
        self.assertNotIn('id="snapshot-now"', page)
        self.assertNotIn('id="schedule-dialog"', page)
        self.assertIn("/live_account_schedule.js", page)

    def test_the_totals_cover_yesterday_and_both_months(self) -> None:
        for identifier in ("total-daily", "total-monthly", "total-previous-month"):
            self.assertTrue(TREE.xpath(f'//*[@id="{identifier}"]'))
        month_totals = TREE.xpath('//*[contains(@class, "total-months")]')[0]
        self.assertEqual(
            month_totals.xpath('.//strong/@id'),
            ["total-monthly", "total-previous-month"],
        )
        self.assertIn("totals.previous_month", SCRIPT)
        self.assertIn("data.previous_month", SCRIPT)
        self.assertIn("Total ayer", PAGE.read_text(encoding="utf-8"))

    def test_the_previous_month_does_not_repeat_the_comparison_in_words(self) -> None:
        for phrase in ("Este mes va mejor", "Este mes va peor", "Este mes cae"):
            self.assertNotIn(phrase, SCRIPT)

    def test_the_manager_restart_is_the_same_red_action_as_on_other_pages(self) -> None:
        css = (STATIC / "accounts.css").read_text(encoding="utf-8")
        self.assertIn(".danger{", css)
        self.assertIn("color:var(--danger)", css)

    def test_each_account_can_be_included_in_or_excluded_from_the_totals(self) -> None:
        self.assertIn('class="total-account-toggle"', SCRIPT)
        self.assertIn("accountIsIncluded(card) ? ' checked' : ''", SCRIPT)
        self.assertIn("const selected = all.filter(accountIsIncluded)", SCRIPT)
        for key in ("daily_display", "monthly_display", "previous_month_display"):
            self.assertIn(f"sum('{key}')", SCRIPT)
        self.assertIn("live-account-total-exclusions", SCRIPT)
        self.assertIn("/api/live-account-total-selection", SCRIPT)
        self.assertIn("await syncTotalSelection(data)", SCRIPT)
        self.assertIn("localStorage.removeItem(TOTAL_SELECTION_KEY)", SCRIPT)
        self.assertIn("cardsEl.addEventListener('change'", SCRIPT)

    def test_the_dialog_can_choose_any_day_of_the_week(self) -> None:
        """Los siete días salen de la lista, no de siete casillas escritas."""
        names = re.search(r"const WEEKDAYS = \[([^\]]+)\]", MOUNTED)
        self.assertIsNotNone(names)
        self.assertEqual(len(re.findall(r"'[^']+'", names.group(1))), 7)
        self.assertIn("WEEKDAYS.map((name, index)", MOUNTED)
        self.assertIn('data-weekday="${index}"', MOUNTED)

    def test_the_dialog_edits_every_field_of_the_cron(self) -> None:
        for identifier in (
            "account-cron-enabled", "account-cron-hour", "account-cron-minute",
            "account-cron-timezone", "account-cron-retries",
            "account-cron-retry-delay", "account-cron-overlap",
            "account-cron-currency", "account-cron-rates",
        ):
            self.assertIn(f'id="{identifier}"', MOUNTED)

    def test_the_dialog_says_the_rates_are_written_by_hand(self) -> None:
        self.assertIn("no tiene ninguna fuente de cambios", MOUNTED)
        self.assertIn("no se suma", MOUNTED)

    def test_the_schedule_button_mounts_only_on_the_accounts_header(self) -> None:
        self.assertIn(".header-actions", MOUNTED)
        self.assertIn("Sondear ahora", MOUNTED)
        self.assertIn("Sondeo de cuentas", MOUNTED)
        self.assertIn("/live_account_schedule.js", PAGE.read_text(encoding="utf-8"))
        for name in ("index.html", "dashboard.html"):
            self.assertNotIn(
                "/live_account_schedule.js",
                (STATIC / name).read_text(encoding="utf-8"), name,
            )
        self.assertIn('id="account-snapshot-now"', MOUNTED)
        self.assertLess(
            MOUNTED.index('id="account-snapshot-now"'),
            MOUNTED.index('id="account-cron-save"'),
        )

    def test_the_page_loads_the_bell_of_the_audit_cron(self) -> None:
        sources = TREE.xpath("//script/@src")
        self.assertIn("/live_audit_schedule.js", sources)
        self.assertIn("/accounts.js", sources)


class CriterionTests(unittest.TestCase):
    def test_yesterdays_profit_is_coloured_by_its_sign(self) -> None:
        self.assertIn("if (Number(value) > 0) return 'good'", SCRIPT)
        self.assertIn("if (Number(value) < 0) return 'bad'", SCRIPT)
        self.assertIn("signedTone(card.daily.profit)", SCRIPT)
        self.assertIn("metricRow('Ayer'", SCRIPT)

    def test_the_tones_come_from_the_payload_and_are_not_recomputed(self) -> None:
        for name in ("profit_tone", "drawdown_tone"):
            self.assertIn(name, SCRIPT)
        # Si la pantalla comparase por su cuenta, aquí habría una comparación
        # entre el valor del mes y la media. No la hay a propósito.
        self.assertNotIn("monthly.profit >", SCRIPT)
        self.assertNotIn("expected_month.profit >", SCRIPT)

    def test_the_pace_of_the_month_comes_prorated_from_the_manager(self) -> None:
        self.assertIn("profit_prorated", SCRIPT)
        self.assertNotIn("* 21 / 31", SCRIPT)

    def test_the_pace_says_it_is_the_average_month_and_not_this_one(self) -> None:
        """«Al ritmo del mes» se lee como el ritmo de **este** mes.

        Y es lo contrario: son los € que el mes medio del backtest llevaría a
        estas alturas. Sin «medio» el número se entiende al revés.
        """
        self.assertIn("Al ritmo del mes medio (", SCRIPT)
        self.assertIn("% del mes)", SCRIPT)

    def test_the_pace_is_coloured_with_the_same_verdict_as_the_big_number(self) -> None:
        """Es la referencia que decide el color de arriba, así que se pinta.

        Verde si la cuenta va igual o por encima y rojo si va por debajo: el
        criterio es el de `tone()`, no uno nuevo, o la nota y el número
        podrían discrepar.
        """
        self.assertIn('<em class="${escapeHtml(card.monthly.profit_tone', SCRIPT)
        # El gancho de color ya existía en la hoja y nadie lo usaba.
        self.assertIn(".metric small em.good", STYLE)
        self.assertIn(".metric small em.bad", STYLE)

    def test_the_card_says_how_many_years_the_mean_averages(self) -> None:
        self.assertIn("años", SCRIPT)
        self.assertIn("Sin media del backtest", SCRIPT)

    def test_the_manual_snapshot_warns_that_it_pauses_the_pipeline(self) -> None:
        self.assertIn("pausa", MOUNTED)
        self.assertIn("window.confirm", MOUNTED)

    def test_arming_the_cron_asks_before_saving(self) -> None:
        self.assertIn("Activar el sondeo automático", MOUNTED)

    def test_an_account_with_autolot_says_the_target_moves(self) -> None:
        self.assertIn("Autolote activo", SCRIPT)
        self.assertIn("está medida a un lote fijo", SCRIPT)

    def test_a_promise_measured_at_the_month_lot_says_so(self) -> None:
        """La frase cambia cuando la media ya está al lote del mes.

        Decir «medida a un lote fijo» cuando ya no lo está sería peor que no
        decir nada: el usuario corregiría a ojo una comparación ya corregida.
        """
        self.assertIn("const AUTOLOT_BASIS = 'autolot_mean_balance'", SCRIPT)
        self.assertIn("expected.lot_basis !== AUTOLOT_BASIS", SCRIPT)
        self.assertIn("medida al lote que la cuenta", SCRIPT)
        self.assertIn("su saldo medio de", SCRIPT)
        # El rango de lotes es lote y no dinero, y las que se quedaron sin
        # regla en el Excel se cuentan en vez de callarse.
        self.assertIn("plainNumber(expected.lot_low)", SCRIPT)
        self.assertIn("sin regla en el Excel", SCRIPT)

    def test_a_rescaled_card_says_its_euros_are_not_what_the_account_made(self) -> None:
        # Con una columna marcada en la simulación de lotes del portafolio, el
        # beneficio de esos símbolos está llevado a otro tamaño. Un número
        # reescalado sin decirlo se lee como saldo.
        self.assertIn("presentation", SCRIPT)
        self.assertIn("presentados al lote del tester en", SCRIPT)
        self.assertIn("donde la cuenta ejecutó otro tamaño no son lo que ingresó", SCRIPT)
        self.assertIn("La media del backtest está medida al mismo lote", SCRIPT)
        # Y los avisos de lo que se quedó sin escalar salen con él.
        self.assertIn("(card.presentation || {}).warnings", SCRIPT)
        # Un lote no es dinero: sin símbolo de moneda y sin redondeo a dos.
        self.assertIn("function plainNumber(value)", SCRIPT)
        self.assertIn("maximumFractionDigits: 8", SCRIPT)

    def test_account_cards_reuse_the_active_portfolio_aliases(self) -> None:
        self.assertIn("/portfolio-aliases?ids=", SCRIPT)
        self.assertIn("aliasOf(card.node_id, Number(row.portfolio_id || 0))", SCRIPT)
        self.assertIn("|| row.name || `Portafolio #${row.portfolio_id}`", SCRIPT)
        self.assertIn("if (await loadAliases(data.accounts || [])) render(data)", SCRIPT)
        self.assertIn("const missing = [...ids].filter", SCRIPT)

    def test_the_card_never_flashes_the_number_before_the_name(self) -> None:
        """La tarjeta se pinta antes de que el alias llegue, a propósito.

        Medido el 2026-10-23 contra el manager en marcha: 4 ms hasta
        «Portafolio #67» y 48 hasta «AXI-Select_Test». Lo recordado hace que el
        primer pintado ya sea el bueno, sin bloquear la tarjeta esperándolo.
        """
        self.assertIn("restoreAliases();\nrefresh();", SCRIPT)
        self.assertIn("localStorage.getItem(ALIAS_CACHE_KEY)", SCRIPT)
        self.assertIn("localStorage.setItem(\n      ALIAS_CACHE_KEY", SCRIPT)
        # Recordar no es saber: se vuelve a preguntar una vez por sesión, o un
        # portafolio renombrado se quedaría con el nombre viejo para siempre.
        self.assertIn("const wanted = asked ? missing : [...ids];", SCRIPT)
        self.assertIn("askedNodes.add(nodeId);", SCRIPT)
        self.assertIn(
            "return JSON.stringify(fresh) !== JSON.stringify(known);", SCRIPT,
        )

    def test_each_card_opens_its_own_month_by_month_chart(self) -> None:
        self.assertIn('class="month-chart-open"', SCRIPT)
        self.assertIn("cardsEl.addEventListener('click'", SCRIPT)
        self.assertIn("window.LiveAccountMonthly.open(", SCRIPT)
        self.assertIn("/live_account_monthly.js", PAGE.read_text(encoding="utf-8"))
        # Una cuenta sin credenciales o sin sondear no tiene ningun cierre
        # guardado: el boton no sale en vez de abrir un grafico vacio.
        self.assertIn("['blocked', 'never_read'].includes(card.state || '')", SCRIPT)

    def test_the_chart_paints_the_numbers_the_manager_measured(self) -> None:
        """La pantalla dibuja barras; los importes y la DD vienen medidos."""
        self.assertIn("/monthly`", MONTHLY)
        for name in ("totals.drawdown", "totals.worst_month_drawdown", "month.drawdown"):
            self.assertIn(name, MONTHLY)
        # Si el grafico midiera por su cuenta, aqui habria una curva acumulada
        # y un maximo de las filas mensuales. No los hay a proposito.
        for forbidden in ("balance +=", "Math.max(...months.map(month => month.drawdown"):
            self.assertNotIn(forbidden, MONTHLY)

    def test_each_month_of_the_span_shows_its_average_and_whether_it_met_it(self) -> None:
        self.assertIn("month.average_profit", MONTHLY)
        self.assertIn("month.meets_average ? '✓' : '×'", MONTHLY)
        self.assertIn("media ${money(month.average_profit, currency)}", MONTHLY)
        self.assertIn(".monthly-average.good{fill:var(--accent2)}", STYLE)
        self.assertIn(".monthly-average.bad{fill:var(--danger)}", STYLE)

    def test_the_chart_marks_where_this_portfolio_starts_instead_of_cutting(self) -> None:
        """Cortar dejaría seis de nueve cuentas con una sola barra.

        Casi todos los usos en real se guardaron a mediados de octubre sobre
        cuentas que llevaban meses operando, así que las barras anteriores se
        atenúan y se marca el mes de entrada: son del mismo dinero pero no de
        este portafolio.
        """
        self.assertIn("month.tracked === false", MONTHLY)
        self.assertIn("monthly-boundary", MONTHLY)
        self.assertIn("Antes de este portafolio", MONTHLY)
        self.assertIn(".monthly-bar.before{opacity:", STYLE)
        self.assertIn(".monthly-bar.partial{opacity:", STYLE)
        # El histórico permanece en la gráfica, pero no se presenta como un
        # récord del portafolio activo en las tarjetas superiores.
        self.assertNotIn("el mejor es anterior al portafolio", MONTHLY)

    def test_the_tracked_portfolio_is_linked_to_its_history(self) -> None:
        """Es donde se verifica cuándo se guardó, y qué lleva dentro."""
        for source in (SCRIPT, MONTHLY):
            self.assertIn("/portfolio_history.html?node=", source)
            self.assertIn("&portfolio=", source)
        # La tarjeta dice el día del mes, no sólo el mes: un portafolio que
        # entró el 30 deja septiembre con un día.
        self.assertIn("row.created_day", SCRIPT)
        self.assertIn("desde el ${parts[2]}-${parts[1]}", SCRIPT)

    def test_the_drawdown_is_drawn_downwards_and_says_why(self) -> None:
        css = (STATIC / "accounts.css").read_text(encoding="utf-8")
        self.assertIn(".monthly-bar.drawdown{fill:var(--warn)}", css)
        self.assertIn(".monthly-bar.profit{fill:var(--accent)}", css)
        self.assertIn("no es la suma ni el máximo de los\n      mensuales", MONTHLY)

    def test_the_totals_say_which_currencies_are_not_summed(self) -> None:
        self.assertIn("missing_rates", SCRIPT)
        self.assertIn("no se suman", SCRIPT)

    def test_the_two_months_are_side_by_side_but_their_metrics_are_vertical(self) -> None:
        css = (STATIC / "accounts.css").read_text(encoding="utf-8")
        self.assertIn('<div class="month-comparison">', SCRIPT)
        self.assertIn(".metric-row{display:grid;gap:12px;", css)
        self.assertIn(
            ".month-comparison{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));"
            "grid-template-rows:auto auto auto;column-gap:1px;row-gap:0;",
            css,
        )
        self.assertIn(
            ".month-comparison .metric-row{grid-row:span 3;grid-template-rows:subgrid;"
            "align-content:start}",
            css,
        )
        self.assertIn(".metric{display:grid;align-content:start;gap:4px}", css)
        self.assertIn(".month-comparison{grid-template-columns:1fr;row-gap:1px}", css)
        self.assertNotIn(".metric-row{display:grid;grid-template-columns:", css)
        self.assertIn("@media(max-width:480px){", css)


if __name__ == "__main__":
    unittest.main()
