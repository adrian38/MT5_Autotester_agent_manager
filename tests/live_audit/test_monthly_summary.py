"""El criterio del resumen mensual, sin montar un manager.

`monthly_summary_notices`, `monthly_summary_evidence` y
`monthly_summary_coverage` no tocan red, ni ficheros, ni `server`: entran
hechos ya medidos y salen avisos. Eso es lo que permite exigirles aquí lo que
de verdad decide —la racha, el drawdown inédito y que la falta de cobertura
tape a todo lo demás— en vez de comprobar que el panel pinta.

Lo que se vigila, y por qué cada cosa:

- Un mes sin promesa **corta** la racha. Una racha con un agujero no es una
  racha, y presentarla como una sola justificaría un cambio con menos
  evidencia de la que dice tener.
- SIN CONCLUSIÓN gana a CAMBIAR. Con medio mes sin auditar, un «cambia esta
  cartera» está sacado de la mitad de los datos.
- Los periodos de auditoría se **unen**, no se suman: se encadenan repitiendo
  el último día, así que sumarlos inflaría la cobertura justo en el solape.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import monthly_summary_coverage as coverage
from mt5_manager import monthly_summary_evidence as evidence
from mt5_manager import monthly_summary_notices as notices
from mt5_manager.monthly_summary_schedule import (
    DEFAULT_MONTHLY_SUMMARY_SETTINGS, month_label, month_name, next_occurrence,
    normalize_monthly_summary_settings, previous_occurrence, shift_month,
    target_month,
)

MONTH = "2026-09"


def months(values: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
    """La tabla del backtest como la deja `evidence.backtest_months`."""
    return {
        label: {"profit": profit, "drawdown": drawdown, "trades": 10}
        for label, (profit, drawdown) in values.items()
    }


def table(profits: list[float], drawdowns: list[float] | None = None) -> dict:
    depths = drawdowns or [100.0] * len(profits)
    return months({
        shift_month("2020-01", step): (profit, depths[step])
        for step, profit in enumerate(profits)
    })


def facts(**changes: object) -> dict:
    base = {
        "currency": "EUR", "month": MONTH,
        "closed": {"profit": 100.0, "drawdown": 50.0, "trades": 12},
        "expected": {"profit": 80.0, "drawdown": 60.0, "years": [2020], "portfolios": 1},
        "below": False, "streak": 0, "streak_required": 3,
        "profit_standing": {"overall": evidence.rank(100.0, [10.0, 20.0, 30.0])},
        "drawdown_standing": {"verdict": "normal", "measured": True, "worst": 90.0},
        "backtest_months": 72, "started_inside": False,
        "coverage": {"pct": 100.0, "audits": 2, "missing_days": 0.0, "month_days": 30},
        "series": {"never_read": False, "stale": False, "updated_at": "2026-10-02T00:00:00+00:00"},
        "degraded": [], "crossing": [], "slow": [],
    }
    base.update(changes)
    return base


class ScheduleTests(unittest.TestCase):
    def test_it_comes_armed_from_the_factory(self) -> None:
        # Al revés que los otros dos crones: éste no lanza nada, sólo lee el
        # disco del manager, y un resumen que hay que acordarse de encender no
        # sirve de aviso.
        self.assertTrue(DEFAULT_MONTHLY_SUMMARY_SETTINGS["enabled"])
        self.assertEqual(DEFAULT_MONTHLY_SUMMARY_SETTINGS["day"], 1)

    def test_the_day_never_leaves_february_without_an_appointment(self) -> None:
        with self.assertRaises(ValueError):
            normalize_monthly_summary_settings({"day": 31})
        self.assertEqual(normalize_monthly_summary_settings({"day": 28})["day"], 28)

    def test_an_unknown_field_is_refused_instead_of_ignored(self) -> None:
        with self.assertRaises(ValueError):
            normalize_monthly_summary_settings({"dai": 3})

    def test_the_appointment_resumes_the_month_that_just_closed(self) -> None:
        settings = normalize_monthly_summary_settings({})
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.assertEqual(month_label(now), "2026-10")
        self.assertEqual(target_month(previous_occurrence(now, settings)), "2026-09")
        self.assertEqual(target_month(next_occurrence(now, settings)), "2026-10")

    def test_the_month_is_named_in_words(self) -> None:
        self.assertEqual(month_name("2026-09"), "septiembre de 2026")


class EvidenceTests(unittest.TestCase):
    def test_a_month_without_a_promise_is_neither_below_nor_above(self) -> None:
        self.assertIsNone(evidence.below_promise(100.0, None))
        self.assertIsNone(evidence.below_promise(None, 100.0))
        self.assertTrue(evidence.below_promise(10.0, 100.0))
        self.assertFalse(evidence.below_promise(100.0, 10.0))

    def test_a_month_without_a_promise_cuts_the_streak(self) -> None:
        self.assertEqual(evidence.streak([True, True, None, True]), 2)
        self.assertEqual(evidence.streak([True, True, False, True]), 2)
        self.assertEqual(evidence.streak([None, True, True]), 0)
        self.assertEqual(evidence.streak([True, True, True]), 3)

    def test_the_standing_counts_months_instead_of_assuming_a_normal(self) -> None:
        standing = evidence.rank(15.0, [10.0, 12.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual((standing["worse"], standing["total"]), (2, 6))
        self.assertTrue(standing["measured"])

    def test_four_months_of_backtest_do_not_situate_anything(self) -> None:
        # Por debajo de `MINIMUM_MONTHS` no se sitúa: el recuento saldría igual
        # de redondo y no mediría nada.
        standing = evidence.rank(15.0, [10.0, 20.0, 30.0, 40.0])
        self.assertFalse(standing["measured"])
        self.assertEqual((standing["worse"], standing["total"]), (0, 4))
        self.assertIn(
            "no tiene meses suficientes", notices.worse_than(standing, "meses"),
        )

    def test_a_drawdown_deeper_than_the_worst_month_is_unheard_of(self) -> None:
        depths = [float(10 * step) for step in range(1, 21)]
        standing = evidence.drawdown_standing(500.0, table([1.0] * 20, depths))
        self.assertEqual(standing["verdict"], "beyond")
        self.assertEqual(standing["worst"], 200.0)
        self.assertEqual(
            evidence.drawdown_standing(20.0, table([1.0] * 20, depths))["verdict"],
            "normal",
        )

    def test_without_enough_backtest_nothing_is_declared(self) -> None:
        standing = evidence.drawdown_standing(500.0, table([1.0], [10.0]))
        self.assertFalse(standing["measured"])
        self.assertEqual(standing["verdict"], "")


class CoverageTests(unittest.TestCase):
    def audit(self, first: str, last: str) -> dict[str, str]:
        return {"period_start": f"{first}T00:00:00+00:00", "period_end": f"{last}T00:00:00+00:00"}

    def test_the_chained_periods_are_joined_and_not_added(self) -> None:
        entries = [self.audit("2026-09-01", "2026-09-15"), self.audit("2026-09-14", "2026-09-21")]
        self.assertAlmostEqual(coverage.audited_days(entries, MONTH), 20.0)

    def test_what_falls_outside_the_month_does_not_count(self) -> None:
        entries = [self.audit("2026-08-20", "2026-10-10")]
        self.assertAlmostEqual(coverage.audited_days(entries, MONTH), 30.0)
        measured = coverage.audit_coverage(entries, MONTH)
        self.assertEqual((measured["pct"], measured["missing_days"]), (100.0, 0.0))

    def test_a_portfolio_born_inside_the_month_did_not_have_the_month(self) -> None:
        self.assertTrue(coverage.started_after("2026-09-20", MONTH))
        self.assertTrue(coverage.started_after("2026-10-01", MONTH))
        self.assertFalse(coverage.started_after("2026-08-31", MONTH))
        self.assertFalse(coverage.started_after("", MONTH))

    def test_a_series_read_long_after_a_closed_month_is_not_stale(self) -> None:
        # Un resumen de marzo mirado en octubre está bien medido aunque su
        # lectura tenga medio año: vieja es «se leyó antes de que el mes
        # cuajase», no «se leyó hace mucho».
        measured = coverage.series_coverage(
            {"updated_at": "2026-10-05T00:00:00+00:00"}, MONTH,
        )
        self.assertFalse(measured["stale"])
        self.assertTrue(coverage.series_coverage(
            {"updated_at": "2026-09-20T00:00:00+00:00"}, MONTH,
        )["stale"])
        self.assertTrue(coverage.series_coverage({}, MONTH)["never_read"])


class NoticeTests(unittest.TestCase):
    def kinds(self, found: list[dict[str, object]]) -> list[str]:
        return [str(notice["kind"]) for notice in found]

    def test_the_month_always_has_its_line(self) -> None:
        found = notices.notices(facts())
        self.assertEqual(self.kinds(found), ["month_closed"])
        self.assertEqual(notices.verdict(found), notices.KEEP)

    def test_one_weak_month_is_noise_and_the_streak_is_evidence(self) -> None:
        self.assertIsNone(notices.below_streak(facts(streak=2)))
        notice = notices.below_streak(facts(streak=3))
        self.assertEqual(notice["verdict"], notices.CHANGE)

    def test_an_unheard_of_drawdown_asks_for_a_decision(self) -> None:
        breach = notices.drawdown_breach(facts(
            drawdown_standing={"verdict": "beyond", "measured": True, "worst": 90.0},
        ))
        self.assertEqual(breach["verdict"], notices.CHANGE)
        extreme = notices.drawdown_breach(facts(drawdown_standing={
            "verdict": "extreme", "measured": True, "worst": 90.0,
            "rank": evidence.rank(80.0, [10.0, 20.0], lower_is_worse=False),
        }))
        self.assertEqual(extreme["verdict"], notices.WATCH)

    def test_what_degrades_changes_the_strategies_and_not_the_portfolio(self) -> None:
        notice = notices.strategies_degraded(facts(
            degraded=[{"symbol": "EURUSD", "strategy_set": "A"}],
        ))
        self.assertEqual(notice["verdict"], notices.CHANGE)
        self.assertIn("esas estrategias", str(notice["message"]))
        crossing = notices.strategies_degraded(facts(
            crossing=[{"symbol": "EURUSD", "strategy": "B"}],
        ))
        self.assertEqual(crossing["verdict"], notices.WATCH)

    def test_half_an_audited_month_covers_every_other_verdict(self) -> None:
        found = notices.notices(facts(
            streak=5,
            coverage={"pct": 40.0, "audits": 1, "missing_days": 18.0, "month_days": 30},
        ))
        self.assertEqual(self.kinds(found)[0], "coverage_gap")
        self.assertEqual(notices.verdict(found), notices.UNKNOWN)

    def test_no_audit_at_all_is_said_out_loud(self) -> None:
        notice = notices.coverage_gap(facts(
            coverage={"pct": 0.0, "audits": 0, "missing_days": 30.0, "month_days": 30},
        ))
        self.assertIn("ninguna auditoría", str(notice["message"]).lower())

    def test_a_missing_promise_is_a_reason_not_to_conclude(self) -> None:
        notice = notices.coverage_gap(facts(expected={"profit": None}))
        self.assertIn("no tiene media", str(notice["message"]))

    def test_the_amounts_are_written_as_they_are_read_here(self) -> None:
        self.assertEqual(notices.amount(1234.56, "EUR"), "1.234,56 EUR")
        self.assertEqual(notices.amount(None), "sin medir")

    def test_the_badge_counts_one_card_per_verdict(self) -> None:
        counted = notices.decided([
            {"verdict": notices.CHANGE}, {"verdict": notices.CHANGE},
            {"verdict": notices.KEEP},
        ])
        self.assertEqual(counted[notices.CHANGE], 2)
        self.assertEqual(counted[notices.WATCH], 0)


if __name__ == "__main__":
    unittest.main()
