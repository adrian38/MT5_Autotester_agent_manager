"""La media de «ese mes» en el backtest del portafolio.

Dos cosas se comprueban aquí porque las dos se midieron rotas sobre datos
reales: que la curva son **dos** informes (IS y OOS) y no uno, y que cada
miembro se reconcilia con la contribución que el portafolio declara, porque lo
único con fecha son las operaciones emparejadas y algún cierre no se empareja.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_account_expectation as expectation


@dataclass
class Trade:
    close_time: datetime
    size: float
    profit_loss: float


class Report:
    def __init__(self, trades: list[Trade]) -> None:
        self.trades = trades


def trade(stamp: str, profit: float, size: float = 0.01) -> Trade:
    return Trade(datetime.fromisoformat(stamp), size, profit)


def member(**changes: object) -> dict[str, object]:
    row = {
        "candidate_id": "AXI/STANDARD:1437", "set_name": "XAUUSD_D1.set",
        "lot": 0.02, "net_profit_contribution": 0.0,
        "is_report_path": "/data/axi/reports/is.htm",
        "oos_report_path": "/data/axi/reports/oos.htm",
    }
    row.update(changes)
    return row


REPORTS = {
    "/data/axi/reports/is.htm": Report([
        trade("2020-10-05T10:00:00", 4.0), trade("2020-10-20T10:00:00", -1.0),
        trade("2021-10-05T10:00:00", 6.0),
    ]),
    "/data/axi/reports/oos.htm": Report([trade("2025-10-07T10:00:00", 10.0)]),
}


def read(path: str) -> Report:
    if path not in REPORTS:
        raise OSError(f"no existe {path}")
    return REPORTS[path]


class SourceTests(unittest.TestCase):
    def test_the_curve_is_the_is_report_plus_the_oos_one(self) -> None:
        self.assertEqual(
            expectation.member_paths(member(), {}),
            ["/data/axi/reports/is.htm", "/data/axi/reports/oos.htm"],
        )

    def test_a_manual_portfolio_takes_its_report_from_the_history(self) -> None:
        manual = expectation.manual_report_paths({
            "history": {"strategies": [
                {"set_id": "DaytradePro_CHFJPY.set",
                 "report_path": "/data/roboforex/reports/DaytradePro_CHFJPY.htm"},
            ]},
        })
        row = member(
            candidate_id="manual:2", set_name="DaytradePro_CHFJPY.set",
            is_report_path="", oos_report_path="",
        )
        self.assertEqual(
            expectation.member_paths(row, manual),
            ["/data/roboforex/reports/DaytradePro_CHFJPY.htm"],
        )

    def test_the_real_lot_of_the_profile_beats_the_saved_one(self) -> None:
        self.assertEqual(
            expectation.member_lot(member(), {"AXI/STANDARD:1437": 0.5}), (0.5, "real"),
        )
        self.assertEqual(expectation.member_lot(member(), {}), (0.02, "saved"))

    def test_the_trades_are_normalised_by_volume_and_taken_to_the_lot(self) -> None:
        values = expectation.scaled_trades(
            Report([trade("2026-10-05T10:00:00", 4.0, size=0.02)]), 0.04,
        )
        self.assertAlmostEqual(values[0][1], 8.0)


class ReconcileTests(unittest.TestCase):
    def test_the_member_is_scaled_up_to_what_the_portfolio_declares(self) -> None:
        values, factor = expectation.reconcile(
            [(datetime(2026, 10, 5), 90.0), (datetime(2026, 10, 6), 10.0)], 110.0,
        )
        self.assertAlmostEqual(factor, 1.1)
        self.assertAlmostEqual(sum(amount for _moment, amount in values), 110.0)

    def test_a_difference_too_big_is_another_report_and_nothing_is_scaled(self) -> None:
        values, factor = expectation.reconcile([(datetime(2026, 10, 5), 10.0)], 100.0)
        self.assertEqual(factor, 1.0)
        self.assertAlmostEqual(sum(amount for _moment, amount in values), 10.0)

    def test_without_a_declared_contribution_nothing_is_scaled(self) -> None:
        _values, factor = expectation.reconcile([(datetime(2026, 10, 5), 10.0)], 0.0)
        self.assertEqual(factor, 1.0)

    def test_an_opposite_sign_is_never_flipped(self) -> None:
        _values, factor = expectation.reconcile([(datetime(2026, 10, 5), -10.0)], 10.0)
        self.assertEqual(factor, 1.0)


class BuildTests(unittest.TestCase):
    def build(self, **changes: object) -> dict[str, object]:
        rows = changes.pop("members", [member()])
        return expectation.build(
            {"id": 105, "members": rows}, read=read, **changes,
        )

    def test_the_mean_of_october_averages_its_years(self) -> None:
        built = self.build()
        october = expectation.month_expectation(built, "2026-10")
        # 2020: (4-1)*2 = 6; 2021: 6*2 = 12; 2025: 10*2 = 20. Media 12,67.
        self.assertEqual(october["years"], [2020, 2021, 2025])
        self.assertAlmostEqual(october["profit"], (6.0 + 12.0 + 20.0) / 3)

    def test_the_mean_drawdown_of_october_is_the_mean_of_its_valleys(self) -> None:
        october = expectation.month_expectation(self.build(), "2026-10")
        # Sólo 2020 tiene caída: sube a 8 y baja a 6, son 2. Media 2/3.
        self.assertAlmostEqual(october["drawdown"], 2.0 / 3)

    def test_a_month_the_backtest_never_saw_has_no_mean(self) -> None:
        april = expectation.month_expectation(self.build(), "2026-04")
        self.assertIsNone(april["profit"])
        self.assertEqual(april["years"], [])

    def test_an_unreadable_report_leaves_the_member_out_and_says_so(self) -> None:
        built = self.build(members=[member(is_report_path="/data/axi/reports/no.htm")])
        self.assertEqual(built["members_read"], 0)
        self.assertEqual(built["members_total"], 1)
        self.assertIn("no se pudo leer", built["warnings"][0])

    def test_a_member_without_lot_is_not_measured(self) -> None:
        built = self.build(members=[member(lot=0)])
        self.assertEqual(built["members_read"], 0)
        self.assertIn("sin lote", built["warnings"][0])

    def test_an_excluded_member_does_not_count_in_the_promise(self) -> None:
        built = self.build(members=[member(), member(excluded=True)])
        self.assertEqual(built["members_total"], 1)

    def test_the_declared_total_is_reproduced_after_reconciling(self) -> None:
        built = self.build(members=[member(net_profit_contribution=40.0)])
        # Medido son 38 (19 por 0,01 x2); declarado 40: se escala y cuadra.
        self.assertAlmostEqual(built["measured_profit"], 40.0)
        self.assertAlmostEqual(built["declared_profit"], 40.0)

    def test_the_signature_only_changes_when_the_measurement_would(self) -> None:
        first = self.build()["signature"]
        self.assertEqual(first, self.build()["signature"])
        self.assertNotEqual(first, self.build(real_lots={"AXI/STANDARD:1437": 0.5})["signature"])
        self.assertNotEqual(first, self.build(members=[member(lot=0.04)])["signature"])

    def test_it_says_whether_it_measured_at_real_or_saved_lots(self) -> None:
        self.assertEqual(self.build()["lots"], "saved")
        self.assertEqual(
            self.build(real_lots={"AXI/STANDARD:1437": 0.5})["lots"], "real",
        )

    def test_the_resolver_translates_the_path_the_node_stored(self) -> None:
        seen: list[str] = []

        def resolve(value: str) -> str:
            seen.append(value)
            return value

        self.build(resolve=resolve)
        self.assertEqual(seen[:1], ["/data/axi/reports/is.htm"])


if __name__ == "__main__":
    unittest.main()
