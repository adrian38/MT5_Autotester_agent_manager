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
        self.assertIn("config.live_audit_ids", self.script)
        self.assertIn("config.profiles[id]?.live_trading", self.script)
        self.assertIn("/live-audit-config", self.script)
        self.assertIn("/live-audits/${encodeURIComponent(entry.auditId)}", self.script)

    def test_cards_only_show_profit_drawdown_and_flagged_strategies(self) -> None:
        for label in ("Profit Test", "Profit Real", "DD Test", "DD Real", "Estrategias a revisar"):
            self.assertIn(label, self.script)
        self.assertIn("['AVISO', 'NO REPRODUCE'].includes(row.verdict)", self.metrics)
        self.assertIn("EXPECTED_SHORTFALL_PCT = 20", self.metrics)
        self.assertIn("BROKEN_SHORTFALL_PCT = 60", self.metrics)
        self.assertNotIn("MEJOR QUE EL TESTER", self.metrics)

    def test_unreferenced_real_operations_do_not_become_a_final_strategy_verdict(self) -> None:
        self.assertIn("pending_attribution: pendingSymbols.has(group.symbol)", self.metrics)
        self.assertIn("row.pending_attribution ? 'ATRIBUCIÓN PENDIENTE'", self.script)
        self.assertIn("Hay operaciones reales sin referenciar", self.script)
        self.assertIn("flagged.filter(row => !row.pending_attribution)", self.script)
        self.assertIn("Pendientes de atribuir", self.script)

    def test_dashboard_assets_are_public(self) -> None:
        for name in ("dashboard.js", "dashboard.css", "dashboard_metrics.js"):
            self.assertIn(name, STATIC_FILES)
            self.assertIn(f'/{name}', self.page_text)


if __name__ == "__main__":
    unittest.main()
