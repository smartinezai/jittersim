import io
import json
import socketserver
import logging
import tempfile
import threading
import re
import time
import types
import unittest
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import MAX_SHAPED_MARKS_PER_PLAN, compile_plan
from jittersim.engine.plan import (
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
)
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.browser_proxy import _read_active_plan
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, FakeExecutor
from tests.helper_support import OldHelperService, OlderHelperService, captures, serving
from tests.wifi_support import WifiExecutor
from jittersim.networking.helper import (
    HELPER_OUT_OF_DATE_MESSAGE,
    WIFI_DETAILS_MISSING_MESSAGE,
    HelperClient,
    HelperClientError,
    HelperError,
    HelperRequestHandler,
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
from jittersim.networking.router import (
    ROUTER_CHECK_MESSAGES,
    ROUTER_IMPORT_MESSAGES,
    ROUTER_WIFI_MESSAGES,
    RouterError,
    RouterWifiSettings,
)
from jittersim.networking.renderer import queue_handle, render_command_plan
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
        if command.arguments == ("-j", "list", "table", "netdev", "jittersim_download"):
            return CommandResult(return_code=0, stdout='{"nftables":[]}')
        if command.executable == "nft":
            return CommandResult(return_code=0, stdout=NFT_COUNTERS_WITH_MARKS)
        interface = command.argv[-1]
        output = TC_UPLOAD_STATISTICS if interface == "eth0" else TC_DOWNLOAD_STATISTICS
        return CommandResult(return_code=0, stdout=output)


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


class GatewayProbeApplier(NetworkApplier):
    def __init__(self, *, executor, topology) -> None:
        super().__init__(executor=executor, topology=topology)
        self.probe_results = []
        self.probe_calls = 0
        self.reset_calls = 0
        self.apply_calls = 0
        self.reset_failures = 0

    def probe_default_gateway(self):
        self.probe_calls += 1
        return self.probe_results.pop(0)

    def reset(self):
        self.reset_calls += 1
        if self.reset_failures:
            self.reset_failures -= 1
            raise CommandExecutionError("simulated gateway reset failure")
        return super().reset()

    def apply(self, *, plan, service_addresses=None):
        self.apply_calls += 1
        return super().apply(plan=plan, service_addresses=service_addresses)


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
            for action in ("router-check", "router-wifi", "router-sync-ap", "router-preview-ap",
                           "router-status", "router-wifi-credentials"):
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
        # Both project tables go. Without project root queues no tc command runs.
        self.assertEqual(response["commands_run"], 2)

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
            self.assertEqual(_read_active_plan(plan_path)[0], make_plan())
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
        self.assertEqual(executor.settings["802-11-wireless-security.group"], "ccmp")
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

    def test_router_wifi_changes_are_rejected_before_router_access(self) -> None:
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

        self.assertEqual(response["reason"], "router-control-disabled")
        self.assertEqual(routers, [])

    def test_router_wifi_rejection_does_not_expose_request_values(self) -> None:
        routers = []

        def router_factory(address, password):
            router = FakeRouter(address, password)
            routers.append(router)
            return router

        service = HelperService(
            NetworkApplier(
                executor=UplinkOutputExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            router_factory=router_factory,
        )
        response = service.handle_payload(
            {"action": "router-wifi", "admin_password": "router-secret", "enabled": False}
        )
        self.assertEqual(response["reason"], "router-control-disabled")
        self.assertNotIn("router-secret", str(response))
        self.assertEqual(routers, [])

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

    def test_wifi_credentials_return_the_name_and_password_when_asked(self) -> None:
        service, routers = self._router_check_service()

        response = service.handle_payload(
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": True}
        )

        self.assertEqual(
            response,
            {"ok": True, "action": "router-wifi-credentials", "ssid": "Vodafone-Test",
             "password": "router-wifi-password"},
        )
        self.assertEqual(routers[0].address, "192.168.0.1")
        self.assertEqual(routers[0].password, "router-secret")
        self.assertTrue(routers[0].logged_in)
        self.assertTrue(routers[0].closed)

    def test_wifi_credentials_without_the_password_never_read_it(self) -> None:
        class NoPasswordSettings:
            ssid = "Vodafone-Test"
            enabled = True
            security = "WPA2"
            band = "2.4 GHz"

            @property
            def password(self):
                raise AssertionError("the password must not be read for a name-only request")

        class NameOnlyRouter(FakeRouter):
            def read_wifi_settings(self):
                return NoPasswordSettings()

            def set_wifi_enabled(self, *, enabled):
                raise AssertionError("copying the name must not change Wi-Fi")

        for payload in (
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": False},
            {"action": "router-wifi-credentials", "admin_password": "router-secret"},
        ):
            with self.subTest(payload=payload):
                service, routers = self._router_check_service(NameOnlyRouter)
                response = service.handle_payload(payload)
                self.assertEqual(
                    response,
                    {"ok": True, "action": "router-wifi-credentials", "ssid": "Vodafone-Test"},
                )
                self.assertNotIn("password", response)
                self.assertNotIn("router-wifi-password", json.dumps(response))
                self.assertTrue(routers[0].closed)

    def test_wifi_credentials_without_the_password_leave_it_out_of_the_response(self) -> None:
        service, routers = self._router_check_service()

        response = service.handle_payload(
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": False}
        )

        self.assertEqual(response["ok"], True)
        self.assertEqual(response["ssid"], "Vodafone-Test")
        self.assertNotIn("password", response)
        self.assertNotIn("router-wifi-password", json.dumps(response))
        self.assertNotIn("router-secret", json.dumps(response))

    def test_wifi_credentials_need_a_password_and_a_real_boolean(self) -> None:
        service, routers = self._router_check_service()

        for payload in (
            {"action": "router-wifi-credentials"},
            {"action": "router-wifi-credentials", "admin_password": ""},
            {"action": "router-wifi-credentials", "admin_password": 7},
            {"action": "router-wifi-credentials", "admin_password": None},
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": "yes"},
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": 1},
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": "true"},
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": "false"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    service.handle_payload(payload),
                    {"ok": False, "error": "invalid router credentials"},
                )
        self.assertEqual(routers, [])

    def test_wifi_credentials_return_safe_guidance_and_never_echo_the_password(self) -> None:
        for reason, expected in (
            ("login", ROUTER_CHECK_MESSAGES["login"]),
            ("unreachable", ROUTER_CHECK_MESSAGES["unreachable"]),
            ("credentials", ROUTER_CHECK_MESSAGES["failed"]),
            ("uncertain", ROUTER_CHECK_MESSAGES["failed"]),
        ):
            for step in ("login", "read_wifi_settings"):
                with self.subTest(reason=reason, step=step):

                    class RejectedRouter(FakeRouter):
                        def login(self, **kwargs):
                            if step == "login":
                                raise RouterError("router-secret router-wifi-password", reason=reason)

                        def read_wifi_settings(self):
                            if step == "read_wifi_settings":
                                raise RouterError("router-secret router-wifi-password", reason=reason)
                            return super().read_wifi_settings()

                    service, routers = self._router_check_service(RejectedRouter)
                    with self.assertLogs("jittersim.networking.helper", level="WARNING") as logs:
                        response = service.handle_payload(
                            {"action": "router-wifi-credentials", "admin_password": "router-secret",
                             "include_password": True}
                        )
                    self.assertEqual(response["ok"], False)
                    self.assertEqual(response["error"], expected)
                    self.assertTrue(routers[0].closed)
                    recorded = json.dumps(response) + "\n".join(logs.output)
                    for secret in ("router-secret", "router-wifi-password"):
                        self.assertNotIn(secret, recorded)

    def test_wifi_credentials_handle_an_unreachable_router_without_leaking(self) -> None:
        class BrokenRouter(FakeRouter):
            def login(self, **kwargs):
                raise CommandExecutionError("ip route failed for router-secret")

        service, routers = self._router_check_service(BrokenRouter)
        response = service.handle_payload(
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": True}
        )

        self.assertEqual(response, {"ok": False, "error": ROUTER_CHECK_MESSAGES["unreachable"]})
        self.assertTrue(routers[0].closed)

    def test_wifi_credentials_never_log_the_password_on_success(self) -> None:
        service, routers = self._router_check_service()
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture(level=logging.DEBUG)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            response = service.handle_payload(
                {"action": "router-wifi-credentials", "admin_password": "router-secret",
                 "include_password": True}
            )
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)

        self.assertEqual(response["password"], "router-wifi-password")
        for message in records:
            self.assertNotIn("router-wifi-password", message)
            self.assertNotIn("router-secret", message)

    def test_wifi_credentials_ask_the_router_for_a_fifteen_second_limit(self) -> None:
        class TimedRouter(FakeRouter):
            def login(self, **kwargs):
                self.login_kwargs = kwargs
                super().login(**kwargs)

        service, routers = self._router_check_service(TimedRouter)
        service.handle_payload(
            {"action": "router-wifi-credentials", "admin_password": "router-secret",
             "include_password": False}
        )

        self.assertEqual(routers[0].limit, 15.0)
        self.assertEqual(routers[0].login_kwargs, {"preserve_deadline": True})

    def test_wifi_credentials_are_refused_while_another_router_operation_runs(self) -> None:
        service, routers = self._router_check_service()
        service._router_lock.acquire()
        try:
            response = service.handle_payload(
                {"action": "router-wifi-credentials", "admin_password": "router-secret",
                 "include_password": True}
            )
        finally:
            service._router_lock.release()

        self.assertEqual(response["reason"], "helper-busy")
        self.assertNotIn("router-wifi-password", json.dumps(response))
        self.assertEqual(routers, [])

    def test_rejects_short_access_point_passwords(self) -> None:
        response = self.service.handle_payload(
            {"action": "configure-ap", "ssid": "JitterSim-Test", "password": "short"}
        )

        self.assertEqual(response, {"ok": False, "error": "invalid access point settings"})
        self.assertEqual(self.executor.commands, [])

    def test_reading_router_state_never_changes_wifi_or_returns_credentials(self) -> None:
        from unittest.mock import patch

        router = FakeRouter("192.168.0.1", "router-secret")
        service = HelperService(
            NetworkApplier(executor=UplinkOutputExecutor(),
                           topology=Topology(uplink_interface="eth0", test_interface="wlan0")),
            router_factory=lambda address, password: router,
        )
        with patch.object(router, "set_wifi_enabled", side_effect=AssertionError("unexpected write")):
            response = service.handle_payload({"action": "router-status", "admin_password": "router-secret"})
        self.assertEqual(response, {"ok": True, "action": "router-status", "enabled": True})
        self.assertTrue(router.closed)

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

    def test_the_helper_advertises_protected_dns_capture(self) -> None:
        service = HelperService(
            NetworkApplier(
                executor=FakeExecutor(),
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )

        capabilities = service.handle_payload({"action": "capabilities"})

        self.assertIs(capabilities["protected_dns_capture"], True)
        self.assertIs(capabilities["general_profile"], True)
        self.assertIs(capabilities["service_lists"], True)

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

    def test_dns_observation_accepts_and_echoes_the_largest_protected_list(self) -> None:
        executor = OutputExecutor("")
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        macs = ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(256)]

        response = service.handle_payload(
            {"action": "observe-dns", "excluded_macs": list(reversed(macs))}
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["excluded_macs"], macs)
        self.assertEqual(executor.commands[0].arguments.count("ether"), 256)

    def test_dns_observation_rejects_invalid_or_too_many_excluded_addresses(self) -> None:
        executor = OutputExecutor("")
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        too_many = ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(257)]
        for bad in (["not-a-mac"], too_many, "02:00:00:00:00:01", [5], ["00:00:00:00:00:00"]):
            with self.subTest(bad=bad if len(bad) < 5 else "257 addresses"):
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
                ("nft", "-j", "list", "table", "netdev", "jittersim_download"),
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

    def test_counts_what_everyone_else_sends_and_what_its_queues_hold(self) -> None:
        nft = (
            '{"nftables":['
            '{"rule":{"comment":"usage-device alice upload","expr":['
            '{"mangle":{"key":{"meta":{"key":"mark"}},"value":1}},'
            '{"counter":{"packets":1,"bytes":100}}]}},'
            '{"rule":{"comment":"everyone else upload","expr":['
            '{"mangle":{"key":{"meta":{"key":"mark"}},"value":2}},'
            '{"counter":{"packets":7,"bytes":7000}}]}},'
            '{"rule":{"comment":"everyone else download","expr":['
            '{"mangle":{"key":{"meta":{"key":"mark"}},"value":2}},'
            '{"counter":{"packets":9,"bytes":90000}}]}}]}'
        )
        upload = (
            '[{"kind":"netem","handle":"41:","parent":"10:2","drops":3,"qlen":0},'
            '{"kind":"netem","handle":"42:","parent":"10:3","drops":11,"qlen":4}]'
        )

        class Executor(FakeExecutor):
            def run(self, command):
                self.commands.append(command)
                if command.arguments == ("-j", "list", "table", "netdev", "jittersim_download"):
                    return CommandResult(return_code=0, stdout='{"nftables":[]}')
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

        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        self.assertEqual(
            counters["everyone_else"],
            {
                "upload_bytes": 7_000,
                "download_bytes": 90_000,
                "upload_packets": 7,
                "download_packets": 9,
            },
        )
        self.assertEqual(
            counters["queues"]["everyone_else"],
            {
                "upload_dropped": 11,
                "download_dropped": 0,
                "upload_queued": 4,
                "download_queued": 0,
            },
        )
        # Everyone else isn't a device.
        self.assertEqual(set(counters["devices"]), {"alice"})
        self.assertEqual(set(counters["queues"]["devices"]), {"alice"})
        self.assertEqual(counters["queues"]["devices"]["alice"]["upload_dropped"], 3)

    def test_client_checks_everyone_else_counters(self) -> None:
        # The socket is never opened: the request itself is replaced.
        client = HelperClient(socket_path=Path("unused.sock"))
        base = {
            "devices": {},
            "services": {},
            "directions": {"devices": {}, "services": {}},
            "queues": {"devices": {}},
        }
        flows = {"upload_bytes": 1, "download_bytes": 2, "upload_packets": 3, "download_packets": 4}
        queue = {
            "upload_dropped": 5, "download_dropped": 0, "upload_queued": 0, "download_queued": 1
        }

        def answer(counters):
            client._request = lambda payload: {"ok": True, "counters": counters}
            return client.traffic_counters()

        # A helper without everyone else reports none.
        self.assertIsNone(answer(base)["everyone_else"])
        counters = answer(
            {**base, "everyone_else": flows, "queues": {"devices": {}, "everyone_else": queue}}
        )
        self.assertEqual(counters["everyone_else"], flows)
        self.assertEqual(counters["queues"]["everyone_else"], queue)
        with self.assertRaises(HelperClientError):
            answer({**base, "everyone_else": {**flows, "upload_bytes": -1}})

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


