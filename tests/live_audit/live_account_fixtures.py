"""Los dobles que comparten las pruebas del sondeo de cuentas reales.

No son pruebas: son el agente de mentira y su archivo, que
`test_live_account_cron` y sus hermanas montan igual. Vivían dentro del fichero
del cron, y al crecer éste —el refresco del cambio de divisa del 2026-11-16 fue
lo que lo pasó de 600 líneas— se sacaron aquí sin tocar su contenido.

`FakeServer` es deliberadamente lo **mínimo** que `live_account_watch.tick` le
pide al servidor real, y por eso es el sitio donde se nota cuando el tick
aprende a llamar a algo nuevo: mientras el método no esté, `tick` lo da por
fallido, lo escribe en `stderr` y la prueba pasa en verde sin comprobar nada.
Pasó con `update_live_account_scheduler`, y de paso las veintiséis pruebas de
aquel fichero descargaban el feed del BCE de verdad.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

from mt5_manager.live_account.live_account_archive import LiveAccountArchive
from mt5_manager.live_account.live_account_schedule import (
    normalize_live_account_scheduler_settings,
)
from mt5_manager.live_audit.live_audit_notifications import LiveAuditNotificationStore
from mt5_manager.live_audit.live_audit_watch import LiveAuditRunState

REPORT = b"<html>informe</html>"
SHA = hashlib.sha256(REPORT).hexdigest()


def parsed_account():
    stamp = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
    return (
        [(stamp, 7.5)], [(stamp, 8.0)], [("2026-10-20", 1_000.0)],
        [(stamp, 500.0, "balance", "Transfer from 77027933")],
    )


def profile(**changes: object) -> dict[str, object]:
    row = {
        "portfolio_id": 105, "portfolio_type": "aggressive",
        "deployment_name": "Port_107_AXI", "live_trading": True,
        "source_login": "60082017", "source_server": "Axi-US50-Live",
        "real_strategy_lots": {"AXI/STANDARD:1437": 0.01},
        "autolot": {"enabled": False},
    }
    row.update(changes)
    return row


def node_state(**changes: object) -> dict[str, object]:
    row = {
        # Una sola tarjeta abierta y dos usos reales: es el caso que dejó al
        # cron semanal vigilando uno de cuatro.
        "configured_audit_ids": ["audit-105"],
        "profiles": {
            "audit-105": profile(),
            "audit-109": profile(
                portfolio_id=109, deployment_name="Christmas_Axi",
                source_login="60082303", autolot={"enabled": True},
            ),
            "audit-64": profile(portfolio_id=64, live_trading=False, source_login="1"),
        },
        "credential_state": {
            "audit-105": {"source_password_saved": True},
            "audit-109": {"source_password_saved": True},
            "audit-64": {"source_password_saved": True},
        },
        "restore_account": {"configured": True},
    }
    row.update(changes)
    return row


class FakeSettingsStore:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    def state(self, _node_id: str) -> dict[str, object]:
        return self._state

    def credentials(self, _node_id: str, _audit_id: str) -> dict[str, str]:
        return {"source_password": "secreto"}

    def restore_credentials(self, _node_id: str) -> dict[str, str]:
        return {
            "restore_login": "60268704", "restore_server": "Axi-US51-Live",
            "restore_password": "final",
        }


class FakeSource:
    """Lo mínimo que `_rebuild` le pide a la memoria del agente."""

    project = ""

    def saved_portfolio_detail(self, portfolio_id, scope):
        return {"portfolio": {"id": portfolio_id, "members": []}}


class FakeServer:
    def __init__(self, root: Path, state: dict[str, object], **changes: object) -> None:
        self.nodes = [{"id": "axi", "name": "AXI Standard", "url": "http://127.0.0.1:1"}]
        self.live_audit_settings = FakeSettingsStore(state)
        self.live_accounts = LiveAccountArchive(root / "live_accounts")
        self.live_account_runs = LiveAuditRunState(root / "cron.json")
        self.live_audit_notifications = LiveAuditNotificationStore(root / "avisos.json")
        self.live_account_scheduler_settings = normalize_live_account_scheduler_settings(changes)
        self.live_account_scheduler_enabled = bool(changes.get("enabled"))
        self.live_account_stop = threading.Event()
        self.live_account_wakeup = threading.Event()
        self.rate_saves: list[dict[str, object]] = []

    def events(self) -> list[dict[str, object]]:
        return self.live_audit_notifications.state(50)["events"]

    def update_live_account_scheduler(self, changes: dict[str, object]) -> dict:
        """El refresco del cambio de divisa guarda por aquí.

        Sin este método el `tick` lo daba por fallido y escribía en `stderr`,
        que es ruido verde: la prueba pasaba sin comprobar nada.
        """
        self.rate_saves.append(dict(changes))
        self.live_account_scheduler_settings = normalize_live_account_scheduler_settings(
            changes, self.live_account_scheduler_settings,
        )
        return dict(self.live_account_scheduler_settings)


def snapshot_state(status: str = "completed", **changes: object) -> dict[str, object]:
    row = {
        "snapshot_id": "20261021_230500_000001", "status": status,
        "finished_at": "2026-10-21T21:10:00+00:00",
        "period_start": "2026-10-17T00:00:00+00:00",
        "period_end": "2026-10-21T21:00:00+00:00",
        "accounts": {
            "60082017@Axi-US50-Live": {
                "login": "60082017", "server": "Axi-US50-Live", "currency": "EUR",
                "balance": 1200.0, "equity": 1210.0, "status": "completed",
                "report": {"filename": "account_60082017.html", "sha256": SHA},
            },
            "60082303@Axi-US50-Live": {
                "login": "60082303", "server": "Axi-US50-Live", "currency": "USD",
                "status": "failed", "error": "la cuenta no quedó conectada",
            },
        },
    }
    row.update(changes)
    return row


def complete_snapshot_state(**changes: object) -> dict[str, object]:
    """El mismo sondeo, pero con las dos cuentas leídas.

    Es lo que separa un turno cumplido de uno que hay que reintentar, así que
    no vale con mirar el `finished_at`: las dos formas lo traen.
    """
    row = snapshot_state()
    accounts = dict(row["accounts"])
    accounts["60082303@Axi-US50-Live"] = {
        "login": "60082303", "server": "Axi-US50-Live", "currency": "USD",
        "balance": 800.0, "equity": 805.0, "status": "completed",
        "report": {"filename": "account_60082303.html", "sha256": SHA},
    }
    row["accounts"] = accounts
    row.update(changes)
    return row

