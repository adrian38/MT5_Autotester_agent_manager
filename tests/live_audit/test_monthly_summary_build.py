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


def _remeasured_portfolio() -> dict[str, object]:
    """Un portafolio con el escenario de `MONTH` ya remedido a sus lotes."""
    rows = {
        f"{year}-09": {
            "profit": 100.0 + year - 2020, "drawdown": 10.0, "trades": 3,
        }
        for year in range(2020, 2026)
    }
    return {
        "created_at": "2026-01-01T00:00:00+00:00", "created_day": "2026-01-01",
        "expectation": {
            "months": {"09": {
                "profit": 25.0, "drawdown": 2.5, "years": sorted(range(2020, 2026)),
                "months": {
                    label: {key: value / 4 for key, value in row.items()}
                    for label, row in rows.items()
                },
            }},
            "months_at": {MONTH: {
                "profit": 102.5, "drawdown": 10.0, "years": sorted(range(2020, 2026)),
                "basis": "autolot_mean_balance", "lots": {"manual:1": 3.0},
                "months": rows,
                "monthly_mean": {"total": 72, "profit": 80.0, "drawdown": 9.0},
            }},
        },
    }


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
        self.assertFalse(measured["drawdown_band"]["measured"])

    def test_autolot_promises_at_the_month_lots_without_the_remeasured_series(self) -> None:
        # Un escenario viejo trae la media del mes y no sus años: la promesa ya
        # está al lote bueno y la serie con la que se sitúa sigue sin estarlo.
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

    def test_autolot_ranks_against_the_series_remeasured_at_the_month_lots(self) -> None:
        """Con los años del mes remedidos, la tarjeta de autolote compara igual.

        La objeción era el lote, no el autolote: una banda al mismo tamaño que
        el mes que juzga mide comportamiento, que es lo que se pedía.
        """
        measured = monthly_summary_build._performance(
            [(datetime(2026, 9, 2, tzinfo=timezone.utc), 5.0)],
            [_remeasured_portfolio()], MONTH, autolot=True,
        )
        self.assertTrue(measured["promise_comparable"])
        self.assertTrue(measured["table_comparable"])
        self.assertEqual(measured["profit_band"]["total"], 6)
        self.assertAlmostEqual(measured["profit_band"]["mean"], 102.5)
        self.assertEqual(measured["profit_band"]["verdict"], "below")
        self.assertTrue(measured["drawdown_band"]["measured"])
        self.assertEqual(measured["monthly_mean"]["profit"], 80.0)
        self.assertEqual(measured["months_total"], 72)

    def test_half_the_account_remeasured_is_not_an_account_remeasured(self) -> None:
        # Sumar el septiembre de uno al lote que operó con el del otro al lote
        # declarado daría una banda que no está a ningún lote.
        portfolios = [_remeasured_portfolio(), {
            "created_at": "2026-01-01T00:00:00+00:00", "created_day": "2026-01-01",
            "expectation": {"months": {"09": {
                "profit": 100.0, "drawdown": 10.0, "years": [2020],
                "months": {"2020-09": {"profit": 100.0, "drawdown": 10.0, "trades": 3}},
            }}},
        }]
        measured = monthly_summary_build._performance(
            [(datetime(2026, 9, 2, tzinfo=timezone.utc), 5.0)],
            portfolios, MONTH, autolot=True,
        )
        self.assertFalse(measured["table_comparable"])
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
