import unittest
from datetime import datetime
from ipaddress import IPv4Address

from jittersim.config.models import (
    Device,
    DomainObservation,
    Impairment,
    Profile,
    Progression,
    Randomness,
    Schedule,
    Timer,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler


class RecordingHelper:
    def __init__(self) -> None:
        self.plans = []
        self.observations = []

    def apply(self, *, plan):
        self.plans.append(plan)
        return {"commands_run": 1}

    def observe_dns_queries(self):
        return self.observations


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

    def test_timer_overrides_a_schedule_then_returns_to_the_normal_profile(self) -> None:
        normal = Profile(id="normal", name="Normal")
        scheduled = Profile(id="scheduled", name="Scheduled", download=Impairment(delay_ms=500))
        timed = Profile(id="timed", name="Timed", download=Impairment(delay_ms=900))
        self.repository.save_profile(normal)
        self.repository.save_profile(scheduled)
        self.repository.save_profile(timed)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_schedule(
            Schedule(
                id="work-hours",
                device_id="alice",
                profile_id="scheduled",
                days_of_week=(0,),
                start_minute=540,
                end_minute=1_020,
            )
        )
        timer_start = datetime(2026, 9, 21, 10, 0).timestamp()
        self.repository.save_timer(
            Timer(
                id="short-test",
                device_id="alice",
                profile_id="timed",
                started_at=int(timer_start),
                expires_at=int(timer_start) + 600,
            )
        )
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=timer_start)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 900)
        scheduler.tick(repository=self.repository, now=timer_start + 601)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 500)

    def test_time_progression_interpolates_values_then_recovers(self) -> None:
        normal = Profile(id="normal", name="Normal")
        degraded = Profile(
            id="degraded",
            name="Degraded",
            download=Impairment(delay_ms=1_000, loss_percent=80),
        )
        self.repository.save_profile(normal)
        self.repository.save_profile(degraded)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_progression(
            Progression(
                id="five-hours",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="degraded",
                started_at=100,
                duration_seconds=1_000,
            )
        )
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=600)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 500)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.loss_percent, 40)
        scheduler.tick(repository=self.repository, now=1_101)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 0)

    def test_traffic_progression_uses_observed_dns_queries(self) -> None:
        normal = Profile(id="normal", name="Normal")
        degraded = Profile(id="degraded", name="Degraded", download=Impairment(delay_ms=1_000))
        self.repository.save_profile(normal)
        self.repository.save_profile(degraded)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_domain_observations(
            (
                DomainObservation(
                    ipv4_address="192.168.50.10",
                    domain="example.com",
                    query_count=50,
                    first_seen=100,
                    last_seen=100,
                ),
            ),
            now=100,
        )
        self.repository.save_progression(
            Progression(
                id="query-driven",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="degraded",
                started_at=100,
                duration_seconds=1_000,
                mode="traffic",
                traffic_target_queries=100,
            )
        )
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=200)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 500)

    def test_traffic_progression_samples_dns_queries_while_running(self) -> None:
        normal = Profile(id="normal", name="Normal")
        degraded = Profile(id="degraded", name="Degraded", download=Impairment(delay_ms=1_000))
        self.repository.save_profile(normal)
        self.repository.save_profile(degraded)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_progression(
            Progression(
                id="query-driven",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="degraded",
                started_at=100,
                duration_seconds=1_000,
                mode="traffic",
                traffic_target_queries=2,
            )
        )
        self.helper.observations = [
            {
                "ipv4_address": "192.168.50.10",
                "domain": "example.com",
                "query_count": 2,
            }
        ]
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=100)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 1_000)

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
