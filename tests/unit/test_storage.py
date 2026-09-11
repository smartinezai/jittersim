import unittest

from jittersim.config.models import Device, Impairment, Profile
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
        )
        device = Device(
            id="alice-laptop",
            name="Alice's laptop",
            ipv4_address="192.168.50.10",
            profile_id=profile.id,
        )

        self.repository.save_profile(profile)
        self.repository.save_device(device)

        self.assertEqual(self.repository.get_profile(profile.id), profile)
        self.assertEqual(self.repository.get_device(device.id), device)

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


if __name__ == "__main__":
    unittest.main()
