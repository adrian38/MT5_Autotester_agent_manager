"""Lo que los crones recuerdan entre vuelta y vuelta, persistido en disco.

Sale de `live_audit_watch` porque no es suyo: el mismo almacen sirve al cron de
auditoria (`live_audit_runs`), al del sondeo de cuentas (`live_account_runs`) y
al del resumen mensual (`monthly_summary_runs`). Tres duenos y ninguno es el
vigilante, asi que vive por debajo de los tres.

`live_audit_watch` lo reexporta: `from .live_audit_watch import
LiveAuditRunState` sigue funcionando.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import live_audit_catch_up
from ..common import load_json, save_json


class LiveAuditRunState:
    """Lo que el vigilante recuerda de cada uso entre vuelta y vuelta.

    Una entrada por `nodo/uso`: el turno que esta sirviendo, cuantos intentos
    lleva en el, como acabo el ultimo y el ultimo estado visto en el nodo. Se
    persiste porque un reinicio del manager no puede reiniciar la cuenta de
    reintentos ni repetir avisos ya dados.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                stored = load_json(self.path)
            except ValueError:
                stored = {}
            self.records = {
                str(key): dict(value)
                for key, value in (stored.get("audits") or {}).items()
                if isinstance(value, dict)
            }
            if live_audit_catch_up.restore_legacy_markers(self.records):
                save_json(self.path, {"audits": self.records})

    @staticmethod
    def key(node_id: str, audit_id: str) -> str:
        return f"{node_id}/{audit_id}"

    def record(self, key: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.records.get(key) or {})

    def update(self, key: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            record = {**(self.records.get(key) or {}), **changes}
            self.records[key] = record
            save_json(self.path, {"audits": self.records})
            return dict(record)

    def state(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {key: dict(value) for key, value in self.records.items()}
