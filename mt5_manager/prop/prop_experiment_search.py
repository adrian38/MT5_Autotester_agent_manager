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


class CompositionSearch:
    def __init__(self, margins, capital, required, evaluate, progress):
        self.margins = margins
        self.capital = capital
        self.required = required
        self.evaluate = evaluate
        self.progress = progress
        self.scores = {}
        self.stage = "Evaluando estrategias y lotes"

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
        return sum(self.margins[key] * units for key, units in allocation.items())

    def fits(self, allocation):
        return self.margin(allocation) <= self.capital + 1e-9

    def capacity(self, key, others):
        margin = self.margins[key]
        if not isfinite(margin) or margin <= 0:
            return 0
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

    def pair_seeds(self, base, ranked):
        for (left, left_best), (right, right_best) in combinations(ranked[:24], 2):
            for a, b in sorted({(1, 1), (left_best, right_best), (left_best, 1), (1, right_best)}):
                proposal = {**base, left: a, right: b}
                if self.fits(proposal):
                    yield proposal

    def complementary_seeds(self, ranked):
        """Cruza las mejores bases con todo el pool, aunque el socio rinda poco solo."""
        for anchor, anchor_best in ranked[:2]:
            for units in sorted({1, max(1, anchor_best // 2)}):
                base = {anchor: units}
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

    def improve(self, seed, ranked):
        current = seed
        while True:
            best = max(chain(
                (current,), self.lot_changes(current, ranked), self.replacements(current, ranked),
            ), key=self.rank)
            if best == current:
                return current
            current = best

    def run(self):
        base = dict.fromkeys(sorted(self.required), 1)
        if not self.fits(base):
            raise ValueError("Las estrategias supervivientes exceden el margen de la cuenta prop")
        ranked = self.best_single_lots(base)
        self.stage = "Búsqueda de combinaciones"
        singles = [{**base, key: units} for key, units in ranked[:24]]
        pairs = sorted(self.pair_seeds(base, ranked), key=self.rank, reverse=True)
        starts = ([base] if base else []) + singles[:4] + pairs[:4]
        if not self.required:
            existing = {tuple(sorted(seed.items())) for seed in starts}
            complement = max(
                (seed for seed in self.complementary_seeds(ranked)
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
                candidate = self.improve(start, ranked[:24])
                refined.add(signature)
                if self.rank(candidate) > self.rank(best):
                    best = candidate
        return best, self.measure(best)
