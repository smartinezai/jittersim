from pathlib import Path
import tempfile
import threading
import unittest

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperError,
    HelperService,
    UnixHelperServer,
    _parse_neighbor_output,
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


class OutputExecutor(FakeExecutor):
    def __init__(self, output: str) -> None:
        super().__init__()
        self.output = output

    def run(self, command):
        self.commands.append(command)
        return CommandResult(return_code=0, stdout=self.output)


class UplinkOutputExecutor(FakeExecutor):
    def run(self, command):
        self.commands.append(command)
        if command.executable == "nmcli":
            return CommandResult(return_code=0, stdout="netplan-eth0\n")
        return CommandResult(
            return_code=0,
            stdout="default via 192.168.0.1 dev eth0 proto dhcp src 192.168.0.17\n",
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

    def test_configures_the_local_access_point_without_router_commands(self) -> None:
        response = self.service.handle_payload(
            {
                "action": "configure-ap",
                "ssid": "JitterSim-Test",
                "password": "test-password-123",
            }
        )

        self.assertEqual(response["action"], "configure-ap")
        self.assertEqual(response["commands_run"], 3)
        self.assertEqual(self.executor.commands[0].executable, "nmcli")
        self.assertEqual(self.executor.commands[0].arguments[2], "jittersim-ap")

    def test_rejects_short_access_point_passwords(self) -> None:
        response = self.service.handle_payload(
            {"action": "configure-ap", "ssid": "JitterSim-Test", "password": "short"}
        )

        self.assertEqual(response, {"ok": False, "error": "invalid access point settings"})
        self.assertEqual(self.executor.commands, [])

    def test_lists_valid_ipv4_neighbors_without_failed_entries(self) -> None:
        executor = OutputExecutor(
            """
            192.168.50.178 dev wlan0 lladdr DC:1E:D5:3E:97:4C REACHABLE
            192.168.50.10 dev wlan0 lladdr 00:11:22:33:44:55 STALE
            192.168.50.99 dev wlan0 INCOMPLETE
            invalid dev wlan0 lladdr 00:11:22:33:44:66 REACHABLE
            """
        )
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload({"action": "clients"})

        self.assertEqual(response["action"], "clients")
        self.assertEqual(
            response["devices"],
            [
                {
                    "ipv4_address": "192.168.50.10",
                    "mac_address": "00:11:22:33:44:55",
                    "state": "stale",
                },
                {
                    "ipv4_address": "192.168.50.178",
                    "mac_address": "dc:1e:d5:3e:97:4c",
                    "state": "reachable",
                },
            ],
        )
        self.assertEqual(
            executor.commands[0].argv,
            ("ip", "-4", "neigh", "show", "dev", "wlan0"),
        )

    def test_reports_the_pi_ethernet_connection_and_gateway(self) -> None:
        executor = UplinkOutputExecutor()
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload({"action": "uplink-info"})

        self.assertEqual(response["action"], "uplink-info")
        self.assertEqual(
            response["uplink"],
            {
                "interface": "eth0",
                "connection_name": "netplan-eth0",
                "gateway": "192.168.0.1",
            },
        )
        self.assertEqual(
            [command.argv for command in executor.commands],
            [
                ("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "eth0"),
                ("ip", "-4", "route", "show", "default", "dev", "eth0"),
            ],
        )

    def test_parses_dns_observations_by_client_and_domain(self) -> None:
        executor = OutputExecutor(
            """
            19:10:00.000000 IP 192.168.50.178.53124 > 192.168.50.1.53: 1234+ A? amazon.com. (29)
            19:10:01.000000 IP 192.168.50.178.53125 > 192.168.50.1.53: 1235+ A? amazon.com. (29)
            19:10:02.000000 IP 192.168.50.10.53126 > 192.168.50.1.53: 1236+ AAAA? chat.openai.com. (35)
            """
        )
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload({"action": "observe-dns"})

        self.assertEqual(response["action"], "observe-dns")
        observations_by_domain = {
            observation["domain"]: observation for observation in response["observations"]
        }
        self.assertEqual(observations_by_domain["amazon.com"]["ipv4_address"], "192.168.50.178")
        self.assertEqual(observations_by_domain["amazon.com"]["query_count"], 2)
        self.assertEqual(observations_by_domain["chat.openai.com"]["ipv4_address"], "192.168.50.10")
        self.assertEqual(observations_by_domain["chat.openai.com"]["query_count"], 1)
        self.assertEqual(
            executor.commands[0].argv,
            (
                "tcpdump",
                "-i",
                "wlan0",
                "-n",
                "-l",
                "-s",
                "256",
                "-c",
                "128",
                "udp",
                "port",
                "53",
                "or",
                "tcp",
                "port",
                "53",
            ),
        )
        self.assertEqual(executor.commands[0].timeout_seconds, 5.0)
        self.assertTrue(executor.commands[0].timeout_is_success)

    def test_neighbor_parser_ignores_malformed_lines(self) -> None:
        self.assertEqual(
            _parse_neighbor_output("192.168.50.10 dev wlan0 lladdr not-a-mac STALE"),
            [],
        )

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
        self.assertEqual(
            client.configure_access_point(
                ssid="JitterSim-Test",
                password="test-password-123",
            )["action"],
            "configure-ap",
        )
        self.assertEqual(
            client.uplink_info(),
            {"interface": "eth0", "connection_name": None, "gateway": None},
        )

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
