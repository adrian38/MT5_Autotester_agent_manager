from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mt5_manager.portfolio_service import (
    PortfolioCoordinator,
    filter_rows_by_active_portfolios,
    normalize_settings,
)
from mt5_manager.portfolio_improvement_common import (
    improvement_inherited_overrides,
    improvement_options,
)


class ActivePortfolioStrategyTests(unittest.TestCase):
    def test_filter_keeps_only_live_member_sets(self) -> None:
        rows = [
            {"set_path": r"C:\pool\active.set"},
            {"set_path": r"C:\pool\inactive.set"},
        ]
        inputs = {
            "active_portfolios_only": True,
            "_active_portfolio_members": [{"set_path": "C:/pool/active.set"}],
        }

        self.assertEqual(filter_rows_by_active_portfolios(rows, inputs), [rows[0]])

    def test_filter_reports_an_empty_live_pool(self) -> None:
        with self.assertRaisesRegex(ValueError, "portafolios activos"):
            filter_rows_by_active_portfolios(
                [{"set_path": "candidate.set"}],
                {"active_portfolios_only": True, "_active_portfolio_members": []},
            )

    def test_setting_disables_the_conflicting_used_set_filter(self) -> None:
        self.assertFalse(normalize_settings("full_history", {})["active_portfolios_only"])
        enabled = normalize_settings(
            "full_history", {"active_portfolios_only": "yes", "exclude_used_sets": True},
        )
        self.assertTrue(enabled["active_portfolios_only"])
        self.assertFalse(enabled["exclude_used_sets"])
        self.assertFalse(normalize_settings(
            "monthly", {"active_portfolios_only": True},
        )["active_portfolios_only"])

    def test_improvement_accepts_the_inherited_value_and_keeps_used_candidates(self) -> None:
        requested = {
            "active_portfolios_only": True,
            "improvement_exclude_used_sets": True,
            "improvement_inherited_overrides": {"active_portfolios_only": True},
        }

        self.assertEqual(
            improvement_inherited_overrides(requested),
            {"active_portfolios_only": True},
        )
        self.assertFalse(improvement_options(requested).exclude_used_sets)

    def test_live_members_are_resolved_for_the_same_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = PortfolioCoordinator([], Path(temp_dir) / "settings.json")
            coordinator.live_audit_settings = SimpleNamespace(
                state=lambda _node_id: {
                    "live_audit_ids": ["use-12", "use-19"],
                    "profiles": {
                        "use-12": {"portfolio_id": 12, "portfolio_type": "balanced"},
                        "use-19": {"portfolio_id": 19, "portfolio_type": "aggressive"},
                    },
                }
            )
            source = SimpleNamespace(
                project=Path(temp_dir),
                saved_portfolio_detail=lambda portfolio_id, _scope: {
                    "portfolio": {
                        "portfolio_type": "bundle",
                        "members": [
                            {"set_path": f"{portfolio_id}-a.set", "variant_key": "aggressive"},
                            {"set_path": f"{portfolio_id}-m.set", "variant_key": "balanced"},
                        ],
                    }
                },
            )

            settings = coordinator._with_active_portfolio_members(
                "axi", source, {"active_portfolios_only": True},
            )

        self.assertEqual(
            [member["set_path"] for member in settings["_active_portfolio_members"]],
            [str(Path(temp_dir) / "12-m.set"), str(Path(temp_dir) / "19-a.set")],
        )

    def test_improvement_inherits_and_edits_the_filter(self) -> None:
        static = Path(__file__).parents[2] / "mt5_manager" / "static"
        script = (static / "portfolio_improvement.js").read_text(encoding="utf-8")
        monthly = (static / "portfolio_monthly_improvement.js").read_text(encoding="utf-8")

        self.assertIn('name="active_portfolios_only" type="checkbox"', script)
        self.assertIn(
            "setValue('active_portfolios_only', inherited.active_portfolios_only)", script,
        )
        self.assertIn(
            "active_portfolios_only: fields.active_portfolios_only.checked", script,
        )
        self.assertIn("fields.improvement_exclude_used_sets.disabled", script)
        self.assertNotIn("active_portfolios_only", monthly)


if __name__ == "__main__":
    unittest.main()
