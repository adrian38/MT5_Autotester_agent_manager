"""El laboratorio «Experimenta» está enchufado al manager sin romper nada.

Las dos líneas que se insertaron en `do_GET`/`do_POST` son el único punto de
contacto con el despachador, así que aquí se comprueban las dos direcciones:
que las rutas nuevas contestan y que las de siempre siguen contestando.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mt5_manager.manager import ManagerServer


STATIC_DIR = Path(__file__).parents[1] / "mt5_manager" / "static"


class ExperimentRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        # Nodo sin `portfolio_project_dir`: es el caso real cuando `X:` o `Y:`
        # no están montadas y el que no puede tumbar la pantalla.
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

    def request(self, path: str, payload: dict | None = None) -> tuple[int, object]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method="POST" if payload is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                return response.status, (json.loads(body) if body.startswith(("{", "[")) else body)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            return error.code, (json.loads(body) if body.startswith(("{", "[")) else body)

    def test_the_page_and_its_assets_are_served(self) -> None:
        status, page = self.request("/experiment.html")
        self.assertEqual(status, 200)
        self.assertIn("/experiment.js", page)
        self.assertIn("/experiment.css", page)
        for asset in ("/experiment.js", "/experiment.css"):
            with self.subTest(asset=asset):
                self.assertEqual(self.request(asset)[0], 200)

    def test_the_manager_header_opens_the_lab_in_another_tab(self) -> None:
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/experiment.html"', index)
        self.assertIn('target="_blank"', index)
        self.assertIn("Experimenta", index)

    def test_a_node_the_manager_cannot_read_is_reported_not_hidden(self) -> None:
        status, config = self.request("/api/experiment/config")
        self.assertEqual(status, 200)
        node = config["nodes"][0]
        self.assertFalse(node["available"])
        self.assertIn("portfolio_project_dir", node["reason"])
        self.assertEqual(config["settings"]["target_equity"], 1000000.0)
        self.assertEqual(config["settings"]["horizon_months"], 12)

    def test_settings_round_trip_and_reject_unknown_fields(self) -> None:
        status, saved = self.request("/api/experiment/settings", {
            "capital": 25000, "target_equity": 500000, "horizon_months": 6,
            "max_dd_pct": 20, "max_margin_pct": 50, "rebalance_months": 3,
            "max_units_per_strategy": 4, "max_units_per_symbol": 12,
            "max_units_total": 100, "max_pair_corr": 0.5, "pool_limit": 20,
            "greedy_steps": 30, "require_portable": False,
            "target_node": "test-node", "source_nodes": ["test-node"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["settings"]["capital"], 25000.0)
        self.assertEqual(saved["settings"]["rebalance_months"], 3)
        self.assertEqual(
            self.request("/api/experiment/config")[1]["settings"]["target_equity"], 500000.0,
        )
        status, error = self.request("/api/experiment/settings", {"leverage": 500})
        self.assertEqual(status, 400)
        self.assertIn("leverage", error["error"])

    def test_a_target_below_the_capital_is_refused(self) -> None:
        status, error = self.request("/api/experiment/settings", {
            "capital": 100000, "target_equity": 1000,
        })
        self.assertEqual(status, 400)
        self.assertIn("mayor que el capital", error["error"])

    def test_stopping_without_a_run_is_an_error_not_a_crash(self) -> None:
        status, error = self.request("/api/experiment/stop", {})
        self.assertEqual(status, 400)
        self.assertIn("No hay experimento", error["error"])

    def test_a_run_without_readable_memories_fails_with_a_reason(self) -> None:
        status, state = self.request("/api/experiment/run", {
            "target_node": "test-node", "source_nodes": ["test-node"],
        })
        self.assertEqual(status, 200)
        # Sin memorias legibles el hilo puede haber terminado antes de que la
        # respuesta llegue: lo que importa es que acabe en `failed` con motivo.
        self.assertIn(state["job"]["status"], {"running", "failed"})
        deadline = time.time() + 10
        while time.time() < deadline:
            _status, state = self.request("/api/experiment/state")
            if state["job"]["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(state["job"]["status"], "failed")
        self.assertTrue(state["job"]["error"])
        self.assertIsNone(state["result"])
        _status, log = self.request("/api/experiment/log?lines=50")
        self.assertTrue(any("Error" in line for line in log["lines"]))

    def test_an_unknown_experiment_route_falls_through_to_404(self) -> None:
        self.assertEqual(self.request("/api/experiment/nope")[0], 404)
        self.assertEqual(self.request("/experiment_secret.html")[0], 404)

    def test_the_routes_that_existed_before_still_answer(self) -> None:
        # Las dos líneas de delegación se insertaron al principio de `do_GET` y
        # `do_POST`: si se tragaran peticiones ajenas, se vería aquí.
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/app.js")[0], 200)
        status, nodes = self.request("/api/nodes")
        self.assertEqual(status, 200)
        self.assertEqual(len(nodes["nodes"]), 1)
        self.assertEqual(self.request("/api/pulse")[0], 200)


if __name__ == "__main__":
    unittest.main()
