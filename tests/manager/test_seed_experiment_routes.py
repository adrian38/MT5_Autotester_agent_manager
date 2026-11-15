"""«Experimenta Seeds» está enchufada al manager y no pisa al laboratorio 1M.

Es el mismo coordinador con otro pool, así que lo que hay que comprobar no es
el veredicto —ése ya lo cubren las pruebas del laboratorio hermano y las del
motor— sino las cuatro cosas que podrían salir mal al heredarlo:

- que sus rutas contestan y las de siempre siguen contestando;
- que su formulario es el suyo: con «máx. seeds por broker» y sin «máx.
  candidatas por broker», que aquí es un campo desconocido;
- que su configuración y su resultado se guardan en ficheros propios, porque
  los dos coordinadores reciben la misma carpeta;
- que el pool sale de `load_seed_sets` y no del embudo UBS de candidatos.
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
from unittest import mock

from mt5_manager.experiment.experiment_service import DEFAULT_SETTINGS as EXPERIMENT_SETTINGS
from mt5_manager.experiment.seed_experiment_service import (
    DEFAULT_SETTINGS,
    SeedExperimentCoordinator,
    normalize_settings,
)
from mt5_manager.manager.manager import ManagerServer
from tools.package_layout import static_source


class SeedExperimentFormTests(unittest.TestCase):
    def test_the_form_is_the_one_of_the_1m_lab_with_the_seed_limit(self) -> None:
        # Derivado a proposito: si los dos diccionarios se escribieran a mano,
        # un limite nuevo en uno se quedaria fuera del otro sin que se note.
        self.assertEqual(
            set(DEFAULT_SETTINGS),
            (set(EXPERIMENT_SETTINGS) - {"max_candidates_per_node"}) | {"max_seeds_per_node"},
        )
        shared = set(DEFAULT_SETTINGS) - {"max_seeds_per_node"}
        self.assertEqual(
            {key: DEFAULT_SETTINGS[key] for key in shared},
            {key: EXPERIMENT_SETTINGS[key] for key in shared},
        )

    def test_the_candidate_limit_is_an_unknown_field_here(self) -> None:
        with self.assertRaises(ValueError) as raised:
            normalize_settings({"max_candidates_per_node": 50})
        self.assertIn("max_candidates_per_node", str(raised.exception))

    def test_the_seed_limit_is_clamped_and_zero_means_all(self) -> None:
        self.assertEqual(normalize_settings({"max_seeds_per_node": 0})["max_seeds_per_node"], 0)
        self.assertEqual(
            normalize_settings({"max_seeds_per_node": 99999})["max_seeds_per_node"], 10000,
        )
        self.assertNotIn("max_candidates_per_node", normalize_settings({}))


class SeedExperimentPoolTests(unittest.TestCase):
    """De dónde salen las estrategias, que es lo único que cambia del motor."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coordinator = SeedExperimentCoordinator(
            [{"id": "node", "name": "Node", "portfolio_broker": "AXI"}],
            Path(self.temp.name) / "seed_experiment_settings.json",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_the_pool_comes_from_the_accepted_seeds_and_says_what_it_trimmed(self) -> None:
        source = mock.Mock(broker="AXI")
        warnings: list[str] = []
        origins: dict[str, object] = {}
        with mock.patch.object(self.coordinator, "readonly_source", return_value=source), \
             mock.patch(
                 "mt5_manager.experiment.seed_experiment_service.load_seed_sets",
                 return_value=(["set-a", "set-b"], ["aviso de la seed 7"], 9),
             ) as loader:
            loaded = self.coordinator._load_origin_sets(
                "node", {"max_seeds_per_node": 2}, warnings, origins,
            )
        self.assertEqual(loader.call_args.kwargs["limit"], 2)
        self.assertEqual(loaded, [("set-a", "AXI", "node"), ("set-b", "AXI", "node")])
        self.assertEqual(
            origins["node"],
            {"broker": "AXI", "rows": 9, "read": 2, "loaded": 2, "error": ""},
        )
        self.assertIn("aviso de la seed 7", warnings)
        self.assertTrue(any("solo se leyeron 2 seeds de 9" in item for item in warnings))
        # El embudo UBS no se toca: aqui no se piden candidatos.
        source.candidate_rows.assert_not_called()

    def test_a_memory_that_does_not_answer_is_reported_not_fatal(self) -> None:
        warnings: list[str] = []
        origins: dict[str, object] = {}
        with mock.patch.object(
            self.coordinator, "readonly_source", side_effect=ValueError("sin memoria"),
        ):
            loaded = self.coordinator._load_origin_sets(
                "node", {"max_seeds_per_node": 0}, warnings, origins,
            )
        self.assertEqual(loaded, [])
        self.assertEqual(origins["node"]["error"], "sin memoria")
        self.assertIn("node: sin memoria", warnings)

    def test_its_settings_and_result_do_not_collide_with_the_1m_lab(self) -> None:
        self.assertEqual(self.coordinator.result_path.name, "seed_experiment_last_result.json")
        self.assertEqual(
            self.coordinator.settings_path.name, "seed_experiment_settings.json",
        )


