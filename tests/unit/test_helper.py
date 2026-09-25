import json
import tempfile
import threading
import unittest
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.browser_proxy import load_active_plan
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, FakeExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperError,
    HelperService,
    UnixHelperServer,
    _merge_connected_device_output,
    _parse_dhcp_lease_output,
    _parse_neighbor_output,
    _parse_queue_statistics,
    _parse_station_output,
    _parse_traffic_counter_output,
    run_helper,
)
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.router import RouterWifiSettings
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


# Real kernel output after applying 50% upload loss and 20 ms download delay
# to alice (mark 1) and sending 40 pings.
NFT_COUNTERS_WITH_MARKS = (
    '{"nftables":['
    '{"rule":{"comment":"usage-device alice upload","expr":['
    '{"mangle":{"key":{"meta":{"key":"mark"}},"value":1}},'
    '{"counter":{"packets":40,"bytes":3360}}]}},'
    '{"rule":{"comment":"usage-device alice download","expr":['
    '{"mangle":{"key":{"meta":{"key":"mark"}},"value":1}},'
    '{"counter":{"packets":16,"bytes":1344}}]}}]}'
)
TC_UPLOAD_STATISTICS = (
    '[{"kind":"prio","handle":"10:","root":true,"drops":24,"backlog":0,"qlen":0},'
    '{"kind":"netem","handle":"11:","parent":"10:2","bytes":1568,"packets":16,'
    '"drops":24,"backlog":0,"qlen":0}]'
)
TC_DOWNLOAD_STATISTICS = (
    '[{"kind":"prio","handle":"10:","root":true,"drops":0,"backlog":0,"qlen":0},'
    '{"kind":"netem","handle":"11:","parent":"10:2","bytes":1568,"packets":16,'
    '"drops":0,"backlog":196,"qlen":2}]'
)


class CounterExecutor(FakeExecutor):
    """Answer nft and tc statistics commands the way the kernel does."""

    def run(self, command):
        self.commands.append(command)
        if command.executable == "nft":
            return CommandResult(return_code=0, stdout=NFT_COUNTERS_WITH_MARKS)
        interface = command.argv[-1]
        output = TC_UPLOAD_STATISTICS if interface == "eth0" else TC_DOWNLOAD_STATISTICS
        return CommandResult(return_code=0, stdout=output)


class UplinkOutputExecutor(FakeExecutor):
    def run(self, command):
        self.commands.append(command)
        if command.executable == "nmcli":
            return CommandResult(return_code=0, stdout="netplan-eth0\n")
        return CommandResult(
            return_code=0,
            stdout="default via 192.168.0.1 dev eth0 proto dhcp src 192.168.0.17\n",
        )


