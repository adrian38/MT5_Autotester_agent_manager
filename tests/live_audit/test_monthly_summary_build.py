"""Lo que mide `monthly_summary_build` antes de que haya un aviso que leer.

Aqui vive el criterio del lado de los hechos —que tramo se mide, a que lotes se
puede comparar y que meses entran en la racha—, separado de
`test_monthly_summary`, que exige lo que esos hechos acaban diciendo. Son dos
duenos: uno arma la foto y el otro la narra, y mezclarlos hacia un fichero que
ya no cabia de un vistazo.

No monta un manager: `_performance` y `_streak` reciben movimientos, promesas y
series, asi que el criterio se puede exigir entero sin red ni ficheros.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import monthly_summary_build

MONTH = "2026-09"

class BuildCriterionTests(unittest.TestCase):
    def test_a_partial_month_only_measures_movements_after_birth(self) -> None:
        ordered = [
            (datetime(2026, 9, 1, tzinfo=timezone.utc), -100.0),
            (datetime(2026, 9, 18, 12, tzinfo=timezone.utc), 10.0),
            (datetime(2026, 9, 19, tzinfo=timezone.utc), -3.0),
        ]
        portfolios = [{
            "created_at": "2026-09-18T10:00:00+00:00",
            "created_day": "2026-09-18", "expectation": {},
        }]
        measured = monthly_summary_build._performance(ordered, portfolios, MONTH)
        self.assertEqual(measured["closed"], {
            "profit": 7.0, "drawdown": 3.0, "trades": 2,
        })
        self.assertTrue(measured["started_inside"])
        self.assertIsNone(measured["below"])

    def test_autolot_without_a_remeasured_month_has_no_promise_to_compare(self) -> None:
        # Sin escenario, la media de siempre está al lote declarado: compararla
        # con lo que corrió el autolote mide el volumen, no el comportamiento.
        portfolios = [{
            "created_at": "2026-01-01T00:00:00+00:00", "created_day": "2026-01-01",
            "expectation": {"months": {"10": {
                "profit": 100.0, "drawdown": 10.0, "years": [2020],
                "months": {"2020-10": {"profit": 100.0, "drawdown": 10.0, "trades": 3}},
            }}},
        }]
        measured = monthly_summary_build._performance(
            [(datetime(2026, 10, 2, tzinfo=timezone.utc), 5.0)], portfolios, MONTH,
            autolot=True,
        )
        self.assertFalse(measured["promise_comparable"])
        self.assertFalse(measured["table_comparable"])
        self.assertIsNone(measured["below"])
        self.assertFalse(measured["profit_band"]["measured"])
        self.assertFalse(measured["drawdown_standing"]["measured"])

    def test_autolot_promises_at_the_month_lots_but_never_ranks(self) -> None:
        # El escenario remide **sólo la fila de su mes**, así que la promesa sí
        # está al lote bueno y la serie de meses sigue sin estarlo.
        portfolios = [{
            "created_at": "2026-01-01T00:00:00+00:00", "created_day": "2026-01-01",
            "expectation": {
                "months": {"10": {
                    "profit": 100.0, "drawdown": 10.0, "years": [2020],
                    "months": {"2020-10": {"profit": 100.0, "drawdown": 10.0, "trades": 3}},
                }},
                "months_at": {MONTH: {
                    "profit": 300.0, "drawdown": 30.0, "years": [2020],
                    "basis": "autolot_mean_balance", "lots": {"manual:1": 3.0},
                }},
            },
        }]
        measured = monthly_summary_build._performance(
            [(datetime(2026, 10, 2, tzinfo=timezone.utc), 5.0)], portfolios, MONTH,
            autolot=True,
        )
        self.assertTrue(measured["promise_comparable"])
        self.assertFalse(measured["table_comparable"])
        self.assertEqual(measured["expected"]["profit"], 300.0)
        self.assertTrue(measured["below"])
        self.assertFalse(measured["profit_band"]["measured"])

    def test_a_streak_skips_months_promised_at_another_lot(self) -> None:
        # Un mes cuya promesa no está al lote que se corrió no puede entrar en
        # la racha ni romperla: nadie sabe de qué lado habría caído.
        expectation = {"months": {
            "09": {"profit": 100.0, "drawdown": 10.0, "years": [2020]},
            "10": {"profit": 100.0, "drawdown": 10.0, "years": [2020]},
        }}
        series = {"2026-10": {"profit": 0.0}, "2026-09": {"profit": 0.0}}
        portfolios = [{"created_day": "2026-01-01", "expectation": expectation}]
        self.assertEqual(
            monthly_summary_build._streak(series, portfolios, "2026-10")["months"], 2,
        )
        self.assertEqual(
            monthly_summary_build._streak(
                series, portfolios, "2026-10", autolot=True,
            )["months"],
            0,
        )

    def test_a_streak_never_uses_months_before_the_portfolio_existed(self) -> None:
        expectation = {"months": {
            "09": {"profit": 100.0, "drawdown": 10.0, "years": [2020]},
            "10": {"profit": 100.0, "drawdown": 10.0, "years": [2020]},
        }}
        measured = monthly_summary_build._streak(
            {
                "2026-10": {"profit": 0.0},
                "2026-09": {"profit": 0.0},
            },
            [{"created_day": "2026-09-30", "expectation": expectation}],
            "2026-10",
        )
        self.assertEqual(measured["months"], 1)


if __name__ == "__main__":
    unittest.main()
