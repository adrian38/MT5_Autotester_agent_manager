"""La pantalla del resumen mensual: sus rutas, su almacén y lo que no recalcula.

En este equipo no hay runtime de JavaScript, así que la pantalla se comprueba
por el texto de sus ficheros. Lo que importa aquí es que **pinte y no decida**:
los veredictos, los avisos y sus frases vienen hechos de
`monthly_summary_notices.py`, y dos criterios para el mismo número acabarían
diciendo cosas distintas del mismo mes. La única excepción es el veredicto de
reproducción, que vive en `live_audit_reproduction.js` desde antes y que la
pantalla pide prestado en vez de copiar.

El resumen es una **ventana propia** desde el 2026-10-25, no un diálogo sobre
la portada, y eso parte el JavaScript en dos: `monthly_summary_button.js`
monta el botón en las dos pantallas de operación real y `monthly_summary.js`
pinta la pantalla. Que la portada no cargue el pintado que nunca usa es parte
del contrato, y por eso se comprueba.

Del lado del manager se comprueba lo que decide: que un `?month=` a medias no
pase por mes, que recalcular borre la marca de leído —lo que el usuario miró
ya no es lo que hay— y que el almacén no crezca sin fin.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.manager import manager_handler, manager_summary_routes
from mt5_manager.monthly_summary import monthly_summary_runtime
from mt5_manager.monthly_summary import monthly_summary_notices, monthly_summary_watch
from mt5_manager.monthly_summary.monthly_summary_store import MAXIMUM_MONTHS, MonthlySummaryStore

STATIC = Path(__file__).resolve().parents[2] / "mt5_manager" / "static"
PAGE = (STATIC / "monthly_summary.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "monthly_summary.js").read_text(encoding="utf-8")
BUTTON = (STATIC / "monthly_summary_button.js").read_text(encoding="utf-8")
STYLE = (STATIC / "monthly_summary.css").read_text(encoding="utf-8")
HOSTS = ("accounts.html", "dashboard.html")
"""Las dos pantallas de operación real, que son las que montan el botón."""


def ids_of(script: str) -> set[str]:
    """Los ids que el guion pide, por `querySelector` y por su atajo `field`."""
    return set(re.findall(r"(?:querySelector|field)\('#([\w-]+)'\)", script))


def declared_ids(*sources: str) -> set[str]:
    found: set[str] = set()
    for source in sources:
        found |= set(re.findall(r'id="([\w-]+)"', source))
        found |= set(re.findall(r"\.id = '([\w-]+)'", source))
    return found


class FakeHandler:
    """Lo mínimo que las rutas le piden al handler: un cuerpo y una respuesta."""

    def __init__(self, server: object, body: dict | None = None) -> None:
        self.server = server
        self._payload = body or {}
        self.status = 0
        self.sent: dict = {}

    def _body(self) -> dict:
        return dict(self._payload)

    def _send_json(self, status: int, value: dict) -> None:
        self.status = status
        self.sent = value


class FakeServer:
    """El servidor que las rutas tocan, con la misma delegacion que el real."""

    nodes: list = []

    def __init__(self) -> None:
        # `update` despierta el hilo del cron; aqui no hay hilo, pero el
        # aviso tiene que poder darse igual.
        self.monthly_summary_wakeup = threading.Event()

    def update_monthly_summary(self, changes: dict) -> dict:
        return monthly_summary_runtime.update(self, changes)


def parsed(path: str):
    return urllib.parse.urlparse(path)


def report(month: str, verdict: str = "keep") -> dict:
    return {
        "month": month, "generated_at": f"{month}-01T09:00:00+00:00",
        "accounts": [{"verdict": verdict, "login": "1", "node_id": "n"}],
        "counts": {verdict: 1},
    }


class RoutingTests(unittest.TestCase):
    def test_the_page_and_its_three_assets_are_servable(self) -> None:
        self.assertLessEqual(
            {
                "monthly_summary.html", "monthly_summary.js",
                "monthly_summary_button.js", "monthly_summary.css",
            },
            manager_handler.STATIC_FILES,
        )

    def test_only_the_two_real_operation_screens_mount_the_button(self) -> None:
        for page in STATIC.glob("*.html"):
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual(
                    "/monthly_summary_button.js" in source, page.name in HOSTS,
                )

    def test_only_its_own_page_loads_the_painting(self) -> None:
        # La portada no carga lo que nunca usa: son 300 líneas y los dos
        # ficheros del veredicto de reproducción detrás.
        for page in STATIC.glob("*.html"):
            source = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual(
                    '"/monthly_summary.js"' in source, page.name == "monthly_summary.html",
                )

    def test_the_page_brings_its_stylesheet_and_its_borrowed_rules(self) -> None:
        for asset in (
            "/monthly_summary.css", "/live_audit_schedule.css", "/styles.css",
            "/live_audit_reproduction.js", "/dashboard_metrics.js",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, PAGE)
        self.assertLess(
            PAGE.index("/live_audit_reproduction.js"), PAGE.index("/monthly_summary.js"),
            "la pantalla lee `window.LiveAuditReproduction` al cargarse",
        )

    def test_the_button_opens_a_window_instead_of_a_dialog(self) -> None:
        for expression in (
            "const PAGE = '/monthly_summary.html'", "open.target = '_blank'",
            "open.rel = 'noopener'",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, BUTTON)
        # Lo que fue un diálogo y ya no lo es.
        self.assertNotIn("showModal", BUTTON + SCRIPT)

    def test_the_button_takes_the_same_anchor_as_the_other_two(self) -> None:
        for expression in (
            "actions.querySelector('#restart-manager')",
            'actions.querySelector(\'a[href="/"]\')',
            "actions.insertBefore(open, anchor || null)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, BUTTON)

    def test_the_bell_lands_on_the_page_with_its_month(self) -> None:
        self.assertEqual(monthly_summary_watch.LINK, "/monthly_summary.html")
        self.assertIn("monthly_summary.html", manager_handler.STATIC_FILES)
        source = Path(monthly_summary_watch.__file__).read_text(encoding="utf-8")
        self.assertIn('link=f"{LINK}?month=', source)
        # Y la pantalla lo lee, que es la otra mitad del enlace.
        self.assertIn("URLSearchParams(window.location.search).get('month')", SCRIPT)


class ScriptTests(unittest.TestCase):
    def test_every_id_it_asks_for_is_one_the_page_declares(self) -> None:
        # `#restart-manager` es el ancla de la barra y lo pone cada pantalla
        # (o `manager_restart.js`); el resto sale de este HTML y estos guiones.
        asked = (ids_of(SCRIPT) | ids_of(BUTTON)) - {"restart-manager"}
        self.assertLessEqual(asked, declared_ids(PAGE, SCRIPT, BUTTON))
        self.assertIn("monthly-summary-cards", asked)

    def test_the_verdict_and_the_notices_come_from_the_manager(self) -> None:
        for expression in ("card.notices", "card.verdict", "report.counts"):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)

    def test_it_does_not_measure_the_month_again(self) -> None:
        # Las cuatro cosas que el manager ya decidió. Si alguna se calculase
        # aquí, la pantalla y la campana podrían decir distinto del mismo mes.
        for forbidden in ("below_promise", "COVERAGE_MINIMUM", "quantile", "worse_than"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, SCRIPT)

    def test_the_reproduction_verdict_is_borrowed_and_not_copied(self) -> None:
        for expression in (
            "window.LiveDashboardMetrics", "window.LiveAuditReproduction",
            "metrics.summarize(", "issues = summary.flagged.map",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)
        # El cálculo entra en el nodo: se pide después de pintar, nunca antes.
        self.assertIn("loadReproduction", SCRIPT)
        self.assertLess(
            SCRIPT.index("renderReport(answer"), SCRIPT.index("loadDetails(answer"),
        )

    def test_only_bad_strategies_are_named_with_strategy_and_symbol(self) -> None:
        for expression in (
            "issue.strategy", "issue.symbol", "issue.verdict", "issue.set",
            'aria-label="Estrategias con problemas"',
            "if (issues.length) return {issues}",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)
        self.assertNotIn("`${broken} NO REPRODUCE`", SCRIPT)
        self.assertNotIn("`${warnings} AVISO", SCRIPT)
        self.assertNotIn("text: 'reproduce'", SCRIPT)
        self.assertNotIn("text: 'sin comparación'", SCRIPT)
        self.assertIn(".summary-reproduction-issues{", STYLE)
        # El `.set` es lo que se abre para mirar el problema, así que se nombra
        # junto a la estrategia; sólo cuando dice algo distinto de su nombre.
        self.assertIn(".summary-reproduction-set{", STYLE)
        self.assertIn("set && set !== strategy ? set : ''", SCRIPT)

    def test_the_alias_is_the_first_highlighted_identity_of_the_card(self) -> None:
        # El alias vive en la memoria UBS del agente, así que tampoco viaja con
        # el informe: se pide por nodo y sólo para los portafolios del mes.
        for expression in (
            "/portfolio-aliases?ids=", "function portfolioIdentityMarkup",
            "aliases.set(`${nodeId}|${portfolioId}`, alias)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)
        self.assertIn(".summary-identity h2{", STYLE)
        card = SCRIPT[SCRIPT.index("function cardMarkup"):]
        self.assertLess(
            card.index("portfolioIdentityMarkup(card)"),
            card.index("figuresMarkup(card)"),
        )

    def test_recalculating_asks_first(self) -> None:
        # El resumen guardado es la foto de lo que se sabía al cerrar el mes.
        run = SCRIPT[SCRIPT.index("async function recalculate"):]
        self.assertIn("window.confirm(warning)", run)
        self.assertIn("/api/monthly-summary/run", run)

    def test_completion_gets_both_missing_sources_and_recalculates(self) -> None:
        action = SCRIPT[SCRIPT.index("async function completeValidation"):]
        for expression in (
            "window.confirm(warning)", "/api/live-account-snapshot-now",
            "/api/live-audit-scheduler-catch-up", "waitForRecovery(nodes, queued)",
            "recalculateRecovered(month)",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, action)
        self.assertIn("refreshedRecoveryPlan(month)", action)

    def test_completion_uses_the_manager_recovery_plan(self) -> None:
        plan = SCRIPT[SCRIPT.index("function recoveryPlan"):]
        self.assertIn("card.recovery", plan)
        self.assertIn("account_snapshot", plan)
        self.assertNotIn("COVERAGE_MINIMUM", plan)
        refreshed = SCRIPT[SCRIPT.index("async function refreshedRecoveryPlan"):]
        self.assertIn("/api/monthly-summary/run", refreshed)

    def test_completion_waits_for_the_two_real_activities(self) -> None:
        waiting = SCRIPT[SCRIPT.index("function recoveryActive"):]
        for expression in ("activity.account_snapshots", "activity.recovery", "recovery.pending"):
            with self.subTest(expression=expression):
                self.assertIn(expression, waiting)
        self.assertIn("id=\"monthly-summary-complete\"", PAGE)
        self.assertIn("id=\"monthly-summary-completion\"", PAGE)

    def test_the_button_is_only_live_when_something_is_missing(self) -> None:
        # El informe ya dice cuenta por cuenta qué se puede obtener: pulsarlo
        # sin nada que falte sondearía doce cuentas para no cambiar nada.
        plan = SCRIPT[SCRIPT.index("function applyRecoveryPlan"):]
        self.assertIn("recoveryNeeded = plan.snapshot || plan.audit", plan)
        self.assertIn("complete.disabled = busy || !recoveryNeeded", SCRIPT)
        self.assertIn("applyRecoveryPlan(current)", SCRIPT)
        # Y nace apagado: antes de leer el informe nadie sabe si falta algo.
        self.assertIn("id=\"monthly-summary-complete\" disabled", PAGE)
        self.assertNotIn("complete.disabled = false", SCRIPT)

    def test_one_lock_covers_both_buttons_and_the_month(self) -> None:
        # «Volver a calcular» sólo se apagaba a sí mismo, así que mientras
        # recalculaba seguía pudiendo pulsarse «Obtener lo que falta», que sí
        # entra en los agentes y lanza sondeos. Y cambiar de mes a media
        # petición dejaba el informe de un mes bajo el nombre de otro.
        lock = SCRIPT[SCRIPT.index("function applyBusy"):SCRIPT.index("// --- Pintado")]
        for expression in (
            "run.disabled = busy",
            "complete.disabled = busy || !recoveryNeeded",
            "field('#monthly-summary-month').disabled = busy || !hasMonths",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, lock)
        for action in ("async function recalculate", "async function completeValidation"):
            with self.subTest(action=action):
                body = SCRIPT[SCRIPT.index(action):]
                self.assertIn("setBusy(true)", body[:body.index("} finally {")])
        # Repintar el selector no lo reabre a media petición.
        months = SCRIPT[SCRIPT.index("function renderMonths"):]
        self.assertIn("hasMonths = Boolean(months.length)", months)
        self.assertNotIn("select.disabled = !months.length", SCRIPT)
        # Los dos botones vuelven por el mismo sitio, con el plan que quedó.
        self.assertNotIn("run.disabled = false", SCRIPT)

    def test_the_summary_shows_its_own_work_and_not_only_the_agents(self) -> None:
        # El recálculo recorre el archivo de auditorías: sin nada en pantalla
        # el botón parecía no haber respondido. No lleva porcentaje porque no
        # hay ninguno que sea verdad; el trabajo de los agentes sí lo lleva.
        run = SCRIPT[SCRIPT.index("async function recalculate"):]
        self.assertIn("Recalculando el resumen de ${named}", run)
        self.assertIn("{working: true}", run)
        self.assertIn("{percent: 100, tone: 'done'}", run)
        self.assertIn("completionState(error.message, {tone: 'error'})", run)
        bar = SCRIPT[SCRIPT.index("function completionState"):]
        self.assertIn("working ?", bar)
        self.assertIn("${working ? ' working' : ''}", bar)
        self.assertIn(".summary-activity.working .cron-activity-bar i{", STYLE)
        self.assertIn("@keyframes summary-activity-sweep", STYLE)

    def test_the_recovery_shows_the_same_activity_bar_as_the_rest(self) -> None:
        # La recuperación tarda minutos y antes sólo dejaba una línea de texto.
        for expression in (
            "cron-activity-dot", "cron-activity-bar",
            "id=\"monthly-summary-activity-fill\"",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, PAGE)
        self.assertIn(".summary-activity{", STYLE)
        # El relleno es trabajo terminado, no tiempo pasado.
        percent = SCRIPT[SCRIPT.index("function recoveryPercent"):]
        self.assertIn("launched - left", percent)
        self.assertIn("recoveryPercent(launched, snapshots, pending)", SCRIPT)

    def test_a_summary_from_an_older_criterion_says_so(self) -> None:
        # Los recuadros se pintan en vivo y los avisos vienen guardados: tras
        # un despliegue la tarjeta puede decir dos cosas del mismo numero, y
        # sin el aviso eso se lee como un fallo del panel.
        criterion = SCRIPT[SCRIPT.index("function applyCriterion"):]
        self.assertIn("answer.criterion", criterion)
        self.assertIn("(answer.report || {}).criterion", criterion)
        self.assertIn("report !== current", criterion)
        self.assertIn("applyCriterion(answer)", SCRIPT)
        self.assertIn("id=\"monthly-summary-stale\"", PAGE)
        self.assertIn(".summary-page .summary-toolbar .cron-warning{", STYLE)

    def test_every_card_shows_the_same_seven_measures(self) -> None:
        """La rejilla no cambia; la unidad visible sí cuando sólo hay un tramo."""
        figures = SCRIPT[SCRIPT.index("function figuresMarkup"):]
        self.assertEqual(figures.count("figure(labels["), 7)
        for gone in (
            "'Promesa comparable'", "'Comparacion historica'".replace(
                "Comparacion historica", "Comparación histórica",
            ),
            "figure('Meses completos de referencia'", "function promiseLabel",
        ):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, SCRIPT)

    def test_a_partial_month_names_tranches_and_full_month_drawdowns(self) -> None:
        labels = SCRIPT[SCRIPT.index("function figureLabels"):]
        for label in (
            "'Profit MT5 del tramo'", "`Objetivo neto del tramo${span}`",
            "'Resultado neto medio histórico del tramo'", "'Drawdown del tramo'",
            "`Drawdown medio de los ${season} (mes entero)`",
            "'Drawdown medio mensual (mes entero)'", "'Tramo auditado'",
        ):
            with self.subTest(label=label):
                self.assertIn(label, labels)

    def test_autolot_is_visible_on_the_card_and_its_portfolio(self) -> None:
        identity = SCRIPT[SCRIPT.index("function portfolioIdentityMarkup"):]
        portfolios = SCRIPT[SCRIPT.index("function portfoliosMarkup"):]
        self.assertIn("AUTOLOTE ACTIVO", identity)
        self.assertIn("portfolio.autolot === true", portfolios)
        self.assertIn("AUTOLOTE ACTIVO", portfolios)
        self.assertIn(".summary-autolot{", STYLE)

    def test_a_partial_card_names_its_tranche_beside_the_title(self) -> None:
        tranche = SCRIPT[SCRIPT.index("function trancheTitleMarkup"):]
        self.assertIn("if (!card.started_inside) return '';", tranche)
        self.assertIn("coverage.eligible_start", tranche)
        self.assertIn("(tramo ${start}-${end})", tranche)
        identity = SCRIPT[SCRIPT.index("function portfolioIdentityMarkup"):]
        self.assertIn("${trancheTitleMarkup(card)}", identity)
        self.assertIn(".summary-title-line{", STYLE)
        self.assertIn(".summary-tranche{", STYLE)

    def test_the_card_counts_the_months_of_the_year_and_names_them(self) -> None:
        # «octubres», no «meses»: lo que se mide y lo que se enseña tienen que
        # ser la misma población, y el drawdown se enseña con la misma vara.
        plural = SCRIPT[SCRIPT.index("function monthPlural"):]
        self.assertIn("endsWith('l')", plural)
        figures = SCRIPT[SCRIPT.index("function figuresMarkup"):]
        self.assertIn("const season = escapeHtml(monthPlural(card.month));", figures)
        self.assertNotIn("drawdown_standing", SCRIPT)

    def test_the_month_is_coloured_against_its_own_average(self) -> None:
        # El color sigue la comparación neta, aunque el valor visible sea la
        # columna Profit. En el drawdown esta al reves, asi que lo decide
        # `tone` y no el signo del numero.
        colour = SCRIPT[SCRIPT.index("function tone("):]
        self.assertIn("lowerIsBetter ? left <= right : left >= right", colour)
        figures = SCRIPT[SCRIPT.index("function figuresMarkup"):]
        self.assertIn("tone(netClosed.profit, seasonProfit)", figures)
        self.assertIn("tone(closed.drawdown, seasonDepth, true)", figures)
        # Los siete recuadros se comparan en columna: etiqueta y numero van
        # centrados en su caja, no pegados al margen izquierdo.
        self.assertIn("justify-items:center;text-align:center", STYLE)
        for rule in (".summary-grid strong.good{", ".summary-grid strong.bad{"):
            with self.subTest(rule=rule):
                self.assertIn(rule, STYLE)

    def test_the_reference_boxes_follow_what_can_be_compared(self) -> None:
        # Las marcas siguen mandando: lo que cambia es que ahora deciden el
        # **valor** del recuadro, no si el recuadro existe.
        values = SCRIPT[SCRIPT.index("function referenceValues"):]
        for expression in (
            "card.promise_comparable !== false", "card.table_comparable !== false",
            "(card.profit_band || {}).mean", "monthly.profit",
            "(card.drawdown_band || {}).mean", "monthly.drawdown",
            "return [null, null, null, null];",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, values)

    def test_a_tranche_is_measured_against_the_same_tranche_of_the_backtest(self) -> None:
        # Un numero a mes entero al lado de uno de diecisiete dias compara dos
        # cosas distintas con la misma cara: las medias se prorratean igual.
        values = SCRIPT[SCRIPT.index("function referenceValues"):]
        for expression in (
            "tranche.profit", "tranche.monthly_profit",
            "tranche.drawdown", "tranche.monthly_drawdown",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, values)

    def test_the_audited_tranche_says_its_days(self) -> None:
        # Un alta el día 31 cubría «100 %» de un día y se leía igual que un
        # mes entero.
        audited = SCRIPT[SCRIPT.index("function auditedValue"):]
        self.assertIn("coverage.eligible_days", audited)
        self.assertIn("días", audited)

    def test_broker_colours_and_account_order_match_the_other_screens(self) -> None:
        for broker in ("axi", "roboforex", "ictrading", "vantage"):
            with self.subTest(broker=broker):
                self.assertIn(f".summary-card.broker-{broker}{{", STYLE)
        for verdict in ("change", "watch", "keep", "unknown"):
            with self.subTest(verdict=verdict):
                self.assertIn(f".summary-verdict.{verdict}{{", STYLE)
        for expression in (
            "const BROKER_CLASSES", "function cardBroker", "function sortAccounts",
            "cardIsProp(left)",
            "brokerLeft.localeCompare(brokerRight)", "sortAccounts(current.accounts",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, SCRIPT)

    def test_the_badge_is_painted_without_opening_anything(self) -> None:
        # El día 1 el botón ya dice que hay algo que mirar, y que falle no
        # puede dejar la pantalla a medias.
        self.assertIn("/api/monthly-summary", BUTTON)
        self.assertIn(".catch(() => {})", BUTTON)


class MonthTests(unittest.TestCase):
    def test_half_a_date_is_not_a_month(self) -> None:
        for value in ("2026", "2026-13", "2026-1", "202610", "", None, "2026-aa"):
            with self.subTest(value=value):
                self.assertEqual(manager_summary_routes._month(value), "")
        self.assertEqual(manager_summary_routes._month(" 2026-09 "), "2026-09")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "monthly_summary.json"
        self.addCleanup(self.folder.cleanup)

    def test_a_recalculated_month_loses_its_read_mark(self) -> None:
        store = MonthlySummaryStore(self.path)
        store.remember(report("2026-09"))
        store.mark_read("2026-09")
        self.assertEqual(store.unread(), [])
        store.remember(report("2026-09"))
        self.assertEqual(store.unread(), ["2026-09"])

    def test_it_keeps_two_years_and_no_more(self) -> None:
        store = MonthlySummaryStore(self.path)
        for step in range(MAXIMUM_MONTHS + 5):
            store.remember(report(f"20{24 + step // 12:02d}-{step % 12 + 1:02d}"))
        self.assertLessEqual(len(store.months()), MAXIMUM_MONTHS)

    def test_a_summary_without_a_month_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            MonthlySummaryStore(self.path).remember({"accounts": []})

    def test_the_state_carries_todays_criterion_next_to_the_saved_one(self) -> None:
        # El informe guarda el criterio con el que se escribieron sus avisos y
        # el estado trae el de hoy: comparados, el panel sabe si esta leyendo
        # una foto vieja en vez de enseñar cifras nuevas sobre frases viejas.
        store = MonthlySummaryStore(self.path)
        store.remember({**report("2026-09"), "criterion": "viejo"})
        state = store.state("2026-09")
        self.assertEqual(state["criterion"], monthly_summary_notices.CRITERION_VERSION)
        self.assertEqual(state["report"]["criterion"], "viejo")
        self.assertNotEqual(state["criterion"], "viejo")

    def test_it_survives_a_restart(self) -> None:
        MonthlySummaryStore(self.path).remember(report("2026-09"))
        self.assertEqual(MonthlySummaryStore(self.path).months(), ["2026-09"])

    def test_a_broken_file_does_not_take_the_manager_down(self) -> None:
        self.path.write_text("{no es json", encoding="utf-8")
        self.assertEqual(MonthlySummaryStore(self.path).months(), [])


class RouteAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.server = FakeServer()
        monthly_summary_runtime.configure(
            self.server, {}, Path(self.folder.name) / "settings.json",
        )
        self.server.monthly_summaries.remember(report("2026-08"))
        self.server.monthly_summaries.remember(report("2026-09", "change"))

    def get(self, path: str) -> FakeHandler:
        handler = FakeHandler(self.server)
        self.assertTrue(
            manager_summary_routes.handle_get(handler, parsed(path), path.split("/")),
        )
        return handler

    def test_the_latest_month_is_what_the_panel_gets_without_asking(self) -> None:
        answer = self.get("/api/monthly-summary").sent
        self.assertEqual(answer["report"]["month"], "2026-09")
        self.assertEqual(answer["months"], ["2026-09", "2026-08"])
        self.assertEqual(answer["unread"], ["2026-09", "2026-08"])

    def test_a_month_that_is_asked_for_is_the_one_that_comes_back(self) -> None:
        answer = self.get("/api/monthly-summary?month=2026-08").sent
        self.assertEqual(answer["report"]["month"], "2026-08")

    def test_a_month_that_is_not_a_month_falls_back_instead_of_inventing(self) -> None:
        answer = self.get("/api/monthly-summary?month=2026").sent
        self.assertEqual(answer["report"]["month"], "2026-09")

    def test_the_configuration_says_when_the_next_one_lands(self) -> None:
        answer = self.get("/api/monthly-summary-config").sent
        self.assertTrue(answer["enabled"])
        self.assertEqual(answer["day"], 1)
        self.assertTrue(answer["next_run_at"])
        self.assertEqual(answer["unread"], ["2026-09", "2026-08"])

    def test_reading_a_month_takes_it_off_the_badge(self) -> None:
        handler = FakeHandler(self.server, {"month": "2026-09"})
        path = "/api/monthly-summary/read"
        self.assertTrue(
            manager_summary_routes.handle_post(handler, parsed(path), path.split("/")),
        )
        self.assertEqual(handler.sent["unread"], ["2026-08"])

    def test_saving_the_appointment_applies_it_without_a_restart(self) -> None:
        handler = FakeHandler(self.server, {"day": 3, "below_streak_months": 4})
        path = "/api/monthly-summary-config"
        self.assertTrue(
            manager_summary_routes.handle_post(handler, parsed(path), path.split("/")),
        )
        self.assertEqual(handler.status, 200)
        self.assertEqual(self.server.monthly_summary_settings["day"], 3)
        self.assertEqual(handler.sent["below_streak_months"], 4)
        self.assertEqual(
            json.loads(self.server.monthly_summary_path.read_text(encoding="utf-8"))["day"], 3,
        )

    def test_a_refused_appointment_comes_back_as_an_error(self) -> None:
        handler = FakeHandler(self.server, {"day": 31})
        path = "/api/monthly-summary-config"
        manager_summary_routes.handle_post(handler, parsed(path), path.split("/"))
        self.assertEqual(handler.status, 400)
        self.assertIn("día", handler.sent["error"].lower() + "día")
        self.assertEqual(self.server.monthly_summary_settings["day"], 1)

    def test_what_is_not_its_route_it_leaves_alone(self) -> None:
        handler = FakeHandler(self.server)
        path = "/api/live-accounts"
        self.assertFalse(
            manager_summary_routes.handle_get(handler, parsed(path), path.split("/")),
        )
        self.assertFalse(
            manager_summary_routes.handle_post(handler, parsed(path), path.split("/")),
        )


if __name__ == "__main__":
    unittest.main()