class FakeRouter:
    def __init__(self, address, password) -> None:
        self.address = address
        self.password = password
        self.logged_in = False
        self.closed = False
        self.enabled = True

    def login(self) -> None:
        self.logged_in = True

    def read_wifi_settings(self) -> RouterWifiSettings:
        return RouterWifiSettings("Vodafone-Test", "router-wifi-password", self.enabled)

    def set_wifi_enabled(self, *, enabled: bool) -> RouterWifiSettings:
        self.enabled = enabled
        return RouterWifiSettings("", "", enabled)

    def close(self) -> None:
        self.closed = True


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

    def test_failed_apply_logs_bounded_diagnostics_without_returning_them(self) -> None:
        class ApplyFailureExecutor(FakeExecutor):
            def run(self, command):
                self.commands.append(command)
                if command.executable == "nft" and command.arguments == ("-f", "-"):
                    return CommandResult(
                        return_code=1,
                        stderr="private-detail " * 100,
                    )
                return CommandResult(return_code=0)

        service = HelperService(
            NetworkApplier(
                executor=ApplyFailureExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        with self.assertLogs("jittersim.networking.helper", level="ERROR") as captured:
            response = service.handle_payload(
                {"action": "apply", "plan": plan_to_payload(make_plan())}
            )

        self.assertEqual(
            response,
            {
                "ok": False,
                "error": "network plan apply failed",
                "cleanup_failed": False,
            },
        )
        diagnostic = captured.records[0].getMessage()
        self.assertIn("step 1", diagnostic)
        self.assertIn("exit code 1", diagnostic)
        self.assertIn("private-detail", diagnostic)
        self.assertLessEqual(len(diagnostic), 900)
        self.assertNotIn("private-detail", str(response))

    def test_handles_reset_as_a_separate_action(self) -> None:
        response = self.service.handle_payload({"action": "reset"})

        self.assertEqual(response["action"], "reset")
        self.assertEqual(response["commands_run"], 1)

    def test_publishes_and_clears_the_proxy_plan_with_network_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "active-plan.json"
            service = HelperService(
                NetworkApplier(
                    executor=FakeExecutor(),
                    topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
                ),
                active_plan_path=plan_path,
            )
            response = service.handle_payload(
                {"action": "apply", "plan": plan_to_payload(make_plan())}
            )
            self.assertTrue(response["ok"])
            self.assertEqual(load_active_plan(plan_path), make_plan())
            self.assertTrue(service.handle_payload({"action": "reset"})["ok"])
            self.assertFalse(plan_path.exists())

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

    def test_copies_router_wifi_to_the_local_access_point(self) -> None:
        executor = UplinkOutputExecutor()
        routers = []

        def router_factory(address, password):
            router = FakeRouter(address, password)
            routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=router_factory,
        )

        response = service.handle_payload(
            {"action": "router-sync-ap", "admin_password": "router-secret"}
        )

        self.assertEqual(response["action"], "router-sync-ap")
        self.assertEqual(response["ssid"], "Vodafone-Test")
        self.assertEqual(routers[0].address, "192.168.0.1")
        self.assertEqual(routers[0].password, "router-secret")
        self.assertTrue(routers[0].logged_in)
        self.assertTrue(routers[0].closed)
        self.assertEqual(executor.commands[2].arguments[2], "jittersim-ap")

    def test_changes_router_wifi_through_the_helper(self) -> None:
        executor = UplinkOutputExecutor()
        routers = []

        def router_factory(address, password):
            router = FakeRouter(address, password)
            routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=router_factory,
        )

        response = service.handle_payload(
            {
                "action": "router-wifi",
                "admin_password": "router-secret",
                "enabled": False,
            }
        )

        self.assertEqual(response, {"ok": True, "action": "router-wifi", "enabled": False})
        self.assertTrue(routers[0].logged_in)
        self.assertTrue(routers[0].closed)

    def test_rejects_short_access_point_passwords(self) -> None:
        response = self.service.handle_payload(
            {"action": "configure-ap", "ssid": "JitterSim-Test", "password": "short"}
        )

        self.assertEqual(response, {"ok": False, "error": "invalid access point settings"})
        self.assertEqual(self.executor.commands, [])

    def test_lists_associated_wifi_clients_and_joins_local_details(self) -> None:
        station_output = (
            "Station dc:1e:d5:3e:97:4c (on wlan0)\n"
            "\tsignal: -47 dBm\n\tconnected time: 95 seconds\n"
            "Station 00:11:22:33:44:55 (on wlan0)\n\tsignal: -70 dBm\n"
        )
        neighbor_output = (
            "192.168.50.178 dev wlan0 lladdr DC:1E:D5:3E:97:4C REACHABLE\n"
            "192.168.50.10 dev wlan0 lladdr 00:11:22:33:44:55 STALE\n"
            "192.168.50.99 dev wlan0 INCOMPLETE\n"
            "invalid dev wlan0 lladdr 00:11:22:33:44:66 REACHABLE\n"
        )

        class ClientOutputExecutor(FakeExecutor):
            def run(self, command):
                self.commands.append(command)
                output = station_output if command.executable == "iw" else neighbor_output
                return CommandResult(return_code=0, stdout=output)

        executor = ClientOutputExecutor()
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "dnsmasq-wlan0.leases").write_text(
                "4102444800 dc:1e:d5:3e:97:4c 192.168.50.178 MacBook-Pro *\n"
                "4102444800 00:11:22:33:44:55 192.168.50.10 * *\n",
                encoding="utf-8",
            )
            service = HelperService(
                NetworkApplier(
                    executor=executor,
                    topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
                    lease_directory=Path(temp_dir),
                )
            )
            response = service.handle_payload({"action": "clients"})

        self.assertEqual(response["action"], "clients")
        self.assertTrue(response["association_status_available"])
        self.assertEqual(
            response["devices"],
            [
                {
                    "mac_address": "00:11:22:33:44:55",
                    "signal_dbm": -70,
                    "ipv4_address": "192.168.50.10",
                    "state": "connected",
                    "association_verified": True,
                    "neighbor_state": "stale",
                    "hostname": None,
                },
                {
                    "mac_address": "dc:1e:d5:3e:97:4c",
                    "signal_dbm": -47,
                    "connected_seconds": 95,
                    "ipv4_address": "192.168.50.178",
                    "state": "connected",
                    "association_verified": True,
                    "neighbor_state": "reachable",
                    "hostname": "MacBook-Pro",
                },
            ],
        )
        self.assertEqual(
            [command.argv for command in executor.commands],
            [
                ("iw", "dev", "wlan0", "station", "dump"),
                ("ip", "-4", "neigh", "show", "dev", "wlan0"),
            ],
        )

    def test_station_and_lease_parsers_ignore_bad_and_expired_rows(self) -> None:
        stations = _parse_station_output(
            "Station AA:BB:CC:DD:EE:02 (on wlan0)\n"
            " signal: -45 dBm\n"
            " connected time: 50 seconds\n"
            "Station bad-mac (on wlan0)\n"
            "Station AA:BB:CC:DD:EE:02 (on wlan0)\n"
            " signal: -140 dBm\n"
        )
        leases = _parse_dhcp_lease_output(
            "4102444800 aa:bb:cc:dd:ee:02 192.168.50.10 iPhone *\n"
            "1 aa:bb:cc:dd:ee:03 192.168.50.11 stale *\n"
            "broken row\n"
            "4102444800 01:bb:cc:dd:ee:04 192.168.50.12 multicast *\n"
            "4102444800 aa:bb:cc:dd:ee:05 invalid bad-ip *\n",
            observed_at=100,
        )

        self.assertEqual(
            stations,
            [
                {
                    "mac_address": "aa:bb:cc:dd:ee:02",
                    "signal_dbm": -45,
                    "connected_seconds": 50,
                }
            ],
        )
        self.assertEqual(
            leases,
            {
                "aa:bb:cc:dd:ee:02": {
                    "ipv4_address": "192.168.50.10",
                    "hostname": "iPhone",
                    "expiry": 4102444800,
                }
            },
        )

    def test_connected_clients_exclude_neighbor_entries_without_a_station(self) -> None:
        devices = _merge_connected_device_output(
            "Station aa:bb:cc:dd:ee:02 (on wlan0)\n",
            "192.168.50.10 dev wlan0 lladdr aa:bb:cc:dd:ee:02 STALE\n"
            "192.168.50.11 dev wlan0 lladdr aa:bb:cc:dd:ee:03 REACHABLE\n",
            "",
            observed_at=100,
        )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["ipv4_address"], "192.168.50.10")
        self.assertEqual(devices[0]["state"], "connected")

    def test_station_without_ip_remains_visible(self) -> None:
        devices = _merge_connected_device_output(
            "Station aa:bb:cc:dd:ee:02 (on wlan0)\n",
            "",
            "",
            observed_at=100,
        )

        self.assertIsNone(devices[0]["ipv4_address"])

    def test_station_parser_limits_the_number_of_clients(self) -> None:
        output = "\n".join(
            f"Station 02:00:00:{index >> 16:02x}:{index >> 8 & 255:02x}:{index & 255:02x}"
            for index in range(257)
        )

        self.assertEqual(len(_parse_station_output(output)), 256)

    def test_neighbor_details_remain_visible_when_station_status_fails(self) -> None:
        class StationFailureExecutor(FakeExecutor):
            def run(self, command):
                self.commands.append(command)
                if command.executable == "iw":
                    raise CommandExecutionError("iw is unavailable")
                return CommandResult(
                    return_code=0,
                    stdout="192.168.50.10 dev wlan0 lladdr aa:bb:cc:dd:ee:02 STALE\n",
                )

        executor = StationFailureExecutor()
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload({"action": "clients"})

        self.assertFalse(response["association_status_available"])
        self.assertEqual(response["devices"][0]["state"], "unknown")
        self.assertFalse(response["devices"][0]["association_verified"])

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

    def test_reads_device_and_service_traffic_counters(self) -> None:
        executor = OutputExecutor(
            '{"nftables":['
            '{"rule":{"comment":"usage-device alice upload",'
            '"expr":[{"counter":{"packets":3,"bytes":1200}}]}},'
            '{"rule":{"comment":"usage-device alice download",'
            '"expr":[{"counter":{"packets":4,"bytes":800}}]}},'
            '{"rule":{"comment":"usage-device alice upload",'
            '"expr":[{"counter":{"packets":1,"bytes":100}}]}},'
            '{"rule":{"comment":"usage-service alice-video upload",'
            '"expr":[{"counter":{"packets":1,"bytes":300}}]}},'
            '{"rule":{"comment":"unrelated rule",'
            '"expr":[{"counter":{"packets":1,"bytes":9000}}]}}]}'
        )
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        # This executor answers tc with nftables output, so queue figures are skipped.
        with self.assertLogs("jittersim.networking.helper", level="WARNING"):
            response = service.handle_payload({"action": "traffic-counters"})

        self.assertEqual(response["action"], "traffic-counters")
        self.assertEqual(
            response["counters"],
            {
                "devices": {"alice": 2_100},
                "services": {"alice-video": 300},
                "directions": {
                    "devices": {
                        "alice": {
                            "upload_bytes": 1_300,
                            "download_bytes": 800,
                            "upload_packets": 4,
                            "download_packets": 4,
                        }
                    },
                    "services": {
                        "alice-video": {
                            "upload_bytes": 300,
                            "download_bytes": 0,
                            "upload_packets": 1,
                            "download_packets": 0,
                        }
                    },
                },
                "queues": {"devices": {}},
            },
        )
        self.assertEqual(
            executor.commands[0].argv,
            ("nft", "-j", "list", "table", "inet", "jittersim"),
        )

    def test_reports_what_each_device_queue_dropped_or_holds(self) -> None:
        executor = CounterExecutor()
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload({"action": "traffic-counters"})

        self.assertEqual(
            response["counters"]["queues"],
            {
                "devices": {
                    "alice": {
                        "upload_dropped": 24,
                        "download_dropped": 0,
                        "upload_queued": 0,
                        "download_queued": 2,
                    }
                }
            },
        )
        self.assertEqual(
            [command.argv for command in executor.commands[1:]],
            [
                ("tc", "-s", "-j", "qdisc", "show", "dev", "eth0"),
                ("tc", "-s", "-j", "qdisc", "show", "dev", "wlan0"),
            ],
        )

    def test_queue_figures_include_browser_service_profile_queues(self) -> None:
        # The output chain counts proxied traffic by the service profile's mark.
        nft = (
            '{"nftables":['
            '{"rule":{"comment":"usage-device alice upload","expr":['
            '{"mangle":{"key":{"meta":{"key":"mark"}},"value":1}},'
            '{"counter":{"packets":40,"bytes":3360}}]}},'
            '{"rule":{"comment":"usage-device alice upload","expr":['
            '{"match":{"op":"==","left":{"meta":{"key":"oifname"}},"right":"eth0"}},'
            '{"match":{"op":"==","left":{"meta":{"key":"mark"}},"right":2}},'
            '{"counter":{"packets":5,"bytes":3000}}]}}]}'
        )
        upload = (
            '[{"kind":"netem","handle":"41:","parent":"10:2","drops":24,"qlen":0},'
            '{"kind":"netem","handle":"42:","parent":"10:3","drops":500,"qlen":7}]'
        )

        class Executor(FakeExecutor):
            def run(self, command):
                self.commands.append(command)
                if command.executable == "nft":
                    return CommandResult(return_code=0, stdout=nft)
                return CommandResult(
                    return_code=0, stdout=upload if command.argv[-1] == "eth0" else "[]"
                )

        service = HelperService(
            NetworkApplier(
                executor=Executor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        queues = service.handle_payload({"action": "traffic-counters"})["counters"]["queues"]

        self.assertEqual(queues["devices"]["alice"]["upload_dropped"], 524)
        self.assertEqual(queues["devices"]["alice"]["upload_queued"], 7)

    def test_queue_figures_include_an_active_service_limit_queue(self) -> None:
        # A service limit's rules match the device's address, not its name.
        nft = (
            '{"nftables":['
            '{"rule":{"comment":"usage-device alice upload","expr":['
            '{"match":{"op":"==","left":{"payload":{"protocol":"ip","field":"saddr"}},'
            '"right":"192.168.50.10"}},'
            '{"mangle":{"key":{"meta":{"key":"mark"}},"value":1}},'
            '{"counter":{"packets":40,"bytes":3360}}]}},'
            '{"rule":{"comment":"apply-service-quota video upload","expr":['
            '{"match":{"op":"==","left":{"payload":{"protocol":"ip","field":"saddr"}},'
            '"right":"192.168.50.10"}},'
            '{"match":{"op":"==","left":{"payload":{"protocol":"ip","field":"daddr"}},'
            '"right":{"set":["203.0.113.10"]}}},'
            '{"mangle":{"key":{"meta":{"key":"mark"}},"value":3}}]}}]}'
        )
        upload = (
            '[{"kind":"netem","handle":"41:","parent":"10:2","drops":5,"qlen":0},'
            '{"kind":"netem","handle":"43:","parent":"10:4","drops":40,"qlen":1}]'
        )

        class Executor(FakeExecutor):
            def run(self, command):
                self.commands.append(command)
                if command.executable == "nft":
                    return CommandResult(return_code=0, stdout=nft)
                return CommandResult(
                    return_code=0, stdout=upload if command.argv[-1] == "eth0" else "[]"
                )

        service = HelperService(
            NetworkApplier(
                executor=Executor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        queues = service.handle_payload({"action": "traffic-counters"})["counters"]["queues"]

        self.assertEqual(queues["devices"]["alice"]["upload_dropped"], 45)
        self.assertEqual(queues["devices"]["alice"]["upload_queued"], 1)

    def test_queue_statistics_follow_the_rendered_queue_layout(self) -> None:
        plan = compile_plan(
            devices=(
                Device("alice", "Alice", "192.168.50.10", "slow"),
                Device("bob", "Bob", "192.168.50.11", "normal"),
            ),
            profiles={
                "slow": Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100)),
                "normal": Profile(id="normal", name="Normal"),
            },
        )
        commands = render_command_plan(
            plan=plan, topology=Topology(uplink_interface="eth0", test_interface="wlan0")
        )
        # Answer as tc would for every child queue the renderer creates.
        qdiscs = {"upload": [], "download": []}
        for argv in (command.argv for command in commands):
            if argv[:3] == ("tc", "qdisc", "replace") and "parent" in argv:
                handle = argv.index("handle")
                qdiscs["upload" if argv[4] == "eth0" else "download"].append(
                    {
                        "kind": argv[handle + 2],
                        "handle": argv[handle + 1],
                        "parent": argv[argv.index("parent") + 1],
                        "drops": 7,
                        "qlen": 3,
                    }
                )

        statistics = _parse_queue_statistics(
            {direction: json.dumps(values) for direction, values in qdiscs.items()},
            marks={rule.device_id: {rule.mark} for rule in plan.mark_rules},
        )

        figures = {
            "upload_dropped": 7,
            "download_dropped": 7,
            "upload_queued": 3,
            "download_queued": 3,
        }
        self.assertEqual(statistics, {"devices": {"alice": figures, "bob": figures}})

    def test_rejects_invalid_traffic_counter_json(self) -> None:
        with self.assertRaises(ValueError):
            _parse_traffic_counter_output("not json")

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
        self.assertEqual(client.connected_devices(), [])
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

    def test_client_reads_directional_traffic_counters_over_the_socket(self) -> None:
        output = (
            '{"nftables":[{"rule":{"comment":"usage-device alice upload",'
            '"expr":[{"counter":{"packets":3,"bytes":1200}}]}}]}'
        )
        traffic_socket = Path(self.temp_dir.name) / "traffic.sock"
        traffic_service = HelperService(
            NetworkApplier(
                executor=OutputExecutor(output),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        traffic_server = UnixHelperServer(traffic_socket, traffic_service)
        traffic_thread = threading.Thread(target=traffic_server.serve_forever)
        traffic_thread.start()
        try:
            with self.assertLogs("jittersim.networking.helper", level="WARNING"):
                counters = HelperClient(socket_path=traffic_socket).traffic_counters()
        finally:
            traffic_server.shutdown()
            traffic_server.server_close()
            traffic_thread.join(timeout=2)

        self.assertEqual(counters["devices"], {"alice": 1_200})
        self.assertEqual(counters["queues"], {"devices": {}})
        self.assertEqual(
            counters["directions"]["devices"]["alice"],
            {
                "upload_bytes": 1_200,
                "download_bytes": 0,
                "upload_packets": 3,
                "download_packets": 0,
            },
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
