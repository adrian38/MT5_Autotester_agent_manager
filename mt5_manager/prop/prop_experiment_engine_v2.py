"""Prop search with a bounded frontier and complementary OHLC strategies."""

from __future__ import annotations

from functools import lru_cache, partial
from heapq import nlargest, nsmallest
from itertools import chain
from random import Random

from .prop_experiment_engine import (
    _merge_position_events, _scale_events, evaluate, growth_series, search,
)
from .prop_experiment_search import CompositionSearch
from .the5ers_hyper_growth_engine import GrowthDay


class _PositionDays:
    def __init__(self, days, position_maps):
        self.days = days
        self.position_maps = position_maps
        self.observations = {}

    def __len__(self):
        return len(self.days)

    def __getitem__(self, index):
        if index not in self.observations:
            day = self.days[index]
            events = [rows[index] for rows in self.position_maps if index in rows]
            self.observations[index] = GrowthDay(0.0, date=day, positions=_merge_position_events(events))
        return self.observations[index]


def _growth_builder(strategies):
    events = {item.lab.key: item.position_events for item in strategies}

    @lru_cache(maxsize=128)
    def sized(key, units):
        return {day: tuple(_scale_events(rows, units)) for day, rows in events[key].items()}

    def build(selected, allocations, axis):
        keys = sorted(item.lab.key for item in selected if allocations.get(item.lab.key))
        if not keys or any(events[key] is None for key in keys):
            return growth_series(selected, allocations, axis)
        return _PositionDays(axis.days, [sized(key, allocations[key]) for key in keys])

    return build


