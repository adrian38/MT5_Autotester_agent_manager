"""Un campo retirado del esquema no puede borrar el nodo entero.

Vive aparte de `test_live_audit_settings.py` porque aquel fichero ya rozaba
las 600 lineas, no porque sea otro contrato: esto es el cargador del mismo
almacen leyendo un fichero que escribio otra version de nosotros.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mt5_manager.live_audit_settings import (
    LiveAuditSettingsStore,
    normalize_live_audit_settings,
)
from tests.test_live_audit_settings import profile


class RetiredProfileFieldTests(unittest.TestCase):
    """Un campo que el esquema ya no conoce no puede borrar el nodo entero.

    El 2026-10-24 lo hizo: la version de las abiertas sin enlazar escribio
    `reviewed_open_positions` en los catorce perfiles de AXI y RoboForex, la
    reversion le quito el sitio en el esquema, y al reiniciar el manager
    «Portafolios activos» amanecio vacia. El rechazo por campo desconocido
    caia en el `except ValueError` del cargador, que descarta el registro
    completo y no dice nada.
    """

    def stored_node(self, extra: dict[str, object]) -> dict[str, object]:
        return {"node-a": {
            "selected_audit_ids": ["audit-1", "audit-2"],
            "profiles": {
                "audit-1": {
                    **profile("111", "911"), "portfolio_id": 11,
                    "portfolio_type": "balanced", "live_trading": True, **extra,
                },
                "audit-2": {
                    **profile("222", "922"), "portfolio_id": 12,
                    "portfolio_type": "aggressive", "live_trading": True,
                },
            },
            "updated_at": "2026-10-24T17:00:00+00:00",
        }}

    def load(self, stored: dict[str, object]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "live_audit_settings.json"
            path.write_text(json.dumps(stored), encoding="utf-8")
            return LiveAuditSettingsStore(path).state("node-a")

    def test_a_field_we_retired_is_forgotten_and_the_uses_survive(self) -> None:
        state = self.load(self.stored_node({"reviewed_open_positions": {}}))

        self.assertEqual(sorted(state["profiles"]), ["audit-1", "audit-2"])
        self.assertEqual(state["live_audit_ids"], ["audit-1", "audit-2"])
        # Olvidado, no conservado: quien lo lea despues no puede creer que el
        # campo sigue existiendo.
        self.assertNotIn("reviewed_open_positions", state["profiles"]["audit-1"])

    def test_without_that_the_whole_node_disappeared(self) -> None:
        # La prueba de que el fallo era ese y no otro: con el campo retirado
        # puesto a mano, el perfil que el esquema valida se cae entero.
        with self.assertRaisesRegex(ValueError, "reviewed_open_positions"):
            normalize_live_audit_settings({
                **profile("111", "911"), "portfolio_id": 11,
                "portfolio_type": "balanced", "reviewed_open_positions": {},
            })

    def test_what_arrives_through_the_api_is_still_rejected(self) -> None:
        # Al escribir sigue siendo una errata y hay que cantarla: lo que se
        # perdona es el fichero que escribio otra version de nosotros.
        with tempfile.TemporaryDirectory() as temp:
            store = LiveAuditSettingsStore(Path(temp) / "live_audit_settings.json")
            with self.assertRaisesRegex(ValueError, "Campos desconocidos"):
                store.update("node-a", {
                    "selected_audit_ids": ["audit-1"],
                    "profiles": {"audit-1": {
                        **profile("111", "911"), "portfolio_id": 11,
                        "portfolio_type": "balanced", "revieweed_strategies": {},
                        "source_password": "real", "tester_password": "test",
                    }},
                })


if __name__ == "__main__":
    unittest.main()
