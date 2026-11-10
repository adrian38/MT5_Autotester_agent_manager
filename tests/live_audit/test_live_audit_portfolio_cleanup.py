import tempfile
import unittest
from pathlib import Path

from mt5_manager.live_audit_settings import (
    DEFAULT_LIVE_AUDIT_PROFILE,
    LiveAuditSettingsStore,
)


def profile(login: str, portfolio_id: int, portfolio_type: str) -> dict[str, object]:
    return {
        **DEFAULT_LIVE_AUDIT_PROFILE,
        "source_login": login,
        "source_server": "Broker-Live",
        "tester_login": f"9{login}",
        "tester_server": "Broker-Demo",
        "portfolio_id": portfolio_id,
        "portfolio_type": portfolio_type,
        "source_password": f"source-{login}",
        "tester_password": f"tester-{login}",
    }


class LiveAuditPortfolioCleanupTests(unittest.TestCase):
    def test_deleted_portfolio_removes_all_its_uses_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = LiveAuditSettingsStore(root / "live_audit_settings.json")
            store.update("node-a", {
                "selected_audit_ids": ["main", "reserve", "other"],
                "profiles": {
                    "main": profile("111", 11, "balanced"),
                    "reserve": profile("222", 11, "conservative"),
                    "other": profile("333", 12, "aggressive"),
                },
            })
            state = store.remove_portfolio("node-a", 11)
            reloaded = LiveAuditSettingsStore(root / "live_audit_settings.json")

        self.assertEqual(state["selected_audit_ids"], ["other"])
        self.assertEqual(set(state["profiles"]), {"other"})
        self.assertEqual(set(reloaded.credential_records["node-a"]), {"other"})


if __name__ == "__main__":
    unittest.main()
