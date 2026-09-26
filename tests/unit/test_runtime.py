import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime
from ipaddress import IPv4Address
from pathlib import Path
from unittest.mock import patch

from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    Profile,
    Progression,
    Randomness,
    Scenario,
    ScenarioRun,
    ScenarioStep,
    Schedule,
    ServiceQuota,
    TimeQuota,
    Timer,
    UnaffectedDevice,
    time_quota_counter_id,
)
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import PlanError
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError


class RecordingHelper:
    def __init__(self) -> None:
        self.plans = []
        self.resets = 0
        self.observations = []
        self.counters = {"devices": {}, "services": {}}

    def apply(self, *, plan):
        self.plans.append(plan)
        return {"commands_run": 1}

    def reset(self):
        self.resets += 1
        return {"commands_run": 1}

    def observe_dns_queries(self):
        return self.observations

    def traffic_counters(self):
        return self.counters


class ClientListingHelper(RecordingHelper):
    """Like the real helper, it lists the clients on the test Wi-Fi."""

    def __init__(self) -> None:
        super().__init__()
        self.clients = []
        self.snapshots = 0
        self.status_available = True

    def connected_device_snapshot(self):
        self.snapshots += 1
        return {"devices": self.clients, "association_status_available": self.status_available}


class CounterResettingHelper(RecordingHelper):
    """Like the real helper, each apply recreates the nftables counters at zero."""

    def apply(self, *, plan):
        result = super().apply(plan=plan)
        self.counters = {
            kind: {identifier: 0 for identifier in values}
            for kind, values in self.counters.items()
        }
        return result


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

    def test_pause_waits_for_an_inflight_apply(self) -> None:
        class BlockingHelper(RecordingHelper):
            def __init__(self) -> None:
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()
                self.actions = []

            def apply(self, *, plan):
                self.entered.set()
                if not self.release.wait(3):
                    raise AssertionError("apply was not released")
                self.actions.append("apply")

            def reset(self):
                self.actions.append("reset")

        with tempfile.TemporaryDirectory() as directory:
            database_path = f"{directory}/jittersim.db"
            with ConfigRepository(database_path) as repository:
                repository.save_profile(Profile(id="normal", name="Normal"))
                repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
            helper = BlockingHelper()
            scheduler = RuntimeScheduler(database_path=database_path, helper_client=helper)
            scheduler._paused = False
            scheduler._current_payload = {}
            apply_thread = threading.Thread(target=scheduler.tick)
            reset_started = threading.Event()
            reset_finished = threading.Event()

            def reset_after_pause():
                reset_started.set()
                scheduler.pause()
                helper.reset()
                reset_finished.set()

            reset_thread = threading.Thread(target=reset_after_pause)
            apply_thread.start()
            self.assertTrue(helper.entered.wait(3))
            reset_thread.start()
            self.assertTrue(reset_started.wait(3))
            self.assertFalse(reset_finished.wait(0.1))
            helper.release.set()
            apply_thread.join(3)
            reset_thread.join(3)
            self.assertFalse(apply_thread.is_alive())
            self.assertFalse(reset_thread.is_alive())
            self.assertEqual(helper.actions, ["apply", "reset"])
            self.assertFalse(scheduler.tick())

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

    def test_applied_queue_limits_read_only_the_last_successful_plan(self) -> None:
        payload = {
            "queues": [
                {
                    "device_id": "alice",
                    "direction": "upload",
                    "impairment": {"bandwidth_kbit": None},
                },
                {
                    "device_id": "alice",
                    "direction": "download",
                    "impairment": {"bandwidth_kbit": 20_000},
                },
            ]
        }
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)

        self.assertIsNone(scheduler.applied_queue_bandwidth_limits())
        scheduler._current_payload = payload
        with patch.object(scheduler, "_build_plan", side_effect=AssertionError("must not rebuild")):
            self.assertEqual(
                scheduler.applied_queue_bandwidth_limits(),
                {"alice": {"upload_kbit": None, "download_kbit": 20_000}},
            )
        scheduler.pause()
        self.assertIsNone(scheduler.applied_queue_bandwidth_limits())

    def test_applied_queue_limit_tracks_successful_schedule_switch(self) -> None:
        normal = Profile(
            id="normal",
            name="Normal",
            download=Impairment(bandwidth_kbit=100_000),
        )
        scheduled = Profile(
            id="scheduled",
            name="Scheduled",
            download=Impairment(bandwidth_kbit=20_000),
        )
        self.repository.save_profile(normal)
        self.repository.save_profile(scheduled)
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
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler._paused = False
        scheduler._current_payload = {
            "queues": [
                {
                    "device_id": "alice",
                    "direction": "upload",
                    "impairment": {"bandwidth_kbit": None},
                },
                {
                    "device_id": "alice",
                    "direction": "download",
                    "impairment": {"bandwidth_kbit": 100_000},
                },
            ]
        }
        self.assertEqual(
            scheduler.applied_queue_bandwidth_limits()["alice"]["download_kbit"],
            100_000,
        )

        scheduler.tick(
            repository=self.repository,
            now=datetime(2026, 9, 21, 10, 0).timestamp(),
        )

        self.assertEqual(
            scheduler.applied_queue_bandwidth_limits()["alice"]["download_kbit"],
            20_000,
        )

    def test_failed_profile_apply_retries_even_after_reverting_to_the_previous_plan(self) -> None:
        original = Profile(
            id="normal", name="Normal", download=Impairment(bandwidth_kbit=100_000)
        )
        self._save_device(original)
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler._paused = False
        self.assertTrue(scheduler.tick(repository=self.repository, now=0))
        original_plan = scheduler.applied_plan()
        self.repository.save_profile(
            replace(original, download=Impairment(bandwidth_kbit=20_000))
        )

        with patch.object(self.helper, "apply", side_effect=HelperClientError("apply failed")):
            with self.assertLogs("jittersim.engine.runtime", "ERROR"):
                self.assertFalse(scheduler.tick(repository=self.repository, now=1))

        self.assertIsNone(scheduler.applied_plan())
        self.assertIsNone(scheduler.applied_queue_bandwidth_limits())
        self.repository.save_profile(original)
        self.assertTrue(scheduler.tick(repository=self.repository, now=2))
        self.assertEqual(scheduler.applied_plan(), original_plan)
        self.assertEqual(
            scheduler.applied_queue_bandwidth_limits()["alice"]["download_kbit"], 100_000
        )

    def test_build_failure_preserves_the_plan_that_is_still_applied(self) -> None:
        self._save_device(Profile(id="normal", name="Normal"))
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler._paused = False
        self.assertTrue(scheduler.tick(repository=self.repository, now=0))
        original_plan = scheduler.applied_plan()

        with patch.object(scheduler, "_build_plan", side_effect=PlanError("invalid plan")):
            with self.assertLogs("jittersim.engine.runtime", "ERROR"):
                self.assertFalse(scheduler.tick(repository=self.repository, now=1))

        self.assertEqual(scheduler.applied_plan(), original_plan)
        self.assertFalse(scheduler.tick(repository=self.repository, now=2))
        self.assertEqual(len(self.helper.plans), 1)

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

    def test_byte_progression_advances_with_streaming_and_survives_counter_reset(self) -> None:
        normal = Profile(id="normal", name="Normal")
        degraded = Profile(id="degraded", name="Degraded", download=Impairment(delay_ms=1_000))
        self.repository.save_profile(normal)
        self.repository.save_profile(degraded)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_progression(
            Progression(
                id="byte-driven",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="degraded",
                started_at=100,
                duration_seconds=1_000,
                mode="bytes",
                traffic_target_bytes=1_000,
            )
        )
        self.helper.counters = {"devices": {"alice": 0}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=100)
        self.helper.counters["devices"]["alice"] = 500
        scheduler.tick(repository=self.repository, now=106)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 500)

        self.helper.counters["devices"]["alice"] = 0
        scheduler.tick(repository=self.repository, now=112)
        self.helper.counters["devices"]["alice"] = 500
        scheduler.tick(repository=self.repository, now=118)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 1_000)

    def test_device_quota_switches_to_its_fallback_profile(self) -> None:
        normal = Profile(id="normal", name="Normal")
        fallback = Profile(id="fallback", name="Fallback", download=Impairment(delay_ms=700))
        self.repository.save_profile(normal)
        self.repository.save_profile(fallback)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_data_limit(
            DataLimit(
                device_id="alice",
                profile_id="fallback",
                limit_bytes=100,
                last_reset_at=100,
            )
        )
        self.helper.counters = {"devices": {"alice": 150}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=100)

        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 700)
        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 150)

    def test_group_profile_and_shared_quota_apply_to_every_member(self) -> None:
        normal = Profile(id="normal", name="Normal")
        group_profile = Profile(
            id="group-slow",
            name="Group slow",
            download=Impairment(delay_ms=400),
        )
        fallback = Profile(
            id="group-fallback",
            name="Group fallback",
            download=Impairment(delay_ms=700),
        )
        for profile in (normal, group_profile, fallback):
            self.repository.save_profile(profile)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device_group(
            DeviceGroup(
                id="family",
                name="Family devices",
                device_ids=("alice", "bob"),
                profile_id="group-slow",
            )
        )
        self.repository.save_group_data_limit(
            GroupDataLimit(
                group_id="family",
                profile_id="group-fallback",
                limit_bytes=100,
                last_reset_at=100,
            )
        )
        self.helper.counters = {"devices": {"alice": 60, "bob": 40}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=100)

        self.assertEqual(self.repository.get_group_data_limit("family").used_bytes, 100)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 700)
        self.assertEqual(self.helper.plans[-1].queues[3].impairment.delay_ms, 700)

    def test_service_quota_uses_all_selected_observed_domains(self) -> None:
        normal = Profile(id="normal", name="Normal")
        fallback = Profile(id="fallback", name="Fallback", download=Impairment(delay_ms=900))
        self.repository.save_profile(normal)
        self.repository.save_profile(fallback)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_service_quota(
            ServiceQuota(
                id="alice-video",
                device_id="alice",
                name="Video service",
                domains=("netflix.com", "nflxvideo.net"),
                profile_id="fallback",
                limit_bytes=100,
                weight_percent=120,
                last_reset_at=100,
            )
        )
        self.helper.counters = {"devices": {}, "services": {"alice-video": 150}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        with patch(
            "jittersim.engine.runtime.resolve_domains",
            return_value=(IPv4Address("203.0.113.10"), IPv4Address("203.0.113.11")),
        ):
            scheduler.tick(repository=self.repository, now=100)

        rule = self.helper.plans[-1].service_quota_rules[0]
        self.assertTrue(rule.active)
        self.assertEqual(self.repository.get_service_quota("alice-video").used_bytes, 180)
        self.assertEqual(
            rule.destination_addresses,
            (IPv4Address("203.0.113.10"), IPv4Address("203.0.113.11")),
        )

    def test_interval_reset_clears_usage_and_counts_only_new_traffic(self) -> None:
        normal = Profile(id="normal", name="Normal")
        fallback = Profile(id="fallback", name="Fallback", download=Impairment(delay_ms=700))
        self.repository.save_profile(normal)
        self.repository.save_profile(fallback)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_data_limit(
            DataLimit(
                device_id="alice",
                profile_id="fallback",
                limit_bytes=100,
                reset_mode="interval",
                reset_interval_seconds=60,
                used_bytes=100,
                counter_bytes=10,
                last_reset_at=100,
            )
        )
        helper = CounterResettingHelper()
        helper.counters = {"devices": {"alice": 20}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler.activate(applied_payload={})

        # The reset lifts the fallback, and applying that restarts the counters.
        scheduler.tick(repository=self.repository, now=161)

        data_limit = self.repository.get_data_limit("alice")
        self.assertEqual(data_limit.used_bytes, 0)
        self.assertEqual(data_limit.counter_bytes, 0)
        self.assertEqual(helper.plans[-1].queues[1].impairment.delay_ms, 0)

        helper.counters["devices"]["alice"] = 30
        scheduler.tick(repository=self.repository, now=170)

        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 30)

    def test_time_quota_counts_active_traffic_inside_its_timer(self) -> None:
        normal = Profile(id="normal", name="Normal")
        fallback = Profile(id="fallback", name="Fallback", download=Impairment(delay_ms=700))
        self.repository.save_profile(normal)
        self.repository.save_profile(fallback)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_domain_observations(
            (
                DomainObservation(
                    ipv4_address="192.168.50.10",
                    domain="netflix.com",
                    query_count=1,
                    first_seen=100,
                    last_seen=100,
                ),
            ),
            now=100,
        )
        quota = TimeQuota(
            id="alice-netflix-time",
            device_id="alice",
            name="Netflix time",
            domains=("netflix.com",),
            profile_id="fallback",
            limit_seconds=60,
            window_duration_seconds=3_600,
            started_at=100,
            expires_at=1_000,
            last_sample_at=100,
        )
        self.repository.save_time_quota(quota)
        counter_id = time_quota_counter_id(quota.id)
        self.helper.counters = {"devices": {}, "services": {counter_id: 0}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        with patch(
            "jittersim.engine.runtime.resolve_domains",
            return_value=(IPv4Address("203.0.113.10"),),
        ):
            scheduler.tick(repository=self.repository, now=100)
            self.helper.counters["services"][counter_id] = 100
            scheduler.tick(repository=self.repository, now=160)

        self.assertEqual(self.repository.get_time_quota(quota.id).used_seconds, 60)
        self.assertTrue(self.helper.plans[-1].service_quota_rules[0].active)

        self.helper.counters["services"][counter_id] = 200
        with patch(
            "jittersim.engine.runtime.resolve_domains",
            return_value=(IPv4Address("203.0.113.10"),),
        ):
            scheduler.tick(repository=self.repository, now=1_001)
        self.assertFalse(self.helper.plans[-1].service_quota_rules[0].active)

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

    def test_edited_profile_values_apply_without_a_reset(self) -> None:
        self._save_device(Profile(id="slow", name="Slow", download=Impairment(delay_ms=100)))
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=self.repository, now=0)

        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=500))
        )
        scheduler.tick(repository=self.repository, now=10)

        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 500)

    def test_changing_a_group_profile_applies_the_new_values(self) -> None:
        for profile in (
            Profile(id="normal", name="Normal"),
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=400)),
            Profile(id="slower", name="Slower", download=Impairment(delay_ms=900)),
        ):
            self.repository.save_profile(profile)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        group = DeviceGroup(id="family", name="Family", device_ids=("alice",), profile_id="slow")
        self.repository.save_device_group(group)
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=self.repository, now=0)

        self.repository.save_device_group(
            DeviceGroup(id="family", name="Family", device_ids=("alice",), profile_id="slower")
        )
        scheduler.tick(repository=self.repository, now=10)

        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 900)

    def test_devices_with_the_longest_allowed_id_can_be_scheduled(self) -> None:
        device_id = "d" * 64
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device(device_id, "Long", "192.168.50.10", "slow"))
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        self.assertTrue(scheduler.tick(repository=self.repository, now=0))
        self.assertEqual(self.helper.plans[-1].mark_rules[0].device_id, device_id)

    def test_scheduler_thread_survives_an_unexpected_tick_error(self) -> None:
        scheduler = RuntimeScheduler(
            database_path=":memory:",
            helper_client=self.helper,
            poll_seconds=0.01,
        )
        calls = []
        ticked_again = threading.Event()

        def flaky_tick():
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("database is locked")
            ticked_again.set()
            return False

        scheduler.tick = flaky_tick
        try:
            with self.assertLogs("jittersim.engine.runtime", level="ERROR"):
                scheduler.start()
                self.assertTrue(ticked_again.wait(timeout=2))
        finally:
            scheduler.stop()

    def test_outage_checks_vary_when_rerolls_are_off(self) -> None:
        self._save_device(
            Profile(
                id="flaky",
                name="Flaky",
                outage_probability=50,
                outage_duration_seconds=1,
                outage_interval_seconds=1,
                random_seed=7,
            )
        )
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.build_plan(repository=self.repository, now=0)

        # Each build after the first runs one outage check.
        outcomes = {
            bool(
                scheduler.build_plan(
                    repository=self.repository, now=second
                ).blocked_device_addresses
            )
            for second in range(2, 80, 2)
        }

        self.assertEqual(outcomes, {True, False})

    def test_pause_waits_for_an_in_flight_scheduled_apply(self) -> None:
        applying = threading.Event()
        release = threading.Event()

        class BlockingHelper(RecordingHelper):
            def apply(self, *, plan):
                applying.set()
                release.wait(timeout=5)
                return super().apply(plan=plan)

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jittersim.db"
            with ConfigRepository(database_path) as repository:
                repository.save_profile(Profile(id="slow", name="Slow"))
                repository.save_device(Device("alice", "Alice", "192.168.50.10", "slow"))
            helper = BlockingHelper()
            scheduler = RuntimeScheduler(
                database_path=database_path,
                helper_client=helper,
                poll_seconds=3_600,
            )
            scheduler.activate(applied_payload={})
            tick = threading.Thread(target=lambda: scheduler.tick(now=0))
            paused = threading.Event()
            pause = threading.Thread(target=lambda: (scheduler.pause(), paused.set()))
            try:
                tick.start()
                self.assertTrue(applying.wait(timeout=5))
                pause.start()
                # A reset must not run while the scheduler is still applying.
                self.assertFalse(paused.wait(timeout=0.2))
            finally:
                release.set()
                tick.join(timeout=5)
                pause.join(timeout=5)
                scheduler.stop()

            self.assertTrue(paused.is_set())
            self.assertFalse(scheduler.tick(now=10))
            self.assertEqual(len(helper.plans), 1)

    def test_quota_usage_survives_plan_reapplies(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=900))
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_data_limit(
            DataLimit(device_id="alice", profile_id="slow", limit_bytes=10**9, last_reset_at=100)
        )
        helper = CounterResettingHelper()
        helper.counters = {"devices": {"alice": 1_000}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler.activate(applied_payload={})

        scheduler.tick(repository=self.repository, now=100)
        helper.counters["devices"]["alice"] = 600
        # A profile change reapplies the plan before the next regular sample.
        self.repository.save_profile(
            Profile(id="normal", name="Normal", download=Impairment(delay_ms=10))
        )
        scheduler.tick(repository=self.repository, now=102)
        helper.counters["devices"]["alice"] = 1_500
        scheduler.tick(repository=self.repository, now=110)

        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 3_100)

    def test_byte_progression_keeps_its_progress_after_a_manual_apply(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(
            Profile(id="degraded", name="Degraded", download=Impairment(delay_ms=1_000))
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_progression(
            Progression(
                id="byte-driven",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="degraded",
                started_at=100,
                duration_seconds=1_000,
                mode="bytes",
                traffic_target_bytes=1_000,
            )
        )
        helper = CounterResettingHelper()
        helper.counters = {"devices": {"alice": 0}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=self.repository, now=100)
        helper.counters["devices"]["alice"] = 500
        scheduler.tick(repository=self.repository, now=106)

        # Another change applied from the dashboard, such as a timer for bob.
        scheduler.activate(applied_payload={})
        helper.counters["devices"]["alice"] = 100
        scheduler.tick(repository=self.repository, now=112)

        self.assertEqual(scheduler.device_traffic_bytes(device_id="alice"), 600)

    def test_changing_group_members_does_not_charge_the_group_limit(self) -> None:
        for profile in (
            Profile(id="normal", name="Normal"),
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=900)),
        ):
            self.repository.save_profile(profile)
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device_group(
            DeviceGroup(id="family", name="Family", device_ids=("alice",))
        )
        self.repository.save_group_data_limit(
            GroupDataLimit(
                group_id="family", profile_id="slow", limit_bytes=10**9, last_reset_at=100
            )
        )
        helper = CounterResettingHelper()
        helper.counters = {"devices": {"alice": 100, "bob": 5_000}, "services": {}}
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=self.repository, now=100)

        # Bob joins with 3,000 bytes he used outside the group.
        helper.counters["devices"].update({"alice": 50, "bob": 3_000})
        self.repository.save_device_group(
            DeviceGroup(id="family", name="Family", device_ids=("alice", "bob"))
        )
        scheduler.tick(repository=self.repository, now=106)
        helper.counters["devices"].update({"alice": 60, "bob": 3_010})
        scheduler.tick(repository=self.repository, now=112)

        self.assertEqual(self.repository.get_group_data_limit("family").used_bytes, 120)

    def test_a_reset_made_outside_the_dashboard_stops_scheduled_applies(self) -> None:
        self._save_device(Profile(id="slow", name="Slow", download=Impairment(delay_ms=100)))
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=self.repository, now=0)

        # `jittersim reset` records the reset, then resets through the helper.
        self.repository.update_runtime_state(
            observed_state="reset",
            last_action="reset",
            last_result="success",
            last_error=None,
        )
        noticed = scheduler.tick(repository=self.repository, now=10)
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=900))
        )
        later = scheduler.tick(repository=self.repository, now=20)

        self.assertFalse(noticed)
        self.assertFalse(later)
        # The scheduler resets once more in case one of its applies raced the reset.
        self.assertEqual(self.helper.resets, 1)
        self.assertEqual(len(self.helper.plans), 1)
        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")

    def test_a_reset_recorded_during_a_scheduled_apply_is_kept(self) -> None:
        repository = self.repository

        class ResetDuringApplyHelper(RecordingHelper):
            def apply(self, *, plan):
                result = super().apply(plan=plan)
                # `jittersim reset` runs from another process meanwhile.
                repository.update_runtime_state(
                    observed_state="reset",
                    last_action="reset",
                    last_result="success",
                    last_error=None,
                )
                return result

        self._save_device(Profile(id="slow", name="Slow", download=Impairment(delay_ms=100)))
        helper = ResetDuringApplyHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler.activate(applied_payload={})

        self.assertFalse(scheduler.tick(repository=self.repository, now=0))
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=900))
        )
        scheduler.tick(repository=self.repository, now=10)

        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")
        self.assertEqual(helper.resets, 1)
        self.assertEqual(len(helper.plans), 1)


class EveryoneElseSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.scheduler = RuntimeScheduler(database_path=":memory:", helper_client=None)

    def test_no_everyone_else_profile_means_no_rule(self) -> None:
        plan = self.scheduler.build_plan(repository=self.repository, now=1_000)

        self.assertIsNone(plan.everyone_else)

    def test_everyone_else_gets_its_profile_and_the_unaffected_addresses(self) -> None:
        self.repository.save_profile(
            Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100))
        )
        self.repository.save_everyone_else_profile_id("slow")
        self.repository.save_unaffected_device(UnaffectedDevice("aa:aa:aa:aa:aa:aa"))

        plan = self.scheduler.build_plan(repository=self.repository, now=1_000)

        self.assertEqual(plan.everyone_else.upload_impairment, Impairment(delay_ms=100))
        self.assertEqual(plan.everyone_else.unaffected_macs, ("aa:aa:aa:aa:aa:aa",))
        self.assertFalse(plan.everyone_else.blocked)

    def test_everyone_else_follows_the_profile_outages(self) -> None:
        self.repository.save_profile(
            Profile(
                id="flaky",
                name="Flaky",
                outage_probability=100,
                outage_duration_seconds=60,
                outage_interval_seconds=10,
            )
        )
        self.repository.save_everyone_else_profile_id("flaky")

        first = self.scheduler.build_plan(repository=self.repository, now=1_000)
        later = self.scheduler.build_plan(repository=self.repository, now=1_011)

        self.assertFalse(first.everyone_else.blocked)
        self.assertTrue(later.everyone_else.blocked)

    def _start_an_everyone_else_outage(self) -> Profile:
        profile = Profile(
            id="flaky",
            name="Flaky",
            outage_probability=100,
            outage_duration_seconds=60,
            outage_interval_seconds=10,
        )
        self.repository.save_profile(profile)
        self.repository.save_everyone_else_profile_id("flaky")
        self.scheduler.build_plan(repository=self.repository, now=1_000)
        plan = self.scheduler.build_plan(repository=self.repository, now=1_011)
        self.assertTrue(plan.everyone_else.blocked)
        return profile

    def test_editing_the_everyone_else_profile_starts_a_fresh_roll(self) -> None:
        profile = self._start_an_everyone_else_outage()
        self.repository.save_profile(replace(profile, upload=Impairment(delay_ms=50)))

        plan = self.scheduler.build_plan(repository=self.repository, now=1_012)

        self.assertFalse(plan.everyone_else.blocked)
        self.assertEqual(plan.everyone_else.upload_impairment, Impairment(delay_ms=50))

    def test_pausing_forgets_the_everyone_else_roll(self) -> None:
        self._start_an_everyone_else_outage()
        self.scheduler.pause()

        plan = self.scheduler.build_plan(repository=self.repository, now=1_012)

        self.assertFalse(plan.everyone_else.blocked)

