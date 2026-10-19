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
        self.assertEqual(links["Portafolios activos"], "/")
        # Y el par de ida y vuelta se mira junto: la raíz enlaza aquí.
        self.assertEqual(
            {anchor.text_content().strip(): anchor.get("href")
             for anchor in self.page.xpath("//nav//a")}["Generación"],
            "/generation.html",
        )
        for name in sorted(STATIC_FILES):
            if not name.endswith(".html") or name == "dashboard.html":
                continue
            page = (Path(__file__).parents[1] / "mt5_manager" / "static" / name)
            self.assertIn('href="/"', page.read_text(encoding="utf-8"), name)

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
