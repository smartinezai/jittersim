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
from tests.wifi_support import WifiExecutor
from jittersim.networking.helper import (
    HelperClient,
    HelperClientError,
    HelperError,
    HelperService,
    UnixHelperServer,
    _merge_connected_device_output,
    _parse_dhcp_lease_output,
    _parse_neighbor_output,
    _parse_station_output,
    _parse_traffic_counter_output,
    run_helper,
)
from jittersim.networking.router import (
    ROUTER_CHECK_MESSAGES,
    ROUTER_IMPORT_MESSAGES,
    ROUTER_WIFI_MESSAGES,
    RouterError,
    RouterWifiSettings,
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
        if command.arguments == ("-j", "list", "table", "netdev", "jittersim_download"):
            return CommandResult(return_code=0, stdout='{"nftables":[]}')
        return CommandResult(return_code=0, stdout=self.output)


class UplinkOutputExecutor(WifiExecutor):
    def run(self, command):
        if command.executable == "wpa_cli":
            return super().run(command)
        if command.executable == "nmcli" and (
            "connection" in command.arguments or command.arguments[-1] == "wlan0"
        ):
            return super().run(command)
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

    def limit_operation(self, seconds) -> None:
        self.limit = seconds

    def login(self, **kwargs) -> None:
        self.logged_in = True

    def read_wifi_settings(self) -> RouterWifiSettings:
        return RouterWifiSettings("Vodafone-Test", "router-wifi-password", self.enabled, "WPA2", "2.4 GHz")

    def read_wifi_enabled(self):
        return self.enabled

    def set_wifi_enabled(self, *, enabled: bool) -> RouterWifiSettings:
        self.enabled = enabled
        return RouterWifiSettings("", "", enabled)

    def close(self) -> None:
        self.closed = True


class HelperServiceTests(unittest.TestCase):
    def test_router_status_reads_without_writing_and_closes_session(self):
        routers = []

        def factory(address, password):
            router = FakeRouter(address, password)
            router.enabled = False
            routers.append(router)
            return router

        service = HelperService(NetworkApplier(executor=self.executor,
                                topology=Topology("eth0", "wlan0")), router_factory=factory)
        service._applier.uplink_info = lambda: {"gateway": "192.168.0.1"}
        result = service.handle_payload({"action": "router-status", "admin_password": "fixture"})
        self.assertEqual(result, {"ok": True, "action": "router-status", "enabled": False})
        self.assertTrue(routers[0].closed)
        self.assertLessEqual(routers[0].limit, 15)

    def test_all_router_operations_share_one_session_lock(self):
        self.service._router_lock.acquire()
        try:
            for action in ("router-check", "router-wifi", "router-sync-ap", "router-preview-ap", "router-status"):
                self.assertEqual(self.service.handle_payload({"action": action})["reason"], "helper-busy")
        finally:
            self.service._router_lock.release()
    def setUp(self) -> None:
        self.executor = WifiExecutor()
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
                if command.executable == "nft" and command.arguments == ("-f", "-") and command.purpose != "remove obsolete JitterSim firewall tables":
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
        self.assertEqual(response["commands_run"], 3)

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
        self.assertGreater(response["commands_run"], 10)
        self.assertEqual(self.executor.commands[0].executable, "nmcli")
        self.assertEqual(self.executor.settings["802-11-wireless-security.key-mgmt"], "sae")

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

        preview = service.handle_payload({"action": "router-preview-ap", "admin_password": "router-secret"})
        response = service.handle_payload(
            {"action": "router-sync-ap", "admin_password": "router-secret",
             "expected_fingerprint": preview["fingerprint"]}
        )

        self.assertEqual(response["action"], "router-sync-ap")
        self.assertEqual(response["ssid"], "Vodafone-Test")
        self.assertEqual(routers[0].address, "192.168.0.1")
        self.assertEqual(routers[0].password, "router-secret")
        self.assertTrue(routers[0].logged_in)
        self.assertTrue(routers[0].closed)
        self.assertEqual(executor.settings["802-11-wireless-security.proto"], "rsn")
        self.assertEqual(executor.settings["802-11-wireless-security.key-mgmt"], "wpa-psk")

    def test_failed_router_read_never_changes_ap_or_logs_credentials(self) -> None:
        class FailedReadRouter(FakeRouter):
            def read_wifi_settings(self):
                raise RouterError("response contains router-wifi-password", reason="unsupported")

        executor = UplinkOutputExecutor()
        routers = []

        def factory(address, password):
            router = FailedReadRouter(address, password)
            routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=factory,
        )
        with self.assertLogs("jittersim.networking.helper", level="WARNING") as logs:
            response = service.handle_payload(
                {"action": "router-sync-ap", "admin_password": "router-secret"}
            )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], ROUTER_IMPORT_MESSAGES["unsupported"])
        self.assertTrue(routers[0].closed)
        self.assertFalse(any("modify" in command.arguments for command in executor.commands))
        for secret in ("router-secret", "router-wifi-password"):
            self.assertNotIn(secret, "\n".join(logs.output) + str(response))

    def test_failed_ap_update_does_not_log_password_from_exception_chain(self) -> None:
        class FailedApExecutor(UplinkOutputExecutor):
            def run(self, command):
                if command.executable == "nmcli" and "modify" in command.arguments:
                    raise CommandExecutionError("update failed") from RuntimeError(
                        "router-wifi-password"
                    )
                return super().run(command)

        service = HelperService(
            NetworkApplier(
                executor=FailedApExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=FakeRouter,
        )
        preview = service.handle_payload({"action": "router-preview-ap", "admin_password": "router-secret"})
        with self.assertLogs("jittersim.networking.helper", level="WARNING") as logs:
            response = service.handle_payload(
                {"action": "router-sync-ap", "admin_password": "router-secret",
                 "expected_fingerprint": preview["fingerprint"]}
            )

        self.assertEqual(response["error"], ROUTER_IMPORT_MESSAGES["restore-failed"])
        self.assertNotIn("router-wifi-password", "\n".join(logs.output) + str(response))

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

    def test_router_toggle_returns_safe_guidance_and_always_closes_the_session(self):
        class RejectedRouter(FakeRouter):
            def set_wifi_enabled(self, *, enabled):
                raise RouterError("router-secret wifi-secret response contents", reason="uncertain")

        routers = []

        def router_factory(address, password):
            router = RejectedRouter(address, password)
            routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=UplinkOutputExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=router_factory,
        )
        with self.assertLogs("jittersim.networking.helper", level="WARNING") as logs:
            response = service.handle_payload(
                {
                    "action": "router-wifi",
                    "admin_password": "router-secret",
                    "enabled": False,
                }
            )
        self.assertEqual(response["error"], ROUTER_WIFI_MESSAGES["uncertain"])
        self.assertTrue(routers[0].closed)
        recorded = str(response) + "\n".join(logs.output)
        for secret in ("router-secret", "wifi-secret", "response contents"):
            self.assertNotIn(secret, recorded)

    def _router_check_service(self, router_class=FakeRouter):
        routers = []

        def router_factory(address, password):
            router = router_class(address, password)
            routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=UplinkOutputExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=router_factory,
        )
        return service, routers

    def test_checks_the_router_password_without_reading_or_changing_anything(self) -> None:
        class WatchedRouter(FakeRouter):
            def read_wifi_settings(self):
                raise AssertionError("a password check must not read Wi-Fi settings")

            def set_wifi_enabled(self, *, enabled):
                raise AssertionError("a password check must not change Wi-Fi")

        service, routers = self._router_check_service(WatchedRouter)

        response = service.handle_payload(
            {"action": "router-check", "admin_password": "router-secret"}
        )

        self.assertEqual(response, {"ok": True, "action": "router-check"})
        self.assertEqual(routers[0].address, "192.168.0.1")
        self.assertEqual(routers[0].password, "router-secret")
        self.assertTrue(routers[0].logged_in)
        self.assertTrue(routers[0].closed)

    def test_router_check_returns_safe_guidance_and_always_closes_the_session(self) -> None:
        for reason, expected in (
            ("login", ROUTER_CHECK_MESSAGES["login"]),
            ("unreachable", ROUTER_CHECK_MESSAGES["unreachable"]),
            ("uncertain", ROUTER_CHECK_MESSAGES["failed"]),
        ):
            with self.subTest(reason=reason):

                class RejectedRouter(FakeRouter):
                    def login(self):
                        raise RouterError("router-secret response contents", reason=reason)

                service, routers = self._router_check_service(RejectedRouter)
                with self.assertLogs("jittersim.networking.helper", level="WARNING") as logs:
                    response = service.handle_payload(
                        {"action": "router-check", "admin_password": "router-secret"}
                    )
                self.assertEqual(response, {"ok": False, "error": expected})
                self.assertTrue(routers[0].closed)
                recorded = str(response) + "\n".join(logs.output)
                for secret in ("router-secret", "response contents"):
                    self.assertNotIn(secret, recorded)

    def test_router_check_needs_a_password_before_contacting_the_router(self) -> None:
        service, routers = self._router_check_service()

        for payload in (
            {"action": "router-check"},
            {"action": "router-check", "admin_password": ""},
            {"action": "router-check", "admin_password": 7},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    service.handle_payload(payload),
                    {"ok": False, "error": "invalid router credentials"},
                )
        self.assertEqual(routers, [])

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

    def test_dns_observation_echoes_the_sorted_excluded_addresses(self) -> None:
        executor = OutputExecutor("")
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload(
            {
                "action": "observe-dns",
                "excluded_macs": ["02:00:00:00:00:02", "02-00-00-00-00-01", "02:00:00:00:00:02"],
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["excluded_macs"], ["02:00:00:00:00:01", "02:00:00:00:00:02"])
        arguments = executor.commands[0].arguments
        self.assertEqual(arguments.count("ether"), 2)
        self.assertLess(
            arguments.index("02:00:00:00:00:01"), arguments.index("02:00:00:00:00:02")
        )

    def test_dns_observation_without_exclusions_echoes_an_empty_list(self) -> None:
        service = HelperService(
            NetworkApplier(
                executor=OutputExecutor(""),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        response = service.handle_payload({"action": "observe-dns"})

        self.assertEqual(response["excluded_macs"], [])

    def test_dns_observation_rejects_invalid_or_too_many_excluded_addresses(self) -> None:
        executor = OutputExecutor("")
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        too_many = ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(65)]
        for bad in (["not-a-mac"], too_many, "02:00:00:00:00:01", [5], ["00:00:00:00:00:00"]):
            with self.subTest(bad=bad if len(bad) < 5 else "65 addresses"):
                response = service.handle_payload({"action": "observe-dns", "excluded_macs": bad})
                self.assertEqual(response, {"ok": False, "error": "invalid excluded devices"})

        self.assertEqual(executor.commands, [])

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
            },
        )
        self.assertEqual(
            executor.commands[0].argv,
            ("nft", "-j", "list", "table", "inet", "jittersim"),
        )

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