class SeedExperimentRouteTests(unittest.TestCase):
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

    def test_the_page_is_served_and_mounts_the_lab_assets(self) -> None:
        status, page = self.request("/seed_experiment.html")
        self.assertEqual(status, 200)
        # No tiene `.js` ni `.css` propios: monta los del laboratorio hermano y
        # le dice a que API hablar.
        self.assertIn('data-api="/api/seed-experiment/"', page)
        for asset in ("/experiment.js", "/experiment.css"):
            with self.subTest(asset=asset):
                self.assertIn(asset, page)
                self.assertEqual(self.request(asset)[0], 200)

    def test_the_common_menu_opens_it_in_another_tab(self) -> None:
        nav = static_source("main_nav.js")
        self.assertIn(
            "label: 'Experimenta Seeds', url: '/seed_experiment.html', blank: true", nav,
        )

    def test_config_offers_the_seed_limit_and_not_the_candidate_one(self) -> None:
        status, config = self.request("/api/seed-experiment/config")
        self.assertEqual(status, 200)
        self.assertIn("max_seeds_per_node", config["settings"])
        self.assertNotIn("max_candidates_per_node", config["settings"])
        self.assertNotIn("max_candidates_per_node", config["defaults"])
        self.assertEqual(config["settings"]["target_equity"], 1000000.0)

    def test_settings_round_trip_without_touching_the_1m_lab(self) -> None:
        status, saved = self.request("/api/seed-experiment/settings", {
            "capital": 25000, "target_equity": 500000, "horizon_months": 6,
            "max_seeds_per_node": 40, "target_node": "test-node",
            "source_nodes": ["test-node"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["settings"]["max_seeds_per_node"], 40)
        self.assertEqual(
            self.request("/api/seed-experiment/config")[1]["settings"]["capital"], 25000.0,
        )
        # El laboratorio 1M sigue con los suyos: dos ficheros, dos pantallas.
        self.assertEqual(
            self.request("/api/experiment/config")[1]["settings"]["capital"], 10000.0,
        )
        status, error = self.request("/api/seed-experiment/settings", {"leverage": 500})
        self.assertEqual(status, 400)
        self.assertIn("leverage", error["error"])

    def test_stopping_without_a_run_is_an_error_not_a_crash(self) -> None:
        status, error = self.request("/api/seed-experiment/stop", {})
        self.assertEqual(status, 400)
        self.assertIn("No hay experimento", error["error"])

    def test_a_run_without_readable_memories_fails_with_a_reason(self) -> None:
        status, state = self.request("/api/seed-experiment/run", {
            "target_node": "test-node", "source_nodes": ["test-node"],
        })
        self.assertEqual(status, 200)
        self.assertIn(state["job"]["status"], {"running", "failed"})
        deadline = time.time() + 10
        while time.time() < deadline:
            _status, state = self.request("/api/seed-experiment/state")
            if state["job"]["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(state["job"]["status"], "failed")
        self.assertTrue(state["job"]["error"])
        _status, log = self.request("/api/seed-experiment/log?lines=50")
        self.assertTrue(any("Error" in line for line in log["lines"]))

    def test_an_unknown_seed_route_falls_through_to_404(self) -> None:
        self.assertEqual(self.request("/api/seed-experiment/nope")[0], 404)
        self.assertEqual(self.request("/seed_experiment_secret.html")[0], 404)

    def test_the_routes_that_existed_before_still_answer(self) -> None:
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/experiment.html")[0], 200)
        self.assertEqual(self.request("/prop_experiment.html")[0], 200)
        self.assertEqual(self.request("/api/pulse")[0], 200)


if __name__ == "__main__":
    unittest.main()