class TargetScheduleTests(unittest.TestCase):
    """Schedules for groups and everyone else."""

    # 21 September 2026 is a Monday.
    IN_WINDOW = datetime(2026, 9, 21, 10, 0).timestamp()
    OUT_OF_WINDOW = datetime(2026, 9, 21, 20, 0).timestamp()

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.scheduler = RuntimeScheduler(database_path=":memory:", helper_client=None)
        for profile_id, delay in (
            ("normal", 0), ("group", 100), ("own", 200), ("scheduled", 300), ("timed", 400)
        ):
            self.repository.save_profile(
                Profile(id=profile_id, name=profile_id, download=Impairment(delay_ms=delay))
            )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "own"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device(Device("carol", "Carol", "192.168.50.12", "own"))
        self.repository.save_device_group(
            DeviceGroup("family", "Family", ("alice", "bob"), profile_id="group")
        )

    def _schedule(self, schedule_id: str, profile_id: str = "scheduled", **target) -> None:
        self.repository.save_schedule(
            Schedule(
                id=schedule_id, profile_id=profile_id, days_of_week=(0,),
                start_minute=540, end_minute=1_020, **target,
            )
        )

    def _download_delays(self, now: float) -> dict:
        plan = self.scheduler.build_plan(repository=self.repository, now=now)
        return {
            queue.device_id: queue.impairment.delay_ms
            for queue in plan.queues
            if queue.direction == "download"
        }

    def test_a_group_schedule_applies_to_its_members_in_its_window(self) -> None:
        self._schedule("family-day", device_id=None, group_id="family")

        self.assertEqual(
            self._download_delays(self.IN_WINDOW), {"alice": 300, "bob": 300, "carol": 200}
        )
        # Outside the window the group profile applies again.
        self.assertEqual(
            self._download_delays(self.OUT_OF_WINDOW), {"alice": 100, "bob": 100, "carol": 200}
        )

    def test_a_device_schedule_wins_over_its_group_schedule(self) -> None:
        self._schedule("family-day", device_id=None, group_id="family")
        self._schedule("alice-day", profile_id="timed", device_id="alice")

        delays = self._download_delays(self.IN_WINDOW)

        self.assertEqual((delays["alice"], delays["bob"]), (400, 300))

    def test_a_timer_wins_over_a_group_schedule(self) -> None:
        self._schedule("family-day", device_id=None, group_id="family")
        self.repository.save_timer(
            Timer(
                id="short", device_id="bob", profile_id="timed",
                started_at=int(self.IN_WINDOW), expires_at=int(self.IN_WINDOW) + 600,
            )
        )

        delays = self._download_delays(self.IN_WINDOW)

        self.assertEqual((delays["alice"], delays["bob"]), (300, 400))

    def test_an_everyone_else_schedule_needs_no_saved_profile(self) -> None:
        self._schedule("others-day", device_id=None, everyone_else=True)

        during = self.scheduler.build_plan(repository=self.repository, now=self.IN_WINDOW)
        after = self.scheduler.build_plan(repository=self.repository, now=self.OUT_OF_WINDOW)

        self.assertEqual(during.everyone_else.download_impairment.delay_ms, 300)
        self.assertIsNone(after.everyone_else)

    def test_an_everyone_else_schedule_overrides_its_profile(self) -> None:
        self.repository.save_everyone_else_profile_id("group")
        self._schedule("others-day", device_id=None, everyone_else=True)

        during = self.scheduler.build_plan(repository=self.repository, now=self.IN_WINDOW)
        after = self.scheduler.build_plan(repository=self.repository, now=self.OUT_OF_WINDOW)

        self.assertEqual(during.everyone_else.download_impairment.delay_ms, 300)
        self.assertEqual(after.everyone_else.download_impairment.delay_ms, 100)


