"""El cron de las cuentas reales: conjunto de días, turnos y moneda.

Lo que distingue a este cron del de auditoría es la cadencia —un conjunto de
días en vez de uno— y que suma monedas distintas. Las dos cosas tienen una
forma de romperse silenciosa: una cita que se calcula sobre un instante con
zona se mueve con el horario de verano, y un cambio que falta convierte el
total de arriba en un número falso.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import live_account_schedule as schedule

SETTINGS = dict(schedule.DEFAULT_LIVE_ACCOUNT_SCHEDULER_SETTINGS)


def madrid(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/Madrid"))


class SettingsTests(unittest.TestCase):
    def test_the_factory_setting_is_weekdays_at_eleven_at_night(self) -> None:
        self.assertEqual(SETTINGS["weekdays"], [0, 1, 2, 3, 4])
        self.assertEqual((SETTINGS["hour"], SETTINGS["minute"]), (23, 0))
        self.assertEqual(SETTINGS["timezone"], "Europe/Madrid")
        self.assertFalse(SETTINGS["enabled"])

    def test_an_empty_set_of_days_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            schedule.normalize_live_account_scheduler_settings({"weekdays": []})

    def test_the_days_come_out_sorted_and_without_repeats(self) -> None:
        normalized = schedule.normalize_live_account_scheduler_settings(
            {"weekdays": [4, 0, 4, 2]},
        )
        self.assertEqual(normalized["weekdays"], [0, 2, 4])

    def test_an_unknown_field_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            schedule.normalize_live_account_scheduler_settings({"weekday": 5})

    def test_a_rate_that_is_not_a_positive_number_is_refused(self) -> None:
        for rates in ({"USD": 0}, {"USD": -1}, {"USD": "casi un euro"}):
            with self.assertRaises(ValueError):
                schedule.normalize_live_account_scheduler_settings(
                    {"exchange_rates": rates},
                )

    def test_the_currency_code_is_three_letters(self) -> None:
        normalized = schedule.normalize_live_account_scheduler_settings(
            {"display_currency": "eur", "exchange_rates": {"usd": 0.9}},
        )
        self.assertEqual(normalized["display_currency"], "EUR")
        self.assertEqual(normalized["exchange_rates"], {"USD": 0.9})


class OccurrenceTests(unittest.TestCase):
    def test_the_previous_appointment_skips_the_weekend(self) -> None:
        # Domingo 18 de octubre de 2026 a mediodía: la última cita fue el
        # viernes 16, no el sábado 17.
        previous = schedule.previous_occurrence(madrid("2026-10-18T12:00:00"), SETTINGS)
        self.assertEqual(previous.isoformat(), "2026-10-16T23:00:00+02:00")

    def test_before_the_hour_the_previous_one_is_yesterday(self) -> None:
        previous = schedule.previous_occurrence(madrid("2026-10-21T22:00:00"), SETTINGS)
        self.assertEqual(previous.isoformat(), "2026-10-20T23:00:00+02:00")

    def test_after_the_hour_the_previous_one_is_today(self) -> None:
        previous = schedule.previous_occurrence(madrid("2026-10-21T23:30:00"), SETTINGS)
        self.assertEqual(previous.isoformat(), "2026-10-21T23:00:00+02:00")

    def test_the_next_appointment_can_be_today(self) -> None:
        following = schedule.next_occurrence(madrid("2026-10-21T10:00:00"), SETTINGS)
        self.assertEqual(following.isoformat(), "2026-10-21T23:00:00+02:00")

    def test_the_next_one_on_friday_night_is_monday(self) -> None:
        following = schedule.next_occurrence(madrid("2026-10-16T23:30:00"), SETTINGS)
        self.assertEqual(following.isoformat(), "2026-10-19T23:00:00+02:00")

    def test_the_appointment_keeps_its_wall_hour_across_the_time_change(self) -> None:
        # El cambio de horario en Madrid fue el domingo 25 de octubre de 2026.
        # Las dos citas son a las 23:00 locales aunque el desfase cambie.
        before = schedule.previous_occurrence(madrid("2026-10-23T23:30:00"), SETTINGS)
        after = schedule.previous_occurrence(
            datetime(2026, 10, 27, 23, 30, tzinfo=ZoneInfo("Europe/Madrid")), SETTINGS,
        )
        self.assertEqual(before.strftime("%H:%M"), "23:00")
        self.assertEqual(after.strftime("%H:%M"), "23:00")
        self.assertNotEqual(before.utcoffset(), after.utcoffset())


class WindowTests(unittest.TestCase):
    def test_a_completion_after_the_appointment_closes_the_turn(self) -> None:
        now = madrid("2026-10-21T23:30:00")
        window, pending = schedule.pending_window(
            SETTINGS, madrid("2026-10-21T23:10:00"), now,
        )
        self.assertEqual(window, "2026-10-21T23:00:00+02:00")
        self.assertFalse(pending)

    def test_a_completion_before_the_appointment_leaves_it_pending(self) -> None:
        _window, pending = schedule.pending_window(
            SETTINGS, madrid("2026-10-20T23:10:00"), madrid("2026-10-21T23:30:00"),
        )
        self.assertTrue(pending)

    def test_an_account_born_after_the_appointment_waits_for_the_next(self) -> None:
        _window, pending = schedule.pending_window(
            SETTINGS, None, madrid("2026-10-21T23:30:00"),
            eligible_since=madrid("2026-10-21T23:20:00"),
        )
        self.assertFalse(pending)

    def test_a_missed_appointment_is_still_pending_the_next_morning(self) -> None:
        _window, pending = schedule.pending_window(
            SETTINGS, None, madrid("2026-10-22T09:00:00"),
        )
        self.assertTrue(pending)


class PeriodTests(unittest.TestCase):
    def test_without_a_series_the_whole_life_of_the_account_is_asked(self) -> None:
        start, end = schedule.snapshot_period(
            SETTINGS, "", madrid("2026-10-21T23:00:00"),
        )
        self.assertEqual((start, end), ("", "2026-10-21"))

    def test_with_a_series_the_last_days_are_read_again(self) -> None:
        start, end = schedule.snapshot_period(
            SETTINGS, "2026-10-20", madrid("2026-10-21T23:00:00"),
        )
        self.assertEqual((start, end), ("2026-10-17", "2026-10-21"))

    def test_an_unreadable_last_day_falls_back_to_the_whole_life(self) -> None:
        start, _end = schedule.snapshot_period(
            SETTINGS, "el martes", madrid("2026-10-21T23:00:00"),
        )
        self.assertEqual(start, "")


class CurrencyTests(unittest.TestCase):
    def test_the_display_currency_is_not_converted(self) -> None:
        self.assertAlmostEqual(schedule.convert(10.0, "EUR", SETTINGS), 10.0)

    def test_another_currency_uses_its_stored_rate(self) -> None:
        self.assertAlmostEqual(schedule.convert(10.0, "USD", SETTINGS), 9.2)

    def test_a_missing_rate_is_said_and_never_summed_raw(self) -> None:
        self.assertIsNone(schedule.convert(10.0, "GBP", SETTINGS))

    def test_an_account_without_currency_counts_as_the_display_one(self) -> None:
        self.assertAlmostEqual(schedule.convert(10.0, "", SETTINGS), 10.0)


class LabelTests(unittest.TestCase):
    def test_the_label_says_the_days_as_a_person_would(self) -> None:
        self.assertIn("de lunes a viernes a las 23:00", schedule.schedule_label(SETTINGS))
        self.assertEqual(
            schedule.weekday_label([0, 1, 2, 3, 4, 5, 6]), "todos los días",
        )
        self.assertEqual(schedule.weekday_label([5]), "cada sábado")
        self.assertEqual(schedule.weekday_label([0, 3]), "lunes y jueves")
        self.assertEqual(
            schedule.weekday_label([1, 2, 3]), "de martes a jueves",
        )

    def test_the_description_says_that_it_pauses_the_pipeline(self) -> None:
        text = schedule.schedule_description(SETTINGS)
        self.assertIn("pausa el pipeline", text)
        self.assertIn("3 día(s)", text)


class SharedPolicyTests(unittest.TestCase):
    def test_the_retry_policy_is_the_one_of_the_audit_cron(self) -> None:
        from mt5_manager import live_audit_schedule

        self.assertIs(schedule.due_decision, live_audit_schedule.due_decision)
        record = {"window": "w", "outcome": "failed", "attempts": 1,
                  "last_attempt_at": "2026-10-21T23:00:00+00:00"}
        now = datetime(2026, 10, 21, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(schedule.due_decision(SETTINGS, record, "w", now), "retry")


if __name__ == "__main__":
    unittest.main()
