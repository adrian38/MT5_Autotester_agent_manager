from __future__ import annotations

import unittest
import math
from pathlib import Path

from lxml import html


class PortfolioFormTests(unittest.TestCase):
    def test_capital_accepts_any_numeric_value_like_the_original_ubs_form(self) -> None:
        page = html.fromstring(
            (Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.html").read_text(
                encoding="utf-8"
            )
        )

        capital = page.xpath('//input[@name="capital"]')[0]
        self.assertEqual(capital.get("type"), "number")
        self.assertEqual(capital.get("step"), "any")
        self.assertIsNone(capital.get("min"))

    def test_portfolio_configuration_is_saved_after_field_changes(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("form.addEventListener('change', scheduleSettingsSave)", script)
        self.assertIn("postManager('settings', payload)", script)
        self.assertIn("if (!form.checkValidity()) return", script)
        self.assertIn("if (!form.reportValidity()) return", script)

    def test_saved_bundle_members_can_be_excluded(self) -> None:
        script = (
            Path(__file__).parents[1] / "mt5_manager" / "static" / "portfolios.js"
        ).read_text(encoding="utf-8")

        self.assertIn("se borrará por completo el portafolio A/M/C", script)
        self.assertIn("onclick=\"excludeStrategy('detail',${index})\">Excluir</button>", script)
        self.assertNotIn("${isBundle ? '' : `<button type=\"button\" class=\"danger table-action\"", script)

    def test_explicit_save_actions_show_a_blocking_progress_overlay(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        page = html.fromstring((static_dir / "portfolios.html").read_text(encoding="utf-8"))
        script = (static_dir / "portfolios.js").read_text(encoding="utf-8")
        styles = (static_dir / "styles.css").read_text(encoding="utf-8")

        overlay = page.xpath('//*[@id="save-overlay"]')[0]
        self.assertEqual(overlay.get("role"), "dialog")
        self.assertIsNotNone(overlay.get("hidden"))
        self.assertIn("async function withSaveOverlay", script)
        self.assertIn("'Guardando configuración'", script)
        self.assertIn("'Guardando portafolio'", script)
        self.assertIn("guardado, pero no se pudo actualizar la vista", script)
        self.assertIn(".save-overlay{", styles)
        self.assertIn(".save-spinner{", styles)

    def test_every_html_number_input_accepts_representative_backend_values(self) -> None:
        static_dir = Path(__file__).parents[1] / "mt5_manager" / "static"
        fields = {}
        for path in static_dir.glob("*.html"):
            page = html.fromstring(path.read_text(encoding="utf-8"))
            for field in page.xpath('//input[@type="number"]'):
                key = field.get("name") or field.get("id")
                self.assertIsNotNone(key, f"Input numérico sin name/id en {path.name}")
                self.assertNotIn(key, fields, f"Input numérico duplicado: {key}")
                fields[key] = field

        valid_values = {
            "cycles": (1, 100),
            "generations": (1, 1000),
            "variants": (1, 10, 10000),
            "max-seeds": (0, 30, 100000),
            "max-workers": (1, 64),
            "generation-repair-attempts": (1, 20),
            "repair-attempts": (1, 20),
            "capital": (0.5, 5000, 10000.25),
            "valley_dd_pct": (0.5, 6, 6.05),
            "max_daily_dd": (0.5, 150, 150.25),
            "top_k_per_symbol": (1, 3, 20),
            "max_total_candidates": (1, 30, 100),
            "min_trades_2020_2026": (0, 15, 100),
            "min_strategy_recent_contribution_pct": (0, 5, 100),
            "max_units_per_set": (1, 30),
            "max_total_units": (1, 30),
            "max_units_per_symbol": (1, 30),
            "max_sets_per_symbol": (1, 3),
            "dd_reserve_pct": (0, 10, 99.5),
            "search_restarts": (0, 4),
            "max_margin_pct": (0.5, 100, 100.25),
            "max_pair_corr": (0, 0.35, 0.355, 1),
            "max_downside_corr": (0, 0.25, 0.255, 1),
            "max_dd_overlap": (0, 0.35, 0.355, 1),
            "max_portfolio_corr": (0, 0.5, 0.505, 1),
        }
        self.assertEqual(set(fields), set(valid_values), "Actualiza la auditoría para los inputs numéricos")

        for key, values in valid_values.items():
            for value in values:
                self.assertTrue(
                    self._html_number_accepts(fields[key], value),
                    f"{key} no acepta el valor válido {value}",
                )

    @staticmethod
    def _html_number_accepts(field, value: float) -> bool:
        minimum = float(field.get("min")) if field.get("min") is not None else -math.inf
        maximum = float(field.get("max")) if field.get("max") is not None else math.inf
        if not minimum <= value <= maximum:
            return False
        step = field.get("step") or "1"
        if step == "any":
            return True
        base = float(field.get("min") or field.get("value") or 0)
        quotient = (value - base) / float(step)
        return math.isclose(quotient, round(quotient), abs_tol=1e-9)


if __name__ == "__main__":
    unittest.main()
