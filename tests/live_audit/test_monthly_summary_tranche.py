"""El objetivo de un tramo: lo que la tarjeta dice de un mes a medio nacer.

Vive aparte de `test_monthly_summary` porque es un comportamiento con dueño
propio —`_tranche` en `monthly_summary_build` y la frase que lo cuenta—, y
porque aquél ya no cabía de un vistazo.

Lo que se exige aquí es que el objetivo sea **el mismo** que el del resumen del
portafolio (`live_account_monthly.add_month_expectations`): la media del mes
prorrateada por los días vividos para el beneficio, y la del mes entero para el
drawdown, que no se reparte por días. Dos pantallas con dos objetivos del mismo
tramo es lo único que no puede pasar.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager.monthly_summary import monthly_summary_notices as notices

from .test_monthly_summary import facts


def _partial(**changes: object) -> dict:
    """Hechos de un portafolio nacido el 18, con el tramo entero auditado."""
    return facts(
        started_inside=True,
        coverage={
            "pct": 40.0, "eligible_pct": 100.0, "audits": 1,
            "missing_days": 18.0, "eligible_missing_days": 0.0,
            "eligible_start": "2026-09-18",
        },
        **changes,
    )


class TrancheNoticeTests(unittest.TestCase):
    def test_the_tranche_is_measured_against_the_objective_of_the_other_screen(self) -> None:
        found = notices.notices(_partial(tranche={
            "measured": True, "days": 13, "month_days": 30, "fraction": 13 / 30,
            "average": 80.0, "profit": 34.67, "drawdown": 60.0,
            "meets_profit": True, "meets_drawdown": True,
        }))
        self.assertEqual(notices.verdict(found), notices.WATCH)
        message = str(found[1]["message"])
        self.assertIn("Objetivo del tramo: 34,67 EUR", message)
        self.assertIn("13 de 30 días", message)
        self.assertIn("lo alcanza", message)
        self.assertIn("dentro", message)
        self.assertIn("pide un mes natural completo", message)

    def test_a_tranche_that_falls_short_says_so_without_deciding(self) -> None:
        """Decir que no llega no es decidir: el veredicto sigue siendo VIGILAR.

        Un tramo no entra en la racha ni retira una cartera; lo que gana con
        esto es dejar de parecer un número sin medir.
        """
        found = notices.notices(_partial(tranche={
            "measured": True, "days": 13, "month_days": 30, "fraction": 13 / 30,
            "average": 80.0, "profit": 34.67, "drawdown": 20.0,
            "meets_profit": False, "meets_drawdown": False,
        }))
        self.assertEqual(notices.verdict(found), notices.WATCH)
        message = str(found[1]["message"])
        self.assertIn("no lo alcanza", message)
        self.assertIn("por encima", message)

    def test_without_an_objective_the_tranche_says_why(self) -> None:
        # Con autolote sin remedir, prorratear la media daría un objetivo de
        # volumen; entonces se dice el motivo y no se inventa el número.
        found = notices.notices(_partial(tranche={"measured": False}))
        self.assertIn("no hay promesa a su tamaño", str(found[1]["message"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
