from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClient, HelperService, UnixHelperServer
from jittersim.networking.topology import Topology


class ApiHelperIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        socket_path = Path(self.temp_dir.name) / "helper.sock"
        self.active_plan_path = Path(self.temp_dir.name) / "active-plan.json"
        helper_executor = FakeExecutor()
        helper_service = HelperService(
            NetworkApplier(
                executor=helper_executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            active_plan_path=self.active_plan_path,
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

    def test_failed_manual_apply_recovers_the_same_plan_after_helper_cleanup(self) -> None:
        scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.api_server.helper_client
        )
        self.api_server.scheduler = scheduler
        with patch.object(scheduler, "start"):
            self.assertEqual(self.api_server.write_response_for("/api/apply", {})[0], 200)
            self.assertTrue(self.active_plan_path.exists())
            original_plan = scheduler.applied_plan()
            # Fail after inspecting the two root queues so the helper cleans up.
            self.helper_executor.fail_on_index = len(self.helper_executor.commands) + 2

            with self.assertLogs(level="ERROR"):
                status, _, _ = self.api_server.write_response_for("/api/apply", {})

            self.assertEqual(status, 502)
            self.assertFalse(self.active_plan_path.exists())
            self.assertIsNone(scheduler.applied_plan())
            self.assertIsNone(scheduler.applied_queue_bandwidth_limits())
            self.assertTrue(scheduler.tick(repository=self.repository))
            self.assertTrue(self.active_plan_path.exists())
            self.assertEqual(scheduler.applied_plan(), original_plan)

    def test_reset_after_a_failed_apply_prevents_background_recovery(self) -> None:
        scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.api_server.helper_client
        )
        self.api_server.scheduler = scheduler
        with patch.object(scheduler, "start"):
            self.assertEqual(self.api_server.write_response_for("/api/apply", {})[0], 200)
            self.helper_executor.fail_on_index = len(self.helper_executor.commands) + 2
            with self.assertLogs(level="ERROR"):
                self.assertEqual(self.api_server.write_response_for("/api/apply", {})[0], 502)
            self.assertEqual(self.api_server.write_response_for("/api/reset", {})[0], 200)

            self.assertFalse(scheduler.tick(repository=self.repository))
            self.assertFalse(self.active_plan_path.exists())
            self.assertEqual(self.repository.get_runtime_state().last_action, "reset")


if __name__ == "__main__":
    unittest.main()
