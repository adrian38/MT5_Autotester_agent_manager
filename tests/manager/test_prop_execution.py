from dataclasses import replace
from datetime import datetime, timedelta
import unittest

from mt5_manager.experiment.experiment_lab import LabAxis
from mt5_manager.prop.prop_execution import (
    ObservedEuroRates, VolumeSpec, destination_contract, equivalent_volume_unit, source_volume_spec,
)
from mt5_manager.prop.prop_experiment_engine import Evaluation, SearchResult, evaluate, executable_pool, growth_series
from mt5_manager.prop.prop_experiment_periods import replay_window
from mt5_manager.prop.prop_experiment_reporting import attempt_analysis
from mt5_manager.prop.prop_experiment_search import CompositionSearch
from mt5_manager.prop.prop_profiles import profile
from mt5_manager.prop.prop_search_base import search_base_payload
from mt5_manager.prop.prop_experiment_validation import _validation_groups
from mt5_manager.prop.prop_position_book import PositionEvent, PropPosition
from mt5_manager.prop.the5ers_hyper_growth_budget import simulate_monthly_campaign
from mt5_manager.prop.the5ers_hyper_growth_engine import GrowthDay, simulate_evaluation
from mt5_manager.prop.the5ers_hyper_growth_rules import HYPER_GROWTH_5K
from portfolio_manager.ubs_portfolio import ClosedTrade
from tests.manager.test_prop_experiment import _strategy


