"""Con qué lote operó la cuenta cada mes cuando lo decide el autolote.

Los números de referencia son los de la cuenta 77028839 de RoboForex, que
sirve al portafolio #173, medidos el 2026-10-24: saldo medio de 6.203,14 € en
octubre y 3.208,51 € en septiembre, y un Excel de autolote al 3 % de riesgo.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import (
    live_account_expectation, live_account_ingest, live_account_month_lots as month_lots,
    live_account_presentation,
)
from mt5_manager.live_account_archive import account_key
from tests.live_audit.test_live_account_cron import (
    NOW, FakeServer, FakeSource, node_state,
)

BALANCES = [
    ("2026-09-29", 3_100.0), ("2026-09-30", 3_317.02),
    ("2026-10-01", 5_366.63), ("2026-10-02", 7_039.65),
]
RULES = {
    "ROBOFOREX/ECN:1": {"dd_001": 104.0, "risk_pct": 3.0},
    "ROBOFOREX/ECN:2": {"dd_001": 20.0, "risk_pct": 3.0},
}
MEMBERS = [
    {"candidate_id": "ROBOFOREX/ECN:1", "set_name": "dailyK", "symbol": "EURUSD"},
    {"candidate_id": "ROBOFOREX/ECN:2", "set_name": "EURUSD_e", "symbol": "EURUSD"},
]


class MonthBalanceTests(unittest.TestCase):
    def test_the_month_is_the_mean_of_its_days(self) -> None:
        self.assertAlmostEqual(
            month_lots.month_balance(BALANCES, "2026-10"),
            (5_366.63 + 7_039.65) / 2,
        )

    def test_each_month_only_sees_its_own_days(self) -> None:
        self.assertAlmostEqual(
            month_lots.month_balance(BALANCES, "2026-09"), (3_100.0 + 3_317.02) / 2,
        )

    def test_a_month_outside_the_series_is_not_invented(self) -> None:
        """Cero es «no se sabe», no «cuenta vacía»: no se escala nada."""
        self.assertEqual(month_lots.month_balance(BALANCES, "2026-08"), 0.0)
        self.assertEqual(month_lots.month_balance([], "2026-10"), 0.0)

    def test_a_month_of_another_year_does_not_count(self) -> None:
        self.assertEqual(month_lots.month_balance(BALANCES, "2025-10"), 0.0)

    def test_step_lot_uses_the_balance_that_opened_the_month(self) -> None:
        self.assertEqual(
            month_lots.month_opening_balance(BALANCES, "2026-10"), 3_317.02,
        )


class MonthLotTests(unittest.TestCase):
    def test_the_lot_is_the_autolot_formula_over_the_mean_balance(self) -> None:
        # 6203,14 x 3 / 100 x 0,01 / 104 = 0,01789 -> 0,02 al paso de 0,01.
        measured = month_lots.month_lots(MEMBERS, RULES, "2026-10", 6_203.14)
        self.assertAlmostEqual(measured.lots["ROBOFOREX/ECN:1"], 0.02)
        # Y con DD_0.01 de 20 la misma cuenta pide 0,09.
        self.assertAlmostEqual(measured.lots["ROBOFOREX/ECN:2"], 0.09)

    def test_half_the_balance_is_half_the_lot(self) -> None:
        """Es lo que hace honesta la comparación del mes anterior."""
        september = month_lots.month_lots(MEMBERS, RULES, "2026-09", 3_208.51)
        self.assertAlmostEqual(september.lots["ROBOFOREX/ECN:2"], 0.05)

    def test_the_lot_never_drops_below_the_minimum_of_the_broker(self) -> None:
        """El caso GOOGL: 0,00846 bruto se ejecuta como 0,01.

        Y en AXI hay símbolos cuyo mínimo es mucho mayor, así que el redondeo
        es el mismo que usa el veredicto y no un `round` cualquiera.
        """
        measured = month_lots.month_lots(
            MEMBERS, RULES, "2026-10", 100.0, {"eurusd": (0.1, 0.1)},
        )
        self.assertEqual(measured.lots["ROBOFOREX/ECN:1"], 0.1)

    def test_a_strategy_without_a_rule_keeps_its_declared_lot_and_is_named(self) -> None:
        measured = month_lots.month_lots(
            MEMBERS, {"ROBOFOREX/ECN:1": RULES["ROBOFOREX/ECN:1"]},
            "2026-10", 6_203.14,
        )
        self.assertNotIn("ROBOFOREX/ECN:2", measured.lots)
        self.assertEqual(measured.without_rule, ("EURUSD_e",))

    def test_a_rule_without_numbers_does_not_produce_a_lot(self) -> None:
        measured = month_lots.month_lots(
            MEMBERS, {"ROBOFOREX/ECN:1": {"dd_001": 0.0, "risk_pct": 3.0}},
            "2026-10", 6_203.14,
        )
        self.assertEqual(measured.lots, {})

    def test_it_remembers_the_month_and_the_balance_it_used(self) -> None:
        measured = month_lots.month_lots(MEMBERS, RULES, "2026-10", 6_203.14)
        self.assertEqual(measured.label, "2026-10")
        self.assertAlmostEqual(measured.balance, 6_203.14)

    def test_balance_step_adds_point_zero_one_per_complete_step(self) -> None:
        measured = month_lots.step_month_lots(
            MEMBERS, {"ROBOFOREX/ECN:1": {"step": 80.0, "max_lots": 99.0}},
            "2026-10", 3_317.02,
        )
        self.assertAlmostEqual(measured.lots["ROBOFOREX/ECN:1"], 0.41)

    def test_only_risk_two_activates_the_step_from_the_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixed = root / "fixed.set"
            stepped = root / "stepped.set"
            fixed.write_text("Risk=0\nLotPerBalance_step=80\n", encoding="utf-8")
            stepped.write_text(
                "Risk=2||0||0||3||N\nLotPerBalance_step=80\nMaxLots=99\n",
                encoding="utf-8",
            )
            rules = month_lots.step_rules([
                {"candidate_id": "fixed", "set_path": str(fixed)},
                {"candidate_id": "stepped", "set_path": str(stepped)},
            ], Path)
        self.assertEqual(set(rules), {"stepped"})
        self.assertEqual(rules["stepped"]["step"], 80.0)

    def test_observed_real_step_restores_a_normalized_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            set_path = Path(temp) / "normalized.set"
            set_path.write_text(
                "Risk=0\nStartLots=0.01\nLotPerBalance_step=80\n", encoding="utf-8",
            )
            members = [{"candidate_id": "manual:1", "set_path": str(set_path)}]
            rules = month_lots.step_rules(members, Path, ["manual:1"])
        self.assertEqual(rules["manual:1"]["step"], 80.0)

    def test_first_real_lot_of_the_month_is_the_lotstep_evidence(self) -> None:
        detail = {"operation_comparisons": [
            {"strategy": "manual:1", "real": {
                "open_time": "2026-10-20T13:55:55+02:00", "volume": 0.13,
            }},
            {"strategy": "manual:1", "real": {
                "open_time": "2026-10-22T00:02:56+02:00", "volume": 0.14,
            }},
        ]}
        self.assertEqual(
            month_lots.opening_real_lots(detail, "2026-10"), {"manual:1": 0.13},
        )


class IngestScenarioTests(unittest.TestCase):
    """Cuándo la ingesta mide la promesa al lote del mes, y cuándo no.

    Las piezas de mentira son las del cron, como en las de la tarjeta: es el
    mismo servidor y el mismo estado de nodo.
    """

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = node_state()
        self.server = FakeServer(Path(self.folder.name), self.state)
        self.account = {"account_key": account_key("60082017", "Axi-US50-Live")}
        self.server.live_accounts.merge(
            "axi", self.account["account_key"], login="60082017",
            server="Axi-US50-Live", currency="EUR", movements=[],
            period_start="", period_end="2026-10-21",
            balances=[("2026-09-30", 3_208.51), ("2026-10-20", 6_203.14)],
        )
        self.saved = {"id": 105, "members": [
            {"candidate_id": "ROBOFOREX/ECN:1", "set_name": "dailyK", "symbol": "EURUSD"},
        ]}

    def profile_with(self, **changes: object) -> None:
        self.state["profiles"]["audit-105"] = {
            **self.state["profiles"]["audit-105"], **changes,
        }

    def scenarios(self, use=None) -> dict:
        return live_account_ingest._scenarios(
            self.server, FakeSource(), "axi", self.account,
            {"portfolio_id": 105, "audit_id": "audit-105", "portfolio_type": ""},
            self.saved, "", use or live_account_presentation.UseLots(), NOW,
        )

    def test_without_autolot_there_is_nothing_to_correct(self) -> None:
        """`real_strategy_lots` ya describe toda la vida de la cuenta."""
        self.assertEqual(self.scenarios(), {})

    def test_with_autolot_each_month_gets_the_lot_of_its_balance(self) -> None:
        self.profile_with(autolot={
            "enabled": True, "rules": {"dailyK": {"dd_001": 104.0, "risk_pct": 3.0}},
        })
        found = self.scenarios()
        self.assertEqual(sorted(found), ["2026-09", "2026-10"])
        self.assertEqual(found["2026-10"]["basis"], month_lots.LOT_BASIS)
        self.assertAlmostEqual(found["2026-10"]["balance"], 6_203.14)
        self.assertAlmostEqual(found["2026-10"]["lots"]["ROBOFOREX/ECN:1"], 0.02)
        self.assertAlmostEqual(found["2026-09"]["lots"]["ROBOFOREX/ECN:1"], 0.01)

    def test_risk_two_set_uses_each_months_opening_balance(self) -> None:
        set_path = Path(self.folder.name) / "dailyK.set"
        set_path.write_text(
            "Risk=2\nLotPerBalance_step=80\nMaxLots=99\n", encoding="utf-8",
        )
        self.saved["members"][0]["set_path"] = str(set_path)
        found = self.scenarios()
        self.assertEqual(found["2026-10"]["basis"], month_lots.STEP_LOT_BASIS)
        self.assertAlmostEqual(found["2026-10"]["balance"], 3_208.51)
        self.assertAlmostEqual(found["2026-10"]["lots"]["ROBOFOREX/ECN:1"], 0.4)

    def test_normalized_set_uses_the_first_real_lot_without_a_probe(self) -> None:
        set_path = Path(self.folder.name) / "dailyK.set"
        set_path.write_text(
            "Risk=0\nStartLots=0.01\nLotPerBalance_step=80\nMaxLots=99\n",
            encoding="utf-8",
        )
        self.saved["members"][0]["set_path"] = str(set_path)
        found = live_account_ingest._step_scenarios(
            FakeSource(), self.saved["members"], [], {"ROBOFOREX/ECN:1": 0.13}, NOW,
        )
        self.assertEqual(found["2026-10"]["basis"], month_lots.STEP_LOT_BASIS)
        self.assertEqual(found["2026-10"]["balance"], 0.0)
        self.assertAlmostEqual(found["2026-10"]["lots"]["ROBOFOREX/ECN:1"], 0.13)

    def test_with_the_tester_column_marked_nothing_is_rescaled(self) -> None:
        """Ahí los cierres ya están al lote del backtest: mover la promesa
        separaría las dos mitades que esa casilla existe para juntar."""
        self.profile_with(
            autolot={
                "enabled": True,
                "rules": {"dailyK": {"dd_001": 104.0, "risk_pct": 3.0}},
            },
            view_lot_overrides={"$mode": live_account_presentation.TESTER_LEADS},
        )
        self.assertEqual(self.scenarios(), {})

    def test_a_broken_autolot_leaves_the_promise_at_the_declared_lot(self) -> None:
        """Activo sin Excel no puede dejar a la cartera sin promesa.

        Se mide al lote declarado, como antes, y la tarjeta sigue diciendo que
        está a un lote fijo. El que tiene que gritar es el veredicto.
        """
        self.profile_with(autolot={"enabled": True, "rules": {}})
        self.assertEqual(self.scenarios(), {})

    def test_a_rule_that_names_nobody_is_not_a_scenario(self) -> None:
        self.profile_with(autolot={
            "enabled": True, "rules": {"otra": {"dd_001": 104.0, "risk_pct": 3.0}},
        })
        self.assertEqual(self.scenarios(), {})

    def test_a_month_without_balance_is_left_alone(self) -> None:
        self.profile_with(autolot={
            "enabled": True, "rules": {"dailyK": {"dd_001": 104.0, "risk_pct": 3.0}},
        })
        self.server.live_accounts.merge(
            "axi", self.account["account_key"], login="60082017",
            server="Axi-US50-Live", currency="EUR", movements=[],
            period_start="", period_end="2026-10-21",
            balances=[("2026-10-20", 6_203.14)],
        )
        self.assertEqual(sorted(self.scenarios()), ["2026-10"])

    def test_what_the_audit_saw_executed_beats_the_formula(self) -> None:
        """La fórmula estima; lo observado es un hecho y manda donde exista."""
        self.profile_with(autolot={
            "enabled": True, "rules": {"dailyK": {"dd_001": 104.0, "risk_pct": 3.0}},
        })
        found = self.scenarios(live_account_presentation.UseLots(
            members={"ROBOFOREX/ECN:1": 0.07},
        ))
        self.assertAlmostEqual(found["2026-10"]["lots"]["ROBOFOREX/ECN:1"], 0.07)

    def test_a_promise_from_last_month_is_measured_again(self) -> None:
        """El día 1 la tarjeta pide un mes que lo guardado no tiene."""
        stored = {"criterion": live_account_expectation.CRITERION_VERSION,
                  "presentation_criterion":
                      live_account_presentation.PRESENTATION_VERSION,
                  "updated_at": NOW.isoformat(),
                  "portfolio_created_at": "",
                  "months_at": {"2026-09": {"profit": 1.0}}}
        self.assertTrue(live_account_ingest._needs_rebuild(stored, NOW))
        stored["months_at"]["2026-10"] = {"profit": 2.0}
        self.assertFalse(live_account_ingest._needs_rebuild(stored, NOW))


if __name__ == "__main__":
    unittest.main()
