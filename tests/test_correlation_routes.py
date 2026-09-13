"""La pantalla de correlación reutiliza solo las lecturas existentes."""

from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mt5_manager.manager import ManagerServer


STATIC_DIR = Path(__file__).parents[1] / "mt5_manager" / "static"


class CorrelationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.manager = ManagerServer(("127.0.0.1", 0), {
            "nodes": [{"id": "test-node", "name": "Test Node", "url": "http://127.0.0.1:1", "token": "x"}],
            "preferences_file": str(root / "launch_preferences.json"),
            "live_audit_settings_file": str(root / "live_audit_settings.json"),
            "portfolio_settings_file": str(root / "portfolio_settings.json"),
        })
        self.thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.manager.server_address[1]}"

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.manager.server_close()
        self.temp.cleanup()

    def request(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def test_page_and_assets_are_served(self) -> None:
        status, page = self.request("/correlation.html")
        self.assertEqual(status, 200)
        self.assertIn("/correlation.js", page)
        self.assertIn("/correlation.css", page)
        self.assertIn("Pearson sobre cambios de PnL", page)
        for asset in ("/correlation.js", "/correlation.css"):
            with self.subTest(asset=asset):
                self.assertEqual(self.request(asset)[0], 200)

    def test_header_places_correlation_next_to_experiment(self) -> None:
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        experiment = index.index('href="/experiment.html"')
        correlation = index.index('href="/correlation.html"')
        restart = index.index('id="restart-manager"')
        self.assertLess(experiment, correlation)
        self.assertLess(correlation, restart)
        self.assertIn('target="_blank"', index[correlation:restart])

    def test_screen_uses_read_only_existing_portfolio_endpoints(self) -> None:
        script = (STATIC_DIR / "correlation.js").read_text(encoding="utf-8")
        self.assertIn("/api/nodes", script)
        self.assertIn("/portfolios?scope=", script)
        self.assertIn("/portfolios/${entry.id}?scope=", script)
        self.assertNotIn("method: 'POST'", script)
        self.assertNotIn('method: "POST"', script)
        self.assertIn("curveCorrelation", script)
        self.assertIn("increments(left)", script)


if __name__ == "__main__":
    unittest.main()