class PropExecutionTests(unittest.TestCase):
    def test_the_base_size_follows_the_approval_and_duration_frontier(self):
        outcomes = {f'set-{index}': (index, index) for index in range(1, 32)}
        outcomes['dominated'] = (15, 50)

        def measure(lots):
            passed, duration = outcomes[next(iter(lots))]
            return Evaluation(40, passed, passed / 40 * 100, duration, 100, {})

        optimizer = CompositionSearch(dict.fromkeys(outcomes, 1), 1, set(), measure, None)
        leaders = optimizer.seed_frontier({}, optimizer.best_single_lots({}))
        self.assertEqual([key for key, _ in leaders], [f'set-{index}' for index in range(31, 0, -1)])

    def test_search_base_reports_brokers_and_real_daily_correlations(self):
        rows = [
            replace(_strategy('gold', 2, (1, 0, -1)), origin='ICTRADING', symbol='XAUUSD'),
            replace(_strategy('yen', 2, (-1, 0, 1)), origin='AXI', symbol='USDJPY'),
            replace(_strategy('flat', 2, (0, 0, 0)), origin='ROBOFOREX', symbol='EURUSD'),
        ]
        strategies = {row.key: item for row, item in zip(rows, executable_pool(rows, set(), set()))}
        optimizer = CompositionSearch(dict.fromkeys(strategies, 1), 10, set(),
                                      lambda _: Evaluation(4, 2, 50, 6, 100, {}), None)
        optimizer.seed_leaders = (('gold', 1), ('yen', 2), ('flat', 1))
        axis = LabAxis(('2025-01-02', '2025-01-03', '2025-01-06'), (0, 0, 0), 1)
        base = search_base_payload(optimizer, strategies, axis)
        self.assertEqual([(row['symbol'], row['broker']) for row in base['strategies']],
                         [('XAUUSD', 'ICTRADING'), ('USDJPY', 'AXI'), ('EURUSD', 'ROBOFOREX')])
        self.assertEqual(base['correlation'], [[1, -1, None], [-1, 1, None], [None, None, None]])
        self.assertEqual(base['days'], 3)

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
        items, _, _ = replay_window([strategy], axis, day, day, rates=ObservedEuroRates([], [eurusd]))
        self.assertEqual((items[0].lot_per_unit, items[0].source_lot_per_unit), (0.01, 0.05))
        self.assertAlmostEqual(items[0].series[0], 50)
        self.assertAlmostEqual(items[0].lab.margin_per_unit, 50 * 118.685 / 1.2 / 25)
        replay = simulate_evaluation(growth_series(items, {'silver': 56}, axis))
        self.assertEqual(replay.margin_rejections, 1)
        self.assertEqual(replay.final_balance, 5000)

    def test_euro_valuation_uses_only_observed_rates(self):
        opened = datetime(2025, 1, 2, 9)
        closed = opened.replace(hour=12)
        eurusd = ClosedTrade(opened, closed, 'EURUSD', 0.01, 0, open_price=1.25, close_price=1.25)
        usdjpy = ClosedTrade(opened, closed, 'USDJPY', 0.01, 0, open_price=150, close_price=160)
        rates = ObservedEuroRates([], [eurusd, usdjpy])
        self.assertEqual(rates.at('EUR', opened - timedelta(days=100)), 1.0)
        self.assertIsNone(rates.at('USD', opened - timedelta(seconds=1)))
        self.assertAlmostEqual(rates.at('JPY', opened), 1 / 150 / 1.25)
        self.assertAlmostEqual(rates.at('JPY', closed), 1 / 160 / 1.25)
        self.assertIsNone(rates.at('JPY', closed + timedelta(days=8)))

    def test_available_margin_uses_equity_after_a_loss_and_ignores_rejected_closes(self):
        morning = datetime(2025, 1, 2, 9)
        noon = morning.replace(hour=12)
        afternoon = morning.replace(hour=14)
        loser = PropPosition('test1', morning, noon, 100, 99, 1, 1, 100, 1, 25, -100)
        winner = PropPosition('test2', noon, afternoon, 247.5, 300, 1, 1, 500, 1, 25, 1500)
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
        held = PropPosition('test', morning, afternoon, 100, 110, 1, 1, 100, 1, 25, 1000)
        quote = PropPosition('test', noon, afternoon, 96, 110, 1, 1, 1, 1, 25, 14)
        day = GrowthDay(0, date='2025-01-02', positions=(
            PositionEvent(morning, held, True), PositionEvent(noon, quote, True),
            PositionEvent(afternoon, held, False), PositionEvent(afternoon, quote, False),
        ))
        replay = simulate_evaluation([day])
        self.assertEqual(replay.reason, 'stop_out')
        self.assertEqual(replay.final_balance, 4600)

    def test_campaign_starts_again_intraday_without_reusing_trades_or_budget(self):
        january = datetime(2025, 1, 31)
        february = datetime(2025, 2, 3)

        def trade(symbol, date, opened, closed):
            start, end = date.replace(hour=opened), date.replace(hour=closed)
            position = PropPosition(symbol, start, end, 100, 106, 1, 1, 100, 1, 25, 600)
            return PositionEvent(start, position, True), PositionEvent(end, position, False)

        january_events = sorted((
            *trade('first', january, 7, 9), *trade('held', january, 8, 11),
            *trade('same_timestamp', january, 9, 10), *trade('second', january, 10, 12),
            *trade('over_budget', january, 13, 14),
        ), key=lambda event: event.time)
        days = ('2025-01-31', '2025-02-03')
        axis = LabAxis(days, (0, 1), 2)
        strategy = executable_pool([_strategy('set', 10, (0, 0))], set(), set())[0]
        strategy = replace(strategy, position_events={
            0: tuple(january_events), 1: trade('february', february, 9, 10),
        })
        program = profile('the5ers-growth')
        settings = {'capital': 5000.0, 'bankroll_eur': 104.0, 'evaluation_fee_eur': 52.0}
        series = growth_series([strategy], {'set': 1}, axis)
        campaign = simulate_monthly_campaign(series, days, HYPER_GROWTH_5K, 104, 52)
        self.assertEqual([funded.result.end_event for funded in campaign.attempts], [4, 8, 2])
        measured = evaluate([strategy], {'set': 1}, axis, program, 5000, 120, 104, 52)
        result = SearchResult({'set': 1}, measured, (strategy,))
        analysis = attempt_analysis(result, axis, program, settings)
        self.assertEqual(measured.passed, 3)
        self.assertEqual([row['started'] for row in analysis['monthly']], [2, 1])
        self.assertEqual([row['spent_eur'] for row in analysis['monthly']], [104, 52])
        self.assertEqual(analysis['calendar'][0]['status'], 'multiple')
        self.assertEqual([row['status'] for row in analysis['calendar'][0]['attempts']], ['passed', 'passed'])

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
