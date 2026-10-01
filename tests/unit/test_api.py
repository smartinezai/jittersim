import json
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from email.message import Message
from io import BytesIO
from ipaddress import IPv4Address
from pathlib import Path
from unittest.mock import patch

from jittersim.api.server import DASHBOARD_HTML, ControlRequestHandler, ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    Device,
    DeviceGroup,
    DomainObservation,
    Impairment,
    Profile,
    RandomWebsiteOutcome,
    ServiceList,
)
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError
from tests.helper_support import OlderHelperService, OldHelperService, captures, serving


class FakeHelper:
    def __init__(self, fail_action: str = "") -> None:
        self.fail_action = fail_action
        self.applied_plan = None
        self.reset_called = False

    def apply(self, *, plan):
        if self.fail_action == "apply":
            raise HelperClientError("simulated helper failure")
        self.applied_plan = plan
        return {"ok": True, "action": "apply", "commands_run": 3}

    def reset(self):
        if self.fail_action == "reset":
            raise HelperClientError("simulated helper failure")
        self.reset_called = True
        return {"ok": True, "action": "reset", "commands_run": 3}

    def ping(self):
        if self.fail_action == "ping":
            raise HelperClientError("simulated helper failure")
        return {"ok": True, "action": "ping"}

    def configure_access_point(self, *, ssid, password):
        return {"ok": True, "action": "configure-ap", "commands_run": 3}

    def sync_access_point_from_router(self, *, admin_password, expected_fingerprint, confirm_legacy=False):
        return {
            "ok": True,
            "action": "router-sync-ap",
            "ssid": "Vodafone-Test",
            "router_wifi_enabled": True,
            "commands_run": 3,
        }

    def set_router_wifi(self, *, admin_password, enabled):
        return {"ok": True, "action": "router-wifi", "enabled": enabled}

    def check_router_password(self, *, admin_password):
        return {"ok": True, "action": "router-check"}

    def read_router_wifi_status(self, *, admin_password):
        return {"ok": True, "enabled": False}

    def connected_devices(self):
        return [
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "state": "connected",
                "association_verified": True,
                "neighbor_state": "reachable",
                "hostname": "MacBook-Pro",
                "signal_dbm": -47,
                "connected_seconds": 95,
            }
        ]

    def connected_device_snapshot(self):
        return {
            "devices": self.connected_devices(),
            "association_status_available": True,
        }

    def uplink_info(self):
        return {
            "interface": "eth0",
            "connection_name": "netplan-eth0",
            "gateway": "192.168.0.1",
        }

    def observe_dns_queries(self, excluded_macs=()):
        return [
            {
                "ipv4_address": "192.168.50.178",
                "domain": "amazon.com",
                "query_count": 2,
                "first_seen": 10,
                "last_seen": 10,
            }
        ]

    def traffic_counters(self):
        return {
            "devices": {"alice": 0},
            "services": {},
            "directions": {
                "devices": {
                    "alice": {
                        "upload_bytes": 120,
                        "download_bytes": 80,
                        "upload_packets": 2,
                        "download_packets": 1,
                    }
                },
                "services": {},
            },
        }


class PlanRecordingHelper(FakeHelper):
    """Keep every plan that was applied rather than only the last one."""

    def __init__(self) -> None:
        super().__init__()
        self.plans = []

    def apply(self, *, plan):
        self.plans.append(plan)
        return super().apply(plan=plan)


class MovingClientHelper(FakeHelper):
    """One client that keeps its hardware address while its address changes or it leaves."""

    def __init__(self, mac, address) -> None:
        super().__init__()
        self.mac = mac
        self.address = address
        self.online = True

    def connected_devices(self):
        if not self.online:
            return []
        return [{**super().connected_devices()[0], "mac_address": self.mac,
                 "ipv4_address": self.address}]

    def observe_dns_queries(self, excluded_macs=()):
        return [{"ipv4_address": self.address, "domain": "amazon.com", "query_count": 2}]


