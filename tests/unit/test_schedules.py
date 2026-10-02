import os
import time
import unittest
from dataclasses import replace
from datetime import datetime

from jittersim.config.models import Schedule
from jittersim.engine.schedules import next_schedule_change


@unittest.skipUnless(hasattr(time, "tzset"), "requires a process time zone")
class ScheduleTransitionTests(unittest.TestCase):
    def setUp(self):
        self.original_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Berlin"
        time.tzset()

    def tearDown(self):
        if self.original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.original_tz
        time.tzset()

    def stamp(self, date, *, fold=0):
        return datetime.fromisoformat(date).replace(fold=fold).timestamp()

    def test_device_schedule_wins_until_it_ends_then_group_resumes(self):
        group = Schedule("a-group", None, "slow", (0,), 8*60, 18*60, group_id="family")
        own = Schedule("z-own", "phone", "fast", (0,), 9*60, 11*60)
        self.assertEqual(
            next_schedule_change(schedules=[group, own], device_id="phone", group_id="family",
                                 now=self.stamp("2026-09-28T09:30:00")),
            self.stamp("2026-09-28T11:00:00"),
        )

    def test_hidden_overlap_boundaries_do_not_claim_a_change(self):
        first = Schedule("a-first", "phone", "slow", (0,), 8*60, 18*60)
        hidden = replace(first, id="z-hidden", start_minute=10*60, end_minute=11*60)
        disabled = replace(first, id="disabled", start_minute=9*60, enabled=False)
        other = replace(first, id="other", start_minute=9*60, device_id="tablet")
        self.assertEqual(
            next_schedule_change(schedules=[first, hidden, disabled, other], device_id="phone",
                                 now=self.stamp("2026-09-28T08:30:00")),
            self.stamp("2026-09-28T18:00:00"),
        )

    def test_overnight_and_next_week_for_everyone_else(self):
        schedule = Schedule("night", None, "slow", (6,), 23*60, 60, everyone_else=True)
        for now, expected in [("2026-09-28T00:30:00", "2026-09-28T01:00:00"),
                              ("2026-09-28T01:00:00", "2026-10-04T23:00:00")]:
            with self.subTest(now=now):
                self.assertEqual(next_schedule_change(schedules=[schedule], everyone_else=True,
                                                     now=self.stamp(now)), self.stamp(expected))

    def test_spring_forward_starts_when_clock_skips_into_window(self):
        schedule = Schedule("spring", "phone", "slow", (6,), 150, 210)
        self.assertEqual(next_schedule_change(schedules=[schedule], device_id="phone",
                                             now=self.stamp("2026-03-29T01:45:00")),
                         self.stamp("2026-03-29T03:00:00"))

    def test_fall_back_restarts_and_ends_a_repeated_window(self):
        schedule = Schedule("fall", "phone", "slow", (6,), 120, 150)
        # The first 02:45 goes backward to 02:00, so this window starts again.
        self.assertEqual(next_schedule_change(schedules=[schedule], device_id="phone",
                                             now=self.stamp("2026-10-25T02:45:00")),
                         self.stamp("2026-10-25T02:00:00", fold=1))
        self.assertEqual(next_schedule_change(schedules=[schedule], device_id="phone",
                                             now=self.stamp("2026-10-25T02:15:00", fold=1)),
                         self.stamp("2026-10-25T02:30:00", fold=1))

    def test_no_relevant_schedule_has_no_transition(self):
        self.assertIsNone(next_schedule_change(schedules=[], now=0, device_id="phone"))
