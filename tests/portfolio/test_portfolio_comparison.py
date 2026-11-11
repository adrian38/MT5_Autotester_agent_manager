import shutil
import subprocess
import unittest

from tools.package_layout import module_source, static_source
from pathlib import Path


class PortfolioComparisonTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js no está instalado: se omiten pruebas JS de comparación")
    def test_comparison_behaviour(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(["node", "--test", "tests/portfolio/portfolio_comparison.test.cjs"], cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_button_is_exclusive_to_normal_ubs_and_script_is_served(self):
        root = Path(__file__).resolve().parents[2] / "mt5_manager"
        page = static_source("portfolios.html")
        monthly = static_source("portfolios_monthly.html")
        self.assertIn('id="detail-compare-original"', page)
        self.assertIn('src="/portfolio_comparison.js"', page)
        self.assertNotIn('portfolio_comparison.js', monthly)
        self.assertIn('"portfolio_comparison.js"', module_source("manager_handler"))
