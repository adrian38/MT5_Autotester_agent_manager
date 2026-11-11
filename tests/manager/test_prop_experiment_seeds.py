"""Las seeds se leen de su tabla propia sin escribir ni ampliar el veredicto."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from portfolio_manager.ubs_portfolio import PeriodReport
from mt5_manager.prop.prop_experiment_seeds import accepted_seed_rows, load_seed_sets


class _Source:
    broker = "TEST"

    def __init__(self, project: Path, memory: Path) -> None:
        self.project = project
        self.memory = memory

    @contextmanager
    def connect(self, *, write: bool):
        if write:
            raise AssertionError("Experimenta Prop nunca abre la memoria para escritura")
        connection = sqlite3.connect(self.memory)
        try:
            yield connection
        finally:
            connection.close()


class PropExperimentSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = self.root / "memory.sqlite"
        connection = sqlite3.connect(self.memory)
        try:
            connection.execute(
                "create table seed_scores (id integer, seed_path text, symbol text, "
                "period text, family text, report_path text, score real, metrics_json text, "
                "status text, active integer, accepted integer)"
            )
            connection.executemany(
                "insert into seed_scores values (?,?,?,?,?,?,?,?,?,?,?)", [
                    (1, "one.set", "EURUSD", "H1", "seed", "one.html", 9.0, "{}", "accepted", 1, 1),
                    (2, "two.set", "GBPUSD", "H1", "seed", "two.html", 10.0, "{}", "rejected", 1, 0),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        self.source = _Source(self.root, self.memory)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_only_active_accepted_seed_scores_enter_the_pool(self) -> None:
        rows = accepted_seed_rows(self.source)
        self.assertEqual([row["id"] for row in rows], [1])

    def test_the_seed_report_becomes_a_regular_curve_for_the_shared_aligner(self) -> None:
        (self.root / "one.set").write_text("set", encoding="utf-8")
        (self.root / "one.html").write_text("report", encoding="utf-8")
        period = PeriodReport(
            "seed", 2020, 2026, "EURUSD", "H1", [0.0, 10.0], 10.0,
            5.0, 4.0, 1.5, 2.0, 3,
        )
        report = SimpleNamespace(symbol="EURUSD", timeframe="H1")
        with mock.patch(
            "mt5_manager.prop.prop_experiment_seeds.period_report_from_strategy_report",
            return_value=period,
        ):
            loaded, warnings, total = load_seed_sets(
                self.source, parse=lambda _path: report, limit=10,
            )
        self.assertEqual(total, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(loaded[0].robustness_status, "seed_accepted")
        self.assertEqual(loaded[0].set_id, "seed-1-one")


if __name__ == "__main__":
    unittest.main()
