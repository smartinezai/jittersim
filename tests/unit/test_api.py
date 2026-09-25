import contextlib
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path

from jittersim.api.server import DASHBOARD_HTML, ControlRequestHandler, ControlServer
from jittersim.config.models import DataLimit, Device, DomainObservation, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.helper import HelperClientError


class FakeHelper:
    def __init__(self, fail_action: str = "") -> None:
        self.fail_action = fail_action
        self.applied_plan = None
        self.reset_called = False

    def ping(self):
        return {"ok": True, "action": "ping"}

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

    def configure_access_point(self, *, ssid, password):
        return {"ok": True, "action": "configure-ap", "commands_run": 3}

    def sync_access_point_from_router(self, *, admin_password):
        return {
            "ok": True,
            "action": "router-sync-ap",
            "ssid": "Vodafone-Test",
            "router_wifi_enabled": True,
            "commands_run": 3,
        }

    def set_router_wifi(self, *, admin_password, enabled):
        return {"ok": True, "action": "router-wifi", "enabled": enabled}

    def connected_devices(self):
        return [
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "state": "reachable",
                "assigned_device_id": None,
                "assigned_device_name": None,
                "assigned_profile_id": None,
            }
        ]

    def uplink_info(self):
        return {
            "interface": "eth0",
            "connection_name": "netplan-eth0",
            "gateway": "192.168.0.1",
        }

    def observe_dns_queries(self):
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


