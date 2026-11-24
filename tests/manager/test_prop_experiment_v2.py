"""V2 search and persistent evidence use the same Prop campaign contract."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from unittest.mock import patch

from mt5_manager.experiment.experiment_lab import LabAxis
from mt5_manager.experiment.experiment_seed_sets import single_report_strategy
from mt5_manager.prop.prop_experiment_cache import PropEvidenceCache
from mt5_manager.prop.prop_experiment_engine import evaluate, executable_pool, search
from mt5_manager.prop.prop_experiment_engine_v2 import _growth_builder, search_v2
from mt5_manager.prop.prop_position_book import PositionEvent, PropPosition
from mt5_manager.prop.prop_profiles import profile
from portfolio_manager.ubs_portfolio import ClosedTrade, PeriodReport

from .test_prop_experiment import _strategy


class PropEvidenceCacheTests(unittest.TestCase):
    def test_report_round_trip_refresh_and_file_change(self):
        opened = datetime(2025, 1, 2, 8)
        closed = opened + timedelta(hours=2)
        trade = ClosedTrade(opened, closed, 'EURUSD', 0.01, 45.0, commission=-1.0,
                            open_price=1.09, close_price=1.1, side='buy', currency='USD')
        period = PeriodReport('OHLC', 2025, 2026, 'EURUSD', 'H1', [0.0, 44.0],
                              44.0, 0.0, 0.0, float('inf'), 44.0, 1,
                              closed_trades=[trade], pnl_points_001=[(closed, 44.0)],
                              backtest_start='2025-01-01', backtest_end='2026-05-21')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'prop.sqlite3'
            report = path.with_suffix('.htm')
            report.write_text('first', encoding='utf-8')
            with patch('mt5_manager.prop.prop_experiment_cache._load_period', return_value=period) as load:
                with PropEvidenceCache(path) as cold:
                    self.assertEqual(cold.read_period(report), period)
                with PropEvidenceCache(path) as warm:
                    restored = warm.read_period(report)
                    self.assertEqual(restored, period)
                    self.assertEqual((warm.hits, warm.misses), (1, 0))
                load.assert_called_once_with(report)
                seed = single_report_strategy(restored, {}, 'a.set', 'a', 'seed_accepted', report_path=str(report))
                self.assertEqual(seed.report_2025_2026.period_name, 'seed')
                self.assertEqual(seed.closed_trades_2020_2026, [trade])
                with PropEvidenceCache(path, refresh=True) as forced:
                    forced.read_period(report)
                    forced.read_period(report)
                self.assertEqual(load.call_count, 2)
                report.write_text('replaced with a new report', encoding='utf-8')
                with PropEvidenceCache(path) as changed:
                    changed.read_period(report)
                self.assertEqual(load.call_count, 3)

    def test_grid_refresh_reads_the_set_even_when_its_signature_does_not_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'prop.sqlite3'
            set_path = path.with_name('strategy.set')
            set_path.write_text('EnableGrid=0', encoding='utf-8')
            stamp = set_path.stat().st_mtime_ns
            with PropEvidenceCache(path) as cold:
                self.assertFalse(cold.is_grid(str(set_path)))
            set_path.write_text('EnableGrid=1', encoding='utf-8')
            os.utime(set_path, ns=(stamp, stamp))
            with PropEvidenceCache(path, refresh=True) as forced:
                self.assertTrue(forced.is_grid(str(set_path)))
            with PropEvidenceCache(path) as warm:
                self.assertTrue(warm.is_grid(str(set_path)))
                self.assertEqual(warm.hits, 1)


class PropV2SearchTests(unittest.TestCase):
    def test_finds_a_composition_with_more_and_faster_approvals(self):
        days = tuple((date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(32))
        axis = LabAxis(days, (0,) * 31 + (1,), 2)
        rng = Random(1)
        labs = [replace(_strategy(str(i), 10, tuple(rng.randrange(-180, 270, 20) for _ in days)),
                        margin_per_unit=750) for i in range(5)]
        items = executable_pool(labs, set(), set())
        program = profile('the5ers-growth')
        settings = {'capital': 5000}
        before = search(items, axis, program, settings)
        after = search_v2(items, axis, program, settings)
        self.assertGreater(after.evaluation.passed, before.evaluation.passed)
        self.assertLess(after.evaluation.median_days, before.evaluation.median_days)
        repaired = search_v2(items, axis, program, settings, required_keys={'2'})
        self.assertIn('2', repaired.allocations)

    def test_lazy_position_days_match_the_original_campaign(self):
        start = datetime(2025, 1, 31, 8)
        end = start + timedelta(hours=2)
        days = ('2025-01-31', '2025-02-03', '2025-02-04')
        axis = LabAxis(days, (0, 1, 1), 2)
        items = executable_pool([_strategy(key, 10, (0, 0, 0)) for key in ('a', 'b')], set(), set())
        for index, (item, pnl) in enumerate(zip(items, (-150.0, 600.0))):
            position = PropPosition(item.lab.key, start, end, 100, 100 + pnl / 100, 1, 1, 100, 1, 25, pnl)
            items[index] = replace(item, position_events={0: (
                PositionEvent(start, position, True), PositionEvent(end, position, False),
            )})
        build = _growth_builder(items)
        program = profile('the5ers-growth')
        for units in ({'a': 1, 'b': 1}, {'a': 2, 'b': 1}, {'a': 10, 'b': 1}):
            expected = evaluate(items, units, axis, program, 5000, 120)
            self.assertEqual(evaluate(items, units, axis, program, 5000, 120, build_growth_series=build), expected)
