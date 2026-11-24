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
        self.assertIn("seeds UBS que aprobaron UBS Final Tick 6M", page)
        self.assertIn("sets de portafolios manuales", page)
        self.assertNotIn('id="include_candidates"', page)
        self.assertNotIn('id="include_seeds"', page)
        self.assertNotIn('id="include_manual_portfolios"', page)
        self.assertIn("Solo estrategias presentes en portafolios activos", page)
        self.assertIn("Bankroll mensual (€)", page)
        self.assertIn("Coste por prueba (€)", page)
        self.assertIn('id="include_grids"', page)
        self.assertIn('id="attempt-calendar"', page)
        self.assertIn('id="broker-pool-summary"', page)
        self.assertIn("Admisiones por broker", page)
        self.assertNotIn('id="broker-pool-summary" class="broker-pool-summary" hidden', page)
        self.assertIn("Calculando inventario preliminar", page)
        self.assertIn("Pool reproducido y descartes por broker", page)
        self.assertIn('id="search-comparison"', page)
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
        self.assertIn('id="broker-symbol-controls"', page)
        self.assertIn('id="prop-symbol-dialog"', page)
        self.assertIn("Deshabilitar en generaciones UBS y en Experimenta Prop", page)
        self.assertIn("exclusion_reason.js", page)
        self.assertNotIn("Progreso y registro</summary>", page)
        self.assertIn("Construir Portafolio Prop", page)
        self.assertIn("OHLC frente a Every Tick", page)
        self.assertIn("Validación final 2020–2024", page)
        self.assertIn('id="auto_continue"', page)
        self.assertIn('id="repair-prop"', page)
        self.assertIn('id="phase-two-comparison"', page)
        self.assertIn('id="allocation-table"', page)
        self.assertNotIn('id="portfolio-chart"', page)
        self.assertNotIn("Curva combinada", page)
        status, script = self.request("/prop_experiment.js")
        self.assertEqual(status, 200)
        self.assertNotIn('querySelector("#portfolio-chart")', script)
        self.assertIn('request("pool-inventory"', script)
        self.assertIn("loadPoolInventory();", script)
        self.assertIn("generados aceptados FT6M", script)
        self.assertIn("seeds UBS aceptadas FT6M", script)
        status, styles = self.request("/prop_experiment.css")
        self.assertEqual(status, 200)
        self.assertNotIn("details { margin-top", styles)
        self.assertIn("align-items: flex-end", styles)
        self.assertNotIn("align-items: flex-start", styles)

    def test_profile_and_phase_configuration_is_served(self) -> None:
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
        self.assertEqual(config["settings"]["evaluation_fee_eur"], 52.0)
        self.assertTrue(config["settings"]["include_candidates"])
        self.assertTrue(config["settings"]["include_seeds"])
        self.assertTrue(config["settings"]["include_manual_portfolios"])
        self.assertFalse(config["settings"]["include_grids"])
        self.assertFalse(config["settings"]["auto_continue"])
        self.assertEqual([item["id"] for item in config["phases"]], [1, 2, 3])
        self.assertNotIn("horizon_months", config["settings"])

    def test_pool_inventory_is_available_before_phase_one(self) -> None:
        expected = {"pool": {"strategies": 3, "by_broker": []}, "warnings": [], "axis": None}
        self.manager.prop_experiments.pool_inventory = lambda: expected
        status, payload = self.request("/api/prop-experiment/pool-inventory")
        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)

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

    def test_the_three_source_contract_cannot_be_changed_by_the_client(self) -> None:
        status, saved = self.request("/api/prop-experiment/settings", {
            "include_candidates": False, "include_seeds": False,
            "include_manual_portfolios": False, "active_portfolios_only": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved["settings"]["include_candidates"])
        self.assertTrue(saved["settings"]["include_seeds"])
        self.assertTrue(saved["settings"]["include_manual_portfolios"])

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

    def test_the_accidental_260_fee_is_migrated_but_an_explicit_value_is_kept(self) -> None:
        coordinator = self.manager.prop_experiments
        coordinator.stored = {**coordinator.settings(), "evaluation_fee_eur": 260.0}
        self.assertEqual(coordinator.settings()["evaluation_fee_eur"], 52.0)
        saved = coordinator.update_settings({"evaluation_fee_eur": 75.0})
        self.assertEqual(saved["evaluation_fee_eur"], 75.0)

    def test_legacy_result_is_not_presented_as_a_valid_prop_portfolio(self) -> None:
        coordinator = self.manager.prop_experiments
        coordinator.result_path.write_text('{"kind":"prop_portfolio","revision":1}', encoding="utf-8")
        coordinator.result = coordinator._load_optional(coordinator.result_path)
        loaded = type(coordinator)(
            list(coordinator.nodes.values()), coordinator.settings_path,
            coordinator.source_factory, coordinator.active_members_factory,
            coordinator.symbol_settings_factory, coordinator.node_request,
        )
        self.assertIsNone(loaded.result)


if __name__ == "__main__":
    unittest.main()
