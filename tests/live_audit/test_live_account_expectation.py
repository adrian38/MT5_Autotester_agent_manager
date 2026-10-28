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
    # Para el escenario por mes hace falta un backtest que vea los dos.
    "/data/axi/reports/dos-meses.htm": Report([
        trade("2025-09-10T10:00:00", 5.0), trade("2025-10-10T10:00:00", 7.0),
    ]),
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


class VariantTests(unittest.TestCase):
    """Un bundle guarda A/M/C en la misma fila: medirlas todas promete el triple."""

    BUNDLE = [
        member(variant_key="aggressive"),
        member(variant_key="balanced"),
        member(variant_key="conservative"),
    ]

    def test_only_the_audited_variant_is_measured(self) -> None:
        rows = expectation.audited_members({"members": self.BUNDLE}, "aggressive")
        self.assertEqual([row["variant_key"] for row in rows], ["aggressive"])

    def test_without_variants_the_whole_row_is_the_portfolio(self) -> None:
        """Una mejora guarda `variant_key` vacío: la variante es la fila entera."""
        rows = [member(), member(candidate_id="AXI/STANDARD:2")]
        self.assertEqual(
            expectation.audited_members({"members": rows}, "aggressive"), rows,
        )

    def test_the_bundle_promises_once_and_not_once_per_variant(self) -> None:
        whole = expectation.build({"id": 67, "members": self.BUNDLE}, read=read)
        audited = expectation.build(
            {"id": 67, "members": self.BUNDLE}, read=read, portfolio_type="aggressive",
        )
        self.assertEqual(whole["members_read"], 3)
        self.assertEqual(audited["members_read"], 1)
        self.assertAlmostEqual(
            expectation.month_expectation(whole, "2026-10")["profit"],
            expectation.month_expectation(audited, "2026-10")["profit"] * 3,
        )

    def test_the_signature_knows_which_variant_was_measured(self) -> None:
        portfolio = {"id": 67, "members": self.BUNDLE}
        self.assertNotEqual(
            expectation.signature(portfolio, {}),
            expectation.signature(portfolio, {}, "aggressive"),
        )


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

    def test_the_promise_keeps_the_global_valley_of_the_combined_curve(self) -> None:
        built = self.build()
        self.assertGreater(built["global_drawdown"], 0.0)
        october = expectation.month_expectation(built, "2026-10")
        self.assertAlmostEqual(october["global_drawdown"], built["global_drawdown"])

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

    def test_a_scenario_measures_the_same_backtest_at_another_lot(self) -> None:
        """Con autolote el lote lo decide el saldo, no `real_strategy_lots`.

        La media de siempre se queda —un mes sin escenario la necesita— y la
        del mes se mide aparte, al tamaño que la cuenta tuvo ese mes.
        """
        built = self.build(scenarios={
            "2026-10": {
                "lots": {"AXI/STANDARD:1437": 0.08}, "basis": "autolot_mean_balance",
                "balance": 6_203.14, "without_rule": [],
            },
        })
        plain = expectation.month_expectation({"months": built["months"]}, "2026-10")
        scaled = expectation.month_expectation(built, "2026-10")
        # El miembro se midió a 0,02 y el escenario pide 0,08: por cuatro.
        self.assertAlmostEqual(scaled["profit"], plain["profit"] * 4)
        self.assertAlmostEqual(scaled["drawdown"], plain["drawdown"] * 4)
        self.assertAlmostEqual(
            scaled["global_drawdown"], built["global_drawdown"] * 4,
        )
        self.assertEqual(scaled["years"], plain["years"])
        self.assertEqual(scaled["basis"], "autolot_mean_balance")
        self.assertAlmostEqual(scaled["balance"], 6_203.14)

    def test_a_month_without_a_scenario_still_reads_the_usual_mean(self) -> None:
        built = self.build(scenarios={
            "2026-10": {"lots": {"AXI/STANDARD:1437": 0.08}, "basis": "x"},
        })
        september = expectation.month_expectation(built, "2026-09")
        self.assertIsNone(september["profit"])
        self.assertEqual(expectation.month_expectation(built, "2026-10")["basis"], "x")

    def test_each_month_keeps_its_own_lot(self) -> None:
        """Septiembre no se mide al tamaño que la cuenta tiene hoy.

        Si lo hiciera, cada subida de saldo reescribiría lo bien que fue el
        mes pasado. Medido sobre 77028839: 6.203,14 € de media en octubre
        contra 3.208,51 € en septiembre, casi la mitad.
        """
        built = self.build(
            members=[member(
                is_report_path="/data/axi/reports/dos-meses.htm", oos_report_path="",
            )],
            scenarios={
                "2026-10": {"lots": {"AXI/STANDARD:1437": 0.08}},
                "2026-09": {"lots": {"AXI/STANDARD:1437": 0.04}},
            },
        )
        self.assertEqual(
            sorted(built["months_at"]), ["2026-09", "2026-10"],
        )
        self.assertEqual(built["months_at"]["2026-10"]["lots"], {"AXI/STANDARD:1437": 0.08})
        self.assertEqual(built["months_at"]["2026-09"]["lots"], {"AXI/STANDARD:1437": 0.04})

    def test_a_scenario_for_a_month_without_backtest_is_not_invented(self) -> None:
        built = self.build(scenarios={
            "2026-04": {"lots": {"AXI/STANDARD:1437": 0.08}},
        })
        self.assertEqual(built["months_at"], {})
        self.assertIsNone(expectation.month_expectation(built, "2026-04")["profit"])

    def test_the_signature_follows_the_lot_of_the_month(self) -> None:
        """Sin esto la media no se vuelve a medir cuando el saldo la mueve."""
        first = self.build(scenarios={
            "2026-10": {"lots": {"AXI/STANDARD:1437": 0.08}},
        })["signature"]
        self.assertNotEqual(first, self.build(scenarios={
            "2026-10": {"lots": {"AXI/STANDARD:1437": 0.09}},
        })["signature"])
        self.assertNotEqual(first, self.build()["signature"])

    def test_the_resolver_translates_the_path_the_node_stored(self) -> None:
        seen: list[str] = []

        def resolve(value: str) -> str:
            seen.append(value)
            return value

        self.build(resolve=resolve)
        self.assertEqual(seen[:1], ["/data/axi/reports/is.htm"])


if __name__ == "__main__":
    unittest.main()
