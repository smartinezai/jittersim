"""Saved rules belong to a device even when DHCP changes its address."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jittersim.api.server import ControlServer
from jittersim.config.models import DataLimit, Device, DeviceGroup, Profile, Schedule
from jittersim.config.storage import ConfigRepository, DeviceConflictError
from tests.unit.test_api import FakeHelper


class DeviceIdentityTests(unittest.TestCase):
    MAC = "dc:1e:d5:3e:97:4c"

    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile("normal", "Normal"))
        self.repository.save_profile(Profile("slow", "Slow"))
        self.device = Device("alice", "Alice", "192.168.50.10", "normal", self.MAC)
        self.repository.save_device(self.device)
        self.server = ControlServer(("127.0.0.1", 0), self.repository, helper_client=FakeHelper())
        self.addCleanup(self.server.server_close)

    def assign(self):
        status, _, body = self.server.write_response_for("/api/connected-devices/apply-profile", {
            "ipv4_address": "192.168.50.178", "mac_address": self.MAC.upper(), "profile_id": "slow",
        })
        return status, json.loads(body)

    def test_moved_client_keeps_its_id_name_group_schedule_and_limit(self):
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice",)))
        self.repository.save_schedule(Schedule(
            id="morning", profile_id="slow", days_of_week=(0,), start_minute=540,
            end_minute=600, device_id="alice",
        ))
        limit = DataLimit(device_id="alice", profile_id="slow", limit_bytes=1000)
        self.repository.save_data_limit(limit)

        status, response = self.assign()

        self.assertEqual(status, 200, response)
        self.assertEqual(len(self.repository.list_devices()), 1)
        self.assertEqual(self.repository.get_device("alice"), replace(
            self.device, ipv4_address="192.168.50.178", profile_id="slow",
        ))
        self.assertEqual(self.repository.get_device_group("family").device_ids, ("alice",))
        self.assertEqual(self.repository.list_schedules()[0].device_id, "alice")
        self.assertEqual(self.repository.get_data_limit("alice").limit_bytes, 1000)

    def test_assignment_does_not_steal_another_saved_devices_address(self):
        other = Device("bob", "Bob", "192.168.50.178", "normal", "aa:bb:cc:dd:ee:ff")
        self.repository.save_device(other)

        status, _ = self.assign()

        self.assertEqual(status, 409)
        self.assertEqual(self.repository.get_device("alice"), self.device)
        self.assertEqual(self.repository.get_device("bob"), other)
        self.assertIsNone(self.server.helper_client.applied_plan)

    def test_legacy_duplicate_mac_is_reported_without_choosing_a_saved_identity(self):
        self.repository._connection.execute(
            "INSERT INTO devices VALUES (?, ?, ?, ?, ?)",
            ("duplicate", "Duplicate", "192.168.50.11", "normal", self.MAC),
        )
        self.repository._connection.commit()

        status, response = self.assign()

        self.assertEqual(status, 409)
        self.assertIn("Several saved devices", response["error"])
        self.assertEqual(self.repository.get_device("alice"), self.device)

    def test_manual_assignment_cannot_add_a_duplicate_mac(self):
        status, _, body = self.server.write_response_for("/api/devices", {
            "id": "duplicate", "name": "Duplicate", "ipv4_address": "192.168.50.11",
            "profile_id": "normal", "mac_address": self.MAC,
        })

        self.assertEqual(status, 409, body)
        self.assertEqual(self.repository.list_devices(), (self.device,))

    def test_stale_form_cannot_restore_an_old_address(self):
        opened = json.loads(self.server.response_for("/api/devices")[2])["devices"][0]
        self.repository.save_device(replace(self.device, ipv4_address="192.168.50.178"))
        changed = dict(opened, name="Renamed", expected=opened)

        status, _, body = self.server.write_response_for("/api/devices", changed)

        self.assertEqual(status, 409, body)
        self.assertIn("Reload", json.loads(body)["error"])
        self.assertEqual(str(self.repository.get_device("alice").ipv4_address), "192.168.50.178")
        self.assertEqual(self.repository.get_device("alice").name, "Alice")

    def test_fresh_form_can_edit_the_device(self):
        opened = json.loads(self.server.response_for("/api/devices")[2])["devices"][0]

        status, _, body = self.server.write_response_for(
            "/api/devices", dict(opened, name="Renamed", expected=opened),
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.repository.get_device("alice").name, "Renamed")

    def test_a_second_connection_cannot_overwrite_a_concurrent_move(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "settings.db"
            with ConfigRepository(database) as first, ConfigRepository(database) as second:
                first.save_profile(Profile("normal", "Normal"))
                first.save_device(self.device)
                opened = first.get_device("alice")
                second.save_device(replace(self.device, ipv4_address="192.168.50.178"))
                version = first.get_runtime_state().configuration_version
                with self.assertRaises(DeviceConflictError):
                    first.save_device(replace(opened, name="Renamed"), expected=opened)
                self.assertEqual(first.get_runtime_state().configuration_version, version)
                self.assertEqual(str(first.get_device("alice").ipv4_address), "192.168.50.178")
