from pathlib import Path
import tempfile
import threading
import unittest

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClient, HelperService, UnixHelperServer
from jittersim.networking.topology import Topology


class ApiHelperIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        socket_path = Path(self.temp_dir.name) / "helper.sock"
        helper_executor = FakeExecutor()
        helper_service = HelperService(
            NetworkApplier(
                executor=helper_executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        self.helper_server = UnixHelperServer(socket_path, helper_service)
        self.helper_thread = threading.Thread(target=self.helper_server.serve_forever)
        self.helper_thread.start()
        self.helper_executor = helper_executor

        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(
            Profile(
                id="slow",
                name="Slow",
                upload=Impairment(delay_ms=300),
            )
        )
        self.repository.save_device(
            Device(
                id="alice",
                name="Alice",
                ipv4_address="192.168.50.10",
                profile_id="slow",
            )
        )
        self.api_server = ControlServer(
            ("127.0.0.1", 0),
            self.repository,
            admin_token="test-token-123456",
            helper_client=HelperClient(socket_path=socket_path),
        )

    def tearDown(self) -> None:
        self.api_server.server_close()
        self.repository.close()
        self.helper_server.shutdown()
        self.helper_server.server_close()
        self.helper_thread.join(timeout=2)
        self.temp_dir.cleanup()

    def test_apply_and_reset_cross_the_unix_socket_boundary(self) -> None:
        apply_status, _, apply_body = self.api_server.write_response_for("/api/apply", {})
        reset_status, _, reset_body = self.api_server.write_response_for("/api/reset", {})

        self.assertEqual(apply_status, 200)
        self.assertEqual(reset_status, 200)
        self.assertGreater(len(self.helper_executor.commands), 0)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")
        self.assertIn("commands_run", apply_body.decode("utf-8"))
        self.assertIn("commands_run", reset_body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