class RecordingHelperClient(HelperClient):
    """A client that answers from a canned response instead of a socket."""

    def __init__(self, response):
        super().__init__(socket_path=Path("unused.sock"))
        self.response = response
        self.sent = []

    def _request(self, payload, *, timeout_seconds=None):
        self.sent.append(payload)
        return self.response


class ObserveDnsClientTests(unittest.TestCase):
    MACS = ["02:00:00:00:00:01", "02:00:00:00:00:02"]

    def test_sends_the_normalized_sorted_list_and_accepts_the_echo(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": [], "excluded_macs": self.MACS})

        observations = client.observe_dns_queries(["02-00-00-00-00-02", self.MACS[0]])

        self.assertEqual(observations, [])
        self.assertEqual(
            client.sent, [{"action": "observe-dns", "excluded_macs": self.MACS}]
        )

    def test_an_old_helper_that_does_not_echo_the_list_fails_closed(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": [{"ipv4_address": "1.2.3.4"}]})

        with self.assertRaisesRegex(HelperClientError, "out of date"):
            client.observe_dns_queries(self.MACS)

    def test_a_helper_that_echoes_a_different_list_fails_closed(self) -> None:
        client = RecordingHelperClient(
            {"ok": True, "observations": [], "excluded_macs": self.MACS[:1]}
        )

        with self.assertRaisesRegex(HelperClientError, "out of date"):
            client.observe_dns_queries(self.MACS)

    def test_no_exclusions_needs_no_echo(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": []})

        self.assertEqual(client.observe_dns_queries(), [])
        self.assertEqual(client.sent, [{"action": "observe-dns", "excluded_macs": []}])

    def test_rejects_a_bad_or_oversized_list_before_contacting_the_helper(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": [], "excluded_macs": []})

        with self.assertRaises(HelperClientError):
            client.observe_dns_queries(["not-a-mac"])
        with self.assertRaises(HelperClientError):
            client.observe_dns_queries(
                ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(65)]
            )
        self.assertEqual(client.sent, [])


class UnixHelperServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp_dir.name) / "helper.sock"
        self.executor = WifiExecutor()
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
            counters = HelperClient(socket_path=traffic_socket).traffic_counters()
        finally:
            traffic_server.shutdown()
            traffic_server.server_close()
            traffic_thread.join(timeout=2)

        self.assertEqual(counters["devices"], {"alice": 1_200})
        self.assertEqual(
            counters["directions"]["devices"]["alice"],
            {
                "upload_bytes": 1_200,
                "download_bytes": 0,
                "upload_packets": 3,
                "download_packets": 0,
            },
        )

    def test_client_excludes_protected_devices_over_the_socket(self) -> None:
        protected = ["02:00:00:00:00:01", "02:00:00:00:00:02"]
        client = HelperClient(socket_path=self.socket_path)

        self.assertEqual(client.observe_dns_queries(protected), [])

        arguments = [
            command for command in self.executor.commands if command.executable == "tcpdump"
        ][-1].arguments
        self.assertEqual(arguments.count("ether"), 2)

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