class RecordingHelperClient(HelperClient):
    """A client that answers from canned responses instead of a socket."""

    def __init__(self, response, capabilities=None):
        super().__init__(socket_path=Path("unused.sock"))
        self.response = response
        self.capabilities = (
            {"ok": True, "protected_dns_capture": True} if capabilities is None else capabilities
        )
        self.sent = []

    def _request(self, payload, *, timeout_seconds=None):
        self.sent.append(payload)
        if payload["action"] == "capabilities":
            if isinstance(self.capabilities, Exception):
                raise self.capabilities
            return self.capabilities
        return self.response


class ObserveDnsClientTests(unittest.TestCase):
    MACS = ["02:00:00:00:00:01", "02:00:00:00:00:02"]

    def test_sends_the_normalized_sorted_list_and_accepts_the_echo(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": [], "excluded_macs": self.MACS})

        observations = client.observe_dns_queries(["02-00-00-00-00-02", self.MACS[0]])

        self.assertEqual(observations, [])
        self.assertEqual(
            client.sent,
            [{"action": "capabilities"}, {"action": "observe-dns", "excluded_macs": self.MACS}],
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

    def test_checks_the_capability_before_asking_for_a_capture(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": [], "excluded_macs": self.MACS})

        client.observe_dns_queries(self.MACS)

        self.assertEqual([item["action"] for item in client.sent], ["capabilities", "observe-dns"])

    def test_a_helper_without_the_capability_is_never_asked_to_capture(self) -> None:
        for capabilities in (
            {"ok": True, "general_profile": True, "service_lists": True},
            {"ok": True, "protected_dns_capture": False},
            {"ok": True, "protected_dns_capture": "yes"},
            HelperClientError("unknown action"),
        ):
            with self.subTest(capabilities=capabilities):
                client = RecordingHelperClient(
                    {"ok": True, "observations": [], "excluded_macs": self.MACS},
                    capabilities=capabilities,
                )

                with self.assertRaises(HelperClientError) as caught:
                    client.observe_dns_queries(self.MACS)

                self.assertEqual(str(caught.exception), HELPER_OUT_OF_DATE_MESSAGE)
                self.assertEqual([item["action"] for item in client.sent], ["capabilities"])

    def test_a_helper_that_cannot_be_reached_is_not_reported_as_out_of_date(self) -> None:
        for message in (
            "could not contact the networking helper",
            "helper returned an invalid response",
            "helper returned invalid JSON",
        ):
            with self.subTest(message=message):
                client = RecordingHelperClient(
                    {"ok": True, "observations": [], "excluded_macs": self.MACS},
                    capabilities=HelperClientError(message),
                )

                with self.assertRaises(HelperClientError) as caught:
                    client.observe_dns_queries(self.MACS)

                self.assertEqual(str(caught.exception), message)
                self.assertEqual([item["action"] for item in client.sent], ["capabilities"])

    def test_a_busy_helper_is_not_reported_as_out_of_date(self) -> None:
        client = RecordingHelperClient(
            {"ok": True, "observations": []},
            capabilities=HelperClientError("busy", reason="helper-busy"),
        )

        with self.assertRaises(HelperClientError) as caught:
            client.observe_dns_queries(self.MACS)

        self.assertEqual(caught.exception.reason, "helper-busy")
        self.assertNotEqual(str(caught.exception), HELPER_OUT_OF_DATE_MESSAGE)
        self.assertEqual([item["action"] for item in client.sent], ["capabilities"])

    def test_the_out_of_date_message_says_how_to_fix_it(self) -> None:
        self.assertEqual(
            HELPER_OUT_OF_DATE_MESSAGE,
            "The networking helper is out of date and cannot skip protected devices. "
            "Restart the JitterSim services or reinstall the release.",
        )

    def test_the_echo_check_still_applies_when_the_capability_is_advertised(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": []})

        with self.assertRaisesRegex(HelperClientError, "out of date"):
            client.observe_dns_queries(self.MACS)

    def test_rejects_a_bad_or_oversized_list_before_contacting_the_helper(self) -> None:
        client = RecordingHelperClient({"ok": True, "observations": [], "excluded_macs": []})

        with self.assertRaises(HelperClientError):
            client.observe_dns_queries(["not-a-mac"])
        with self.assertRaises(HelperClientError):
            client.observe_dns_queries(
                ["02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(257)]
            )
        self.assertEqual(client.sent, [])


class RouterWifiCredentialsClientTests(unittest.TestCase):
    GOOD = {"ok": True, "action": "router-wifi-credentials", "ssid": "Vodafone-Test",
            "password": "router-wifi-password"}

    def test_asks_for_the_name_and_password_and_returns_both(self) -> None:
        client = RecordingHelperClient(dict(self.GOOD))

        result = client.read_router_wifi_credentials(
            admin_password="router-secret", include_password=True)

        self.assertEqual(result, {"ssid": "Vodafone-Test", "password": "router-wifi-password"})
        self.assertEqual(
            client.sent,
            [{"action": "router-wifi-credentials", "admin_password": "router-secret",
              "include_password": True}],
        )

    def test_returns_only_the_name_when_the_password_was_not_asked_for(self) -> None:
        client = RecordingHelperClient(dict(self.GOOD))

        result = client.read_router_wifi_credentials(
            admin_password="router-secret", include_password=False)

        self.assertEqual(result, {"ssid": "Vodafone-Test"})
        self.assertFalse(client.sent[0]["include_password"])

    def test_gives_the_router_no_more_than_twenty_five_seconds(self) -> None:
        timeouts = []

        class TimedClient(RecordingHelperClient):
            def _request(self, payload, *, timeout_seconds=None):
                timeouts.append(timeout_seconds)
                return super()._request(payload, timeout_seconds=timeout_seconds)

        TimedClient(dict(self.GOOD)).read_router_wifi_credentials(
            admin_password="router-secret", include_password=True)

        self.assertEqual(timeouts, [25.0])

    def test_rejects_a_name_that_is_not_usable(self) -> None:
        for ssid in (None, 7, "", "x" * 33, "\u00e4" * 17, ["Vodafone-Test"]):
            with self.subTest(ssid=ssid):
                client = RecordingHelperClient({**self.GOOD, "ssid": ssid})
                with self.assertRaises(HelperClientError) as caught:
                    client.read_router_wifi_credentials(
                        admin_password="router-secret", include_password=False)
                self.assertEqual(str(caught.exception), WIFI_DETAILS_MISSING_MESSAGE)

    def test_rejects_a_password_that_is_not_usable_without_repeating_it(self) -> None:
        for password in (None, 12345678, "short", "x" * 64, "has\ncontrol-char", ["router-wifi-pass"]):
            with self.subTest(password=password):
                client = RecordingHelperClient({**self.GOOD, "password": password})
                with self.assertRaises(HelperClientError) as caught:
                    client.read_router_wifi_credentials(
                        admin_password="router-secret", include_password=True)
                self.assertEqual(str(caught.exception), WIFI_DETAILS_MISSING_MESSAGE)
                self.assertNotIn(str(password), str(caught.exception))
                self.assertNotIn("router-secret", str(caught.exception))

    def test_a_missing_password_is_an_error_only_when_it_was_asked_for(self) -> None:
        without_password = {key: value for key, value in self.GOOD.items() if key != "password"}
        client = RecordingHelperClient(without_password)
        with self.assertRaises(HelperClientError) as caught:
            client.read_router_wifi_credentials(admin_password="router-secret", include_password=True)
        self.assertEqual(str(caught.exception), WIFI_DETAILS_MISSING_MESSAGE)
        self.assertEqual(
            client.read_router_wifi_credentials(
                admin_password="router-secret", include_password=False),
            {"ssid": "Vodafone-Test"},
        )

    def test_an_old_helper_answers_unknown_action_through_the_socket(self) -> None:
        class HelperWithoutCredentials(HelperService):
            def _handle_payload(self, payload):
                if isinstance(payload, dict) and payload.get("action") == "router-wifi-credentials":
                    return {"ok": False, "error": "unknown action"}
                return super()._handle_payload(payload)

        with serving(HelperWithoutCredentials) as (client, _):
            with self.assertRaises(HelperClientError) as caught:
                client.read_router_wifi_credentials(
                    admin_password="router-secret", include_password=True)
        self.assertEqual(str(caught.exception), "unknown action")
class GatewayPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.active_plan_path = directory / "active-plan.json"
        self.gateway_state_path = directory / "gateway-state.json"
        self.executor = FakeExecutor()
        self.applier = GatewayProbeApplier(
            executor=self.executor,
            topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
        )
        self.service = self._new_service()
        result = self.service.handle_payload(
            {"action": "apply-experiment", "plan": plan_to_payload(make_plan())}
        )
        self.assertTrue(result["ok"])

    def _new_service(self):
        return HelperService(
            self.applier,
            active_plan_path=self.active_plan_path,
            gateway_state_path=self.gateway_state_path,
        )

    def _check_probes(self, results):
        self.applier.probe_results = list(results)
        for _ in results:
            self.service.monitor_gateway_once()

    def test_off_mode_stops_once_and_stays_stopped_until_explicit_apply(self) -> None:
        self.service.handle_payload({"action": "gateway-persistence", "enabled": False})
        self._check_probes([False, False, False, True, True])

        status = self.service.handle_payload({"action": "gateway-status"})
        self.assertEqual(status["gateway_state"], "available")
        self.assertTrue(status["outage_stop_latched"])
        self.assertFalse(status["reset_pending"])
        self.assertEqual(self.applier.reset_calls, 1)
        self.assertFalse(self.active_plan_path.exists())

        restarted = self._new_service()
        restarted_status = restarted.handle_payload({"action": "gateway-status"})
        self.assertTrue(restarted_status["outage_stop_latched"])
        commands_before = len(self.executor.commands)
        blocked = restarted.handle_payload(
            {"action": "apply-experiment", "plan": plan_to_payload(make_plan())}
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("use Apply", blocked["error"])
        self.assertEqual(len(self.executor.commands), commands_before)

        applied = restarted.handle_payload(
            {
                "action": "apply",
                "explicit_apply": True,
                "plan": plan_to_payload(make_plan()),
            }
        )
        self.assertTrue(applied["ok"])
        self.assertFalse(restarted.handle_payload({"action": "gateway-status"})[
            "outage_stop_latched"
        ])
        self.assertTrue(self.active_plan_path.exists())

    def test_on_mode_keeps_plan_and_marks_one_reapply_after_recovery(self) -> None:
        self.service.handle_payload({"action": "gateway-persistence", "enabled": True})
        self._check_probes([False, False, False, True, True])

        status = self.service.handle_payload({"action": "gateway-status"})
        self.assertEqual(status["gateway_state"], "available")
        self.assertTrue(status["recovery_pending"])
        self.assertEqual(self.applier.reset_calls, 0)
        self.assertTrue(self.active_plan_path.exists())
        self.assertEqual(self.applier.apply_calls, 1)

        result = self.service.handle_payload(
            {"action": "apply-experiment", "plan": plan_to_payload(make_plan())}
        )
        self.assertTrue(result["ok"])
        self.assertFalse(self.service.handle_payload({"action": "gateway-status"})[
            "recovery_pending"
        ])
        self.assertEqual(self.applier.apply_calls, 2)

    def test_unknown_gateway_probe_does_not_stop_profiles_or_count_as_failure(self) -> None:
        self.service.handle_payload({"action": "gateway-persistence", "enabled": False})
        self._check_probes([False, False, None, False])

        status = self.service.handle_payload({"action": "gateway-status"})
        self.assertEqual(status["gateway_state"], "unknown")
        self.assertFalse(status["outage_stop_latched"])
        self.assertEqual(self.applier.reset_calls, 0)
        self.assertTrue(self.active_plan_path.exists())

    def test_route_lost_after_a_reachable_gateway_counts_as_an_outage(self) -> None:
        # A router reboot drops the Ethernet link, and the default route with it.
        self.service.handle_payload({"action": "gateway-persistence", "enabled": False})
        self._check_probes([True, True, False, None, None])

        status = self.service.handle_payload({"action": "gateway-status"})
        self.assertEqual(status["gateway_state"], "unavailable")
        self.assertTrue(status["outage_stop_latched"])
        self.assertEqual(self.applier.reset_calls, 1)
        self.assertFalse(self.active_plan_path.exists())

    def test_status_reports_whether_the_mode_was_received(self) -> None:
        fresh = HelperService(self.applier, active_plan_path=self.active_plan_path)
        self.assertFalse(fresh.handle_payload({"action": "gateway-status"})["mode_initialized"])
        fresh.handle_payload({"action": "gateway-persistence", "enabled": True})
        self.assertTrue(fresh.handle_payload({"action": "gateway-status"})["mode_initialized"])

    def test_failed_outage_reset_stays_latched_and_retries_after_backoff(self) -> None:
        self.service.handle_payload({"action": "gateway-persistence", "enabled": False})
        self.applier.reset_failures = 1
        self.applier.probe_results = [False, False, False, False, False]

        with patch("jittersim.networking.helper.time.monotonic", return_value=100):
            for _ in range(3):
                self.service.monitor_gateway_once()
            status = self.service.handle_payload({"action": "gateway-status"})
            self.assertTrue(status["outage_stop_latched"])
            self.assertTrue(status["reset_pending"])
            self.assertTrue(self.active_plan_path.exists())
            self.service.monitor_gateway_once()
            self.assertEqual(self.applier.reset_calls, 1)

        with patch("jittersim.networking.helper.time.monotonic", return_value=130):
            self.service.monitor_gateway_once()
        self.assertFalse(self.service.handle_payload({"action": "gateway-status"})[
            "reset_pending"
        ])
        self.assertFalse(self.active_plan_path.exists())
        self.assertEqual(self.applier.reset_calls, 2)


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


class OldHelperTests(unittest.TestCase):
    MAC = "02:00:00:00:00:01"

    def test_an_old_helper_never_captures_when_devices_are_protected(self) -> None:
        for service_class in (OldHelperService, OlderHelperService):
            with self.subTest(service=service_class.__name__):
                with serving(service_class) as (client, executor):
                    with self.assertRaises(HelperClientError) as caught:
                        client.observe_dns_queries([self.MAC])

                    self.assertEqual(str(caught.exception), HELPER_OUT_OF_DATE_MESSAGE)
                    self.assertEqual(captures(executor), [])

    def test_a_missing_helper_socket_gives_the_real_cause_and_no_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = HelperClient(socket_path=Path(directory) / "missing.sock")

            with self.assertRaises(HelperClientError) as caught:
                client.observe_dns_queries([self.MAC])

        self.assertEqual(str(caught.exception), "could not contact the networking helper")

    def test_an_old_helper_still_serves_a_scan_when_nothing_is_protected(self) -> None:
        with serving(OldHelperService) as (client, executor):
            self.assertEqual(client.observe_dns_queries(), [])

            self.assertEqual(len(captures(executor)), 1)

    def test_a_current_helper_captures_with_the_exclusions(self) -> None:
        with serving(HelperService) as (client, executor):
            self.assertEqual(client.observe_dns_queries([self.MAC]), [])

            (capture,) = captures(executor)
            self.assertIn(self.MAC, capture.arguments)


def lossy_plan():
    profile = Profile(
        id="lossy",
        name="Lossy",
        upload=Impairment(loss_percent=20),
        download=Impairment(loss_percent=20),
    )
    return compile_plan(
        devices=(Device("lab", "Lab", "192.168.50.10", "lossy"),),
        profiles={"lossy": profile},
    )


def qdisc(handle, parent, **counters):
    entry = {
        "kind": "netem",
        "handle": handle,
        "parent": parent,
        "bytes": 0,
        "packets": 0,
        "drops": 0,
        "overlimits": 0,
        "requeues": 0,
        "backlog": 0,
        "qlen": 0,
    }
    entry.update(counters)
    return entry


class QueueExecutor(FakeExecutor):
    """Answer the queue counter command with canned stats per interface."""

    def __init__(self, stats, fail=False):
        super().__init__()
        self.stats = stats
        self.fail = fail
        # A queue plan command that fails after the commands before it ran.
        self.fail_apply = False
        # A test can hold the commands that match `gate` until it sets `release`.
        self.gate = None
        self.gated = threading.Event()
        self.release = threading.Event()
        self.read_seconds = 0.0

    def run(self, command):
        if self.gate is not None and self.gate(command):
            self.gated.set()
            self.release.wait(10)
        if (
            self.fail_apply
            and command.executable == "tc"
            and command.arguments[:2] == ("qdisc", "replace")
            and not command.ignore_failure
        ):
            raise CommandExecutionError("simulated tc failure while applying")
        if command.executable == "tc" and command.arguments[:3] == ("-s", "-j", "qdisc"):
            self.commands.append(command)
            time.sleep(self.read_seconds)
            if self.fail:
                raise CommandExecutionError("simulated tc failure")
            return CommandResult(return_code=0, stdout=json.dumps(self.stats[command.arguments[-1]]))
        return super().run(command)


def is_queue_read(command):
    return command.executable == "tc" and command.arguments[:3] == ("-s", "-j", "qdisc")


def is_queue_install(command):
    return command.executable == "tc" and command.arguments[:2] == ("qdisc", "replace")


ROOT_QDISC = {"kind": "prio", "handle": "10:", "root": True, "bytes": 9, "packets": 1,
              "drops": 0, "overlimits": 0, "requeues": 0, "backlog": 0, "qlen": 0}


class QueueStatsHelperTests(unittest.TestCase):
    def helper(self, stats, *, plan=lossy_plan(), fail=False, plan_path=None):
        executor = QueueExecutor(stats, fail=fail)
        service = HelperService(
            NetworkApplier(
                executor=executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            ),
            active_plan_path=plan_path,
        )
        if plan is not None:
            self.assertTrue(
                service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})["ok"]
            )
        executor.commands.clear()
        return service, executor

    def test_joins_each_queue_to_the_plan_the_helper_applied(self) -> None:
        service, executor = self.helper(
            {
                "eth0": [ROOT_QDISC, qdisc(queue_handle(1), "10:2", packets=10, drops=3, bytes=1500)],
                "wlan0": [
                    ROOT_QDISC,
                    qdisc(queue_handle(1), "10:2", packets=40, drops=9, bytes=6000, backlog=300, qlen=2,
                          overlimits=4, requeues=1),
                ],
            }
        )

        response = service.handle_payload({"action": "queue-stats"})

        self.assertTrue(response["ok"])
        self.assertEqual(response["action"], "queue-stats")
        upload, download = response["queues"]
        self.assertEqual(
            (upload["device_id"], upload["direction"], upload["interface"], upload["handle"]),
            ("lab", "upload", "eth0", queue_handle(1)),
        )
        self.assertEqual(
            (download["device_id"], download["direction"], download["interface"]),
            ("lab", "download", "wlan0"),
        )
        self.assertEqual(upload["kind"], "netem")
        self.assertEqual(upload["impairment"]["loss_percent"], 20)
        self.assertEqual(
            {key: upload[key] for key in ("packets", "bytes", "drops", "backlog_bytes", "qlen")},
            {"packets": 10, "bytes": 1500, "drops": 3, "backlog_bytes": 0, "qlen": 0},
        )
        self.assertEqual(
            {key: download[key] for key in (
                "packets", "bytes", "drops", "overlimits", "requeues", "backlog_bytes", "qlen")},
            {"packets": 40, "bytes": 6000, "drops": 9, "overlimits": 4, "requeues": 1,
             "backlog_bytes": 300, "qlen": 2},
        )
        self.assertEqual(
            [command.arguments for command in executor.commands],
            [
                ("-s", "-j", "qdisc", "show", "dev", "eth0"),
                ("-s", "-j", "qdisc", "show", "dev", "wlan0"),
            ],
        )

    def test_without_an_applied_plan_it_reports_no_queues_and_reads_nothing(self) -> None:
        service, executor = self.helper({}, plan=None)

        self.assertEqual(
            service.handle_payload({"action": "queue-stats"}),
            {"ok": True, "action": "queue-stats", "queues": []},
        )
        self.assertEqual(executor.commands, [])

    def test_only_queues_with_the_project_handle_and_parent_are_used(self) -> None:
        service, _ = self.helper(
            {
                "eth0": [
                    qdisc(queue_handle(2), "10:2", packets=111),
                    qdisc(queue_handle(1), "10:5", packets=222),
                    {"kind": "fq_codel", "handle": queue_handle(1), "packets": 333},
                    qdisc(queue_handle(1), "10:2", packets=7),
                ],
                "wlan0": [qdisc(queue_handle(1), "1:2", packets=444)],
            }
        )

        response = service.handle_payload({"action": "queue-stats"})

        self.assertEqual([(q["interface"], q["packets"]) for q in response["queues"]],
                         [("eth0", 7)])

    def test_a_plan_queue_without_a_kernel_queue_is_left_out(self) -> None:
        service, _ = self.helper({"eth0": [ROOT_QDISC], "wlan0": [qdisc(queue_handle(1), "10:2", packets=5)]})

        response = service.handle_payload({"action": "queue-stats"})

        self.assertEqual([(q["direction"], q["packets"]) for q in response["queues"]],
                         [("download", 5)])

    def test_a_failed_read_gives_a_plain_error(self) -> None:
        service, _ = self.helper({}, fail=True)

        with self.assertLogs("jittersim.networking.helper", level="WARNING"):
            response = service.handle_payload({"action": "queue-stats"})

        self.assertEqual(response, {"ok": False, "error": "queue statistics unavailable"})

    def test_a_restarted_helper_reads_and_adopts_the_published_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "active-plan.json"
            stats = {"eth0": [qdisc(queue_handle(1), "10:2", packets=1)], "wlan0": []}
            self.helper(stats, plan_path=plan_path)
            restarted, _ = self.helper(stats, plan=None, plan_path=plan_path)

            response = restarted.handle_payload({"action": "queue-stats"})

            self.assertEqual([q["device_id"] for q in response["queues"]], ["lab"])
            self.assertIsNotNone(restarted._active_plan)

    def test_a_failed_read_is_logged_once_for_each_reason(self) -> None:
        service, executor = self.helper({"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": []}, fail=True)

        with self.assertLogs("jittersim.networking.helper", level="WARNING") as logged:
            for _ in range(3):
                self.assertFalse(service.handle_payload({"action": "queue-stats"})["ok"])
            executor.fail = False
            self.assertTrue(service.handle_payload({"action": "queue-stats"})["ok"])
            executor.fail = True
            for _ in range(2):
                self.assertFalse(service.handle_payload({"action": "queue-stats"})["ok"])

        self.assertEqual(len(logged.records), 2)
        self.assertTrue(all(record.exc_info for record in logged.records))

    def test_a_queue_read_holds_no_lock_of_its_own(self) -> None:
        """Only the socket handler's lock keeps it apart from an apply and a Wi-Fi action never
        changes queues so it must not be refused because of a read."""

        service, executor = self.helper({"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": []})
        executor.gate = is_queue_read
        reader = threading.Thread(
            target=lambda: service.handle_payload({"action": "queue-stats"})
        )
        reader.start()
        self.addCleanup(reader.join, 5)
        self.addCleanup(executor.release.set)
        self.assertTrue(executor.gated.wait(5))

        self.assertTrue(service._network_lock.acquire(blocking=False))
        service._network_lock.release()
        refused = service.handle_payload({"action": "wifi-reset"})
        self.assertNotEqual(refused.get("reason"), "helper-busy")

    def test_every_entry_carries_the_generation_of_the_applied_plan(self) -> None:
        stats = {"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": [qdisc(queue_handle(1), "10:2")]}
        with mock.patch("jittersim.networking.helper.secrets.token_hex", return_value="0a1b2c3d"):
            service, _ = self.helper(stats)
            first = service.handle_payload({"action": "queue-stats"})["queues"]
            self.assertTrue(
                service.handle_payload({"action": "apply", "plan": plan_to_payload(lossy_plan())})["ok"]
            )
            second = service.handle_payload({"action": "queue-stats"})["queues"]
            self.assertEqual(
                service.handle_payload({"action": "queue-stats"})["queues"][0]["generation"],
                second[0]["generation"],
            )

        self.assertEqual({item["generation"] for item in first}, {"0a1b2c3d-1"})
        self.assertEqual({item["generation"] for item in second}, {"0a1b2c3d-2"})

    def test_a_failed_apply_does_not_change_the_generation(self) -> None:
        stats = {"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": [qdisc(queue_handle(1), "10:2")]}
        service, _ = self.helper(stats)
        before = service.handle_payload({"action": "queue-stats"})["queues"][0]["generation"]

        refused = service.handle_payload({"action": "apply", "plan": {"mark_rules": "bad"}})

        self.assertFalse(refused["ok"])
        after = service.handle_payload({"action": "queue-stats"})["queues"][0]["generation"]
        self.assertEqual(before, after)

    def test_a_valid_apply_that_fails_halfway_does_not_change_the_generation(self) -> None:
        stats = {"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": [qdisc(queue_handle(1), "10:2")]}
        with mock.patch("jittersim.networking.helper.secrets.token_hex", return_value="0a1b2c3d"):
            service, executor = self.helper(stats)
            self.assertEqual(self.generation(service), "0a1b2c3d-1")

            executor.fail_apply = True
            failed = service.handle_payload(
                {"action": "apply", "plan": plan_to_payload(lossy_plan())}
            )
            self.assertEqual(failed["error"], "network plan apply failed")
            self.assertEqual(service.handle_payload({"action": "queue-stats"})["queues"], [])

            executor.fail_apply = False
            self.assertTrue(
                service.handle_payload({"action": "apply", "plan": plan_to_payload(lossy_plan())})["ok"]
            )
            # one successful apply since the first one and the failed one did not count
            self.assertEqual(self.generation(service), "0a1b2c3d-2")

    def test_a_reset_and_the_same_plan_again_get_a_new_generation(self) -> None:
        stats = {"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": [qdisc(queue_handle(1), "10:2")]}
        with mock.patch("jittersim.networking.helper.secrets.token_hex", return_value="0a1b2c3d"):
            service, _ = self.helper(stats)
            first = self.generation(service)
            self.assertTrue(service.handle_payload({"action": "reset"})["ok"])
            self.assertTrue(
                service.handle_payload({"action": "apply", "plan": plan_to_payload(lossy_plan())})["ok"]
            )
            second = self.generation(service)

        self.assertEqual((first, second), ("0a1b2c3d-1", "0a1b2c3d-2"))

    def generation(self, service):
        queues = service.handle_payload({"action": "queue-stats"})["queues"]
        self.assertEqual(len({queue["generation"] for queue in queues}), 1)
        return queues[0]["generation"]

    def test_a_restarted_helper_has_a_new_boot_nonce_and_counts_the_adopted_plan(self) -> None:
        stats = {"eth0": [qdisc(queue_handle(1), "10:2")], "wlan0": [qdisc(queue_handle(1), "10:2")]}
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "active-plan.json"
            with mock.patch(
                "jittersim.networking.helper.secrets.token_hex", side_effect=["aaaa0001", "bbbb0002"]
            ):
                first, _ = self.helper(stats, plan_path=plan_path)
                restarted, _ = self.helper(stats, plan=None, plan_path=plan_path)

            before = first.handle_payload({"action": "queue-stats"})["queues"][0]["generation"]
            after = restarted.handle_payload({"action": "queue-stats"})["queues"][0]["generation"]

        self.assertEqual(before, "aaaa0001-1")
        self.assertEqual(after, "bbbb0002-1")

    def assert_every_queue_joins(self, plan) -> None:
        """Feed the queues the renderer installs back as kernel stats and join every plan queue."""

        topology = Topology(uplink_interface="eth0", test_interface="wlan0")
        installed = re.compile(
            r"(?:apply \w+ impairment for|preserve fair queuing for) device (\S+) on (\S+)$"
        )
        expected = {}
        kernel = {"eth0": [ROOT_QDISC], "wlan0": [ROOT_QDISC]}
        counter = 100
        for command in render_command_plan(plan=plan, topology=topology):
            if command.executable != "tc" or command.arguments[:2] != ("qdisc", "replace"):
                continue
            if "parent" not in command.arguments:
                continue
            arguments = command.arguments
            interface = arguments[arguments.index("dev") + 1]
            parent = arguments[arguments.index("parent") + 1]
            handle = arguments[arguments.index("handle") + 1]
            kind = arguments[arguments.index("handle") + 2]
            counter += 1
            entry = qdisc(handle, parent, packets=counter, drops=counter // 10)
            entry["kind"] = kind
            kernel[interface].append(entry)
            found = installed.search(command.purpose)
            if found:
                expected[(found.group(1), interface)] = (handle, kind, counter)
        self.assertEqual(len(expected), len(plan.queues))
        service, _ = self.helper(kernel, plan=plan)

        response = service.handle_payload({"action": "queue-stats"})

        self.assertTrue(response["ok"])
        joined = {(item["device_id"], item["interface"]): item for item in response["queues"]}
        self.assertEqual(set(joined), set(expected))
        for key, (handle, kind, counter) in expected.items():
            self.assertEqual(
                (joined[key]["handle"], joined[key]["kind"], joined[key]["packets"]),
                (handle, kind, counter),
                key,
            )

    def plan_with_every_queue_kind(self, device_count):
        impairment = Impairment(loss_percent=7)
        profile = Profile("p", "P", upload=impairment, download=impairment)
        plain = Profile("plain", "Plain")
        devices = tuple(
            Device(
                f"d{index:02d}", f"D{index:02d}", f"192.168.50.{10 + index}",
                "plain" if index == 1 else "p", f"02:00:00:00:00:{index + 1:02x}",
            )
            for index in range(device_count)
        )
        return compile_plan(
            devices=devices,
            profiles={"p": profile, "plain": plain},
            service_quota_rules=(
                ServiceQuotaRule(
                    "q1", "d00", devices[0].ipv4_address, (IPv4Address("198.18.0.2"),),
                    0, impairment, impairment, True,
                ),
            ),
            service_assignment_rules=(
                ServiceAssignmentRule(
                    "a1", "d00", devices[0].ipv4_address, ("example.com",),
                    0, impairment, impairment,
                ),
            ),
            service_list_rules=(ServiceListRule("s1", ("x.example",), 0, impairment, impairment),),
        )

    def test_every_queue_kind_joins_at_marks_above_one(self) -> None:
        general = compile_plan(
            devices=(),
            profiles={"p": Profile("p", "P", upload=Impairment(loss_percent=7))},
            general_profile=GeneralProfileRule("p"),
            service_list_rules=(
                ServiceListRule("s1", ("x.example",), 0, Impairment(loss_percent=7), Impairment()),
            ),
        )
        for plan in (self.plan_with_every_queue_kind(3), general):
            with self.subTest(marks=sorted({queue.mark for queue in plan.queues})):
                self.assertGreaterEqual(max(queue.mark for queue in plan.queues), 2)
                self.assert_every_queue_joins(plan)

    def test_every_queue_joins_at_every_mark_up_to_the_limit_including_hex_bands(self) -> None:
        plan = self.plan_with_every_queue_kind(MAX_SHAPED_MARKS_PER_PLAN - 3)
        marks = sorted({queue.mark for queue in plan.queues})
        self.assertEqual(marks, list(range(1, MAX_SHAPED_MARKS_PER_PLAN + 1)))
        self.assertEqual(
            {queue.device_id for queue in plan.queues if queue.mark in (9, 15)},
            {"d08", "servicelist:s1"},
        )
        parents = {
            arguments[arguments.index("parent") + 1]
            for command in render_command_plan(
                plan=plan, topology=Topology(uplink_interface="eth0", test_interface="wlan0")
            )
            if command.executable == "tc" and command.arguments[:2] == ("qdisc", "replace")
            for arguments in (command.arguments,)
            if "parent" in arguments
        }
        self.assertIn("10:a", parents)
        self.assertIn("10:10", parents)
        self.assert_every_queue_joins(plan)

    def test_after_a_reset_it_reports_no_queues(self) -> None:
        service, _ = self.helper({"eth0": [qdisc(queue_handle(1), "10:2", packets=1)], "wlan0": []})
        self.assertTrue(service.handle_payload({"action": "reset"})["ok"])

        self.assertEqual(service.handle_payload({"action": "queue-stats"})["queues"], [])

    def test_the_client_returns_validated_entries_over_the_socket(self) -> None:
        stats = {
            "eth0": [qdisc(queue_handle(1), "10:2", packets=10, drops=3, bytes=1500)],
            "wlan0": [qdisc(queue_handle(1), "10:2", packets=40, drops=9, bytes=6000)],
        }
        service, _ = self.helper(stats)
        with tempfile.TemporaryDirectory() as directory:
            queue_socket = Path(directory) / "q.sock"
            server = UnixHelperServer(queue_socket, service)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                queues = HelperClient(socket_path=queue_socket).queue_stats()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual([(q["device_id"], q["direction"], q["drops"]) for q in queues],
                         [("lab", "upload", 3), ("lab", "download", 9)])
        self.assertEqual(queues[0]["impairment"]["loss_percent"], 20)


class FakeAccessManager:
    """Stand in for the persistent access manager when only the helper is tested."""

    def __init__(self, *, fail: bool = False) -> None:
        self.disabled = 0
        self.fail = fail
        self.dns_failed = False

    def disable(self, **_):
        self.disabled += 1
        if self.fail:
            raise CommandExecutionError("simulated access failure")

    def status(self):
        return {"available": True, "enabled": False}

    def apply(self, plan):
        self.applied = plan
        return {"enabled": plan.options.enabled}


class QueueReadAndWriteTests(unittest.TestCase):
    """A read never makes Panic or an apply fail and a write never waits for ever."""

    STATS = {"eth0": [qdisc("11:", "10:2", packets=5)], "wlan0": [qdisc("11:", "10:2", packets=5)]}

    def setUp(self) -> None:
        self.executor = QueueExecutor(self.STATS)
        self.service = HelperService(
            NetworkApplier(
                executor=self.executor,
                topology=Topology(uplink_interface="eth0", test_interface="wlan0"),
            )
        )
        self.assertTrue(
            self.service.handle_payload({"action": "apply", "plan": plan_to_payload(lossy_plan())})["ok"]
        )
        self.executor.commands.clear()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.server = UnixHelperServer(Path(directory.name) / "helper.sock", self.service)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.executor.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def client(self) -> HelperClient:
        return HelperClient(socket_path=self.server.socket_path, timeout_seconds=10)

    def in_thread(self, call):
        """Run a client call in a thread and keep its answer or its error in a dictionary."""

        box = {}

        def work() -> None:
            started = time.monotonic()
            try:
                box["result"] = call()
            except HelperClientError as error:
                box["error"] = error
            box["seconds"] = time.monotonic() - started

        thread = threading.Thread(target=work)
        thread.start()
        self.addCleanup(thread.join, 10)
        return box, thread

    def hold_a_read(self):
        self.executor.gate = is_queue_read
        box, thread = self.in_thread(self.client().queue_stats)
        self.assertTrue(self.executor.gated.wait(5))
        return box, thread

    def assert_completed_reply_does_not_block(self, *, failure, before_write) -> None:
        self.service._access_manager = FakeAccessManager()
        transport_entered = threading.Event()
        first_response = threading.Event()
        release_transport = threading.Event()
        original_write = socketserver._SocketWriter.write

        def held_write(writer, response):
            if first_response.is_set():
                return original_write(writer, response)
            first_response.set()
            if before_write:
                transport_entered.set()
                if not release_transport.wait(5):
                    raise TimeoutError("test did not release response transport")
            result = original_write(writer, response)
            if not before_write:
                transport_entered.set()
                if not release_transport.wait(5):
                    raise TimeoutError("test did not release response transport")
            return result

        payload = {"action": "apply", "plan": None} if failure else {"action": "reset"}
        with mock.patch.object(socketserver._SocketWriter, "write", held_write):
            first, first_thread = self.in_thread(lambda: self.client()._request(payload))
            try:
                self.assertTrue(transport_entered.wait(5))
                with self.subTest(operation="read"):
                    self.assertTrue(self.client().gateway_status()["ok"])
                with self.subTest(operation="Panic"):
                    with mock.patch("jittersim.networking.helper.WRITE_LOCK_WAIT_SECONDS", 0.1):
                        self.assertTrue(self.client().normal_access()["access_disabled"])
            finally:
                release_transport.set()
                first_thread.join(5)
            self.assertFalse(first_thread.is_alive())
            if failure:
                self.assertIn("error", first)
                self.assertNotEqual(first["error"].reason, "helper-busy")
            else:
                self.assertEqual(first["result"]["action"], "reset")

    def test_reads_and_panic_continue_before_a_success_reply_is_sent(self) -> None:
        self.assert_completed_reply_does_not_block(failure=False, before_write=True)

    def test_reads_and_panic_continue_after_a_success_reply_is_sent(self) -> None:
        self.assert_completed_reply_does_not_block(failure=False, before_write=False)

    def test_reads_and_panic_continue_before_an_error_reply_is_sent(self) -> None:
        self.assert_completed_reply_does_not_block(failure=True, before_write=True)

    def test_reads_and_panic_continue_after_an_error_reply_is_sent(self) -> None:
        self.assert_completed_reply_does_not_block(failure=True, before_write=False)

    def test_operation_lock_is_released_when_a_request_raises(self) -> None:
        for phase in ("service", "serialization", "transport"):
            with self.subTest(phase=phase):
                handler = object.__new__(HelperRequestHandler)
                handler.server = self.server
                handler.rfile = io.BytesIO(b'{"action":"gateway-status"}\n')
                handler.wfile = mock.Mock()
                if phase == "service":
                    answer = mock.Mock(side_effect=RuntimeError("service failed"))
                    expected_error = RuntimeError
                elif phase == "serialization":
                    answer = mock.Mock(return_value={"ok": True, "invalid": object()})
                    expected_error = TypeError
                else:
                    answer = mock.Mock(return_value={"ok": True})
                    handler.wfile.write.side_effect = OSError("client disconnected")
                    expected_error = OSError
                with mock.patch.object(self.service, "handle_payload", answer):
                    with self.assertRaises(expected_error):
                        handler._handle_request()
                self.assertFalse(self.server.operation_lock.locked())
                self.assertTrue(self.client().gateway_status()["ok"])

    def test_a_reset_that_arrives_during_a_slow_queue_read_waits_and_runs(self) -> None:
        reader, reader_thread = self.hold_a_read()
        writer, writer_thread = self.in_thread(self.client().reset)

        time.sleep(0.3)
        self.assertEqual(writer, {})
        self.executor.release.set()
        writer_thread.join(5)
        reader_thread.join(5)

        self.assertEqual(writer["result"]["action"], "reset")
        self.assertGreater(writer["seconds"], 0.25)
        self.assertIn("result", reader)
        self.assertIsNone(self.service._active_plan)

    def test_an_apply_that_arrives_during_a_slow_queue_read_waits_and_runs(self) -> None:
        reader, reader_thread = self.hold_a_read()
        writer, writer_thread = self.in_thread(lambda: self.client().apply(plan=lossy_plan()))

        time.sleep(0.3)
        self.assertEqual(writer, {})
        self.executor.release.set()
        writer_thread.join(5)
        reader_thread.join(5)

        self.assertEqual(writer["result"]["action"], "apply")
        self.assertIn("result", reader)
        self.assertEqual(self.service._plan_count, 2)

    def assert_profile_apply_waits_for_queue_read(self, plan) -> None:
        reader, reader_thread = self.hold_a_read()
        writer, writer_thread = self.in_thread(lambda: self.client().apply(plan=plan))
        writer_thread.join(0.3)

        try:
            self.assertTrue(writer_thread.is_alive(), writer)
            self.assertEqual(writer, {})
            self.assertEqual(self.service._plan_count, 1)
        finally:
            self.executor.release.set()
        writer_thread.join(5)
        reader_thread.join(5)

        self.assertFalse(writer_thread.is_alive())
        self.assertFalse(reader_thread.is_alive())
        self.assertEqual(writer["result"]["action"], "apply")
        self.assertIn("result", reader)
        # Ordinary plans omit homepage negotiation on the wire. All their actual
        # traffic rules must still reach the helper unchanged after the read.
        self.assertEqual(self.service._active_plan, replace(plan, homepage_policy_version=0))
        self.assertEqual(self.service._plan_count, 2)

    def test_general_profile_apply_waits_for_a_slow_queue_read(self) -> None:
        profile = Profile("shared", "Shared", upload=Impairment(loss_percent=7))
        plan = compile_plan(
            devices=(),
            profiles={profile.id: profile},
            general_profile=GeneralProfileRule(profile.id),
        )
        self.assert_profile_apply_waits_for_queue_read(plan)

    def test_service_list_apply_waits_for_a_slow_queue_read(self) -> None:
        plan = compile_plan(
            devices=(),
            profiles={},
            service_list_rules=(
                ServiceListRule("video", ("video.example",), 0, Impairment(loss_percent=7), Impairment()),
            ),
        )
        self.assert_profile_apply_waits_for_queue_read(plan)

    def test_normal_access_that_arrives_during_a_slow_queue_read_runs_at_once(self) -> None:
        # Panic runs Normal access. A read holds the request operation lock, but
        # Normal access does not take that lock (gap audit 2026-10-09, finding 1):
        # it serializes on the network lock, which reads do not hold.
        access = FakeAccessManager()
        self.service._access_manager = access
        reader, reader_thread = self.hold_a_read()
        writer, writer_thread = self.in_thread(self.client().normal_access)

        writer_thread.join(5)
        self.assertEqual(reader, {})
        self.executor.release.set()
        reader_thread.join(5)

        self.assertEqual(writer["result"]["ok"], True)
        self.assertTrue(writer["result"]["access_disabled"])
        self.assertIn("result", reader)
        self.assertEqual(access.disabled, 1)
        self.assertIsNone(self.service._active_plan)

    def test_normal_access_is_not_held_by_a_hung_read(self) -> None:
        self.service._access_manager = FakeAccessManager()
        reader, reader_thread = self.hold_a_read()
        answer, thread = self.in_thread(self.client().normal_access)
        thread.join(5)
        self.assertTrue(answer["result"]["access_disabled"])
        self.assertLess(answer["seconds"], 2.0)
        self.executor.release.set()
        reader_thread.join(5)

    def test_normal_access_also_waits_for_the_network_lock_of_a_direct_call(self) -> None:
        self.service._access_manager = FakeAccessManager()
        self.assertTrue(self.service._network_lock.acquire(blocking=False))
        release = threading.Timer(0.3, self.service._network_lock.release)
        release.start()
        started = time.monotonic()
        answer = self.service.handle_payload({"action": "normal-access"})
        release.join(5)
        self.assertTrue(answer["ok"])
        self.assertGreater(time.monotonic() - started, 0.25)

        with mock.patch("jittersim.networking.helper.PANIC_NETWORK_LOCK_WAIT_SECONDS", 0.2):
            self.assertTrue(self.service._network_lock.acquire(blocking=False))
            try:
                answer = self.service.handle_payload({"action": "normal-access"})
            finally:
                self.service._network_lock.release()
        self.assertEqual(answer["reason"], "helper-busy")

    def test_normal_access_is_a_write_action(self) -> None:
        from jittersim.networking.helper import WRITE_ACTIONS

        self.assertIn("normal-access", WRITE_ACTIONS)

    def test_access_apply_that_arrives_during_a_slow_queue_read_waits_and_runs(self) -> None:
        # Block globally, Unblock, Apply access rules and the device quick action send
        # access-apply. A poll of the helper at that moment must not make them fail
        # with "Saved but the household access rules could not be updated".
        from jittersim.engine.access import AccessPlan

        access = FakeAccessManager()
        self.service._access_manager = access
        reader, reader_thread = self.hold_a_read()
        writer, writer_thread = self.in_thread(lambda: self.client().apply_access(AccessPlan()))

        time.sleep(0.3)
        self.assertEqual(writer, {})
        self.executor.release.set()
        writer_thread.join(5)
        reader_thread.join(5)

        self.assertEqual(writer["result"]["ok"], True)
        self.assertGreater(writer["seconds"], 0.25)
        self.assertIn("result", reader)
        self.assertEqual(access.applied, AccessPlan())

    def test_a_read_that_arrives_during_a_write_is_refused_at_once(self) -> None:
        self.executor.gate = is_queue_install
        writer, writer_thread = self.in_thread(lambda: self.client().apply(plan=lossy_plan()))
        self.assertTrue(self.executor.gated.wait(5))

        for read in (
            self.client().queue_stats,
            lambda: self.client()._request({"action": "traffic-counters"}),
        ):
            answer, thread = self.in_thread(read)
            thread.join(5)
            self.assertEqual(answer["error"].reason, "helper-busy")
            self.assertLess(answer["seconds"], 0.25)
        self.executor.release.set()
        writer_thread.join(5)
        self.assertEqual(writer["result"]["action"], "apply")

    def test_capabilities_remain_available_during_a_write(self) -> None:
        self.executor.gate = is_queue_install
        writer, writer_thread = self.in_thread(lambda: self.client().apply(plan=lossy_plan()))
        self.assertTrue(self.executor.gated.wait(5))

        answer, thread = self.in_thread(
            lambda: self.client()._request({"action": "capabilities"})
        )
        thread.join(5)
        try:
            self.assertFalse(thread.is_alive())
            self.assertIn("result", answer, answer)
            self.assertIs(answer["result"]["general_profile"], True)
            self.assertIs(answer["result"]["service_lists"], True)
            self.assertTrue(self.server.operation_lock.locked())
            self.assertEqual(self.service._plan_count, 1)
        finally:
            self.executor.release.set()
        writer_thread.join(5)
        self.assertEqual(writer["result"]["action"], "apply")

    def test_a_write_waits_only_a_bounded_time_for_a_hung_read(self) -> None:
        with mock.patch("jittersim.networking.helper.WRITE_LOCK_WAIT_SECONDS", 0.4):
            reader, reader_thread = self.hold_a_read()
            refused, thread = self.in_thread(self.client().reset)
            thread.join(5)

            self.assertEqual(refused["error"].reason, "helper-busy")
            self.assertGreater(refused["seconds"], 0.35)
            self.assertLess(refused["seconds"], 2.5)
            self.executor.release.set()
            reader_thread.join(5)
            self.assertEqual(self.client().reset()["action"], "reset")

    def test_a_write_also_waits_for_the_network_lock_of_a_direct_call(self) -> None:
        holder = threading.Event()
        self.assertTrue(self.service._network_lock.acquire(blocking=False))

        def release_later() -> None:
            holder.wait(0.3)
            self.service._network_lock.release()

        thread = threading.Thread(target=release_later)
        thread.start()
        started = time.monotonic()
        answer = self.service.handle_payload({"action": "reset"})
        thread.join(5)

        self.assertTrue(answer["ok"])
        self.assertGreater(time.monotonic() - started, 0.25)

        with mock.patch("jittersim.networking.helper.WRITE_LOCK_WAIT_SECONDS", 0.2):
            self.assertTrue(self.service._network_lock.acquire(blocking=False))
            try:
                answer = self.service.handle_payload({"action": "reset"})
            finally:
                self.service._network_lock.release()
        self.assertEqual(answer["reason"], "helper-busy")

    def test_panic_never_fails_while_the_dashboard_keeps_polling(self) -> None:
        self.executor.read_seconds = 0.02
        stop = threading.Event()
        refusals = []

        def poll(call) -> None:
            while not stop.is_set():
                try:
                    call()
                except HelperClientError as error:
                    # a read may be refused when it meets a write but never for another reason
                    if error.reason != "helper-busy":
                        refusals.append(str(error))
                time.sleep(0.005)

        pollers = [
            threading.Thread(target=poll, args=(self.client().queue_stats,)),
            threading.Thread(
                target=poll, args=(lambda: self.client()._request({"action": "capabilities"}),)
            ),
        ]
        for thread in pollers:
            thread.start()
        try:
            for round_number in range(30):
                client = self.client()
                if round_number % 2:
                    self.assertEqual(client.apply(plan=lossy_plan())["action"], "apply")
                else:
                    self.assertEqual(client.reset()["action"], "reset")
                time.sleep(0.01)
        finally:
            stop.set()
            for thread in pollers:
                thread.join(5)
        self.assertEqual(refusals, [])


def good_queue(**changes):
    entry = {
        "device_id": "lab",
        "direction": "upload",
        "interface": "eth0",
        "handle": "11:",
        "kind": "netem",
        "impairment": {
            "delay_ms": 0, "jitter_ms": 0, "loss_percent": 20, "bandwidth_kbit": None,
            "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0,
            "loss_burst_packets": 0,
        },
        "packets": 10, "bytes": 1500, "drops": 3, "overlimits": 0, "requeues": 0,
        "backlog_bytes": 0, "qlen": 0, "generation": "a1b2c3d4-1",
    }
    entry.update(changes)
    return entry


class GeneralProfileSupportClientTests(unittest.TestCase):
    def test_a_busy_helper_is_reported_as_busy_and_not_as_out_of_date(self) -> None:
        client = RecordingHelperClient(
            {"ok": True}, capabilities=HelperClientError("helper is busy", reason="helper-busy")
        )

        with self.assertRaises(HelperClientError) as caught:
            client.check_general_profile_support()

        self.assertEqual(caught.exception.reason, "helper-busy")
        self.assertNotIn("Update the Pi helper", str(caught.exception))

    def test_a_helper_that_does_not_know_the_action_is_out_of_date(self) -> None:
        client = RecordingHelperClient(
            {"ok": True}, capabilities=HelperClientError("unknown action")
        )

        with self.assertRaisesRegex(
            HelperClientError, "Update the Pi helper before applying a profile for everyone"
        ):
            client.check_general_profile_support()

    def test_a_helper_without_the_general_profile_capability_is_out_of_date(self) -> None:
        client = RecordingHelperClient(
            {"ok": True}, capabilities={"ok": True, "protected_dns_capture": True}
        )

        with self.assertRaisesRegex(
            HelperClientError, "Update the Pi helper before applying a profile for everyone"
        ):
            client.check_general_profile_support()

    def test_a_helper_with_the_capability_passes(self) -> None:
        client = RecordingHelperClient(
            {"ok": True}, capabilities={"ok": True, "general_profile": True}
        )

        client.check_general_profile_support()


class QueueStatsClientTests(unittest.TestCase):
    def read(self, queues):
        client = RecordingHelperClient({"ok": True, "action": "queue-stats", "queues": queues})
        result = client.queue_stats()
        self.assertEqual(client.sent, [{"action": "queue-stats"}])
        return result

    def test_accepts_good_entries_for_every_queue_kind(self) -> None:
        entries = [
            good_queue(),
            good_queue(device_id="general-profile", direction="download", kind="fq_codel",
                       impairment=dict(good_queue()["impairment"], bandwidth_kbit=2000)),
            good_queue(device_id="quota:abc-1"),
            good_queue(device_id="servicelist:streaming", handle="25:"),
        ]

        self.assertEqual([q["device_id"] for q in self.read(entries)],
                         ["lab", "general-profile", "quota:abc-1", "servicelist:streaming"])

    def test_accepts_renderer_handles_for_every_supported_mark(self) -> None:
        entries = [
            good_queue(handle=queue_handle(mark, kind),
                       kind="netem" if kind == "duplicate" else kind)
            for kind in ("netem", "duplicate", "fq_codel")
            for mark in range(1, MAX_SHAPED_MARKS_PER_PLAN + 1)
        ]

        result = self.read(entries)

        self.assertEqual([entry["handle"] for entry in result],
                         [entry["handle"] for entry in entries])
        self.assertIn("4a:", [entry["handle"] for entry in result])
        self.assertIn("4f:", [entry["handle"] for entry in result])

    def test_accepts_the_largest_kernel_queue_handle(self) -> None:
        (entry,) = self.read([good_queue(handle="ffff:")])

        self.assertEqual(entry["handle"], "ffff:")

    def test_keeps_only_the_numbers_the_dashboard_uses_from_the_impairment(self) -> None:
        impairment = dict(good_queue()["impairment"], loss_randomness={"variation_percent": 5})

        (entry,) = self.read([good_queue(impairment=impairment)])

        self.assertNotIn("loss_randomness", entry["impairment"])
        self.assertEqual(entry["impairment"]["loss_percent"], 20)

    def test_keeps_the_burst_length_of_the_loss(self) -> None:
        # The speed test start check compares it with the applied plan. A cleaner that
        # dropped it would let a queue with another burst length pass as the same queue.
        impairment = dict(good_queue()["impairment"], loss_burst_packets=8)

        (entry,) = self.read([good_queue(impairment=impairment)])

        self.assertEqual(entry["impairment"]["loss_burst_packets"], 8)

    def test_rejects_every_malformed_entry(self) -> None:
        bad_impairment = dict(good_queue()["impairment"])
        del bad_impairment["delay_ms"]
        no_burst_impairment = dict(good_queue()["impairment"])
        del no_burst_impairment["loss_burst_packets"]
        cases = {
            "negative counter": [good_queue(drops=-1)],
            "bool counter": [good_queue(packets=True)],
            "float counter": [good_queue(bytes=1.5)],
            "huge counter": [good_queue(bytes=2**63)],
            "string counter": [good_queue(qlen="3")],
            "bad handle": [good_queue(handle="abc")],
            "long handle": [good_queue(handle="123456:")],
            "five digit handle": [good_queue(handle="10000:")],
            "upper case handle": [good_queue(handle="4A:")],
            "handle with whitespace": [good_queue(handle=" 4a:")],
            "handle with newline": [good_queue(handle="4a:\n")],
            "handle with suffix": [good_queue(handle="4a:1")],
            "non hex handle": [good_queue(handle="4g:")],
            "bad direction": [good_queue(direction="sideways")],
            "bad kind": [good_queue(kind="tbf")],
            "bad id": [good_queue(device_id="Lab Phone")],
            "empty id": [good_queue(device_id="")],
            "long id": [good_queue(device_id="a" * 129)],
            "long interface": [good_queue(interface="e" * 33)],
            "non string interface": [good_queue(interface=5)],
            "impairment not a dict": [good_queue(impairment=[])],
            "impairment missing key": [good_queue(impairment=bad_impairment)],
            "impairment bad value": [good_queue(impairment=dict(
                good_queue()["impairment"], loss_percent="20"))],
            "impairment bool value": [good_queue(impairment=dict(
                good_queue()["impairment"], delay_ms=True))],
            "entry not a dict": ["lab"],
            "too many entries": [good_queue() for _ in range(601)],
            "not a list": {"lab": good_queue()},
            "missing field": [{key: value for key, value in good_queue().items() if key != "qlen"}],
            "missing generation": [{key: value for key, value in good_queue().items()
                                    if key != "generation"}],
            "empty generation": [good_queue(generation="")],
            "upper case generation": [good_queue(generation="A1B2C3D4-1")],
            "long generation": [good_queue(generation="a" * 25)],
            "generation with a space": [good_queue(generation="a1 b2")],
            "numeric generation": [good_queue(generation=7)],
            "delay above the limit": [good_queue(impairment=dict(
                good_queue()["impairment"], delay_ms=60_001))],
            "jitter above the limit": [good_queue(impairment=dict(
                good_queue()["impairment"], jitter_ms=60_001))],
            "negative delay": [good_queue(impairment=dict(
                good_queue()["impairment"], delay_ms=-1))],
            "loss above 100": [good_queue(impairment=dict(
                good_queue()["impairment"], loss_percent=101))],
            "reorder above 100": [good_queue(impairment=dict(
                good_queue()["impairment"], reorder_percent=101))],
            "duplicate above 100": [good_queue(impairment=dict(
                good_queue()["impairment"], duplicate_percent=101))],
            "corrupt above 100": [good_queue(impairment=dict(
                good_queue()["impairment"], corrupt_percent=101))],
            "burst above 100": [good_queue(impairment=dict(
                good_queue()["impairment"], loss_burst_packets=101))],
            "negative burst": [good_queue(impairment=dict(
                good_queue()["impairment"], loss_burst_packets=-1))],
            "boolean burst": [good_queue(impairment=dict(
                good_queue()["impairment"], loss_burst_packets=True))],
            "string burst": [good_queue(impairment=dict(
                good_queue()["impairment"], loss_burst_packets="4"))],
            "missing burst": [good_queue(impairment=no_burst_impairment)],
            "boolean bandwidth": [good_queue(impairment=dict(
                good_queue()["impairment"], bandwidth_kbit=True))],
            "zero bandwidth": [good_queue(impairment=dict(
                good_queue()["impairment"], bandwidth_kbit=0))],
            "bandwidth above the limit": [good_queue(impairment=dict(
                good_queue()["impairment"], bandwidth_kbit=1_000_001))],
            "string bandwidth": [good_queue(impairment=dict(
                good_queue()["impairment"], bandwidth_kbit="2000"))],
        }
        for name, queues in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(
                    HelperClientError, "^helper returned invalid queue statistics$"
                ):
                    self.read(queues)

    def test_accepts_the_largest_allowed_impairment_values(self) -> None:
        largest = dict(
            good_queue()["impairment"], delay_ms=60_000, jitter_ms=60_000, loss_percent=100,
            reorder_percent=100, duplicate_percent=100, corrupt_percent=100,
            loss_burst_packets=100, bandwidth_kbit=1_000_000,
        )

        (entry,) = self.read([good_queue(impairment=largest, generation="0a-" + "f" * 21)])

        self.assertEqual(entry["impairment"], largest)
        self.assertEqual(entry["generation"], "0a-" + "f" * 21)

    def test_accepts_exactly_600_entries(self) -> None:
        self.assertEqual(len(self.read([good_queue() for _ in range(600)])), 600)


if __name__ == "__main__":
    unittest.main()


class UpstreamSafeApplyResponseTests(unittest.TestCase):
    """The dashboard learns whether upload corruption ran before the Pi forwards."""

    def response(self, report_value):
        applier = NetworkApplier(executor=FakeExecutor(),
                                 topology=Topology(uplink_interface="eth0", test_interface="wlan0"))
        real_apply = applier.apply

        def apply(**arguments):
            real_apply(**arguments)
            # A report object of the chosen shape. With None it lacks the field,
            # like the report of an applier from before the upload-safe layout.
            fields = {"commands_run": 1, "counters_preserved": False}
            if report_value is not None:
                fields["upload_corruption_applied"] = report_value
            return types.SimpleNamespace(**fields)

        applier.apply = apply
        plan = compile_plan(
            devices=(Device("alice", "Alice", "192.168.50.10", "bad"),),
            profiles={"bad": Profile(id="bad", name="Bad", upload=Impairment(corrupt_percent=6))},
        )
        return HelperService(applier).handle_payload({"action": "apply", "plan": plan_to_payload(plan)})

    def test_the_apply_response_reports_upload_corruption(self) -> None:
        self.assertIs(self.response(True)["upload_corruption_applied"], True)
        self.assertIs(self.response(False)["upload_corruption_applied"], False)

    def test_an_applier_without_the_field_reports_false(self) -> None:
        self.assertIs(self.response(None)["upload_corruption_applied"], False)
