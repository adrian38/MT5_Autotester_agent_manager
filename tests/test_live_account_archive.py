"""La serie guardada de cada cuenta real.

Lo que se guarda son los movimientos y no las filas, porque la DD de un mes no
es el máximo de las de sus días. Y un sondeo manda sobre su periodo: así se
corrige el día que se leyó a medias y un día que se queda sin cierres baja a
cero en vez de conservar el número viejo para siempre.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_account_series, manager_account_routes
from mt5_manager.live_account_archive import (
    LiveAccountArchive, account_key, stored_movements,
)


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class KeyTests(unittest.TestCase):
    def test_the_account_is_its_login_on_its_server(self) -> None:
        self.assertEqual(account_key(" 60082017 ", "Axi-US50-Live"), "60082017@Axi-US50-Live")

    def test_the_same_login_on_two_servers_is_two_accounts(self) -> None:
        self.assertNotEqual(
            account_key("1", "Axi-US50-Live"), account_key("1", "Axi-US50-Demo"),
        )


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = LiveAccountArchive(self.folder.name)
        self.key = account_key("60082017", "Axi-US50-Live")

    def merge(self, movements, start, end, **extra):
        return self.archive.merge(
            "axi", self.key, login="60082017", server="Axi-US50-Live",
            currency="EUR", movements=movements, period_start=start,
            period_end=end, **extra,
        )

    def test_an_unknown_account_reads_as_empty_but_complete(self) -> None:
        stored = self.archive.read("axi", "nadie@ningun-servidor")
        self.assertEqual(stored["movements"], [])
        self.assertEqual(stored["last_day"], "")
        self.assertEqual(stored["account_key"], "nadie@ningun-servidor")

    def test_the_first_snapshot_writes_the_series_and_its_edges(self) -> None:
        record = self.merge(
            [(moment("2026-10-19T08:00:00"), 5.0), (moment("2026-10-20T09:00:00"), -2.0)],
            "", "2026-10-20",
        )
        self.assertEqual(record["first_day"], "2026-10-19")
        self.assertEqual(record["last_day"], "2026-10-20")
        self.assertEqual(record["currency"], "EUR")
        self.assertEqual(len(record["movements"]), 2)

    def test_a_snapshot_rules_over_its_period_and_keeps_the_rest(self) -> None:
        self.merge(
            [
                (moment("2026-10-10T08:00:00"), 100.0),
                (moment("2026-10-20T08:00:00"), 5.0),
            ],
            "", "2026-10-20",
        )
        # La relectura del 19 al 21 ve el 20 completo: ya son 9, no 5.
        record = self.merge([(moment("2026-10-20T08:00:00"), 9.0)], "2026-10-19", "2026-10-21")
        amounts = {row[0][:10]: row[1] for row in record["movements"]}
        self.assertAlmostEqual(amounts["2026-10-10"], 100.0)
        self.assertAlmostEqual(amounts["2026-10-20"], 9.0)
        self.assertEqual(len(record["movements"]), 2)

    def test_a_day_that_loses_its_trades_drops_to_zero(self) -> None:
        self.merge([(moment("2026-10-20T08:00:00"), 5.0)], "", "2026-10-20")
        record = self.merge([], "2026-10-20", "2026-10-21")
        self.assertEqual(record["movements"], [])
        self.assertEqual(record["last_day"], "")

    def test_a_full_life_snapshot_replaces_the_whole_series(self) -> None:
        self.merge([(moment("2026-09-01T08:00:00"), 1.0)], "", "2026-09-01")
        record = self.merge([(moment("2026-10-20T08:00:00"), 2.0)], "", "2026-10-20")
        self.assertEqual(len(record["movements"]), 1)
        self.assertEqual(record["first_day"], "2026-10-20")

    def test_the_series_survives_a_restart_and_feeds_the_rows(self) -> None:
        self.merge(
            [
                (moment("2026-10-20T08:00:00"), 10.0),
                (moment("2026-10-20T09:00:00"), -4.0),
                (moment("2026-10-21T08:00:00"), 1.0),
            ],
            "", "2026-10-21",
        )
        again = LiveAccountArchive(self.folder.name)
        ordered = [
            (datetime.fromisoformat(stamp), amount)
            for stamp, amount in stored_movements(again.read("axi", self.key))
        ]
        rows = live_account_series.daily_rows(ordered)
        self.assertAlmostEqual(rows["2026-10-20"]["profit"], 6.0)
        self.assertAlmostEqual(rows["2026-10-20"]["drawdown"], 4.0)
        self.assertAlmostEqual(
            live_account_series.monthly_rows(ordered)["2026-10"]["profit"], 7.0,
        )

    def test_the_snapshot_log_remembers_what_was_read_and_when(self) -> None:
        record = self.merge(
            [(moment("2026-10-20T08:00:00"), 5.0)], "2026-10-17", "2026-10-20",
            taken_at="2026-10-20T21:00:00+00:00",
        )
        self.assertEqual(record["snapshots"][-1], {
            "taken_at": "2026-10-20T21:00:00+00:00",
            "period_start": "2026-10-17", "period_end": "2026-10-20",
            "movements": 1,
        })
        self.assertEqual(record["updated_at"], "2026-10-20T21:00:00+00:00")

    def test_the_known_accounts_of_a_node_are_listed(self) -> None:
        self.merge([(moment("2026-10-20T08:00:00"), 5.0)], "", "2026-10-20")
        self.assertEqual(self.archive.accounts("axi"), [self.key])
        self.assertEqual(self.archive.accounts("roboforex"), [])

    def test_the_last_day_is_where_the_next_snapshot_starts(self) -> None:
        self.merge([(moment("2026-10-20T08:00:00"), 5.0)], "", "2026-10-20")
        self.assertEqual(self.archive.last_day("axi", self.key), "2026-10-20")

    def test_total_selection_is_shared_and_survives_a_restart(self) -> None:
        selection_key = f"axi::{self.key}"
        self.assertFalse(self.archive.total_selection()["initialized"])
        self.archive.update_total_selection({"account": selection_key, "included": False})
        again = LiveAccountArchive(self.folder.name)
        self.assertEqual(again.total_selection(), {
            "initialized": True, "excluded_accounts": [selection_key],
        })
        again.update_total_selection({"account": selection_key, "included": True})
        self.assertEqual(again.total_selection()["excluded_accounts"], [])

    def test_only_the_first_browser_migrates_its_local_selection(self) -> None:
        first = self.archive.update_total_selection({"excluded_accounts": ["axi::one"]})
        second = self.archive.update_total_selection({"excluded_accounts": ["axi::two"]})
        self.assertEqual(first, second)

    def test_the_http_route_persists_one_checkbox_change(self) -> None:
        response = {}
        handler = SimpleNamespace(
            server=SimpleNamespace(live_accounts=self.archive),
            _body=lambda: {"account": f"axi::{self.key}", "included": False},
            _send_json=lambda status, body: response.update(status=status, body=body),
        )
        handled = manager_account_routes.handle_post(
            handler, SimpleNamespace(path="/api/live-account-total-selection"), [],
        )
        self.assertTrue(handled)
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["excluded_accounts"], [f"axi::{self.key}"])

    def test_a_corrupt_row_does_not_take_the_series_down(self) -> None:
        record = self.merge([(moment("2026-10-20T08:00:00"), 5.0)], "", "2026-10-20")
        record["movements"].append(["2026-10-21T08:00:00+00:00"])
        record["movements"].append(["2026-10-22T08:00:00+00:00", "mucho"])
        self.assertEqual(len(stored_movements(record)), 1)


if __name__ == "__main__":
    unittest.main()