class ControlServerTests(unittest.TestCase):
    def test_router_status_and_results_require_admin_and_own_site(self):
        self.server.admin_token = "fixture-admin"
        for path in ("/api/router/status", "/api/router/operation", "/api/router/wifi", "/api/router/check"):
            for authorized, cross_site, expected in ((False, False, 401), (True, True, 403)):
                handler = object.__new__(ControlRequestHandler)
                handler.server = self.server
                handler.path = path
                handler.client_address = ("127.0.0.1", 12345)
                handler.headers = Message()
                handler.headers["Host"] = "127.0.0.1:8080"
                if authorized:
                    handler.headers["Authorization"] = "Bearer fixture-admin"
                if cross_site:
                    handler.headers["Origin"] = "https://different.example"
                captured = []
                handler._send_response = lambda **values: captured.append(values)
                handler._handle_write()
                self.assertEqual(captured[0]["status"], expected)

    def test_background_router_change_keeps_dashboard_responsive_and_records_once(self):
        release = threading.Event()

        class SlowHelper(FakeHelper):
            def set_router_wifi(self, *, admin_password, enabled):
                assert release.wait(2)
                return {"enabled": enabled}

        self.server.helper_client = SlowHelper()
        try:
            status, _, body = self.server.write_response_for("/api/router/wifi", {
                "admin_password": "private-password", "enabled": False,
                "confirm_disable": True, "background": True})
            self.assertEqual(status, 202)
            operation = json.loads(body)["operation_id"]
            self.assertEqual(self.server.response_for("/api/health")[0], 200)
            state = json.loads(self.server.write_response_for("/api/router/status", {
                "admin_password": "another-password"})[2])["result"]
            self.assertTrue(state["changing"])
            self.assertIsNone(state["enabled"])
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status, _, body = self.server.write_response_for("/api/router/operation", {
                    "admin_password": "private-password", "operation_id": operation})
                if status != 202:
                    break
                time.sleep(0.005)
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["result"]["enabled"])
            self.server.write_response_for("/api/router/operation", {
                "admin_password": "private-password", "operation_id": operation})
            history = self.repository.list_recent_actions()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["action"], "/api/router/wifi")
            self.assertNotIn("private-password", str(history) + body.decode())
        finally:
            release.set()

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

    def test_startup_recovery_reapplies_the_last_applied_configuration(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper
        self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )

        self.server.recover_saved_network_state()

        self.assertIsNotNone(helper.applied_plan)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "applied")

    def test_startup_recovery_keeps_an_explicit_reset(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper
        self.repository.update_runtime_state(
            observed_state="reset",
            last_action="reset",
            last_result="success",
            last_error=None,
        )

        self.server.recover_saved_network_state()

        self.assertTrue(helper.reset_called)
        self.assertIsNone(helper.applied_plan)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")

    def test_startup_recovery_does_not_apply_on_a_new_installation(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper

        self.server.recover_saved_network_state()

        self.assertFalse(helper.reset_called)
        self.assertIsNone(helper.applied_plan)

    def test_startup_recovery_reports_a_helper_failure(self) -> None:
        self.server.helper_client = FakeHelper(fail_action="apply")
        self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )

        self.server.recover_saved_network_state()

        state = self.repository.get_runtime_state()
        self.assertEqual(state.observed_state, "error")
        self.assertEqual(state.last_result, "failure")

    def test_health_response_identifies_configuration_only_mode(self) -> None:
        status, content_type, body = self.server.response_for("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "helper_configured": False,
                "helper_reachable": False,
                "mode": "configuration-only",
                "status": "ok",
            },
        )

    def test_served_dashboard_script_parses(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to parse the served dashboard script")
        script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)
        self.assertIsNotNone(script)
        result = subprocess.run(
            [node, "--check"], input=script.group(1), text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_diagnostics_and_history_show_bounded_admin_information(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.record_action(action="/api/apply", status_code=200)

        status, _, body = self.server.response_for("/api/diagnostics")
        diagnostics = json.loads(body)
        history_status, _, history_body = self.server.response_for("/api/action-history")
        history = json.loads(history_body)
        backup_status, content_type, backup = self.server.response_for("/api/backup")

        self.assertEqual(status, 200)
        self.assertEqual(diagnostics["connected_clients"], 1)
        self.assertEqual(diagnostics["traffic_counters"]["devices"], {"alice": 0})
        self.assertEqual(history_status, 200)
        self.assertEqual(history["actions"][0]["action"], "/api/apply")
        self.assertEqual(backup_status, 200)
        self.assertEqual(content_type, "application/vnd.sqlite3")
        self.assertTrue(backup.startswith(b"SQLite format 3\x00"))

    def test_reset_diagnostics_keeps_client_scan_without_reading_removed_counters(self) -> None:
        class ResetHelper(FakeHelper):
            def traffic_counters(self):
                raise AssertionError("Reset traffic tables must not be inspected")

        self.server.helper_client = ResetHelper()
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None,
        )

        status, _, body = self.server.response_for("/api/diagnostics")
        diagnostics = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(diagnostics["runtime"]["observed_state"], "reset")
        self.assertEqual(diagnostics["connected_clients"], 1)
        self.assertIsNone(diagnostics["traffic_counters"])
        self.assertTrue(diagnostics["traffic_counters_message"])
        self.assertEqual(diagnostics["warnings"], [])

    def test_active_diagnostics_still_reports_failed_counters(self) -> None:
        class FailedCountersHelper(FakeHelper):
            def traffic_counters(self):
                raise HelperClientError("simulated counter failure")

        self.server.helper_client = FailedCountersHelper()
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success", last_error=None,
        )

        status, _, body = self.server.response_for("/api/diagnostics")
        diagnostics = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(diagnostics["connected_clients"], 1)
        self.assertIsNone(diagnostics["traffic_counters_message"])
        self.assertIsNone(diagnostics["traffic_counters"])
        self.assertEqual(diagnostics["warnings"], ["traffic counters are unavailable"])

    def test_live_traffic_requires_admin_token_and_returns_directional_counters(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def get_traffic(authorization):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.client_address = ("127.0.0.1", 45000)
            handler.path = "/api/traffic"
            handler.headers = Message()
            if authorization:
                handler.headers["Authorization"] = authorization
            response = {}
            handler._send_response = lambda **values: response.update(values)
            handler.do_GET()
            return response

        unauthorized = get_traffic(None)
        incorrect = get_traffic("Bearer wrong-token")
        authorized = get_traffic("Bearer admin-token")

        self.assertEqual(unauthorized["status"], 401)
        self.assertEqual(incorrect["status"], 401)
        self.assertEqual(authorized["status"], 200)
        self.assertTrue(json.loads(authorized["body"])["available"])
        self.assertEqual(
            json.loads(authorized["body"])["devices"]["alice"],
            {
                "upload_bytes": 120,
                "download_bytes": 80,
                "upload_packets": 2,
                "download_packets": 1,
            },
        )
        traffic = json.loads(authorized["body"])
        self.assertEqual(
            traffic["isp_limits"],
            {"upload_kbit": None, "download_kbit": 100_000},
        )
        self.assertFalse(traffic["profile_limits_available"])
        self.assertEqual(traffic["profile_limits"], {})

    def test_traffic_settings_write_is_protected_and_does_not_apply_traffic(self) -> None:
        self.server.admin_token = "admin-token"
        helper = FakeHelper()
        self.server.helper_client = helper

        def save_rates(*, authorization, body):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.client_address = ("127.0.0.1", 45000)
            handler.path = "/api/traffic-settings"
            handler.headers = Message()
            if authorization:
                handler.headers["Authorization"] = authorization
            handler.headers["Content-Length"] = str(len(body))
            handler.rfile = BytesIO(body)
            response = {}
            handler._send_response = lambda **values: response.update(values)
            handler.do_POST()
            return response

        body = json.dumps(
            {"upload_kbit": None, "download_kbit": 150_000}
        ).encode()
        unauthorized = save_rates(authorization=None, body=body)
        incorrect = save_rates(authorization="Bearer wrong-token", body=body)
        authorized = save_rates(authorization="Bearer admin-token", body=body)
        invalid = save_rates(
            authorization="Bearer admin-token",
            body=b'{"upload_kbit":null,"download_kbit":101}',
        )

        self.assertEqual(unauthorized["status"], 401)
        self.assertEqual(incorrect["status"], 401)
        self.assertEqual(authorized["status"], 200)
        self.assertEqual(invalid["status"], 400)
        self.assertEqual(
            json.loads(authorized["body"]),
            {"upload_kbit": None, "download_kbit": 150_000},
        )
        self.assertEqual(
            self.repository.get_traffic_rate_settings(),
            {"upload_kbit": None, "download_kbit": 150_000},
        )
        self.assertIsNone(helper.applied_plan)

    def test_traffic_endpoint_includes_applied_default_queue_limits(self) -> None:
        class SnapshotScheduler:
            def applied_queue_bandwidth_limits(self):
                return {
                    "alice": {
                        "upload_kbit": None,
                        "download_kbit": 20_000,
                    }
                }

        self.server.helper_client = FakeHelper()
        self.server.scheduler = SnapshotScheduler()

        status, _, body = self.server.response_for("/api/traffic")
        response = json.loads(body)

        self.assertEqual(status, 200)
        self.assertTrue(response["profile_limits_available"])
        self.assertEqual(
            response["profile_limits"]["alice"],
            {"upload_kbit": None, "download_kbit": 20_000},
        )

    def test_private_read_routes_require_token_including_backup(self) -> None:
        self.server.admin_token = "admin-token"
        for path in (
            "/api/backup",
            "/api/observations",
            "/api/devices",
            "/api/plan",
            "/api/service-assignments",
            "/api/connected-devices",
            "/api/connected-device-labels",
            "/api/traffic-settings",
            "/api/random-website-analytics",
        ):
            with self.subTest(path=path):
                handler = object.__new__(ControlRequestHandler)
                handler.server = self.server
                handler.client_address = ("127.0.0.1", 45000)
                handler.path = path
                handler.headers = Message()
                response = {}
                handler._send_response = lambda **values: response.update(values)
                handler.do_GET()
                self.assertEqual(response["status"], 401)
                handler.headers["Authorization"] = "Bearer admin-token"
                handler.do_GET()
                self.assertEqual(response["status"], 200)

    def test_random_website_analytics_route_filters_and_validates_queries(self) -> None:
        now = int(time.time())
        self.repository.save_random_website_outcomes(
            [
                RandomWebsiteOutcome("alice", "kept.example", True, now),
                RandomWebsiteOutcome("alice", "removed.example", False, now),
            ],
            now=now,
        )

        status, content_type, body = self.server.response_for(
            "/api/random-website-analytics",
            query="days=1&device_id=alice",
        )
        report = json.loads(body)
        invalid_range = self.server.response_for(
            "/api/random-website-analytics",
            query="days=31",
        )[0]
        repeated_filter = self.server.response_for(
            "/api/random-website-analytics",
            query="days=1&days=2",
        )[0]

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(report["days"], 1)
        self.assertEqual(
            {
                row["domain"]: (row["kept_count"], row["removed_count"])
                for row in report["rows"]
            },
            {"kept.example": (1, 0), "removed.example": (0, 1)},
        )
        self.assertEqual(invalid_range, 400)
        self.assertEqual(repeated_filter, 400)

    def test_health_reports_an_unreachable_helper(self) -> None:
        self.server.helper_client = FakeHelper(fail_action="ping")
        _, _, body = self.server.response_for("/api/health")
        self.assertEqual(json.loads(body)["status"], "degraded")
        self.assertFalse(json.loads(body)["helper_reachable"])

    def test_write_history_records_only_authorized_route_and_result(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def handle_write(*, authorization, body, path="/api/apply"):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.client_address = ("127.0.0.1", 45000)
            handler.path = path
            handler.headers = Message()
            handler.headers["Authorization"] = authorization
            handler.headers["Content-Length"] = str(len(body))
            handler.rfile = BytesIO(body)
            response = {}
            handler._send_response = lambda **values: response.update(values)
            handler._handle_write()
            return response

        secret_marker = b"not-for-history"
        result = handle_write(
            authorization="Bearer admin-token",
            body=b'{"note":"' + secret_marker + b'"}',
        )
        history = self.repository.list_recent_actions()
        count_after_authorized_request = len(history)
        unauthorized = handle_write(
            authorization="Bearer incorrect",
            body=b"{}",
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(history[0]["action"], "/api/apply")
        self.assertEqual(history[0]["status_code"], 200)
        self.assertNotIn("note", history[0])
        self.assertEqual(len(self.repository.list_recent_actions()), count_after_authorized_request)
        self.assertEqual(unauthorized["status"], 401)

    def test_connected_label_auth_and_profile_isolation(self) -> None:
        self.server.admin_token = "admin-token"
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.client_address = ("127.0.0.1", 45000)
        handler.path = "/api/connected-device-labels"
        handler.headers = Message()
        body = json.dumps(
            {
                "mac_address": "DC:1E:D5:3E:97:4C",
                "display_name": "Family MacBook",
                "device_type": "laptop",
            }
        ).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = BytesIO(body)
        response = {}
        handler._send_response = lambda **values: response.update(values)

        handler.do_POST()
        self.assertEqual(response["status"], 401)

        handler.headers["Authorization"] = "Bearer admin-token"
        handler.do_POST()
        saved = self.repository.list_connected_device_labels()

        self.assertEqual(response["status"], 200)
        self.assertEqual(
            saved["dc:1e:d5:3e:97:4c"],
            {"display_name": "Family MacBook", "device_type": "laptop"},
        )
        self.assertEqual(self.repository.get_device("alice").profile_id, "slow")
        _, _, labels_body = self.server.response_for("/api/connected-device-labels")
        self.assertEqual(
            json.loads(labels_body)["labels"],
            [
                {
                    "mac_address": "dc:1e:d5:3e:97:4c",
                    "display_name": "Family MacBook",
                    "device_type": "laptop",
                }
            ],
        )
        self.server.helper_client = FakeHelper()
        _, _, connected_body = self.server.response_for("/api/connected-devices")
        connected_device = json.loads(connected_body)["devices"][0]
        self.assertEqual(connected_device["display_name"], "Family MacBook")
        self.assertEqual(connected_device["device_type_source"], "saved")

        status, _, body = self.server.write_response_for(
            "/api/connected-device-labels/delete",
            {"mac_address": "dc:1e:d5:3e:97:4c"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"deleted": True})
        self.assertEqual(self.repository.list_connected_device_labels(), {})

    def test_tls_requires_both_files_and_encrypts_accepted_connections(self) -> None:
        with self.assertRaisesRegex(ValueError, "TLS is required"):
            ControlServer(("0.0.0.0", 0), self.repository)
        with self.assertRaisesRegex(ValueError, "provided together"):
            ControlServer(
                ("127.0.0.1", 0),
                self.repository,
                tls_certfile=Path("dashboard.crt"),
            )

        openssl = shutil.which("openssl")
        if openssl is None:
            self.skipTest("OpenSSL is required to create a test certificate")
        with tempfile.TemporaryDirectory(prefix="jittersim-tls-test-") as directory:
            certificate = Path(directory) / "dashboard.crt"
            key = Path(directory) / "dashboard.key"
            subprocess.run(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-nodes",
                    "-days",
                    "1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(certificate),
                    "-subj",
                    "/CN=127.0.0.1",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            secure_server = ControlServer(
                ("127.0.0.1", 0),
                self.repository,
                tls_certfile=certificate,
                tls_keyfile=key,
            )
            response: list[bytes] = []
            accepted_encryption: list[bool] = []
            accept_connection = secure_server.get_request

            def accept_encrypted_connection():
                connection, address = accept_connection()
                accepted_encryption.append(
                    isinstance(connection, ssl.SSLSocket) and connection.cipher() is not None
                )
                return connection, address

            secure_server.get_request = accept_encrypted_connection

            def send_health_request() -> None:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                context.maximum_version = ssl.TLSVersion.TLSv1_2
                with socket.create_connection(secure_server.server_address) as connection:
                    with context.wrap_socket(connection, server_hostname="127.0.0.1") as client:
                        client.sendall(
                            b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"
                        )
                        response.append(client.recv(4096))

            client_thread = threading.Thread(target=send_health_request)
            client_thread.start()
            try:
                secure_server.handle_request()
                client_thread.join(timeout=2)
            finally:
                secure_server.server_close()

        self.assertTrue(secure_server.secure_transport)
        self.assertEqual(accepted_encryption, [True])
        self.assertEqual(len(response), 1)
        self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))

    def test_data_limit_endpoint_preserves_usage_and_can_reset_it(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/data-limits",
            {
                "device_id": "alice",
                "profile_id": "slow",
                "limit_bytes": 1_000,
                "reset_mode": "manual",
                "reset_interval_seconds": 0,
                "reset_times": [],
                "enabled": True,
            },
        )

        self.assertEqual(status, 200)
        self.repository.record_data_usage("alice", 1_100)
        _, _, body = self.server.response_for("/api/data-limits")
        self.assertTrue(json.loads(body)["data_limits"][0]["reached"])

        status, _, body = self.server.write_response_for(
            "/api/data-limits/reset",
            {"device_id": "alice"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data_limit"]["used_bytes"], 0)

    def test_service_quota_endpoint_stores_selected_observed_domains(self) -> None:
        self.repository.save_domain_observations(
            (
                DomainObservation(
                    ipv4_address="192.168.50.10",
                    domain="netflix.com",
                    query_count=1,
                    first_seen=10,
                    last_seen=10,
                ),
                DomainObservation(
                    ipv4_address="192.168.50.10",
                    domain="nflxvideo.net",
                    query_count=1,
                    first_seen=10,
                    last_seen=10,
                ),
            ),
            now=10,
        )
        status, _, body = self.server.write_response_for(
            "/api/service-quotas",
            {
                "id": "alice-video",
                "device_id": "alice",
                "name": "Video service",
                "domains": ["netflix.com", "nflxvideo.net"],
                "profile_id": "slow",
                "limit_bytes": 2_000,
                "weight_percent": 120,
                "reset_mode": "daily",
                "reset_times": [120, 720],
                "reset_interval_seconds": 0,
                "enabled": True,
            },
        )

        self.assertEqual(status, 200)
        payload = json.loads(body)["service_quota"]
        self.assertEqual(payload["domains"], ["netflix.com", "nflxvideo.net"])
        self.assertEqual(payload["weight_percent"], 120)
        _, _, body = self.server.response_for("/api/service-quotas")
        self.assertEqual(json.loads(body)["service_quotas"][0]["name"], "Video service")

    def test_browser_service_profile_accepts_direct_domains_and_rejects_overlap(self) -> None:
        self.server.helper_client = FakeHelper()
        profile = {
            "id": "alice-video",
            "device_id": "alice",
            "name": "Video",
            "domains": ["youtube.com", "netflix.com"],
            "profile_id": "slow",
            "enabled": True,
        }
        status, _, body = self.server.write_response_for("/api/service-assignments", profile)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["live_applied"])
        self.assertEqual(self.server.helper_client.applied_plan.service_assignment_rules[0].mark, 2)

        status, _, _ = self.server.write_response_for(
            "/api/service-assignments",
            {**profile, "id": "alice-more", "domains": ["www.youtube.com"]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(len(self.repository.list_service_assignments()), 1)

        status, _, _ = self.server.write_response_for(
            "/api/service-assignments",
            {**profile, "id": "alice-address", "domains": ["203.0.113.10"]},
        )
        self.assertEqual(status, 400)

        status, _, _ = self.server.write_response_for(
            "/api/service-assignments/delete", {"id": "alice-video"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_service_assignments(), ())

    def test_service_quota_rejects_a_domain_not_observed_for_the_device(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/service-quotas",
            {
                "id": "alice-unknown",
                "device_id": "alice",
                "name": "Unknown service",
                "domains": ["not-observed.example"],
                "profile_id": "slow",
                "limit_bytes": 2_000,
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid service quota configuration"})

    def test_time_quota_can_be_saved_started_stopped_and_reset(self) -> None:
        self.repository.save_domain_observations(
            (
                DomainObservation(
                    ipv4_address="192.168.50.10",
                    domain="netflix.com",
                    query_count=1,
                    first_seen=10,
                    last_seen=10,
                ),
            ),
            now=10,
        )
        status, _, body = self.server.write_response_for(
            "/api/time-quotas",
            {
                "id": "alice-netflix-time",
                "device_id": "alice",
                "name": "Netflix time",
                "domains": ["netflix.com"],
                "profile_id": "slow",
                "limit_seconds": 5 * 60 * 60,
                "window_duration_seconds": 12 * 60 * 60,
                "reset_mode": "daily",
                "reset_times": [600, 1_200],
                "reset_interval_seconds": 0,
                "enabled": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["time_quota"]["limit_seconds"], 18_000)

        status, _, body = self.server.write_response_for(
            "/api/time-quotas/start",
            {"id": "alice-netflix-time"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["time_quota"]["running"])

        status, _, body = self.server.write_response_for(
            "/api/time-quotas/stop",
            {"id": "alice-netflix-time"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["time_quota"]["running"])

        self.repository.record_time_usage(
            "alice-netflix-time",
            100,
            10,
            active=False,
        )
        status, _, body = self.server.write_response_for(
            "/api/time-quotas/reset",
            {"id": "alice-netflix-time"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["time_quota"]["used_seconds"], 0)

    def test_health_response_identifies_hardware_mode_when_helper_is_configured(self) -> None:
        self.server.helper_client = FakeHelper()

        status, content_type, body = self.server.response_for("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "helper_configured": True,
                "helper_reachable": True,
                "mode": "hardware",
                "status": "ok",
            },
        )

    def test_configuration_endpoints_serialize_validated_values(self) -> None:
        status, _, body = self.server.response_for("/api/profiles")
        profiles = json.loads(body)["profiles"]
        self.assertEqual(status, 200)
        self.assertEqual(profiles[0]["upload"]["delay_ms"], 3_000)

        status, _, body = self.server.response_for("/api/devices")
        devices = json.loads(body)["devices"]
        self.assertEqual(status, 200)
        self.assertEqual(devices[0]["ipv4_address"], "192.168.50.10")

    def test_router_endpoint_describes_the_tg3442de_integration(self) -> None:
        status, _, body = self.server.response_for("/api/router")

        self.assertEqual(status, 200)
        router = json.loads(body)
        self.assertEqual(router["model"], "Vodafone TG3442DE")
        self.assertTrue(router["automatic_wifi_control"])
        self.assertEqual(router["wifi_control"], "tg3442de-local-web")

    def test_uplink_endpoint_reports_the_pi_connection_and_router_controls(self) -> None:
        self.server.helper_client = FakeHelper()

        status, content_type, body = self.server.response_for("/api/uplink")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "available": True,
                "router": {
                    "model": "Vodafone TG3442DE",
                    "settings_url": "http://192.168.0.1",
                    "wifi_control": "tg3442de-local-web",
                },
                "uplink": {
                    "connection_name": "netplan-eth0",
                    "gateway": "192.168.0.1",
                    "interface": "eth0",
                },
            },
        )

    def test_uplink_endpoint_reports_unavailable_without_helper(self) -> None:
        status, content_type, body = self.server.response_for("/api/uplink")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "available": False,
                "message": "upstream connection information is unavailable in configuration-only mode",
                "uplink": None,
            },
        )

    def test_router_sync_endpoint_uses_the_helper(self) -> None:
        self.server.helper_client = FakeHelper()

        status, content_type, body = self.server.write_response_for(
            "/api/router/sync-ap",
            {"admin_password": "router-secret", "expected_fingerprint": "a" * 64},
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body)["result"]["ssid"], "Vodafone-Test")

    def test_router_preview_does_not_expose_helper_secrets(self) -> None:
        class PreviewHelper(FakeHelper):
            def preview_router_wifi(self, *, admin_password):
                return {"ssid": "Home", "security": "WPA2", "band": "2.4 GHz",
                        "fingerprint": "a" * 64, "legacy": False, "password": "private"}

        self.server.helper_client = PreviewHelper()
        status, _, body = self.server.write_response_for(
            "/api/router/preview-ap", {"admin_password": "router-secret"})
        self.assertEqual(status, 200)
        self.assertNotIn(b"private", body)
        self.assertNotIn(b"router-secret", body)

    def test_router_preview_rejects_a_helper_without_detection(self) -> None:
        self.server.helper_client = FakeHelper()
        status, _, body = self.server.write_response_for(
            "/api/router/preview-ap", {"admin_password": "router-secret"})
        self.assertEqual(status, 502)
        self.assertIn("out of date", json.loads(body)["error"])

    def test_router_import_error_guidance_is_safe_in_response_and_logs(self) -> None:
        from jittersim.networking.router import ROUTER_IMPORT_MESSAGES

        for message, expected in (
            (ROUTER_IMPORT_MESSAGES["unsupported"], ROUTER_IMPORT_MESSAGES["unsupported"]),
            ("router-secret was present in a response", "router Wi-Fi import failed"),
        ):
            with self.subTest(message=message):
                class FailedHelper(FakeHelper):
                    def sync_access_point_from_router(self, *, admin_password, expected_fingerprint, confirm_legacy=False):
                        raise HelperClientError(message)

                self.server.helper_client = FailedHelper()
                with self.assertLogs("jittersim.api.server", level="WARNING") as logs:
                    status, _, body = self.server.write_response_for(
                        "/api/router/sync-ap", {"admin_password": "router-secret", "expected_fingerprint": "a" * 64},
                    )
                self.assertEqual(status, 502)
                self.assertEqual(json.loads(body)["error"], expected)
                self.assertNotIn("router-secret", body.decode() + "\n".join(logs.output))

    def test_router_check_endpoint_signs_in_through_the_helper(self) -> None:
        self.server.helper_client = FakeHelper()

        status, content_type, body = self.server.write_response_for(
            "/api/router/check", {"admin_password": "router-secret"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body), {"result": {"ok": True, "action": "router-check"}})
        self.assertNotIn("router-secret", body.decode())

    def test_router_check_endpoint_needs_a_helper_and_a_password(self) -> None:
        self.server.helper_client = None
        status, _, body = self.server.write_response_for(
            "/api/router/check", {"admin_password": "router-secret"}
        )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "network helper is not configured")

        class UnexpectedHelper(FakeHelper):
            def check_router_password(self, *, admin_password):
                raise AssertionError("no router contact without a password")

        self.server.helper_client = UnexpectedHelper()
        for payload in ({}, {"admin_password": ""}, {"admin_password": 5}):
            with self.subTest(payload=payload):
                status, _, body = self.server.write_response_for("/api/router/check", payload)
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body)["error"], "router admin password is required")

    def test_router_check_error_guidance_is_safe_in_response_and_logs(self) -> None:
        from jittersim.networking.router import ROUTER_CHECK_MESSAGES

        for message, expected in (
            (ROUTER_CHECK_MESSAGES["login"], ROUTER_CHECK_MESSAGES["login"]),
            (ROUTER_CHECK_MESSAGES["unreachable"], ROUTER_CHECK_MESSAGES["unreachable"]),
            ("router-secret was present in a response", ROUTER_CHECK_MESSAGES["failed"]),
            ("some other helper text", ROUTER_CHECK_MESSAGES["failed"]),
            ("unknown action", ROUTER_CHECK_MESSAGES["outdated"]),
        ):
            with self.subTest(message=message):

                class FailedHelper(FakeHelper):
                    def check_router_password(self, *, admin_password):
                        raise HelperClientError(message)

                self.server.helper_client = FailedHelper()
                with self.assertLogs("jittersim.api.server", level="WARNING") as logs:
                    status, _, body = self.server.write_response_for(
                        "/api/router/check", {"admin_password": "router-secret"}
                    )
                self.assertEqual(status, 502)
                self.assertEqual(json.loads(body)["error"], expected)
                self.assertNotIn("router-secret", body.decode() + "\n".join(logs.output))

    def test_router_check_route_requires_the_admin_token_and_records_no_password(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.client_address = ("127.0.0.1", 45000)
        handler.path = "/api/router/check"
        handler.headers = Message()
        body = json.dumps({"admin_password": "router-secret"}).encode()
        handler.headers["Content-Length"] = str(len(body))
        response = {}
        handler._send_response = lambda **values: response.update(values)

        handler.rfile = BytesIO(body)
        handler.do_POST()
        self.assertEqual(response["status"], 401)

        handler.headers["Authorization"] = "Bearer admin-token"
        handler.rfile = BytesIO(body)
        handler.do_POST()
        self.assertEqual(response["status"], 200)
        history = self.repository.list_recent_actions()
        self.assertEqual(
            (history[0]["action"], history[0]["status_code"]), ("/api/router/check", 200)
        )
        self.assertNotIn("router-secret", str(history))

    def test_router_wifi_endpoint_requires_confirmation_to_disable(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for(
            "/api/router/wifi",
            {"admin_password": "router-secret", "enabled": False},
        )
        self.assertEqual(status, 400)
        self.assertIn("requires confirmation", json.loads(body)["error"])

        status, _, body = self.server.write_response_for(
            "/api/router/wifi",
            {
                "admin_password": "router-secret",
                "enabled": False,
                "confirm_disable": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["result"]["enabled"])

        status, _, body = self.server.write_response_for(
            "/api/router/wifi",
            {"admin_password": "router-secret", "enabled": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["result"]["enabled"])

    def test_router_toggle_propagates_only_safe_errors(self):
        from jittersim.networking.router import ROUTER_WIFI_MESSAGES

        for reported in (*ROUTER_WIFI_MESSAGES.values(), "router-secret response contents"):
            with self.subTest(reported=reported):

                class RejectedHelper(FakeHelper):
                    def set_router_wifi(self, *, admin_password, enabled):
                        raise HelperClientError(reported)

                self.server.helper_client = RejectedHelper()
                with self.assertLogs("jittersim.api.server", level="WARNING") as logs:
                    status, _, body = self.server.write_response_for(
                        "/api/router/wifi",
                        {
                            "admin_password": "router-secret",
                            "enabled": False,
                            "confirm_disable": True,
                        },
                    )
                self.assertEqual(status, 502)
                expected = (
                    reported
                    if reported in ROUTER_WIFI_MESSAGES.values()
                    else ROUTER_WIFI_MESSAGES["uncertain"]
                )
                self.assertEqual(json.loads(body)["error"], expected)
                self.assertNotIn("router-secret", body.decode() + "\n".join(logs.output))
                self.assertNotIn("response contents", body.decode() + "\n".join(logs.output))

    def test_connected_devices_endpoint_reports_unavailable_without_helper(self) -> None:
        status, content_type, body = self.server.response_for("/api/connected-devices")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "available": False,
                "devices": [],
                "message": "connected-client information is unavailable in configuration-only mode",
            },
        )

    def test_connected_devices_endpoint_uses_the_network_helper(self) -> None:
        self.server.helper_client = FakeHelper()

        status, content_type, body = self.server.response_for("/api/connected-devices")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "available": True,
                "association_status_available": True,
                "devices": [
                    {
                        "ipv4_address": "192.168.50.178",
                        "mac_address": "dc:1e:d5:3e:97:4c",
                        "state": "connected",
                        "association_verified": True,
                        "neighbor_state": "reachable",
                        "hostname": "MacBook-Pro",
                        "signal_dbm": -47,
                        "connected_seconds": 95,
                        "display_name": "MacBook-Pro",
                        "saved_display_name": "",
                        "saved_device_type": "",
                        "has_saved_label": False,
                        "device_type": "laptop",
                        "device_type_source": "hostname suggestion",
                        "assigned_device_id": None,
                        "assigned_device_name": None,
                        "assigned_profile_id": None,
                        "unaffected": False,
                        "group_id": None,
                        "group_name": None,
                        "group_profile_id": None,
                    }
                ],
            },
        )

    def test_connected_device_profile_assignment_saves_and_applies(self) -> None:
        self.server.helper_client = FakeHelper()

        status, content_type, body = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        response = json.loads(body)
        self.assertEqual(response["device"]["id"], "client-dc1ed53e974c")
        self.assertEqual(response["device"]["profile_id"], "slow")
        self.assertEqual(response["device"]["mac_address"], "dc:1e:d5:3e:97:4c")
        self.assertEqual(
            self.repository.get_device_by_ipv4_address("192.168.50.178").profile_id,
            "slow",
        )
        self.assertEqual(self.server.helper_client.applied_plan.mark_rules[0].device_id, "alice")
        self.assertEqual(len(self.server.helper_client.applied_plan.mark_rules), 2)

    def test_profile_assignment_rejects_a_neighbor_without_verified_association(self) -> None:
        class UnverifiedHelper(FakeHelper):
            def connected_device_snapshot(self):
                device = self.connected_devices()[0]
                device["state"] = "unknown"
                device["association_verified"] = False
                return {"devices": [device], "association_status_available": False}

        self.server.helper_client = UnverifiedHelper()
        status, _, body = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 409)
        self.assertIn("no longer verified", json.loads(body)["error"])
        self.assertIsNone(self.repository.get_device_by_ipv4_address("192.168.50.178"))

    def test_observation_scan_persists_and_lists_domains(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for(
            "/api/observations/scan",
            {},
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["captured_observations"], 1)

        status, _, body = self.server.response_for("/api/observations")

        self.assertEqual(status, 200)
        response = json.loads(body)
        self.assertTrue(response["available"])
        self.assertEqual(response["devices"][0]["ipv4_address"], "192.168.50.178")
        self.assertEqual(response["devices"][0]["domains"][0]["domain"], "amazon.com")

    def test_plan_endpoint_uses_the_deterministic_compiler(self) -> None:
        status, _, body = self.server.response_for("/api/plan")

        plan = json.loads(body)["plan"]
        self.assertEqual(status, 200)
        self.assertEqual(plan["mark_rules"][0]["mark"], 1)
        self.assertEqual(plan["queues"][0]["impairment"]["delay_ms"], 3_000)

    def test_dashboard_contains_configuration_and_network_controls(self) -> None:
        status, content_type, body = self.server.response_for("/")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertEqual(body.decode("utf-8"), DASHBOARD_HTML)
        self.assertIn("Checking control mode", DASHBOARD_HTML)
        self.assertIn("Save profile", DASHBOARD_HTML)
        self.assertIn("Save device", DASHBOARD_HTML)
        self.assertIn("Group devices", DASHBOARD_HTML)
        self.assertIn("group-devices", DASHBOARD_HTML)
        self.assertIn("Shared group data limit", DASHBOARD_HTML)
        self.assertIn("/api/groups", DASHBOARD_HTML)
        self.assertIn("/api/group-data-limits", DASHBOARD_HTML)
        self.assertIn("Start with saved settings", DASHBOARD_HTML)
        self.assertNotIn("Apply current configuration", DASHBOARD_HTML)
        self.assertIn("Reset network", DASHBOARD_HTML)
        self.assertIn('id="check-admin-token"', DASHBOARD_HTML)
        self.assertIn("Check token", DASHBOARD_HTML)
        self.assertIn("Checking the admin token...", DASHBOARD_HTML)
        self.assertIn("setButtonBusy(event.submitter, true)", DASHBOARD_HTML)
        self.assertIn("if (submitButton) setButtonBusy(submitButton, false);", DASHBOARD_HTML)
        self.assertIn("Dashboard refresh failed:", DASHBOARD_HTML)
        self.assertNotIn('element("admin-token").addEventListener("change"', DASHBOARD_HTML)
        self.assertIn(
            'element("check-admin-token").addEventListener("click", checkAdminToken)',
            DASHBOARD_HTML,
        )
        self.assertIn('id="toast" class="toast" role="status"', DASHBOARD_HTML)
        self.assertIn(
            'element("toast-dismiss").addEventListener("click", dismissToast)',
            DASHBOARD_HTML,
        )
        self.assertIn('document.addEventListener("submit"', DASHBOARD_HTML)
        self.assertIn("Connected clients", DASHBOARD_HTML)
        self.assertIn("connected-devices", DASHBOARD_HTML)
        self.assertIn('id="traffic-rate-form"', DASHBOARD_HTML)
        self.assertIn('id="traffic-isp-download"', DASHBOARD_HTML)
        self.assertIn('id="traffic-isp-upload"', DASHBOARD_HTML)
        self.assertIn('request("/api/traffic-settings", "POST"', DASHBOARD_HTML)
        self.assertIn("Default queue download limit", DASHBOARD_HTML)
        self.assertIn("Edit name and type", DASHBOARD_HTML)
        self.assertIn("/api/connected-device-labels", DASHBOARD_HTML)
        self.assertIn("Saved labels for devices not listed above", DASHBOARD_HTML)
        self.assertIn('id="traffic-chart"', DASHBOARD_HTML)
        self.assertIn('id="traffic-device"', DASHBOARD_HTML)
        self.assertIn("Last 60 seconds", DASHBOARD_HTML)
        self.assertIn("TRAFFIC_POLL_INTERVAL_MS = 2000", DASHBOARD_HTML)
        self.assertIn("TRAFFIC_MAX_POINTS = 30", DASHBOARD_HTML)
        self.assertIn('document.addEventListener("visibilitychange"', DASHBOARD_HTML)
        self.assertIn('request("/api/traffic", "GET")', DASHBOARD_HTML)
        self.assertIn("before its queues", DASHBOARD_HTML)
        self.assertIn("Upstream router connection", DASHBOARD_HTML)
        self.assertIn("/api/uplink", DASHBOARD_HTML)
        self.assertIn("Open router Wi-Fi settings", DASHBOARD_HTML)
        self.assertIn("Copy router Wi-Fi to Pi", DASHBOARD_HTML)
        self.assertIn("Turn off router Wi-Fi", DASHBOARD_HTML)
        self.assertIn("router-admin-password", DASHBOARD_HTML)
        self.assertIn("/api/router/sync-ap", DASHBOARD_HTML)
        self.assertIn("/api/router/wifi", DASHBOARD_HTML)
        self.assertIn("response.result || response", DASHBOARD_HTML)
        self.assertIn("Manual Pi access-point fallback", DASHBOARD_HTML)
        self.assertIn("Apply profile", DASHBOARD_HTML)
        self.assertIn("mode-badge", DASHBOARD_HTML)
        self.assertIn("action-message", DASHBOARD_HTML)
        self.assertIn("Runtime state", DASHBOARD_HTML)
        self.assertIn("Traffic discovery", DASHBOARD_HTML)
        self.assertIn("Run a profile timer", DASHBOARD_HTML)
        self.assertIn("Duration (minutes)", DASHBOARD_HTML)
        self.assertIn("/api/timers", DASHBOARD_HTML)
        self.assertIn("Progression mode", DASHBOARD_HTML)
        self.assertIn("After device data", DASHBOARD_HTML)
        self.assertIn("/api/progressions", DASHBOARD_HTML)
        self.assertIn("/api/observations", DASHBOARD_HTML)
        self.assertIn("Scan now", DASHBOARD_HTML)
        self.assertIn("Device data limit", DASHBOARD_HTML)
        self.assertIn("Service data limit", DASHBOARD_HTML)
        self.assertIn("Service time limit", DASHBOARD_HTML)
        self.assertIn("Quota weight", DASHBOARD_HTML)
        self.assertIn("service-quota-domains", DASHBOARD_HTML)
        self.assertIn("time-quota-domains", DASHBOARD_HTML)
        self.assertIn("/api/data-limits", DASHBOARD_HTML)
        self.assertIn("/api/service-quotas", DASHBOARD_HTML)
        self.assertIn("/api/time-quotas", DASHBOARD_HTML)
        self.assertIn("Start timer", DASHBOARD_HTML)
        self.assertIn("Select all names used by the service", DASHBOARD_HTML)
        self.assertIn('data-day="0"', DASHBOARD_HTML)
        self.assertIn('data-day="6"', DASHBOARD_HTML)
        self.assertIn("day-toggle", DASHBOARD_HTML)
        self.assertIn("help-icon", DASHBOARD_HTML)
        self.assertIn("A 200 ms value with 30% variation", DASHBOARD_HTML)

    def test_state_endpoint_reports_desired_version_and_observed_state(self) -> None:
        status, content_type, body = self.server.response_for("/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body)["state"],
            {
                "configuration_version": 2,
                "last_action": None,
                "last_error": None,
                "last_result": None,
                "observed_state": "unknown",
                "running": False,
            },
        )

    def _runtime_with_scheduler(self):
        from jittersim.engine.runtime import RuntimeScheduler

        helper = FakeHelper()
        self.server.helper_client = helper
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        self.server.scheduler = scheduler
        self.addCleanup(scheduler.stop)
        return scheduler

    def test_state_reports_running_only_while_the_scheduler_is_active(self) -> None:
        def running():
            _, _, body = self.server.response_for("/api/state")
            return json.loads(body)["state"]["running"]

        self.assertFalse(running())

        scheduler = self._runtime_with_scheduler()
        self.assertFalse(running())

        status, _, _ = self.server.write_response_for("/api/apply", {})
        self.assertEqual(status, 200)
        self.assertTrue(running())

        scheduler.pause()
        self.assertFalse(running())

    def test_reset_stops_the_running_flag(self) -> None:
        self._runtime_with_scheduler()
        self.server.write_response_for("/api/apply", {})
        _, _, body = self.server.response_for("/api/state")
        self.assertTrue(json.loads(body)["state"]["running"])

        status, _, body = self.server.write_response_for("/api/reset", {})

        self.assertEqual(status, 200)
        _, _, state_body = self.server.response_for("/api/state")
        self.assertFalse(json.loads(state_body)["state"]["running"])

    def test_apply_and_reset_responses_carry_the_running_flag(self) -> None:
        self._runtime_with_scheduler()

        _, _, apply_body = self.server.write_response_for("/api/apply", {})
        _, _, reset_body = self.server.write_response_for("/api/reset", {})

        self.assertIs(json.loads(apply_body)["state"]["running"], True)
        self.assertIs(json.loads(reset_body)["state"]["running"], False)

    def test_apply_reports_what_it_sent(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body)["applied"],
            {
                "devices": 1,
                "groups": 0,
                "service_lists": 0,
                "protected": 0,
                "general_profile": None,
            },
        )

    def test_apply_counts_groups_protected_devices_and_service_lists(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_device(
            Device(
                id="laptop",
                name="Laptop",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address="dc:1e:d5:3e:97:4c",
            )
        )
        self.repository.save_device_group(DeviceGroup("family", "Family", ("laptop",), "slow"))
        self.repository.save_device_group(DeviceGroup("others", "Others", ("alice",), "slow"))
        self.repository.save_service_list(
            ServiceList("video", "Video", "slow", hostnames=("video.example.com",))
        )
        self.repository.save_service_list(
            ServiceList("paused", "Paused", "slow", hostnames=("paused.example.com",), enabled=False)
        )
        self.protect("02:00:00:00:00:01")

        status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 200, body)
        # The unverified device alice is left out of the plan so her group is not counted.
        self.assertEqual(
            json.loads(body)["applied"],
            {
                "devices": 1,
                "groups": 1,
                "service_lists": 1,
                "protected": 1,
                "general_profile": None,
            },
        )

    def test_apply_names_the_general_profile_instead_of_counting_devices(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice",), "slow"))
        self.repository.save_general_profile("slow")
        self.protect()

        status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 200, body)
        self.assertEqual(
            json.loads(body)["applied"],
            {
                "devices": 0,
                "groups": 0,
                "service_lists": 0,
                "protected": 1,
                "general_profile": "Slow",
            },
        )

    def test_diagnostics_runtime_carries_the_running_flag(self) -> None:
        self.server.helper_client = FakeHelper()
        _, _, body = self.server.response_for("/api/diagnostics")
        self.assertFalse(json.loads(body)["runtime"]["running"])

        self._runtime_with_scheduler()
        self.server.write_response_for("/api/apply", {})
        _, _, body = self.server.response_for("/api/diagnostics")
        self.assertTrue(json.loads(body)["runtime"]["running"])

    def test_unknown_paths_return_json_not_found(self) -> None:
        status, content_type, body = self.server.response_for("/missing")

        self.assertEqual(status, 404)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body), {"error": "not found"})

    def test_persists_valid_profile_and_device_writes(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/profiles",
            {
                "id": "normal",
                "name": "Normal",
                "upload": {},
                "download": {},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["profile"]["id"], "normal")

        status, _, body = self.server.write_response_for(
            "/api/devices",
            {
                "id": "bob",
                "name": "Bob's phone",
                "ipv4_address": "192.168.50.11",
                "profile_id": "normal",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["device"]["id"], "bob")
        self.assertIsNotNone(self.repository.get_device("bob"))

    def test_persists_device_groups_and_shared_quotas(self) -> None:
        self.repository.save_device(
            Device(
                id="bob",
                name="Bob's phone",
                ipv4_address="192.168.50.11",
                profile_id="slow",
            )
        )
        status, _, body = self.server.write_response_for(
            "/api/groups",
            {
                "id": "family",
                "name": "Family devices",
                "device_ids": ["alice", "bob"],
                "profile_id": "slow",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["group"]["device_ids"], ["alice", "bob"])

        status, _, body = self.server.write_response_for(
            "/api/group-data-limits",
            {
                "group_id": "family",
                "profile_id": "slow",
                "limit_bytes": 5_000,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body)["group_data_limit"]["group_name"],
            "Family devices",
        )

        status, _, body = self.server.response_for("/api/groups")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["groups"][0]["profile_id"], "slow")
        self.assertEqual(
            self.repository.get_device_group("family").device_ids,
            ("alice", "bob"),
        )

    def test_persists_schedules_and_network_policies(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/schedules",
            {
                "id": "work-hours",
                "device_id": "alice",
                "profile_id": "slow",
                "days_of_week": [0, 1, 2, 3, 4],
                "start_minute": 540,
                "end_minute": 1020,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["schedule"]["id"], "work-hours")

        status, _, body = self.server.write_response_for(
            "/api/policies",
            {
                "mac": {
                    "mode": "denylist",
                    "allowlist": [],
                    "denylist": ["aa:bb:cc:dd:ee:ff"],
                },
                "websites": {
                    "mode": "allow_only",
                    "allowed_domains": ["example.com"],
                    "blocked_domains": ["ads.example.com"],
                },
            },
        )
        self.assertEqual(status, 200)
        policies = json.loads(body)
        self.assertEqual(policies["mac"]["mode"], "denylist")
        self.assertEqual(policies["websites"]["blocked_domains"], ["ads.example.com"])

    def test_starts_and_cancels_a_profile_timer(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/timers",
            {
                "id": "ten-minutes",
                "device_id": "alice",
                "profile_id": "slow",
                "duration_minutes": 10,
            },
        )

        self.assertEqual(status, 200)
        timer = json.loads(body)["timer"]
        self.assertEqual(timer["id"], "ten-minutes")
        self.assertEqual(timer["expires_at"] - timer["started_at"], 600)
        self.assertFalse(json.loads(body)["live_applied"])

        status, _, body = self.server.response_for("/api/timers")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["timers"][0]["profile_name"], "Slow")

        status, _, body = self.server.write_response_for(
            "/api/timers/cancel", {"id": "ten-minutes"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["timer_id"], "ten-minutes")
        self.assertEqual(json.loads(self.server.response_for("/api/timers")[2]), {"timers": []})

    def test_rejects_timer_duration_outside_the_supported_range(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/timers",
            {
                "device_id": "alice",
                "profile_id": "slow",
                "duration_minutes": 0,
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid timer configuration"})

    def test_starts_and_stops_a_profile_progression(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/progressions",
            {
                "id": "five-hours",
                "device_id": "alice",
                "start_profile_id": "slow",
                "end_profile_id": "slow",
                "mode": "time",
                "duration_minutes": 300,
                "website_domains": ["youtube.com"],
            },
        )

        self.assertEqual(status, 200)
        progression = json.loads(body)["progression"]
        self.assertEqual(progression["id"], "five-hours")
        self.assertEqual(progression["duration_seconds"], 18_000)
        self.assertEqual(progression["website_domains"], ["youtube.com"])
        self.assertFalse(json.loads(body)["live_applied"])

        status, _, body = self.server.response_for("/api/progressions")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["progressions"][0]["progress_percent"], 0)

        status, _, body = self.server.write_response_for(
            "/api/progressions/stop", {"id": "five-hours"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["progression_id"], "five-hours")
        self.assertEqual(
            json.loads(self.server.response_for("/api/progressions")[2]),
            {"progressions": []},
        )

    def test_rejects_traffic_progression_without_a_query_target(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/progressions",
            {
                "device_id": "alice",
                "start_profile_id": "slow",
                "end_profile_id": "slow",
                "mode": "traffic",
                "duration_minutes": 30,
                "traffic_target_queries": 0,
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid progression configuration"})

    def test_starts_a_byte_progression(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/progressions",
            {
                "device_id": "alice",
                "start_profile_id": "slow",
                "end_profile_id": "slow",
                "mode": "bytes",
                "duration_minutes": 30,
                "traffic_target_bytes": 10_000_000,
            },
        )

        self.assertEqual(status, 200)
        progression = json.loads(body)["progression"]
        self.assertEqual(progression["mode"], "bytes")
        self.assertEqual(progression["traffic_target_bytes"], 10_000_000)

    def test_updates_the_local_test_access_point_without_returning_password(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for(
            "/api/access-point",
            {"ssid": "JitterSim-Test", "password": "test-password-123"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"]["action"], "configure-ap")
        self.assertNotIn("test-password-123", body.decode("utf-8"))

    def test_can_promote_an_observed_domain_to_a_profile_or_global_block(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domain": "Chat.OpenAI.com."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.repository.get_profile("slow").website_domains,
            ("chat.openai.com",),
        )

        status, _, body = self.server.write_response_for(
            "/api/observations/add-global-block",
            {"domain": "Ads.Example.com."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.repository.get_website_policy().blocked_domains,
            ("ads.example.com",),
        )

    def test_remove_global_block_removes_one_exact_name(self) -> None:
        from dataclasses import replace

        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(
            replace(policy, blocked_domains=("facebook.com", "example.org"))
        )

        status, _, body = self.server.write_response_for(
            "/api/observations/remove-global-block", {"domain": "Facebook.com."}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ("example.org",))
        self.assertEqual(
            json.loads(body)["policies"]["websites"]["blocked_domains"], ["example.org"]
        )

    def test_remove_global_block_keeps_the_mode_and_the_allowed_list(self) -> None:
        from jittersim.config.models import WebsitePolicy

        self.repository.save_website_policy(
            WebsitePolicy(
                mode="allow_only",
                allowed_domains=("ok.example",),
                blocked_domains=("x.example", "y.example"),
            )
        )

        self.server.write_response_for(
            "/api/observations/remove-global-block", {"domain": "x.example"}
        )

        policy = self.repository.get_website_policy()
        self.assertEqual(policy.mode, "allow_only")
        self.assertEqual(policy.allowed_domains, ("ok.example",))
        self.assertEqual(policy.blocked_domains, ("y.example",))

    def test_remove_global_block_leaves_a_subdomain_entry_alone(self) -> None:
        from dataclasses import replace

        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(
            replace(policy, blocked_domains=("facebook.com", "www.facebook.com"))
        )

        self.server.write_response_for(
            "/api/observations/remove-global-block", {"domain": "facebook.com"}
        )

        self.assertEqual(
            self.repository.get_website_policy().blocked_domains, ("www.facebook.com",)
        )

    def test_remove_global_block_for_an_unlisted_name_changes_nothing(self) -> None:
        version = self.repository.get_runtime_state().configuration_version

        status, _, _ = self.server.write_response_for(
            "/api/observations/remove-global-block", {"domain": "never-listed.example"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ())
        self.assertEqual(self.repository.get_runtime_state().configuration_version, version)

    def test_remove_global_block_rejects_a_bad_domain(self) -> None:
        for payload in ({}, {"domain": 5}, {"domain": "not a domain"}):
            status, _, body = self.server.write_response_for(
                "/api/observations/remove-global-block", payload
            )
            self.assertEqual(status, 400)
            self.assertEqual(
                json.loads(body)["error"], "could not remove domain from global block list"
            )

    def test_remove_global_block_needs_the_admin_token(self) -> None:
        from jittersim.api.server import WRITE_PATHS

        self.assertIn("/api/observations/remove-global-block", WRITE_PATHS)

    def test_block_many_names_in_one_call(self) -> None:
        status, _, _ = self.server.write_response_for(
            "/api/observations/add-global-block",
            {
                "domains": [
                    "sso.immobilienscout24.de",
                    "Tracking.immobilienscout24.de",
                    "sso.immobilienscout24.de",
                ]
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.repository.get_website_policy().blocked_domains,
            ("sso.immobilienscout24.de", "tracking.immobilienscout24.de"),
        )

    def test_block_many_names_keeps_the_existing_list_and_saves_once(self) -> None:
        from dataclasses import replace

        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(replace(policy, blocked_domains=("old.example.com",)))
        saves = []
        original = self.repository.save_website_policy
        self.repository.save_website_policy = lambda value: (saves.append(value), original(value))

        status, _, body = self.server.write_response_for(
            "/api/observations/add-global-block",
            {"domains": ["a.example.com", "old.example.com", "b.example.com"]},
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(saves), 1)
        self.assertEqual(
            self.repository.get_website_policy().blocked_domains,
            ("old.example.com", "a.example.com", "b.example.com"),
        )
        self.assertEqual(
            json.loads(body)["policies"]["websites"]["blocked_domains"],
            ["old.example.com", "a.example.com", "b.example.com"],
        )

    def test_one_bad_name_rejects_the_whole_batch(self) -> None:
        for path, text in (
            ("/api/observations/add-global-block", "could not add domain to global block list"),
            (
                "/api/observations/remove-global-block",
                "could not remove domain from global block list",
            ),
        ):
            status, _, body = self.server.write_response_for(
                path, {"domains": ["fine.example.com", "bad name"]}
            )
            self.assertEqual(status, 400, path)
            self.assertEqual(json.loads(body)["error"], text)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ())

        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domains": ["fine.example.com", 5]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "could not add domain to profile")
        self.assertEqual(self.repository.get_profile("slow").website_domains, ())

    def test_one_bad_name_leaves_the_block_list_as_it_was(self) -> None:
        from dataclasses import replace

        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(replace(policy, blocked_domains=("a.example.com",)))

        status, _, _ = self.server.write_response_for(
            "/api/observations/remove-global-block", {"domains": ["a.example.com", "bad name"]}
        )

        self.assertEqual(status, 400)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ("a.example.com",))

    def test_batch_bounds_and_exclusivity(self) -> None:
        many = ["h%d.example.com" % i for i in range(65)]
        payloads = (
            {"domains": many},
            {"domains": []},
            {"domains": "x.example.com"},
            {"domains": None},
            {"domain": "a.example.com", "domains": ["b.example.com"]},
            {},
        )
        for payload in payloads:
            status, _, _ = self.server.write_response_for(
                "/api/observations/add-global-block", payload
            )
            self.assertEqual(status, 400, payload)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ())
        sixty_four = ["h%d.example.com" % i for i in range(64)]
        status, _, _ = self.server.write_response_for(
            "/api/observations/add-global-block", {"domains": sixty_four}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(self.repository.get_website_policy().blocked_domains), 64)

    def test_the_bound_counts_names_before_duplicates_collapse(self) -> None:
        status, _, _ = self.server.write_response_for(
            "/api/observations/add-global-block", {"domains": ["same.example.com"] * 65}
        )
        self.assertEqual(status, 400)

    def test_unblock_many_and_add_many_to_a_profile(self) -> None:
        from dataclasses import replace

        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(
            replace(policy, blocked_domains=("a.example.com", "b.example.com", "c.example.com"))
        )
        status, _, _ = self.server.write_response_for(
            "/api/observations/remove-global-block",
            {"domains": ["a.example.com", "c.example.com"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ("b.example.com",))

        status, _, _ = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domains": ["x.example.com", "y.example.com"]},
        )
        self.assertEqual(status, 200)
        profile = self.repository.get_profile("slow")
        self.assertEqual(profile.website_mode, "allow_only")
        self.assertEqual(profile.website_domains[-2:], ("x.example.com", "y.example.com"))

    def test_adding_many_names_to_a_profile_keeps_what_it_has_and_skips_duplicates(self) -> None:
        self.server.write_response_for(
            "/api/observations/add-to-profile", {"profile_id": "slow", "domain": "one.example.com"}
        )

        status, _, _ = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domains": ["Two.example.com", "one.example.com"]},
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            self.repository.get_profile("slow").website_domains,
            ("one.example.com", "two.example.com"),
        )

    def test_adding_many_names_to_a_missing_profile_is_rejected(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "nope", "domains": ["x.example.com"]},
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "could not add domain to profile")

    def test_unblocking_names_that_are_not_listed_changes_nothing(self) -> None:
        version = self.repository.get_runtime_state().configuration_version

        status, _, _ = self.server.write_response_for(
            "/api/observations/remove-global-block",
            {"domains": ["never.example.com", "listed.example.com"]},
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.get_runtime_state().configuration_version, version)

    def _fill_block_list(self, count: int) -> None:
        from dataclasses import replace

        policy = self.repository.get_website_policy()
        names = tuple("listed%d.example.com" % i for i in range(count))
        self.repository.save_website_policy(replace(policy, blocked_domains=names))

    def test_a_full_block_list_says_so_and_stays_as_it_was(self) -> None:
        self._fill_block_list(98)

        status, _, body = self.server.write_response_for(
            "/api/observations/add-global-block",
            {"domains": ["new1.example.com", "new2.example.com", "new3.example.com"]},
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(body)["error"], "The global block list can hold 100 names. Remove some first."
        )
        self.assertEqual(len(self.repository.get_website_policy().blocked_domains), 98)

    def test_the_block_list_can_be_filled_to_exactly_the_cap(self) -> None:
        self._fill_block_list(98)

        status, _, _ = self.server.write_response_for(
            "/api/observations/add-global-block",
            {"domains": ["new1.example.com", "new2.example.com"]},
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(self.repository.get_website_policy().blocked_domains), 100)

    def test_names_already_on_a_full_block_list_do_not_count_again(self) -> None:
        self._fill_block_list(100)

        status, _, _ = self.server.write_response_for(
            "/api/observations/add-global-block",
            {"domains": ["listed0.example.com", "listed99.example.com"]},
        )

        self.assertEqual(status, 200)

    def test_one_more_name_on_a_full_block_list_gets_the_same_message(self) -> None:
        self._fill_block_list(100)

        status, _, body = self.server.write_response_for(
            "/api/observations/add-global-block", {"domain": "one-too-many.example.com"}
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(body)["error"], "The global block list can hold 100 names. Remove some first."
        )

    def test_a_full_block_list_can_still_be_shortened(self) -> None:
        self._fill_block_list(100)

        status, _, _ = self.server.write_response_for(
            "/api/observations/remove-global-block", {"domains": ["listed0.example.com"]}
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(self.repository.get_website_policy().blocked_domains), 99)

    def _fill_profile(self, count: int) -> None:
        from dataclasses import replace

        profile = self.repository.get_profile("slow")
        names = tuple("allowed%d.example.com" % i for i in range(count))
        self.repository.save_profile(replace(profile, website_mode="allow_only", website_domains=names))

    def test_a_full_profile_list_says_so_and_stays_as_it_was(self) -> None:
        self._fill_profile(99)

        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domains": ["new1.example.com", "new2.example.com"]},
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(body)["error"], "A profile can list 100 websites. Remove some first."
        )
        self.assertEqual(len(self.repository.get_profile("slow").website_domains), 99)

    def test_a_profile_list_can_be_filled_to_exactly_the_cap(self) -> None:
        self._fill_profile(99)

        status, _, _ = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domains": ["new1.example.com", "allowed0.example.com"]},
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(self.repository.get_profile("slow").website_domains), 100)

    def test_one_more_name_for_a_full_profile_gets_the_same_message(self) -> None:
        self._fill_profile(100)

        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domain": "one-too-many.example.com"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(body)["error"], "A profile can list 100 websites. Remove some first."
        )

    def _scheduled_server(self):
        helper = PlanRecordingHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        self.server.helper_client = helper
        self.server.scheduler = scheduler
        return helper, scheduler

    def test_a_block_list_change_before_the_first_apply_is_not_applied_by_the_scheduler(
        self,
    ) -> None:
        # The success message tells the user to start again after Reset or Panic. The scheduler
        # is paused then and a saved change waits for the Start with saved settings button.
        helper, scheduler = self._scheduled_server()

        self.server.write_response_for(
            "/api/observations/add-global-block", {"domain": "ads.example.com"}
        )

        self.assertFalse(scheduler.tick(repository=self.repository))
        self.assertEqual(helper.plans, [])

    def test_a_block_list_change_after_a_reset_is_not_applied_by_the_scheduler(self) -> None:
        helper, scheduler = self._scheduled_server()
        self.server.write_response_for("/api/apply", {})
        self.server.write_response_for("/api/reset", {})
        applied = len(helper.plans)

        self.server.write_response_for(
            "/api/observations/add-global-block", {"domain": "ads.example.com"}
        )

        self.assertFalse(scheduler.tick(repository=self.repository))
        self.assertEqual(len(helper.plans), applied)

    def test_an_active_scheduler_applies_a_removed_block_on_its_next_tick(self) -> None:
        # The success message says a change applies on its own while JitterSim is running.
        helper, scheduler = self._scheduled_server()
        blocked = IPv4Address("203.0.113.9")
        kept = IPv4Address("203.0.113.10")
        lookups = {"ads.example.com": (blocked,), "other.example.com": (kept,)}
        for domain in lookups:
            self.server.write_response_for(
                "/api/observations/add-global-block", {"domain": domain}
            )

        with patch(
            "jittersim.engine.website._cached_addresses",
            side_effect=lambda *, domain: lookups.get(domain, ()),
        ):
            status, _, _ = self.server.write_response_for("/api/apply", {})
            self.assertEqual(status, 200)
            applied = len(helper.plans)
            self.assertCountEqual(helper.plans[-1].global_blocked_addresses, (blocked, kept))

            self.server.write_response_for(
                "/api/observations/remove-global-block", {"domain": "ads.example.com"}
            )
            # The endpoint only saves. It never calls the helper itself.
            self.assertEqual(len(helper.plans), applied)

            self.assertTrue(scheduler.tick(repository=self.repository))

        self.assertEqual(len(helper.plans), applied + 1)
        self.assertEqual(helper.plans[-1].global_blocked_addresses, (kept,))

    def test_the_global_block_endpoints_never_call_the_helper(self) -> None:
        helper = PlanRecordingHelper()
        self.server.helper_client = helper

        self.server.write_response_for(
            "/api/observations/add-global-block", {"domain": "ads.example.com"}
        )
        self.server.write_response_for(
            "/api/observations/remove-global-block", {"domain": "ads.example.com"}
        )

        self.assertEqual(helper.plans, [])

    def test_rejects_invalid_profile_writes_before_persistence(self) -> None:
        status, content_type, body = self.server.write_response_for(
            "/api/profiles",
            {"id": "unsafe id", "name": "Invalid"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body), {"error": "invalid profile configuration"})
        self.assertIsNone(self.repository.get_profile("unsafe id"))

    def test_apply_and_reset_require_a_configured_helper(self) -> None:
        apply_status, _, apply_body = self.server.write_response_for("/api/apply", {})
        reset_status, _, reset_body = self.server.write_response_for("/api/reset", {})

        self.assertEqual(apply_status, 503)
        self.assertEqual(reset_status, 503)
        self.assertEqual(json.loads(apply_body)["error"], "network helper is not configured")
        self.assertEqual(json.loads(reset_body)["error"], "network helper is not configured")

    def test_apply_and_reset_update_runtime_state(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper

        apply_status, _, apply_body = self.server.write_response_for("/api/apply", {})
        apply_response = json.loads(apply_body)
        self.assertEqual(apply_status, 200)
        self.assertIsNotNone(helper.applied_plan)
        self.assertEqual(apply_response["state"]["observed_state"], "applied")
        self.assertEqual(apply_response["state"]["last_result"], "success")

        reset_status, _, reset_body = self.server.write_response_for("/api/reset", {})
        reset_response = json.loads(reset_body)
        self.assertEqual(reset_status, 200)
        self.assertTrue(helper.reset_called)
        self.assertEqual(reset_response["state"]["observed_state"], "reset")

    def test_helper_failure_is_recorded_without_exposing_internal_details(self) -> None:
        self.server.helper_client = FakeHelper(fail_action="apply")

        status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"], "network helper apply failed")
        self.assertIn("journalctl -u jittersim-helper.service", json.loads(body)["hint"])
        state = self.repository.get_runtime_state()
        self.assertEqual(state.observed_state, "error")
        self.assertEqual(state.last_error, "network helper apply failed")

    def test_state_changes_require_a_matching_bearer_token(self) -> None:
        self.assertFalse(self.server.is_authorized("Bearer anything"))
        token_server = ControlServer(
            ("127.0.0.1", 0),
            self.repository,
            admin_token="test-token-123456",
        )
        try:
            self.assertFalse(token_server.is_authorized(None))
            self.assertFalse(token_server.is_authorized("Basic test-token-123456"))
            self.assertFalse(token_server.is_authorized("Bearer wrong-token"))
            self.assertTrue(token_server.is_authorized("Bearer test-token-123456"))
        finally:
            token_server.server_close()

    def test_devices_endpoint_adds_a_display_name(self) -> None:
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client 192.168.50.178",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address="dc:1e:d5:3e:97:4c",
            )
        )
        self.repository.save_device(
            Device(
                id="kitchen",
                name="Kitchen tablet",
                ipv4_address="192.168.50.179",
                profile_id="slow",
            )
        )

        _, _, body = self.server.response_for("/api/devices")
        by_id = {item["id"]: item for item in json.loads(body)["devices"]}

        self.assertEqual(by_id["client-dc1ed53e974c"]["display_name"], "Unknown device")
        self.assertEqual(by_id["client-dc1ed53e974c"]["name"], "Client 192.168.50.178")
        self.assertEqual(by_id["kitchen"]["display_name"], "Kitchen tablet")

        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c", display_name="Sam MacBook", device_type="laptop"
        )
        _, _, body = self.server.response_for("/api/devices")
        by_id = {item["id"]: item for item in json.loads(body)["devices"]}
        self.assertEqual(by_id["client-dc1ed53e974c"]["display_name"], "Sam MacBook")

    def test_device_display_name_helper(self) -> None:
        from jittersim.api.server import _device_display_name

        device = Device(
            id="client-dc1ed53e974c",
            name="Client 192.168.50.178",
            ipv4_address="192.168.50.178",
            profile_id="slow",
            mac_address="dc:1e:d5:3e:97:4c",
        )
        labels = {"dc:1e:d5:3e:97:4c": {"display_name": "Sam MacBook", "device_type": "laptop"}}

        self.assertEqual(_device_display_name(device, labels), "Sam MacBook")
        self.assertEqual(_device_display_name(device, {}), "Unknown device")
        self.assertEqual(_device_display_name(None, labels), "Unknown device")

    def test_payloads_use_the_resolved_device_name(self) -> None:
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client 192.168.50.178",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address="dc:1e:d5:3e:97:4c",
            )
        )
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c", display_name="Sam MacBook", device_type="laptop"
        )
        self.server.write_response_for(
            "/api/data-limits",
            {
                "device_id": "client-dc1ed53e974c",
                "profile_id": "slow",
                "limit_bytes": 1_000,
                "reset_mode": "manual",
                "reset_interval_seconds": 0,
                "reset_times": [],
                "enabled": True,
            },
        )
        self.server.write_response_for(
            "/api/timers",
            {
                "id": "ten-minutes",
                "device_id": "client-dc1ed53e974c",
                "profile_id": "slow",
                "duration_minutes": 10,
            },
        )

        _, _, body = self.server.response_for("/api/data-limits")
        self.assertEqual(json.loads(body)["data_limits"][0]["device_name"], "Sam MacBook")
        _, _, body = self.server.response_for("/api/timers")
        self.assertEqual(json.loads(body)["timers"][0]["device_name"], "Sam MacBook")

        self.repository.delete_connected_device_label(mac_address="dc:1e:d5:3e:97:4c")
        _, _, body = self.server.response_for("/api/data-limits")
        self.assertEqual(
            json.loads(body)["data_limits"][0]["device_name"], "Unknown device (192.168.50.178)"
        )

    def test_observations_name_a_client_by_its_hostname(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})

        _, _, body = self.server.response_for("/api/observations")
        group = json.loads(body)["devices"][0]

        self.assertEqual(group["ipv4_address"], "192.168.50.178")
        self.assertEqual(group["display_name"], "MacBook-Pro")
        self.assertEqual(group["hostname"], "MacBook-Pro")

    def test_observations_prefer_a_saved_label(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c", display_name="Sam MacBook", device_type="laptop"
        )

        _, _, body = self.server.response_for("/api/observations")

        self.assertEqual(json.loads(body)["devices"][0]["display_name"], "Sam MacBook")

    def test_observations_carry_a_label_and_a_seen_with_hint(self) -> None:
        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.124", "mmx-ds.cdn.whatsapp.net", 5, 100, 100),
                DomainObservation("192.168.50.124", "a1051.dscg4.akamai.net", 1, 100, 100),
                DomainObservation("192.168.50.124", "cdn.somesmallsite.io", 1, 5000, 5000),
            ],
            now=100,
        )

        _, _, body = self.server.response_for("/api/observations")
        domains = {item["domain"]: item for item in json.loads(body)["devices"][0]["domains"]}

        self.assertEqual(domains["mmx-ds.cdn.whatsapp.net"]["label"]["name"], "WhatsApp")
        self.assertEqual(domains["mmx-ds.cdn.whatsapp.net"]["seen_with"], [])
        self.assertEqual(domains["a1051.dscg4.akamai.net"]["label"]["kind"], "cdn")
        self.assertEqual(domains["a1051.dscg4.akamai.net"]["seen_with"], ["WhatsApp"])
        self.assertEqual(domains["cdn.somesmallsite.io"]["label"]["known"], False)
        self.assertEqual(domains["cdn.somesmallsite.io"]["seen_with"], [])

    def test_observations_carry_a_group(self) -> None:
        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.124", "sso.immobilienscout24.de", 2, 100, 100),
                DomainObservation("192.168.50.124", "tracking.immobilienscout24.de", 2, 100, 100),
                DomainObservation("192.168.50.124", "tags-eu.tiqcdn.com", 2, 100, 100),
            ],
            now=100,
        )

        _, _, body = self.server.response_for("/api/observations")
        domains = {item["domain"]: item for item in json.loads(body)["devices"][0]["domains"]}

        self.assertEqual(
            domains["sso.immobilienscout24.de"]["group"],
            {"key": "label:immoscout24", "name": "ImmoScout24", "kind": "app"},
        )
        self.assertEqual(
            domains["tracking.immobilienscout24.de"]["group"]["key"], "label:immoscout24"
        )
        self.assertEqual(domains["tags-eu.tiqcdn.com"]["group"]["key"], "domain:tiqcdn.com")

    def test_the_group_kind_comes_with_the_observations(self) -> None:
        names = {
            "www.google.com": "system",
            "connectivitycheck.gstatic.com": "system",
            "a1051.dscg4.akamai.net": "cdn",
            "tags-eu.tiqcdn.com": "tracker",
            "10000.mt.eu.iot.dreame.tech": "iot",
            "cdn.somesmallsite.io": "unknown",
            "sso.immobilienscout24.de": "app",
        }
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.124", name, 1, 100, 100) for name in names], now=100
        )

        _, _, body = self.server.response_for("/api/observations")
        domains = {item["domain"]: item for item in json.loads(body)["devices"][0]["domains"]}

        self.assertEqual({name: domains[name]["group"]["kind"] for name in names}, names)

    def test_seen_with_hints_never_cross_devices(self) -> None:
        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.124", "mmx-ds.cdn.whatsapp.net", 5, 100, 100),
                DomainObservation("192.168.50.125", "a1051.dscg4.akamai.net", 1, 100, 100),
            ],
            now=100,
        )

        _, _, body = self.server.response_for("/api/observations")
        devices = {item["ipv4_address"]: item for item in json.loads(body)["devices"]}

        self.assertEqual(devices["192.168.50.125"]["domains"][0]["seen_with"], [])

    def test_observations_flag_globally_blocked_names(self) -> None:
        from dataclasses import replace

        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.124", "facebook.com", 2, 100, 100),
                DomainObservation("192.168.50.124", "www.facebook.com", 1, 100, 100),
            ],
            now=100,
        )
        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(replace(policy, blocked_domains=("facebook.com",)))

        _, _, body = self.server.response_for("/api/observations")
        domains = {item["domain"]: item for item in json.loads(body)["devices"][0]["domains"]}

        self.assertTrue(domains["facebook.com"]["globally_blocked"])
        self.assertFalse(domains["www.facebook.com"]["globally_blocked"])

    def test_globally_blocked_matches_after_normalization(self) -> None:
        from dataclasses import replace

        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.124", "Ads.Example.COM.", 1, 100, 100)], now=100
        )
        policy = self.repository.get_website_policy()
        self.repository.save_website_policy(replace(policy, blocked_domains=("ADS.example.com",)))

        _, _, body = self.server.response_for("/api/observations")
        domain = json.loads(body)["devices"][0]["domains"][0]

        self.assertEqual(domain["domain"], "ads.example.com")
        self.assertTrue(domain["globally_blocked"])

    def test_nothing_is_globally_blocked_without_a_block_list(self) -> None:
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.124", "facebook.com", 1, 100, 100)], now=100
        )

        _, _, body = self.server.response_for("/api/observations")

        self.assertFalse(json.loads(body)["devices"][0]["domains"][0]["globally_blocked"])

    def test_observations_name_the_profile_assigned_to_each_device(self) -> None:
        self.repository.save_domain_observations(
            [
                DomainObservation("192.168.50.10", "facebook.com", 1, 100, 100),
                DomainObservation("192.168.50.124", "facebook.com", 1, 100, 100),
            ],
            now=100,
        )

        _, _, body = self.server.response_for("/api/observations")
        devices = {item["ipv4_address"]: item for item in json.loads(body)["devices"]}

        self.assertEqual(devices["192.168.50.10"]["assigned_device_id"], "alice")
        self.assertEqual(devices["192.168.50.10"]["assigned_profile_id"], "slow")
        self.assertIsNone(devices["192.168.50.124"]["assigned_device_id"])
        self.assertIsNone(devices["192.168.50.124"]["assigned_profile_id"])

    def test_the_scan_response_keeps_its_original_fields(self) -> None:
        self.server.helper_client = FakeHelper()

        _, _, body = self.server.write_response_for("/api/observations/scan", {})

        observation = json.loads(body)["observations"][0]
        self.assertNotIn("label", observation)
        self.assertNotIn("seen_with", observation)
        self.assertNotIn("globally_blocked", observation)
        self.assertNotIn("group", observation)

    def test_observations_work_without_the_helper(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.server.helper_client = None
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client 192.168.50.178",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address="dc:1e:d5:3e:97:4c",
            )
        )

        status, _, body = self.server.response_for("/api/observations")

        self.assertEqual(status, 200)
        group = json.loads(body)["devices"][0]
        self.assertEqual(group["display_name"], "Unknown device")
        self.assertEqual(group["assigned_device_name"], "Client 192.168.50.178")

    def test_observations_survive_a_failing_station_scan(self) -> None:
        class BrokenSnapshot(FakeHelper):
            def connected_device_snapshot(self):
                raise HelperClientError("simulated failure")

        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.server.helper_client = BrokenSnapshot()

        status, _, body = self.server.response_for("/api/observations")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["devices"][0]["display_name"], "Unknown device")

    PROTECTED_MAC = "dc:1e:d5:3e:97:4c"
    OLD_HELPER_MESSAGE = (
        "The networking helper is out of date and cannot skip protected devices. "
        "Restart the JitterSim services or reinstall the release."
    )

    def protect(self, mac=PROTECTED_MAC) -> None:
        self.repository.save_unaffected_device(UnaffectedDevice(mac, "Protected", 1))

    def test_scan_passes_the_protected_macs_to_the_helper(self) -> None:
        seen = []

        class Recording(FakeHelper):
            def observe_dns_queries(self, excluded_macs=()):
                seen.append(list(excluded_macs))
                return super().observe_dns_queries(excluded_macs)

        self.server.helper_client = Recording()
        self.protect()
        self.protect("02:00:00:00:00:01")

        self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(seen, [["02:00:00:00:00:01", self.PROTECTED_MAC]])

    def test_scan_drops_observations_of_a_protected_station(self) -> None:
        self.server.helper_client = FakeHelper()  # its fake ignores the exclusion
        self.protect()

        status, _, body = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["captured_observations"], 0)
        self.assertEqual(json.loads(body)["observations"], [])
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_scan_drops_observations_of_a_protected_saved_device_without_a_station(self) -> None:
        class NoStations(FakeHelper):
            def connected_device_snapshot(self):
                raise HelperClientError("simulated failure")

        self.server.helper_client = NoStations()
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address=self.PROTECTED_MAC,
            )
        )
        self.protect()

        status, _, body = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["captured_observations"], 0)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_scan_keeps_observations_of_ordinary_clients(self) -> None:
        self.server.helper_client = FakeHelper()
        self.protect("02:00:00:00:00:01")

        status, _, body = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["captured_observations"], 1)
        self.assertEqual(len(self.repository.list_domain_observations()), 1)

    def test_scan_fails_closed_with_an_old_helper(self) -> None:
        message = self.OLD_HELPER_MESSAGE

        class OldHelper(FakeHelper):
            def observe_dns_queries(self, excluded_macs=()):
                raise HelperClientError(message)

        self.server.helper_client = OldHelper()
        self.protect()

        status, _, body = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"], message)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_scan_never_captures_through_a_helper_that_cannot_skip_protected_devices(self) -> None:
        for service_class in (OldHelperService, OlderHelperService):
            with self.subTest(service=service_class.__name__):
                with serving(service_class) as (client, executor):
                    self.server.helper_client = client
                    self.protect()

                    status, _, body = self.server.write_response_for(
                        "/api/observations/scan", {}
                    )

                    self.assertEqual(status, 502)
                    self.assertEqual(json.loads(body)["error"], self.OLD_HELPER_MESSAGE)
                    self.assertEqual(captures(executor), [])
                    self.assertEqual(self.repository.list_domain_observations(), ())

    PHONE = "02:00:00:00:00:77"

    def scan_with(self, helper) -> None:
        self.server.helper_client = helper
        status, _, _ = self.server.write_response_for("/api/observations/scan", {})
        self.assertEqual(status, 200)

    def stored_macs(self):
        return {
            (str(item.ipv4_address), item.mac_address)
            for item in self.repository.list_domain_observations()
        }

    def shown_addresses(self):
        _, _, body = self.server.response_for("/api/observations")
        return [item["ipv4_address"] for item in json.loads(body)["devices"]]

    def test_a_scan_records_the_hardware_address_of_each_client(self) -> None:
        self.scan_with(MovingClientHelper(self.PHONE, "192.168.50.99"))

        self.assertEqual(self.stored_macs(), {("192.168.50.99", self.PHONE)})

    def test_a_scan_records_no_hardware_address_for_an_unknown_or_shared_address(self) -> None:
        class Shared(MovingClientHelper):
            def connected_devices(self):
                client = super().connected_devices()[0]
                return [client, {**client, "mac_address": "02:00:00:00:00:78"}]

        class Broken(MovingClientHelper):
            def connected_device_snapshot(self):
                raise HelperClientError("simulated failure")

        for helper in (Shared(self.PHONE, "192.168.50.99"), Broken(self.PHONE, "192.168.50.99")):
            with self.subTest(helper=type(helper).__name__):
                self.repository.delete_domain_observations(["192.168.50.99"])
                self.scan_with(helper)

                self.assertEqual(self.stored_macs(), {("192.168.50.99", None)})

    def test_the_browser_never_receives_hardware_addresses_of_observations(self) -> None:
        self.server.helper_client = MovingClientHelper(self.PHONE, "192.168.50.99")
        _, _, scan = self.server.write_response_for("/api/observations/scan", {})
        _, _, listing = self.server.response_for("/api/observations")

        for body in (scan, listing):
            self.assertNotIn(self.PHONE, body.decode())
        group = json.loads(listing)["devices"][0]
        self.assertNotIn("mac_address", group)
        self.assertNotIn("mac_address", group["domains"][0])
        self.assertNotIn("mac_address", json.loads(scan)["observations"][0])

    def test_protecting_a_client_that_moved_purges_the_rows_under_its_old_address(self) -> None:
        helper = MovingClientHelper(self.PHONE, "192.168.50.99")
        self.scan_with(helper)
        helper.address = "192.168.50.178"  # It now has another address and is not saved.

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PHONE}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_the_rows_of_a_protected_client_that_moved_are_hidden(self) -> None:
        helper = MovingClientHelper(self.PHONE, "192.168.50.99")
        self.scan_with(helper)
        helper.address = "192.168.50.178"
        self.assertEqual(self.shown_addresses(), ["192.168.50.99"])
        self.protect(self.PHONE)  # Saved elsewhere so nothing purged the rows.

        self.assertEqual(self.shown_addresses(), [])

    def test_protecting_an_offline_unsaved_client_purges_and_hides_its_rows(self) -> None:
        helper = MovingClientHelper(self.PHONE, "192.168.50.99")
        self.scan_with(helper)
        helper.online = False
        self.assertEqual(self.shown_addresses(), ["192.168.50.99"])
        self.protect(self.PHONE)
        self.assertEqual(self.shown_addresses(), [])
        self.assertNotEqual(self.repository.list_domain_observations(), ())

        self.repository.delete_unaffected_device(self.PHONE)
        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PHONE}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_rows_without_a_recorded_hardware_address_still_match_by_address(self) -> None:
        self.server.helper_client = MovingClientHelper(self.PHONE, "192.168.50.178")
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.178", "amazon.com", 2, 10, 10)], now=10
        )
        self.assertEqual(self.stored_macs(), {("192.168.50.178", None)})
        self.assertEqual(self.shown_addresses(), ["192.168.50.178"])

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PHONE}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_rows_without_a_hardware_address_are_hidden_by_address_too(self) -> None:
        self.server.helper_client = MovingClientHelper(self.PHONE, "192.168.50.178")
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.178", "amazon.com", 2, 10, 10)], now=10
        )
        self.protect(self.PHONE)

        self.assertEqual(self.shown_addresses(), [])

    UPPERCASE_PHONE = "DC:1E:D5:3E:97:4C"

    def test_a_scan_drops_a_protected_client_that_the_snapshot_names_in_capitals(self) -> None:
        # The saved protection is lower case and the snapshot is not. The row is under an
        # address that is not the one the saved device was assigned.
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client",
                ipv4_address="192.168.50.50",
                profile_id="slow",
                mac_address=self.UPPERCASE_PHONE,
            )
        )
        self.protect(self.UPPERCASE_PHONE)
        helper = MovingClientHelper(self.UPPERCASE_PHONE, "192.168.50.178")

        self.scan_with(helper)

        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_protecting_a_client_that_the_snapshot_names_in_capitals_purges_its_old_rows(
        self,
    ) -> None:
        self.server.helper_client = MovingClientHelper(self.UPPERCASE_PHONE, "192.168.50.178")
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.178", "amazon.com", 2, 10, 10)], now=10
        )
        self.assertEqual(self.stored_macs(), {("192.168.50.178", None)})

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.UPPERCASE_PHONE}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_a_corrupt_stored_observation_gets_the_normal_storage_error_response(self) -> None:
        self.server.admin_token = "admin-token"
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.20", "amazon.com", 2, 10, 10)], now=10
        )
        self.repository._connection.execute(
            "UPDATE domain_observations SET mac_address = 'garbage'"
        )
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.client_address = ("127.0.0.1", 45000)
        handler.path = "/api/observations"
        handler.headers = Message()
        handler.headers["Authorization"] = "Bearer admin-token"
        response = {}
        handler._send_response = lambda **values: response.update(values)

        handler.do_GET()

        self.assertEqual(response["status"], 500)
        self.assertEqual(
            json.loads(response["body"]), {"error": "stored configuration could not be read"}
        )

    def test_a_scan_records_a_capital_snapshot_address_in_its_normal_form(self) -> None:
        self.scan_with(MovingClientHelper(self.UPPERCASE_PHONE, "192.168.50.99"))

        self.assertEqual(self.stored_macs(), {("192.168.50.99", self.UPPERCASE_PHONE.lower())})

    def test_rows_recorded_from_a_capital_snapshot_are_hidden_by_the_normal_form(self) -> None:
        helper = MovingClientHelper(self.UPPERCASE_PHONE, "192.168.50.99")
        self.scan_with(helper)
        helper.address = "192.168.50.178"  # The rows are now under an address it left.
        self.assertEqual(self.shown_addresses(), ["192.168.50.99"])
        self.protect(self.UPPERCASE_PHONE.lower())

        self.assertEqual(self.shown_addresses(), [])

    def test_scan_keeps_the_generic_error_for_other_helper_failures(self) -> None:
        class Failing(FakeHelper):
            def observe_dns_queries(self, excluded_macs=()):
                raise HelperClientError("socket path /run/secret is gone")

        self.server.helper_client = Failing()

        status, _, body = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"], "could not scan traffic observations")

    def test_scan_refuses_when_more_devices_are_protected_than_the_helper_can_skip(self) -> None:
        calls = []

        class Counting(FakeHelper):
            def observe_dns_queries(self, excluded_macs=()):
                calls.append(list(excluded_macs))
                return super().observe_dns_queries(excluded_macs)

        self.server.helper_client = Counting()
        too_many = tuple(
            "02:00:00:00:%02x:%02x" % (index // 256, index % 256) for index in range(257)
        )

        with patch("jittersim.api.server.protected_macs", return_value=too_many):
            status, _, body = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 409)
        self.assertIn("at most 256", json.loads(body)["error"])
        self.assertEqual(calls, [])
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_scan_still_works_with_the_largest_protected_list(self) -> None:
        seen = []

        class Recording(FakeHelper):
            def observe_dns_queries(self, excluded_macs=()):
                seen.append(list(excluded_macs))
                return super().observe_dns_queries(excluded_macs)

        self.server.helper_client = Recording()
        for index in range(256):
            self.protect("02:00:00:00:%02x:%02x" % (index // 256, index % 256))

        status, _, _ = self.server.write_response_for("/api/observations/scan", {})

        self.assertEqual(status, 200)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 256)
        self.assertEqual(seen[0], sorted(seen[0]))

    def test_old_rows_of_a_protected_station_are_hidden(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        _, _, before = self.server.response_for("/api/observations")
        self.assertEqual(len(json.loads(before)["devices"]), 1)
        self.protect()

        _, _, body = self.server.response_for("/api/observations")

        self.assertEqual(json.loads(body)["devices"], [])
        self.assertEqual(json.loads(body)["total_domains"], 0)

    def test_old_rows_of_a_protected_saved_device_are_hidden_without_the_helper(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.server.helper_client = None
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address=self.PROTECTED_MAC,
            )
        )
        self.protect()

        _, _, body = self.server.response_for("/api/observations")

        self.assertEqual(json.loads(body)["devices"], [])

    def test_protecting_a_station_deletes_its_stored_rows(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.assertNotEqual(self.repository.list_domain_observations(), ())

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices",
            {"mac_address": self.PROTECTED_MAC, "display_name": "Mine"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_domain_observations(), ())
        self.assertEqual(self.repository.total_domain_queries("192.168.50.178"), 0)

    def test_protecting_a_saved_device_deletes_its_rows_even_when_it_is_offline(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Client",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address=self.PROTECTED_MAC,
            )
        )
        self.server.helper_client = None

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PROTECTED_MAC}
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_domain_observations(), ())

    def test_protecting_one_device_keeps_the_rows_of_the_others(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.repository.save_domain_observations(
            [DomainObservation("192.168.50.10", "example.com", 1, 10, 10)], now=10
        )

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PROTECTED_MAC}
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            [str(item.ipv4_address) for item in self.repository.list_domain_observations()],
            ["192.168.50.10"],
        )

    def test_a_failed_purge_rolls_the_protection_back(self) -> None:
        # The purge and the protection are one change. If the rows cannot be deleted the
        # device must not look protected while its old rows are still stored.
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})

        def refuse(addresses, macs=()):
            raise StorageError("could not delete domain observations")

        self.repository.delete_domain_observations = refuse

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PROTECTED_MAC}
        )

        self.assertEqual(status, 409)
        self.assertEqual(self.repository.list_unaffected_devices(), ())
        self.assertEqual(self.repository.total_domain_queries("192.168.50.178"), 2)

    def test_a_failed_protection_change_keeps_the_rows_and_the_list(self) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})

        def refuse():
            raise ValueError("simulated plan failure")

        self.server._validate_saved_plan = refuse

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": self.PROTECTED_MAC}
        )

        self.assertEqual(status, 409)
        self.assertEqual(self.repository.list_unaffected_devices(), ())
        self.assertEqual(self.repository.total_domain_queries("192.168.50.178"), 2)

    def test_a_new_assignment_is_named_after_the_station_hostname(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 200, body)
        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "MacBook-Pro")

    def test_a_new_assignment_prefers_the_saved_label(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c", display_name="Sam MacBook", device_type="laptop"
        )

        self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "Sam MacBook")

    def test_without_a_hostname_the_old_placeholder_remains(self) -> None:
        class Anonymous(FakeHelper):
            def connected_devices(self):
                device = super().connected_devices()[0]
                device["hostname"] = None
                return [device]

        self.server.helper_client = Anonymous()

        self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "Client 192.168.50.178")

    def test_an_existing_device_keeps_its_name(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name="Alice phone",
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address="dc:1e:d5:3e:97:4c",
            )
        )

        self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "Alice phone")

    def test_a_hostname_that_is_not_a_usable_name_falls_back_to_the_placeholder(self) -> None:
        class OddHostname(FakeHelper):
            def connected_devices(self):
                device = super().connected_devices()[0]
                device["hostname"] = "x" * 101
                return [device]

        self.server.helper_client = OddHostname()

        status, _, body = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 200, body)
        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "Client 192.168.50.178")

    def test_a_client_added_to_a_group_is_named_after_the_station_hostname(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice",), "slow"))

        status, _, body = self.server.write_response_for(
            "/api/connected-devices/group",
            {
                "group_id": "family",
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
            },
        )

        self.assertEqual(status, 200, body)
        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "MacBook-Pro")
        self.assertIn(saved.id, self.repository.get_device_group("family").device_ids)

    def test_clients_picked_for_a_new_group_are_named_after_the_station_hostname(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for(
            "/api/groups",
            {
                "id": "family",
                "name": "Family",
                "profile_id": "slow",
                "device_ids": [],
                "connected_clients": [
                    {"ipv4_address": "192.168.50.178", "mac_address": "dc:1e:d5:3e:97:4c"}
                ],
            },
        )

        self.assertEqual(status, 200, body)
        saved = self.repository.get_device_by_ipv4_address("192.168.50.178")
        self.assertEqual(saved.name, "MacBook-Pro")

    PROTECTED_MAC_FORMS = (
        "dc:1e:d5:3e:97:4c",
        "DC:1E:D5:3E:97:4C",
        "DC-1E-D5-3E-97-4C",
        "  dc:1e:d5:3e:97:4c  ",
    )

    def _protect_the_fake_client(self) -> None:
        from jittersim.config.exemptions import UnaffectedDevice

        self.repository.save_unaffected_device(UnaffectedDevice("dc:1e:d5:3e:97:4c", "Mine", 1))

    def test_a_protected_client_cannot_join_a_new_group(self) -> None:
        self.server.helper_client = FakeHelper()
        self._protect_the_fake_client()

        for mac_address in self.PROTECTED_MAC_FORMS:
            with self.subTest(mac_address=mac_address):
                status, _, body = self.server.write_response_for(
                    "/api/groups",
                    {
                        "id": "lab",
                        "name": "Lab",
                        "profile_id": "slow",
                        "connected_clients": [
                            {"ipv4_address": "192.168.50.178", "mac_address": mac_address}
                        ],
                    },
                )

                self.assertEqual(status, 409, body)
                self.assertIn("unaffected by profiles", json.loads(body)["error"])
                self.assertIsNone(self.repository.get_device_by_mac_address("dc:1e:d5:3e:97:4c"))
                self.assertIsNone(self.repository.get_device_group("lab"))

    def test_a_protected_client_cannot_be_added_to_an_existing_group(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice",), "slow"))
        self._protect_the_fake_client()

        for mac_address in self.PROTECTED_MAC_FORMS:
            with self.subTest(mac_address=mac_address):
                status, _, body = self.server.write_response_for(
                    "/api/connected-devices/group",
                    {
                        "group_id": "family",
                        "ipv4_address": "192.168.50.178",
                        "mac_address": mac_address,
                    },
                )

                self.assertEqual(status, 409, body)
                self.assertIn("unaffected by profiles", json.loads(body)["error"])
                self.assertIsNone(self.repository.get_device_by_mac_address("dc:1e:d5:3e:97:4c"))
                self.assertEqual(
                    self.repository.get_device_group("family").device_ids, ("alice",)
                )

    def test_a_protected_client_in_a_new_group_saves_nothing_for_the_others(self) -> None:
        class TwoClients(FakeHelper):
            def connected_devices(self):
                ordinary = dict(super().connected_devices()[0])
                ordinary.update(
                    ipv4_address="192.168.50.179", mac_address="aa:bb:cc:dd:ee:02", hostname="Tab"
                )
                return super().connected_devices() + [ordinary]

        self.server.helper_client = TwoClients()
        self._protect_the_fake_client()
        ordinary = {"ipv4_address": "192.168.50.179", "mac_address": "aa:bb:cc:dd:ee:02"}
        protected = {"ipv4_address": "192.168.50.178", "mac_address": "dc:1e:d5:3e:97:4c"}

        for clients in ([ordinary, protected], [protected, ordinary]):
            with self.subTest(first=clients[0]["mac_address"]):
                status, _, body = self.server.write_response_for(
                    "/api/groups",
                    {
                        "id": "lab",
                        "name": "Lab",
                        "profile_id": "slow",
                        "connected_clients": clients,
                    },
                )

                self.assertEqual(status, 409, body)
                self.assertIn("unaffected by profiles", json.loads(body)["error"])
                self.assertIsNone(self.repository.get_device_by_mac_address("aa:bb:cc:dd:ee:02"))
                self.assertIsNone(self.repository.get_device_by_mac_address("dc:1e:d5:3e:97:4c"))
                self.assertIsNone(self.repository.get_device_group("lab"))

        # the ordinary client alone is fine so the refusals above came from the protected one
        status, _, body = self.server.write_response_for(
            "/api/groups",
            {"id": "lab", "name": "Lab", "profile_id": "slow", "connected_clients": [ordinary]},
        )
        self.assertEqual(status, 200, body)
        self.assertIsNotNone(self.repository.get_device_by_mac_address("aa:bb:cc:dd:ee:02"))

    def _save_unlabelled_client(self, name: str = "Client 192.168.50.178") -> None:
        self.repository.save_device(
            Device(
                id="client-dc1ed53e974c",
                name=name,
                ipv4_address="192.168.50.178",
                profile_id="slow",
                mac_address="dc:1e:d5:3e:97:4c",
            )
        )
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c", display_name="Sam MacBook", device_type="laptop"
        )

    def test_group_members_carry_the_saved_label_as_display_name(self) -> None:
        self._save_unlabelled_client()
        self.repository.save_device_group(
            DeviceGroup("family", "Family", ("alice", "client-dc1ed53e974c"), "slow")
        )

        _, _, body = self.server.response_for("/api/groups")
        members = {item["id"]: item for item in json.loads(body)["groups"][0]["devices"]}

        self.assertEqual(members["client-dc1ed53e974c"]["display_name"], "Sam MacBook")
        self.assertEqual(members["alice"]["display_name"], "Alice's laptop")

    def test_the_group_save_response_carries_the_saved_label(self) -> None:
        self._save_unlabelled_client()

        status, _, body = self.server.write_response_for(
            "/api/groups",
            {
                "id": "family",
                "name": "Family",
                "device_ids": ["client-dc1ed53e974c"],
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 200, body)
        member = json.loads(body)["group"]["devices"][0]
        self.assertEqual(member["display_name"], "Sam MacBook")

    def test_the_device_save_response_carries_the_saved_label(self) -> None:
        self.repository.save_connected_device_label(
            mac_address="dc:1e:d5:3e:97:4c", display_name="Sam MacBook", device_type="laptop"
        )

        status, _, body = self.server.write_response_for(
            "/api/devices",
            {
                "id": "client-dc1ed53e974c",
                "name": "Client 192.168.50.178",
                "ipv4_address": "192.168.50.178",
                "profile_id": "slow",
                "mac_address": "dc:1e:d5:3e:97:4c",
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["device"]["display_name"], "Sam MacBook")

    def test_the_apply_profile_response_carries_the_saved_label(self) -> None:
        self.server.helper_client = FakeHelper()
        self._save_unlabelled_client(name="Alice phone")

        status, _, body = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 200, body)
        device = json.loads(body)["device"]
        self.assertEqual(device["name"], "Alice phone")
        self.assertEqual(device["display_name"], "Sam MacBook")

    def test_random_website_analytics_rows_carry_a_display_name(self) -> None:
        self._save_unlabelled_client()
        self.repository.save_device(
            Device(
                id="client-aabbccddeeff",
                name="Client 192.168.50.180",
                ipv4_address="192.168.50.180",
                profile_id="slow",
                mac_address="aa:bb:cc:dd:ee:00",
            )
        )
        now = int(time.time())
        self.repository.save_random_website_outcomes(
            [
                RandomWebsiteOutcome("client-dc1ed53e974c", "labelled.example", True, now),
                RandomWebsiteOutcome("client-aabbccddeeff", "plain.example", True, now),
                RandomWebsiteOutcome("alice", "named.example", True, now),
            ],
            now=now,
        )

        status, _, body = self.server.response_for(
            "/api/random-website-analytics", query="days=1"
        )

        self.assertEqual(status, 200)
        rows = {row["domain"]: row for row in json.loads(body)["rows"]}
        self.assertEqual(rows["labelled.example"]["display_name"], "Sam MacBook")
        self.assertEqual(rows["labelled.example"]["device_name"], "Client 192.168.50.178")
        self.assertEqual(rows["plain.example"]["display_name"], "Unknown device")
        self.assertEqual(rows["named.example"]["display_name"], "Alice's laptop")

    def test_device_title_helper(self) -> None:
        from jittersim.api.server import _device_title

        placeholder = Device(
            id="client-dc1ed53e974c",
            name="Client 192.168.50.178",
            ipv4_address="192.168.50.178",
            profile_id="slow",
            mac_address="dc:1e:d5:3e:97:4c",
        )
        named = Device(
            id="kitchen", name="Kitchen tablet", ipv4_address="192.168.50.179", profile_id="slow"
        )
        labels = {"dc:1e:d5:3e:97:4c": {"display_name": "Sam MacBook", "device_type": "laptop"}}

        self.assertEqual(_device_title(placeholder, labels, "gone"), "Sam MacBook")
        self.assertEqual(
            _device_title(placeholder, {}, "gone"), "Unknown device (192.168.50.178)"
        )
        self.assertEqual(_device_title(named, {}, "gone"), "Kitchen tablet")
        self.assertEqual(_device_title(None, labels, "gone"), "gone")

    def test_unlabelled_placeholder_devices_keep_distinct_titles(self) -> None:
        for suffix in ("178", "179"):
            self.repository.save_device(
                Device(
                    id="client-" + suffix,
                    name="Client 192.168.50." + suffix,
                    ipv4_address="192.168.50." + suffix,
                    profile_id="slow",
                    mac_address="dc:1e:d5:3e:97:" + ("4c" if suffix == "178" else "4d"),
                )
            )
            self.server.write_response_for(
                "/api/data-limits",
                {
                    "device_id": "client-" + suffix,
                    "profile_id": "slow",
                    "limit_bytes": 1_000,
                    "reset_mode": "manual",
                    "reset_interval_seconds": 0,
                    "reset_times": [],
                    "enabled": True,
                },
            )

        _, _, body = self.server.response_for("/api/data-limits")
        titles = {
            item["device_id"]: item["device_name"] for item in json.loads(body)["data_limits"]
        }

        self.assertEqual(titles["client-178"], "Unknown device (192.168.50.178)")
        self.assertEqual(titles["client-179"], "Unknown device (192.168.50.179)")

    def _observe_domains(self, *domains: str) -> None:
        self.repository.save_domain_observations(
            tuple(
                DomainObservation(
                    ipv4_address="192.168.50.178",
                    domain=domain,
                    query_count=1,
                    first_seen=10,
                    last_seen=10,
                )
                for domain in domains
            ),
            now=10,
        )

    def test_service_quota_payload_uses_the_resolved_device_name(self) -> None:
        self._save_unlabelled_client()
        self._observe_domains("netflix.com")
        status, _, body = self.server.write_response_for(
            "/api/service-quotas",
            {
                "id": "sam-video",
                "device_id": "client-dc1ed53e974c",
                "name": "Video service",
                "domains": ["netflix.com"],
                "profile_id": "slow",
                "limit_bytes": 2_000,
            },
        )
        self.assertEqual(status, 200, body)

        _, _, body = self.server.response_for("/api/service-quotas")

        self.assertEqual(json.loads(body)["service_quotas"][0]["device_name"], "Sam MacBook")

    def test_time_quota_payload_uses_the_resolved_device_name(self) -> None:
        self._save_unlabelled_client()
        self._observe_domains("netflix.com")
        status, _, body = self.server.write_response_for(
            "/api/time-quotas",
            {
                "id": "sam-netflix-time",
                "device_id": "client-dc1ed53e974c",
                "name": "Netflix time",
                "domains": ["netflix.com"],
                "profile_id": "slow",
                "limit_seconds": 3_600,
                "window_duration_seconds": 7_200,
            },
        )
        self.assertEqual(status, 200, body)

        _, _, body = self.server.response_for("/api/time-quotas")

        self.assertEqual(json.loads(body)["time_quotas"][0]["device_name"], "Sam MacBook")

    def test_service_assignment_payload_uses_the_resolved_device_name(self) -> None:
        self._save_unlabelled_client()
        status, _, body = self.server.write_response_for(
            "/api/service-assignments",
            {
                "id": "sam-video",
                "device_id": "client-dc1ed53e974c",
                "name": "Video",
                "domains": ["youtube.com"],
                "profile_id": "slow",
                "enabled": True,
            },
        )
        self.assertEqual(status, 200, body)

        _, _, body = self.server.response_for("/api/service-assignments")

        self.assertEqual(json.loads(body)["service_assignments"][0]["device_name"], "Sam MacBook")

    def test_progression_payload_uses_the_resolved_device_name(self) -> None:
        self._save_unlabelled_client()
        status, _, body = self.server.write_response_for(
            "/api/progressions",
            {
                "id": "five-hours",
                "device_id": "client-dc1ed53e974c",
                "start_profile_id": "slow",
                "end_profile_id": "slow",
                "mode": "time",
                "duration_minutes": 300,
            },
        )
        self.assertEqual(status, 200, body)

        _, _, body = self.server.response_for("/api/progressions")

        self.assertEqual(json.loads(body)["progressions"][0]["device_name"], "Sam MacBook")

    def test_observations_find_the_label_through_the_saved_assignment_without_a_helper(
        self,
    ) -> None:
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        self.server.helper_client = None
        self._save_unlabelled_client()

        _, _, body = self.server.response_for("/api/observations")

        self.assertEqual(json.loads(body)["devices"][0]["display_name"], "Sam MacBook")

    def test_new_client_name_uses_a_64_character_hostname_but_not_a_longer_one(self) -> None:
        from jittersim.api.server import _new_client_name

        self.assertEqual(_new_client_name(None, "h" * 64, "192.168.50.7"), "h" * 64)
        self.assertEqual(
            _new_client_name(None, "h" * 65, "192.168.50.7"), "Client 192.168.50.7"
        )
        self.assertEqual(_new_client_name("l" * 64, "host", "192.168.50.7"), "l" * 64)
        self.assertEqual(_new_client_name("l" * 65, "host", "192.168.50.7"), "host")

    def test_new_client_name_skips_a_name_with_control_characters(self) -> None:
        from jittersim.api.server import _new_client_name

        self.assertEqual(
            _new_client_name(None, "bad\x07name", "192.168.50.7"), "Client 192.168.50.7"
        )
        self.assertEqual(_new_client_name("bad\nlabel", "host", "192.168.50.7"), "host")
        self.assertEqual(_new_client_name("  Sam MacBook ", None, "192.168.50.7"), "Sam MacBook")

    def test_observations_survive_any_kind_of_broken_station_snapshot(self) -> None:
        def broken_with(snapshot=None, error=None):
            class Broken(FakeHelper):
                def connected_device_snapshot(self):
                    if error is not None:
                        raise error
                    return snapshot

            return Broken()

        cases = {
            "value error": broken_with(error=ValueError("bad reply")),
            "os error": broken_with(error=OSError("socket closed")),
            "helper error": broken_with(error=HelperClientError("simulated failure")),
            "no snapshot": broken_with(snapshot=None),
            "devices is a number": broken_with(snapshot={"devices": 5}),
            "devices is missing": broken_with(snapshot={}),
            "station is not a dict": broken_with(snapshot={"devices": [None, 5, "text"]}),
            "address is not text": broken_with(
                snapshot={"devices": [{"ipv4_address": ["192.168.50.178"], "hostname": "x"}]}
            ),
        }
        self.server.helper_client = FakeHelper()
        self.server.write_response_for("/api/observations/scan", {})
        for name, helper in cases.items():
            with self.subTest(name):
                self.server.helper_client = helper

                status, _, body = self.server.response_for("/api/observations")

                self.assertEqual(status, 200)
                group = json.loads(body)["devices"][0]
                self.assertEqual(group["display_name"], "Unknown device")
                self.assertIsNone(group["hostname"])

    def test_new_client_name_skips_hidden_control_characters(self) -> None:
        from jittersim.api.server import _new_client_name

        for character in ("\x7f", "‎", "‏", "‪", "‮", "⁦", "⁩"):
            with self.subTest(character=repr(character)):
                self.assertEqual(
                    _new_client_name(None, "host" + character + "name", "192.168.50.7"),
                    "Client 192.168.50.7",
                )
                self.assertEqual(
                    _new_client_name("Sam" + character + "iPhone", "host", "192.168.50.7"), "host"
                )

    def test_new_client_name_keeps_emoji_and_accents(self) -> None:
        from jittersim.api.server import _new_client_name

        self.assertEqual(_new_client_name("Sam's 📱 phone", "host", "192.168.50.7"), "Sam's 📱 phone")
        self.assertEqual(_new_client_name(None, "Café-tablet", "192.168.50.7"), "Café-tablet")


if __name__ == "__main__":
    unittest.main()
