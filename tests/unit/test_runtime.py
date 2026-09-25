import tempfile
import threading
import unittest
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
    Schedule,
    ServiceQuota,
    TimeQuota,
    Timer,
    UnaffectedDevice,
    time_quota_counter_id,
)
from jittersim.config.storage import ConfigRepository
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

    def test_failed_profile_apply_keeps_last_successful_bandwidth_reference(self) -> None:
        class FailingApplyHelper(RecordingHelper):
            def apply(self, *, plan):
                raise HelperClientError("simulated apply failure")

        original = Profile(
            id="normal",
            name="Normal",
            download=Impairment(bandwidth_kbit=100_000),
        )
        self._save_device(original)
        scheduler = RuntimeScheduler(
            database_path=":memory:",
            helper_client=FailingApplyHelper(),
        )
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
        self.repository.save_profile(
            Profile(
                id="normal",
                name="Normal updated",
                download=Impairment(bandwidth_kbit=20_000),
            )
        )

        self.assertFalse(scheduler.tick(repository=self.repository))
        self.assertEqual(
            scheduler.applied_queue_bandwidth_limits()["alice"]["download_kbit"],
            100_000,
        )

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

if __name__ == "__main__":
    unittest.main()
