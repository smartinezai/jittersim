import unittest
from ipaddress import IPv4Address

from jittersim.config.models import (
    Device,
    DeviceGroup,
    DataLimit,
    DomainObservation,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    RuntimeState,
    Schedule,
    ServiceQuota,
    Timer,
    TimeQuota,
    time_quota_counter_id,
    ValidationError,
    WebsitePolicy,
)


class ImpairmentTests(unittest.TestCase):
    def test_default_impairment_is_normal_traffic(self) -> None:
        self.assertEqual(
            Impairment(),
            Impairment(
                delay_ms=0,
                jitter_ms=0,
                loss_percent=0,
                bandwidth_kbit=None,
                reorder_percent=0,
                duplicate_percent=0,
                corrupt_percent=0,
            ),
        )

    def test_accepts_combined_packet_impairments(self) -> None:
        impairment = Impairment(
            delay_ms=3_000,
            jitter_ms=700,
            loss_percent=15,
            bandwidth_kbit=2_000,
            reorder_percent=2,
            duplicate_percent=1,
            corrupt_percent=1,
        )

        self.assertEqual(impairment.delay_ms, 3_000)
        self.assertEqual(impairment.bandwidth_kbit, 2_000)

    def test_rejects_values_outside_safe_ranges(self) -> None:
        invalid_values = (
            {"delay_ms": -1},
            {"jitter_ms": 60_001},
            {"loss_percent": 101},
            {"bandwidth_kbit": 0},
            {"duplicate_percent": -1},
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    Impairment(**values)

    def test_rejects_boolean_numeric_values(self) -> None:
        with self.assertRaises(ValidationError):
            Impairment(loss_percent=True)


class ProfileTests(unittest.TestCase):
    def test_accepts_seeded_profile(self) -> None:
        profile = Profile(
            id="terrible-wifi",
            name="Terrible Wi-Fi",
            random_seed=42,
        )

        self.assertEqual(profile.id, "terrible-wifi")
        self.assertEqual(profile.random_seed, 42)

    def test_accepts_reroll_outage_and_website_settings(self) -> None:
        profile = Profile(
            id="variable",
            name="Variable",
            upload=Impairment(
                delay_ms=200,
                delay_randomness=Randomness(variation_percent=30, enabled_probability=90),
            ),
            reroll_interval_seconds=300,
            reroll_interval_randomness=Randomness(variation_percent=20),
            outage_probability=10,
            outage_duration_seconds=60,
            website_mode="allow_only",
            website_domains=("Chat.OpenAI.com.", "youtube.com"),
        )

        self.assertEqual(profile.upload.delay_randomness.variation_percent, 30)
        self.assertEqual(profile.website_domains, ("chat.openai.com", "youtube.com"))

    def test_rejects_invalid_identifiers(self) -> None:
        for identifier in ("Terrible Wi-Fi", "", "contains spaces"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValidationError):
                    Profile(id=identifier, name="Example")


class DeviceTests(unittest.TestCase):
    def test_normalizes_a_private_ipv4_string(self) -> None:
        device = Device(
            id="alice-laptop",
            name="Alice's laptop",
            ipv4_address="192.168.50.10",
            profile_id="terrible-wifi",
        )

        self.assertEqual(device.ipv4_address, IPv4Address("192.168.50.10"))

    def test_normalizes_a_device_mac_address(self) -> None:
        device = Device(
            id="alice-laptop",
            name="Alice's laptop",
            ipv4_address="192.168.50.10",
            profile_id="normal",
            mac_address="AA-BB-CC-DD-EE-FF",
        )

        self.assertEqual(device.mac_address, "aa:bb:cc:dd:ee:ff")

    def test_rejects_non_private_addresses(self) -> None:
        for address in ("8.8.8.8", "127.0.0.1", "169.254.10.20"):
            with self.subTest(address=address):
                with self.assertRaises(ValidationError):
                    Device(
                        id="test-device",
                        name="Test device",
                        ipv4_address=address,
                        profile_id="normal",
                    )

    def test_rejects_invalid_profile_identifier(self) -> None:
        with self.assertRaises(ValidationError):
            Device(
                id="test-device",
                name="Test device",
                ipv4_address="192.168.50.10",
                profile_id="Not a valid identifier",
            )


class DeviceGroupTests(unittest.TestCase):
    def test_normalizes_and_keeps_group_members(self) -> None:
        group = DeviceGroup(
            id="family",
            name="Family devices",
            device_ids=("alice", "bob"),
            profile_id="slow",
        )

        self.assertEqual(group.device_ids, ("alice", "bob"))
        self.assertEqual(group.profile_id, "slow")

    def test_rejects_duplicate_group_members(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            DeviceGroup(
                id="family",
                name="Family devices",
                device_ids=("alice", "alice"),
            )


class DomainObservationTests(unittest.TestCase):
    def test_normalizes_dns_names_and_tracks_time_bounds(self) -> None:
        observation = DomainObservation(
            ipv4_address="192.168.50.10",
            domain="Amazon.COM.",
            query_count=3,
            first_seen=10,
            last_seen=20,
        )

        self.assertEqual(observation.domain, "amazon.com")
        self.assertEqual(observation.query_count, 3)

    def test_normalizes_the_client_hardware_address_when_one_is_given(self) -> None:
        values = dict(ipv4_address="192.168.50.10", domain="example.com", query_count=1)

        self.assertIsNone(DomainObservation(first_seen=1, last_seen=1, **values).mac_address)
        self.assertIsNone(
            DomainObservation(first_seen=1, last_seen=1, mac_address=None, **values).mac_address
        )
        self.assertEqual(
            DomainObservation(
                first_seen=1, last_seen=1, mac_address="AA-BB-CC-DD-EE-01", **values
            ).mac_address,
            "aa:bb:cc:dd:ee:01",
        )

    def test_rejects_an_invalid_client_hardware_address(self) -> None:
        for value in ("not-a-mac", 5, "01:00:5e:00:00:01", "00:00:00:00:00:00", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    DomainObservation(
                        ipv4_address="192.168.50.10",
                        domain="example.com",
                        query_count=1,
                        first_seen=1,
                        last_seen=1,
                        mac_address=value,
                    )

    def test_rejects_invalid_dns_names_and_time_bounds(self) -> None:
        invalid_values = (
            {"domain": "-not-a-domain.com"},
            {"domain": "a" * 254},
            {"first_seen": 21, "last_seen": 20},
            {"query_count": 0},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    DomainObservation(
                        ipv4_address="192.168.50.10",
                        domain=values.get("domain", "example.com"),
                        query_count=values.get("query_count", 1),
                        first_seen=values.get("first_seen", 10),
                        last_seen=values.get("last_seen", 20),
                    )


class PolicyAndScheduleTests(unittest.TestCase):
    def test_requires_an_allowlist_entry_when_allowlist_mode_is_enabled(self) -> None:
        with self.assertRaises(ValidationError):
            MacPolicy(mode="allowlist")

    def test_normalizes_policy_lists_and_supports_overnight_schedule(self) -> None:
        policy = MacPolicy(
            mode="denylist",
            allowlist=("AA:BB:CC:DD:EE:FF",),
            denylist=("11-22-33-44-55-66",),
        )
        website_policy = WebsitePolicy(
            mode="allow_only",
            allowed_domains=("Example.COM",),
            blocked_domains=("Ads.Example.com.",),
        )
        schedule = Schedule(
            id="late-night",
            device_id="alice",
            profile_id="terrible-wifi",
            days_of_week=(0, 2, 2),
            start_minute=1_380,
            end_minute=60,
        )

        self.assertEqual(policy.allowlist, ("aa:bb:cc:dd:ee:ff",))
        self.assertEqual(policy.denylist, ("11:22:33:44:55:66",))
        self.assertEqual(website_policy.blocked_domains, ("ads.example.com",))
        self.assertEqual(schedule.days_of_week, (0, 2))

    def test_timer_requires_a_positive_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "expires_at must be later"):
            Timer(
                id="ten-minutes",
                device_id="alice",
                profile_id="slow",
                started_at=100,
                expires_at=100,
            )

    def test_progression_requires_a_valid_mode_and_traffic_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive query target"):
            Progression(
                id="degrade",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="slow",
                started_at=100,
                duration_seconds=600,
                mode="traffic",
            )

    def test_bytes_progression_requires_a_positive_byte_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive byte target"):
            Progression(
                id="degrade",
                device_id="alice",
                start_profile_id="normal",
                end_profile_id="slow",
                started_at=100,
                duration_seconds=600,
                mode="bytes",
            )

    def test_accepts_data_and_service_quota_reset_schedules(self) -> None:
        data_limit = DataLimit(
            device_id="alice",
            profile_id="slow",
            limit_bytes=5_000_000,
            reset_mode="daily",
            reset_times=(0, 720),
        )
        service_quota = ServiceQuota(
            id="alice-video",
            device_id="alice",
            name="Video sites",
            domains=("Netflix.com", "nflxvideo.net"),
            profile_id="slow",
            limit_bytes=1_000_000,
            weight_percent=120,
            reset_mode="interval",
            reset_interval_seconds=3_600,
        )

        self.assertEqual(data_limit.reset_times, (0, 720))
        self.assertEqual(service_quota.domains, ("netflix.com", "nflxvideo.net"))
        self.assertEqual(service_quota.weight_percent, 120)

    def test_accepts_a_timed_service_quota_window(self) -> None:
        quota = TimeQuota(
            id="alice-netflix-time",
            device_id="alice",
            name="Netflix time",
            domains=("netflix.com",),
            profile_id="slow",
            limit_seconds=5 * 60 * 60,
            window_duration_seconds=12 * 60 * 60,
            reset_mode="daily",
            reset_times=(600, 1_200),
            started_at=100,
            expires_at=43_300,
        )

        self.assertEqual(quota.limit_seconds, 18_000)
        self.assertEqual(quota.reset_times, (600, 1_200))

    def test_rejects_a_timed_quota_window_longer_than_configured(self) -> None:
        with self.assertRaises(ValidationError):
            TimeQuota(
                id="alice-netflix-time",
                device_id="alice",
                name="Netflix time",
                domains=("netflix.com",),
                profile_id="slow",
                limit_seconds=300,
                window_duration_seconds=600,
                started_at=100,
                expires_at=701,
            )

    def test_time_quota_counter_ids_are_bounded_and_stable(self) -> None:
        first = time_quota_counter_id("a" * 64)

        self.assertEqual(first, time_quota_counter_id("a" * 64))
        self.assertLessEqual(len(first), 64)

    def test_service_quota_weight_has_a_safe_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "weight_percent must be between 10 and 1000"):
            ServiceQuota(
                id="alice-video",
                device_id="alice",
                name="Video sites",
                domains=("netflix.com",),
                profile_id="slow",
                limit_bytes=1_000,
                weight_percent=1_001,
            )


class RuntimeStateTests(unittest.TestCase):
    def test_accepts_known_observed_states(self) -> None:
        for observed_state in ("unknown", "applying", "resetting", "applied", "reset", "error"):
            with self.subTest(observed_state=observed_state):
                self.assertEqual(
                    RuntimeState(observed_state=observed_state).observed_state,
                    observed_state,
                )

    def test_rejects_unknown_state_and_negative_version(self) -> None:
        with self.assertRaises(ValidationError):
            RuntimeState(observed_state="running")
        with self.assertRaises(ValidationError):
            RuntimeState(configuration_version=-1)


if __name__ == "__main__":
    unittest.main()
