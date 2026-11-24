from dataclasses import replace
from datetime import datetime
import unittest

from mt5_manager.experiment.experiment_lab import LabAxis
from mt5_manager.prop.prop_execution import (
    ObservedUsdRates, VolumeSpec, destination_contract, equivalent_volume_unit, source_volume_spec,
)
from mt5_manager.prop.prop_experiment_engine import Evaluation, executable_pool, growth_series
from mt5_manager.prop.prop_experiment_periods import replay_window
from mt5_manager.prop.prop_experiment_search import CompositionSearch
from mt5_manager.prop.prop_experiment_validation import _validation_groups
from mt5_manager.prop.prop_position_book import PositionEvent, PropPosition
from mt5_manager.prop.the5ers_hyper_growth_engine import GrowthDay, simulate_evaluation
from portfolio_manager.ubs_portfolio import ClosedTrade
from tests.manager.test_prop_experiment import _strategy


class PropExecutionTests(unittest.TestCase):
    def test_roboforex_nikkei_is_not_a_one_to_one_lot_on_the5ers(self):
        source = source_volume_spec('.JP225Cash', {}, 'ROBOFOREX', 'ECN')
        prop = destination_contract('.JP225Cash')
        self.assertEqual((source.contract, prop.symbol, prop.volume.contract), (100, 'JPN225', 1))
        self.assertEqual(equivalent_volume_unit(source, prop.volume), (0.02, 2.0))

    def test_the_old_ictrading_silver_lot_cannot_open_at_ohlc_price(self):
        opened = datetime(2026, 1, 29, 1, 16, 40)
        closed = opened.replace(hour=2)
        silver = ClosedTrade(opened, closed, 'XAGUSD', 0.01, 10, open_price=117.685,
                             close_price=118.685, side='buy', currency='EUR')
        eurusd = ClosedTrade(opened, closed, 'EURUSD', 0.01, 0, open_price=1.2, close_price=1.2)
        day = '2026-01-29'
        axis = LabAxis((day,), (0,), 1)
        strategy = executable_pool([_strategy('silver', 1, (10,))], set(), set())[0]
        strategy = replace(strategy, source_volume=VolumeSpec(1000, 0.01, 0.01),
                           prop_contract=destination_contract('XAGUSD'), coverage=((day, day),),
                           closed_trades=(silver,))
        items, _, _ = replay_window([strategy], axis, day, day, rates=ObservedUsdRates([], [eurusd]))
        self.assertEqual((items[0].lot_per_unit, items[0].source_lot_per_unit), (0.01, 0.05))
        self.assertAlmostEqual(items[0].series[0], 60)
        replay = simulate_evaluation(growth_series(items, {'silver': 56}, axis))
        self.assertEqual(replay.margin_rejections, 1)
        self.assertEqual(replay.final_balance, 5000)

    def test_available_margin_uses_equity_after_a_loss_and_ignores_rejected_closes(self):
        morning = datetime(2025, 1, 2, 9)
        noon = morning.replace(hour=12)
        afternoon = morning.replace(hour=14)
        loser = PropPosition('test1', morning, noon, 100, 99, 1, 1, 100, 1, 25, False, -100)
        winner = PropPosition('test2', noon, afternoon, 247.5, 300, 1, 1, 500, 1, 25, False, 1500)
        day = GrowthDay(0, date='2025-01-02', positions=(
            PositionEvent(morning, loser, True), PositionEvent(noon, loser, False),
            PositionEvent(noon, winner, True), PositionEvent(afternoon, winner, False),
        ))
        replay = simulate_evaluation([day])
        self.assertFalse(replay.passed)
        self.assertEqual(replay.margin_rejections, 1)
        self.assertEqual(replay.final_balance, 4900)

    def test_open_loss_at_an_observed_price_can_fail_before_a_profitable_close(self):
        morning = datetime(2025, 1, 2, 9)
        noon = morning.replace(hour=12)
        afternoon = morning.replace(hour=14)
        held = PropPosition('test', morning, afternoon, 100, 110, 1, 1, 100, 1, 25, False, 1000)
        quote = PropPosition('test', noon, afternoon, 96, 110, 1, 1, 1, 1, 25, False, 14)
        day = GrowthDay(0, date='2025-01-02', positions=(
            PositionEvent(morning, held, True), PositionEvent(noon, quote, True),
            PositionEvent(afternoon, held, False), PositionEvent(afternoon, quote, False),
        ))
        replay = simulate_evaluation([day])
        self.assertEqual(replay.reason, 'stop_out')
        self.assertEqual(replay.final_balance, 4600)

    def test_full_pool_can_beat_the_saturated_pair_with_a_low_ranked_third(self):
        margins = dict.fromkeys(['a', 'b', *(f'mid{i:02}' for i in range(24)), 'third'], 1)

        def measure(lots):
            if {'a', 'b', 'third'} <= lots.keys() and min(lots['a'], lots['b']) >= 2:
                passed = 12
            elif lots == {'a': 4, 'b': 4}:
                passed = 10
            elif len(lots) == 1 and 'third' not in lots:
                passed = 8 - abs(next(iter(lots.values())) - 4)
            else:
                passed = 0
            return Evaluation(12, passed, passed * 100 / 12, 3, 100, {})

        optimizer = CompositionSearch(margins, 8, set(), measure, None)
        result, measured = optimizer.run()
        self.assertEqual(set(result), {'a', 'b', 'third'})
        self.assertEqual(measured.passed, 12)
        self.assertEqual({row['members']: row['best_passed'] for row in optimizer.summary()}[2], 10)

    def test_mt5_validation_uses_equal_exposure_at_the_source_broker(self):
        result = {'allocations': [{'origin_node': 'ic', 'lot': 0.07, 'source_lot': 0.35}]}
        self.assertEqual(_validation_groups(result)[0][:2], ('ic', 0.35))
