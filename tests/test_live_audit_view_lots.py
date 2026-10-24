"""El escalado de lotes que la vista aplica, de punta a punta.

Vive en el perfil del uso y no en `localStorage`: guardado en el navegador, la
misma auditoria salia reconciliada en el escritorio y en bruto en el movil.
Aqui se comprueban las dos mitades: lo que el almacen persiste y lo que la ruta
contesta. Detalle en `ai_context/live_account_audit_mvp.md`.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)
from mt5_manager.manager_live_audit_routes import handle_get, handle_post
from tests.test_live_audit_settings import profile as stored_profile


class LiveAuditViewLotsStoreTests(unittest.TestCase):
    def test_view_lot_scaling_is_saved_per_use_and_survives_a_reload(self) -> None:
        # Vive en el perfil y no en el navegador: la portada y Análisis tienen
        # que enseñar la misma comparación desde cualquier dispositivo.
        self.assertEqual(DEFAULT_LIVE_AUDIT_PROFILE["view_lot_overrides"], {})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "live_audit_settings.json"
            store = LiveAuditSettingsStore(path)
            store.update("node-a", {
                "selected_audit_ids": ["real-a"],
                "profiles": {"real-a": {
                    **stored_profile("111", "911"), "portfolio_id": 11, "portfolio_type": "balanced",
                    "source_password": "real", "tester_password": "test",
                }},
            })

            self.assertEqual(store.view_lot_overrides("node-a", "real-a"), {})
            self.assertEqual(
                store.set_view_lot_overrides("node-a", "real-a", {
                    "$mode": "autolot", "eth-grid": {"demo": "0.1", "real": 0}, "vacia": {},
                }),
                {"$mode": "autolot", "eth-grid": {"demo": 0.1, "real": 0.0}},
            )
            reloaded = LiveAuditSettingsStore(path)
            self.assertEqual(
                reloaded.view_lot_overrides("node-a", "real-a"),
                {"$mode": "autolot", "eth-grid": {"demo": 0.1, "real": 0.0}},
            )
            # Restablecer es guardar vacío, no dejar de guardar.
            self.assertEqual(store.set_view_lot_overrides("node-a", "real-a", {}), {})
            self.assertEqual(
                LiveAuditSettingsStore(path).view_lot_overrides("node-a", "real-a"), {},
            )
            # Y no se come el resto del perfil.
            saved = store.state("node-a")["profiles"]["real-a"]
            self.assertEqual(saved["portfolio_id"], 11)
            self.assertEqual(saved["source_login"], "111")
            # Al revés tampoco: la pantalla de configuración no manda este
            # campo, así que guardar ahí no puede deshacer la reconciliación.
            store.set_view_lot_overrides("node-a", "real-a", {"$mode": "autolot"})
            store.update("node-a", {
                "selected_audit_ids": ["real-a"],
                "profiles": {"real-a": {
                    "portfolio_id": 11, "portfolio_type": "balanced", "period_days": 30,
                    "source_login": "111", "source_server": "Broker-Live",
                    "tester_login": "911", "tester_server": "Broker-Demo",
                    "source_password": "real", "tester_password": "test",
                }},
            })
            kept = store.state("node-a")["profiles"]["real-a"]
            self.assertEqual(kept["view_lot_overrides"], {"$mode": "autolot"})
            self.assertEqual(kept["period_days"], 30)
            with self.assertRaisesRegex(KeyError, "no tiene configuración"):
                store.set_view_lot_overrides("node-a", "otro-uso", {})

    def test_view_lot_scaling_only_admits_a_known_lead_and_positive_lots(self) -> None:
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"view_lot_overrides": []})
        with self.assertRaisesRegex(ValueError, "autolot"):
            normalize_live_audit_settings({"view_lot_overrides": {"$mode": "lo-que-sea"}})
        # Los dos sentidos: `autolot` sube la demo al lote que la cuenta
        # ejecutó y `tester` baja la real al lote del backtest. El nombre del
        # primero es histórico —fue el único que existió— y se conserva porque
        # hay perfiles guardados con él.
        for mode in ("autolot", "tester"):
            self.assertEqual(
                normalize_live_audit_settings({"view_lot_overrides": {"$mode": mode}})[
                    "view_lot_overrides"
                ],
                {"$mode": mode},
            )
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_live_audit_settings({"view_lot_overrides": {"eth-grid": 0.1}})
        with self.assertRaisesRegex(ValueError, "entre"):
            normalize_live_audit_settings({"view_lot_overrides": {"eth": {"demo": -1}}})
        # Lote 0 es una pregunta legítima: «¿y si esto no hubiera operado?».
        self.assertEqual(
            normalize_live_audit_settings({"view_lot_overrides": {"eth": {"demo": 0}}})[
                "view_lot_overrides"
            ],
            {"eth": {"demo": 0.0}},
        )


class RouteHandler:
    """Lo mínimo que una ruta usa del handler HTTP."""

    def __init__(self, store: LiveAuditSettingsStore, body: object = None) -> None:
        self.server = SimpleNamespace(live_audit_settings=store)
        self.body = body
        self.sent: tuple[int, object] | None = None

    def _node(self, node_id: str) -> dict:
        return {"id": node_id}

    def _body(self) -> object:
        return self.body

    def _send_json(self, status: int, payload: object) -> None:
        self.sent = (status, payload)


class LiveAuditViewLotsRouteTests(unittest.TestCase):
    """El escalado de la vista se guarda en el uso, no en cada navegador."""

    PATH = ["api", "nodes", "nodo", "live-audits", "real-a", "view-lots"]

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = LiveAuditSettingsStore(Path(temp.name) / "live_audit_settings.json")
        self.store.update("nodo", {
            "selected_audit_ids": ["real-a"],
            "profiles": {"real-a": {
                **stored_profile("111", "911"), "portfolio_id": 11,
                "portfolio_type": "balanced",
                "source_password": "real", "tester_password": "test",
            }},
        })

    def post(self, body: object, parts: list[str] | None = None) -> tuple[int, object]:
        handler = RouteHandler(self.store, body)
        self.assertTrue(handle_post(handler, None, parts or self.PATH))
        return handler.sent

    def get(self) -> tuple[int, object]:
        handler = RouteHandler(self.store)
        self.assertTrue(handle_get(handler, SimpleNamespace(query=""), self.PATH))
        return handler.sent

    def test_the_applied_scaling_is_read_back_by_any_other_device(self) -> None:
        self.assertEqual(self.get(), (200, {"view_lot_overrides": {}}))

        saved = self.post({"view_lot_overrides": {"$mode": "autolot"}})

        self.assertEqual(saved, (200, {"view_lot_overrides": {"$mode": "autolot"}}))
        self.assertEqual(self.get(), (200, {"view_lot_overrides": {"$mode": "autolot"}}))
        # Y restablecer también viaja: guardar vacío es una decisión.
        self.assertEqual(self.post({"view_lot_overrides": {}}), (200, {"view_lot_overrides": {}}))
        self.assertEqual(self.get(), (200, {"view_lot_overrides": {}}))

    def test_a_bad_scaling_or_an_unknown_use_answers_400(self) -> None:
        status, body = self.post({"view_lot_overrides": {"$mode": "lo-que-sea"}})
        self.assertEqual(status, 400)
        self.assertIn("autolot", str(body))

        status, body = self.post(
            {"view_lot_overrides": {}},
            ["api", "nodes", "nodo", "live-audits", "sin-configurar", "view-lots"],
        )
        self.assertEqual(status, 400)
        self.assertIn("no tiene configuración", str(body))


if __name__ == "__main__":
    unittest.main()
