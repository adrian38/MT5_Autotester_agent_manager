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
- SIN CONCLUSIÓN gana a CAMBIAR. Con medio tramo exigible sin auditar, un
  «cambia esta cartera» está sacado de la mitad de los datos. Nacer a mitad de
  mes no es un hueco: produce una conclusión parcial y explícitamente marcada.
- Los periodos de auditoría se **unen**, no se suman: se encadenan repitiendo
  el último día, así que sumarlos inflaría la cobertura justo en el solape.
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import monthly_summary_coverage as coverage
from mt5_manager import monthly_summary_build
from mt5_manager import monthly_summary_evidence as evidence
from mt5_manager import monthly_summary_notices as notices
from mt5_manager import monthly_summary_watch
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


SEPTEMBER_YEARS = [8.44, 47.63, 18.50, 44.44, 26.78, 19.73]
"""Los seis meses de una cartera real: media 27,59 y desviación 15,47, así que
lo normal va de 12,11 a 43,06. Son números suyos para que la frase que se
comprueba sea la que el usuario lee."""


def _drawdowns(depths: list[float]) -> dict:
    """Una tabla cuyos meses son todos el de `MONTH`, con esas profundidades."""
    return {
        label: {**row, "drawdown": depths[step]}
        for step, (label, row) in enumerate(_table([1.0] * len(depths)).items())
    }


def _table(profits: list[float]) -> dict:
    """Una tabla de backtest en la que todos los meses son el de `MONTH`."""
    return {
        f"{2020 + step}-{MONTH.split('-')[1]}": {
            "profit": profit, "drawdown": 0.0, "trades": 0,
        }
        for step, profit in enumerate(profits)
    }


SEPTEMBERS = evidence.profit_band(38.14, _table(SEPTEMBER_YEARS), MONTH)


