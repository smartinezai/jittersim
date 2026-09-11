import json
import unittest

from jittersim.api.server import ControlServer, DASHBOARD_HTML
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository


class ControlServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(
            Profile(
                id="slow",
                name="Slow",
                upload=Impairment(delay_ms=3_000),
            )
        )
        self.repository.save_device(
            Device(
                id="alice",
                name="Alice's laptop",
                ipv4_address="192.168.50.10",
                profile_id="slow",
            )
        )
        self.server = ControlServer(("127.0.0.1", 0), self.repository)

    def tearDown(self) -> None:
        self.server.server_close()
        self.repository.close()

    def test_health_response_identifies_read_only_mode(self) -> None:
        status, content_type, body = self.server.response_for("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body), {"mode": "read-only", "status": "ok"})

    def test_configuration_endpoints_serialize_validated_values(self) -> None:
        status, _, body = self.server.response_for("/api/profiles")
        profiles = json.loads(body)["profiles"]
        self.assertEqual(status, 200)
        self.assertEqual(profiles[0]["upload"]["delay_ms"], 3_000)

        status, _, body = self.server.response_for("/api/devices")
        devices = json.loads(body)["devices"]
        self.assertEqual(status, 200)
        self.assertEqual(devices[0]["ipv4_address"], "192.168.50.10")

    def test_plan_endpoint_uses_the_deterministic_compiler(self) -> None:
        status, _, body = self.server.response_for("/api/plan")

        plan = json.loads(body)["plan"]
        self.assertEqual(status, 200)
        self.assertEqual(plan["mark_rules"][0]["mark"], 1)
        self.assertEqual(plan["queues"][0]["impairment"]["delay_ms"], 3_000)

    def test_dashboard_is_explicitly_read_only(self) -> None:
        status, content_type, body = self.server.response_for("/")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertEqual(body.decode("utf-8"), DASHBOARD_HTML)
        self.assertIn("Read-only inspection mode", DASHBOARD_HTML)

    def test_unknown_paths_return_json_not_found(self) -> None:
        status, content_type, body = self.server.response_for("/missing")

        self.assertEqual(status, 404)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body), {"error": "not found"})


if __name__ == "__main__":
    unittest.main()
