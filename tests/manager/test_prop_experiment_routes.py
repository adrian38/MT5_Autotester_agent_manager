"""Experimenta Prop tiene puerta propia y Vantage queda fuera por broker."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mt5_manager.manager.manager import ManagerServer


class PropExperimentRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        nodes = [
            {"id": "ic", "name": "IC", "portfolio_broker": "ICTRADING", "url": "http://127.0.0.1:1", "token": "x"},
            {"id": "vantage", "name": "Vantage", "portfolio_broker": "VANTAGE", "url": "http://127.0.0.1:1", "token": "x"},
        ]
        self.manager = ManagerServer(("127.0.0.1", 0), {
            "nodes": nodes, "preferences_file": str(root / "preferences.json"),
            "live_audit_settings_file": str(root / "audit.json"),
            "portfolio_settings_file": str(root / "portfolio.json"),
        })
        self.thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.manager.server_address[1]}"

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.manager.server_close()
        self.temp.cleanup()

    def request(self, path: str, payload: dict | None = None) -> tuple[int, object]:
        request = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode() if payload is not None else None,
            method="POST" if payload is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body) if body.startswith(("{", "[")) else body
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            return error.code, json.loads(body) if body.startswith(("{", "[")) else body

    def test_page_assets_and_profiles_are_served(self) -> None:
        status, page = self.request("/prop_experiment.html")
        self.assertEqual(status, 200)
        self.assertIn("Seeds UBS con Final Tick 6M", page)
        self.assertIn("Estrategias de portafolios manuales", page)
        self.assertIn("Solo estrategias presentes en portafolios activos", page)
        self.assertIn("Bankroll disponible (€)", page)
        self.assertIn("Familias de símbolos", page)
        self.assertIn("portfolio_curve_view.js", page)
        self.assertIn("Plan de pruebas prop", page)
        self.assertNotIn("Ventana histórica", page)
        self.assertNotIn("Riesgo por unidad", page)
        self.assertNotIn("Máx. estrategias", page)
        self.assertNotIn("Ajustes avanzados", page)
        self.assertIn("PROGRESO DEL CÁLCULO", page)
        self.assertIn('id="stage-list"', page)
        self.assertIn('id="log" class="live-log"', page)
        self.assertNotIn("Progreso y registro</summary>", page)
        self.assertIn("Histórico completo", page)
        self.assertIn('id="allocation-table"', page)
        self.assertEqual(self.request("/prop_experiment.js")[0], 200)
        status, styles = self.request("/prop_experiment.css")
        self.assertEqual(status, 200)
        self.assertNotIn("details { margin-top", styles)
        self.assertIn("align-items: flex-end", styles)
        self.assertNotIn("align-items: flex-start", styles)
        status, config = self.request("/api/prop-experiment/config")
        self.assertEqual(status, 200)
        self.assertEqual(len(config["profiles"]), 1)
        profile = config["profiles"][0]
        self.assertEqual(profile["profile_id"], "the5ers-growth")
        self.assertEqual(profile["account_sizes"], [5000.0, 10000.0, 20000.0])
        self.assertEqual(profile["account_leverage"], 30.0)
        self.assertEqual(profile["leverage_by_family"], {
            "Forex": 30.0, "Metals": 25.0, "Indices": 25.0,
        })
        self.assertEqual(profile["symbol_families"], ["Forex", "Metals", "Indices"])
        self.assertEqual(config["settings"]["bankroll_eur"], 1000.0)
        self.assertNotIn("horizon_months", config["settings"])

    def test_vantage_is_visible_but_never_selected(self) -> None:
        config = self.request("/api/prop-experiment/config")[1]
        self.assertEqual(config["settings"]["source_nodes"], ["ic"])
        vantage = next(node for node in config["nodes"] if node["id"] == "vantage")
        self.assertTrue(vantage["excluded"])
        status, saved = self.request("/api/prop-experiment/settings", {
            "source_nodes": ["ic", "vantage"], "include_candidates": True,
            "include_seeds": True, "include_manual_portfolios": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["settings"]["source_nodes"], ["ic"])

    def test_all_three_source_switches_cannot_be_off(self) -> None:
        status, error = self.request("/api/prop-experiment/settings", {
            "include_candidates": False, "include_seeds": False,
            "include_manual_portfolios": False, "active_portfolios_only": True,
        })
        self.assertEqual(status, 400)
        self.assertIn("seeds", error["error"])

    def test_capital_and_symbol_family_must_be_offered_by_the_profile(self) -> None:
        status, error = self.request("/api/prop-experiment/settings", {
            "capital": 100000, "symbol_families": ["Forex"],
        })
        self.assertEqual(status, 400)
        self.assertIn("Capital no ofrecido", error["error"])
        status, saved = self.request("/api/prop-experiment/settings", {
            "capital": 10000, "bankroll_eur": 1000,
            "symbol_families": ["Forex", "Metals"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["settings"]["capital"], 10000.0)
        self.assertEqual(saved["settings"]["symbol_families"], ["Forex", "Metals"])

    def test_unknown_routes_still_fall_through(self) -> None:
        self.assertEqual(self.request("/api/prop-experiment/nope")[0], 404)
        self.assertEqual(self.request("/api/nodes")[0], 200)


if __name__ == "__main__":
    unittest.main()
