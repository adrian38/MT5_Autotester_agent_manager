import shutil
import subprocess
import unittest
from pathlib import Path


class PortfolioComparisonTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js no está instalado: se omiten pruebas JS de comparación")
    def test_comparison_behaviour(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(["node", "--test", "tests/portfolio_comparison.test.cjs"], cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_button_is_exclusive_to_normal_ubs_and_script_is_served(self):
        root = Path(__file__).resolve().parents[1] / "mt5_manager"
        page = (root / "static/portfolios.html").read_text(encoding="utf-8")
        monthly = (root / "static/portfolios_monthly.html").read_text(encoding="utf-8")
        self.assertIn('id="detail-compare-original"', page)
        self.assertIn('src="/portfolio_comparison.js"', page)
        self.assertNotIn('portfolio_comparison.js', monthly)
        self.assertIn('"portfolio_comparison.js"', (root / "manager.py").read_text(encoding="utf-8"))
