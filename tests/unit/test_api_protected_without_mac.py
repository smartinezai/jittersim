"""The API says the same as the plan about a saved device that cannot be checked."""

import json
import unittest

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository

AEROMAC = "d6:ac:49:a3:ee:6a"
OTHER = "ba:bb:00:00:00:02"
ADDRESS = "192.168.50.178"


class Helper:
    def __init__(self, *clients) -> None:
        self.clients = clients

    def connected_device_snapshot(self):
        return {
            "association_status_available": True,
            "devices": [
                {"mac_address": mac, "ipv4_address": address, "association_verified": True}
                for mac, address in self.clients
            ],
        }

    def queue_stats(self):
        return [
            {"device_id": "aeromac", "direction": "upload", "sent_bytes": 1, "dropped_packets": 9}
        ]


class ProtectedWithoutMacApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(
            Profile(id="bad", name="Bad test", upload=Impairment(delay_ms=1000, loss_percent=20))
        )
        self.repository.save_device(Device("aeromac", "Aeromac", ADDRESS, "bad"))
        self.repository.save_unaffected_device(UnaffectedDevice(AEROMAC, "Aeromac", 1))
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.server.helper_client = Helper((AEROMAC, ADDRESS))

    def tearDown(self) -> None:
        self.server.server_close()
        self.repository.close()

    def get(self, path):
        status, _, body = self.server.response_for(path)
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_devices_say_the_profile_is_paused_and_name_the_protected_client(self) -> None:
        device = self.get("/api/devices")["devices"][0]
        self.assertEqual(device["effective_state"], "unverified")
        self.assertEqual(
            device["protected_match"], {"mac_address": AEROMAC, "name": "Aeromac"}
        )
        self.assertIn("cannot tell it is the protected Aeromac", device["effective_note"])

    def test_a_device_with_the_protected_mac_is_protected(self) -> None:
        self.repository.save_device(Device("aeromac", "Aeromac", ADDRESS, "bad", AEROMAC))
        self.assertEqual(self.get("/api/devices")["devices"][0]["effective_state"], "protected")

    def test_without_protected_clients_the_device_is_active(self) -> None:
        self.repository.delete_unaffected_device(AEROMAC)
        device = self.get("/api/devices")["devices"][0]
        self.assertEqual(device["effective_state"], "active")
        self.assertIsNone(device["protected_match"])

    def test_conditions_never_call_a_paused_device_degraded(self) -> None:
        conditions = self.get("/api/conditions")
        self.assertEqual(conditions["devices"], {})
        self.assertNotIn("degraded", json.dumps(conditions))

    def test_saving_the_observed_mac_protects_the_device(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/devices",
            {
                "id": "aeromac", "name": "Aeromac", "ipv4_address": ADDRESS,
                "profile_id": "bad", "mac_address": AEROMAC,
                "expected": {
                    "id": "aeromac", "name": "Aeromac", "ipv4_address": ADDRESS,
                    "profile_id": "bad", "mac_address": None,
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["device"]["effective_state"], "protected")

    def test_the_impact_check_never_lists_a_device_that_cannot_be_checked(self) -> None:
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success", last_error=None
        )
        impact = self.get("/api/impact")
        self.assertTrue(impact["available"])
        self.assertEqual(impact["queues"], [])
        self.assertEqual(impact["protected"], ["Aeromac"])


if __name__ == "__main__":
    unittest.main()


class ConnectedClientsTests(ProtectedWithoutMacApiTests):
    def test_a_client_assigned_to_a_paused_device_says_so(self) -> None:
        other = "02:00:00:00:00:09"
        self.repository.save_device(Device("phone", "Phone", "192.168.50.60", "bad", other))
        # Two clients claim one address so the phone cannot be told from the protected laptop.
        self.server.helper_client = Helper((AEROMAC, "192.168.50.61"), (other, "192.168.50.61"))
        clients = {
            client["mac_address"]: client
            for client in self.get("/api/connected-devices")["devices"]
        }
        self.assertTrue(clients[AEROMAC]["unaffected"])
        self.assertEqual(clients[other]["assigned_effective_state"], "unverified")
