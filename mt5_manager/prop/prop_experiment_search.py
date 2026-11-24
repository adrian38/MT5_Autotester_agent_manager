"""Búsqueda de composiciones con varios puntos de partida y cambios de lote."""

from __future__ import annotations

from itertools import chain, combinations
from math import floor, isfinite


def _lot_probes(maximum: int) -> tuple[int, ...]:
    probes = []
    units = 1
    while units < maximum:
        probes.append(units)
        units *= 2
    return (*probes, maximum) if maximum else ()


def daily_margin_usage(daily_margins, allocation):
    if not daily_margins:
        return ()
    days = len(next(iter(daily_margins.values())))
    used = [0.0] * days
    for key, units in allocation.items():
        for day, margin in enumerate(daily_margins[key]):
            used[day] += margin * units
    return tuple(used)


class CompositionSearch:
    def __init__(self, margins, capital, required, evaluate, progress, *, daily_margins=None):
        self.margins = margins
        self.capital = capital
        self.required = required
        self.evaluate = evaluate
        self.progress = progress
        self.scores = {}
        self.seed_leaders = ()
        self.stage = "Evaluando estrategias y lotes"
        self.daily_margins = daily_margins or {}
        self._margin_key = None
        self._daily_usage = ()
        self._peak_usage = 0.0

    def measure(self, allocation):
        key = tuple(sorted(allocation.items()))
        if key not in self.scores:
            self.scores[key] = self.evaluate(allocation)
            if self.progress and len(self.scores) % 250 == 0:
                self.progress(f"{self.stage} · {len(self.scores)} composiciones")
        return self.scores[key]

    def rank(self, allocation):
        return self.measure(allocation).score

    def margin(self, allocation):
        if self.daily_margins:
            self._margin_by_day(allocation)
            return self._peak_usage
        return sum(self.margins[key] * units for key, units in allocation.items())

    def _margin_by_day(self, allocation):
        key = tuple(allocation.items())
        if key != self._margin_key:
            self._daily_usage = daily_margin_usage(self.daily_margins, allocation)
            self._peak_usage = max(self._daily_usage)
            self._margin_key = key
        return self._daily_usage

    def fits(self, allocation):
        return self.margin(allocation) <= self.capital + 1e-9

    def capacity(self, key, others):
        margin = self.margins[key]
        if not isfinite(margin) or margin <= 0:
            return 0
        if self.daily_margins:
            used = self._margin_by_day(others)
            if self._peak_usage > self.capital + 1e-9:
                return 0
            maximum = min((
                (self.capital - existing + 1e-9) / required
                for existing, required in zip(used, self.daily_margins[key]) if required > 0
            ), default=0)
            return max(0, floor(maximum))
        return max(0, floor((self.capital - self.margin(others) + 1e-9) / margin))

    def best_single_lots(self, base):
        seeds = []
        for key in self.margins:
            others = {name: units for name, units in base.items() if name != key}
            probes = _lot_probes(self.capacity(key, others))
            if probes:
                best = max(probes, key=lambda units: self.rank({**others, key: units}))
                seeds.append((key, best))
        return sorted(seeds, key=lambda pair: self.rank({**base, pair[0]: pair[1]}), reverse=True)

    def seed_frontier(self, base, ranked):
        """One representative for each undominated approval count and duration."""
        leaders = []
        fastest = float('inf')
        for key, lots in ranked:
            result = self.measure({**base, key: lots})
            if result.margin_feasible and result.passed and result.median_days < fastest:
                leaders.append((key, lots))
                fastest = result.median_days
        return tuple(leaders or ranked[:1])

    def pair_seeds(self, base, leaders):
        for (left, left_best), (right, right_best) in combinations(leaders, 2):
            for a, b in sorted({(1, 1), (left_best, right_best), (left_best, 1), (1, right_best)}):
                proposal = {**base, left: a, right: b}
                if self.fits(proposal):
                    yield proposal

    def complementary_seeds(self, required, ranked, leaders):
        """Cruza cada base con todo el pool, aunque el socio rinda poco solo."""
        for anchor, anchor_best in leaders:
            for units in sorted({1, max(1, anchor_best // 2), anchor_best}):
                base = {**required, anchor: units}
                for partner, hint in ranked:
                    if partner == anchor:
                        continue
                    maximum = self.capacity(partner, base)
                    if maximum:
                        for lots in sorted({1, min(hint, maximum)}):
                            yield {**base, partner: lots}

    def lot_changes(self, current, ranked):
        suggested = dict(ranked)
        for key in dict.fromkeys((*current, *suggested)):
            old = current.get(key, 0)
            others = {name: units for name, units in current.items() if name != key}
            maximum = self.capacity(key, others)
            hint = suggested.get(key, 1)
            choices = {old - 1, old + 1, old // 2, old * 2, hint, min(hint, maximum), maximum}
            if old and key not in self.required and others:
                yield others
            for units in sorted(choices):
                if units != old and 0 < units <= maximum:
                    yield {**others, key: units}

    def replacements(self, current, ranked):
        for old in sorted(current.keys() - self.required):
            remaining = {key: units for key, units in current.items() if key != old}
            for key, hint in ranked:
                if key in current:
                    continue
                maximum = self.capacity(key, remaining)
                for units in sorted({1, min(hint, maximum), min(current[old], maximum)}):
                    if 0 < units <= maximum:
                        yield {**remaining, key: units}

    def add_with_rebalanced_lots(self, current, ranked):
        """Reserve margin while testing an addition, even when every single change loses."""
        bases = [current]
        for numerator in (3, 2):
            base = {key: max(1, lots * numerator // 4) for key, lots in current.items()}
            if base not in bases:
                bases.append(base)
        for base in bases:
            for key, hint in ranked:
                if key in base:
                    continue
                maximum = self.capacity(key, base)
                if maximum:
                    for lots in sorted({1, min(hint, maximum)}):
                        yield {**base, key: lots}

    def improve(self, seed, ranked):
        current = seed
        while True:
            best = max(chain(
                (current,), self.lot_changes(current, ranked), self.replacements(current, ranked),
            ), key=self.rank)
            if best == current:
                return current
            current = best

    def grow(self, best, ranked):
        """Give larger portfolios a start even if their parents scored below the winner."""
        self.stage = 'Búsqueda de composiciones de más estrategias'
        pair = max((key for key in self.scores if len(key) == 2), key=lambda key: self.scores[key].score,
                   default=None)
        frontier = best if len(best) >= 2 or pair is None else dict(pair)
        without_improvement = 0
        while without_improvement < 2:
            frontier = max(self.add_with_rebalanced_lots(frontier, ranked), key=self.rank, default=None)
            if frontier is None:
                break
            if self.rank(frontier) > self.rank(best):
                best = frontier
                without_improvement = 0
            else:
                without_improvement += 1
        return self.improve(best, ranked)

    def summary(self):
        by_size = {}
        for key, result in self.scores.items():
            count, feasible, best = by_size.get(len(key), (0, 0, None))
            if result.margin_feasible and (best is None or result.score > best.score):
                best = result
            by_size[len(key)] = count + 1, feasible + int(result.margin_feasible), best
        return tuple({
            'members': size, 'evaluated': count, 'feasible': feasible,
            'best_passed': best.passed if best else None,
            'best_median_days': best.median_days if best and best.passed else None,
            'best_pass_rate': best.pass_rate if best else None,
        } for size, (count, feasible, best) in sorted(by_size.items()))

    def run(self, incumbent=None):
        base = dict.fromkeys(sorted(self.required), 1)
        if not self.fits(base):
            raise ValueError("Las estrategias supervivientes exceden el margen de la cuenta prop")
        ranked = self.best_single_lots(base)
        self.seed_leaders = self.seed_frontier(base, ranked)
        self.stage = "Búsqueda de combinaciones"
        singles = [{**base, key: units} for key, units in self.seed_leaders]
        pairs = sorted(self.pair_seeds(base, self.seed_leaders), key=self.rank, reverse=True)
        starts = ([base] if base else []) + singles + pairs[:4]
        existing = {tuple(sorted(seed.items())) for seed in starts}
        complement = max(
            (seed for seed in self.complementary_seeds(base, ranked, self.seed_leaders)
             if tuple(sorted(seed.items())) not in existing),
            key=self.rank, default=None,
        )
        if complement is not None:
            starts.append(complement)
        if not starts:
            raise ValueError("Ninguna estrategia cabe en el margen de la cuenta prop")
        best = max(starts, key=self.rank)
        refined = set()
        for start in starts:
            signature = tuple(sorted(start.items()))
            if signature not in refined:
                candidate = self.improve(start, self.seed_leaders)
                refined.add(signature)
                if self.rank(candidate) > self.rank(best):
                    best = candidate
        best = self.grow(best, ranked)
        if (incumbent and self.required <= incumbent.keys() <= self.margins.keys()
                and all(units > 0 for units in incumbent.values()) and self.fits(incumbent)):
            retuned = self.improve(incumbent, ranked)
            if self.rank(retuned) > self.rank(best):
                best = retuned
        return best, self.measure(best)
