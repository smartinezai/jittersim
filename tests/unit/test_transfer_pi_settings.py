"""Export, import and history restore keep the Pi line's own settings.

Service lists and the general profile come from the Pi line. A settings file
from main or from an older Pi build does not carry them, so such a file must
leave them alone. A protected saved device stays saved and dormant on every
path and a newly protected device loses its stored DNS observations on every
path.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jittersim.api.server import ControlServer
from jittersim.config.models import (
    Device,
    DomainObservation,
    Profile,
    SavedSettings,
    ServiceList,
    UnaffectedDevice,
)
from jittersim.config.storage import ConfigRepository
from jittersim.config.transfer import check_settings, settings_to_payload
from jittersim.config.workflows import WorkflowStore

MAC = "aa:aa:aa:aa:aa:aa"
OTHER_MAC = "02:00:00:00:00:bb"


class PiSettingsTransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = self.new_repository("settings.db")
        self.repository.save_profile(Profile("slow", "Slow"))
        self.repository.save_profile(Profile("choppy", "Choppy"))
        self.repository.save_device(Device("phone", "Phone", "192.168.50.10", "slow", MAC))
        self.repository.save_service_list(
            ServiceList(id="video", name="Video", profile_id="choppy", hostnames=("video.example",))
        )
        self.repository.save_general_profile("slow")
        self.server = self.new_server(self.repository)

    def new_repository(self, name):
        repository = ConfigRepository(Path(self.directory.name) / name)
        self.addCleanup(repository.close)
        return repository

    def new_server(self, repository):
        server = ControlServer(("127.0.0.1", 0), repository, admin_token="local-test")
        self.addCleanup(server.server_close)
        return server

    def export(self, server=None):
        status, _, body = (server or self.server).response_for("/api/export")
        self.assertEqual(status, 200)
        return json.loads(body)

    def post(self, path, payload, server=None):
        status, _, body = (server or self.server).write_response_for(path, payload)
        return status, json.loads(body)

    def restore(self, identifier):
        _, preview = self.post("/api/history/preview", {"id": identifier})
        return self.post(
            "/api/history/restore",
            {"id": identifier, "current_version": preview["current_version"]},
        )

    def lists(self, repository=None):
        return [item.id for item in (repository or self.repository).list_service_lists()]

    def general(self, repository=None):
        return (repository or self.repository).saved_settings().general_profile_id

    def test_export_carries_the_service_lists_and_the_general_profile(self):
        exported = self.export()

        self.assertEqual(exported["general_profile_id"], "slow")
        self.assertEqual(
            exported["service_lists"],
            [
                {
                    "id": "video",
                    "name": "Video",
                    "profile_id": "choppy",
                    "services": [],
                    "hostnames": ["video.example"],
                    "enabled": True,
                }
            ],
        )

    def test_the_settings_move_to_another_installation_with_both(self):
        other_repository = self.new_repository("other.db")
        other = self.new_server(other_repository)

        status, body = self.post("/api/import", self.export(), server=other)

        self.assertEqual(status, 200, body)
        self.assertEqual(other_repository.list_service_lists(), self.repository.list_service_lists())
        self.assertEqual(self.general(other_repository), "slow")

    def test_an_import_without_the_keys_leaves_them_untouched(self):
        exported = self.export()
        del exported["service_lists"]
        del exported["general_profile_id"]

        status, body = self.post("/api/import", exported)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.lists(), ["video"])
        self.assertEqual(self.general(), "slow")

    def test_an_import_with_the_keys_replaces_them(self):
        exported = self.export()
        exported["service_lists"] = []
        exported["general_profile_id"] = None

        status, body = self.post("/api/import", exported)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.lists(), [])
        self.assertIsNone(self.general())

    def test_an_old_file_whose_profiles_miss_the_kept_list_is_refused_and_changes_nothing(self):
        exported = self.export()
        del exported["service_lists"]
        del exported["general_profile_id"]
        exported["profiles"] = [item for item in exported["profiles"] if item["id"] == "slow"]

        status, body = self.post("/api/import", exported)

        self.assertEqual(status, 400, body)
        self.assertIn("service list video uses the missing profile choppy", body["error"])
        self.assertEqual(self.lists(), ["video"])
        self.assertIsNotNone(self.repository.get_profile("choppy"))

    def test_the_check_refuses_lists_and_a_general_profile_that_do_not_fit(self):
        slow = Profile("slow", "Slow")
        cases = {
            "service list video uses the missing profile gone": SavedSettings(
                profiles=(slow,),
                service_lists=(
                    ServiceList(id="video", name="Video", profile_id="gone", hostnames=("a.example",)),
                ),
            ),
            "the general profile uses the missing profile gone": SavedSettings(
                profiles=(slow,), general_profile_id="gone"
            ),
            "service lists one and two overlap on b.a.example": SavedSettings(
                profiles=(slow,),
                service_lists=(
                    ServiceList(id="one", name="One", profile_id="slow", hostnames=("a.example",)),
                    ServiceList(id="two", name="Two", profile_id="slow", hostnames=("b.a.example",)),
                ),
            ),
        }
        for message, settings in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    check_settings(settings, max_traffic_classes=15, max_service_domains=64)
        # A list that is off may share names with one that is on.
        check_settings(
            SavedSettings(
                profiles=(slow,),
                service_lists=(
                    ServiceList(id="one", name="One", profile_id="slow", hostnames=("a.example",)),
                    ServiceList(
                        id="two", name="Two", profile_id="slow", hostnames=("a.example",),
                        enabled=False,
                    ),
                ),
            ),
            max_traffic_classes=15,
            max_service_domains=64,
        )

    def test_a_restore_puts_the_lists_and_the_general_profile_back(self):
        store = WorkflowStore(self.repository)
        store.checkpoint("Before the edit")
        identifier = store.history()[0]["id"]
        self.repository.delete_service_list("video")
        self.repository.save_general_profile(None)

        status, body = self.restore(identifier)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.lists(), ["video"])
        self.assertEqual(self.general(), "slow")

    def test_a_restore_of_an_old_version_without_the_keys_leaves_them_untouched(self):
        old = settings_to_payload(self.repository.saved_settings(), exported_at=0)
        del old["service_lists"]
        del old["general_profile_id"]
        self.repository._connection.execute(
            "INSERT INTO settings_history (happened_at, label, digest, settings_json) "
            "VALUES (1, 'Old build', 'old', ?)",
            (json.dumps(old),),
        )
        self.repository._connection.commit()
        identifier = WorkflowStore(self.repository).history()[0]["id"]

        status, body = self.restore(identifier)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.lists(), ["video"])
        self.assertEqual(self.general(), "slow")

    def test_a_protected_saved_device_can_still_be_edited(self):
        # The Pi base keeps a protected device's saved assignment. It stays dormant
        # and comes back when the protection is removed.
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone"))

        status, body = self.post(
            "/api/devices",
            {
                "id": "phone",
                "name": "Renamed phone",
                "ipv4_address": "192.168.50.10",
                "profile_id": "choppy",
                "mac_address": MAC,
            },
        )

        self.assertEqual(status, 200, body)
        device = self.repository.get_device("phone")
        self.assertEqual((device.name, device.profile_id), ("Renamed phone", "choppy"))
        self.assertEqual([item.mac_address for item in self.repository.list_unaffected_devices()], [MAC])

    def test_an_export_with_a_protected_saved_device_imports_and_restores(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone"))
        exported = self.export()
        store = WorkflowStore(self.repository)
        store.checkpoint("Protected phone")
        identifier = store.history()[0]["id"]

        status, body = self.post("/api/import", exported)
        self.assertEqual(status, 200, body)
        restored_status, restored = self.restore(identifier)
        self.assertEqual(restored_status, 200, restored)

        self.assertEqual(self.repository.get_device("phone").mac_address, MAC)
        self.assertEqual([item.mac_address for item in self.repository.list_unaffected_devices()], [MAC])

    def save_observations(self):
        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.10", "phone.example", 3, 100, 200, MAC),
                # A row from before hardware addresses were recorded.
                DomainObservation("192.168.50.10", "old.example", 1, 100, 200),
                DomainObservation("192.168.50.11", "laptop.example", 2, 100, 200, OTHER_MAC),
            ],
            now=300,
        )

    def observed(self):
        return sorted(row.domain for row in self.repository.list_domain_observations())

    def test_an_import_that_protects_a_device_drops_its_dns_observations(self):
        self.save_observations()
        exported = self.export()
        exported["everyone_else"]["unaffected_devices"] = [{"mac_address": MAC, "name": "Phone"}]

        status, body = self.post("/api/import", exported)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.observed(), ["laptop.example"])

    def test_a_restore_that_protects_a_device_drops_its_dns_observations(self):
        self.repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone"))
        store = WorkflowStore(self.repository)
        store.checkpoint("Protected phone")
        identifier = store.history()[0]["id"]
        self.repository.delete_unaffected_device(MAC)
        self.save_observations()

        status, body = self.restore(identifier)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.observed(), ["laptop.example"])

    def test_a_device_that_stays_protected_keeps_nothing_to_purge_and_others_keep_theirs(self):
        self.save_observations()
        exported = self.export()

        status, body = self.post("/api/import", exported)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.observed(), ["laptop.example", "old.example", "phone.example"])


if __name__ == "__main__":
    unittest.main()
