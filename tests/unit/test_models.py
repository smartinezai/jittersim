import unittest
from ipaddress import IPv4Address

from jittersim.config.models import (
    Device,
    DomainObservation,
    Impairment,
    MacPolicy,
    Profile,
    Randomness,
    RuntimeState,
    Schedule,
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