class AdaptiveCompositionSearch(CompositionSearch):
    WIDTH = 6
    MIN_ROUNDS = 6
    ROUNDS = 12
    PATIENCE = 2
    # Restarts: about ten minutes on the real 3,000-strategy pool, where they took 46-52 approvals to 54-55.
    RESTART_BUDGET = 30000
    RESTART_KICKS = 300
    RESTART_IDLE = 25
    RESTART_SEED = 1

    def __init__(self, *args, series, symbols, **kwargs):
        super().__init__(*args, **kwargs)
        self.series = series
        self.symbols = symbols

    def _anchors(self, ranked, pareto):
        by_symbol = {}
        for key, units in ranked:
            by_symbol.setdefault(self.symbols[key], (key, units))
        return tuple(dict.fromkeys(chain(pareto, by_symbol.values(), ranked)))[:self.WIDTH]

    def _partners(self, current, ranked):
        available = [(key, hint) for key, hint in ranked if key not in current]
        if len(available) <= 24:
            return available
        daily = list(map(sum, zip(*(tuple(value * units for value in self.series[key])
                                    for key, units in current.items()))))
        weak_days = nsmallest(36, range(len(daily)), key=daily.__getitem__)

        def benefit(pair):
            key, units = pair
            values = self.series[key]
            # Scale the closed PnL to the standalone lot before comparing strategies.
            return sum(values[day] for day in weak_days) * units

        return list(dict.fromkeys(chain(available[:9], nlargest(7, available, key=benefit))))

    def _additions(self, current, partners):
        lighter = {key: max(1, units // 2) for key, units in current.items()}
        bases = (current,) if lighter == current else (current, lighter)
        for base in bases:
            for key, hint in partners:
                maximum = self.capacity(key, base)
                if maximum:
                    chosen = {min(hint, maximum), max(1, maximum // 2), maximum}
                    if base is current:
                        chosen.add(1)
                    for units in sorted(chosen):
                        yield {**base, key: units}

    def _refine_lots(self, current):
        yield from self.lot_changes(current, ())
        for key, old in current.items():
            others = {name: units for name, units in current.items() if name != key}
            maximum = self.capacity(key, others)
            for units in sorted({old * 3 // 4, old * 5 // 4, (old + maximum) // 2}):
                if 0 < units <= maximum and units != old:
                    yield {**others, key: units}

    def _transfer_lots(self, current, *, fine=False):
        for donor, old in current.items():
            if old <= 1:
                continue
            reductions = {old - 1, max(1, old // 2)} if fine else {max(1, old * 3 // 4)}
            for reduced_lots in sorted(reductions):
                reduced = {**current, donor: reduced_lots}
                for key, units in current.items():
                    if key == donor:
                        continue
                    others = {name: lots for name, lots in reduced.items() if name != key}
                    maximum = self.capacity(key, others)
                    for raised in sorted({units + 1, maximum}):
                        if units < raised <= maximum:
                            yield {**others, key: raised}

    def _neighbors(self, current, ranked):
        partners = self._partners(current, ranked)
        return chain(self._refine_lots(current), self._transfer_lots(current),
                     self._additions(current, partners), self.replacements(current, partners[:5]))

    def _leader(self):
        return max(result.score for result in self.scores.values())

    def _speed_rank(self, proposal, weight):
        result = self.measure(proposal)
        days = result.median_days if result.passed else float('inf')
        return result.margin_feasible, result.passed - weight * days, result.score

    def _frontier(self, proposals, expanded):
        best_by_members = {}
        for proposal in proposals:
            signature = tuple(sorted(proposal.items()))
            if signature in expanded:
                continue
            members = frozenset(proposal)
            previous = best_by_members.get(members)
            if previous is None or self.rank(proposal) > self.rank(previous):
                best_by_members[members] = proposal
        ranked = nlargest(self.WIDTH, best_by_members.values(), key=self.rank)
        selected = ranked[:self.WIDTH - 2]
        for weight in (0.5, 1.0):
            alternate = max((item for item in best_by_members.values() if item not in selected),
                            key=lambda item: self._speed_rank(item, weight), default=None)
            if alternate is not None:
                selected.append(alternate)
        return selected

    def _polish(self):
        feasible = [dict(key) for key, result in self.scores.items() if result.margin_feasible]
        if not feasible:
            return
        fastest = max(feasible, key=lambda item: self._speed_rank(item, 0.5))
        best = max(feasible, key=self.rank)
        for current in (best,) if fastest == best else (best, fastest):
            for _ in range(2):
                neighbors = chain((current,), self._refine_lots(current), self._transfer_lots(current, fine=True))
                revised = max(neighbors, key=self.rank)
                if revised == current:
                    break
                current = revised

    def _climb(self, current, ranked, limit):
        while len(self.scores) < limit:
            best = max(chain((current,), self._neighbors(current, ranked)), key=self.rank)
            if best == current:
                break
            current = best
        return current

    def _kick(self, current, rng):
        removable = sorted(current.keys() - self.required)
        if len(removable) < 2:
            return current
        removed = set(rng.sample(removable, min(len(removable) - 1, rng.choice((2, 3)))))
        return {key: units for key, units in current.items() if key not in removed}

    def _restart(self, best, ranked):
        """Drop two or three members of the walk's composition and climb again from what is left.

        Everything climbed is kept in self.scores, so the caller still returns the best measured.
        """
        rng = Random(self.RESTART_SEED)
        limit = len(self.scores) + self.RESTART_BUDGET
        self.stage = 'Reinicios V2'
        current, idle = best, 0
        for kick in range(self.RESTART_KICKS):
            if len(self.scores) >= limit or idle >= self.RESTART_IDLE:
                break
            if self.progress and kick % 10 == 0:
                self.progress(f'{self.stage} · {kick} de hasta {self.RESTART_KICKS}')
            measured = len(self.scores)
            candidate = self._climb(self._kick(current, rng), ranked, limit)
            # Kicks that measure nothing new only revisit known compositions: a run of them ends the phase.
            idle = idle + 1 if len(self.scores) == measured else 0
            # Equal approvals are accepted so the walk can drift along the plateau to a new neighbourhood.
            if self.measure(candidate).passed >= self.measure(current).passed:
                current = candidate

    def run(self, incumbent=None):
        # V1 is the floor: its walk shares self.scores, so V2 never returns less.
        baseline, _ = super().run(incumbent)
        base = dict.fromkeys(sorted(self.required), 1)
        ranked = self.best_single_lots(base)
        self.seed_leaders = self._anchors(ranked, self.seed_frontier(base, ranked))
        starts = [baseline, *({**base, key: units} for key, units in self.seed_leaders)]
        if base:
            starts.append(base)
        # V1 only measures an incumbent that still fits and keeps its required members.
        if incumbent and incumbent != baseline and tuple(sorted(incumbent.items())) in self.scores:
            starts.insert(1, incumbent)
        for start in starts:
            self.measure(start)
        self.stage = 'Búsqueda V2 de combinaciones y lotes'
        frontier, expanded, stale = starts, set(), 0
        leader = self._leader()
        for depth in range(self.ROUNDS):
            if not frontier or (depth >= self.MIN_ROUNDS and stale >= self.PATIENCE):
                break
            if self.progress:
                self.progress(f'{self.stage} · ronda {depth + 1} · hasta {self.ROUNDS}')
            expanded.update(tuple(sorted(item.items())) for item in frontier)
            proposals = chain.from_iterable(self._neighbors(item, ranked) for item in frontier)
            frontier = self._frontier(proposals, expanded)
            previous, leader = leader, self._leader()
            # Each round adds at most one member, so large portfolios need the rounds that keep paying.
            stale = 0 if leader > previous else stale + 1
        self._polish()
        self._restart(dict(max(self.scores, key=lambda signature: self.scores[signature].score)), ranked)
        best = max(self.scores, key=lambda signature: self.scores[signature].score)
        return dict(best), self.scores[best]


def search_v2(strategies, axis, profile, settings, progress=None, required_keys=None, *, incumbent=None):
    factory = partial(
        AdaptiveCompositionSearch,
        series={item.lab.key: item.series for item in strategies},
        symbols={item.lab.key: item.lab.symbol_key for item in strategies},
    )
    evaluator = partial(evaluate, build_growth_series=_growth_builder(strategies))
    return search(strategies, axis, profile, settings, progress, required_keys,
                  incumbent=incumbent, optimizer_factory=factory, evaluator=evaluator)