class ScenarioSchedulingTests(unittest.TestCase):
    # 21 September 2026 is a Monday; the schedules below cover 09:00 to 17:00.
    START = int(datetime(2026, 9, 21, 10, 0).timestamp())

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.scheduler = RuntimeScheduler(database_path=":memory:", helper_client=None)
        for profile_id, delay in (
            ("normal", 0), ("group", 100), ("own", 200), ("scheduled", 300),
            ("timed", 400), ("step-one", 500), ("step-two", 600),
        ):
            self.repository.save_profile(
                Profile(id=profile_id, name=profile_id, download=Impairment(delay_ms=delay))
            )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "own"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device(Device("carol", "Carol", "192.168.50.12", "own"))
        self.repository.save_device_group(
            DeviceGroup("family", "Family", ("alice", "bob"), profile_id="group")
        )
        self.save_scenario()

    def save_scenario(self, loop: bool = False) -> None:
        self.repository.save_scenario(
            Scenario(
                id="commute",
                name="Commute",
                steps=(ScenarioStep("step-one", 60), ScenarioStep("step-two", 30)),
                loop=loop,
            )
        )

    def start(self, **target) -> None:
        self.repository.start_scenario_run(
            ScenarioRun(id="run", scenario_id="commute", started_at=self.START, **target)
        )

    def delays(self, seconds: int) -> dict:
        plan = self.scheduler.build_plan(repository=self.repository, now=self.START + seconds)
        return {
            queue.device_id: queue.impairment.delay_ms
            for queue in plan.queues
            if queue.direction == "download"
        }

    def test_a_device_scenario_moves_through_its_steps_then_ends(self) -> None:
        self.start(device_id="carol")

        self.assertEqual(
            [self.delays(seconds)["carol"] for seconds in (10, 70, 100)], [500, 600, 200]
        )

    def test_a_repeating_scenario_starts_over(self) -> None:
        self.save_scenario(loop=True)
        self.start(device_id="carol")

        self.assertEqual(
            [self.delays(seconds)["carol"] for seconds in (10, 70, 95, 160)], [500, 600, 500, 600]
        )

    def test_a_group_scenario_reaches_its_members_over_their_schedules(self) -> None:
        self.repository.save_schedule(
            Schedule(
                id="alice-day", device_id="alice", profile_id="scheduled",
                days_of_week=(0,), start_minute=540, end_minute=1_020,
            )
        )
        self.start(group_id="family")

        self.assertEqual(self.delays(10), {"alice": 500, "bob": 500, "carol": 200})
        # Once it ends, alice's own schedule applies again.
        self.assertEqual(self.delays(100)["alice"], 300)

    def test_a_device_timer_or_scenario_wins_over_its_group_scenario(self) -> None:
        self.start(group_id="family")
        self.repository.save_timer(
            Timer(
                id="short", device_id="bob", profile_id="timed",
                started_at=self.START, expires_at=self.START + 600,
            )
        )
        self.repository.start_scenario_run(
            ScenarioRun(id="alice-run", scenario_id="commute", started_at=self.START + 60,
                        device_id="alice")
        )

        # Alice's own run is at its first step while the group's is at its second.
        self.assertEqual(self.delays(70), {"alice": 500, "bob": 400, "carol": 200})

    def test_a_data_limit_wins_over_a_scenario(self) -> None:
        self.start(device_id="alice")
        self.repository.save_data_limit(
            DataLimit(device_id="alice", profile_id="timed", limit_bytes=100, used_bytes=100)
        )

        self.assertEqual(self.delays(10)["alice"], 400)

    def test_a_group_data_limit_wins_over_a_members_scenario(self) -> None:
        self.start(device_id="alice")
        self.repository.save_group_data_limit(
            GroupDataLimit(group_id="family", profile_id="timed", limit_bytes=100, used_bytes=100)
        )

        self.assertEqual(self.delays(10)["alice"], 400)

    def test_an_everyone_else_scenario_needs_no_saved_profile(self) -> None:
        self.start(everyone_else=True)

        during = self.scheduler.build_plan(repository=self.repository, now=self.START + 70)
        after = self.scheduler.build_plan(repository=self.repository, now=self.START + 100)

        self.assertEqual(during.everyone_else.download_impairment.delay_ms, 600)
        self.assertIsNone(after.everyone_else)

    def test_an_everyone_else_scenario_wins_over_its_schedule(self) -> None:
        self.repository.save_everyone_else_profile_id("normal")
        self.repository.save_schedule(
            Schedule(
                id="others-day", device_id=None, everyone_else=True, profile_id="scheduled",
                days_of_week=(0,), start_minute=540, end_minute=1_020,
            )
        )
        self.start(everyone_else=True)

        during = self.scheduler.build_plan(repository=self.repository, now=self.START + 10)
        after = self.scheduler.build_plan(repository=self.repository, now=self.START + 100)

        self.assertEqual(during.everyone_else.download_impairment.delay_ms, 500)
        self.assertEqual(after.everyone_else.download_impairment.delay_ms, 300)


