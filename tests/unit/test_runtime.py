import unittest
from datetime import datetime
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile, Randomness, Schedule
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler


class RecordingHelper:
    def __init__(self) -> None:
        self.plans = []

    def apply(self, *, plan):
        self.plans.append(plan)
        return {"commands_run": 1}


class RuntimeSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.helper = RecordingHelper()

    def tearDown(self) -> None:
        self.repository.close()

    def _save_device(self, profile: Profile) -> None:
        self.repository.save_profile(profile)
        self.repository.save_device(
            Device("alice", "Alice", "192.168.50.10", profile.id)
        )

    def test_applies_only_when_the_runtime_plan_changes(self) -> None:
        profile = Profile(
            id="variable",
            name="Variable",
            upload=Impairment(
                delay_ms=200,
                delay_randomness=Randomness(variation_percent=30),
            ),
            reroll_interval_seconds=60,
            random_seed=42,
        )
        self._save_device(profile)
        scheduler = RuntimeScheduler(
            database_path=":memory:",
            helper_client=self.helper,
        )
        scheduler.activate(applied_payload={})

        self.assertTrue(scheduler.tick(repository=self.repository, now=0))
        self.assertFalse(scheduler.tick(repository=self.repository, now=30))
        self.assertTrue(scheduler.tick(repository=self.repository, now=61))
        self.assertEqual(len(self.helper.plans), 2)

    def test_schedule_switches_the_profile_for_its_device(self) -> None:
        normal = Profile(id="normal", name="Normal")
        slow = Profile(id="slow", name="Slow", download=Impairment(delay_ms=500))
        self.repository.save_profile(normal)
        self.repository.save_profile(slow)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_schedule(
            Schedule(
                id="work-hours",
                device_id="alice",
                profile_id="slow",
                days_of_week=(0,),
                start_minute=540,
                end_minute=1_020,
            )
        )
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=0)
        scheduler.tick(repository=self.repository, now=datetime(2026, 9, 21, 10, 0).timestamp())

        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 500)

    def test_random_outage_marks_a_device_as_disconnected(self) -> None:
        profile = Profile(
            id="outage",
            name="Outage",
            reroll_interval_seconds=1,
            outage_probability=100,
            outage_duration_seconds=5,
            outage_interval_seconds=1,
            random_seed=42,
        )
        self._save_device(profile)
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=0)
        scheduler.tick(repository=self.repository, now=2)

        self.assertEqual(
            self.helper.plans[-1].blocked_device_addresses,
            (IPv4Address("192.168.50.10"),),
        )


if __name__ == "__main__":
    unittest.main()
