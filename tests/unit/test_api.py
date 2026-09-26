import base64
import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime
from email.message import Message
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from jittersim.api.server import (
    DASHBOARD_HTML,
    ControlRequestHandler,
    ControlServer,
    _build_parser,
)
from jittersim.api.session import issue_session
from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    Impairment,
    Profile,
    Schedule,
    UnaffectedDevice,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.measure import comparison_table
from jittersim.networking.helper import HelperClientError


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


VIDEO_ASSIGNMENT = {
    "id": "video",
    "device_id": "alice",
    "name": "Video",
    "domains": ["youtube.com"],
    "profile_id": "slow",
}


class CountingHelper(FakeHelper):
    """Count traffic like the real helper: every apply restarts the counters."""

    def __init__(self, *, devices=None, services=None) -> None:
        super().__init__()
        self.devices = dict(devices or {})
        self.services = dict(services or {})

    def apply(self, *, plan):
        result = super().apply(plan=plan)
        self.devices = dict.fromkeys(self.devices, 0)
        self.services = dict.fromkeys(self.services, 0)
        return result

    def traffic_counters(self):
        return {"devices": dict(self.devices), "services": dict(self.services)}


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


@contextlib.contextmanager
def local_time_zone(name: str):
    """Run with the process's local time in another zone."""

    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


