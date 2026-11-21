"""El pool prop mantiene separadas fuentes y filtro de portafolios activos."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from portfolio_manager.ubs_portfolio import MarginModel
from mt5_manager.prop import prop_experiment_pool as pool


class _Source:
    broker = "TEST"
    project = Path("C:/agent")

    @staticmethod
    def saved_portfolios(_scope: str) -> list[dict[str, object]]:
        return [
            {"id": 7, "manual_portfolio": True},
            {"id": 8, "manual_portfolio": False},
        ]

    @staticmethod
    def saved_portfolio_detail(portfolio_id: int, _scope: str) -> dict[str, object]:
        return {"portfolio": {"members": [
            {"set_path": f"C:/old/manual_{portfolio_id}.set", "symbol": "EURUSD"},
        ]}}


def _item(set_id: str, set_path: str) -> SimpleNamespace:
    return SimpleNamespace(set_id=set_id, set_path=set_path)


class PropExperimentPoolTests(unittest.TestCase):
    def test_symbol_families_filter_the_funnel_before_search(self) -> None:
        items = [
            SimpleNamespace(symbol="EURUSD"),
            SimpleNamespace(symbol="XAUUSD"),
            SimpleNamespace(symbol="BTCUSD"),
        ]
        filtered = pool._filter_symbol_families(items, {"symbol_families": ["Forex", "Metals"]})
        self.assertEqual([item.symbol for item in filtered], ["EURUSD", "XAUUSD"])

    def test_prop_leverage_replaces_the_origin_brokers_leverage(self) -> None:
        with mock.patch.object(pool, "build_margin_model", return_value=MarginModel()):
            model = pool._prop_margin_model(_Source(), {"profile_id": "the5ers-growth"})
        self.assertEqual(model.account_leverage, 30.0)
        self.assertEqual(model.leverage_for("EURUSD"), 30.0)
        self.assertEqual(model.leverage_for("AAPL"), 30.0)

    def test_active_filter_is_independent_from_the_selected_sources(self) -> None:
        callback = mock.Mock(return_value=[{"set_path": "C:/live/Only.set"}])
        allowed = pool._allowed_names(
            {"active_portfolios_only": True}, "ic", _Source(), callback,
        )
        self.assertEqual(allowed, {"only.set"})
        callback.assert_called_once()
        self.assertIsNone(pool._allowed_names(
            {"active_portfolios_only": False}, "ic", _Source(), None,
        ))

    def test_manual_source_reads_manual_portfolios_not_only_active_ones(self) -> None:
        with mock.patch.object(
            pool, "_relocated_member", side_effect=lambda member, _project, _pid: member,
        ):
            rows = pool._manual_member_rows(_Source(), None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["set_path"], "C:/old/manual_7.set")

    def test_deduplication_prioritizes_candidate_then_seed_then_manual(self) -> None:
        settings = {"include_candidates": True, "include_seeds": True, "include_manual_portfolios": True}
        with (
            mock.patch.object(pool, "_candidate_sets", return_value=([_item("c", "robust_202601_same.set")], 1, 1)),
            mock.patch.object(pool, "_seed_sets", return_value=([_item("s", "same.set")], 1)),
            mock.patch.object(pool, "_manual_sets", return_value=([_item("m", "other.set")], 1)),
        ):
            loaded, seeds, manual, meta = pool.load_origin(
                _Source(), "ic", settings, [], lambda _message: None, None,
            )
        self.assertEqual([item.set_id for item, _broker, _node in loaded], ["c", "m"])
        self.assertEqual(seeds, set())
        self.assertEqual(manual, {"m"})
        self.assertEqual(meta["manual_loaded"], 1)


if __name__ == "__main__":
    unittest.main()
