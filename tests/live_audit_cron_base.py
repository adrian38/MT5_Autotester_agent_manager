"""Los dobles que comparten las pruebas del cron de auditoria.

Salieron de `test_live_audit_cron.py` al pasar ese fichero de 600 lineas por
segunda vez. Aqui no se comprueba nada: solo esta el servidor falso, su
almacen de configuracion y las dos formas de estado -el uso configurado y el
uso ya terminado- que montan tanto la aritmetica de citas como el vigilante.
"""
from __future__ import annotations

import threading
from pathlib import Path

from mt5_manager.live_audit_notifications import LiveAuditNotificationStore
from mt5_manager.live_audit_schedule import normalize_live_audit_scheduler_settings
from mt5_manager.live_audit_watch import LiveAuditRunState


def settings(**changes: object) -> dict[str, object]:
    return normalize_live_audit_scheduler_settings(changes)


class FakeSettingsStore:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    def state(self, _node_id: str) -> dict[str, object]:
        return self._state

    def credentials(self, _node_id: str, _audit_id: str) -> dict[str, str]:
        return {"source_password": "s", "tester_password": "t"}

    def restore_credentials(self, _node_id: str) -> dict[str, str]:
        return {
            "restore_login": "9", "restore_server": "Broker-Test", "restore_password": "r",
        }


class FakeServer:
    def __init__(self, root: Path, state: dict[str, object],
                 portfolios: object = None, **changes: object) -> None:
        self.nodes = [{"id": "n", "name": "Nodo N", "url": "http://127.0.0.1:1", "token": "t"}]
        self.portfolios = portfolios
        self.live_audit_settings = FakeSettingsStore(state)
        self.live_audit_scheduler_settings = settings(**changes)
        self.live_audit_scheduler_enabled = bool(changes.get("enabled"))
        self.live_audit_notifications = LiveAuditNotificationStore(root / "avisos.json")
        self.live_audit_runs = LiveAuditRunState(root / "cron.json")
        self.live_audit_stop = threading.Event()

    def events(self) -> list[dict[str, object]]:
        return self.live_audit_notifications.state(50)["events"]


def audit_state(**changes: object) -> dict[str, object]:
    return {
        "configured_audit_ids": ["11", "22"],
        "live_audit_ids": ["11"],
        "restore_account": {"configured": True},
        "profiles": {
            "11": {
                "portfolio_id": 11, "deployment_name": "Cartera viva",
                "live_trading": True, "period_mode": "rolling_days", "period_days": 7,
                "real_attribution": {"rules": [{"ticket": 1}]},
                "manual_pairs": {"1": "2"}, "reviewed_strategies": {"a": True},
            },
            "22": {"portfolio_id": 22, "deployment_name": "Cartera parada"},
        },
        **changes,
    }


def published_audit(**changes: object) -> dict[str, object]:
    """Un uso terminado con la forma que publica un agente de hoy.

    Desde que la comparación se movió al manager, el nodo sólo publica
    `last_payload`: `last_result` llega **siempre** a `None`. Alimentar
    `{"last_result": {...}}` modelaba un agente que ya no existe, y por eso el
    suite no vio que el periodo nunca se encadenaba.
    """
    payload: dict[str, object] = {
        "audit_id": "run-1", "audit_key": "11",
        "completed_at": "2026-01-02T03:00:00+00:00",
        "period_start": "2026-01-01T00:00:00+00:00",
        "period_end": "2026-01-01T23:59:59+00:00",
        "request": {
            "audit_key": "11", "portfolio_id": 11, "portfolio_type": "balanced",
            "period_mode": "fixed_dates", "period_days": 7,
            "period_start_date": "2025-12-26", "period_end_date": "2026-01-01",
        },
    }
    payload.update(changes)
    return {
        "status": "completed", "audit_id": payload["audit_id"],
        "finished_at": "2026-01-02T03:00:00+00:00",
        "last_result": None, "last_payload": payload,
    }
