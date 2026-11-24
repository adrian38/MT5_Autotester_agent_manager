"""El pool prop mantiene separadas fuentes y filtro de portafolios activos."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from portfolio_manager.ubs_portfolio import ClosedTrade
from mt5_manager.prop import prop_experiment_pool as pool
from mt5_manager.prop.prop_execution import destination_contract
from tests.manager.test_experiment_lab import strategy


class _Source:
    broker = "TEST"
    project = Path("C:/agent")

    @staticmethod
    def quarantine_rows() -> list[dict[str, object]]:
        return []

    @staticmethod
    def _match_key(value: object) -> str:
        """La clave de ruta de ``PortfolioSource``, sin resolver el proyecto."""
        return str(value or "").replace("/", "\\").casefold()

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
    def test_fx_reference_reads_previous_ohlc_when_the_first_rate_is_missing(self) -> None:
        source = _Source()
        bridge = {'target_symbol': 'EURUSD', 'is_report_path': 'previous.htm'}
        cross = {'target_symbol': 'USDJPY', 'is_report_path': 'cross.htm'}
        current = {'target_symbol': 'EURJPY', 'oos_report_path': 'current.htm'}
        source.candidate_rows = lambda **_kwargs: [bridge, cross, current]
        with mock.patch.object(pool, '_read_ohlc_sets', return_value=[]) as read:
            pool.load_fx_reference(
                lambda _: source, {'source_nodes': ['node']}, print, {'JPY'},
                preceding={'JPY'},
            )
        calls = {call.args[1]: call.args[0] for call in read.call_args_list}
        self.assertCountEqual(calls['oos_report_path'], [cross, current])
        self.assertCountEqual(calls['is_report_path'], [bridge, cross])

    def test_opening_inventory_counts_admitted_sets_without_parsing_reports(self) -> None:
        source = _Source()
        source.candidate_rows = lambda **_kwargs: [
            {"set_path": "C:/sets/a.set", "target_symbol": "EURUSD"},
        ]
        seeds = [
            {"seed_path": "C:/sets/robust_000001_a.set", "symbol": "EURUSD"},
            {"seed_path": "C:/sets/b.set", "symbol": "EURUSD"},
        ]
        manuals = [
            {"set_path": "C:/sets/b.set", "symbol": "EURUSD"},
            {"set_path": "C:/sets/c.set", "symbol": "EURUSD"},
        ]
        with (
            mock.patch.object(pool, "accepted_seed_rows", return_value=seeds),
            mock.patch.object(pool, "_manual_member_rows", return_value=manuals),
        ):
            result = pool.load_inventory(
                {"source_nodes": ["ic"], "symbol_families": ["Forex"],
                 "active_portfolios_only": False},
                lambda _node_id: source,
            )
        self.assertEqual(result["strategies"], 3)
        self.assertEqual(result["by_broker"][0], {
            "node_id": "ic", "broker": "TEST", "candidates": 1,
            "seeds": 1, "manual": 1, "total": 3,
            "symbol_exceptions": [], "symbol_exception_total": 0,
        })

    def test_symbol_exception_totals_follow_family_and_source_filters(self) -> None:
        source = _Source()
        source.candidate_rows = lambda **_kwargs: [
            {"set_path": "C:/sets/a.set", "target_symbol": "EURHKD"},
            {"set_path": "C:/sets/SE30_grid.set", "target_symbol": "SE30"},
            {"set_path": "C:/sets/b.set", "target_symbol": "XAUEUR"},
            {"set_path": "C:/sets/c.set", "target_symbol": "XAUUSD"},
        ]
        seeds = [{"seed_path": "C:/sets/robust_202601_a.set", "symbol": "EURHKD"}]
        manual = [{"set_path": "C:/sets/other.set", "symbol": "XAUEUR"}]
        settings = {"source_nodes": ["ic"], "symbol_families": ["Forex", "Metals"],
                    "active_portfolios_only": False}
        with (
            mock.patch.object(pool, "accepted_seed_rows", return_value=seeds),
            mock.patch.object(pool, "_manual_member_rows", return_value=manual),
        ):
            result = pool.load_inventory(settings, lambda _: source)
            forex = pool.load_inventory({**settings, "symbol_families": ["Forex"]}, lambda _: source)
        row = result["by_broker"][0]
        self.assertEqual((result["strategies"], result["symbol_exception_total"], row["symbol_exception_total"]), (1, 3, 3))
        self.assertEqual([(item["family"], item["symbol"], item["total"]) for item in row["symbol_exceptions"]],
                         [("Forex", "EURHKD", 1), ("Metals", "XAUEUR", 2)])
        self.assertEqual((forex["strategies"], forex["symbol_exception_total"]), (0, 1))

    def test_symbol_exceptions_do_not_open_candidate_seed_or_manual_reports(self) -> None:
        source = _Source()
        unsupported = {"seed_path": "a.set", "set_path": "a.set", "symbol": "EURHKD"}
        supported = {"seed_path": "b.set", "set_path": "b.set", "symbol": "EURUSD"}
        source.candidate_rows = lambda **_kwargs: [unsupported, supported]
        settings = {"include_candidates": True, "include_seeds": True, "include_manual_portfolios": True}
        with (
            mock.patch.object(pool, "_manual_member_rows", return_value=[unsupported, supported]),
            mock.patch.object(pool, "_read_ohlc_sets", return_value=[]) as read,
            mock.patch("mt5_manager.experiment.experiment_seed_sets.accepted_seed_rows", return_value=[unsupported, supported]),
            mock.patch("mt5_manager.experiment.experiment_seed_sets._seed_strategy", return_value=SimpleNamespace(symbol="EURUSD")) as seed,
        ):
            pool._candidate_sets(source, settings, None, [], lambda _: None)
            pool._manual_sets(source, settings, None, [], lambda _: None)
            pool._seed_sets(source, settings, None, [], lambda _: None)
        self.assertEqual([call.args[0] for call in read.call_args_list], [[supported], [supported]])
        seed.assert_called_once_with(supported, source, pool._ohlc_report)

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
        with mock.patch.object(pool.Path, "is_file", return_value=True), mock.patch.object(
            pool, "_ohlc_report", side_effect=lambda path: captured.append(path) or SimpleNamespace(symbol="EURUSD", timeframe="H1"),
        ), mock.patch.object(pool, "single_report_strategy", return_value=strategy("a", "EURUSD", {"2025-01-02": 1})) as build:
            pool._candidate_sets(
                source,
                {"include_candidates": True, "symbol_families": [], "disabled_symbols": []},
                None, [], lambda _message: None,
            )
        self.assertEqual(captured, [Path("C:/reports/oos_ohlc.html")])
        build.assert_called_once()

    def test_prop_leverage_replaces_the_origin_brokers_leverage(self) -> None:
        self.assertEqual(destination_contract("EURUSD").leverage, 30.0)
        self.assertEqual(destination_contract("XAUUSD").leverage, 25.0)
        self.assertEqual(destination_contract("US500").leverage, 25.0)

    def test_origin_specs_supply_each_strategys_native_contract_separately(self) -> None:
        stamp = datetime(2025, 1, 2)
        deals = [ClosedTrade(stamp, stamp, 'XAGUSD', 0.01, 10)]
        rows = [
            (replace(strategy(node, "XAGUSD", {"2025-01-02": 10.0}), closed_trades_2020_2026=deals), node, node)
            for node in ("IC", "AXI")
        ]
        specs = {
            node: ({'XAGUSD': {'contract_size': contract, 'volume_min': 0.01, 'volume_step': 0.01}}, node, '')
            for node, contract in {'IC': 1000, 'AXI': 5000}.items()
        }
        items, _, _, _ = pool._assemble_pool(rows, set(), set(), [], {}, lambda _: None, specs)
        self.assertEqual(
            {item.lab.origin: (item.source_volume.contract, item.prop_contract.volume.contract) for item in items},
            {"IC": (1000, 5000), "AXI": (5000, 5000)},
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
        source.quarantine_rows = lambda: [{"set_path": "C:/runs/13/same.set"}]
        settings = {
            "include_candidates": True, "include_seeds": True,
            "include_manual_portfolios": True, "symbol_families": [],
        }
        discarded = _item("discarded", "C:/runs/13/same.set")
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

    def test_a_homonym_from_another_run_survives_the_quarantine(self) -> None:
        """El nombre del set no identifica la estrategia: la ruta sí.

        ``..._g002_s001_v003.set`` son posiciones dentro de su run, así que dos
        runs reutilizan el nombre para parámetros distintos. Comparando por
        nombre, excluir uno tiraba al otro.
        """
        source = _Source()
        source.quarantine_rows = lambda: [{"set_path": "C:/runs/13/same.set"}]
        settings = {
            "include_candidates": True, "include_seeds": True,
            "include_manual_portfolios": True, "symbol_families": [],
        }
        homonym = _item("homonym", "C:/runs/15/same.set")
        homonym.symbol = "EURUSD"
        with (
            mock.patch.object(pool, "_candidate_sets", return_value=([homonym], 1, 1)),
            mock.patch.object(pool, "_seed_sets", return_value=([], 0)),
            mock.patch.object(pool, "_manual_sets", return_value=([], 0)),
        ):
            loaded, _seeds, _manual, meta = pool.load_origin(
                source, "ic", settings, [], lambda _message: None, None,
            )
        self.assertEqual([item.set_id for item, _broker, _node in loaded], ["homonym"])
        self.assertEqual(meta["quarantine_filtered_out"], 0)

    def test_the_inventory_discounts_grids_like_the_executable_pool(self) -> None:
        """La tarjeta no puede prometer sets que ``credible_pool`` tira después."""
        source = _Source()
        source.candidate_rows = lambda **_kwargs: [
            {"set_path": "C:/sets/a.set", "target_symbol": "EURUSD"},
            {"set_path": "C:/sets/EURUSD_grid_b.set", "target_symbol": "EURUSD"},
        ]
        settings = {
            "source_nodes": ["ic"], "symbol_families": ["Forex"],
            "active_portfolios_only": False,
        }
        with (
            mock.patch.object(pool, "accepted_seed_rows", return_value=[]),
            mock.patch.object(pool, "_manual_member_rows", return_value=[]),
        ):
            without_grids = pool.load_inventory(settings, lambda _node_id: source)
            with_grids = pool.load_inventory(
                {**settings, "include_grids": True}, lambda _node_id: source,
            )
        self.assertEqual(without_grids["by_broker"][0]["candidates"], 1)
        self.assertEqual(with_grids["by_broker"][0]["candidates"], 2)


if __name__ == "__main__":
    unittest.main()