def create_test_certificate(directory: Path):
    """Create a throwaway self-signed certificate, or return None without OpenSSL."""

    openssl = shutil.which("openssl")
    if openssl is None:
        return None
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
    return certificate, key


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

    def test_startup_recovery_survives_a_later_settings_change(self) -> None:
        for last_action in ("apply", "reset"):
            with self.subTest(last_action=last_action):
                helper = FakeHelper()
                self.server.helper_client = helper
                self.repository.update_runtime_state(
                    observed_state="applied" if last_action == "apply" else "reset",
                    last_action=last_action,
                    last_result="success",
                    last_error=None,
                )
                self.repository.save_device(
                    Device(
                        id="alice",
                        name="Renamed laptop",
                        ipv4_address="192.168.50.10",
                        profile_id="slow",
                    )
                )

                self.server.recover_saved_network_state()

                self.assertEqual(helper.applied_plan is not None, last_action == "apply")
                self.assertEqual(helper.reset_called, last_action == "reset")

    def test_startup_recovery_waits_for_the_helper_to_start(self) -> None:
        class StartingHelper(FakeHelper):
            pings = 0

            def ping(self):
                self.pings += 1
                if self.pings < 3:
                    raise HelperClientError("helper socket is not ready")
                return super().ping()

        helper = StartingHelper()
        self.server.helper_client = helper
        self.server.helper_poll_seconds = 0.01
        self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )

        self.server.recover_saved_network_state()

        self.assertEqual(helper.pings, 3)
        self.assertIsNotNone(helper.applied_plan)

    def test_startup_recovery_gives_up_when_the_helper_never_starts(self) -> None:
        class MissingHelper(FakeHelper):
            def ping(self):
                raise HelperClientError("helper socket is not ready")

        helper = MissingHelper()
        self.server.helper_client = helper
        self.server.helper_startup_wait_seconds = 0.05
        self.server.helper_poll_seconds = 0.01
        self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )

        with self.assertLogs("jittersim.api.server", level="ERROR"):
            self.server.recover_saved_network_state()

        self.assertIsNone(helper.applied_plan)

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
                "mode": "configuration-only",
                "status": "ok",
            },
        )

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

    def test_admin_reads_require_admin_token(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def get(path, authorization=None):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.path = path
            handler.headers = Message()
            if authorization:
                handler.headers["Authorization"] = authorization
            response = {}
            handler._send_response = lambda **values: response.update(values)
            handler.do_GET()
            return response

        for path in (
            "/api/backup",
            "/api/diagnostics",
            "/api/action-history",
            "/api/observations",
        ):
            with self.subTest(path=path):
                self.assertEqual(get(path)["status"], 401)
                self.assertEqual(get(path, "Bearer wrong-token")["status"], 401)
                self.assertEqual(get(path, "Bearer admin-token")["status"], 200)
        self.assertEqual(get("/api/profiles")["status"], 200)

    def test_live_traffic_requires_admin_token_and_returns_directional_counters(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def get_traffic(authorization):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
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

    def test_write_history_records_only_authorized_route_and_result(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def handle_write(*, authorization, body):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.path = "/api/apply"
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

    def _send(self, method, path, *, authorization="Bearer admin-token", body=None):
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.path = path
        handler.headers = Message()
        handler.headers["Authorization"] = authorization
        if body is not None:
            handler.headers["Content-Length"] = str(len(body))
            handler.rfile = BytesIO(body)
        response = {}
        handler._send_response = lambda **values: response.update(values)
        if method == "GET":
            handler.do_GET()
        else:
            handler._handle_write()
        return response

    def test_malformed_requests_get_an_error_response(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        non_ascii_token = self._send("GET", "/api/diagnostics", authorization="Bearer t\u00f6ken")
        nested = self._send("POST", "/api/profiles", body=b"[" * 100_000 + b"]" * 100_000)
        with self.assertLogs("jittersim.api.server", level="ERROR"):
            list_id = self._send(
                "POST",
                "/api/data-limits",
                body=b'{"device_id": ["alice"], "profile_id": "slow", "limit_bytes": 1}',
            )

        self.assertEqual(non_ascii_token["status"], 401)
        self.assertEqual(nested["status"], 400)
        self.assertEqual(list_id["status"], 500)
        self.assertIn("error", json.loads(list_id["body"]))
        self.assertEqual(self.repository.list_recent_actions()[0]["action"], "/api/data-limits")

    def test_rejected_requests_explain_what_was_wrong(self) -> None:
        cases = (
            (
                "/api/profiles",
                {"id": "Bad Profile!", "name": "Bad"},
                "invalid profile configuration: id must contain 1 to 64 lowercase letters",
            ),
            ("/api/profiles", {"id": "slow"}, "invalid profile configuration: missing name"),
            (
                "/api/timers",
                {"device_id": "alice", "profile_id": "slow", "duration_minutes": 0},
                "invalid timer configuration: duration_minutes must be between 1 and 10080",
            ),
        )
        for path, payload, message in cases:
            with self.subTest(path=path, payload=payload):
                status, _, body = self.server.write_response_for(path, payload)
                self.assertEqual(status, 400)
                self.assertTrue(json.loads(body)["error"].startswith(message))

    def test_list_fields_reject_plain_strings(self) -> None:
        requests = (
            ("/api/policies", {"websites": {"mode": "unrestricted", "blocked_domains": "netflix"}}),
            ("/api/profiles", {"id": "video", "name": "Video", "website_domains": "youtube.com"}),
            (
                "/api/schedules",
                {
                    "id": "work",
                    "device_id": "alice",
                    "profile_id": "slow",
                    "days_of_week": "12",
                    "start_minute": 540,
                    "end_minute": 1_020,
                },
            ),
        )
        for path, payload in requests:
            with self.subTest(path=path):
                status, _, _ = self.server.write_response_for(path, payload)
                self.assertEqual(status, 400)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ())

    def test_tls_requires_both_files_and_wraps_the_dashboard_socket(self) -> None:
        with self.assertRaisesRegex(ValueError, "TLS is required"):
            ControlServer(("0.0.0.0", 0), self.repository)
        with self.assertRaisesRegex(ValueError, "provided together"):
            ControlServer(
                ("127.0.0.1", 0),
                self.repository,
                tls_certfile=Path("dashboard.crt"),
            )

        with tempfile.TemporaryDirectory(prefix="jittersim-tls-test-") as directory:
            files = create_test_certificate(Path(directory))
            if files is None:
                self.skipTest("OpenSSL is required to create a test certificate")
            certificate, key = files
            secure_server = ControlServer(
                ("127.0.0.1", 0),
                self.repository,
                tls_certfile=certificate,
                tls_keyfile=key,
            )
            response: list[bytes] = []

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
        self.assertEqual(len(response), 1)
        self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))

    def _health_after_an_idle_connection(self, server, wrap_client=None) -> bytes:
        server.request_timeout_seconds = 0.5
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            with socket.create_connection(server.server_address):
                with socket.create_connection(server.server_address, timeout=3) as connection:
                    client = wrap_client(connection) if wrap_client else connection
                    client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                    return client.recv(4096)
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_an_idle_connection_does_not_block_other_requests(self) -> None:
        response = self._health_after_an_idle_connection(self.server)

        self.assertTrue(response.startswith(b"HTTP/1.0 200 OK"))

    def test_an_idle_tls_connection_does_not_block_other_requests(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jittersim-tls-test-") as directory:
            files = create_test_certificate(Path(directory))
            if files is None:
                self.skipTest("OpenSSL is required to create a test certificate")
            secure_server = ControlServer(
                ("127.0.0.1", 0),
                self.repository,
                tls_certfile=files[0],
                tls_keyfile=files[1],
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            try:
                response = self._health_after_an_idle_connection(
                    secure_server,
                    wrap_client=lambda connection: context.wrap_socket(
                        connection, server_hostname="127.0.0.1"
                    ),
                )
            finally:
                secure_server.server_close()

        self.assertTrue(response.startswith(b"HTTP/1.0 200 OK"))

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
        self.assertEqual(
            json.loads(body)["error"],
            "invalid service quota configuration: "
            "service quota domains must come from DNS observations",
        )

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
            {"admin_password": "router-secret"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body)["result"]["ssid"], "Vodafone-Test")

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
                "devices": [
                    {
                        "ipv4_address": "192.168.50.178",
                        "mac_address": "dc:1e:d5:3e:97:4c",
                        "state": "reachable",
                        "assigned_device_id": None,
                        "assigned_device_name": None,
                        "assigned_profile_id": None,
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
        self.assertEqual(response["device"]["id"], "client-192-168-50-178")
        self.assertEqual(response["device"]["profile_id"], "slow")
        self.assertEqual(response["device"]["mac_address"], "dc:1e:d5:3e:97:4c")
        self.assertEqual(
            self.repository.get_device_by_ipv4_address("192.168.50.178").profile_id,
            "slow",
        )
        self.assertEqual(self.server.helper_client.applied_plan.mark_rules[0].device_id, "alice")
        self.assertEqual(len(self.server.helper_client.applied_plan.mark_rules), 2)

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

    def test_dashboard_keeps_javascript_escapes_intact(self) -> None:
        self.assertIn('.join("\\n")', DASHBOARD_HTML)
        self.assertIn("/^(\\d{1,2}):(\\d{2})$/", DASHBOARD_HTML)

    def test_dashboard_script_is_valid_javascript(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to parse the dashboard script")
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "dashboard.js"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

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
        self.assertIn("Apply current configuration", DASHBOARD_HTML)
        self.assertIn("Reset network", DASHBOARD_HTML)
        self.assertIn('id="check-admin-token"', DASHBOARD_HTML)
        self.assertIn("Check token", DASHBOARD_HTML)
        self.assertIn("Checking the admin token...", DASHBOARD_HTML)
        self.assertIn("setButtonBusy(event.submitter, true)", DASHBOARD_HTML)
        self.assertIn("if (submitButton) setButtonBusy(submitButton, false);", DASHBOARD_HTML)
        self.assertIn("Dashboard refresh failed:", DASHBOARD_HTML)
        self.assertIn("Settings changed since the last network action", DASHBOARD_HTML)
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
        self.assertIn('id="traffic-chart"', DASHBOARD_HTML)
        self.assertIn('id="traffic-device"', DASHBOARD_HTML)
        self.assertIn("Last 60 seconds", DASHBOARD_HTML)
        self.assertIn("TRAFFIC_POLL_INTERVAL_MS = 2000", DASHBOARD_HTML)
        self.assertIn("TRAFFIC_MAX_POINTS = 30", DASHBOARD_HTML)
        self.assertIn('document.addEventListener("visibilitychange"', DASHBOARD_HTML)
        self.assertIn('request("/api/traffic", "GET")', DASHBOARD_HTML)
        self.assertIn("before JitterSim adds delay or drops packets", DASHBOARD_HTML)
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
            },
        )

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

    def test_live_changes_do_not_undo_an_explicit_reset(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper
        self.repository.update_runtime_state(
            observed_state="reset",
            last_action="reset",
            last_result="success",
            last_error=None,
        )
        self.server.write_response_for(
            "/api/data-limits",
            {"device_id": "alice", "profile_id": "slow", "limit_bytes": 1_000},
        )

        requests = (
            ("/api/data-limits/reset", {"device_id": "alice"}),
            ("/api/timers", {"id": "t", "device_id": "alice", "profile_id": "slow", "duration_minutes": 5}),
            ("/api/timers/cancel", {"id": "t"}),
        )
        for path, payload in requests:
            with self.subTest(path=path):
                status, _, body = self.server.write_response_for(path, payload)
                self.assertEqual(status, 200)
                self.assertFalse(json.loads(body)["live_applied"])
        self.assertIsNone(helper.applied_plan)
        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")

    def test_manual_apply_runs_while_scheduled_applies_are_held(self) -> None:
        events = []

        class RecordingScheduler:
            @contextlib.contextmanager
            def exclusive(self):
                events.append("hold")
                yield
                events.append("release")

            def build_plan(self, *, repository):
                return compile_stored_plan(repository=repository)

            def sample_usage(self, *, repository):
                events.append("sample")

            def activate(self, *, applied_payload):
                events.append("activate")

        class RecordingHelper(FakeHelper):
            def apply(self, *, plan):
                events.append("apply")
                return super().apply(plan=plan)

        self.server.helper_client = RecordingHelper()
        self.server.scheduler = RecordingScheduler()

        status, _, _ = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 200)
        self.assertEqual(events, ["hold", "sample", "apply", "activate", "release"])

    def test_apply_and_reset_record_usage_before_the_counters_restart(self) -> None:
        class CountingHelper(FakeHelper):
            def traffic_counters(self):
                return {"devices": {"alice": self.alice_bytes}, "services": {}}

        for path in ("/api/apply", "/api/reset"):
            with self.subTest(path=path):
                self.repository.save_data_limit(
                    DataLimit(
                        device_id="alice",
                        profile_id="slow",
                        limit_bytes=10**9,
                        used_bytes=1_000,
                        counter_bytes=1_000,
                        last_reset_at=100,
                    )
                )
                helper = CountingHelper()
                helper.alice_bytes = 1_600
                scheduler = RuntimeScheduler(
                    database_path=":memory:",
                    helper_client=helper,
                    poll_seconds=3_600,
                )
                self.server.helper_client = helper
                self.server.scheduler = scheduler
                try:
                    status, _, _ = self.server.write_response_for(path, {})
                finally:
                    scheduler.stop()

                self.assertEqual(status, 200)
                self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 1_600)

    def test_live_changes_apply_while_the_network_is_applied(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper
        self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )

        status, _, body = self.server.write_response_for(
            "/api/timers",
            {"id": "t", "device_id": "alice", "profile_id": "slow", "duration_minutes": 5},
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["live_applied"])
        self.assertIsNotNone(helper.applied_plan)

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
        self.assertEqual(
            json.loads(body)["error"],
            "invalid timer configuration: duration_minutes must be between 1 and 10080",
        )

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
        self.assertEqual(
            json.loads(body)["error"],
            "invalid progression configuration: traffic mode requires a positive query target",
        )

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

    def test_rejects_invalid_profile_writes_before_persistence(self) -> None:
        status, content_type, body = self.server.write_response_for(
            "/api/profiles",
            {"id": "unsafe id", "name": "Invalid"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body)["error"],
            "invalid profile configuration: id must contain 1 to 64 lowercase letters, "
            "numbers, hyphens or underscores",
        )
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


if __name__ == "__main__":
    unittest.main()