class ActivityRecordingTests(unittest.TestCase):
    # 21 September 2026 is a Monday; work-hours runs from 09:00 to 17:00.
    BEFORE = datetime(2026, 9, 21, 8, 59).timestamp()
    DURING = datetime(2026, 9, 21, 9, 0).timestamp()
    AFTER = datetime(2026, 9, 21, 17, 0).timestamp()

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=300))
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.helper = RecordingHelper()
        self.scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        self.scheduler.activate(applied_payload={})

    def schedule(self, **target) -> None:
        target = target or {"device_id": "alice"}
        self.repository.save_schedule(
            Schedule(
                id="work-hours", profile_id="slow", days_of_week=(0,),
                start_minute=540, end_minute=1_020, **target,
            )
        )

    def messages(self) -> list:
        return [event["message"] for event in reversed(self.repository.list_activity())]

    def tick(self, now: float) -> None:
        self.scheduler.tick(repository=self.repository, now=now)

    def test_schedule_switches_are_recorded_as_they_apply(self) -> None:
        self.schedule()

        for now in (self.BEFORE, self.DURING, self.AFTER):
            self.tick(now)

        self.assertEqual(
            self.messages(),
            [
                "Alice: schedule work-hours started, now Slow",
                "Alice: schedule work-hours ended, back to Normal",
            ],
        )
        self.assertEqual(
            [event["kind"] for event in self.repository.list_activity()], ["schedule", "schedule"]
        )

    def test_previews_and_failed_applies_record_nothing(self) -> None:
        self.schedule()
        self.tick(self.BEFORE)
        self.scheduler.build_plan(repository=self.repository, now=self.DURING)

        def fail(*, plan):
            raise HelperClientError("simulated apply failure")

        self.helper.apply = fail
        with self.assertLogs("jittersim.engine.runtime", "ERROR"):
            self.tick(self.DURING)
        failed = self.messages()
        del self.helper.apply
        self.tick(self.DURING + 1)

        self.assertEqual(failed, [])
        self.assertEqual(self.messages(), ["Alice: schedule work-hours started, now Slow"])

    def test_a_manual_apply_records_activity_without_waiting_for_a_tick(self) -> None:
        self.tick(self.BEFORE)
        self.repository.save_timer(
            Timer(
                id="short", device_id="alice", profile_id="slow",
                started_at=int(self.BEFORE), expires_at=int(self.BEFORE) + 600,
            )
        )
        prepared = self.scheduler.prepare_plan(repository=self.repository, now=self.BEFORE + 1)
        self.helper.apply(plan=prepared.plan)
        self.scheduler.activate_plan(repository=self.repository, prepared=prepared)
        applied = len(self.helper.plans)
        self.assertEqual(self.messages(), ["Alice: timer started, now Slow"])

        self.tick(self.BEFORE + 2)

        self.assertEqual(len(self.helper.plans), applied)
        self.assertEqual(self.messages(), ["Alice: timer started, now Slow"])

    def test_the_first_tick_after_a_reset_is_quiet(self) -> None:
        self.schedule()
        self.tick(self.BEFORE)
        self.scheduler.pause()
        self.scheduler.activate(applied_payload={})

        self.tick(self.DURING)

        self.assertEqual(self.messages(), [])

    def test_outages_are_recorded(self) -> None:
        self.repository.save_profile(
            Profile(
                id="flaky", name="Flaky", outage_probability=100,
                outage_duration_seconds=5, outage_interval_seconds=10,
            )
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "flaky"))

        for now in (1_000, 1_011, 1_017):
            self.tick(now)

        self.assertEqual(self.messages(), ["Alice: outage started", "Alice: outage ended"])

    def test_a_reached_data_limit_is_recorded(self) -> None:
        self.tick(self.BEFORE)
        self.repository.save_data_limit(
            DataLimit(device_id="alice", profile_id="slow", limit_bytes=100, used_bytes=100)
        )

        self.tick(self.BEFORE + 1)

        self.assertEqual(self.messages(), ["Alice reached its data limit, now Slow"])

    def test_everyone_else_switches_are_recorded(self) -> None:
        self.schedule(device_id=None, everyone_else=True)

        for now in (self.BEFORE, self.DURING, self.AFTER):
            self.tick(now)

        self.assertEqual(
            self.messages(),
            [
                "Everyone else: schedule work-hours started, now Slow",
                "Everyone else: schedule work-hours ended, back to a normal connection",
            ],
        )

    def test_a_reset_outside_the_dashboard_is_recorded_once(self) -> None:
        self.tick(self.BEFORE)
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None
        )

        self.tick(self.BEFORE + 1)
        self.tick(self.BEFORE + 2)

        self.assertEqual(self.messages(), ["Network reset outside the dashboard"])
        self.assertEqual(self.repository.list_activity()[0]["kind"], "reset")

    def test_an_outside_reset_that_cannot_be_repeated_says_so(self) -> None:
        self.tick(self.BEFORE)
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None
        )

        def fail():
            raise HelperClientError("helper is busy")

        self.helper.reset = fail
        with self.assertLogs("jittersim.engine.runtime", "WARNING"):
            self.tick(self.BEFORE + 1)

        self.assertEqual(
            self.messages(), ["Network reset requested outside the dashboard; repeating it failed"]
        )

    def start_timer(self, timer_id: str, profile_id: str = "slow") -> None:
        self.repository.save_timer(
            Timer(
                id=timer_id, device_id="alice", profile_id=profile_id,
                started_at=int(self.BEFORE), expires_at=int(self.BEFORE) + 600,
            )
        )

    def test_a_timer_that_replaces_a_timer_is_recorded(self) -> None:
        self.start_timer("first")
        self.tick(self.BEFORE)

        # Same profile, so the plan doesn't even change.
        self.start_timer("second")
        self.tick(self.BEFORE + 1)

        self.assertEqual(self.messages(), ["Alice: timer started, now Slow"])

    def save_commute(self, name: str = "Commute") -> None:
        self.repository.save_scenario(
            Scenario(
                id="commute", name=name,
                steps=(ScenarioStep("slow", 60), ScenarioStep("normal", 60)),
            ),
            now=self.BEFORE,
        )

    def test_renaming_a_running_scenario_is_not_recorded(self) -> None:
        self.save_commute()
        self.repository.start_scenario_run(
            ScenarioRun(id="run", scenario_id="commute", started_at=int(self.BEFORE),
                        device_id="alice")
        )
        self.tick(self.BEFORE)

        self.save_commute("Rush hour")
        self.tick(self.BEFORE + 1)

        self.assertEqual(self.messages(), [])

    def test_a_group_scenario_step_is_one_event(self) -> None:
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice", "bob")))
        self.save_commute()
        self.repository.start_scenario_run(
            ScenarioRun(id="run", scenario_id="commute", started_at=int(self.BEFORE),
                        group_id="family")
        )

        self.tick(self.BEFORE)
        self.tick(self.BEFORE + 61)

        self.assertEqual(
            self.messages(), ["Group Family: scenario Commute, step 2 of 2, now Normal"]
        )

    def test_an_everyone_else_scenario_is_recorded(self) -> None:
        self.save_commute()
        self.tick(self.BEFORE)
        self.repository.start_scenario_run(
            ScenarioRun(id="run", scenario_id="commute", started_at=int(self.BEFORE),
                        everyone_else=True)
        )

        self.tick(self.BEFORE + 1)

        self.assertEqual(
            self.messages(), ["Everyone else: scenario Commute, step 1 of 2, now Slow"]
        )

    def test_a_progression_is_recorded_as_it_starts_and_ends(self) -> None:
        self.tick(self.BEFORE)
        self.repository.save_progression(
            Progression(
                id="degrade", device_id="alice", start_profile_id="normal",
                end_profile_id="slow", started_at=int(self.BEFORE), duration_seconds=60,
            )
        )

        for seconds in (1, 30, 59, 61):
            self.tick(self.BEFORE + seconds)

        self.assertEqual(
            self.messages(),
            [
                "Alice: progression started, from Normal to Slow",
                "Alice: progression ended, back to Normal",
            ],
        )

    def test_a_shared_data_limit_is_one_event_for_the_group(self) -> None:
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice", "bob")))
        self.tick(self.BEFORE)
        self.repository.save_group_data_limit(
            GroupDataLimit(group_id="family", profile_id="slow", limit_bytes=100, used_bytes=100)
        )

        self.tick(self.BEFORE + 1)

        self.assertEqual(self.messages(), ["Group Family reached its shared data limit, now Slow"])

    def test_service_limits_are_recorded(self) -> None:
        self.repository.save_service_quota(
            ServiceQuota(
                id="alice-video", device_id="alice", name="Video", domains=("example.com",),
                profile_id="slow", limit_bytes=100, used_bytes=100,
            )
        )
        with patch(
            "jittersim.engine.runtime.resolve_domains",
            return_value=(IPv4Address("203.0.113.10"),),
        ):
            self.tick(self.BEFORE)
            self.repository.reset_service_quota_usage("alice-video", counter_bytes=0, reset_at=1)
            self.tick(self.BEFORE + 1)

        self.assertEqual(self.messages(), ["Alice: service limit Video lifted"])

    def test_a_storage_error_never_breaks_a_tick(self) -> None:
        self.schedule()
        self.tick(self.BEFORE)

        with patch.object(
            self.repository, "record_activities", side_effect=StorageError("disk full")
        ):
            with self.assertLogs("jittersim.engine.runtime", "WARNING"):
                applied = self.scheduler.tick(repository=self.repository, now=self.DURING)

        self.assertTrue(applied)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 300)


class DeviceAddressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(
            Device("phone", "Phone", "192.168.50.10", "slow", "aa:aa:aa:aa:aa:01")
        )
        self.helper = ClientListingHelper()
        self.scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.helper, poll_seconds=3_600
        )
        self.addCleanup(self.scheduler.stop)
        self.scheduler.activate(applied_payload={})

    def connect(self, *clients, neighbor_state: str = "reachable") -> None:
        # The real client refuses a snapshot whose clients disagree with the
        # association status.
        verified = self.helper.status_available
        self.helper.clients = [
            {
                "mac_address": mac,
                "ipv4_address": address,
                "state": "connected" if verified else "unknown",
                "association_verified": verified,
                "neighbor_state": neighbor_state,
            }
            for mac, address in clients
        ]

    def address(self, device_id: str) -> str:
        return str(self.repository.get_device(device_id).ipv4_address)

    def check(self, now: float = 100) -> None:
        self.scheduler.tick(repository=self.repository, now=now)

    def test_a_saved_device_follows_its_mac_address(self) -> None:
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))

        self.check()

        self.assertEqual(self.address("phone"), "192.168.50.23")
        # The plan applied in the same tick already uses the new address.
        self.assertEqual(str(self.helper.plans[-1].mark_rules[0].ipv4_address), "192.168.50.23")

    def test_only_a_confirmed_address_moves_a_device(self) -> None:
        # Without a reachable neighbour entry the helper guesses from the DHCP
        # lease or an old entry, which would move a device back and forth.
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"), neighbor_state="stale")
        self.check(100)
        stayed = self.address("phone")
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))
        self.check(131)

        self.assertEqual((stayed, self.address("phone")), ("192.168.50.10", "192.168.50.23"))

    def test_an_address_another_saved_device_keeps_is_left_alone(self) -> None:
        self.repository.save_device(Device("tablet", "Tablet", "192.168.50.23", "slow"))
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))

        with self.assertLogs("jittersim.engine.runtime", "WARNING") as logs:
            self.check(100)
            self.check(131)

        self.assertEqual(self.address("phone"), "192.168.50.10")
        self.assertEqual(self.address("tablet"), "192.168.50.23")
        # The tick still applied its plan, and the blocked move was logged once.
        self.assertEqual(len(self.helper.plans), 1)
        self.assertEqual(len([line for line in logs.output if "not moving" in line]), 1)
        self.assertEqual(
            [event["message"] for event in self.repository.list_activity()],
            ["Phone now uses 192.168.50.23, which Tablet keeps, so it stays at 192.168.50.10"],
        )

    def test_a_blocked_move_is_recorded_once_while_the_device_comes_and_goes(self) -> None:
        self.repository.save_device(Device("tablet", "Tablet", "192.168.50.23", "slow"))

        with self.assertLogs("jittersim.engine.runtime", "WARNING"):
            for now, state in ((100, "reachable"), (131, "stale"), (162, "reachable")):
                self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"), neighbor_state=state)
                self.check(now)

        self.assertEqual(len(self.repository.list_activity()), 1)

    def test_a_move_is_recorded(self) -> None:
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))

        self.check()

        self.assertEqual(
            [(event["kind"], event["message"]) for event in self.repository.list_activity()],
            [("address", "Phone moved from 192.168.50.10 to 192.168.50.23")],
        )

    def test_devices_that_swap_addresses_both_move(self) -> None:
        self.repository.save_device(
            Device("tablet", "Tablet", "192.168.50.23", "slow", "aa:aa:aa:aa:aa:02")
        )
        self.connect(
            ("aa:aa:aa:aa:aa:01", "192.168.50.23"), ("aa:aa:aa:aa:aa:02", "192.168.50.10")
        )

        self.check()

        self.assertEqual((self.address("phone"), self.address("tablet")),
                         ("192.168.50.23", "192.168.50.10"))

    def test_a_chain_of_moves_completes_in_one_check(self) -> None:
        # The tablet takes the address the phone leaves, whatever their order.
        self.repository.save_device(
            Device("a-tablet", "Tablet", "192.168.50.11", "slow", "aa:aa:aa:aa:aa:02")
        )
        self.connect(
            ("aa:aa:aa:aa:aa:01", "192.168.50.30"), ("aa:aa:aa:aa:aa:02", "192.168.50.10")
        )

        self.check()

        self.assertEqual((self.address("phone"), self.address("a-tablet")),
                         ("192.168.50.30", "192.168.50.10"))

    def test_two_devices_claiming_one_address_both_stay(self) -> None:
        self.repository.save_device(
            Device("tablet", "Tablet", "192.168.50.11", "slow", "aa:aa:aa:aa:aa:02")
        )
        self.connect(
            ("aa:aa:aa:aa:aa:01", "192.168.50.30"), ("aa:aa:aa:aa:aa:02", "192.168.50.30")
        )

        with self.assertLogs("jittersim.engine.runtime", "WARNING"):
            self.check()

        self.assertEqual((self.address("phone"), self.address("tablet")),
                         ("192.168.50.10", "192.168.50.11"))

    def test_a_blocked_move_blocks_the_move_into_the_address_it_keeps(self) -> None:
        self.repository.save_device(Device("tablet", "Tablet", "192.168.50.23", "slow"))
        self.repository.save_device(
            Device("watch", "Watch", "192.168.50.12", "slow", "aa:aa:aa:aa:aa:03")
        )
        # The phone can't have .23, so it keeps .10, which the watch wanted.
        self.connect(
            ("aa:aa:aa:aa:aa:01", "192.168.50.23"), ("aa:aa:aa:aa:aa:03", "192.168.50.10")
        )

        with self.assertLogs("jittersim.engine.runtime", "WARNING"):
            self.check()

        self.assertEqual((self.address("phone"), self.address("watch")),
                         ("192.168.50.10", "192.168.50.12"))
        self.assertEqual(len(self.helper.plans), 1)

    def test_a_client_on_an_address_a_device_cannot_use_is_ignored(self) -> None:
        self.connect(("aa:aa:aa:aa:aa:01", "169.254.10.20"))

        self.check()

        self.assertEqual(self.address("phone"), "192.168.50.10")
        self.assertEqual(len(self.helper.plans), 1)

    def test_nothing_moves_without_association_status(self) -> None:
        self.repository.save_device(Device("laptop", "Laptop", "192.168.50.11", "slow"))
        self.helper.status_available = False
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))

        self.check()

        self.assertEqual(self.address("phone"), "192.168.50.10")
        self.assertEqual(self.address("laptop"), "192.168.50.11")

    def test_nothing_moves_while_the_network_is_reset(self) -> None:
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))
        self.scheduler.pause()

        self.check()

        self.assertEqual(self.address("phone"), "192.168.50.10")
        self.assertEqual(self.helper.snapshots, 0)

    def test_a_move_keeps_a_traffic_progression_going(self) -> None:
        self.repository.save_profile(Profile(id="fast", name="Fast"))
        self.repository.save_domain_observations(
            (
                DomainObservation(
                    ipv4_address="192.168.50.10", domain="example.com", query_count=500,
                    first_seen=90, last_seen=90,
                ),
            ),
            now=90,
        )
        self.repository.save_progression(
            Progression(
                id="degrade", device_id="phone", start_profile_id="fast",
                end_profile_id="slow", started_at=90, duration_seconds=600, mode="traffic",
                traffic_target_queries=200, traffic_baseline_queries=400,
            )
        )
        self.connect(("aa:aa:aa:aa:aa:01", "192.168.50.23"))

        self.check()

        progression = self.repository.list_active_progressions(now=100)[0]
        self.assertEqual(self.address("phone"), "192.168.50.23")
        self.assertEqual(progression.traffic_baseline_queries, -100)

    def test_addresses_are_checked_every_thirty_seconds(self) -> None:
        self.connect()

        for now in (100, 110, 131):
            self.scheduler.tick(repository=self.repository, now=now)

        self.assertEqual(self.helper.snapshots, 2)


if __name__ == "__main__":
    unittest.main()