class DashboardStructure(HTMLParser):
    """Record which tab panel holds each card and heading, checking every tag closes."""

    VOID_TAGS = {"br", "hr", "img", "input", "link", "meta", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.open_tags = []
        self.errors = []
        self.panels = []
        self.panel_parents = set()
        self.cards = []
        self.headings = {}
        self._heading = None

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        panel = self.open_tags[-1][1] if self.open_tags else None
        if "tab-panel" in classes:
            if panel is not None:
                self.errors.append(f"{attributes.get('id')} is inside {panel}")
            self.panel_parents.add(tuple(open_tag for open_tag, _ in self.open_tags))
            panel = attributes.get("id")
            self.panels.append(panel)
        if "card" in classes:
            self.cards.append((classes, panel))
        if tag in self.VOID_TAGS:
            return
        self.open_tags.append((tag, panel))
        if tag == "h2":
            self._heading = ""

    def handle_endtag(self, tag) -> None:
        if not self.open_tags or self.open_tags[-1][0] != tag:
            expected = self.open_tags[-1][0] if self.open_tags else "nothing"
            self.errors.append(f"</{tag}> at line {self.getpos()[0]} closes {expected}")
            return
        _, panel = self.open_tags.pop()
        if tag == "h2":
            self.headings.setdefault(panel, []).append(" ".join(self._heading.split()))
            self._heading = None

    def handle_data(self, data) -> None:
        if self._heading is not None:
            self._heading += data


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
            "/api/export",
        ):
            with self.subTest(path=path):
                self.assertEqual(get(path)["status"], 401)
                self.assertEqual(get(path, "Bearer wrong-token")["status"], 401)
                self.assertEqual(get(path, "Bearer admin-token")["status"], 200)
        # Only the sign-in page is public.
        self.assertEqual(get("/api/profiles")["status"], 401)
        self.assertEqual(get("/api/health")["status"], 401)
        self.assertEqual(get("/api/health", "Bearer admin-token")["status"], 200)

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
        ):
            with self.subTest(path=path):
                handler = object.__new__(ControlRequestHandler)
                handler.server = self.server
                handler.path = path
                handler.headers = Message()
                response = {}
                handler._send_response = lambda **values: response.update(values)
                handler.do_GET()
                self.assertEqual(response["status"], 401)
                handler.headers["Authorization"] = "Bearer admin-token"
                handler.do_GET()
                self.assertEqual(response["status"], 200)

    def test_health_reports_an_unreachable_helper(self) -> None:
        self.server.helper_client = FakeHelper(fail_action="ping")
        _, _, body = self.server.response_for("/api/health")
        self.assertEqual(json.loads(body)["status"], "degraded")
        self.assertFalse(json.loads(body)["helper_reachable"])

    def test_live_traffic_includes_what_jittersim_dropped_and_holds(self) -> None:
        class QueueHelper(FakeHelper):
            def traffic_counters(self):
                counters = super().traffic_counters()
                counters["queues"] = {
                    "devices": {
                        "alice": {
                            "upload_dropped": 24,
                            "download_dropped": 0,
                            "upload_queued": 0,
                            "download_queued": 2,
                        }
                    }
                }
                return counters

        self.server.helper_client = QueueHelper()
        with_queues = json.loads(self.server.response_for("/api/traffic")[2])
        self.server.helper_client = FakeHelper()
        without_queues = json.loads(self.server.response_for("/api/traffic")[2])

        self.assertEqual(with_queues["queues"]["alice"]["upload_dropped"], 24)
        self.assertEqual(with_queues["queues"]["alice"]["download_queued"], 2)
        self.assertEqual(without_queues["queues"], {})

    def test_live_traffic_includes_everyone_else(self) -> None:
        flows = {
            "upload_bytes": 700, "download_bytes": 9_000, "upload_packets": 7, "download_packets": 9
        }
        queue = {
            "upload_dropped": 11, "download_dropped": 0, "upload_queued": 4, "download_queued": 0
        }

        class EveryoneElseHelper(FakeHelper):
            def traffic_counters(self):
                counters = super().traffic_counters()
                counters["everyone_else"] = flows
                counters["queues"] = {"devices": {}, "everyone_else": queue}
                return counters

        self.server.helper_client = EveryoneElseHelper()
        traffic = json.loads(self.server.response_for("/api/traffic")[2])
        self.server.helper_client = FakeHelper()
        without = json.loads(self.server.response_for("/api/traffic")[2])

        self.assertEqual(traffic["everyone_else"], flows)
        self.assertEqual(traffic["everyone_else_queue"], queue)
        # Everyone else is not one of the devices.
        self.assertEqual(set(traffic["devices"]), {"alice"})
        self.assertIsNone(without["everyone_else"])
        self.assertIsNone(without["everyone_else_queue"])

    def test_live_traffic_can_show_everyone_else(self) -> None:
        result = self._run_dashboard_functions(
            ["selectedTrafficTotals", "selectedQueueTotals"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n"
            "const picked = {value: 'all'};\n"
            "const element = () => picked;\n"
            "const flows = (bytes) => ({upload_bytes: bytes, download_bytes: bytes,\n"
            "  upload_packets: 1, download_packets: 1});\n"
            "const counters = {alice: flows(10), bob: flows(20), __everyone_else__: flows(500)};\n"
            "const queues = {alice: {upload_dropped: 1, download_dropped: 0, upload_queued: 0,\n"
            "  download_queued: 0}, __everyone_else__: {upload_dropped: 9, download_dropped: 0,\n"
            "  upload_queued: 2, download_queued: 0}};\n",
            "const all = [selectedTrafficTotals(counters).upload_bytes,\n"
            "  selectedQueueTotals(queues)];\n"
            "picked.value = EVERYONE_ELSE_TRAFFIC;\n"
            "const others = [selectedTrafficTotals(counters).upload_bytes,\n"
            "  selectedQueueTotals(queues)];\n"
            "console.log(JSON.stringify({all, others}));\n",
        )

        # "All assigned devices" keeps meaning the saved devices.
        self.assertEqual(result["all"], [30, {"dropped": 1, "queued": 0}])
        self.assertEqual(result["others"], [500, {"dropped": 9, "queued": 2}])
        self.assertIn('option.value = EVERYONE_ELSE_TRAFFIC;', DASHBOARD_HTML)

    def test_live_traffic_adds_everyone_else_to_the_counters(self) -> None:
        result = self._run_dashboard_functions(
            ["mergedTrafficCounters"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n",
            "const data = {devices: {alice: {upload_bytes: 1}}, queues: {alice: {queued: 1}},\n"
            "  everyone_else: {upload_bytes: 9}, everyone_else_queue: {queued: 2}};\n"
            "const merged = mergedTrafficCounters(data);\n"
            "const without = mergedTrafficCounters({devices: {alice: {upload_bytes: 1}}});\n"
            "console.log(JSON.stringify({merged: merged, without: without,\n"
            "  untouched: Object.keys(data.devices)}));\n",
        )

        self.assertEqual(
            result["merged"],
            {
                "devices": {"alice": {"upload_bytes": 1}, "__everyone_else__": {"upload_bytes": 9}},
                "queues": {"alice": {"queued": 1}, "__everyone_else__": {"queued": 2}},
            },
        )
        self.assertEqual(
            result["without"], {"devices": {"alice": {"upload_bytes": 1}}, "queues": {}}
        )
        self.assertEqual(result["untouched"], ["alice"])

    def test_the_traffic_menu_offers_everyone_else_while_it_has_a_profile(self) -> None:
        result = self._run_dashboard_functions(
            ["updateTrafficDeviceOptions", "everyoneElseAssigned"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n"
            "let everyoneElse = {profile_id: 'slow'};\n"
            "let savedSchedules = [];\n"
            "const select = {options: [], value: '',\n"
            "  replaceChildren() { this.options = []; },\n"
            "  appendChild(option) { this.options.push(option); }};\n"
            "const element = () => select;\n"
            "const document = {createElement: () => ({})};\n"
            "let resets = 0;\n"
            "function resetTrafficHistory() { resets += 1; }\n",
            "const devices = [{id: 'alice', name: 'Alice', ipv4_address: '192.168.50.10'}];\n"
            "updateTrafficDeviceOptions(devices);\n"
            "const offered = select.options.map((option) => option.value);\n"
            "select.value = EVERYONE_ELSE_TRAFFIC;\n"
            "updateTrafficDeviceOptions(devices);\n"
            "const kept = [select.value, resets];\n"
            "everyoneElse = {profile_id: null};\n"
            "updateTrafficDeviceOptions(devices);\n"
            "const after = [select.options.map((option) => option.value), select.value, resets];\n"
            "savedSchedules = [{everyone_else: true, enabled: true}];\n"
            "updateTrafficDeviceOptions(devices);\n"
            "console.log(JSON.stringify({offered: offered, kept: kept, after: after,\n"
            "  scheduled: select.options.map((option) => option.value)}));\n",
        )

        self.assertEqual(result["offered"], ["all", "alice", "__everyone_else__"])
        self.assertEqual(result["kept"], ["__everyone_else__", 0])
        # Without a profile the choice goes, and the graph starts again for "all".
        self.assertEqual(result["after"], [["all", "alice"], "all", 1])
        # An everyone-else schedule brings the choice back.
        self.assertEqual(result["scheduled"], ["all", "alice", "__everyone_else__"])

    def test_everyone_else_shows_its_own_queue_limits(self) -> None:
        result = self._run_dashboard_functions(
            ["trafficRateMbit", "trafficReferencesFromResponse"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n"
            "const EVERYONE_ELSE_QUEUE_ID = 'rule:everyone-else';\n"
            "let currentIspTrafficLimits = {upload_kbit: null, download_kbit: 100000};\n"
            "const fields = {'traffic-device': {value: EVERYONE_ELSE_TRAFFIC},\n"
            "  'traffic-profile-status': {textContent: ''}};\n"
            "const element = (id) => fields[id];\n",
            "const references = trafficReferencesFromResponse({profile_limits_available: true,\n"
            "  profile_limits: {'rule:everyone-else': {upload_kbit: 1000, download_kbit: 2000},\n"
            "    alice: {upload_kbit: 5, download_kbit: 5}}});\n"
            "console.log(JSON.stringify([references.profile_upload,\n"
            "  references.profile_download]));\n",
        )

        self.assertEqual(result, [1, 2])

    def test_loading_everyone_else_refreshes_the_traffic_menu(self) -> None:
        result = self._run_dashboard_functions(
            ["loadEveryoneElse"],
            "let everyoneElse = {profile_id: null, unaffected_devices: []};\n"
            "const devices = [];\n"
            "const element = () => ({value: ''});\n"
            "async function request() { return {profile_id: 'slow', unaffected_devices: []}; }\n"
            "function renderTable() {}\n"
            "function renderDeviceTable() {}\n"
            "function rerenderConnectedDevices() {}\n"
            "const menus = [];\n"
            "function updateTrafficDeviceOptions() { menus.push(everyoneElse.profile_id); }\n",
            "loadEveryoneElse().then(() => console.log(JSON.stringify(menus)));\n",
        )

        self.assertEqual(result, ["slow"])

    def test_write_history_records_only_authorized_route_and_result(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def handle_write(*, authorization, body, path="/api/apply"):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
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

    def test_browser_service_profile_rejections_say_why(self) -> None:
        video = {
            "id": "video",
            "device_id": "alice",
            "name": "Video",
            "domains": ["youtube.com"],
            "profile_id": "slow",
        }
        cases = (
            ({"profile_id": "ghost"}, "profile does not exist"),
            ({"domains": ["youtube.com", "m.youtube.com"]}, "service assignment domains overlap"),
            ({"device_id": "ghost"}, "device must be on the test Wi-Fi"),
        )
        for change, reason in cases:
            with self.subTest(reason=reason):
                status, _, body = self.server.write_response_for(
                    "/api/service-assignments", {**video, **change}
                )

                self.assertEqual(status, 400)
                self.assertEqual(
                    json.loads(body)["error"],
                    f"invalid service assignment configuration: {reason}",
                )

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

    def test_deletes_need_the_admin_token(self) -> None:
        self.server.admin_token = "admin-token"

        unauthorized = self._send(
            "POST", "/api/devices/delete", authorization="Bearer wrong", body=b'{"id": "alice"}'
        )
        missing = self._send("POST", "/api/devices/delete", body=b'{"id": "nobody"}')

        self.assertEqual(unauthorized["status"], 401)
        self.assertEqual(missing["status"], 404)
        self.assertIsNotNone(self.repository.get_device("alice"))

    def test_profile_delete_refuses_a_profile_in_use(self) -> None:
        self.repository.save_profile(Profile(id="unused", name="Unused"))

        in_use_status, _, in_use_body = self.server.write_response_for(
            "/api/profiles/delete", {"id": "slow"}
        )
        unused_status, _, _ = self.server.write_response_for(
            "/api/profiles/delete", {"id": "unused"}
        )

        self.assertEqual(in_use_status, 409)
        self.assertEqual(
            json.loads(in_use_body),
            {
                "error": "profile is in use by device Alice's laptop",
                "references": ["device Alice's laptop"],
            },
        )
        self.assertEqual(unused_status, 200)
        self.assertIsNone(self.repository.get_profile("unused"))
        self.assertIsNotNone(self.repository.get_profile("slow"))

    def test_deleting_a_device_reapplies_the_plan_without_it(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper
        self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )

        status, _, body = self.server.write_response_for("/api/devices/delete", {"id": "alice"})

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["device"], {"id": "alice", "deleted": True})
        self.assertTrue(json.loads(body)["live_applied"])
        self.assertEqual(helper.applied_plan.mark_rules, ())
        self.assertIsNone(self.repository.get_device("alice"))

    def test_rules_and_limits_can_be_deleted(self) -> None:
        self.server.write_response_for(
            "/api/schedules",
            {
                "id": "work",
                "device_id": "alice",
                "profile_id": "slow",
                "days_of_week": [0],
                "start_minute": 540,
                "end_minute": 1_020,
            },
        )
        self.server.write_response_for(
            "/api/data-limits", {"device_id": "alice", "profile_id": "slow", "limit_bytes": 1_000}
        )
        self.server.write_response_for(
            "/api/groups", {"id": "family", "name": "Family", "device_ids": ["alice"]}
        )
        self.server.write_response_for(
            "/api/group-data-limits", {"group_id": "family", "profile_id": "slow", "limit_bytes": 1}
        )

        deletes = (
            ("/api/schedules/delete", {"id": "work"}),
            ("/api/data-limits/delete", {"device_id": "alice"}),
            ("/api/group-data-limits/delete", {"group_id": "family"}),
            ("/api/service-quotas/delete", {"id": "nothing"}),
            ("/api/time-quotas/delete", {"id": "nothing"}),
        )
        statuses = [self.server.write_response_for(path, payload)[0] for path, payload in deletes]
        repeated = [self.server.write_response_for(path, payload)[0] for path, payload in deletes]

        self.assertEqual(statuses, [200, 200, 200, 404, 404])
        self.assertEqual(repeated, [404, 404, 404, 404, 404])
        self.assertEqual(self.repository.list_schedules(), ())
        self.assertIsNone(self.repository.get_data_limit("alice"))
        self.assertIsNone(self.repository.get_group_data_limit("family"))

    def test_saving_refuses_more_devices_than_the_kernel_can_shape(self) -> None:
        for index in range(14):
            self.repository.save_device(
                Device(
                    id=f"device-{index:02d}",
                    name=f"Device {index}",
                    ipv4_address=f"192.168.50.{index + 100}",
                    profile_id="slow",
                )
            )

        status, _, body = self.server.write_response_for(
            "/api/devices",
            {"id": "extra", "name": "Extra", "ipv4_address": "192.168.50.99", "profile_id": "slow"},
        )
        update_status, _, _ = self.server.write_response_for(
            "/api/devices",
            {
                "id": "alice",
                "name": "Renamed",
                "ipv4_address": "192.168.50.10",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 409)
        self.assertIn("at most 15 devices and service rules", json.loads(body)["error"])
        self.assertIsNone(self.repository.get_device("extra"))
        self.assertEqual(update_status, 200)

    def test_enabling_a_limit_counts_against_the_same_capacity(self) -> None:
        self.repository.save_domain_observations(
            (DomainObservation("192.168.50.10", "netflix.com", 1, 10, 10),),
            now=10,
        )
        for index in range(14):
            self.repository.save_device(
                Device(
                    id=f"device-{index:02d}",
                    name=f"Device {index}",
                    ipv4_address=f"192.168.50.{index + 100}",
                    profile_id="slow",
                )
            )
        quota = {
            "id": "alice-netflix-time",
            "device_id": "alice",
            "name": "Netflix time",
            "domains": ["netflix.com"],
            "profile_id": "slow",
            "limit_seconds": 60,
            "window_duration_seconds": 3_600,
        }

        disabled_status, _, _ = self.server.write_response_for(
            "/api/time-quotas", {**quota, "enabled": False}
        )
        enabled_status, _, body = self.server.write_response_for(
            "/api/time-quotas", {**quota, "enabled": True}
        )

        self.assertEqual(disabled_status, 200)
        self.assertEqual(enabled_status, 409)
        self.assertIn("at most 15 devices and service rules", json.loads(body)["error"])
        self.assertFalse(self.repository.get_time_quota("alice-netflix-time").enabled)

    def test_browser_service_profiles_count_against_the_same_capacity(self) -> None:
        for index in range(13):
            self.repository.save_device(
                Device(
                    id=f"device-{index:02d}",
                    name=f"Device {index}",
                    ipv4_address=f"192.168.50.{index + 100}",
                    profile_id="slow",
                )
            )
        saved, _, _ = self.server.write_response_for("/api/service-assignments", VIDEO_ASSIGNMENT)

        status, _, body = self.server.write_response_for(
            "/api/devices",
            {
                "id": "carol",
                "name": "Carol",
                "ipv4_address": "192.168.50.30",
                "profile_id": "slow",
            },
        )

        renamed, _, _ = self.server.write_response_for(
            "/api/service-assignments", {**VIDEO_ASSIGNMENT, "name": "Videos"}
        )

        self.assertEqual(saved, 200)
        self.assertEqual(status, 409)
        self.assertIn("at most 15 devices and service rules", json.loads(body)["error"])
        self.assertIsNone(self.repository.get_device("carol"))
        # Editing a profile that is already enabled needs no extra capacity.
        self.assertEqual(renamed, 200)

    def test_a_browser_service_profile_beyond_capacity_is_refused_like_a_device(self) -> None:
        for index in range(14):
            self.repository.save_device(
                Device(
                    id=f"device-{index:02d}",
                    name=f"Device {index}",
                    ipv4_address=f"192.168.50.{index + 100}",
                    profile_id="slow",
                )
            )

        status, _, body = self.server.write_response_for("/api/service-assignments", VIDEO_ASSIGNMENT)

        self.assertEqual(status, 409)
        self.assertIn("at most 15 devices and service rules", json.loads(body)["error"])
        self.assertEqual(self.repository.list_service_assignments(), ())

    def test_a_profile_used_by_a_browser_service_profile_cannot_be_deleted(self) -> None:
        self.repository.save_profile(Profile(id="video", name="Video"))
        self.server.write_response_for(
            "/api/service-assignments", {**VIDEO_ASSIGNMENT, "profile_id": "video"}
        )

        status, _, body = self.server.write_response_for("/api/profiles/delete", {"id": "video"})

        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["references"], ["browser service profile Video"])
        self.assertEqual(
            [assignment.id for assignment in self.repository.list_service_assignments()],
            ["video"],
        )

    def test_deleting_a_device_removes_its_browser_service_profiles(self) -> None:
        self.server.write_response_for("/api/service-assignments", VIDEO_ASSIGNMENT)

        status, _, _ = self.server.write_response_for("/api/devices/delete", {"id": "alice"})

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.list_service_assignments(), ())
        # A leftover assignment would stop every plan from building.
        self.assertEqual(compile_stored_plan(repository=self.repository).queues, ())

    def test_imports_keep_browser_service_profiles(self) -> None:
        self.server.write_response_for("/api/service-assignments", VIDEO_ASSIGNMENT)
        exported = self._export(self.server)

        status, _, _ = self.server.write_response_for("/api/import", exported)
        with ConfigRepository(":memory:") as other_repository:
            other = ControlServer(("127.0.0.1", 0), other_repository)
            try:
                other_status, _, _ = other.write_response_for("/api/import", exported)
                copied = other_repository.list_service_assignments()
            finally:
                other.server_close()

        self.assertEqual(exported["service_assignments"][0]["domains"], ["youtube.com"])
        self.assertEqual((status, other_status), (200, 200))
        self.assertEqual(self.repository.list_service_assignments(), copied)
        self.assertEqual([assignment.id for assignment in copied], ["video"])

    def test_import_keeps_the_everyone_else_settings(self) -> None:
        self.repository.save_everyone_else_profile_id("slow")
        self.repository.save_unaffected_device(UnaffectedDevice("aa:aa:aa:aa:aa:aa", "Laptop"))
        exported = self._export(self.server)
        self.repository.save_everyone_else_profile_id(None)
        self.repository.delete_unaffected_device("aa:aa:aa:aa:aa:aa")

        status, _, body = self.server.write_response_for("/api/import", exported)

        self.assertEqual(status, 200, body)
        self.assertEqual(self.repository.get_everyone_else_profile_id(), "slow")
        self.assertEqual(
            self.repository.list_unaffected_devices(),
            (UnaffectedDevice("aa:aa:aa:aa:aa:aa", "Laptop"),),
        )

    def test_an_export_without_everyone_else_settings_still_imports(self) -> None:
        exported = self._export(self.server)
        del exported["everyone_else"]
        self.repository.save_everyone_else_profile_id("slow")

        status, _, body = self.server.write_response_for("/api/import", exported)

        self.assertEqual(status, 200, body)
        self.assertIsNone(self.repository.get_everyone_else_profile_id())

    def test_import_names_a_bad_unaffected_device(self) -> None:
        exported = self._export(self.server)
        exported["everyone_else"] = {"unaffected_devices": [{"mac_address": "nope"}]}

        status, _, body = self.server.write_response_for("/api/import", exported)

        self.assertEqual(status, 400)
        self.assertIn("everyone_else.unaffected_devices[0]", json.loads(body)["error"])

    def _export(self, server):
        status, _, body = server.response_for("/api/export")
        self.assertEqual(status, 200)
        exported = json.loads(body)
        exported.pop("exported_at")
        return exported

    def test_settings_move_between_installations_without_usage(self) -> None:
        self.server.write_response_for(
            "/api/schedules",
            {
                "id": "work",
                "device_id": "alice",
                "profile_id": "slow",
                "days_of_week": [0, 4],
                "start_minute": 540,
                "end_minute": 1_020,
            },
        )
        self.server.write_response_for(
            "/api/data-limits", {"device_id": "alice", "profile_id": "slow", "limit_bytes": 1_000}
        )
        self.repository.record_data_usage("alice", 700)
        self.server.write_response_for(
            "/api/policies",
            {"mac": {"mode": "denylist", "denylist": ["aa:bb:cc:dd:ee:ff"]}, "websites": {}},
        )
        exported = self._export(self.server)
        with ConfigRepository(":memory:") as other_repository:
            other = ControlServer(("127.0.0.1", 0), other_repository)
            try:
                status, _, _ = other.write_response_for("/api/import", exported)
                imported = self._export(other)
                used = other_repository.get_data_limit("alice").used_bytes
            finally:
                other.server_close()

        self.assertEqual(status, 200)
        self.assertEqual(imported, exported)
        self.assertEqual(used, 0)
        self.assertEqual(exported["data_limits"][0]["limit_bytes"], 1_000)
        self.assertNotIn("used_bytes", exported["data_limits"][0])

    def test_import_rejects_settings_that_do_not_fit_and_changes_nothing(self) -> None:
        exported = self._export(self.server)
        broken = dict(exported, devices=exported["devices"] + [
            {"id": "bob", "name": "Bob", "ipv4_address": "192.168.50.11", "profile_id": "ghost"}
        ])

        wrong_format_status, _, wrong_format_body = self.server.write_response_for(
            "/api/import", {"format": "something-else", "version": 1}
        )
        broken_status, _, broken_body = self.server.write_response_for("/api/import", broken)

        self.assertEqual(wrong_format_status, 400)
        self.assertIn("not a JitterSim settings export", json.loads(wrong_format_body)["error"])
        self.assertEqual(broken_status, 400)
        self.assertEqual(
            json.loads(broken_body)["error"],
            "invalid settings import: device bob uses the missing profile ghost",
        )
        self.assertEqual(self._export(self.server), exported)

    def test_imports_may_be_larger_than_ordinary_requests(self) -> None:
        self.server.admin_token = "admin-token"
        exported = self._export(self.server)
        exported["profiles"] = [
            dict(exported["profiles"][0], id=f"profile-{index:02d}", name="x" * 90)
            for index in range(40)
        ] + exported["profiles"]
        body = json.dumps(exported).encode("utf-8")
        self.assertGreater(len(body), 64 * 1024)

        response = self._send("POST", "/api/import", body=body)

        self.assertEqual(response["status"], 200)
        self.assertEqual(len(self.repository.list_profiles()), 41)

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
    def test_connected_label_auth_and_profile_isolation(self) -> None:
        self.server.admin_token = "admin-token"
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
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
                            b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"
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
                    client.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                    return client.recv(4096)
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def _get_over_http(self, path: str, headers=None):
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            response.read()
            connection.close()
            return response
        finally:
            self.server.shutdown()
            thread.join(timeout=5)

    @staticmethod
    def _inline_hash(tag: str) -> str:
        content = re.search(f"<{tag}>(.*?)</{tag}>", DASHBOARD_HTML, re.S).group(1)
        digest = hashlib.sha256(content.encode("utf-8")).digest()
        return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"

    def test_dashboard_only_runs_its_own_inline_script_and_style(self) -> None:
        self.server.admin_token = "admin-token"
        session = issue_session("admin-token", now=int(time.time()))
        response = self._get_over_http("/", headers={"Cookie": f"session={session}"})

        policy = response.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src " + self._inline_hash("script"), policy)
        self.assertIn("style-src " + self._inline_hash("style"), policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_responses_deny_framing_and_hide_the_server(self) -> None:
        response = self._get_over_http("/api/health")

        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        # Neither the product nor the Python version is named.
        self.assertEqual(response.getheader("Server"), "server")

    def test_request_logging_keeps_routine_polling_quiet(self) -> None:
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.client_address = ("127.0.0.1", 50000)
        handler.command = "GET"
        handler.requestline = "GET /api/state HTTP/1.1"

        with self.assertLogs("jittersim.api.server", level="DEBUG") as logs:
            handler.log_request(200)
            handler.command = "POST"
            handler.requestline = "POST /api/apply HTTP/1.1"
            handler.log_request(200)
            handler.log_request(401)

        self.assertEqual(
            [record.levelname for record in logs.records], ["DEBUG", "INFO", "WARNING"]
        )

    def test_log_level_can_be_chosen(self) -> None:
        self.assertEqual(_build_parser().parse_args([]).log_level, "INFO")
        self.assertEqual(_build_parser().parse_args(["--log-level", "DEBUG"]).log_level, "DEBUG")

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
                    }
                ],
                "everyone_else_profile_id": None,
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

    def test_connected_device_profile_assignment_keeps_a_reset_network_reset(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None
        )

        status, _, body = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["live_applied"])
        self.assertIsNone(self.server.helper_client.applied_plan)
        self.assertEqual(
            self.repository.get_device_by_ipv4_address("192.168.50.178").profile_id, "slow"
        )
        # Only an explicit Apply may undo a Reset, so the dashboard says so.
        assign = DASHBOARD_HTML.split("async function applyProfileToDevice(", 1)[1]
        self.assertIn("result.live_applied === false", assign.split("\n    }\n", 1)[0])

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

    def test_dashboard_keeps_javascript_escapes_intact(self) -> None:
        self.assertIn('.join("\\n")', DASHBOARD_HTML)
        self.assertIn("/^(\\d{1,2}):(\\d{2})$/", DASHBOARD_HTML)
        # The HTML is a raw string, so an escape written for a regular Python
        # string would reach the browser doubled.
        self.assertNotIn("\\\\", DASHBOARD_HTML)

    def test_dashboard_input_patterns_are_valid(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to compile the input patterns")
        patterns = re.findall(r' pattern="([^"]*)"', DASHBOARD_HTML)
        self.assertTrue(patterns)
        # Browsers compile pattern attributes with the "v" flag and skip
        # validation for any pattern it rejects.
        script = (
            "for (const pattern of JSON.parse(process.argv[1])) "
            "new RegExp('^(?:' + pattern + ')$', 'v');"
        )
        result = subprocess.run(
            [node, "-e", script, json.dumps(patterns)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_traffic_chart_labels_fit_on_the_canvas(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to draw the chart")
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]

        def function(name):
            body = script.split(f"    function {name}(", 1)[1].split("\n    }\n", 1)[0]
            return f"function {name}({body}\n}}\n"

        harness = (
            "const TRAFFIC_WINDOW_MS = 60000;\n"
            "const trafficSamples = [];\n"
            # An ISP plan of 100 Mbit/s sets the scale to 100 Mbit/s.
            "const latestTrafficReferences = {isp_download: 100};\n"
            "const drawn = [];\n"
            "const context = {\n"
            "  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},\n"
            "  lineTo() {}, stroke() {}, setLineDash() {},\n"
            "  measureText(text) { return {width: 7 * text.length}; },\n"
            "  fillText(text, x) { drawn.push({text, x, align: this.textAlign}); },\n"
            "};\n"
            "const elements = {\n"
            "  'traffic-chart': {getBoundingClientRect: () => ({width: 600, height: 320}),\n"
            "                    getContext: () => context},\n"
            "  'traffic-device': {value: 'all'},\n"
            "  'traffic-profile-upload-legend': {},\n"
            "  'traffic-profile-download-legend': {},\n"
            "};\n"
            "const element = (id) => elements[id];\n"
            "const window = {devicePixelRatio: 1};\n"
            + function("formatTrafficRate")
            + function("drawTrafficChart")
            + "drawTrafficChart();\n"
            "const clipped = drawn.filter(({text, x, align}) => {\n"
            "  const width = context.measureText(text).width;\n"
            "  const offset = {right: width, center: width / 2}[align] || 0;\n"
            "  return x - offset < 0;\n"
            "});\n"
            "if (!drawn.length || clipped.length) {\n"
            "  console.error(JSON.stringify(clipped));\n"
            "  process.exit(1);\n"
            "}\n"
        )
        result = subprocess.run(
            [node, "-e", harness], capture_output=True, text=True, check=False
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dashboard_compares_measurement_files(self) -> None:
        for fragment in (
            'id="measurement-files" type="file"',
            'id="measurement-table"',
            'id="measurement-chart"',
            'id="copy-measurements"',
            "function measurementMarkdown(runs)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, DASHBOARD_HTML)

    def test_dashboard_measurement_table_matches_the_command_line(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to build the table")
        runs = [
            {
                "label": "normal",
                "target": "1.1.1.1",
                "summary": {"sent": 50, "loss_percent": 0.0, "rtt_avg_ms": 12.4,
                            "rtt_p95_ms": 14.07, "jitter_ms": 0.81, "reordered": 0},
            },
            {
                "label": "3G | busy",
                "target": "1.1.1.1",
                "summary": {"sent": 50, "loss_percent": 33.3, "rtt_avg_ms": None,
                            "rtt_p95_ms": None, "jitter_ms": None, "reordered": 2},
                "download": {"mbit_per_s": 0.78},
            },
        ]
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        columns = re.search(r"const MEASUREMENT_COLUMNS = \[.*?\];", script, re.S).group(0)

        def function(name):
            body = script.split(f"    function {name}(", 1)[1].split("\n    }\n", 1)[0]
            return f"function {name}({body}\n}}\n"

        harness = (
            columns
            + "\n"
            + re.search(r"const ESCAPED_PIPE = .*?;", script).group(0)
            + "\n"
            + function("measurementMilliseconds")
            + function("measurementRow")
            + function("measurementMarkdown")
            + "process.stdout.write(measurementMarkdown(JSON.parse(process.argv[1])));\n"
        )
        result = subprocess.run(
            [node, "-e", harness, json.dumps(runs)], capture_output=True, text=True, check=False
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, comparison_table(runs))

    def _dashboard_functions(self, *names):
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        functions = ""
        for name in names:
            body = script.split(f"    function {name}(", 1)[1].split("\n    }\n", 1)[0]
            functions += f"function {name}({body}\n}}\n"
        return functions

    def test_dashboard_refuses_measurements_with_fields_of_the_wrong_type(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to check the files")
        good = {
            "format": "jittersim-measurement",
            "version": 1,
            "label": "normal",
            "target": "1.1.1.1",
            "samples": [{"sequence": 0, "rtt_ms": 12.5}],
            "summary": {"sent": 1, "loss_percent": 0.0, "rtt_avg_ms": 12.5,
                        "rtt_p95_ms": 12.5, "jitter_ms": None, "reordered": 0},
        }
        runs = [
            good,
            {**good, "summary": {**good["summary"], "loss_percent": "5"}},
            {**good, "download": {"url": "http://x"}},
            {**good, "samples": [{"rtt_ms": "slow"}]},
            {key: value for key, value in good.items() if key != "label"},
        ]
        harness = (
            self._dashboard_functions("validMeasurement")
            + "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(validMeasurement)));\n"
        )
        result = subprocess.run(
            [node, "-e", harness, json.dumps(runs)], capture_output=True, text=True, check=False
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True, False, False, False, False])

    def test_measurement_chart_shows_every_kind_of_run(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to draw the chart")
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        colors = re.search(r"const MEASUREMENT_COLORS = \[.*?\];", script, re.S).group(0)

        def drawn(runs):
            harness = (
                colors
                + "\nlet measurementRuns = JSON.parse(process.argv[1]);\n"
                "const texts = [];\n"
                "let dots = 0;\n"
                "const context = {\n"
                "  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},\n"
                "  lineTo() {}, stroke() {}, arc() { dots += 1; }, fill() {},\n"
                "  measureText(text) { return {width: 7 * text.length}; },\n"
                "  fillText(text) { texts.push(text); },\n"
                "};\n"
                "const canvas = {getBoundingClientRect: () => ({width: 600, height: 240}),\n"
                "                getContext: () => context};\n"
                "const element = () => canvas;\n"
                "const window = {devicePixelRatio: 1};\n"
                + self._dashboard_functions("drawMeasurementChart")
                + "drawMeasurementChart();\n"
                "console.log(JSON.stringify({texts, dots}));\n"
            )
            result = subprocess.run(
                [node, "-e", harness, json.dumps(runs)], capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        def run(*rtts):
            samples = [{"sequence": index, "rtt_ms": rtt} for index, rtt in enumerate(rtts)]
            return {"samples": samples}

        offline = drawn([run()])
        single = drawn([run(42.0)])
        fast = drawn([run(0.11, 0.12, 0.15)])

        self.assertIn("No replies to draw: every ping was lost.", offline["texts"])
        self.assertEqual(single["dots"], 1)
        labels = [text for text in fast["texts"] if text.endswith(" ms")]
        self.assertEqual(len(set(labels)), 5, labels)

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

    def test_dashboard_can_edit_and_delete_saved_items(self) -> None:
        for fragment in (
            'id="profile-table"',
            'id="device-table"',
            '<select id="device-profile" required></select>',
            'id="profile-cancel-edit"',
            'id="device-cancel-edit"',
            "function fillProfileForm(profile)",
            "function fillDeviceForm(device)",
            "function editSchedule(schedule)",
            "function slugify(value)",
            '"/api/profiles/delete"',
            '"/api/devices/delete"',
            '"/api/schedules/delete"',
            '"/api/data-limits/delete"',
            '"/api/group-data-limits/delete"',
            '"/api/service-quotas/delete"',
            '"/api/time-quotas/delete"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, DASHBOARD_HTML)
        self.assertNotIn('<pre id="profiles">', DASHBOARD_HTML)
        self.assertNotIn('<pre id="devices">', DASHBOARD_HTML)

    def test_dashboard_keeps_the_id_when_renaming_a_saved_item(self) -> None:
        for function, prefix in (
            ("editGroup(group)", "group"),
            ("editServiceQuota(quota)", "service-quota"),
            ("editTimeQuota(quota)", "time-quota"),
        ):
            with self.subTest(function=function):
                body = DASHBOARD_HTML.split(f"function {function} {{", 1)[1]
                body = body.split("\n    }\n", 1)[0]
                self.assertIn(f'element("{prefix}-id").dataset.typed = "true";', body)

    def test_dashboard_keeps_profile_settings_the_form_does_not_show(self) -> None:
        submit = DASHBOARD_HTML.split('element("profile-form").addEventListener("submit"', 1)[1]
        submit = submit.split("\n    });\n", 1)[0]

        for fragment in (
            "const kept = savedProfileRandomness;",
            "enabled_probability: kept.reroll_interval_randomness.enabled_probability",
            "outage_duration_randomness: kept.outage_duration_randomness",
            "outage_interval_randomness: kept.outage_interval_randomness",
            "variation_percent: kept.website_randomness.variation_percent",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, submit)
        self.assertIn('element("profile-form").addEventListener("reset"', DASHBOARD_HTML)

    def test_dashboard_shows_each_device_condition(self) -> None:
        table = DASHBOARD_HTML.split("function renderDeviceTable() {", 1)[1]
        table = table.split("\n    }\n", 1)[0]

        self.assertIn('["Device", "Address", "Profile", "Condition"]', table)
        self.assertIn("deviceConditionCell(device.id)", table)
        self.assertIn('request("/api/conditions", "GET")', DASHBOARD_HTML)
        self.assertIn('id="device-conditions-note"', DASHBOARD_HTML)
        refresh = DASHBOARD_HTML.split("async function refresh() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("loadConditions()", refresh)

    def test_dashboard_condition_lookup_ignores_built_in_names(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to run the lookup")
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        labels = re.search(r"const CONDITION_LABELS = \{.*?\};", script, re.S).group(0)
        def function(name):
            body = script.split(f"    function {name}(", 1)[1].split("\n    }\n", 1)[0]
            return f"function {name}({body}\n}}\n"

        harness = (
            labels
            + "\nconst deviceConditions = "
            + "{applied: true, devices: {alice: {condition: 'normal'}}};\n"
            + function("conditionCell")
            + function("deviceConditionCell")
            # A device may be called "constructor", like a built-in property.
            + "console.log(JSON.stringify([deviceConditionCell('constructor'),"
            + " deviceConditionCell('alice')]));\n"
        )
        result = subprocess.run([node, "-e", harness], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["—", ["Normal", "No network effects"]])

    def test_dashboard_has_the_everyone_else_controls(self) -> None:
        for fragment in (
            'id="everyone-else-form"',
            'id="everyone-else-profile"',
            'id="unaffected-form"',
            'id="unaffected-table"',
            'request("/api/everyone-else", "GET")',
            '"/api/unaffected-devices/delete"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, DASHBOARD_HTML)
        refresh = DASHBOARD_HTML.split("async function refresh() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("loadEveryoneElse()", refresh)

    def _run_dashboard_functions(self, names, setup, calls):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to run the dashboard functions")
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]

        def function(name):
            for kind in ("async function", "function"):
                marker = f"    {kind} {name}("
                if marker in script:
                    body = script.split(marker, 1)[1].split("\n    }\n", 1)[0]
                    return f"{kind} {name}({body}\n}}\n"
            raise AssertionError(f"{name} is not in the dashboard script")

        harness = setup + "".join(function(name) for name in names) + calls
        result = subprocess.run([node, "-e", harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_dashboard_labels_what_each_connected_client_gets(self) -> None:
        labels = self._run_dashboard_functions(
            ["profileName", "connectedTreatment"],
            "const profiles = [{id: 'slow', name: 'Slow'}, {id: '3g', name: '3G'}];\n"
            "let everyoneElse = {profile_id: null, unaffected_devices: []};\n",
            "const labels = [\n"
            "  connectedTreatment({unaffected: true, assigned_profile_id: null}),\n"
            "  connectedTreatment({unaffected: false, assigned_profile_id: 'slow'}),\n"
            "  connectedTreatment({unaffected: false, assigned_profile_id: null}),\n"
            # A saved device keeps its own profile even when its address is listed.
            "  connectedTreatment({unaffected: true, assigned_profile_id: 'slow'}),\n"
            "];\n"
            "everyoneElse = {profile_id: '3g', active_profile_id: '3g'};\n"
            "labels.push(connectedTreatment({unaffected: false, assigned_profile_id: null}));\n"
            # A schedule can give everyone else a profile for now.
            "everyoneElse = {profile_id: null, active_profile_id: 'slow'};\n"
            "labels.push(connectedTreatment({unaffected: false, assigned_profile_id: null}));\n"
            "console.log(JSON.stringify(labels));\n",
        )

        self.assertEqual(
            labels,
            [
                "Unaffected",
                "Saved device: Slow",
                "Normal connection",
                "Saved device: Slow (also on the unaffected list)",
                "Everyone else: 3G",
                "Everyone else: Slow",
            ],
        )

    def test_schedules_can_target_devices_groups_and_everyone_else(self) -> None:
        targets = (
            "[{device_id: 'alice', group_id: null, everyone_else: false},\n"
            " {device_id: null, group_id: 'family', everyone_else: false},\n"
            " {device_id: null, group_id: null, everyone_else: true},\n"
            " {device_id: null, group_id: 'gone', everyone_else: false}]"
        )
        result = self._run_dashboard_functions(
            [
                "deviceName", "scheduleTargetFields", "scheduleTargetValue",
                "scheduleTargetName", "scheduleTargetOptions",
            ],
            "const devices = [{id: 'alice', name: 'Alice', ipv4_address: '192.168.50.10'}];\n"
            "let groups = [{id: 'family', name: 'Family', device_ids: ['alice']}];\n",
            f"const targets = {targets};\n"
            "const fields = ['device:alice', 'group:family', 'everyone-else'].map(\n"
            "  (value) => scheduleTargetFields(value));\n"
            "const options = scheduleTargetOptions();\n"
            "const names = targets.map(scheduleTargetName);\n"
            "groups = [];\n"
            "console.log(JSON.stringify({\n"
            "  fields: fields,\n"
            "  values: targets.map(scheduleTargetValue),\n"
            "  names: names,\n"
            "  options: options,\n"
            "  withoutGroups: scheduleTargetOptions().map((option) => option.value),\n"
            "}));\n",
        )

        self.assertEqual(
            result["fields"],
            [
                {"device_id": "alice", "group_id": None, "everyone_else": False},
                {"device_id": None, "group_id": "family", "everyone_else": False},
                {"device_id": None, "group_id": None, "everyone_else": True},
            ],
        )
        self.assertEqual(
            result["values"], ["device:alice", "group:family", "everyone-else", "group:gone"]
        )
        self.assertEqual(
            result["names"], ["Alice", "Group Family", "Everyone else", "Group gone"]
        )
        self.assertEqual(
            result["options"],
            [
                {"group": "Devices", "value": "device:alice", "label": "Alice (192.168.50.10)"},
                {"group": "Groups", "value": "group:family", "label": "Family"},
                {"group": None, "value": "everyone-else", "label": "Everyone else"},
            ],
        )
        self.assertEqual(result["withoutGroups"], ["device:alice", "everyone-else"])

    def test_an_everyone_else_schedule_counts_as_giving_it_a_profile(self) -> None:
        result = self._run_dashboard_functions(
            ["everyoneElseAssigned"],
            "let everyoneElse = {profile_id: null};\n"
            "let savedSchedules = [{everyone_else: false, enabled: true}];\n",
            "const answers = [everyoneElseAssigned()];\n"
            "savedSchedules.push({everyone_else: true, enabled: false});\n"
            "answers.push(everyoneElseAssigned());\n"
            "savedSchedules.push({everyone_else: true, enabled: true});\n"
            "answers.push(everyoneElseAssigned());\n"
            "savedSchedules = [];\n"
            "everyoneElse = {profile_id: 'slow'};\n"
            "answers.push(everyoneElseAssigned());\n"
            "console.log(JSON.stringify(answers));\n",
        )

        self.assertEqual(result, [False, False, True, True])

    def test_dashboard_messages_say_when_a_change_takes_effect(self) -> None:
        messages = self._run_dashboard_functions(
            ["savedChangeMessage"],
            "let helperConfigured = true;\n",
            "const messages = [\n"
            "  savedChangeMessage('Saved.', {live_applied: true}),\n"
            "  savedChangeMessage('Saved.', {live_applied: false}),\n"
            "];\n"
            "helperConfigured = false;\n"
            "messages.push(savedChangeMessage('Saved.', {live_applied: false}));\n"
            "console.log(JSON.stringify(messages));\n",
        )

        self.assertEqual(
            messages,
            [
                "Saved.",
                "Saved. The network is reset, so apply the configuration to use it.",
                "Saved. It takes effect when the dashboard runs in hardware mode.",
            ],
        )

    def test_dashboard_profile_menus_keep_the_everyone_else_choice(self) -> None:
        values = self._run_dashboard_functions(
            ["loadProfiles"],
            "let profiles = [];\n"
            "const everyoneElse = {profile_id: '3g', unaffected_devices: []};\n"
            "const elements = {};\n"
            "function element(id) {\n"
            "  elements[id] = elements[id] || {\n"
            "    options: [], value: '',\n"
            "    replaceChildren() { this.options = []; this.value = ''; },\n"
            "    appendChild(option) {\n"
            "      this.options.push(option);\n"
            "      if (this.options.length === 1) this.value = option.value;\n"
            "    },\n"
            "  };\n"
            "  return elements[id];\n"
            "}\n"
            "const document = {createElement: () => ({value: '', textContent: ''})};\n"
            "async function request() {\n"
            "  return {profiles: [{id: 'normal', name: 'Normal'}, {id: '3g', name: '3G'}]};\n"
            "}\n"
            "function renderProfileTable() {}\n",
            "loadProfiles().then(() => {\n"
            "  console.log(JSON.stringify(elements['everyone-else-profile'].value));\n"
            "});\n",
        )

        # Rebuilding the menu must not show None, or Save would clear the profile.
        self.assertEqual(values, "3g")

    def test_dashboard_relabels_clients_when_everyone_else_changes(self) -> None:
        load = DASHBOARD_HTML.split("async function loadEveryoneElse() {", 1)[1]
        load = load.split("\n    }\n", 1)[0]

        self.assertIn("rerenderConnectedDevices()", load)
        self.assertIn("function rerenderConnectedDevices()", DASHBOARD_HTML)

    def test_dashboard_lists_everyone_else_with_the_devices(self) -> None:
        rows = self._run_dashboard_functions(
            ["profileName", "conditionCell", "everyoneElseRow"],
            "const CONDITION_LABELS = {normal: 'Normal', degraded: 'Degraded'};\n"
            "const profiles = [{id: '3g', name: '3G'}];\n"
            "let everyoneElse = {profile_id: null, unaffected_devices: []};\n"
            "let deviceConditions = {applied: false, devices: {}, everyone_else: null};\n",
            "const rows = [everyoneElseRow()];\n"
            "everyoneElse = {profile_id: '3g', unaffected_devices: [{mac_address: 'x'}]};\n"
            "deviceConditions.everyone_else = "
            "{condition: 'degraded', reasons: ['upload: delay 100 ms']};\n"
            "rows.push(everyoneElseRow().cells);\n"
            "console.log(JSON.stringify(rows));\n",
        )

        self.assertEqual(
            rows,
            [
                None,
                [
                    ["Everyone else", "Test Wi-Fi clients that aren't saved or unaffected"],
                    ["Any address", "1 unaffected"],
                    "3G",
                    ["Degraded", "upload: delay 100 ms"],
                ],
            ],
        )

    def test_dashboard_offers_presets_and_burst_loss(self) -> None:
        for fragment in (
            'id="profile-preset"',
            "const PROFILE_PRESETS = [",
            'name: "Offline"',
            'id="upload-loss-burst"',
            'id="download-loss-burst"',
            'loss_burst_packets: Number(element(prefix + "-loss-burst").value)',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, DASHBOARD_HTML)

    def test_profiles_accept_burst_loss(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/profiles",
            {
                "id": "bursty",
                "name": "Bursty",
                "upload": {"loss_percent": 10, "loss_burst_packets": 4},
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["profile"]["upload"]["loss_burst_packets"], 4)
        self.assertEqual(self.repository.get_profile("bursty").upload.loss_burst_packets, 4)

    def test_dashboard_can_export_and_import_settings(self) -> None:
        for fragment in (
            'id="export-settings"',
            'id="import-settings-button"',
            'id="import-settings" type="file"',
            'request("/api/export", "GET")',
            'request("/api/import", "POST", settings)',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, DASHBOARD_HTML)

    def test_dashboard_shows_queue_drops_in_live_traffic(self) -> None:
        self.assertIn('id="traffic-drop-rate"', DASHBOARD_HTML)
        self.assertIn('id="traffic-queued"', DASHBOARD_HTML)
        self.assertIn("function selectedQueueTotals(queues)", DASHBOARD_HTML)

    def _small_screen_css(self) -> str:
        return DASHBOARD_HTML.split("@media (max-width: 700px) {", 1)[1].split("\n    }\n", 1)[0]

    def test_dashboard_fits_a_phone_screen(self) -> None:
        small = self._small_screen_css()

        # A plain 1fr column grows to its widest content, such as the JSON boxes.
        self.assertIn(".content-grid, .data-grid { grid-template-columns: minmax(0, 1fr); }", small)
        self.assertNotIn("grid-template-columns: 1fr;", small)

    def test_connected_client_buttons_wrap_on_a_phone(self) -> None:
        small = self._small_screen_css()

        # "Keep unaffected" used to push the Overview to 522 pixels on a 375-pixel phone.
        self.assertIn(".connected-controls { grid-column: 1 / -1; flex-wrap: wrap; }", small)
        self.assertIn(".connected-controls select { flex: 1 1 100%; min-width: 0; }", small)

    def test_help_text_takes_no_room_until_shown(self) -> None:
        tooltip = DASHBOARD_HTML.split(".help-icon::after {", 1)[1].split("}", 1)[0]

        # An invisible tooltip still widens the page, so hidden help leaves the layout.
        self.assertIn("display: none;", tooltip)
        self.assertNotIn("visibility: hidden;", tooltip)
        self.assertIn(
            ".help-icon:hover::after, .help-icon:focus-visible::after { display: block; }",
            DASHBOARD_HTML,
        )
        # On a phone the help spans its field instead of starting at the icon.
        small = self._small_screen_css()
        self.assertIn("label, .randomness-title { position: relative; }", small)
        self.assertIn(".help-icon { position: static; }", small)
        self.assertIn(".help-icon::after { right: 0; width: auto; }", small)

    def test_hidden_fields_stay_hidden(self) -> None:
        # label { display: grid } used to override the hidden attribute.
        self.assertIn("[hidden] { display: none !important; }", DASHBOARD_HTML)
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        startup = script.split(
            '["data-limit", "group-data-limit", "service-quota", "time-quota"].forEach(', 1
        )[1].split("});", 1)[0]
        self.assertIn('addEventListener("change", () => syncQuotaReset(prefix));', startup)
        self.assertIn("syncQuotaReset(prefix);", startup.split("addEventListener", 1)[1])

    def test_devices_tab_cards_keep_their_gap(self) -> None:
        self.assertIn(".data-grid + .content-grid { margin-top: 1rem; }", DASHBOARD_HTML)

    def test_the_controls_bar_stays_slim_on_a_phone(self) -> None:
        small = self._small_screen_css()

        self.assertIn('<p class="action-description">', DASHBOARD_HTML)
        self.assertIn(".action-description { display: none; }", small)
        self.assertIn(".action-buttons button { flex: 1;", small)

    TABS = ("overview", "devices", "profiles", "rules", "measurements", "setup", "diagnostics")

    def _panel(self, name: str) -> str:
        start = DASHBOARD_HTML.index(f'<div class="tab-panel" id="tab-{name}"')
        following = DASHBOARD_HTML.find('<div class="tab-panel" id="tab-', start + 1)
        end = DASHBOARD_HTML.index("<script>") if following == -1 else following
        return DASHBOARD_HTML[start:end]

    def test_dashboard_sections_live_in_tabs(self) -> None:
        nav = DASHBOARD_HTML.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
        for name in self.TABS:
            with self.subTest(tab=name):
                self.assertIn(f'href="#{name}"', nav)
                self.assertIn(f'id="tab-{name}"', DASHBOARD_HTML)
        expected = {
            "overview": ['action="/sign-out"', 'id="connected-devices"', 'id="traffic-chart"'],
            "devices": [
                'id="device-table"', "<h2>Assign a device</h2>", "<h2>Group devices</h2>",
                "<h2>Everyone else</h2>",
            ],
            "profiles": ['id="profile-table"', "<h2>Save profile</h2>"],
            "rules": [
                "<h2>Network access rules</h2>", "<h2>Schedule a profile</h2>",
                "Run a profile timer", "Device data limit", "Service time limit",
                "Traffic discovery",
            ],
            "measurements": ['id="measurement-files"'],
            "setup": ["Upstream router connection", "Manual Pi access-point fallback"],
            "diagnostics": [
                'id="plan"', 'id="state"', 'id="diagnostics-output"', 'id="action-history"',
            ],
        }
        for name, fragments in expected.items():
            panel = self._panel(name)
            positions = []
            for fragment in fragments:
                with self.subTest(tab=name, fragment=fragment):
                    self.assertIn(fragment, panel)
                    positions.append(panel.index(fragment))
            # Lists come before the forms that edit them.
            self.assertEqual(positions, sorted(positions), name)

    def test_every_card_sits_in_one_tab(self) -> None:
        structure = DashboardStructure()
        structure.feed(DASHBOARD_HTML)
        structure.close()

        self.assertEqual(structure.errors, [])
        self.assertEqual(structure.open_tags, [])
        # Sibling panels: hiding one tab never hides another.
        self.assertEqual(structure.panels, [f"tab-{name}" for name in self.TABS])
        self.assertEqual(structure.panel_parents, {("html", "body")})
        # Only the controls bar stays outside the tabs.
        self.assertEqual(
            [classes for classes, panel in structure.cards if panel is None],
            [["card", "action-bar"]],
        )
        self.assertEqual(
            structure.headings,
            {
                None: ["Network controls"],
                "tab-overview": [
                    "Signed in", "Getting started", "Connected clients", "Live traffic",
                ],
                "tab-devices": ["Devices", "Assign a device", "Group devices", "Everyone else"],
                "tab-profiles": ["Profiles", "Save profile"],
                "tab-rules": [
                    "Network access rules", "Schedule a profile", "Run a profile timer",
                    "Progression mode", "Device data limit", "Shared group data limit",
                    "Browser service profile", "Service data limit", "Service time limit",
                    "Traffic discovery",
                ],
                "tab-measurements": ["Measurements"],
                "tab-setup": ["Upstream router connection", "Manual Pi access-point fallback"],
                "tab-diagnostics": [
                    "Compiled plan", "Runtime state", "Access rules", "Diagnostics",
                    "Backup and action history",
                ],
            },
        )

    def test_no_tab_shares_its_name_with_an_element(self) -> None:
        # The address #<tab> would scroll the page to an element with that ID.
        ids = set(re.findall(r'\bid="([^"]+)"', DASHBOARD_HTML))
        self.assertEqual(ids & set(self.TABS), set())

    def test_dashboard_tabs_follow_the_address(self) -> None:
        result = self._run_dashboard_functions(
            ["tabFromHash", "showTab"],
            "const DASHBOARD_TABS = " + json.dumps(list(self.TABS)) + ";\n"
            "const panels = {};\n"
            "const element = (id) => (panels[id] = panels[id] || {hidden: false});\n"
            "const links = DASHBOARD_TABS.map((tab) => ({\n"
            "  href: '#' + tab, current: null,\n"
            "  getAttribute(name) { return this.href; },\n"
            "  setAttribute(name, value) { this.current = value; },\n"
            "  removeAttribute() { this.current = null; },\n"
            "}));\n"
            "const document = {querySelectorAll: () => links};\n"
            "const drawn = [];\n"
            "const revealed = [];\n"
            "function drawTrafficChart() { drawn.push('traffic'); }\n"
            "function drawMeasurementChart() { drawn.push('measurements'); }\n"
            "function revealTab(link) { revealed.push(link.href); }\n",
            "showTab('devices');\n"
            "console.log(JSON.stringify({\n"
            "  hidden: DASHBOARD_TABS.filter((tab) => panels['tab-' + tab].hidden),\n"
            "  current: links.filter((link) => link.current).map((link) => link.href),\n"
            "  revealed: revealed,\n"
            "  fallback: [tabFromHash('#nope'), tabFromHash(''), tabFromHash('#rules')],\n"
            "}));\n",
        )

        self.assertEqual(result["hidden"], [tab for tab in self.TABS if tab != "devices"])
        self.assertEqual(result["current"], ["#devices"])
        self.assertEqual(result["revealed"], ["#devices"])
        self.assertEqual(result["fallback"], ["overview", "overview", "rules"])

    def test_the_chosen_tab_scrolls_into_the_tab_bar(self) -> None:
        result = self._run_dashboard_functions(
            ["revealTab"],
            "const bar = {scrollLeft: 100,\n"
            "  getBoundingClientRect: () => ({left: 16, right: 357})};\n"
            "const tab = (left, right) => ({parentElement: bar,\n"
            "  getBoundingClientRect: () => ({left: left, right: right})});\n",
            "const scrolls = [[400, 480], [-40, 30], [100, 180]].map(([left, right]) => {\n"
            "  bar.scrollLeft = 100;\n"
            "  revealTab(tab(left, right));\n"
            "  return bar.scrollLeft;\n"
            "});\n"
            "console.log(JSON.stringify(scrolls));\n",
        )

        # Past the right edge, past the left edge, already in view. The tab keeps
        # about the bar's padding clear of the edge.
        self.assertEqual(result, [231, 36, 100])

    def test_tab_links_leave_modified_clicks_to_the_browser(self) -> None:
        result = self._run_dashboard_functions(
            ["followTabLink"],
            "const shown = [];\n"
            "const pushed = [];\n"
            "function showTab(name) { shown.push(name); }\n"
            "const window = {location: {hash: '#overview'},\n"
            "  history: {pushState(state, title, url) { pushed.push(url); }}};\n"
            "const click = (extra) => Object.assign({button: 0, metaKey: false, ctrlKey: false,\n"
            "  shiftKey: false, altKey: false, prevented: false,\n"
            "  preventDefault() { this.prevented = true; }}, extra);\n",
            "const events = [click({}), click({metaKey: true}), click({ctrlKey: true}),\n"
            "  click({shiftKey: true}), click({altKey: true}), click({button: 1})];\n"
            "const followed = events.map((event) => followTabLink(event, 'devices'));\n"
            "console.log(JSON.stringify({followed: followed,\n"
            "  prevented: events.map((event) => event.prevented),\n"
            "  shown: shown, pushed: pushed}));\n",
        )

        # A modified click opens the tab in a new browser tab instead.
        self.assertEqual(result["followed"], [True, False, False, False, False, False])
        self.assertEqual(result["prevented"], [True, False, False, False, False, False])
        self.assertEqual(result["shown"], ["devices"])
        self.assertEqual(result["pushed"], ["#devices"])

    def test_dashboard_folds_raw_json_away(self) -> None:
        # The compiled plan alone used to be about 9,000 pixels tall.
        for element_id in ("plan", "state", "policies"):
            with self.subTest(element_id=element_id):
                self.assertRegex(
                    DASHBOARD_HTML,
                    r'<details class="raw-details">\s*<summary>Show details</summary>\s*'
                    f'<pre id="{element_id}">',
                )

    def test_dashboard_getting_started_follows_the_setup(self) -> None:
        result = self._run_dashboard_functions(
            ["gettingStartedSteps"],
            "const state = (changes) => Object.assign({profiles: 0, devices: 0, connected: 0,\n"
            "  everyoneElseProfile: null, lastAction: null, canApply: true}, changes);\n"
            "const done = (changes) => gettingStartedSteps(state(changes)).map((s) => s.done);\n",
            "console.log(JSON.stringify({\n"
            "  fresh: done({}),\n"
            "  connected: done({connected: 1}),\n"
            "  everyone: done({profiles: 2, everyoneElseProfile: '3g', lastAction: 'reset'}),\n"
            "  saved: done({profiles: 1, devices: 1, lastAction: 'apply'}),\n"
            "  configurationOnly: done({profiles: 1, everyoneElseProfile: '3g',\n"
            "    canApply: false}),\n"
            "  tabs: gettingStartedSteps(state({})).map((step) => step.tab),\n"
            "}));\n",
        )

        self.assertEqual(result["fresh"], [False, False, False, False])
        self.assertEqual(result["connected"], [False, True, False, False])
        # Saved settings finish the list whether or not a device is connected right now.
        self.assertEqual(result["everyone"], [True, True, True, True])
        self.assertEqual(result["saved"], [True, True, True, True])
        # Configuration-only mode can't apply anything, so the list skips that step.
        self.assertEqual(result["configurationOnly"], [True, True, True])
        self.assertEqual(result["tabs"], ["profiles", None, "devices", None])

    def test_getting_started_waits_for_the_dashboard_and_keeps_focus(self) -> None:
        result = self._run_dashboard_functions(
            ["gettingStartedSteps", "renderGettingStarted", "everyoneElseAssigned"],
            "let savedSchedules = [];\n"
            "let dashboardLoaded = false;\n"
            "let gettingStartedKey = '';\n"
            "let helperConfigured = true;\n"
            "let profiles = [];\n"
            "let devices = [];\n"
            "let everyoneElse = {profile_id: null};\n"
            "let lastRuntimeState = {last_action: null};\n"
            "let lastConnectedDevices = {available: true, devices: []};\n"
            "const focused = [];\n"
            "const fake = (extra) => Object.assign({children: [], listeners: {}, hidden: false,\n"
            "  appendChild(child) { this.children.push(child); },\n"
            "  append(...children) { this.children.push(...children); },\n"
            "  addEventListener(type, listener) { this.listeners[type] = listener; },\n"
            "  focus() { focused.push(this.id); }}, extra);\n"
            "let rebuilds = 0;\n"
            "const list = fake({\n"
            "  replaceChildren() { rebuilds += 1; this.children = []; },\n"
            "  contains(node) {\n"
            "    return this.children.some(\n"
            "      (item) => item === node || item.children.includes(node));\n"
            "  }});\n"
            "const elements = {'getting-started': fake({hidden: true}),\n"
            "  'getting-started-steps': list, 'tab-profiles': fake({id: 'tab-profiles'})};\n"
            "const element = (id) => elements[id];\n"
            "const document = {activeElement: null, createElement: () => fake({}),\n"
            "  createTextNode: (text) => ({text: text})};\n"
            "const followed = [];\n"
            "function followTabLink(event, name) { followed.push(name); return true; }\n",
            "const seen = [];\n"
            "const snapshot = () => seen.push([elements['getting-started'].hidden, rebuilds]);\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "dashboardLoaded = true;\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "const link = list.children[0].children[1];\n"
            "document.activeElement = link;\n"
            "profiles = [{id: 'slow'}];\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "link.listeners.click({});\n"
            "document.activeElement = null;\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "everyoneElse = {profile_id: 'slow'};\n"
            "lastRuntimeState = {last_action: 'apply'};\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "lastConnectedDevices = {available: true, devices: [{mac_address: 'a'}]};\n"
            "renderGettingStarted();\n"
            "snapshot();\n"
            "console.log(JSON.stringify({seen: seen, followed: followed, focused: focused}));\n",
        )

        self.assertEqual(
            result["seen"],
            [
                # Nothing shows until every saved setting has loaded.
                [True, 0],
                [False, 1],
                # Polls with the same steps leave the list, and keyboard focus, alone.
                [False, 1],
                [False, 1],
                [False, 2],
                [True, 3],
                # A client connecting doesn't change a finished list.
                [True, 3],
            ],
        )
        self.assertEqual(result["followed"], ["profiles"])
        # The list is out of sight after its link switches tabs, so focus moves on.
        self.assertEqual(result["focused"], ["tab-profiles"])

    def test_dashboard_getting_started_card_sits_on_the_overview(self) -> None:
        overview = self._panel("overview")
        self.assertIn('id="getting-started"', overview)
        self.assertLess(
            overview.index('id="getting-started"'), overview.index('id="connected-devices"')
        )
        refresh = DASHBOARD_HTML.split("async function refresh() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("renderGettingStarted()", refresh)

    def test_forms_offer_the_connected_devices(self) -> None:
        pickers = {
            "unaffected-picker": ('id="unaffected-form"', 'id="unaffected-mac"'),
            "device-picker": ('id="device-form"', 'id="device-ip"'),
            "mac-picker": ('id="policy-form"', 'id="mac-allowlist"'),
        }
        for picker, (form, field) in pickers.items():
            with self.subTest(picker=picker):
                form_start = DASHBOARD_HTML.index(form)
                form_end = DASHBOARD_HTML.index("</form>", form_start)
                picker_at = DASHBOARD_HTML.index(f'id="{picker}"')
                self.assertTrue(form_start < picker_at < form_end)
                self.assertLess(picker_at, DASHBOARD_HTML.index(field))
                refresh = DASHBOARD_HTML[picker_at:form_end]
                self.assertIn('data-refresh-connected', refresh)
        self.assertIn('id="mac-add-allowed"', DASHBOARD_HTML)
        self.assertIn('id="mac-add-blocked"', DASHBOARD_HTML)

    def test_picking_a_device_fills_the_forms(self) -> None:
        result = self._run_dashboard_functions(
            [
                "slugify", "bindAutoId", "pickedDeviceFields", "pickedConnectedDevice",
                "fillDeviceFromPicker", "fillUnaffectedFromPicker",
            ],
            "const lastConnectedDevices = {available: true, devices: [\n"
            "  {mac_address: 'aa:aa:aa:aa:aa:01', ipv4_address: '192.168.50.23',\n"
            "   saved_display_name: 'Sam phone', hostname: 'iPhone'}]};\n"
            "const field = () => ({value: '', readOnly: false, dataset: {}, listeners: {},\n"
            "  addEventListener(type, listener) {\n"
            "    (this.listeners[type] = this.listeners[type] || []).push(listener);\n"
            "  },\n"
            "  dispatchEvent(event) {\n"
            "    (this.listeners[event.type] || []).forEach((listener) => listener(event));\n"
            "  }});\n"
            "const elements = {};\n"
            "['device-picker', 'device-name', 'device-id', 'device-ip', 'device-mac',\n"
            "  'unaffected-picker', 'unaffected-mac', 'unaffected-name'].forEach(\n"
            "  (id) => { elements[id] = field(); });\n"
            "const element = (id) => elements[id];\n",
            "bindAutoId('device-name', 'device-id');\n"
            "const values = (ids) => ids.map((id) => elements[id].value);\n"
            "const deviceFields = ['device-name', 'device-id', 'device-ip', 'device-mac'];\n"
            "elements['device-picker'].value = 'aa:aa:aa:aa:aa:01';\n"
            "fillDeviceFromPicker();\n"
            "const fresh = values(deviceFields);\n"
            "elements['device-id'].value = 'my-id';\n"
            "elements['device-id'].dispatchEvent({type: 'input'});\n"
            "fillDeviceFromPicker();\n"
            "const typed = values(deviceFields);\n"
            "elements['device-id'].value = 'saved-id';\n"
            "elements['device-id'].dataset.typed = '';\n"
            "elements['device-id'].readOnly = true;\n"
            "fillDeviceFromPicker();\n"
            "const editing = values(deviceFields);\n"
            "elements['unaffected-picker'].value = 'aa:aa:aa:aa:aa:01';\n"
            "fillUnaffectedFromPicker();\n"
            "console.log(JSON.stringify({fresh: fresh, typed: typed, editing: editing,\n"
            "  unaffected: values(['unaffected-mac', 'unaffected-name'])}));\n",
        )

        filled = ["Sam phone", "sam-phone", "192.168.50.23", "aa:aa:aa:aa:aa:01"]
        self.assertEqual(result["fresh"], filled)
        # The ID follows the name unless it was typed by hand or is being edited.
        self.assertEqual(result["typed"], ["Sam phone", "my-id"] + filled[2:])
        self.assertEqual(result["editing"], ["Sam phone", "saved-id"] + filled[2:])
        self.assertEqual(result["unaffected"], ["aa:aa:aa:aa:aa:01", "Sam phone"])

    def test_adding_a_picked_device_to_a_mac_list(self) -> None:
        result = self._run_dashboard_functions(
            ["addMacToList", "pickedConnectedDevice", "addPickedMac"],
            "let lastConnectedDevices = {available: true,\n"
            "  devices: [{mac_address: 'aa:aa:aa:aa:aa:01'}]};\n"
            "const elements = {'mac-picker': {value: ''},\n"
            "  'mac-allowlist': {value: 'bb:bb:bb:bb:bb:bb'}};\n"
            "const element = (id) => elements[id];\n"
            "const messages = [];\n"
            "function showMessage(text, kind) { messages.push([text, kind]); }\n",
            "addPickedMac('mac-allowlist');\n"
            "elements['mac-picker'].value = 'aa:aa:aa:aa:aa:01';\n"
            "addPickedMac('mac-allowlist');\n"
            "addPickedMac('mac-allowlist');\n"
            "lastConnectedDevices = {available: false, devices: []};\n"
            "addPickedMac('mac-allowlist');\n"
            "console.log(JSON.stringify({list: elements['mac-allowlist'].value,\n"
            "  messages: messages}));\n",
        )

        self.assertEqual(result["list"], "bb:bb:bb:bb:bb:bb, aa:aa:aa:aa:aa:01")
        self.assertEqual(
            result["messages"],
            [
                ["Choose a connected device first.", "error"],
                # Configuration-only mode can't list connected devices at all.
                ["Connected devices are unavailable, so type the address in the list.", "error"],
            ],
        )

    def test_picking_a_connected_device_fills_the_form(self) -> None:
        result = self._run_dashboard_functions(
            ["connectedDeviceOptions", "pickedDeviceFields", "addMacToList"],
            "const devices = [\n"
            "  {mac_address: 'aa:aa:aa:aa:aa:01', ipv4_address: '192.168.50.23',\n"
            "   display_name: 'Sam phone', saved_display_name: 'Sam phone', hostname: 'iPhone'},\n"
            "  {mac_address: 'aa:aa:aa:aa:aa:02', ipv4_address: '192.168.50.24',\n"
            "   display_name: 'Unnamed client', saved_display_name: '', hostname: null},\n"
            "  {mac_address: null, ipv4_address: '192.168.50.25', display_name: 'Ghost'},\n"
            "];\n",
            "console.log(JSON.stringify({\n"
            "  options: connectedDeviceOptions(devices),\n"
            "  named: pickedDeviceFields(devices[0]),\n"
            "  unnamed: pickedDeviceFields(devices[1]),\n"
            "  lists: [\n"
            "    addMacToList('', 'aa:aa:aa:aa:aa:01'),\n"
            "    addMacToList('bb:bb:bb:bb:bb:bb', 'aa:aa:aa:aa:aa:01'),\n"
            "    addMacToList('AA:AA:AA:AA:AA:01, bb:bb:bb:bb:bb:bb', 'aa:aa:aa:aa:aa:01'),\n"
            "  ],\n"
            "}));\n",
        )

        self.assertEqual(
            result["options"],
            [
                {
                    "value": "aa:aa:aa:aa:aa:01",
                    "label": "Sam phone · 192.168.50.23 · aa:aa:aa:aa:aa:01",
                },
                {
                    "value": "aa:aa:aa:aa:aa:02",
                    "label": "Unnamed client · 192.168.50.24 · aa:aa:aa:aa:aa:02",
                },
            ],
        )
        self.assertEqual(
            result["named"],
            {
                "name": "Sam phone",
                "ipv4_address": "192.168.50.23",
                "mac_address": "aa:aa:aa:aa:aa:01",
            },
        )
        self.assertEqual(result["unnamed"]["name"], "Client 192.168.50.24")
        self.assertEqual(
            result["lists"],
            [
                "aa:aa:aa:aa:aa:01",
                "bb:bb:bb:bb:bb:bb, aa:aa:aa:aa:aa:01",
                # Already listed, whatever its case.
                "AA:AA:AA:AA:AA:01, bb:bb:bb:bb:bb:bb",
            ],
        )

    def test_dashboard_offers_sign_out_near_the_top(self) -> None:
        self.assertLess(
            DASHBOARD_HTML.index('action="/sign-out"'),
            DASHBOARD_HTML.index('id="connected-devices"'),
        )

    def test_dashboard_leaves_the_token_to_the_sign_in_page(self) -> None:
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]

        for gone in (
            'id="admin-token"',
            "hasAdminToken",
            "adminTokenVerified",
            "checkAdminToken",
            "Enter the admin token",
            "Check the admin token",
        ):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, DASHBOARD_HTML)
        # The browser's cookie authorizes requests, so the script sends no token.
        self.assertNotIn("Authorization", script)
        # Only signed-in browsers get this page, so it loads at once.
        self.assertTrue(script.rstrip().endswith("startDashboard();"))

    def test_dashboard_returns_to_sign_in_when_signed_out(self) -> None:
        result = self._run_dashboard_functions(
            ["request"],
            "let pendingFormSubmitButton = null;\n"
            "const assigned = [];\n"
            "const window = {location: {assign: (url) => assigned.push(url)}};\n"
            "function setButtonBusy() {}\n"
            "async function fetch() {\n"
            "  return {ok: false, status: 401, statusText: 'Unauthorized',\n"
            "          json: async () => ({error: 'sign in required'})};\n"
            "}\n",
            "request('/api/profiles', 'GET').then(\n"
            "  () => console.log(JSON.stringify({assigned, failed: false})),\n"
            "  () => console.log(JSON.stringify({assigned, failed: true}))\n"
            ");\n",
        )

        self.assertEqual(result, {"assigned": ["/"], "failed": True})

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
        self.assertIn("setButtonBusy(event.submitter, true)", DASHBOARD_HTML)
        self.assertIn("if (submitButton) setButtonBusy(submitButton, false);", DASHBOARD_HTML)
        self.assertIn("Dashboard refresh failed:", DASHBOARD_HTML)
        self.assertIn("Settings changed since the last network action", DASHBOARD_HTML)
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

    SCHEDULE_WINDOW = {
        "profile_id": "slow", "days_of_week": [0], "start_minute": 540, "end_minute": 1020
    }

    def test_saves_group_and_everyone_else_schedules(self) -> None:
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice",)))

        group_status, _, group_body = self.server.write_response_for(
            "/api/schedules", {"id": "family-day", "group_id": "family", **self.SCHEDULE_WINDOW}
        )
        others_status, _, others_body = self.server.write_response_for(
            "/api/schedules", {"id": "others-day", "everyone_else": True, **self.SCHEDULE_WINDOW}
        )

        self.assertEqual((group_status, others_status), (200, 200), (group_body, others_body))
        self.assertEqual(json.loads(group_body)["schedule"]["group_id"], "family")
        self.assertTrue(json.loads(others_body)["schedule"]["everyone_else"])
        _, _, body = self.server.response_for("/api/schedules")
        self.assertEqual(
            [
                (schedule["id"], schedule["device_id"], schedule["group_id"])
                for schedule in json.loads(body)["schedules"]
            ],
            [("family-day", None, "family"), ("others-day", None, None)],
        )

    def test_a_schedule_must_name_one_existing_target(self) -> None:
        cases = {
            "device does not exist": {"device_id": "ghost"},
            "group does not exist": {"group_id": "ghost"},
            "exactly one of a device": {},
        }
        for message, target in cases.items():
            with self.subTest(message=message):
                status, _, body = self.server.write_response_for(
                    "/api/schedules", {"id": "bad", **target, **self.SCHEDULE_WINDOW}
                )
                self.assertEqual(status, 400)
                self.assertIn(message, json.loads(body)["error"])

    def test_everyone_else_schedules_use_one_traffic_class(self) -> None:
        for index in range(1, 14):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )
        others = {"everyone_else": True, **self.SCHEDULE_WINDOW}

        # 14 devices: the first everyone-else schedule takes the last class.
        first, _, _ = self.server.write_response_for("/api/schedules", {"id": "a", **others})
        self.repository.save_device(Device("d14", "D14", "192.168.50.34", "slow"))
        # A second schedule or a profile for everyone else needs no new class...
        second, _, _ = self.server.write_response_for("/api/schedules", {"id": "b", **others})
        profile, _, _ = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "slow"}
        )
        # ...but a new device does.
        device, _, _ = self.server.write_response_for(
            "/api/devices",
            {"id": "d15", "name": "D15", "ipv4_address": "192.168.50.35", "profile_id": "slow"},
        )

        self.assertEqual((first, second, profile, device), (200, 200, 200, 409))

    def test_the_first_everyone_else_schedule_needs_a_free_traffic_class(self) -> None:
        for index in range(1, 15):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )

        status, _, body = self.server.write_response_for(
            "/api/schedules", {"id": "others", "everyone_else": True, **self.SCHEDULE_WINDOW}
        )

        self.assertEqual(status, 409, body)
        self.assertEqual(self.repository.list_schedules(), ())

    def test_everyone_else_reports_the_profile_it_gets_now(self) -> None:
        self.repository.save_schedule(
            Schedule(
                id="others-day", device_id=None, everyone_else=True, profile_id="slow",
                days_of_week=(0,), start_minute=540, end_minute=1_020,
            )
        )
        # 21 September 2026 is a Monday. The Pi's local time decides, so run in
        # a zone away from UTC, where the CI runs.
        answers = []
        with local_time_zone("America/New_York"):
            for hour in (10, 20):
                moment = datetime(2026, 9, 21, hour, 0).timestamp()
                with patch("time.time", return_value=moment):
                    _, _, body = self.server.response_for("/api/everyone-else")
                    _, _, connected = self.server.response_for("/api/connected-devices")
                answers.append(json.loads(body))

        self.assertEqual(
            [(answer["active_profile_id"], answer["active_schedule_id"]) for answer in answers],
            [("slow", "others-day"), (None, None)],
        )
        self.assertEqual([answer["profile_id"] for answer in answers], [None, None])

    def test_connected_clients_carry_everyone_elses_profile_this_minute(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_schedule(
            Schedule(
                id="others-day", device_id=None, everyone_else=True, profile_id="slow",
                days_of_week=(0,), start_minute=540, end_minute=1_020,
            )
        )
        answers = []
        for hour in (10, 20):
            moment = datetime(2026, 9, 21, hour, 0).timestamp()
            with patch("time.time", return_value=moment):
                _, _, body = self.server.response_for("/api/connected-devices")
            answers.append(json.loads(body)["everyone_else_profile_id"])

        # The connected-client list polls every 5 seconds, so its labels follow schedules.
        self.assertEqual(answers, ["slow", None])

    def test_a_schedule_names_a_missing_profile(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/schedules",
            {"id": "work", "device_id": "alice", **dict(self.SCHEDULE_WINDOW, profile_id="ghost")},
        )

        self.assertEqual(status, 400)
        self.assertIn("profile does not exist", json.loads(body)["error"])

    def test_an_everyone_else_schedule_holds_a_traffic_class_for_new_devices(self) -> None:
        for index in range(1, 14):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )
        self.server.write_response_for(
            "/api/schedules", {"id": "others", "everyone_else": True, **self.SCHEDULE_WINDOW}
        )
        new_device = {"id": "d14", "name": "D14", "ipv4_address": "192.168.50.34",
                      "profile_id": "slow"}

        # 14 devices and everyone else fill the 15 classes.
        full, _, _ = self.server.write_response_for("/api/devices", new_device)
        self.server.write_response_for("/api/schedules/delete", {"id": "others"})
        freed, _, _ = self.server.write_response_for("/api/devices", new_device)

        self.assertEqual((full, freed), (409, 200))

    def test_a_turned_off_everyone_else_schedule_holds_no_traffic_class(self) -> None:
        for index in range(1, 15):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )
        self.repository.save_schedule(
            Schedule(
                id="others", device_id=None, everyone_else=True, profile_id="slow",
                days_of_week=(0,), start_minute=540, end_minute=1_020, enabled=False,
            )
        )

        status, _, _ = self.server.write_response_for("/api/everyone-else", {"profile_id": "slow"})

        self.assertEqual(status, 409)

    def test_each_client_poll_brings_everyone_elses_profile(self) -> None:
        result = self._run_dashboard_functions(
            ["followEveryoneElseProfile"],
            "let everyoneElse = {profile_id: null, active_profile_id: 'slow'};\n",
            "followEveryoneElseProfile({devices: [], everyone_else_profile_id: null});\n"
            "const afterWindow = everyoneElse.active_profile_id;\n"
            # Configuration-only mode lists no clients and says nothing about it.
            "followEveryoneElseProfile({available: false, devices: []});\n"
            "console.log(JSON.stringify([afterWindow, everyoneElse.active_profile_id,\n"
            "  everyoneElse.profile_id]));\n",
        )

        self.assertEqual(result, [None, None, None])

    def test_editing_a_schedule_keeps_its_target(self) -> None:
        result = self._run_dashboard_functions(
            ["scheduleTargetValue", "editSchedule"],
            "const fields = {};\n"
            "const element = (id) => (fields[id] = fields[id] ||\n"
            "  {value: '', scrollIntoView() {}});\n"
            "const document = {querySelectorAll: () => []};\n"
            "function syncScheduleDays() {}\n"
            "function formatMinute(minute) { return String(minute); }\n",
            "editSchedule({id: 'family-day', device_id: null, group_id: 'family',\n"
            "  everyone_else: false, profile_id: 'slow', days_of_week: [0],\n"
            "  start_minute: 540, end_minute: 1020});\n"
            "console.log(JSON.stringify(fields['schedule-target'].value));\n",
        )

        self.assertEqual(result, "group:family")

    def test_loading_schedules_updates_what_depends_on_them(self) -> None:
        result = self._run_dashboard_functions(
            ["loadSchedules"],
            "let savedSchedules = [];\n"
            "const devices = [];\n"
            "const schedules = [{id: 'others', everyone_else: true, enabled: true}];\n"
            "async function request() { return {schedules: schedules}; }\n"
            "const rendered = [];\n"
            "function renderSchedules(list) { rendered.push(list.length); }\n"
            "const menus = [];\n"
            "function updateTrafficDeviceOptions() { menus.push(savedSchedules.length); }\n",
            "loadSchedules().then(() => console.log(JSON.stringify({\n"
            "  saved: savedSchedules.length, rendered: rendered, menus: menus})));\n",
        )

        # Live traffic offers Everyone else once an everyone-else schedule loads.
        self.assertEqual(result, {"saved": 1, "rendered": [1], "menus": [1]})

    def test_the_schedule_target_menu_keeps_its_choice_when_rebuilt(self) -> None:
        result = self._run_dashboard_functions(
            ["deviceName", "scheduleTargetOptions", "renderScheduleTargets"],
            "let devices = [{id: 'alice', name: 'Alice', ipv4_address: '192.168.50.10'}];\n"
            "let groups = [{id: 'family', name: 'Family'}];\n"
            "const node = (tag) => ({tag: tag, children: [], value: '', label: '',\n"
            "  appendChild(child) { this.children.push(child); }});\n"
            # Like a real menu, it shows its first option unless told otherwise.
            "const select = {children: [], chosen: '',\n"
            "  appendChild(child) { this.children.push(child); },\n"
            "  replaceChildren() { this.children = []; this.chosen = ''; },\n"
            "  get options() { return this.children.flatMap((child) =>\n"
            "    child.tag === 'optgroup' ? child.children : [child]); },\n"
            "  get value() { const values = this.options.map((option) => option.value);\n"
            "    return values.includes(this.chosen) ? this.chosen : values[0] || ''; },\n"
            "  set value(value) { this.chosen = value; }};\n"
            "const element = () => select;\n"
            "const document = {createElement: node};\n",
            "renderScheduleTargets();\n"
            "select.value = 'group:family';\n"
            "devices = devices.concat([{id: 'bob', name: 'Bob', ipv4_address: '192.168.50.11'}]);\n"
            "renderScheduleTargets();\n"
            "const kept = select.value;\n"
            "groups = [];\n"
            "select.value = 'group:family';\n"
            "renderScheduleTargets();\n"
            "console.log(JSON.stringify({kept: kept,\n"
            "  labels: select.children.map((child) => child.label || child.value),\n"
            "  gone: select.value}));\n",
        )

        self.assertEqual(result["kept"], "group:family")
        self.assertEqual(result["labels"], ["Devices", "everyone-else"])
        # A group that went can't stay chosen.
        self.assertEqual(result["gone"], "device:alice")

    def test_imports_group_and_everyone_else_schedules(self) -> None:
        self.repository.save_device_group(DeviceGroup("family", "Family", ("alice",)))
        for payload in (
            {"id": "family-day", "group_id": "family", **self.SCHEDULE_WINDOW},
            {"id": "others-day", "everyone_else": True, **self.SCHEDULE_WINDOW},
        ):
            saved, _, _ = self.server.write_response_for("/api/schedules", payload)
            self.assertEqual(saved, 200)
        exported = self._export(self.server)
        schedules = self.repository.list_schedules()
        # Exports from before group schedules have no target fields but a device.
        older = dict(exported, schedules=[
            {"id": "work", "device_id": "alice", **self.SCHEDULE_WINDOW}
        ])

        status, _, body = self.server.write_response_for("/api/import", exported)
        imported = self.repository.list_schedules()
        older_status, _, older_body = self.server.write_response_for("/api/import", older)

        self.assertEqual((status, older_status), (200, 200), (body, older_body))
        self.assertEqual(imported, schedules)
        self.assertEqual(
            [schedule.device_id for schedule in self.repository.list_schedules()], ["alice"]
        )

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
            (
                "/api/timers",
                {"id": "t", "device_id": "alice", "profile_id": "slow", "duration_minutes": 5},
            ),
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
                helper = CountingHelper(devices={"alice": 1_600})
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

    def _serve_live(self, helper):
        scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=helper, poll_seconds=3_600
        )
        self.server.helper_client = helper
        self.server.scheduler = scheduler
        self.addCleanup(scheduler.stop)
        return helper

    def test_a_new_limit_counts_only_traffic_after_it_is_saved(self) -> None:
        helper = self._serve_live(CountingHelper(devices={"alice": 0}))
        self.server.write_response_for(
            "/api/groups", {"id": "family", "name": "Family", "device_ids": ["alice"]}
        )
        helper.devices["alice"] = 3_000_000

        self.server.write_response_for(
            "/api/data-limits", {"device_id": "alice", "profile_id": "slow", "limit_bytes": 10**9}
        )
        helper.devices["alice"] = 2_000_000
        self.server.write_response_for(
            "/api/group-data-limits",
            {"group_id": "family", "profile_id": "slow", "limit_bytes": 10**9},
        )

        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 2_000_000)
        self.assertEqual(self.repository.get_group_data_limit("family").used_bytes, 0)

    def test_a_reenabled_limit_skips_traffic_from_while_it_was_off(self) -> None:
        self.repository.save_data_limit(
            DataLimit(
                device_id="alice",
                profile_id="slow",
                limit_bytes=10**9,
                used_bytes=500,
                counter_bytes=0,
                last_reset_at=100,
                enabled=False,
            )
        )
        self._serve_live(CountingHelper(devices={"alice": 3_000_000}))

        self.server.write_response_for(
            "/api/data-limits",
            {"device_id": "alice", "profile_id": "slow", "limit_bytes": 10**9, "enabled": True},
        )

        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 500)

    def test_an_import_counts_only_traffic_after_it(self) -> None:
        self.repository.save_domain_observations(
            (DomainObservation("192.168.50.10", "netflix.com", 1, 10, 10),), now=10
        )
        for path, payload in (
            ("/api/groups", {"id": "family", "name": "Family", "device_ids": ["alice"]}),
            (
                "/api/data-limits",
                {"device_id": "alice", "profile_id": "slow", "limit_bytes": 10**9},
            ),
            (
                "/api/group-data-limits",
                {"group_id": "family", "profile_id": "slow", "limit_bytes": 10**9},
            ),
            (
                "/api/service-quotas",
                {
                    "id": "video",
                    "name": "Video",
                    "device_id": "alice",
                    "profile_id": "slow",
                    "domains": ["netflix.com"],
                    "limit_bytes": 10**9,
                },
            ),
        ):
            self.assertEqual(self.server.write_response_for(path, payload)[0], 200, path)
        exported = self._export(self.server)
        self._serve_live(
            CountingHelper(devices={"alice": 3_000_000}, services={"video": 2_000_000})
        )

        status, _, _ = self.server.write_response_for("/api/import", exported)

        self.assertEqual(status, 200)
        self.assertEqual(self.repository.get_data_limit("alice").used_bytes, 0)
        self.assertEqual(self.repository.get_group_data_limit("family").used_bytes, 0)
        self.assertEqual(self.repository.get_service_quota("video").used_bytes, 0)

    def test_conditions_describe_the_saved_plan_until_one_is_applied(self) -> None:
        status, _, body = self.server.response_for("/api/conditions")

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {
                "applied": False,
                "devices": {
                    "alice": {"condition": "degraded", "reasons": ["upload: delay 3000 ms"]}
                },
                "everyone_else": None,
            },
        )

    def test_conditions_leave_the_schedule_alone_while_nothing_is_applied(self) -> None:
        self.repository.save_profile(
            Profile(
                id="slow",
                name="Slow",
                upload=Impairment(delay_ms=3_000),
                reroll_interval_seconds=60,
                outage_probability=100,
                outage_duration_seconds=30,
                outage_interval_seconds=60,
            )
        )
        scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=FakeHelper(), poll_seconds=3_600
        )
        self.server.scheduler = scheduler
        self.addCleanup(scheduler.stop)

        for _ in range(3):
            status, _, body = self.server.response_for("/api/conditions")

        # Building the runtime plan would advance its rerolls and outages, so the
        # next Apply would start from a state decided while nothing ran.
        self.assertEqual(status, 200)
        self.assertEqual(scheduler._rolls, {})
        self.assertEqual(json.loads(body)["devices"]["alice"]["condition"], "degraded")

    def test_conditions_check_the_saved_mac_address(self) -> None:
        self.repository.save_device(
            Device("alice", "Alice's laptop", "192.168.50.10", "slow", "aa:bb:cc:dd:ee:ff")
        )
        self.server.write_response_for(
            "/api/policies",
            {"mac": {"mode": "denylist", "denylist": ["aa:bb:cc:dd:ee:ff"]}, "websites": {}},
        )

        conditions = json.loads(self.server.response_for("/api/conditions")[2])

        self.assertEqual(
            conditions["devices"]["alice"],
            {"condition": "offline", "reasons": ["MAC address denied"]},
        )

    def test_everyone_else_profile_round_trips(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "slow"}
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(
            json.loads(self.server.response_for("/api/everyone-else")[2]),
            {
                "profile_id": "slow",
                "active_profile_id": "slow",
                "active_schedule_id": None,
                "unaffected_devices": [],
            },
        )
        self.server.write_response_for("/api/everyone-else", {"profile_id": None})
        self.assertIsNone(self.repository.get_everyone_else_profile_id())

    def test_everyone_else_refuses_an_unknown_profile(self) -> None:
        status, _, _ = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "gone"}
        )

        self.assertEqual(status, 400)
        self.assertIsNone(self.repository.get_everyone_else_profile_id())

    def test_everyone_else_counts_toward_the_shaping_capacity(self) -> None:
        for index in range(14):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )

        status, _, body = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "slow"}
        )

        self.assertEqual(status, 409)
        self.assertIn("at most 15", json.loads(body)["error"])

    def test_everyone_else_takes_one_of_the_fifteen_slots(self) -> None:
        for index in range(13):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )
        # Alice, 13 more devices and everyone else fill all 15 slots.
        status, _, body = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "slow"}
        )
        self.assertEqual(status, 200, body)

        status, _, body = self.server.write_response_for(
            "/api/devices",
            {"id": "extra", "name": "Extra", "ipv4_address": "192.168.50.40", "profile_id": "slow"},
        )
        self.assertEqual(status, 409, body)

        # Switching everyone else to another profile needs no new slot.
        self.repository.save_profile(Profile(id="other", name="Other"))
        status, _, body = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "other"}
        )
        self.assertEqual(status, 200, body)

    def test_everyone_else_needs_an_explicit_profile_or_none(self) -> None:
        self.repository.save_everyone_else_profile_id("slow")

        status, _, _ = self.server.write_response_for("/api/everyone-else", {})

        self.assertEqual(status, 400)
        self.assertEqual(self.repository.get_everyone_else_profile_id(), "slow")

    def test_unaffected_devices_are_added_and_removed(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": "AA:AA:AA:AA:AA:AA"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(
            json.loads(self.server.response_for("/api/everyone-else")[2])["unaffected_devices"],
            [{"mac_address": "aa:aa:aa:aa:aa:aa", "name": "aa:aa:aa:aa:aa:aa"}],
        )

        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices/delete", {"mac_address": "aa:aa:aa:aa:aa:aa"}
        )
        self.assertEqual(status, 200)
        status, _, _ = self.server.write_response_for(
            "/api/unaffected-devices/delete", {"mac_address": "aa:aa:aa:aa:aa:aa"}
        )
        self.assertEqual(status, 404)

    def test_a_saved_device_address_and_the_list_exclude_each_other(self) -> None:
        self.repository.save_device(
            Device("bob", "Bob", "192.168.50.11", "slow", "bb:bb:bb:bb:bb:bb")
        )
        status, _, body = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": "bb:bb:bb:bb:bb:bb"}
        )
        self.assertEqual(status, 409)
        self.assertIn("Bob", json.loads(body)["error"])

        self.repository.save_unaffected_device(UnaffectedDevice("cc:cc:cc:cc:cc:cc"))
        status, _, body = self.server.write_response_for(
            "/api/devices",
            {
                "id": "carol",
                "name": "Carol",
                "ipv4_address": "192.168.50.12",
                "profile_id": "slow",
                "mac_address": "cc:cc:cc:cc:cc:cc",
            },
        )
        self.assertEqual(status, 409)
        self.assertIn("unaffected list", json.loads(body)["error"])

    def test_a_listed_connected_client_cannot_get_a_profile(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_unaffected_device(UnaffectedDevice("dc:1e:d5:3e:97:4c"))

        status, _, _ = self.server.write_response_for(
            "/api/connected-devices/apply-profile",
            {
                "ipv4_address": "192.168.50.178",
                "mac_address": "dc:1e:d5:3e:97:4c",
                "profile_id": "slow",
            },
        )

        self.assertEqual(status, 409)
        self.assertIsNone(self.repository.get_device_by_ipv4_address("192.168.50.178"))

    def test_a_profile_used_for_everyone_else_cannot_be_deleted(self) -> None:
        self.repository.save_profile(Profile(id="spare", name="Spare"))
        self.repository.save_everyone_else_profile_id("spare")

        status, _, body = self.server.write_response_for("/api/profiles/delete", {"id": "spare"})

        self.assertEqual(status, 409)
        self.assertIn("everyone else", json.loads(body)["error"])

    def test_everyone_else_changes_apply_unless_the_network_is_reset(self) -> None:
        for last_action, applied in (("apply", True), ("reset", False)):
            with self.subTest(last_action=last_action):
                helper = FakeHelper()
                self.server.helper_client = helper
                self.repository.update_runtime_state(
                    observed_state="applied" if applied else "reset",
                    last_action=last_action,
                    last_result="success",
                    last_error=None,
                )

                status, _, body = self.server.write_response_for(
                    "/api/everyone-else", {"profile_id": "slow"}
                )

                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body)["live_applied"], applied)
                self.assertEqual(helper.applied_plan is not None, applied)
                if applied:
                    self.assertIsNotNone(helper.applied_plan.everyone_else)

    def test_connected_clients_show_which_are_unaffected(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.save_unaffected_device(UnaffectedDevice("dc:1e:d5:3e:97:4c"))

        devices = json.loads(self.server.response_for("/api/connected-devices")[2])["devices"]

        self.assertTrue(devices[0]["unaffected"])

    def test_conditions_describe_everyone_else(self) -> None:
        self.repository.save_everyone_else_profile_id("slow")

        conditions = json.loads(self.server.response_for("/api/conditions")[2])

        self.assertEqual(
            conditions["everyone_else"],
            {"condition": "degraded", "reasons": ["upload: delay 3000 ms"]},
        )

    def test_conditions_describe_the_applied_plan(self) -> None:
        helper = self._serve_live(CountingHelper(devices={"alice": 0}))
        self.assertEqual(self.server.write_response_for("/api/apply", {})[0], 200)
        self.repository.save_profile(Profile(id="slow", name="Slow"))

        conditions = json.loads(self.server.response_for("/api/conditions")[2])

        # The saved profile changed, but the network still runs the applied plan.
        self.assertIsNotNone(helper.applied_plan)
        self.assertTrue(conditions["applied"])
        self.assertEqual(conditions["devices"]["alice"]["condition"], "degraded")

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


class SignInTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(
            Profile(id="slow", name="Slow", upload=Impairment(delay_ms=3_000))
        )
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="admin-token")
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()

        def stop() -> None:
            self.server.shutdown()
            thread.join(timeout=5)
            self.server.server_close()
            self.repository.close()

        self.addCleanup(stop)

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def sign_in(self, token="admin-token"):
        return self.request(
            "POST",
            "/sign-in",
            body=urlencode({"token": token}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def session(self) -> str:
        status, headers, _ = self.sign_in()
        self.assertEqual(status, 303)
        return headers["Set-Cookie"].split(";", 1)[0]

    def assert_neutral(self, headers, body) -> None:
        text = body.decode("utf-8", "replace") + " ".join(f"{k}: {v}" for k, v in headers.items())
        self.assertNotIn("jittersim", text.lower())
        # Whole words: the standard "device-width" viewport setting names nothing.
        self.assertIsNone(
            re.search(r"\b(profiles?|devices?(?!-width)|networks?)\b", text, re.I)
        )

    def test_strangers_see_only_a_neutral_sign_in_page(self) -> None:
        status, headers, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn(b"<title>Sign in</title>", body)
        self.assertIn(b'name="token"', body)
        self.assertNotIn(b"<script", body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("script-src", headers["Content-Security-Policy"])
        self.assert_neutral(headers, body)

    def test_other_addresses_stay_generic_without_a_sign_in(self) -> None:
        for path in ("/api/health", "/api/profiles", "/api/export", "/favicon.ico", "/other"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertIn(status, (401, 404))
                self.assert_neutral(headers, body)
        status, headers, body = self.request(
            "POST", "/api/apply", body="{}", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(status, 401)
        self.assert_neutral(headers, body)
        # A made-up cookie is no sign-in.
        status, _, body = self.request("GET", "/", headers={"Cookie": "session=1.2.3"})
        self.assertIn(b"<title>Sign in</title>", body)

    def test_signing_in_opens_the_dashboard_and_the_api(self) -> None:
        status, headers, _ = self.sign_in()

        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        cookie = headers["Set-Cookie"]
        for attribute in ("session=", "Path=/", "Max-Age=604800", "HttpOnly", "SameSite=Strict"):
            self.assertIn(attribute, cookie)
        # This test server speaks plain HTTP; the Pi's HTTPS adds Secure.
        self.assertNotIn("Secure", cookie)
        session = cookie.split(";", 1)[0]
        status, headers, body = self.request("GET", "/", headers={"Cookie": session})
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), DASHBOARD_HTML)
        self.assertIn("script-src 'sha256-", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/api/health", headers={"Cookie": session})[0], 200)
        # Visiting the sign-in address while signed in goes to the dashboard.
        status, headers, _ = self.request("GET", "/sign-in", headers={"Cookie": session})
        self.assertEqual((status, headers["Location"]), (303, "/"))

    def test_a_wrong_token_shows_the_page_again(self) -> None:
        status, headers, body = self.sign_in("wrong")

        self.assertEqual(status, 401)
        self.assertIn(b"That token didn", body)
        self.assertNotIn("Set-Cookie", headers)
        self.assert_neutral(headers, body)

    def test_repeated_failures_lock_sign_in_for_a_while(self) -> None:
        for _ in range(5):
            self.assertEqual(self.sign_in("wrong")[0], 401)

        status, headers, body = self.sign_in()

        self.assertEqual(status, 429)
        self.assertIn(b"Try again later", body)
        self.assertNotIn("Set-Cookie", headers)

    def test_sign_in_says_when_it_is_not_set_up(self) -> None:
        self.server.admin_token = None

        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Sign-in isn", body)
        self.assertEqual(self.sign_in()[0], 503)

    def test_unsigned_writes_do_not_reveal_which_routes_exist(self) -> None:
        for token in ("admin-token", None):
            with self.subTest(token=token):
                self.server.admin_token = token
                answers = {
                    self.request(
                        method, path, body="{}", headers={"Content-Type": "application/json"}
                    )[::2]
                    for method in ("POST", "PUT")
                    for path in ("/api/apply", "/api/everyone-else", "/api/not-a-route", "/other")
                }
                self.assertEqual(len(answers), 1, answers)

    def test_other_sites_cannot_sign_in_or_out(self) -> None:
        session = self.session()
        cross_site = {"Sec-Fetch-Site": "cross-site"}

        status, headers, _ = self.request(
            "POST", "/sign-out", headers={"Cookie": session, **cross_site}
        )
        self.assertEqual(status, 403)
        self.assertNotIn("Set-Cookie", headers)
        for _ in range(6):
            status, headers, body = self.request(
                "POST",
                "/sign-in",
                body=urlencode({"token": "wrong"}),
                headers={"Content-Type": "application/x-www-form-urlencoded", **cross_site},
            )
            self.assertEqual(status, 403)
            self.assert_neutral(headers, body)
        # None of those counted as guesses.
        self.assertEqual(self.sign_in()[0], 303)

    def test_signing_out_clears_the_cookie(self) -> None:
        status, headers, _ = self.request("POST", "/sign-out", headers={"Cookie": self.session()})

        self.assertEqual((status, headers["Location"]), (303, "/"))
        self.assertIn("Max-Age=0", headers["Set-Cookie"])

    def test_cookie_writes_must_be_json_from_this_page(self) -> None:
        session = self.session()
        host = "127.0.0.1:8080"
        body = json.dumps({"profile_id": None}).encode("utf-8")

        def write(headers):
            # Run the handler in this thread: the database belongs to it.
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.path = "/api/everyone-else"
            handler.client_address = ("127.0.0.1", 50000)
            handler.headers = Message()
            handler.headers["Host"] = host
            handler.headers["Content-Length"] = str(len(body))
            for name, value in headers.items():
                handler.headers[name] = value
            handler.rfile = BytesIO(body)
            response = {}
            handler._send_response = lambda **values: response.update(values)
            handler.do_POST()
            return response["status"]

        json_type = {"Content-Type": "application/json"}
        self.assertEqual(write({"Cookie": session, **json_type, "Origin": f"http://{host}"}), 200)
        self.assertEqual(write({"Cookie": session, **json_type}), 200)
        self.assertEqual(write({"Cookie": session, "Content-Type": "text/plain"}), 403)
        self.assertEqual(
            write({"Cookie": session, **json_type, "Origin": "https://elsewhere.example"}), 403
        )
        self.assertEqual(write(json_type), 401)
        # With the no-referrer policy some browsers send Origin: null for this page.
        null_origin = {"Origin": "null", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(write({"Cookie": session, **json_type, **null_origin}), 200)
        for site in ("same-site", "cross-site"):
            with self.subTest(site=site):
                headers = {"Cookie": session, **json_type, "Sec-Fetch-Site": site}
                self.assertEqual(write(headers), 403)
        # Scripts sending the token header aren't browsers, so none of this applies.
        self.assertEqual(
            write({"Authorization": "Bearer admin-token", "Content-Type": "text/plain"}), 200
        )

if __name__ == "__main__":
    unittest.main()
