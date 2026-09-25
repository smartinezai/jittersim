import sqlite3
import tempfile
import unittest
from pathlib import Path

from jittersim.config.models import (
    MAX_UNAFFECTED_DEVICES,
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    SavedSettings,
    Schedule,
    ServiceAssignment,
    ServiceQuota,
    TimeQuota,
    Timer,
    UnaffectedDevice,
    WebsitePolicy,
)
from jittersim.config.storage import ConfigRepository, StorageError


class ConfigRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")

    def tearDown(self) -> None:
        self.repository.close()

    def test_round_trips_profiles_and_devices(self) -> None:
        profile = Profile(
            id="terrible-wifi",
            name="Alice's terrible Wi-Fi",
            upload=Impairment(delay_ms=3_000, loss_percent=10),
            download=Impairment(bandwidth_kbit=2_000),
            random_seed=42,
            reroll_interval_seconds=300,
            reroll_interval_randomness=Randomness(variation_percent=20),
            website_mode="allow_only",
            website_domains=("example.com",),
        )
        device = Device(
            id="alice-laptop",
            name="Alice's laptop",
            ipv4_address="192.168.50.10",
            profile_id=profile.id,
            mac_address="aa:bb:cc:dd:ee:ff",
        )

        self.repository.save_profile(profile)
        self.repository.save_device(device)

        self.assertEqual(self.repository.get_profile(profile.id), profile)
        self.assertEqual(self.repository.get_device(device.id), device)

    def test_traffic_rate_settings_default_save_and_preserve_network_version(self) -> None:
        version_before = self.repository.get_runtime_state().configuration_version
        self.assertEqual(
            self.repository.get_traffic_rate_settings(),
            {"upload_kbit": None, "download_kbit": 100_000},
        )

        saved = self.repository.save_traffic_rate_settings(
            upload_kbit=20_000,
            download_kbit=150_000,
        )

        self.assertEqual(
            saved,
            {"upload_kbit": 20_000, "download_kbit": 150_000},
        )
        self.assertEqual(self.repository.get_traffic_rate_settings(), saved)
        self.assertEqual(self.repository.get_runtime_state().configuration_version, version_before)

    def test_traffic_rate_settings_reopen_keeps_saved_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = f"{directory}/settings.db"
            first = ConfigRepository(database)
            first.save_traffic_rate_settings(upload_kbit=None, download_kbit=150_000)
            first.close()

            reopened = ConfigRepository(database)
            try:
                self.assertEqual(
                    reopened.get_traffic_rate_settings(),
                    {"upload_kbit": None, "download_kbit": 150_000},
                )
            finally:
                reopened.close()

    def test_traffic_rate_settings_upgrade_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = f"{directory}/settings.db"
            original = ConfigRepository(database)
            original._connection.execute("DROP TABLE traffic_rate_settings")
            original._connection.commit()
            original.close()

            upgraded = ConfigRepository(database)
            self.assertEqual(
                upgraded.get_traffic_rate_settings(),
                {"upload_kbit": None, "download_kbit": 100_000},
            )
            saved = upgraded.save_traffic_rate_settings(
                upload_kbit=10_000,
                download_kbit=150_000,
            )
            upgraded.close()

            reopened = ConfigRepository(database)
            try:
                self.assertEqual(reopened.get_traffic_rate_settings(), saved)
            finally:
                reopened.close()

    def test_traffic_rate_settings_reject_invalid_values(self) -> None:
        for invalid in (True, 99, 101, 1_000_100, 100.0, float("inf"), "100000"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(StorageError):
                    self.repository.save_traffic_rate_settings(
                        upload_kbit=invalid,
                        download_kbit=100_000,
                    )

    def test_connected_device_labels_persist_without_changing_network_version(self) -> None:
        version_before = self.repository.get_runtime_state().configuration_version
        self.repository.save_connected_device_label(
            mac_address="DC:1E:D5:3E:97:4C",
            display_name="Family MacBook",
            device_type="laptop",
        )
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c",
            display_name="Work laptop",
            device_type="desktop",
        )

        self.assertEqual(
            self.repository.list_connected_device_labels(),
            {
                "dc:1e:d5:3e:97:4c": {
                    "display_name": "Work laptop",
                    "device_type": "desktop",
                }
            },
        )
        self.assertEqual(self.repository.get_runtime_state().configuration_version, version_before)
        self.repository.delete_connected_device_label(mac_address="DC:1E:D5:3E:97:4C")
        self.assertEqual(self.repository.list_connected_device_labels(), {})

    def test_connected_device_labels_reject_invalid_values(self) -> None:
        with self.assertRaisesRegex(StorageError, "label is invalid"):
            self.repository.save_connected_device_label(
                mac_address="not-a-mac",
                display_name="Guest",
                device_type="phone",
            )

    def test_connected_device_labels_have_a_bounded_count(self) -> None:
        for index in range(256):
            self.repository.save_connected_device_label(
                mac_address=f"02:00:00:00:{index // 256:02x}:{index % 256:02x}",
                display_name=f"Device {index}",
                device_type="unknown",
            )

        with self.assertRaisesRegex(StorageError, "label limit reached"):
            self.repository.save_connected_device_label(
                mac_address="02:00:00:00:01:00",
                display_name="Next device",
                device_type="unknown",
            )

    def test_browser_service_assignment_survives_reopen_and_can_be_removed(self) -> None:
        assignment = ServiceAssignment(
            id="video",
            device_id="alice",
            name="Video",
            domains=("youtube.com", "netflix.com"),
            profile_id="slow",
        )
        with tempfile.TemporaryDirectory() as directory:
            database = f"{directory}/settings.db"
            first = ConfigRepository(database)
            try:
                first.save_profile(Profile(id="slow", name="Slow"))
                first.save_device(Device("alice", "Alice", "192.168.50.10", "slow"))
                first.save_service_assignment(assignment)
            finally:
                first.close()
            reopened = ConfigRepository(database)
            try:
                self.assertEqual(reopened.list_service_assignments(), (assignment,))
                self.assertTrue(reopened.delete_service_assignment("video"))
                self.assertEqual(reopened.list_service_assignments(), ())
            finally:
                reopened.close()

    def test_round_trips_schedules_and_network_policies(self) -> None:
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(
            Device("alice", "Alice", "192.168.50.10", "slow")
        )
        schedule = Schedule(
            id="work-hours",
            device_id="alice",
            profile_id="slow",
            days_of_week=(0, 1, 2, 3, 4),
            start_minute=540,
            end_minute=1_020,
        )
        self.repository.save_schedule(schedule)
        self.repository.save_mac_policy(
            MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",))
        )
        self.repository.save_website_policy(
            WebsitePolicy(mode="allow_only", allowed_domains=("example.com",))
        )

        self.assertEqual(self.repository.list_schedules(), (schedule,))
        self.assertEqual(
            self.repository.get_mac_policy().denylist,
            ("aa:bb:cc:dd:ee:ff",),
        )
        self.assertEqual(
            self.repository.get_website_policy().allowed_domains,
            ("example.com",),
        )

    def test_round_trips_active_timers_and_can_cancel_them(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        timer = Timer(
            id="ten-minutes",
            device_id="alice",
            profile_id="slow",
            started_at=100,
            expires_at=700,
        )

        self.repository.save_timer(timer)

        self.assertEqual(self.repository.list_active_timers(now=100), (timer,))
        self.assertEqual(self.repository.list_active_timers(now=700), ())
        replacement = Timer(
            id="replacement",
            device_id="alice",
            profile_id="normal",
            started_at=200,
            expires_at=800,
        )
        self.repository.save_timer(replacement)
        self.assertEqual(self.repository.list_active_timers(now=300), (replacement,))
        self.assertFalse(self.repository.delete_timer(timer.id))
        self.assertTrue(self.repository.delete_timer(replacement.id))
        self.assertEqual(self.repository.list_active_timers(now=200), ())
        self.assertFalse(self.repository.delete_timer(replacement.id))

    def test_round_trips_progressions_and_counts_observed_queries(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_domain_observations(
            (
                DomainObservation(
                    ipv4_address="192.168.50.10",
                    domain="example.com",
                    query_count=7,
                    first_seen=100,
                    last_seen=100,
                ),
            ),
            now=100,
        )
        progression = Progression(
            id="degrade",
            device_id="alice",
            start_profile_id="normal",
            end_profile_id="slow",
            started_at=100,
            duration_seconds=600,
            website_domains=("youtube.com",),
        )

        self.repository.save_progression(progression)

        self.assertEqual(self.repository.list_active_progressions(now=200), (progression,))
        self.assertEqual(self.repository.total_domain_queries("192.168.50.10"), 7)
        self.assertTrue(self.repository.delete_progression(progression.id))
        self.assertEqual(self.repository.list_active_progressions(now=200), ())

    def test_timer_and_progression_replace_each_other_for_a_device(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        progression = Progression(
            id="degrade",
            device_id="alice",
            start_profile_id="normal",
            end_profile_id="slow",
            started_at=100,
            duration_seconds=600,
        )
        timer = Timer(
            id="offline",
            device_id="alice",
            profile_id="slow",
            started_at=200,
            expires_at=800,
        )

        self.repository.save_progression(progression)
        self.repository.save_timer(timer)

        # The newest override wins, so the timer listed as active is the one applied.
        self.assertEqual(self.repository.list_active_progressions(now=300), ())
        self.assertEqual(self.repository.list_active_timers(now=300), (timer,))

        self.repository.save_progression(progression)

        self.assertEqual(self.repository.list_active_progressions(now=300), (progression,))
        self.assertEqual(self.repository.list_active_timers(now=300), ())

    def test_query_totals_keep_counting_after_old_observations_are_pruned(self) -> None:
        day = 24 * 60 * 60
        self.repository.save_domain_observations(
            (DomainObservation("192.168.50.10", "old.example", 7, 100, 100),),
            now=100,
        )
        # Saving 31 days later prunes the first row.
        later = 100 + 31 * day
        self.repository.save_domain_observations(
            (DomainObservation("192.168.50.10", "new.example", 3, later, later),),
            now=later,
        )

        self.assertEqual(
            [observation.domain for observation in self.repository.list_domain_observations()],
            ["new.example"],
        )
        self.assertEqual(self.repository.total_domain_queries("192.168.50.10"), 10)

    def test_query_totals_start_from_existing_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/jittersim.db"
            with ConfigRepository(path) as repository:
                repository.save_domain_observations(
                    (DomainObservation("192.168.50.10", "example.com", 7, 100, 100),),
                    now=100,
                )
                # Simulate a database written before running totals existed.
                repository._connection.execute("DROP TABLE domain_query_totals")
                repository._connection.commit()

            with ConfigRepository(path) as repository:
                self.assertEqual(repository.total_domain_queries("192.168.50.10"), 7)

    def _save_lab(self) -> None:
        for profile in (
            Profile(id="normal", name="Normal"),
            Profile(id="slow", name="Slow"),
            Profile(id="unused", name="Unused"),
        ):
            self.repository.save_profile(profile)
        self.repository.save_device(Device("alice", "Alice's laptop", "192.168.50.10", "normal"))
        self.repository.save_device(Device("bob", "Bob's phone", "192.168.50.11", "normal"))
        self.repository.save_device_group(
            DeviceGroup(id="family", name="Family", device_ids=("alice", "bob"))
        )
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
        self.repository.save_data_limit(
            DataLimit(device_id="alice", profile_id="slow", limit_bytes=500, last_reset_at=100)
        )
        self.repository.save_group_data_limit(
            GroupDataLimit(group_id="family", profile_id="slow", limit_bytes=500, last_reset_at=100)
        )
        self.repository.save_service_quota(
            ServiceQuota(
                id="alice-video",
                device_id="alice",
                name="Video",
                domains=("netflix.com",),
                profile_id="slow",
                limit_bytes=500,
                last_reset_at=100,
            )
        )
        self.repository.save_time_quota(
            TimeQuota(
                id="alice-video-time",
                device_id="alice",
                name="Video time",
                domains=("netflix.com",),
                profile_id="slow",
                limit_seconds=300,
                window_duration_seconds=3_600,
                last_reset_at=100,
            )
        )

    def test_profile_references_name_everything_that_uses_a_profile(self) -> None:
        self._save_lab()

        self.assertEqual(
            self.repository.profile_references("slow", now=200),
            (
                "schedule work-hours",
                "data limit for Alice's laptop",
                "shared limit for Family",
                "service limit Video",
                "time limit Video time",
            ),
        )
        self.assertEqual(
            self.repository.profile_references("normal", now=200),
            ("device Alice's laptop", "device Bob's phone"),
        )
        self.assertEqual(self.repository.profile_references("unused", now=200), ())

    def test_deleting_a_device_removes_its_rules(self) -> None:
        self._save_lab()

        self.assertTrue(self.repository.delete_device("alice"))

        self.assertIsNone(self.repository.get_device("alice"))
        self.assertEqual(self.repository.list_schedules(), ())
        self.assertIsNone(self.repository.get_data_limit("alice"))
        self.assertEqual(self.repository.list_service_quotas(), ())
        self.assertEqual(self.repository.list_time_quotas(), ())
        self.assertEqual(self.repository.get_device_group("family").device_ids, ("bob",))
        self.assertFalse(self.repository.delete_device("alice"))

    def test_deleting_a_groups_last_device_removes_the_group(self) -> None:
        self._save_lab()
        self.repository.save_device(Device("carol", "Carol", "192.168.50.12", "normal"))
        self.repository.save_device_group(
            DeviceGroup(id="solo", name="Solo", device_ids=("carol",))
        )
        self.repository.save_group_data_limit(
            GroupDataLimit(group_id="solo", profile_id="slow", limit_bytes=500, last_reset_at=100)
        )

        self.repository.delete_device("carol")

        # A group cannot be empty, so reading it back would fail.
        self.assertEqual(
            [group.id for group in self.repository.list_device_groups()], ["family"]
        )
        self.assertIsNone(self.repository.get_group_data_limit("solo"))
        self.assertIsNotNone(self.repository.get_group_data_limit("family"))

    def test_deletes_report_whether_something_was_removed(self) -> None:
        self._save_lab()

        deletes = (
            (self.repository.delete_schedule, "work-hours"),
            (self.repository.delete_data_limit, "alice"),
            (self.repository.delete_group_data_limit, "family"),
            (self.repository.delete_service_quota, "alice-video"),
            (self.repository.delete_time_quota, "alice-video-time"),
            (self.repository.delete_profile, "slow"),
            (self.repository.delete_profile, "unused"),
        )
        for delete, identifier in deletes:
            with self.subTest(delete=delete.__name__, identifier=identifier):
                self.assertTrue(delete(identifier))
                self.assertFalse(delete(identifier))
        self.assertEqual(self.repository.profile_references("normal", now=200), (
            "device Alice's laptop",
            "device Bob's phone",
        ))
        self.assertEqual(
            [profile.id for profile in self.repository.list_profiles()], ["normal"]
        )

    def test_round_trips_burst_loss_and_loads_older_profiles(self) -> None:
        profile = Profile(
            id="bursty",
            name="Bursty",
            upload=Impairment(loss_percent=10, loss_burst_packets=4),
        )
        self.repository.save_profile(profile)
        # A profile saved before burst loss existed has no such key.
        self.repository._connection.execute(
            "UPDATE profiles SET download_json = ? WHERE id = ?",
            ('{"delay_ms": 20}', "bursty"),
        )
        self.repository._connection.commit()

        stored = self.repository.get_profile("bursty")

        self.assertEqual(stored.upload.loss_burst_packets, 4)
        self.assertEqual(stored.download, Impairment(delay_ms=20))

    def test_replacing_settings_swaps_every_saved_setting(self) -> None:
        self._save_lab()
        self.repository.save_domain_observations(
            (DomainObservation("192.168.50.10", "example.com", 3, 10, 10),), now=10
        )
        replacement = SavedSettings(
            profiles=(Profile(id="fast", name="Fast"),),
            devices=(Device("carol", "Carol", "192.168.50.20", "fast"),),
            mac_policy=MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",)),
        )

        self.repository.replace_settings(replacement)

        saved = self.repository.saved_settings()
        self.assertEqual(saved.profiles, replacement.profiles)
        self.assertEqual(saved.devices, replacement.devices)
        self.assertEqual(saved.groups, ())
        self.assertEqual(saved.schedules, ())
        self.assertEqual(saved.data_limits, ())
        self.assertEqual(saved.service_quotas, ())
        self.assertEqual(saved.mac_policy, replacement.mac_policy)
        # DNS history is not a setting and stays.
        self.assertEqual(len(self.repository.list_domain_observations()), 1)

    def test_replacing_settings_changes_nothing_when_it_fails(self) -> None:
        self._save_lab()
        before = self.repository.saved_settings()
        broken = SavedSettings(
            profiles=(Profile(id="fast", name="Fast"),),
            devices=(Device("carol", "Carol", "192.168.50.20", "missing-profile"),),
        )

        with self.assertRaises(StorageError):
            self.repository.replace_settings(broken)

        self.assertEqual(self.repository.saved_settings(), before)

    def test_replacing_settings_changes_nothing_after_an_unexpected_error(self) -> None:
        self._save_lab()
        before = self.repository.saved_settings()

        def interrupted(policy):
            raise RuntimeError("interrupted")

        self.repository.save_website_policy = interrupted
        with self.assertRaises(RuntimeError):
            self.repository.replace_settings(
                SavedSettings(profiles=(Profile(id="fast", name="Fast"),))
            )
        del self.repository.save_website_policy

        self.assertEqual(self.repository.saved_settings(), before)

    def test_round_trips_byte_progression_targets(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        progression = Progression(
            id="byte-driven",
            device_id="alice",
            start_profile_id="normal",
            end_profile_id="slow",
            started_at=100,
            duration_seconds=600,
            mode="bytes",
            traffic_target_bytes=2_000,
            traffic_baseline_bytes=125,
        )

        self.repository.save_progression(progression)

        self.assertEqual(self.repository.list_active_progressions(now=200), (progression,))

    def test_round_trips_quotas_tracks_usage_and_resets_it(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        data_limit = DataLimit(
            device_id="alice",
            profile_id="slow",
            limit_bytes=1_000,
            last_reset_at=100,
        )
        service_quota = ServiceQuota(
            id="alice-netflix",
            device_id="alice",
            name="Netflix",
            domains=("netflix.com", "nflxvideo.net"),
            profile_id="slow",
            limit_bytes=500,
            weight_percent=120,
            last_reset_at=100,
        )

        self.repository.save_data_limit(data_limit)
        self.repository.save_service_quota(service_quota)
        self.assertEqual(self.repository.get_data_limit("alice"), data_limit)
        self.assertEqual(self.repository.get_service_quota(service_quota.id), service_quota)

        updated = self.repository.record_data_usage("alice", 1_200)
        self.assertEqual(updated.used_bytes, 1_200)
        updated = self.repository.record_data_usage("alice", 1_400)
        self.assertEqual(updated.used_bytes, 1_400)
        self.assertTrue(self.repository.reset_data_limit_usage("alice", counter_bytes=1_400))
        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 0)

        updated = self.repository.record_service_usage(service_quota.id, 600)
        self.assertEqual(updated.used_bytes, 720)
        self.assertTrue(self.repository.reset_service_quota_usage(service_quota.id, counter_bytes=600))
        self.assertEqual(self.repository.get_service_quota(service_quota.id).used_bytes, 0)

    def test_round_trips_device_groups_and_shared_quota_usage(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.repository.save_device(Device("bob", "Bob", "192.168.50.11", "normal"))
        group = DeviceGroup(
            id="family",
            name="Family devices",
            device_ids=("alice", "bob"),
            profile_id="slow",
        )
        data_limit = GroupDataLimit(
            group_id="family",
            profile_id="slow",
            limit_bytes=1_000,
            last_reset_at=100,
        )

        self.repository.save_device_group(group)
        self.repository.save_group_data_limit(data_limit)

        self.assertEqual(self.repository.get_device_group("family"), group)
        self.assertEqual(self.repository.get_group_data_limit("family"), data_limit)
        updated = self.repository.record_group_data_usage("family", 1_200)
        self.assertEqual(updated.used_bytes, 1_200)
        self.assertTrue(self.repository.reset_group_data_limit_usage("family", counter_bytes=1_200))
        self.assertEqual(self.repository.get_group_data_limit("family").used_bytes, 0)

    def test_tracks_active_time_and_starts_a_fresh_window(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="slow", name="Slow"))
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        quota = TimeQuota(
            id="alice-video-time",
            device_id="alice",
            name="Video time",
            domains=("netflix.com",),
            profile_id="slow",
            limit_seconds=300,
            window_duration_seconds=3_600,
            last_reset_at=100,
        )
        self.repository.save_time_quota(quota)

        self.assertEqual(self.repository.record_time_usage(quota.id, 100, 100, active=True).used_seconds, 0)
        self.assertEqual(self.repository.record_time_usage(quota.id, 200, 160, active=True).used_seconds, 60)
        self.assertEqual(self.repository.record_time_usage(quota.id, 200, 220, active=True).used_seconds, 60)
        started = self.repository.start_time_quota(
            quota.id,
            started_at=300,
            expires_at=900,
            counter_bytes=200,
        )
        self.assertEqual(started.used_seconds, 0)
        self.assertEqual(started.started_at, 300)
        self.assertEqual(started.expires_at, 900)

        self.assertEqual(self.repository.record_time_usage(quota.id, 300, 360, active=True).used_seconds, 60)
        self.assertTrue(self.repository.stop_time_quota(quota.id))
        self.assertEqual(self.repository.get_time_quota(quota.id).expires_at, 0)

    def test_lists_values_in_stable_identifier_order(self) -> None:
        self.repository.save_profile(Profile(id="z-profile", name="Z"))
        self.repository.save_profile(Profile(id="a-profile", name="A"))
        self.repository.save_device(
            Device(
                id="z-device",
                name="Z",
                ipv4_address="192.168.50.12",
                profile_id="z-profile",
            )
        )
        self.repository.save_device(
            Device(
                id="a-device",
                name="A",
                ipv4_address="192.168.50.10",
                profile_id="a-profile",
            )
        )

        self.assertEqual(
            [profile.id for profile in self.repository.list_profiles()],
            ["a-profile", "z-profile"],
        )
        self.assertEqual(
            [device.id for device in self.repository.list_devices()],
            ["a-device", "z-device"],
        )

    def test_updates_existing_profile_without_duplicate_rows(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(Profile(id="normal", name="Updated"))

        self.assertEqual(self.repository.get_profile("normal").name, "Updated")
        self.assertEqual(len(self.repository.list_profiles()), 1)

    def test_requires_a_stored_profile_for_a_device(self) -> None:
        device = Device(
            id="alice-laptop",
            name="Alice's laptop",
            ipv4_address="192.168.50.10",
            profile_id="missing",
        )

        with self.assertRaisesRegex(StorageError, "could not save device"):
            self.repository.save_device(device)

    def test_rejects_duplicate_ipv4_addresses(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        first = Device("alice", "Alice", "192.168.50.10", "normal")
        second = Device("bob", "Bob", "192.168.50.10", "normal")
        self.repository.save_device(first)

        with self.assertRaisesRegex(StorageError, "could not save device"):
            self.repository.save_device(second)

    def test_reports_malformed_stored_json(self) -> None:
        self.repository._connection.execute(
            "INSERT INTO profiles (id, name, upload_json, download_json) VALUES (?, ?, ?, ?)",
            ("broken", "Broken", "not-json", "{}"),
        )
        self.repository._connection.commit()

        with self.assertRaisesRegex(StorageError, "stored impairment is malformed"):
            self.repository.get_profile("broken")

    def test_tracks_desired_version_separately_from_observed_state(self) -> None:
        self.assertEqual(self.repository.get_runtime_state().configuration_version, 0)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "unknown")

        self.repository.save_profile(Profile(id="normal", name="Normal"))
        applying = self.repository.update_runtime_state(
            observed_state="applying",
            last_action="apply",
            last_result=None,
            last_error=None,
        )

        self.assertEqual(applying.configuration_version, 1)
        self.assertEqual(applying.observed_state, "applying")
        self.assertEqual(applying.last_action, "apply")

        self.repository.save_device(
            Device(
                id="alice",
                name="Alice",
                ipv4_address="192.168.50.10",
                profile_id="normal",
            )
        )
        updated = self.repository.get_runtime_state()
        self.assertEqual(updated.configuration_version, 2)
        self.assertEqual(updated.observed_state, "unknown")
        # Startup recovery relies on the last explicit network action.
        self.assertEqual(updated.last_action, "apply")

    def test_merges_and_lists_domain_observations_without_bumping_configuration(self) -> None:
        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.10", "Amazon.COM.", 2, 10, 10),
                DomainObservation("192.168.50.10", "chat.openai.com", 1, 11, 11),
            ],
            now=11,
        )
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.10", "amazon.com", 3, 12, 12)],
            now=12,
        )

        observations = self.repository.list_domain_observations()

        self.assertEqual(self.repository.get_runtime_state().configuration_version, 0)
        self.assertEqual(
            [(observation.domain, observation.query_count) for observation in observations],
            [("amazon.com", 5), ("chat.openai.com", 1)],
        )

    def test_action_history_is_newest_first_and_keeps_only_500_entries(self) -> None:
        for index in range(501):
            action = "/api/apply" if index < 500 else "/api/reset"
            self.repository.record_action(action=action, status_code=200)

        history = self.repository.list_recent_actions(limit=100)
        stored_count = self.repository._connection.execute(
            "SELECT COUNT(*) FROM action_history"
        ).fetchone()[0]

        self.assertEqual(stored_count, 500)
        self.assertEqual(history[0]["action"], "/api/reset")
        self.assertEqual(set(history[0]), {"happened_at", "action", "status_code"})

    def test_backup_is_a_readable_snapshot_of_saved_configuration(self) -> None:
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c",
            display_name="Family MacBook",
            device_type="laptop",
        )
        self.repository.save_traffic_rate_settings(
            upload_kbit=None,
            download_kbit=150_000,
        )
        backup = self.repository.backup_bytes()

        with tempfile.NamedTemporaryFile(suffix=".db") as temporary:
            temporary.write(backup)
            temporary.flush()
            with sqlite3.connect(temporary.name) as snapshot:
                profile = snapshot.execute(
                    "SELECT name FROM profiles WHERE id = 'normal'"
                ).fetchone()
                label = snapshot.execute(
                    "SELECT display_name, device_type FROM connected_device_labels "
                    "WHERE mac_address = 'dc:1e:d5:3e:97:4c'"
                ).fetchone()
                traffic_rates = snapshot.execute(
                    "SELECT upload_kbit, download_kbit FROM traffic_rate_settings WHERE id = 1"
                ).fetchone()

        self.assertEqual(profile, ("Normal",))
        self.assertEqual(label, ("Family MacBook", "laptop"))
        self.assertEqual(traffic_rates, (None, 150_000))


class EveryoneElseStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="slow", name="Slow"))

    def test_the_everyone_else_profile_round_trips_and_keeps_its_profile(self) -> None:
        self.assertIsNone(self.repository.get_everyone_else_profile_id())

        self.repository.save_everyone_else_profile_id("slow")

        self.assertEqual(self.repository.get_everyone_else_profile_id(), "slow")
        self.assertIn("everyone else", self.repository.profile_references("slow", now=0))
        with self.assertRaises(StorageError):
            self.repository.delete_profile("slow")
        self.repository.save_everyone_else_profile_id(None)
        self.assertIsNone(self.repository.get_everyone_else_profile_id())

    def test_a_missing_everyone_else_profile_is_refused(self) -> None:
        with self.assertRaises(StorageError):
            self.repository.save_everyone_else_profile_id("missing")

        self.assertIsNone(self.repository.get_everyone_else_profile_id())

    def test_unaffected_devices_round_trip_in_address_order(self) -> None:
        self.repository.save_unaffected_device(UnaffectedDevice("bb:bb:bb:bb:bb:bb", "Phone"))
        self.repository.save_unaffected_device(UnaffectedDevice("aa:aa:aa:aa:aa:aa"))
        self.repository.save_unaffected_device(UnaffectedDevice("bb:bb:bb:bb:bb:bb", "My phone"))

        self.assertEqual(
            self.repository.list_unaffected_devices(),
            (
                UnaffectedDevice("aa:aa:aa:aa:aa:aa"),
                UnaffectedDevice("bb:bb:bb:bb:bb:bb", "My phone"),
            ),
        )
        self.assertTrue(self.repository.delete_unaffected_device("aa:aa:aa:aa:aa:aa"))
        self.assertFalse(self.repository.delete_unaffected_device("aa:aa:aa:aa:aa:aa"))

    def test_the_unaffected_list_holds_at_most_256_devices(self) -> None:
        for index in range(MAX_UNAFFECTED_DEVICES):
            self.repository.save_unaffected_device(
                UnaffectedDevice(f"02:00:00:00:{index // 256:02x}:{index % 256:02x}")
            )

        with self.assertRaises(StorageError):
            self.repository.save_unaffected_device(UnaffectedDevice("02:00:00:01:00:00"))
        # A listed device can still be renamed.
        self.repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:00", "Renamed"))

    def test_replacing_settings_carries_the_everyone_else_settings(self) -> None:
        # The old everyone-else profile is replaced, so it must not block the replace.
        self.repository.save_everyone_else_profile_id("slow")
        settings = SavedSettings(
            profiles=(Profile(id="fast", name="Fast"),),
            everyone_else_profile_id="fast",
            unaffected_devices=(UnaffectedDevice("aa:aa:aa:aa:aa:aa", "Laptop"),),
        )

        self.repository.replace_settings(settings)

        self.assertEqual(self.repository.saved_settings(), settings)

    def test_existing_databases_gain_the_everyone_else_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jittersim.db"
            ConfigRepository(path).close()
            connection = sqlite3.connect(path)
            connection.executescript(
                "DROP TABLE everyone_else_settings; DROP TABLE unaffected_devices;"
            )
            connection.close()

            with ConfigRepository(path) as repository:
                self.assertIsNone(repository.get_everyone_else_profile_id())
                self.assertEqual(repository.list_unaffected_devices(), ())

    def test_finds_a_device_by_its_mac_address(self) -> None:
        self.repository.save_device(
            Device("alice", "Alice", "192.168.50.10", "slow", "AA:AA:AA:AA:AA:AA")
        )

        self.assertEqual(
            self.repository.get_device_by_mac_address("aa:aa:aa:aa:aa:aa").id, "alice"
        )
        self.assertIsNone(self.repository.get_device_by_mac_address("bb:bb:bb:bb:bb:bb"))


if __name__ == "__main__":
    unittest.main()
