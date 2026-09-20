import random
import unittest

from jittersim.config.models import Impairment, Profile, Randomness, Schedule
from jittersim.engine.randomizer import (
    outage_duration_seconds,
    outage_interval_seconds,
    outage_is_selected,
    reroll_impairment,
    reroll_interval_seconds,
    reroll_profile,
    seeded_generator,
)
from jittersim.engine.schedules import active_schedule, is_schedule_active
from datetime import datetime


class RandomizerTests(unittest.TestCase):
    def test_rerolls_values_inside_the_requested_percentage(self) -> None:
        impairment = Impairment(
            delay_ms=200,
            jitter_ms=40,
            loss_percent=10,
            bandwidth_kbit=1_000,
            delay_randomness=Randomness(variation_percent=30),
            jitter_randomness=Randomness(variation_percent=50),
            loss_randomness=Randomness(variation_percent=100),
            bandwidth_randomness=Randomness(variation_percent=20),
        )

        rolled = reroll_impairment(impairment=impairment, generator=random.Random(7))

        self.assertGreaterEqual(rolled.delay_ms, 140)
        self.assertLessEqual(rolled.delay_ms, 260)
        self.assertGreaterEqual(rolled.jitter_ms, 20)
        self.assertLessEqual(rolled.jitter_ms, 60)
        self.assertGreaterEqual(rolled.loss_percent, 0)
        self.assertLessEqual(rolled.loss_percent, 20)
        self.assertGreaterEqual(rolled.bandwidth_kbit, 800)
        self.assertLessEqual(rolled.bandwidth_kbit, 1_200)

    def test_zero_probability_disables_a_feature(self) -> None:
        impairment = Impairment(
            delay_ms=200,
            delay_randomness=Randomness(enabled_probability=0),
        )

        rolled = reroll_impairment(impairment=impairment, generator=random.Random(1))

        self.assertEqual(rolled.delay_ms, 0)

    def test_profile_rerolls_are_stable_for_same_device_and_roll(self) -> None:
        profile = Profile(
            id="variable",
            name="Variable",
            upload=Impairment(
                delay_ms=200,
                delay_randomness=Randomness(variation_percent=30),
            ),
            website_mode="allow_only",
            website_domains=("linkedin.com", "youtube.com"),
            website_randomness=Randomness(enabled_probability=50),
            random_seed=42,
        )

        first = reroll_profile(
            profile=profile,
            generator=seeded_generator(profile=profile, device_id="alice", roll_number=3),
        )
        second = reroll_profile(
            profile=profile,
            generator=seeded_generator(profile=profile, device_id="alice", roll_number=3),
        )

        self.assertEqual(first, second)

    def test_interval_and_outage_helpers_respect_disabled_values(self) -> None:
        profile = Profile(id="normal", name="Normal")
        generator = random.Random(1)

        self.assertEqual(reroll_interval_seconds(profile=profile, generator=generator), 0)
        self.assertEqual(outage_interval_seconds(profile=profile, generator=generator), 0)
        self.assertEqual(outage_duration_seconds(profile=profile, generator=generator), 0)
        self.assertFalse(outage_is_selected(profile=profile, generator=generator))


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedule = Schedule(
            id="overnight",
            device_id="alice",
            profile_id="slow",
            days_of_week=(0,),
            start_minute=1_380,
            end_minute=60,
        )

    def test_matches_overnight_start_and_following_morning(self) -> None:
        self.assertTrue(
            is_schedule_active(
                schedule=self.schedule,
                moment=datetime(2026, 9, 21, 23, 30),
            )
        )
        self.assertTrue(
            is_schedule_active(
                schedule=self.schedule,
                moment=datetime(2026, 9, 22, 0, 30),
            )
        )
        self.assertFalse(
            is_schedule_active(
                schedule=self.schedule,
                moment=datetime(2026, 9, 22, 2, 0),
            )
        )

    def test_chooses_stable_schedule_when_windows_overlap(self) -> None:
        later = Schedule(
            id="z-overlap",
            device_id="alice",
            profile_id="slow",
            days_of_week=(0,),
            start_minute=1_400,
            end_minute=1_430,
        )

        selected = active_schedule(
            schedules=(later, self.schedule),
            device_id="alice",
            moment=datetime(2026, 9, 21, 23, 30),
        )

        self.assertEqual(selected.id, "overnight")


if __name__ == "__main__":
    unittest.main()