def facts(**changes: object) -> dict:
    base = {
        "currency": "EUR", "month": MONTH,
        "closed": {"profit": 100.0, "drawdown": 50.0, "trades": 12},
        "expected": {"profit": 80.0, "drawdown": 60.0, "years": [2020], "portfolios": 1},
        "below": False, "streak": 0, "streak_required": 3,
        "profit_band": {"measured": False, "verdict": "", "total": 3},
        "drawdown_band": {"measured": False, "verdict": "", "total": 3},
        "backtest_months": 72, "started_inside": False, "started_after_month": False,
        "coverage": {
            "pct": 100.0, "eligible_pct": 100.0, "audits": 2,
            "missing_days": 0.0, "eligible_missing_days": 0.0, "month_days": 30,
        },
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

    def test_the_band_judges_against_the_months_of_the_same_season(self) -> None:
        band = evidence.profit_band(38.14, _table(SEPTEMBER_YEARS), MONTH)
        self.assertEqual(band["total"], 6)
        self.assertAlmostEqual(band["mean"], 27.5866, places=3)
        self.assertEqual(band["verdict"], "normal")
        self.assertAlmostEqual(band["ratio"], 138.25, places=1)

    def test_four_years_of_backtest_do_not_judge_anything(self) -> None:
        # Por debajo de `MINIMUM_MONTHS`, una media y una desviación son dos
        # números con la forma de una medida y ninguna muestra detrás.
        band = evidence.profit_band(15.0, _table([10.0, 20.0, 30.0, 40.0]), MONTH)
        self.assertFalse(band["measured"])
        self.assertEqual(band["total"], 4)
        self.assertEqual(band["verdict"], "")

    def test_the_plain_monthly_averages_sit_next_to_the_seasonal_ones(self) -> None:
        # Contexto, no criterio: saber si este mes del año es de los buenos de
        # la cartera cambia lo que pesa quedarse corto en él.
        table = {**_table(SEPTEMBER_YEARS), "2024-03": {
            "profit": 100.0, "drawdown": 50.0, "trades": 1,
        }}
        means = evidence.monthly_means(table)
        self.assertEqual(means["total"], 7)
        self.assertAlmostEqual(means["profit"], 37.9314, places=3)
        self.assertAlmostEqual(means["drawdown"], 7.1428, places=3)
        self.assertFalse(evidence.monthly_means({})["measured"])

    def test_the_drawdown_is_judged_against_its_own_season(self) -> None:
        # Misma vara que el beneficio: un octubre se mide con octubres, y el
        # listón deja de estar en manos de un agosto cualquiera.
        depths = _drawdowns([10.0, 12.0, 14.0, 16.0, 18.0, 20.0])
        self.assertEqual(evidence.drawdown_band(90.0, depths, MONTH)["verdict"], "beyond")
        self.assertEqual(evidence.drawdown_band(19.0, depths, MONTH)["verdict"], "deeper")
        self.assertEqual(evidence.drawdown_band(15.0, depths, MONTH)["verdict"], "normal")
        self.assertEqual(evidence.drawdown_band(1.0, depths, MONTH)["verdict"], "shallower")

    def test_the_drawdown_band_never_opens_below_zero(self) -> None:
        # Con una desviación mayor que la media, `media − desviación` daría un
        # suelo que no existe: un drawdown no es negativo.
        band = evidence.drawdown_band(1.0, _drawdowns([0.0, 0.0, 0.0, 0.0, 0.0, 90.0]), MONTH)
        self.assertEqual(band["low"], 0.0)

    def test_the_drawdown_names_the_month_that_set_the_bar(self) -> None:
        band = evidence.drawdown_band(90.0, _drawdowns([10.0] * 5 + [34.61]), MONTH)
        self.assertEqual(band["worst"], 34.61)
        self.assertEqual(band["worst_month"], "2025-09")

    def test_without_enough_backtest_nothing_is_declared(self) -> None:
        band = evidence.drawdown_band(500.0, _drawdowns([10.0]), MONTH)
        self.assertFalse(band["measured"])
        self.assertEqual(band["verdict"], "")


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
        self.assertTrue(coverage.started_within("2026-09-20", MONTH))
        self.assertFalse(coverage.started_within("2026-10-01", MONTH))
        self.assertTrue(coverage.started_later("2026-10-01", MONTH))

    def test_partial_coverage_is_measured_from_the_portfolio_birth(self) -> None:
        measured = coverage.audit_coverage(
            [self.audit("2026-09-15", "2026-10-01")], MONTH, "2026-09-15",
        )
        self.assertAlmostEqual(measured["pct"], 16 * 100 / 30)
        self.assertEqual(measured["eligible_pct"], 100.0)
        self.assertEqual(measured["eligible_missing_days"], 0.0)

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

    def test_a_read_at_month_close_is_enough_to_close_the_summary(self) -> None:
        self.assertFalse(coverage.series_coverage(
            {"updated_at": "2026-10-01T00:00:00+00:00"}, MONTH,
        )["stale"])
        self.assertTrue(coverage.series_coverage(
            {"updated_at": "2026-09-30T23:59:59+00:00"}, MONTH,
        )["stale"])


class AnnouncementTests(unittest.TestCase):
    """El día 1 deja un aviso en la campana, y la campana es el móvil.

    Publicar no se decide aquí: `LiveAuditNotificationStore.add` anuncia por su
    cuenta y `manager_server` le enchufa `web_push`. Lo que se comprueba es que
    el resumen **pase por esa puerta** y con qué, porque un aviso que no se
    escribe no se publica —y el del mes cerrado es el único que no tiene una
    pantalla delante en la que se vea solo. La puerta la vigila
    `tests/manager/test_notice_single_door.py`.
    """

    def setUp(self) -> None:
        self.written: list[dict] = []
        self.delivered: list[dict] = []
        notice = self

        class Bell:
            def add(self, level, title, message="", **extra):
                event = {"level": level, "title": title, "message": message, **extra}
                notice.written.append(event)
                # Lo que hace el almacén de verdad en cuanto anota.
                notice.delivered.append(event)
                return event

        class Server:
            live_audit_notifications = Bell()
            monthly_summaries = None

        self.server = Server()

    def report(self, change: int = 2, watch: int = 1, unknown: int = 0) -> dict:
        return {
            "month": MONTH,
            "counts": {
                notices.CHANGE: change, notices.WATCH: watch,
                notices.UNKNOWN: unknown, notices.KEEP: 1,
            },
            "accounts": [{"verdict": notices.CHANGE}] * (change + watch + unknown + 1),
        }

    def test_the_summary_leaves_one_notice_and_not_one_per_account(self) -> None:
        # El resumen existe justamente para que las doce tarjetas se lean de
        # una vez; doce avisos serían lo contrario.
        monthly_summary_watch._announce(self.server, self.report())
        self.assertEqual(len(self.written), 1)
        self.assertEqual(len(self.delivered), 1)

    def test_what_it_says_is_cuantas_piden_decision(self) -> None:
        monthly_summary_watch._announce(self.server, self.report())
        event = self.written[0]
        self.assertIn("septiembre de 2026", event["title"])
        self.assertIn("2 piden decisión", event["message"])
        self.assertIn("1 a vigilar", event["message"])
        self.assertEqual(event["level"], "warning")
        self.assertEqual(event["kind"], "monthly_summary")

    def test_a_month_with_nothing_to_decide_is_an_info(self) -> None:
        monthly_summary_watch._announce(self.server, self.report(0, 0, 0))
        event = self.written[0]
        self.assertEqual(event["level"], "info")
        self.assertIn("nada que decidir", event["message"])

    def test_the_notice_carries_the_month_to_its_screen(self) -> None:
        # Abierto tres días después desde el móvil, tiene que salir el mes que
        # se anunció y no el último guardado.
        monthly_summary_watch._announce(self.server, self.report())
        self.assertEqual(
            self.written[0]["link"], f"{monthly_summary_watch.LINK}?month={MONTH}",
        )
        self.assertEqual(self.written[0]["link_text"], "Ver resumen")

    def test_the_automatic_turn_announces_and_the_manual_one_does_not(self) -> None:
        # Quien pulsa «Volver a calcular» está mirando la pantalla: ya lo tiene
        # delante.
        stored = {"month": MONTH, "counts": {}, "accounts": []}

        class Store:
            def remember(self, report):
                return dict(report)

        self.server.monthly_summaries = Store()
        with unittest.mock.patch.object(
            monthly_summary_watch.monthly_summary_build, "build", return_value=stored,
        ):
            monthly_summary_watch.run(self.server, MONTH)
            self.assertEqual(len(self.written), 1)
            monthly_summary_watch.run(self.server, MONTH, announce=False)
            self.assertEqual(len(self.written), 1)


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
        deep = evidence.drawdown_band(90.0, _drawdowns([10.0] * 5 + [34.61]), MONTH)
        breach = notices.drawdown_breach(facts(drawdown_band=deep))
        self.assertEqual(breach["verdict"], notices.CHANGE)
        self.assertIn("peor septiembre del backtest", str(breach["message"]))
        self.assertIn("34,61 EUR, septiembre de 2025", str(breach["message"]))

    def test_a_drawdown_outside_the_band_is_only_to_be_watched(self) -> None:
        band = evidence.drawdown_band(19.0, _drawdowns([10.0, 12.0, 14.0, 16.0, 18.0, 20.0]), MONTH)
        notice = notices.drawdown_breach(facts(
            closed={"profit": 1.0, "drawdown": 19.0, "trades": 1}, drawdown_band=band,
        ))
        self.assertEqual(notice["verdict"], notices.WATCH)
        self.assertIn("un septiembre le cuesta de media", str(notice["message"]))

    def test_a_drawdown_inside_its_band_says_nothing(self) -> None:
        band = evidence.drawdown_band(15.0, _drawdowns([10.0, 12.0, 14.0, 16.0, 18.0, 20.0]), MONTH)
        self.assertIsNone(notices.drawdown_breach(facts(drawdown_band=band)))

    def test_the_month_says_whether_it_respects_its_own_average(self) -> None:
        # No dónde cae, sino si se porta como lo que se compró: un recuento es
        # una posición y la pantalla tiene que dar un juicio.
        message = str(notices.month_closed(facts(
            closed={"profit": 38.14, "drawdown": 1.0, "trades": 9},
            profit_band=SEPTEMBERS,
        ))["message"])
        self.assertIn("Un septiembre normal da entre", message)
        self.assertIn("(media 27,59 EUR, 6 septiembres del backtest)", message)
        self.assertIn("Éste hizo el 138 %: dentro de lo normal.", message)
        self.assertNotIn("fueron peores", message)

    def test_a_month_outside_the_band_is_judged_by_it(self) -> None:
        low = evidence.profit_band(8.0, _table(SEPTEMBER_YEARS), MONTH)
        self.assertEqual(low["verdict"], "below")
        high = evidence.profit_band(90.0, _table(SEPTEMBER_YEARS), MONTH)
        self.assertEqual(high["verdict"], "above")
        self.assertIn("por encima de lo normal", notices.band_sentence(
            facts(closed={"profit": 90.0}, profit_band=high), "EUR",
        ))

    def test_a_season_without_enough_years_is_not_judged(self) -> None:
        # Mismo listón de muestra que el recuento: con tres septiembres no se
        # puede decir qué es normal en uno, así que no se dice.
        band = evidence.profit_band(100.0, _table([10.0, 20.0, 30.0]), MONTH)
        self.assertFalse(band["measured"])
        self.assertIn(
            "no tiene septiembres suficientes para decir qué es normal",
            str(notices.month_closed(facts(profit_band=band))["message"]),
        )

    def test_a_month_below_normal_decides_the_card_by_itself(self) -> None:
        # Petición del usuario: no hay que esperar a la racha para que una
        # cartera que rindió el 45 % de su septiembre deje de ser MANTENER.
        band = evidence.profit_band(8.0, _table(SEPTEMBER_YEARS), MONTH)
        found = notices.notices(facts(profit_band=band))
        self.assertIn("seasonal_shortfall", [str(n["kind"]) for n in found])
        self.assertEqual(notices.verdict(found), notices.WATCH)
        notice = notices.seasonal_shortfall(facts(profit_band=band))
        self.assertEqual(notice["title"], "Septiembre por debajo de lo normal: 29 % de la media")

    def test_a_month_inside_the_band_decides_nothing(self) -> None:
        self.assertIsNone(notices.seasonal_shortfall(facts(profit_band=SEPTEMBERS)))
        self.assertEqual(notices.verdict(notices.notices(facts())), notices.KEEP)

    def test_the_shortfall_keeps_quiet_once_the_streak_speaks(self) -> None:
        # «Este septiembre fue flojo» debajo de «3 meses seguidos por debajo»
        # no añade una decisión, añade una línea.
        band = evidence.profit_band(8.0, _table(SEPTEMBER_YEARS), MONTH)
        self.assertIsNone(
            notices.seasonal_shortfall(facts(profit_band=band, streak=3)),
        )

    def test_a_negative_average_leaves_no_percentage_to_show(self) -> None:
        # El 45 % de una pérdida media no significa nada: se dice con palabras.
        band = evidence.profit_band(-80.0, _table([-10.0, -20.0] * 3), MONTH)
        self.assertIsNone(band["ratio"])
        self.assertIn(
            "Éste quedó por debajo de lo normal.",
            notices.band_sentence(facts(profit_band=band), "EUR"),
        )

    def test_a_drawdown_at_another_lot_is_not_unheard_of(self) -> None:
        # Con autolote la serie del backtest sigue al lote declarado: lo que
        # saldría «inédito» sería el tamaño de la posición.
        deep = evidence.drawdown_band(90.0, _drawdowns([10.0] * 6), MONTH)
        self.assertIsNone(notices.drawdown_breach(facts(
            table_comparable=False, drawdown_band=deep,
        )))

    def test_a_month_that_cannot_be_judged_says_why_instead_of_judging(self) -> None:
        judged = str(notices.month_closed(facts(profit_band=SEPTEMBERS))["message"])
        self.assertIn("dentro de lo normal", judged)
        unjudged = str(notices.month_closed(facts(
            profit_band=SEPTEMBERS, table_comparable=False,
        ))["message"])
        self.assertIn("Prometía", unjudged)
        self.assertNotIn("lo normal", unjudged)
        self.assertEqual(
            unjudged[unjudged.index("No se sitúa"):], notices.AUTOLOT_TABLE,
        )

    def test_a_month_without_a_promise_at_its_lots_shows_no_promise(self) -> None:
        notice = notices.month_closed(facts(promise_comparable=False))
        self.assertEqual(notice["message"], notices.AUTOLOT_PROMISE)
        self.assertEqual(notice["level"], "info")

    def test_a_summary_saved_before_the_lot_marks_keeps_its_comparisons(self) -> None:
        # Un resumen es la foto de un mes y se relee tal cual se guardó: la
        # marca ausente no puede significar «no comparable».
        self.assertTrue(notices.comparable(facts(), "table_comparable"))
        stored = facts(profit_band=SEPTEMBERS)
        self.assertIn("lo normal", str(notices.month_closed(stored)["message"]))

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

    def test_a_portfolio_born_mid_month_gets_a_qualified_conclusion(self) -> None:
        partial = facts(
            started_inside=True,
            coverage={
                "pct": 40.0, "eligible_pct": 100.0, "audits": 1,
                "missing_days": 18.0, "eligible_missing_days": 0.0,
                "eligible_start": "2026-09-18",
            },
        )
        found = notices.notices(partial)
        self.assertEqual(notices.verdict(found), notices.WATCH)
        self.assertEqual(self.kinds(found), ["partial_month", "month_closed"])
        self.assertIn("sólo desde", str(found[0]["message"]))
        self.assertIn("no se compara", str(found[1]["message"]))

    def test_a_partial_month_cannot_claim_a_full_month_drawdown(self) -> None:
        partial = facts(
            started_inside=True, streak=5,
            drawdown_band={"verdict": "beyond", "measured": True, "worst": 10.0},
        )
        found = notices.notices(partial)
        self.assertNotIn("drawdown_breach", self.kinds(found))
        self.assertNotIn("below_streak", self.kinds(found))

    def test_a_partial_month_does_not_need_a_full_month_promise(self) -> None:
        partial = facts(started_inside=True, expected={"profit": None})
        found = notices.notices(partial)
        self.assertEqual(notices.verdict(found), notices.WATCH)
        self.assertNotIn("coverage_gap", self.kinds(found))
        self.assertFalse(notices.recovery_needs(partial)["permanent"])

    def test_a_partial_month_still_blocks_if_its_own_tranche_is_missing(self) -> None:
        found = notices.notices(facts(
            started_inside=True,
            coverage={
                "pct": 20.0, "eligible_pct": 40.0, "audits": 1,
                "missing_days": 24.0, "eligible_missing_days": 9.0,
            },
        ))
        self.assertEqual(notices.verdict(found), notices.UNKNOWN)
        self.assertIn("desde que nació", str(found[0]["message"]))

    def test_a_portfolio_that_did_not_exist_yet_stays_without_conclusion(self) -> None:
        found = notices.notices(facts(started_after_month=True))
        self.assertEqual(notices.verdict(found), notices.UNKNOWN)
        self.assertIn("todavía no existía", str(found[0]["message"]))

    def test_only_recoverable_gaps_are_offered_to_the_button(self) -> None:
        self.assertEqual(notices.recovery_needs(facts(series={"stale": True})), {
            "account_snapshot": True, "audit": False, "permanent": False,
        })
        self.assertEqual(notices.recovery_needs(facts(coverage={"pct": 40.0})), {
            "account_snapshot": False, "audit": True, "permanent": False,
        })
        partial = notices.recovery_needs(facts(
            started_inside=True, series={"stale": True},
            coverage={"pct": 40.0, "eligible_pct": 100.0},
        ))
        self.assertEqual(partial, {
            "account_snapshot": True, "audit": False, "permanent": False,
        })
        blocked = notices.recovery_needs(facts(
            started_after_month=True, series={"stale": True}, coverage={"pct": 40.0},
        ))
        self.assertEqual(blocked, {
            "account_snapshot": False, "audit": False, "permanent": True,
        })

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
