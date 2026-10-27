from __future__ import annotations

import unittest
from pathlib import Path

from lxml import html

from mt5_manager.manager_handler import STATIC_FILES


class LivePortfolioDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).parents[1]
        static = root / "mt5_manager" / "static"
        cls.page_text = (static / "dashboard.html").read_text(encoding="utf-8")
        cls.script = (static / "dashboard.js").read_text(encoding="utf-8")
        cls.css = (static / "dashboard.css").read_text(encoding="utf-8")
        cls.metrics = (static / "dashboard_metrics.js").read_text(encoding="utf-8")
        cls.reproduction = (static / "live_audit_reproduction.js").read_text(encoding="utf-8")
        cls.analysis = (static / "live_audit_analysis.js").read_text(encoding="utf-8")
        cls.analysis_page = (static / "live_audit_analysis.html").read_text(encoding="utf-8")
        cls.generation_page = (static / "index.html").read_text(encoding="utf-8")
        cls.handler = (root / "mt5_manager" / "manager_handler.py").read_text(encoding="utf-8")
        cls.page = html.fromstring(cls.page_text)

    def test_manager_opens_the_live_portfolio_dashboard_first(self) -> None:
        self.assertIn('STATIC_DIR / "dashboard.html"', self.handler)
        self.assertIn('parsed.path == "/generation.html"', self.handler)
        self.assertIn('STATIC_DIR / "index.html"', self.handler)
        self.assertEqual(self.page.xpath('string(//h1)'), "Portafolios activos")

    def test_header_keeps_the_current_actions_and_adds_generation(self) -> None:
        links = {anchor.text_content().strip(): anchor.get("href") for anchor in self.page.xpath("//nav//a")}
        self.assertEqual(links["Generación"], "/generation.html")
        self.assertEqual(links["Experimenta"], "/experiment.html")
        self.assertEqual(links["Correlación"], "/correlation.html")
        self.assertTrue(self.page.xpath('//*[@id="refresh"]'))
        self.assertTrue(self.page.xpath('//*[@id="restart-manager"]'))

    def test_every_screen_can_get_back_to_the_root(self) -> None:
        # La raíz dejó de ser `/generation.html` y pasó a ser el inventario de
        # portafolios, pero la generación se quedó sin vuelta: era la única
        # pantalla sin salida —el resto ya enlazaban a `/`— y desde ella sólo
        # se volvía escribiendo la dirección a mano.
        generation = html.fromstring(self.generation_page)
        links = {anchor.text_content().strip(): anchor.get("href")
                 for anchor in generation.xpath("//header//a")}
        self.assertEqual(links["Cuentas en real"], "/")
        self.assertEqual(links["Portafolios activos"], "/live_portfolios.html")
        # Y el par de ida y vuelta se mira junto: esta pantalla enlaza aquí.
        self.assertEqual(
            {anchor.text_content().strip(): anchor.get("href")
             for anchor in self.page.xpath("//nav//a")}["Generación"],
            "/generation.html",
        )
        # La vuelta de las pantallas de dentro es al generador, que es de
        # donde se entra a ellas (peticion del usuario, 2026-10-21). No es un
        # callejon sin salida: el generador si enlaza a la raiz, que es lo que
        # comprueban las tres lineas de arriba.
        for name in sorted(STATIC_FILES):
            if not name.endswith(".html") or name == "dashboard.html":
                continue
            page = (Path(__file__).parents[1] / "mt5_manager" / "static" / name)
            source = page.read_text(encoding="utf-8")
            self.assertTrue(
                'href="/"' in source or 'href="/generation.html"' in source, name,
            )

    def test_only_profiles_marked_as_live_are_loaded(self) -> None:
        self.assertIn("/api/live-dashboard", self.script)
        self.assertIn("/live-dashboard`,", self.script)

    def test_nodes_are_painted_as_they_arrive(self) -> None:
        self.assertIn("Promise.allSettled(nodes.filter(nodeIsDue).map(async node", self.script)
        self.assertIn("recordNodeResult(node, await loadNode(node))", self.script)
        self.assertIn("render(entries)", self.script)
        self.assertNotIn("fetch('/api/nodes'", self.script)

    def test_failed_nodes_back_off_without_erasing_the_last_result(self) -> None:
        self.assertIn("FAILURE_LIMIT = 3", self.script)
        self.assertIn("BACKOFF_MS = 5 * 60 * 1000", self.script)
        self.assertIn("if (!nodeEntries.has(node.id)) nodeEntries.set(node.id, loaded)", self.script)
        self.assertIn("nodes.filter(nodeIsDue)", self.script)

    def test_a_timed_out_node_keeps_its_identity(self) -> None:
        self.assertIn("entry.node?.name || entry.node?.id || 'Nodo desconocido'", self.script)

    def test_cards_only_show_profit_drawdown_and_flagged_strategies(self) -> None:
        for label in ("Profit Test", "Profit Real", "DD Test", "DD Real", "Estrategias a revisar"):
            self.assertIn(label, self.script)
        self.assertIn("['AVISO', 'NO REPRODUCE'].includes(row.verdict)", self.metrics)
        self.assertIn("EXPECTED_SHORTFALL_PCT = 20", self.reproduction)
        self.assertIn("BROKEN_SHORTFALL_PCT = 60", self.reproduction)
        self.assertNotIn("MEJOR QUE EL TESTER", self.metrics)

    def test_card_profit_and_drawdown_cover_the_archived_audits(self) -> None:
        history = (Path(__file__).parents[1] / "mt5_manager" / "static"
                   / "live_audit_history_verdicts.js").read_text(encoding="utf-8")
        analysis = (Path(__file__).parents[1] / "mt5_manager" / "static"
                    / "live_audit_history.js").read_text(encoding="utf-8")
        self.assertIn("LiveAuditHistoryVerdicts.summary(", analysis)
        self.assertIn("root.LiveAuditHistoryVerdicts.summary(executions, {", self.metrics)
        self.assertIn("function summary(executions, options = {})", history)
        self.assertIn("const kept = uncovered(executions)", history)
        self.assertIn("merge(executions, options.retained, kept)", history)
        self.assertNotIn("function accumulatedMetrics", self.metrics)
        self.assertIn("const accumulated = history.comparable ?", self.script)
        self.assertIn("function historyPending(entry)", self.script)
        self.assertIn("historyPending(entry) ? 'la serie archivada' : ''", self.script)
        self.assertNotIn("history.comparable ? history : summary", self.script)
        for field in ("demo_profit", "real_profit", "demo_drawdown", "real_drawdown"):
            self.assertIn(f"money(history.{field}, currency)", self.script)

    def test_the_card_names_the_strategies_that_degrade_against_their_history(self) -> None:
        # El veredicto DEGRADA de «contra el histórico» sólo salía abriendo
        # Análisis uso por uso; es el único que justifica retirar una
        # estrategia, así que la tarjeta lo enseña y la campana lo anuncia.
        self.assertIn("Contra el histórico", self.script)
        self.assertIn("degradationMarkup(entry)", self.script)
        self.assertIn("entry.degradation?.strategies || []", self.script)
        self.assertIn("en vigilancia", self.script)
        self.assertIn("DEGRADA${degraded === 1 ? '' : 'N'}", self.script)
        # Degradar pinta la tarjeta como crítica, igual que NO REPRODUCE.
        self.assertIn("const tone = broken || degraded ? 'failed'", self.script)

    def test_a_crossing_that_is_not_confirmed_is_named_too(self) -> None:
        # Desde que cruzar −2 σ dejó de ser el veredicto, el recuento «en
        # vigilancia» escondía la estrategia que cruzó, y encima sólo se
        # pintaba si había alguna degradada: la portada se quedaba muda
        # mientras una estrategia cruzaba semana tras semana. Se nombra, con
        # su chapa ámbar, y la tarjeta deja de estar verde.
        self.assertIn("entry.degradation?.crossing || []", self.script)
        self.assertIn(
            "if (!rows.length && !crossing.length && !slow.length) return ''", self.script,
        )
        self.assertIn("strategyAlert(row, 'warn', 'AVISO FUERTE')", self.script)
        self.assertIn("crossing ? `${crossing} EN VIGILANCIA` : ''", self.script)
        # La chapa enseña los dos: son la misma gravedad y cuentan cosas
        # distintas -reproducir la demo y parecerse al backtest-.
        self.assertIn(
            "!broken && !degraded && warnings ? `${warnings} AVISO${warnings === 1 ? '' : 'S'}` : ''",
            self.script,
        )
        # El aviso que no llego a cruzar sigue siendo solo un recuento.
        self.assertIn("sigue sin listarse", self.script)

    def test_the_card_names_the_strategy_that_stopped_trading(self) -> None:
        # Que el beneficio medio baje pide cientos de operaciones para
        # demostrarse; que el recuento caiga a la mitad se ve en dos meses. Es
        # la unica senal rapida que hay, y hasta ahora no sal�a de Analisis.
        self.assertIn("entry.degradation?.slow || []", self.script)
        self.assertIn("function slowAlert(row)", self.script)
        self.assertIn("'OPERA MENOS'", self.script)
        self.assertIn("esperadas", self.script)
        self.assertIn("opera${slow.length === 1 ? '' : 'n'} menos", self.script)
        self.assertIn("slow ? `${slow} OPERA${slow === 1 ? '' : 'N'} MENOS` : ''", self.script)
        # Tampoco pinta la tarjeta de rojo: no es un veredicto de retirada.
        self.assertIn("warnings || crossing || slow || inSeries ? 'pending'", self.script)

    def test_the_card_also_lists_what_the_whole_series_says(self) -> None:
        # «Demo contra real» en una semana con tres operaciones no decide
        # nada, y la tabla que sí decide —«¿Y en todas las auditorías que
        # llevamos?»— sólo se veía abriendo Análisis uso por uso. Las dos
        # listas van juntas y cada una dice de qué habla.
        self.assertIn("Estrategias a revisar · última auditoría", self.script)
        self.assertIn("Estrategias a revisar · todas las auditorías", self.script)
        self.assertIn("function historyMarkup(entry, summary, currency)", self.script)
        self.assertIn("historyMarkup(entry, history, currency)", self.script)
        # La tarjeta no calcula: pide la serie resuelta al mismo módulo que
        # pinta la tabla. Dos cálculos daban dos respuestas —la tarjeta marcó
        # «NO REPRODUCE» tres EURUSD del #173 que la tabla daba por buenos—,
        # así que aquí sólo queda la llamada.
        self.assertIn(
            "function summarizeHistory(result, series, overrides = {}, reviews = {})",
            self.metrics,
        )
        self.assertIn("root.LiveAuditHistoryVerdicts.summary(executions, {", self.metrics)
        self.assertIn("root.LiveAuditHistoryVerdicts.retainedOf(", self.metrics)
        for gone in ("mergeHistoryGroups", "function executedLots", "historyIdentity"):
            self.assertNotIn(gone, self.metrics)
        # Y el módulo carga antes que sus dos consumidores.
        self.assertLess(
            self.page_text.index("/live_audit_history_verdicts.js"),
            self.page_text.index("/dashboard_metrics.js"),
        )
        self.assertIn("LiveDashboardMetrics.summarizeHistory(", self.script)
        # Y la casilla «Validada» del periodo sigue sin tocarla: dice «ya miré
        # esta semana» y la serie contesta otra pregunta. Filtrando por ella,
        # las doce filas marcadas del 109 de AXI se escondían detrás de dos
        # validaciones. Lo que sí la filtra es la casilla de la tabla
        # histórica, que es otro campo del perfil.
        self.assertNotIn(
            "historyOf(entry), overrides,\n      LiveAuditReproduction.storedReviews(",
            self.script,
        )
        self.assertIn(
            "LiveAuditReproduction.storedHistoryReviews(entry.profile),", self.script,
        )
        # Y la tarjeta no puede decir «SIN AVISOS» con la lista debajo.
        self.assertIn("inSeries ? `${inSeries} EN LA SERIE` : ''", self.script)

    def test_few_operations_is_visible_on_the_card_in_both_periods(self) -> None:
        self.assertIn("few: reproduction.fewTrades(numbers)", self.metrics)
        self.assertIn("const few = row.few", self.script)
        self.assertIn('class="sample-warning">POCAS OPERACIONES</span>', self.script)
        self.assertIn(".sample-warning{", self.css)

    def test_a_card_says_what_it_is_still_loading(self) -> None:
        # La tarjeta se pinta en cuanto hay uso y el alias y la serie llegan
        # después: sin decirlo, una tarjeta sin la sección «todo el histórico»
        # parece terminada cuando lo que pasa es que falta por llegar.
        self.assertIn("function cardPending(entry)", self.script)
        self.assertIn("${loadingMarkup(entry)}", self.script)
        self.assertIn("'la serie archivada'", self.script)
        self.assertIn("'el alias'", self.script)
        self.assertIn("card-loading", self.script)
        self.assertIn(".card-loading{", self.css)
        self.assertIn("@keyframes card-spin{", self.css)
        # Y se calla en cuanto se ha intentado, con éxito o sin él: una serie
        # que no se deja leer desaparece como sección, no como aviso eterno.
        self.assertIn("aliasesTried.add(node.id);", self.script)
        self.assertIn("historiesLoading.has(historyTicket(entry))", self.script)
        self.assertIn("pending.forEach(entry => historiesLoading.add(historyTicket(entry)));", self.script)
        self.assertIn("historiesLoading.delete(ticket);", self.script)
        self.assertIn("historiesTried.add(ticket);", self.script)
        self.assertIn("!historiesTried.has(historyTicket(entry))", self.script)
        # El billete lleva la marca de la ejecución, así que la siguiente
        # auditoría vuelve a avisar de que está cargando.
        self.assertIn("return `${historyKey(entry)}|${historyStamp(entry)}`;", self.script)

    def test_the_series_is_asked_once_per_audit_and_never_to_the_node(self) -> None:
        # Es la respuesta más pesada de la portada —todas las auditorías de
        # ese uso con su detalle— y la pantalla se repinta cada minuto: se
        # guarda con la marca de la ejecución y se lee del archivo, sin
        # provocar el análisis en el agente.
        self.assertIn("/history?source=archive", self.script)
        self.assertIn("stored.stamp === historyStamp(entry)", self.script)
        self.assertIn("historyRevision: entry.history_revision || ''", self.script)
        self.assertIn("entry.historyRevision || entry.result?.analysis?.executed_at", self.script)
        self.assertIn("entry.auditId && entry.result && !historyOf(entry)", self.script)
        routes = (Path(__file__).parents[1] / "mt5_manager"
                  / "manager_schedule_routes.py").read_text(encoding="utf-8")
        self.assertIn('query.get("source", [""])[0] == "archive"', routes)
        self.assertIn("return _archived_history(handler, node, node_id, audit_key)", routes)

    def test_the_card_does_not_open_the_agent_memory_to_know_it(self) -> None:
        # El histórico vive en la memoria UBS del agente y la portada se
        # repinta cada minuto: lo mide el vigilante al archivar y la tarjeta
        # sólo lee lo que viene en el payload.
        self.assertIn("degradation: entry.degradation || {}", self.script)
        self.assertNotIn("strategy-history", self.script)

    def test_the_card_counts_what_the_last_audit_could_not_link(self) -> None:
        # El cuadre de Análisis era el único sitio donde se veía que una real
        # no cuenta en ninguna estrategia, y hay que abrirlo uso por uso. Es
        # trabajo pendiente —atribuirla en el Excel o declarar el lote— y la
        # portada es la lista de lo que queda por mirar.
        self.assertIn("function unlinkedMarkup(summary)", self.script)
        self.assertIn("Operaciones sin enlazar", self.script)
        self.assertIn("summary.comparable ? unlinkedMarkup(summary) : ''", self.script)
        # Es de la última auditoría, al revés que la degradación, que es
        # acumulada: decirlo evita leer un número por el otro.
        self.assertIn("última auditoría</span>", self.script)
        self.assertIn("unlinked_real", self.metrics)
        self.assertIn("unlinked_tester", self.metrics)

    def test_the_two_sides_of_a_broken_link_are_counted_apart(self) -> None:
        # Una real sin dueño y una operación del tester que la cuenta nunca
        # abrió son problemas distintos: sumarlos daría un número que no
        # corresponde a ninguna lista que se pueda abrir.
        self.assertIn("sin explicar", self.script)
        self.assertIn("del tester sin ejecutar", self.script)
        # Las descartadas por lote entran en el recuento aunque no sumen en la
        # curva Real: son justo las que un tramo de lote recupera de golpe, y
        # `pendingRealTrades` las excluye a propósito para el PnL.
        self.assertIn(
            "unaccountedRows(result).filter(row => attributionState(row) === 'pending').length",
            self.metrics,
        )

    def test_a_reviewed_missing_strategy_is_not_still_pending_on_the_card(self) -> None:
        # Marcar una estrategia en Análisis ya la saca de la lista de avisos.
        # Su operación SIN REAL tampoco puede seguir apareciendo debajo como
        # trabajo pendiente mientras el mismo veredicto continúe validado.
        self.assertIn("function unlinkedTester(result, reviewed, validatedRows)", self.metrics)
        self.assertIn("!reviewed.has(String(row.strategy || row.tester?.strategy", self.metrics)
        self.assertIn("unlinkedTester(result, accepted, unpairedReviews)", self.metrics)

    def test_each_card_shows_its_alias_and_lets_you_edit_it_from_there(self) -> None:
        self.assertIn("aliasMarkup(entry.node.id, Number(profile.portfolio_id || 0))", self.script)
        self.assertIn("'Sin alias'", self.script)
        self.assertIn("alias ? 'Editar alias' : 'Añadir alias'", self.script)
        self.assertIn("/portfolio-manager/alias`", self.script)
        self.assertIn("scope: 'full_history', portfolio_id: portfolioId, alias", self.script)
        # Las tarjetas se repintan enteras: el oyente tiene que estar en el
        # contenedor o el boton se queda sin el suyo en la primera recarga.
        self.assertIn("cardsEl.addEventListener('click'", self.script)
        self.assertIn("event.target.closest('.alias-edit')", self.script)

    def test_the_alias_is_asked_once_per_node_and_not_on_every_poll(self) -> None:
        # Leerlo exige abrir la memoria UBS del agente, que es justo lo que la
        # portada evita en su sondeo de 60 s.
        self.assertIn("/portfolio-aliases?ids=", self.script)
        self.assertIn("if (!ids.length || ids.every(portfolioId => portfolioId in known)) return false", self.script)
        self.assertIn("if (await loadAliases(node)) render(visibleEntries())", self.script)

    def test_dashboard_and_analysis_share_the_reproduction_primitive(self) -> None:
        self.assertIn("LiveAuditReproduction.numbers(group, lotOverrides)", self.analysis)
        self.assertIn("reproduction.numbers(group, overrides)", self.metrics)
        self.assertIn("reproduction.realLotTimeline(rows)", self.metrics)
        self.assertIn("reproduction.scaleTarget(row, timeline)", self.metrics)
        self.assertIn("function attributedOwner(row)", self.reproduction)
        self.assertIn("reproduction.attributedOwner(row)", self.metrics)
        self.assertIn("LiveAuditReproduction.storedOverrides(entry.profile)", self.script)
        for page, consumer in (
            (self.page_text, "/dashboard_metrics.js"),
            (self.analysis_page, "/live_audit_analysis.js"),
        ):
            self.assertLess(page.index("/live_audit_reproduction.js"), page.index(consumer))

    def test_initial_restart_button_monitors_completion_and_authentication(self) -> None:
        self.assertIn("/api/manager/restart?lines=80", self.script)
        self.assertIn("location.reload()", self.script)
        self.assertIn("location.href = '/generation.html'", self.script)
        self.assertIn("monitorManagerRestart()", self.script)

    def test_dashboard_assets_are_public(self) -> None:
        for name in (
            "dashboard.js", "dashboard.css", "dashboard_metrics.js",
            "live_audit_reproduction.js",
        ):
            self.assertIn(name, STATIC_FILES)
            self.assertIn(f'/{name}', self.page_text)


if __name__ == "__main__":
    unittest.main()
