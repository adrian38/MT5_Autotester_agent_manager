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

    def test_only_profiles_marked_as_live_are_loaded(self) -> None:
        self.assertIn("/api/live-dashboard", self.script)
        self.assertIn("/live-dashboard`,", self.script)

    def test_nodes_are_painted_as_they_arrive(self) -> None:
        self.assertIn("Promise.allSettled(nodes.map(async node", self.script)
        self.assertIn("entries.push(...await loadNode(node))", self.script)
        self.assertIn("render(entries)", self.script)
        self.assertNotIn("fetch('/api/nodes'", self.script)

    def test_a_timed_out_node_keeps_its_identity(self) -> None:
        self.assertIn("entry.node?.name || entry.node?.id || 'Nodo desconocido'", self.script)

    def test_cards_only_show_profit_drawdown_and_flagged_strategies(self) -> None:
        for label in ("Profit Test", "Profit Real", "DD Test", "DD Real", "Estrategias a revisar"):
            self.assertIn(label, self.script)
        self.assertIn("['AVISO', 'NO REPRODUCE'].includes(row.verdict)", self.metrics)
        self.assertIn("EXPECTED_SHORTFALL_PCT = 20", self.reproduction)
        self.assertIn("BROKEN_SHORTFALL_PCT = 60", self.reproduction)
        self.assertNotIn("MEJOR QUE EL TESTER", self.metrics)

    def test_dashboard_and_analysis_share_the_reproduction_primitive(self) -> None:
        self.assertIn("LiveAuditReproduction.numbers(group, lotOverrides)", self.analysis)
        self.assertIn("reproduction.numbers(group, overrides)", self.metrics)
        self.assertIn("LiveAuditReproduction.readOverrides(entry.node.id, entry.auditId)", self.script)
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
