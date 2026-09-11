from pathlib import Path
import tempfile
import threading
import unittest

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperError,
    HelperService,
    UnixHelperServer,
    run_helper,
)
from jittersim.networking.topology import Topology


def make_plan():
    profile = Profile(
        id="slow",
        name="Slow",
        upload=Impairment(delay_ms=300),
        download=Impairment(loss_percent=10),
    )
    return compile_plan(
        devices=(Device("alice", "Alice", "192.168.50.10", "slow"),),
        profiles={"slow": profile},
    )


class HelperServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = FakeExecutor()
        self.service = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

    def test_handles_ping_without_running_network_commands(self) -> None:
        response = self.service.handle_payload({"action": "ping"})

        self.assertEqual(response, {"ok": True, "action": "ping"})
        self.assertEqual(self.executor.commands, [])

    def test_decodes_and_applies_a_plan(self) -> None:
        response = self.service.handle_payload(
            {"action": "apply", "plan": {"mark_rules": [], "queues": []}}
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["action"], "apply")
        self.assertGreater(response["commands_run"], 0)

    def test_rejects_malformed_plans_without_running_commands(self) -> None:
        response = self.service.handle_payload(
            {"action": "apply", "plan": {"mark_rules": "bad", "queues": []}}
        )

        self.assertEqual(response, {"ok": False, "error": "invalid network plan"})
        self.assertEqual(self.executor.commands, [])

    def test_handles_reset_as_a_separate_action(self) -> None:
        response = self.service.handle_payload({"action": "reset"})

        self.assertEqual(response["action"], "reset")
        self.assertEqual(response["commands_run"], 3)

    def test_rejects_unknown_actions(self) -> None:
        response = self.service.handle_payload({"action": "shell"})

        self.assertEqual(response, {"ok": False, "error": "unknown action"})


class UnixHelperServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp_dir.name) / "helper.sock"
        self.executor = FakeExecutor()
        service = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        self.server = UnixHelperServer(self.socket_path, service)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def test_client_can_ping_apply_and_reset_over_the_socket(self) -> None:
        client = HelperClient(socket_path=self.socket_path)

        self.assertEqual(client.ping()["action"], "ping")
        self.assertEqual(client.apply(plan=make_plan())["action"], "apply")
        self.assertEqual(client.reset()["action"], "reset")

    def test_existing_regular_file_is_never_replaced_by_a_socket(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.socket_path.write_text("keep me")

        with self.assertRaisesRegex(HelperError, "not a socket"):
            UnixHelperServer(
                self.socket_path,
                HelperService(
                    NetworkApplier(
                        executor=FakeExecutor(),
                        topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
                    )
                ),
            )
        self.assertEqual(self.socket_path.read_text(), "keep me")

    def test_hardware_mode_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(HelperError, "enable-hardware"):
            run_helper(
                socket_path=self.socket_path,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )


if __name__ == "__main__":
    unittest.main()
