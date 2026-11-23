"""El pool prop mantiene separadas fuentes y filtro de portafolios activos."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from portfolio_manager.ubs_portfolio import MarginModel
from mt5_manager.prop import prop_experiment_pool as pool
from tests.manager.test_experiment_lab import strategy


class _Source:
    broker = "TEST"
    project = Path("C:/agent")

    @staticmethod
    def quarantine_rows() -> list[dict[str, object]]:
        return []

    @staticmethod
    def saved_portfolios(_scope: str) -> dict[str, object]:
        return {"portfolios": [
            {"id": 7, "manual_portfolio": True},
            {"id": 8, "manual_portfolio": False},
        ]}

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

    def test_disabled_symbols_are_filtered_per_broker(self) -> None:
        items = [SimpleNamespace(symbol="EURUSD"), SimpleNamespace(symbol="XAUUSD")]
        filtered = pool._filter_symbol_families(items, {
            "symbol_families": ["Forex", "Metals"], "disabled_symbols": ["XAUUSD"],
        })
        self.assertEqual([item.symbol for item in filtered], ["EURUSD"])

    def test_each_broker_receives_its_own_disabled_symbols(self) -> None:
        captured: dict[str, object] = {}

        def capture(_source, _node_id, settings, *_args):
            captured.update(settings)
            raise RuntimeError("captured")

        with (
            mock.patch.object(pool, "load_origin", side_effect=capture),
            self.assertRaisesRegex(RuntimeError, "captured"),
        ):
            pool.load_pool(
                {"source_nodes": ["ic"], "active_portfolios_only": False},
                lambda _node_id: _Source(), lambda _message: None,
                symbol_settings_factory=lambda _node_id: {"disabled_symbols": ["XAUUSD"]},
            )
        self.assertEqual(captured["disabled_symbols"], ["XAUUSD"])
        self.assertTrue(captured["include_candidates"])
        self.assertTrue(captured["include_seeds"])
        self.assertTrue(captured["include_manual_portfolios"])

    def test_candidate_loader_uses_oos_ohlc_but_not_any_tick_report(self) -> None:
        source = _Source()
        source.candidate_rows = lambda **_kwargs: [{
            "set_path": "C:/sets/a.set", "target_symbol": "EURUSD",
            "oos_report_path": "C:/reports/oos_ohlc.html",
            "full_history_report_path": "C:/reports/probe_every_tick.html",
            "final_tick_report_path": "C:/reports/final_every_tick.html",
            "final_tick_to_date": "2026.06.01",
        }]
        captured = []
        with mock.patch.object(
            pool, "load_robust_sets_from_rows",
            side_effect=lambda rows, *_args, **_kwargs: (captured.extend(rows) or [], []),
        ):
            pool._candidate_sets(
                source,
                {"include_candidates": True, "symbol_families": [], "disabled_symbols": []},
                None, [], lambda _message: None,
            )
        self.assertEqual(captured[0]["oos_report_path"], "C:/reports/oos_ohlc.html")
        self.assertEqual(captured[0]["full_history_report_path"], "")
        self.assertEqual(captured[0]["final_tick_report_path"], "")

    def test_prop_leverage_replaces_the_origin_brokers_leverage(self) -> None:
        with mock.patch.object(pool, "build_margin_model", return_value=MarginModel()):
            model = pool._prop_margin_model(_Source(), {"profile_id": "the5ers-growth"})
        self.assertEqual(model.account_leverage, 30.0)
        self.assertEqual(model.leverage_for("EURUSD"), 30.0)
        self.assertEqual(model.leverage_for("XAUUSD"), 25.0)
        self.assertEqual(model.leverage_for("US500"), 25.0)

    def test_origin_models_supply_each_strategys_margin_and_lot(self) -> None:
        rows = [
            (strategy(node, "EURUSD", {"2025-01-02": 10.0}), node, node)
            for node in ("IC", "AXI")
        ]
        margins = {
            "IC": MarginModel(default_leverage=25.0, symbol_min_lot={"EURUSD": 0.1},
                              symbol_notional={"EURUSD": 250.0}),
            "AXI": MarginModel(default_leverage=25.0, symbol_min_lot={"EURUSD": 1.0},
                               symbol_notional={"EURUSD": 1250.0}),
        }
        items, _, _, _ = pool._assemble_pool(rows, margins, set(), set(), [], {}, lambda _: None)
        self.assertEqual(
            {item.lab.origin: (item.lot_per_unit, item.lab.margin_per_unit) for item in items},
            {"IC": (0.1, 10.0), "AXI": (1.0, 50.0)},
        )

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

    def test_quarantine_excludes_every_source_before_deduplication(self) -> None:
        source = _Source()
        source.quarantine_rows = lambda: [{"set_path": "C:/discarded/same.set"}]
        settings = {
            "include_candidates": True, "include_seeds": True,
            "include_manual_portfolios": True, "symbol_families": [],
        }
        discarded = _item("discarded", "C:/sets/robust_202601_same.set")
        kept = _item("kept", "C:/sets/kept.set")
        discarded.symbol = kept.symbol = "EURUSD"
        with (
            mock.patch.object(pool, "_candidate_sets", return_value=([kept], 1, 1)),
            mock.patch.object(pool, "_seed_sets", return_value=([discarded], 1)),
            mock.patch.object(pool, "_manual_sets", return_value=([discarded], 1)),
        ):
            loaded, seeds, manual, meta = pool.load_origin(
                source, "ic", settings, [], lambda _message: None, None,
            )
        self.assertEqual([item.set_id for item, _broker, _node in loaded], ["kept"])
        self.assertEqual(seeds, set())
        self.assertEqual(manual, set())
        self.assertEqual(meta["quarantine_filtered_out"], 2)


if __name__ == "__main__":
    unittest.main()
