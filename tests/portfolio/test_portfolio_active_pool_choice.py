"""Elegir a mano que estrategias del pool en real se quedan fuera."""
from __future__ import annotations

import unittest
from pathlib import Path

from mt5_manager.portfolio_active_pool import active_strategy_rows
from mt5_manager.portfolio_improvement_common import improvement_inherited_overrides
from mt5_manager.portfolio_service import (
    filter_rows_by_active_portfolios,
    normalize_settings,
)


class ActivePoolChoiceTests(unittest.TestCase):
    """Elegir a mano qué estrategias del pool en real se quedan fuera."""

    MEMBERS = [
        {"set_path": r"F:\a\outputs\gold.set", "symbol": "XAUUSD", "timeframe": "H1",
         "is_report_path": "is.htm", "oos_report_path": "oos.htm",
         "_active_portfolio_id": 67, "_active_portfolio_name": "AXI-Select_Test",
         "variant_key": "aggressive", "variant_label": "Agresivo"},
        {"set_path": r"F:\a\runtime\manual\gold.set", "symbol": "XAUUSD", "timeframe": "H1",
         "_active_portfolio_id": 112, "_active_portfolio_name": "AXI_Select_2",
         "single_pass_report_path": "manual.htm"},
        {"set_path": r"F:\a\runtime\manual\a_mano.set", "symbol": "Tesla+", "timeframe": "M15",
         "_active_portfolio_id": 109, "_active_portfolio_name": "Christmas_Axi"},
    ]

    def test_one_row_per_strategy_with_all_its_live_uses(self) -> None:
        """La misma estrategia puede estar en varios usos —64 filas son 57 en
        AXI—; enseñarla repetida invitaría a quitarla de un sitio y dejarla en
        otro, que no es lo que ocurre."""
        rows = active_strategy_rows(self.MEMBERS)

        self.assertEqual([row["set_name"] for row in rows], ["a_mano.set", "gold.set"])
        gold = rows[1]
        self.assertEqual(
            [(origin["portfolio_id"], origin["portfolio_name"]) for origin in gold["origins"]],
            [(67, "AXI-Select_Test"), (112, "AXI_Select_2")],
        )
        self.assertEqual(gold["origins"][0]["variant_label"], "Agresivo")
        self.assertEqual(gold["symbol"], "XAUUSD")
        self.assertEqual(gold["timeframe"], "H1")

    def test_each_row_says_with_what_it_will_be_evaluated(self) -> None:
        rows = {row["set_name"]: row for row in active_strategy_rows(self.MEMBERS)}

        self.assertEqual(rows["gold.set"]["evidence"], "reports")
        self.assertEqual(rows["a_mano.set"]["evidence"], "none")
        self.assertEqual(
            active_strategy_rows([self.MEMBERS[1]])[0]["evidence"], "single_pass",
        )

    def test_the_excluded_ones_arrive_marked(self) -> None:
        """Se compara por nombre del `.set`, que es la identidad del filtro:
        la ruta del miembro manual no coincide jamás con la del candidato."""
        rows = {
            row["set_name"]: row
            for row in active_strategy_rows(self.MEMBERS, [r"C:\otro\GOLD.set"])
        }

        self.assertTrue(rows["gold.set"]["excluded"])
        self.assertFalse(rows["a_mano.set"]["excluded"])

    def test_the_filter_drops_what_the_dialog_left_out(self) -> None:
        warnings: list[str] = []
        rows = [
            {"set_path": r"F:\a\outputs\gold.set"},
            {"set_path": r"F:\a\outputs\plata.set"},
        ]
        inputs = {
            "active_portfolios_only": True,
            "active_portfolios_excluded": ["gold.set"],
            "_active_portfolio_members": [
                {"set_path": r"F:\a\runtime\manual\gold.set"},
                {"set_path": r"F:\a\outputs\plata.set"},
            ],
        }

        result = filter_rows_by_active_portfolios(rows, inputs, warnings)

        self.assertEqual(result, [rows[1]])
        self.assertIn("quitadas a mano", warnings[0])

    def test_the_setting_keeps_the_set_name_and_drops_duplicates(self) -> None:
        values = normalize_settings("full_history", {
            "active_portfolios_excluded": [
                r"F:\a\runtime\manual\gold.set", "  GOLD.set  ", "otra.set",
            ],
        })

        self.assertEqual(values["active_portfolios_excluded"], ["gold.set", "otra.set"])
        self.assertEqual(
            normalize_settings("monthly", {"active_portfolios_excluded": ["x.set"]})
            ["active_portfolios_excluded"],
            [],
        )

    def test_the_setting_refuses_anything_that_is_not_a_list_of_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "deben ser una lista"):
            normalize_settings("full_history", {"active_portfolios_excluded": "gold.set"})
        with self.assertRaisesRegex(ValueError, "debe ser texto"):
            normalize_settings("full_history", {"active_portfolios_excluded": [7]})

    def test_the_improvement_inherits_the_choice(self) -> None:
        overrides = improvement_inherited_overrides({
            "improvement_inherited_overrides": {"active_portfolios_excluded": ["gold.set"]},
        })

        self.assertEqual(overrides, {"active_portfolios_excluded": ["gold.set"]})

    def test_the_three_forms_offer_the_button_and_send_the_choice(self) -> None:
        static = Path(__file__).parents[2] / "mt5_manager" / "static"
        self.assertIn(
            'id="active-pool-open"', (static / "portfolios.html").read_text(encoding="utf-8"),
        )
        self.assertIn(
            'src="/active_pool.js"', (static / "portfolios.html").read_text(encoding="utf-8"),
        )
        for name in ("portfolio_improvement.js", "portfolio_repair.js"):
            script = (static / name).read_text(encoding="utf-8")
            with self.subTest(form=name):
                self.assertIn("data-active-pool-open", script)
                self.assertIn("openActivePoolDialog('full_history'", script)
                self.assertIn("active_portfolios_excluded: [...excludedStrategies]", script)
        generation = (static / "portfolios.js").read_text(encoding="utf-8")
        self.assertIn("openActivePoolDialog(scope, excludedActiveStrategies())", generation)
        self.assertIn(
            "payload.active_portfolios_excluded = excludedActiveStrategies()", generation,
        )


if __name__ == "__main__":
    unittest.main()

