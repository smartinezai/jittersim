import base64
import contextlib
import hashlib
import http.client
import json
import logging
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
from contextlib import contextmanager
from datetime import datetime
from email.message import Message
from html.parser import HTMLParser
from io import BytesIO
from ipaddress import IPv4Address
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from jittersim.api.server import (
    DASHBOARD_HTML,
    WRITE_PATHS,
    ControlRequestHandler,
    ControlServer,
    _build_parser,
)
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    Impairment,
    Profile,
    RandomWebsiteOutcome,
    Schedule,
    ServiceList,
)
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.runtime import PreparedRuntimePlan, RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.measure import comparison_table
from jittersim.networking.helper import HelperClientError
from tests.helper_support import OlderHelperService, OldHelperService, captures, serving


@contextmanager
def captured_logs():
    """Collect every log message from every logger while the block runs."""

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
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


class FakeHelper:
    def __init__(self, fail_action: str = "") -> None:
        self.fail_action = fail_action
        self.applied_plan = None
        self.reset_called = False
        self.access_plan = None
        self.persistence_enabled = False
        self.release_status_calls = 0
        self.wifi_reset_called = False
        self.gateway_state = {
            "gateway_state": "available",
            "outage_observed": False,
            "outage_stop_latched": False,
            "reset_pending": False,
            "recovery_pending": False,
            "state_error": False,
        }

    def gateway_status(self):
        return {"mode_enabled": self.persistence_enabled, **self.gateway_state}

    def reset_wifi_name(self):
        if self.fail_action == "wifi":
            raise HelperClientError("simulated Wi-Fi failure")
        self.wifi_reset_called = True
        return {"ok": True, "action": "wifi-reset", "ssid": "JitterSim", "security": "WPA3-SAE"}

    def set_gateway_persistence(self, *, enabled):
        self.persistence_enabled = enabled
        return self.gateway_status()

    def release_status(self):
        self.release_status_calls += 1
        return {
            "phase": "idle",
            "installed": "test-release",
            "available": None,
            "rollback_available": False,
            "message": "No newer approved release is staged.",
        }

    def access_status(self):
        return {"available": True, "wifi_admission_available": False,
                "policy": self.access_plan.payload() if self.access_plan else None,
                "enabled": bool(self.access_plan and self.access_plan.options.enabled), "error": None}

    def apply_access(self, plan):
        if self.fail_action == "access":
            raise HelperClientError("simulated access failure")
        self.access_plan = plan
        return self.access_status()

    def normal_access(self):
        from dataclasses import replace

        from jittersim.engine.access import AccessPlan
        self.reset()
        plan = self.access_plan or AccessPlan()
        self.apply_access(replace(plan, options=replace(plan.options, enabled=False)))
        return {"ok": True}

    def apply(self, *, plan, explicit_apply=False):
        if self.fail_action == "apply":
            raise HelperClientError("simulated helper failure")
        self.applied_plan = plan
        if explicit_apply:
            self.gateway_state["outage_stop_latched"] = False
            self.gateway_state["recovery_pending"] = False
        if plan.access is not None:
            self.apply_access(plan.access)
        return {"ok": True, "action": "apply", "commands_run": 3}

    def apply_experiment(self, *, plan):
        return self.apply(plan=plan)

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

    def read_router_wifi_credentials(self, *, admin_password, include_password):
        result = {"ssid": "Browser home"}
        if include_password:
            result["password"] = "browser-wifi-password"
        return result

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

    def apply(self, *, plan, explicit_apply=False):
        self.plans.append(plan)
        return super().apply(plan=plan, explicit_apply=explicit_apply)


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

    def apply(self, *, plan, explicit_apply=False):
        result = super().apply(plan=plan, explicit_apply=explicit_apply)
        self.devices = dict.fromkeys(self.devices, 0)
        self.services = dict.fromkeys(self.services, 0)
        return result

    def traffic_counters(self):
        return {"devices": dict(self.devices), "services": dict(self.services)}


def create_test_certificate(directory: Path, hostname="127.0.0.1"):
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
            "/CN=" + hostname,
            "-addext",
            "subjectAltName=" + ("IP:" if hostname == "127.0.0.1" else "DNS:") + hostname,
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


# The cards every page holds, in order. A card outside the pages is the
# dashboard-access card that the token flow shows first.
EXPECTED_PAGE_HEADINGS = {
    None: ["Credentials", "Dashboard access"],
    "page-devices": [
        "Getting started", "Everyone", "Connected clients", "Saved devices",
        "Everyone else", "Protected devices",
    ],
    "page-profiles": ["Saved profiles", "Save profile", "Assign a device", "Group devices"],
    "page-schedules": [
        "Schedule a profile", "Run a profile timer", "Scenarios", "Progression mode",
        "Device data limit", "Shared group data limit", "Service lists",
        "Browser service profile", "Service data limit", "Service time limit",
    ],
    "page-router": [
        "Wi-Fi name", "Upstream router connection", "Manual Pi access-point fallback",
        "Router outage behavior", "Updates",
    ],
    "page-traffic": ["Live traffic", "Traffic discovery", "Random website choices"],
    "page-measurements": ["Measurements"],
    "page-activity": ["Activity log"],
    "page-access": ["Network access rules"],
    "page-diagnostics": [
        "Impact", "Profiles", "Devices", "Compiled plan", "Runtime state", "Access rules", "Diagnostics",
        "Backup and action history",
    ],
}


class DashboardStructure(HTMLParser):
    """Record which page holds each card and heading, checking every tag closes."""

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
        if "page" in classes:
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
    def test_router_status_and_results_require_admin_and_own_site(self):
        self.server.admin_token = "fixture-admin"
        for path in ("/api/router/status", "/api/router/operation", "/api/router/wifi", "/api/router/check",
                     "/api/router/wifi-credentials"):
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

    def test_the_activity_log_notes_a_restart_that_applies_again(self) -> None:
        self.server.helper_client = FakeHelper()
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success", last_error=None
        )

        self.server.recover_saved_network_state()

        self.assertEqual(
            [event["message"] for event in self.repository.list_activity()],
            ["Configuration applied again after the dashboard restarted"],
        )

    def test_the_activity_log_is_writable_over_http_and_summarised(self) -> None:
        # The routes the browser posts to must be in WRITE_PATHS, or it gets 404.
        self.assertIn("/api/activity/clear", WRITE_PATHS)
        self.repository.record_activity(kind="apply", message="Configuration applied", now=100)
        self.repository.record_activity(kind="reset", message="Network reset", now=200)

        latest = json.loads(self.server.response_for("/api/activity/latest")[2])

        self.assertEqual(latest, {"newest_id": 2, "count": 2})

    def test_failed_actions_are_not_recorded_and_recording_never_breaks_them(self) -> None:
        class FailingHelper(FakeHelper):
            def apply(self, *, plan, explicit_apply=False):
                raise HelperClientError("apply failed")

        self.server.helper_client = FailingHelper()
        with self.assertLogs("jittersim.api.server", "ERROR"):
            failed, _, _ = self.server.write_response_for("/api/apply", {})
        self.server.helper_client = FakeHelper()
        with patch.object(
            self.repository, "record_activities", side_effect=StorageError("disk full")
        ):
            with self.assertLogs("jittersim.api.server", "WARNING"):
                applied, _, _ = self.server.write_response_for("/api/apply", {})

        self.assertEqual((failed, applied), (502, 200))
        self.assertEqual(self.repository.list_activity(), ())

    def test_the_activity_log_lists_and_clears_events(self) -> None:
        self.repository.record_activity(kind="apply", message="Configuration applied", now=100)

        listed = json.loads(self.server.response_for("/api/activity")[2])
        status, _, _ = self.server.write_response_for("/api/activity/clear", {})

        self.assertEqual(
            listed["events"],
            [{"id": 1, "happened_at": 100, "kind": "apply", "message": "Configuration applied"}],
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(self.server.response_for("/api/activity")[2])["events"], [])

    def test_apply_and_reset_are_recorded_but_saved_changes_are_not(self) -> None:
        self.server.helper_client = FakeHelper()

        applied, _, _ = self.server.write_response_for("/api/apply", {})
        # A saved change applies the plan again, which is not a new Apply.
        self.server.write_response_for(
            "/api/timers", {"device_id": "alice", "profile_id": "slow", "duration_minutes": 10}
        )
        reset, _, _ = self.server.write_response_for("/api/reset", {})

        self.assertEqual((applied, reset), (200, 200))
        self.assertEqual(
            [
                (event["kind"], event["message"])
                for event in reversed(self.repository.list_activity())
            ],
            [("apply", "Configuration applied"), ("reset", "Network reset")],
        )

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
        # Only what the Pi base exposed: the page needs the mode before the token.
        self.assertEqual(
            json.loads(body),
            {
                "helper_configured": False,
                "helper_reachable": False,
                "mode": "configuration-only",
                "status": "ok",
            },
        )

    def test_gateway_persistence_reads_and_saves_through_the_helper(self) -> None:
        helper = FakeHelper()
        self.server.helper_client = helper

        read_status, _, read_body = self.server.response_for("/api/gateway-persistence")
        initial = json.loads(read_body)
        version = self.repository.get_runtime_state().configuration_version
        saved_status, _, saved_body = self.server.write_response_for(
            "/api/gateway-persistence",
            {"persistence_enabled": True, "version": version},
        )
        saved = json.loads(saved_body)

        self.assertEqual(read_status, 200)
        self.assertFalse(initial["persistence_enabled"])
        self.assertTrue(initial["helper_synced"])
        self.assertEqual(saved_status, 200)
        self.assertTrue(saved["persistence_enabled"])
        self.assertTrue(saved["helper_synced"])
        self.assertTrue(helper.persistence_enabled)
        self.assertTrue(self.repository.get_persist_profiles_after_gateway_outage())

        stale_status, _, _ = self.server.write_response_for(
            "/api/gateway-persistence",
            {"persistence_enabled": False, "version": version},
        )
        self.assertEqual(stale_status, 409)

    def test_reading_gateway_persistence_does_not_change_an_initialized_helper(self) -> None:
        helper = FakeHelper()
        helper.persistence_enabled = True
        helper.gateway_state["mode_initialized"] = True
        helper.gateway_state["outage_observed"] = True
        self.server.helper_client = helper
        calls = []
        helper.set_gateway_persistence = lambda **kwargs: calls.append(kwargs)

        status, _, body = self.server.response_for("/api/gateway-persistence")

        self.assertEqual(status, 200)
        self.assertEqual(calls, [])
        self.assertFalse(json.loads(body)["helper_synced"])

    def test_reading_gateway_persistence_gives_a_restarted_helper_the_mode(self) -> None:
        helper = FakeHelper()
        helper.persistence_enabled = True
        helper.gateway_state["mode_initialized"] = False
        self.server.helper_client = helper

        status, _, body = self.server.response_for("/api/gateway-persistence")

        self.assertEqual(status, 200)
        self.assertFalse(helper.persistence_enabled)
        self.assertTrue(json.loads(body)["helper_synced"])

    def test_gateway_persistence_rejects_truthy_non_boolean_values(self) -> None:
        self.server.helper_client = FakeHelper()

        status, _, body = self.server.write_response_for(
            "/api/gateway-persistence", {"persistence_enabled": 1}
        )

        self.assertEqual(status, 400)
        self.assertIn("true or false", json.loads(body)["error"])
        self.assertFalse(self.repository.get_persist_profiles_after_gateway_outage())

    def test_dashboard_update_preference_reads_and_saves_without_network_changes(self) -> None:
        before = self.repository.get_runtime_state().configuration_version

        read_status, _, read_body = self.server.response_for("/api/update-preferences")
        initial = json.loads(read_body)
        saved_status, _, saved_body = self.server.write_response_for(
            "/api/update-preferences", {"check_staged_updates_on_open": False}
        )
        saved = json.loads(saved_body)
        after = json.loads(self.server.response_for("/api/update-preferences")[2])

        self.assertEqual(read_status, 200)
        self.assertTrue(initial["check_staged_updates_on_open"])
        self.assertEqual(saved_status, 200)
        self.assertTrue(saved["saved"])
        self.assertFalse(after["check_staged_updates_on_open"])
        self.assertEqual(self.repository.get_runtime_state().configuration_version, before)

    def test_dashboard_update_preference_rejects_non_boolean_values(self) -> None:
        for invalid in (1, 0, "off", None):
            with self.subTest(invalid=invalid):
                status, _, body = self.server.write_response_for(
                    "/api/update-preferences",
                    {"check_staged_updates_on_open": invalid},
                )

                self.assertEqual(status, 400)
                self.assertIn("true or false", json.loads(body)["error"])
                self.assertTrue(self.repository.get_check_staged_updates_on_open())

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
    def test_admin_reads_require_admin_token(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()

        def get(path, authorization=None):
            handler = object.__new__(ControlRequestHandler)
            handler.server = self.server
            handler.path = path
            handler.client_address = ("127.0.0.1", 45000)
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
            "/api/gateway-persistence",
            "/api/update-preferences",
            "/api/system-health",
        ):
            with self.subTest(path=path):
                self.assertEqual(get(path)["status"], 401)
                self.assertEqual(get(path, "Bearer wrong-token")["status"], 401)
                self.assertEqual(get(path, "Bearer admin-token")["status"], 200)
        # The mode check is the only public read, so the page can show its mode
        # before the token is entered.
        self.assertEqual(get("/api/profiles")["status"], 401)
        self.assertEqual(get("/api/health")["status"], 200)
        self.assertEqual(get("/api/health", "Bearer admin-token")["status"], 200)

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

    def test_a_device_named_all_is_separate_from_aggregate_traffic(self) -> None:
        result = self._run_dashboard_functions(
            ["selectedTrafficTotals", "selectedQueueTotals"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n"
            "const picked = {value: 'all'}; const element = () => picked;\n",
            "const counters = {all: {upload_bytes: 7, download_bytes: 9, upload_packets: 1, download_packets: 2},\n"
            "  alice: {upload_bytes: 30, download_bytes: 40, upload_packets: 3, download_packets: 4}};\n"
            "const queues = {all: {upload_dropped: 2, download_dropped: 0, upload_queued: 1, download_queued: 0},\n"
            "  alice: {upload_dropped: 4, download_dropped: 0, upload_queued: 0, download_queued: 0}};\n"
            "const device = [selectedTrafficTotals(counters), selectedQueueTotals(queues)];\n"
            "picked.value = '@all';\n"
            "console.log(JSON.stringify({device, total: [selectedTrafficTotals(counters), selectedQueueTotals(queues)]}));\n",
        )
        self.assertEqual(result["device"][0]["upload_bytes"], 7)
        self.assertEqual(result["device"][1], {"dropped": 2, "queued": 1})
        self.assertEqual(result["total"][0]["upload_bytes"], 37)
        self.assertEqual(result["total"][1], {"dropped": 6, "queued": 1})

    def test_live_traffic_can_show_everyone_else(self) -> None:
        result = self._run_dashboard_functions(
            ["selectedTrafficTotals", "selectedQueueTotals"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n"
            "const picked = {value: '@all'};\n"
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
            ["updateTrafficDeviceOptions", "everyoneElseAssigned", "everyoneElseInLiveTraffic",
             "resetTrafficView"],
            "const EVERYONE_ELSE_TRAFFIC = '__everyone_else__';\n"
            "let generalProfileState = null;\n"
            "let latestTrafficReferences = {profile_upload:10,profile_download:5};\n"
            "let trafficProfileScope = 'everyone';\n"
            "function devicePickerLabel(device) { return device.name; }\n"
            "let everyoneElse = {profile_id: 'slow'};\n"
            "let savedSchedules = [];\n"
            "let runningScenarios = [];\n"
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
            "  scheduled: select.options.map((option) => option.value),\n"
            "  limits: latestTrafficReferences, scope: trafficProfileScope}));\n",
        )

        self.assertEqual(result["offered"], ["@all", "alice", "__everyone_else__"])
        self.assertEqual(result["kept"], ["__everyone_else__", 0])
        # Without a profile the choice goes, and the graph starts again for "all".
        self.assertEqual(result["after"], [["@all", "alice"], "@all", 1])
        self.assertEqual(result["limits"], {"profile_upload": None, "profile_download": None})
        self.assertIsNone(result["scope"])
        # An everyone-else schedule brings the choice back.
        self.assertEqual(result["scheduled"], ["@all", "alice", "__everyone_else__"])

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

    def _send(self, method, path, *, authorization="Bearer admin-token", body=None):
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.path = path
        handler.client_address = ("127.0.0.1", 45000)
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
        self.assertEqual(accepted_encryption, [True])
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
    def _inline_hashes(tag: str) -> str:
        hashes = []
        for content in re.findall(f"<{tag}>(.*?)</{tag}>", DASHBOARD_HTML, re.S):
            digest = hashlib.sha256(content.encode("utf-8")).digest()
            hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
        return " ".join(hashes)

    def test_dashboard_only_runs_its_own_inline_script_and_style(self) -> None:
        self.server.admin_token = "admin-token"
        response = self._get_over_http("/")

        policy = response.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        # Every inline block is allowed, including the sidebar script.
        self.assertEqual(DASHBOARD_HTML.count("<script>"), 2)
        self.assertIn("script-src " + self._inline_hashes("script"), policy)
        self.assertIn("style-src " + self._inline_hashes("style"), policy)
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

    def test_system_details_need_the_token_and_stay_out_of_the_public_health(self) -> None:
        # Version and free disk and temperature and uptime help an attacker on the
        # test Wi-Fi so they sit behind the admin token.
        _, _, public = self.server.response_for("/api/health")
        for detail in ("system", "version", "disk_free_bytes", "temperature_celsius", "uptime"):
            self.assertNotIn(detail, public.decode())
        status, content_type, body = self.server.response_for("/api/system-health")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        system = json.loads(body)["system"]
        self.assertEqual(
            set(system),
            {"version", "checked_at", "disk_free_bytes", "temperature_celsius", "uptime_seconds"},
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

    def test_wifi_credentials_return_the_name_without_the_password(self) -> None:
        self.server.helper_client = FakeHelper()
        status, _, body = self.server.write_response_for(
            "/api/router/wifi-credentials",
            {"admin_password": "router-secret", "include_password": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"result": {"ssid": "Browser home"}})
        self.assertNotIn("router-secret", body.decode())
        self.assertNotIn("browser-wifi-password", body.decode())

    def test_wifi_credentials_default_to_the_name_only(self) -> None:
        self.server.helper_client = FakeHelper()
        status, _, body = self.server.write_response_for(
            "/api/router/wifi-credentials", {"admin_password": "router-secret"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"result": {"ssid": "Browser home"}})

    def test_wifi_credentials_return_the_password_only_when_asked(self) -> None:
        self.server.helper_client = FakeHelper()
        status, _, body = self.server.write_response_for(
            "/api/router/wifi-credentials",
            {"admin_password": "router-secret", "include_password": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body)["result"], {"ssid": "Browser home", "password": "browser-wifi-password"}
        )

    def test_wifi_credentials_never_ask_the_helper_for_more_than_was_requested(self) -> None:
        asked = []

        class Recording(FakeHelper):
            def read_router_wifi_credentials(self, *, admin_password, include_password):
                asked.append((admin_password, include_password))
                # A misbehaving helper that always sends the password must not leak it.
                return {"ssid": "Browser home", "password": "browser-wifi-password"}

        self.server.helper_client = Recording()
        status, _, body = self.server.write_response_for(
            "/api/router/wifi-credentials",
            {"admin_password": "router-secret", "include_password": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(asked, [("router-secret", False)])
        self.assertEqual(json.loads(body), {"result": {"ssid": "Browser home"}})
        self.assertNotIn("browser-wifi-password", body.decode())

    def test_wifi_credentials_reject_bad_requests(self) -> None:
        self.server.helper_client = FakeHelper()
        for payload in (
            {},
            {"admin_password": ""},
            {"admin_password": 7},
            {"admin_password": "x" * 1025},
            {"admin_password": "x", "include_password": "yes"},
            {"admin_password": "x", "include_password": "true"},
            {"admin_password": "x", "include_password": "false"},
            {"admin_password": "x", "include_password": 1},
            {"admin_password": "x", "include_password": None},
        ):
            status, _, body = self.server.write_response_for(
                "/api/router/wifi-credentials", payload)
            self.assertEqual(status, 400, payload)
            self.assertNotIn("browser-wifi-password", body.decode())

    def test_wifi_credentials_need_a_helper_and_the_admin_token(self) -> None:
        from jittersim.api.server import WRITE_PATHS

        self.server.helper_client = None
        status, _, _ = self.server.write_response_for(
            "/api/router/wifi-credentials", {"admin_password": "x"})
        self.assertEqual(status, 503)
        self.assertIn("/api/router/wifi-credentials", WRITE_PATHS)

    def _assert_no_secret_logged(self, records) -> None:
        for message in records:
            for secret in ("hunter2hunter2", "router-secret", "browser-wifi-password"):
                self.assertNotIn(secret, message)

    def test_wifi_credentials_never_echo_a_wrong_password(self) -> None:
        class Rejecting(FakeHelper):
            def read_router_wifi_credentials(self, *, admin_password, include_password):
                raise HelperClientError(
                    "The router rejected the password " + admin_password + " browser-wifi-password")

        self.server.helper_client = Rejecting()
        with captured_logs() as records:
            status, _, body = self.server.write_response_for(
                "/api/router/wifi-credentials",
                {"admin_password": "hunter2hunter2", "include_password": True})
        self.assertEqual(status, 502)
        self.assertNotIn("hunter2hunter2", body.decode())
        self.assertNotIn("browser-wifi-password", body.decode())
        # The failure is logged once so the capture is not vacuous.
        self.assertTrue(any("router Wi-Fi copy failed" in message for message in records))
        self._assert_no_secret_logged(records)

    def test_wifi_credentials_pass_on_only_the_fixed_router_messages(self) -> None:
        from jittersim.networking.helper import WIFI_DETAILS_MISSING_MESSAGE
        from jittersim.networking.router import ROUTER_CHECK_MESSAGES

        busy = "The router is busy right now. Try again in a moment."
        for error, expected in (
            (HelperClientError(ROUTER_CHECK_MESSAGES["login"]), ROUTER_CHECK_MESSAGES["login"]),
            (HelperClientError(ROUTER_CHECK_MESSAGES["unreachable"]),
             ROUTER_CHECK_MESSAGES["unreachable"]),
            (HelperClientError("unknown action"), ROUTER_CHECK_MESSAGES["outdated"]),
            (HelperClientError("browser-wifi-password leaked here"), ROUTER_CHECK_MESSAGES["failed"]),
            (HelperClientError("Another router operation is running. Try again shortly.",
                               reason="helper-busy"), busy),
            (HelperClientError("router-secret browser-wifi-password", reason="helper-busy"), busy),
            (HelperClientError(WIFI_DETAILS_MISSING_MESSAGE), WIFI_DETAILS_MISSING_MESSAGE),
        ):
            class Failing(FakeHelper):
                def read_router_wifi_credentials(self, *, admin_password, include_password):
                    raise error

            self.server.helper_client = Failing()
            with captured_logs() as records:
                status, _, body = self.server.write_response_for(
                    "/api/router/wifi-credentials",
                    {"admin_password": "router-secret", "include_password": True})
            self.assertEqual(status, 502, str(error))
            self.assertEqual(json.loads(body), {"error": expected}, str(error))
            self._assert_no_secret_logged(records)

    def test_wifi_credentials_wording_is_for_this_route_only(self) -> None:
        from jittersim.networking.helper import WIFI_DETAILS_MISSING_MESSAGE
        from jittersim.networking.router import ROUTER_CHECK_MESSAGES

        for error in (
            HelperClientError("Another router operation is running. Try again shortly.",
                              reason="helper-busy"),
            HelperClientError(WIFI_DETAILS_MISSING_MESSAGE),
        ):
            class Failing(FakeHelper):
                def check_router_password(self, *, admin_password):
                    raise error

            self.server.helper_client = Failing()
            status, _, body = self.server.write_response_for(
                "/api/router/check", {"admin_password": "router-secret"})
            self.assertEqual(status, 502)
            self.assertEqual(json.loads(body), {"error": ROUTER_CHECK_MESSAGES["failed"]})

    def test_an_old_helper_gets_the_out_of_date_message(self) -> None:
        class Old(FakeHelper):
            read_router_wifi_credentials = None

        self.server.helper_client = Old()
        status, _, body = self.server.write_response_for(
            "/api/router/wifi-credentials", {"admin_password": "router-secret"})
        self.assertEqual(status, 502)
        self.assertIn("out of date", json.loads(body)["error"])

    def test_wifi_credentials_are_not_logged_or_recorded(self) -> None:
        self.server.helper_client = FakeHelper()
        before = list(self.repository.list_recent_actions())
        with captured_logs() as records:
            status, _, body = self.server.write_response_for(
                "/api/router/wifi-credentials",
                {"admin_password": "router-secret", "include_password": True})
        self.assertEqual(status, 200)
        self.assertIn("browser-wifi-password", body.decode())
        self.assertEqual(list(self.repository.list_recent_actions()), before)
        self._assert_no_secret_logged(records)

    def test_wifi_credentials_never_run_as_a_background_operation(self) -> None:
        self.server.helper_client = FakeHelper()
        status, _, body = self.server.write_response_for(
            "/api/router/wifi-credentials",
            {"admin_password": "router-secret", "include_password": True, "background": True})
        # The password would be kept for minutes in the retained operation result.
        self.assertEqual(status, 200)
        self.assertNotIn("operation_id", json.loads(body))
        self.assertIsNone(self.server.router_operations.completed_action())
        self.assertIsNone(self.server.router_operations._active)
        self.assertIsNone(self.server.router_operations._completed)

    def _post_over_http(self, path, payload, headers=None):
        """Send one POST over a real socket while the repository's own thread serves it."""

        import http.client

        outcome = []

        def send() -> None:
            connection = http.client.HTTPConnection(*self.server.server_address[:2], timeout=5)
            try:
                request_headers = {"Content-Type": "application/json"}
                request_headers.update(headers or {})
                connection.request("POST", path, body=json.dumps(payload).encode(),
                                   headers=request_headers)
                response = connection.getresponse()
                outcome.append((response.status,
                                {k.lower(): v for k, v in response.getheaders()},
                                response.read()))
            finally:
                connection.close()

        client = threading.Thread(target=send)
        client.start()
        try:
            self.server.timeout = 5
            self.server.handle_request()
        finally:
            client.join(timeout=5)
        self.assertEqual(len(outcome), 1)
        return outcome[0]

    def test_wifi_credentials_over_http_need_the_token_and_are_never_cached(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()
        payload = {"admin_password": "router-secret", "include_password": True}

        status, headers, body = self._post_over_http("/api/router/wifi-credentials", payload)
        self.assertEqual(status, 401)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("browser-wifi-password", body.decode())

        status, headers, body = self._post_over_http(
            "/api/router/wifi-credentials", payload,
            {"Authorization": "Bearer wrong-token"})
        self.assertEqual(status, 401)
        self.assertNotIn("browser-wifi-password", body.decode())

        status, headers, body = self._post_over_http(
            "/api/router/wifi-credentials", payload,
            {"Authorization": "Bearer admin-token"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(json.loads(body)["result"]["password"], "browser-wifi-password")

    def test_wifi_credentials_over_http_refuse_other_sites(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()
        payload = {"admin_password": "router-secret", "include_password": True}
        host = "%s:%s" % self.server.server_address[:2]

        for extra in ({"Origin": "https://different.example"}, {"Sec-Fetch-Site": "cross-site"}):
            status, headers, body = self._post_over_http(
                "/api/router/wifi-credentials", payload,
                {"Authorization": "Bearer admin-token", "Host": host, **extra})
            self.assertEqual(status, 403, extra)
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertNotIn("browser-wifi-password", body.decode())

    def test_wifi_credentials_history_keeps_no_password(self) -> None:
        self.server.admin_token = "admin-token"
        self.server.helper_client = FakeHelper()
        status, _, _ = self._post_over_http(
            "/api/router/wifi-credentials",
            {"admin_password": "router-secret", "include_password": True},
            {"Authorization": "Bearer admin-token"})
        self.assertEqual(status, 200)
        history = self.repository.list_recent_actions()
        self.assertEqual(
            (history[0]["action"], history[0]["status_code"]),
            ("/api/router/wifi-credentials", 200),
        )
        for secret in ("router-secret", "browser-wifi-password"):
            self.assertNotIn(secret, str(history))

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
                        "assigned_effective_state": None,
                        "group_id": None,
                        "group_name": None,
                        "group_profile_id": None,
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
        self.assertEqual(response["device"]["id"], "client-dc1ed53e974c")
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
        # Only an explicit Apply may undo a Reset. The dashboard relays the server's
        # message, which says so.
        assign = DASHBOARD_HTML.split("async function applyProfileToDevice(", 1)[1]
        self.assertIn("result.message", assign.split("\n    }\n", 1)[0])
        self.assertIn("Start", json.loads(body)["message"])

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
            + re.search(r"const TRAFFIC_SERIES = \[.*?\n    \];", script, re.S).group(0)
            + "\nconst trafficProfileScope = 'device';\n"
            "const trafficInspection = {pointer:null,legend:null,pinned:null};\n"
            "const TRAFFIC_INSPECTION_HELP = 'Inspect a line';\n"
            "const document = {querySelectorAll: () => []};\n"
            # An ISP plan of 100 Mbit/s sets the scale to 100 Mbit/s.
            "const latestTrafficReferences = {isp_download: 100};\n"
            "const drawn = [];\n"
            "const context = {\n"
            "  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},\n"
            "  lineTo() {}, stroke() {}, setLineDash() {}, arc() {}, fill() {},\n"
            "  measureText(text) { return {width: 7 * text.length}; },\n"
            "  fillText(text, x) { drawn.push({text, x, align: this.textAlign}); },\n"
            "};\n"
            "const elements = {\n"
            "  'traffic-chart': {getBoundingClientRect: () => ({width: 600, height: 320}),\n"
            "                    getContext: () => context},\n"
            "  'traffic-device': {value: 'all'},\n"
            "  'traffic-chart-detail': {},\n"
            "};\n"
            "const element = (id) => elements[id];\n"
            "const window = {devicePixelRatio: 1};\n"
            + function("formatTrafficRate")
            + function("trafficSeriesName")
            + function("trafficSeriesGeometry")
            + function("trafficSegmentDistance")
            + function("trafficLineHits")
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
            'id="saved-profiles"',
            'id="device-table"',
            '<input id="device-profile" required>',
            'id="new-profile"',
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
        self.assertIn("profileFormPayload(editingProfile)", submit)
        payload = DASHBOARD_HTML.split("function profileFormPayload(base) {", 1)[1]
        payload = payload.split("\n    }\n", 1)[0]

        # The profile being edited keeps the randomness the form does not show.
        for fragment in (
            "enabled_probability: base ? base.reroll_interval_randomness.enabled_probability : 100",
            'outage_duration_randomness: keep("outage_duration_randomness")',
            'outage_interval_randomness: keep("outage_interval_randomness")',
            "variation_percent: base ? base.website_randomness.variation_percent : 0",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, payload)
        self.assertIn('element("profile-form").addEventListener("reset"', DASHBOARD_HTML)

    def test_dashboard_shows_each_device_condition(self) -> None:
        table = DASHBOARD_HTML.split("function renderDeviceTable() {", 1)[1]
        table = table.split("\n    }\n", 1)[0]

        self.assertIn('["Device", "Address", "Profile", "Condition"]', table)
        self.assertIn("deviceStateCell(device)", table)
        self.assertIn("return deviceConditionCell(device.id);", DASHBOARD_HTML)
        self.assertIn('request("/api/conditions", "GET")', DASHBOARD_HTML)
        self.assertIn('id="device-conditions-note"', DASHBOARD_HTML)
        refresh = DASHBOARD_HTML.split("async function refreshDashboard(only) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("loadConditions", refresh)

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
            'id="unaffected-devices"',
            'request("/api/everyone-else", "GET")',
            '"/api/unaffected-devices/delete"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, DASHBOARD_HTML)
        refresh = DASHBOARD_HTML.split("async function refreshDashboard(only) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("loadEveryoneElse", refresh)

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

        harness = "const invalidateAdminTools = () => {}; const restoreDrafts = () => {}; const loadHealth = async () => {}; const loadWorkspace = async () => {};\n" + setup + "".join(function(name) for name in names) + calls
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
                "Protected",
                "Saved device: Slow",
                "Normal connection",
                "Saved device: Slow (also on the protected list)",
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
                "deviceDisplayName", "deviceTitle", "deviceName", "scheduleTargetFields",
                "scheduleTargetValue", "scheduleTargetName", "scheduleTargetOptions",
            ],
            "function devicePickerLabel(device) { return device.name + ' (' + device.ipv4_address + ')'; }\n"
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

    def test_the_scenarios_card_sits_with_the_timers(self) -> None:
        rules = self._panel("schedules")
        for fragment in (
            'id="scenario-run-list"', 'id="scenario-start-form"', 'id="scenario-start-scenario"',
            'id="scenario-start-target"', 'id="scenario-list"', 'id="scenario-form"',
            'id="scenario-steps"', 'id="scenario-add-step"', 'id="scenario-loop"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, rules)
        self.assertLess(rules.index("Run a profile timer"), rules.index("<h2>Scenarios</h2>"))
        refresh = DASHBOARD_HTML.split("async function refreshDashboard(only) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("loadScenarios", refresh)
        self.assertIn("makePoller(pollDashboard, 5000);", DASHBOARD_HTML)

    def test_scenario_steps_durations_and_running_text(self) -> None:
        result = self._run_dashboard_functions(
            ["profileName", "formatDuration", "scenarioSummary", "scenarioRunText"],
            "const profiles = [{id: 'slow', name: 'Slow'}, {id: '3g', name: '3G'}];\n",
            "const steps = [{profile_id: 'slow', duration_seconds: 60},\n"
            "  {profile_id: '3g', duration_seconds: 30}];\n"
            "const run = {target_name: 'Alice', scenario_name: 'Commute', step_index: 1,\n"
            "  step_count: 2, profile_name: '3G', step_remaining_seconds: 80,\n"
            "  total_remaining_seconds: 80, loop: false};\n"
            "console.log(JSON.stringify({\n"
            "  durations: [45, 60, 90, 3600, 3601, 3660, 86400].map(formatDuration),\n"
            "  summaries: [scenarioSummary({steps: steps, loop: false}),\n"
            "    scenarioSummary({steps: steps, loop: true})],\n"
            "  ending: scenarioRunText(run),\n"
            "  repeating: scenarioRunText(Object.assign({}, run, {step_index: 0,\n"
            "    profile_name: 'Slow', step_remaining_seconds: 45, total_remaining_seconds: null,\n"
            "    loop: true})),\n"
            "}));\n",
        )

        self.assertEqual(
            result["durations"],
            ["45 s", "1 min", "1 min 30 s", "1 h", "1 h 1 s", "1 h 1 min", "24 h"],
        )
        self.assertEqual(
            result["summaries"],
            ["Slow 1 min → 3G 30 s", "Slow 1 min → 3G 30 s, repeating"],
        )
        self.assertEqual(
            result["ending"],
            {
                "title": "Alice · Commute",
                "detail": "Step 2 of 2: 3G, 1 min 20 s left in this step; ends in 1 min 20 s",
            },
        )
        self.assertEqual(
            result["repeating"]["detail"],
            "Step 1 of 2: Slow, 45 s left in this step; repeats until stopped",
        )

    def test_running_scenarios_update_in_place_and_rebuild_when_they_change(self) -> None:
        result = self._run_dashboard_functions(
            ["formatDuration", "scenarioRunText", "renderScenarioRuns"],
            "let scenarioRunKey = '';\n"
            "const node = () => ({children: [], textContent: '', listeners: {},\n"
            "  append(...children) { this.children.push(...children); },\n"
            "  appendChild(child) { this.children.push(child); },\n"
            "  addEventListener(type, listener) { this.listeners[type] = listener; },\n"
            "  querySelector(selector) {\n"
            "    const details = this.children[0];\n"
            "    return selector.endsWith('strong') ? details.children[0] : details.children[1];\n"
            "  }});\n"
            "let rebuilds = 0;\n"
            "const list = Object.assign(node(), {replaceChildren() {\n"
            "  rebuilds += 1; this.children = []; }});\n"
            "const elements = {'scenario-run-list': list, 'scenario-count': node()};\n"
            "const element = (id) => elements[id];\n"
            "const document = {createElement: node};\n"
            "function stopScenario() {}\n",
            "const run = {id: 'run-1', target_name: 'Alice', scenario_name: 'Commute',\n"
            "  step_index: 0, step_count: 2, profile_name: 'Slow', step_remaining_seconds: 50,\n"
            "  total_remaining_seconds: 80, loop: false};\n"
            "renderScenarioRuns([run]);\n"
            "const row = list.children[0];\n"
            "renderScenarioRuns([Object.assign({}, run, {scenario_name: 'Rush hour',\n"
            "  step_remaining_seconds: 45, total_remaining_seconds: 75})]);\n"
            "const sameRow = list.children[0] === row;\n"
            "const texts = [row.children[0].children[0].textContent,\n"
            "  row.children[0].children[1].textContent];\n"
            "renderScenarioRuns([Object.assign({}, run, {id: 'run-2'})]);\n"
            "console.log(JSON.stringify({sameRow: sameRow, texts: texts, rebuilds: rebuilds,\n"
            "  count: elements['scenario-count'].textContent}));\n",
        )

        # A renamed scenario shows its new name without a rebuild; a new run rebuilds.
        self.assertTrue(result["sameRow"])
        self.assertEqual(
            result["texts"],
            [
                "Alice · Rush hour",
                "Step 1 of 2: Slow, 45 s left in this step; ends in 1 min 15 s",
            ],
        )
        self.assertEqual((result["rebuilds"], result["count"]), (2, "1 running"))

    def test_live_traffic_offers_everyone_else_while_its_scenario_runs(self) -> None:
        result = self._run_dashboard_functions(
            ["everyoneElseAssigned", "everyoneElseInLiveTraffic", "loadScenarios"],
            "let everyoneElse = {profile_id: null};\n"
            "let savedSchedules = [];\n"
            "let savedScenarios = [];\n"
            "let runningScenarios = [];\n"
            "let scenarioRunKey = '';\n"
            "const devices = [];\n"
            "let runs = [{id: 'run-1', everyone_else: true}];\n"
            "async function request() { return {scenarios: [], runs: runs}; }\n"
            "function renderSavedScenarios() {}\n"
            "function renderScenarioRuns() {}\n"
            "const menus = [];\n"
            "function updateTrafficDeviceOptions() { menus.push(everyoneElseInLiveTraffic()); }\n",
            "(async () => {\n"
            "  const before = everyoneElseInLiveTraffic();\n"
            "  await loadScenarios();\n"
            "  await loadScenarios();\n"
            "  runs = [];\n"
            "  await loadScenarios();\n"
            "  console.log(JSON.stringify({before: before, menus: menus,\n"
            "    assigned: everyoneElseAssigned()}));\n"
            "})();\n",
        )

        # The menu is rebuilt only when the running scenario starts or ends.
        self.assertEqual(result["before"], False)
        self.assertEqual(result["menus"], [True, False])
        # A run is temporary, so Getting started doesn't count it.
        self.assertFalse(result["assigned"])

    def test_the_saved_scenario_list_rebuilds_only_when_it_changes(self) -> None:
        result = self._run_dashboard_functions(
            ["profileName", "formatDuration", "scenarioSummary", "renderSavedScenarios"],
            "let profiles = [{id: 'slow', name: 'Slow'}];\n"
            "let savedScenarios = [{id: 'commute', name: 'Commute', loop: false,\n"
            "  steps: [{profile_id: 'slow', duration_seconds: 60}]}];\n"
            "let scenarioListKey = '';\n"
            "let rebuilds = 0;\n"
            "const node = () => ({children: [], value: '', options: [],\n"
            "  append(...children) { this.children.push(...children); },\n"
            "  appendChild(child) { this.children.push(child); },\n"
            "  addEventListener() {},\n"
            "  replaceChildren() { rebuilds += 1; this.children = []; }});\n"
            "const elements = {'scenario-list': node(), 'scenario-start-scenario': node()};\n"
            "const element = (id) => elements[id];\n"
            "const document = {createElement: node};\n",
            "renderSavedScenarios();\n"
            "renderSavedScenarios();\n"
            "const same = rebuilds;\n"
            "profiles = [{id: 'slow', name: 'Crawl'}];\n"
            "renderSavedScenarios();\n"
            "const text = elements['scenario-list'].children[0].children[0];\n"
            "console.log(JSON.stringify({same: same, renamed: rebuilds,\n"
            "  summary: text.children[1].textContent}));\n",
        )

        # Each render rebuilds the list and the start menu, so 2 per change.
        self.assertEqual((result["same"], result["renamed"]), (2, 4))
        self.assertEqual(result["summary"], "Crawl 1 min · commute")

    def test_the_step_editor_keeps_one_step_and_numbers_them(self) -> None:
        result = self._run_dashboard_functions(
            ["profileOptionLabels", "scenarioStepRow", "renumberScenarioSteps"],
            "function updateScenarioDuration() {}\n"
            "const profiles = [{id: 'slow', name: 'Slow'}];\n"
            "const node = (tag) => ({tag: tag, children: [], className: '', value: '',\n"
            "  textContent: '', attributes: {}, listeners: {}, dataset: {},\n"
            "  querySelectorAll() { return this.children[3].children; },\n"
            "  append(...children) { children.forEach((child) => this.appendChild(child)); },\n"
            "  appendChild(child) { child.parent = this; this.children.push(child); },\n"
            "  setAttribute(name, value) { this.attributes[name] = value; },\n"
            "  addEventListener(type, listener) { this.listeners[type] = listener; },\n"
            "  remove() { const siblings = this.parent.children;\n"
            "    siblings.splice(siblings.indexOf(this), 1); },\n"
            "  focus() { focused.push(this.id); }});\n"
            "const focused = [];\n"
            "const steps = node('div');\n"
            "const addStep = Object.assign(node('button'), {id: 'scenario-add-step'});\n"
            "const element = (id) => (id === 'scenario-steps' ? steps : addStep);\n"
            "const document = {createElement: node};\n"
            "steps.querySelectorAll = () => steps.children;\n",
            "steps.appendChild(scenarioStepRow({profile_id: 'slow', duration_seconds: 60}));\n"
            "steps.children[0].children[3].children[3].listeners.click();\n"
            "const lastKept = steps.children.length;\n"
            "steps.appendChild(scenarioStepRow({profile_id: 'slow', duration_seconds: 30}));\n"
            "renumberScenarioSteps();\n"
            "const second = steps.children[1];\n"
            "const labels = [second.children[0].children[0].attributes['aria-label'],\n"
            "  second.children[3].children[3].attributes['aria-label']];\n"
            "steps.children[0].children[3].children[3].listeners.click();\n"
            "const profileMenu = steps.children[0].children[0].children[0];\n"
            "console.log(JSON.stringify({lastKept: lastKept, labels: labels,\n"
            "  left: steps.children.length, focused: focused,\n"
            "  renumbered: profileMenu.attributes['aria-label']}));\n",
        )

        self.assertEqual(result["lastKept"], 1)
        self.assertEqual(result["labels"], ["Step 2 profile", "Remove step 2"])
        # Removing a step moves focus to Add step and renumbers the rest.
        self.assertEqual(
            (result["left"], result["focused"], result["renumbered"]),
            (1, ["scenario-add-step"], "Step 1 profile"),
        )

    def test_rebuilt_profile_menus_keep_the_steps_being_edited(self) -> None:
        result = self._run_dashboard_functions(
            ["profileOptionLabels", "loadProfiles", "followProfileMenus"],
            "let profiles = [];\n"
            "let profilesLoaded = false;\n"
            "function renderSidebarStatus() {}\n"
            "function updateGeneralProfileControls() {}\n"
            "const profilesLoadedHooks = [followProfileMenus];\n"
            "const everyoneElse = {profile_id: null};\n"
            "const element = () => ({value: '', options: [], replaceChildren() {this.options=[];}, appendChild(option) {this.options.push(option);}});\n"
            "const document = {createElement: () => ({})};\n"
            "async function request() { return {profiles: [{id: 'slow', name: 'Slow'}]}; }\n"
            "function renderProfileTable() {}\n"
            "let editing = [{profile_id: 'slow', duration_seconds: 90}];\n"
            "function scenarioStepsFromForm() { return editing; }\n"
            "const rendered = [];\n"
            "function renderScenarioSteps(steps) { rendered.push(steps); }\n",
            "loadProfiles().then(() => { editing = []; return loadProfiles(); })\n"
            "  .then(() => console.log(JSON.stringify(rendered)));\n",
        )

        self.assertEqual(
            result,
            [
                [{"profile_id": "slow", "duration_seconds": 90}],
                # An empty editor starts with one one-minute step.
                [{"profile_id": "", "duration_seconds": 60}],
            ],
        )

    def test_scenario_steps_come_from_the_form(self) -> None:
        result = self._run_dashboard_functions(
            ["scenarioStepsFromForm"],
            "const row = (profile, minutes, seconds) => ({querySelector: (selector) => ({\n"
            "  '.scenario-step-profile': {value: profile},\n"
            "  '.scenario-step-minutes': {value: minutes},\n"
            "  '.scenario-step-seconds': {value: seconds}}[selector])});\n"
            "const rows = [row('slow', '1', '30'), row('3g', '', '45'), row('slow', '2', '')];\n"
            "const element = () => ({querySelectorAll: () => rows});\n",
            "console.log(JSON.stringify(scenarioStepsFromForm()));\n",
        )

        self.assertEqual(
            result,
            [
                {"profile_id": "slow", "duration_seconds": 90},
                {"profile_id": "3g", "duration_seconds": 45},
                {"profile_id": "slow", "duration_seconds": 120},
            ],
        )

    def test_the_activity_log_has_its_own_page_and_polls(self) -> None:
        overview = self._panel("activity")
        for fragment in (
            'id="activity-list"', 'id="activity-copy"', 'id="activity-clear"',
            'id="activity-markdown"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, overview)
        refresh = DASHBOARD_HTML.split("async function refreshDashboard(only) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("loadActivity", refresh)
        self.assertIn("makePoller(pollDashboard, 5000);", DASHBOARD_HTML)

    def test_the_activity_log_copies_as_markdown(self) -> None:
        result = self._run_dashboard_functions(
            ["formatActivityTime", "activityMarkdown"],
            "process.env.TZ = 'UTC';\n",
            "const events = [\n"
            "  {id: 3, happened_at: 1790000130, message: '*Kid* <b>_tab_</b> [x] `y` ~z~'},\n"
            "  {id: 2, happened_at: 1790000125, message: 'Alice | test \\\\ path\\nsecond'},\n"
            "  {id: 1, happened_at: 1790000000, message: 'Configuration applied'},\n"
            "];\n"
            "console.log(JSON.stringify(activityMarkdown(events)));\n",
        )

        self.assertEqual(
            result.split("\n"),
            [
                "| Time (UTC) | Event |",
                "| --- | --- |",
                # Oldest first, as a report reads.
                "| 2026-09-21 14:13:20 | Configuration applied |",
                "| 2026-09-21 14:15:25 | Alice \\| test \\\\ path second |",
                # A device name can't format the report.
                "| 2026-09-21 14:15:30 | \\*Kid\\* \\<b\\>\\_tab\\_\\</b\\> "
                "\\[x\\] \\`y\\` \\~z\\~ |",
            ],
        )

    def activity_page(self) -> str:
        return (
            "let latestActivity = [];\n"
            "let activityKey = null;\n"
            "const ACTIVITY_EMPTY_TEXT = 'No events yet.';\n"
            "const node = (tag) => ({tag: tag, children: [], textContent: '', hidden: false,\n"
            "  append(...children) { this.children.push(...children); },\n"
            "  appendChild(child) { this.children.push(child); },\n"
            "  replaceChildren() { this.children = []; },\n"
            "  focus() {}, select() { this.selected = true; }});\n"
            "const elements = {'activity-list': node('ol'), 'activity-empty': node('p'),\n"
            "  'activity-markdown': Object.assign(node('textarea'), {hidden: true}),\n"
            "  'activity-count': node('p'), 'activity-kind': {value: ''}, 'activity-search': {value: ''}};\n"
            "const element = (id) => elements[id];\n"
            "const document = {createElement: node};\n"
            "const messages = [];\n"
            "function showMessage(text, kind) { messages.push(kind); }\n"
            "function setButtonBusy() {}\n"
        )

    def test_the_activity_list_fetches_only_when_something_changed(self) -> None:
        result = self._run_dashboard_functions(
            ["formatActivityTime", "filteredActivity", "renderActivity", "loadActivity"],
            self.activity_page()
            + "let latest = {newest_id: 1, count: 1};\n"
            "const events = [{id: 1, happened_at: 1790000000,\n"
            "  message: '<img src=x onerror=alert(1)>'}];\n"
            "const fetched = [];\n"
            "async function request(path) {\n"
            "  fetched.push(path);\n"
            "  return path === '/api/activity/latest' ? latest : {events: events};\n"
            "}\n",
            "(async () => {\n"
            "  await loadActivity();\n"
            "  await loadActivity();\n"
            "  latest = {newest_id: 1, count: 0};\n"
            "  events.length = 0;\n"
            "  await loadActivity();\n"
            "  console.log(JSON.stringify({fetched: fetched,\n"
            "    empty: elements['activity-empty'].hidden,\n"
            "    items: elements['activity-list'].children.length}));\n"
            "})();\n",
        )

        self.assertEqual(
            result["fetched"],
            [
                "/api/activity/latest", "/api/activity",
                "/api/activity/latest",
                "/api/activity/latest", "/api/activity",
            ],
        )
        # After the clear, the list is empty and says so.
        self.assertEqual((result["empty"], result["items"]), (False, 0))

    def test_activity_messages_are_shown_as_text(self) -> None:
        result = self._run_dashboard_functions(
            ["formatActivityTime", "filteredActivity", "renderActivity"],
            self.activity_page(),
            "renderActivity([{id: 1, happened_at: 1790000000,\n"
            "  message: '<img src=x onerror=alert(1)>'}]);\n"
            "const item = elements['activity-list'].children[0];\n"
            "console.log(JSON.stringify({text: item.children[1].textContent,\n"
            "  html: 'innerHTML' in item.children[1]}));\n",
        )

        self.assertEqual(result, {"text": "<img src=x onerror=alert(1)>", "html": False})

    def test_copying_falls_back_to_selected_text(self) -> None:
        result = self._run_dashboard_functions(
            ["formatActivityTime", "activityMarkdown", "filteredActivity", "copyActivity"],
            self.activity_page()
            + "latestActivity = [{id: 1, happened_at: 1790000000, message: 'Applied'}];\n"
            "const navigator = {clipboard: {writeText: () => Promise.reject(new Error('no'))}};\n",
            "copyActivity().then(() => {\n"
            "  const box = elements['activity-markdown'];\n"
            "  console.log(JSON.stringify({hidden: box.hidden, selected: box.selected === true,\n"
            "    last: box.value.split('\\n').pop()}));\n"
            "});\n",
        )

        self.assertEqual(result["hidden"], False)
        self.assertTrue(result["selected"])
        self.assertTrue(result["last"].endswith("| Applied |"))

    def test_clearing_the_log_asks_first_then_reloads_it(self) -> None:
        result = self._run_dashboard_functions(
            ["clearActivity"],
            self.activity_page()
            + "let answer = false;\n"
            "const window = {confirm: () => answer};\n"
            "const posted = [];\n"
            "async function request(path) { posted.push(path); return {}; }\n"
            "let reloads = 0;\n"
            "async function loadActivity() { reloads += 1; }\n",
            "(async () => {\n"
            "  await clearActivity({});\n"
            "  answer = true;\n"
            "  await clearActivity({});\n"
            "  console.log(JSON.stringify({posted: posted, reloads: reloads}));\n"
            "})();\n",
        )

        self.assertEqual(result, {"posted": ["/api/activity/clear"], "reloads": 1})

    def test_the_activity_list_can_be_scrolled_by_keyboard(self) -> None:
        overview = self._panel("activity")
        list_tag = overview.split('id="activity-list"', 1)[0].rsplit("<ol", 1)[1]
        list_tag += overview.split('id="activity-list"', 1)[1].split(">", 1)[0]

        # Focusable, named, and not re-read in full whenever an event arrives.
        self.assertIn('tabindex="0"', list_tag)
        self.assertIn('aria-label="Activity log events"', list_tag)
        self.assertNotIn("aria-live", list_tag)

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
                "Saved. The network is reset, so choose Start to use it.",
                "Saved. It takes effect when the dashboard runs in hardware mode.",
            ],
        )

    def test_dashboard_profile_menus_keep_the_everyone_else_choice(self) -> None:
        values = self._run_dashboard_functions(
            ["profileOptionLabels", "loadProfiles", "followProfileMenus"],
            "let profiles = [];\n"
            "let profilesLoaded = false;\n"
            "function renderSidebarStatus() {}\n"
            "function updateGeneralProfileControls() {}\n"
            "const profilesLoadedHooks = [followProfileMenus];\n"
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
            "function renderProfileTable() {}\n"
            "function scenarioStepsFromForm() { return []; }\n"
            "function renderScenarioSteps() {}\n",
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
            ["profileName", "conditionCell", "appliedProfileCell", "everyoneElseRow"],
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
                    ["Everyone else", "Test Wi-Fi clients that aren't saved or protected"],
                    ["Any address", "1 protected"],
                    ["3G", "Saved profile"],
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

    def test_help_text_takes_no_room_until_shown(self) -> None:
        tooltip = DASHBOARD_HTML.split(".help-icon::after {", 1)[1].split("}", 1)[0]

        # An invisible tooltip still widens the page, so hidden help leaves the layout.
        self.assertIn("display: none;", tooltip)
        self.assertNotIn("visibility: hidden;", tooltip)
        self.assertIn(
            ".help-icon:hover::after, .help-icon:focus-visible::after { display: block; }",
            DASHBOARD_HTML,
        )
        # On a phone the help bubble sits at the bottom of the screen instead.
        small = self._small_screen_css()
        self.assertIn(
            ".help-icon::after { position: fixed; top: auto; bottom: 1rem; left: 1rem; "
            "width: calc(100vw - 2rem); }",
            small,
        )

    def test_hidden_fields_stay_hidden(self) -> None:
        # label { display: grid } used to override the hidden attribute.
        self.assertIn("[hidden] { display: none !important; }", DASHBOARD_HTML)
        script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        for prefix in ("data-limit", "group-data-limit", "service-quota", "time-quota"):
            with self.subTest(prefix=prefix):
                listener = (
                    f'element("{prefix}-reset-mode").addEventListener("change", '
                    f'() => syncQuotaReset("{prefix}"));'
                )
                self.assertIn(listener, script)
                self.assertIn(f'syncQuotaReset("{prefix}");', script.split(listener, 1)[1])

    PAGES = (
        "devices", "profiles", "schedules", "router", "traffic", "measurements", "activity",
        "access", "diagnostics", "updates",
    )

    def _panel(self, name: str) -> str:
        start = DASHBOARD_HTML.index(f'<section class="page" id="page-{name}"')
        end = DASHBOARD_HTML.index(f"<!-- /page:{name} -->", start)
        return DASHBOARD_HTML[start:end]

    def test_dashboard_sections_live_on_pages(self) -> None:
        nav = DASHBOARD_HTML.split('<nav class="sidebar-nav"', 1)[1].split("</nav>", 1)[0]
        for name in self.PAGES:
            with self.subTest(page=name):
                self.assertIn(f'href="#{name}"', nav)
                self.assertIn(f'id="page-{name}"', DASHBOARD_HTML)
        expected = {
            "devices": [
                'id="getting-started"', 'id="connected-devices"', 'id="device-table"',
                "<h2>Everyone else</h2>",
            ],
            "profiles": [
                'id="saved-profiles"', "Save profile", "<h2>Assign a device</h2>",
                "<h2>Group devices</h2>",
            ],
            "schedules": [
                "<h2>Schedule a profile</h2>", "Run a profile timer", "<h2>Scenarios</h2>",
                "Device data limit", "Service time limit",
            ],
            "router": [
                "Upstream router connection", "Manual Pi access-point fallback",
                "Router outage behavior",
            ],
            "traffic": ['id="traffic-chart"', "Traffic discovery"],
            "measurements": ['id="measurement-files"'],
            "activity": ['id="activity-list"'],
            "access": ["<h2>Network access rules</h2>"],
            "diagnostics": [
                'id="plan"', 'id="state"', 'id="diagnostics-json"', 'id="action-history"',
                'id="export-settings"',
            ],
        }
        for name, fragments in expected.items():
            panel = self._panel(name)
            positions = []
            for fragment in fragments:
                with self.subTest(page=name, fragment=fragment):
                    self.assertIn(fragment, panel)
                    positions.append(panel.index(fragment))
            # Lists come before the forms that edit them.
            self.assertEqual(positions, sorted(positions), name)

    def test_every_card_sits_on_one_page(self) -> None:
        structure = DashboardStructure()
        structure.feed(DASHBOARD_HTML)
        structure.close()

        self.assertEqual(structure.errors, [])
        self.assertEqual(structure.open_tags, [])
        # Sibling pages: hiding one never hides another.
        self.assertEqual(structure.panels, [f"page-{name}" for name in self.PAGES])
        self.assertEqual(structure.panel_parents, {("html", "body", "div", "main", "div")})
        # Only the dashboard-access card stays outside the pages.
        self.assertEqual(
            [classes for classes, panel in structure.cards if panel is None],
            [["card"]],
        )
        self.assertEqual(structure.headings, EXPECTED_PAGE_HEADINGS)

    def test_background_polling_pauses_and_never_overlaps_refresh(self) -> None:
        result = self._run_dashboard_functions(
            ["pollDashboard", "pagePollJobs", "refresh"],
            "const document = {hidden: true}; const dashboardLoaded = true;\n"
            "let adminTokenVerified = true;\n"
            "let dashboardPoll = null, dashboardRefresh = null, refreshAgain = false, refreshWanted;\n"
            "const unansweredPaths = new Set();\n"
            "let page = 'activity'; function currentPage() { return page; }\n"
            "const seen = []; let release; let hold = new Promise(resolve => {release = resolve;});\n"
            "const loadActivity = async () => {seen.push('activity'); await hold;};\n"
            "const loadQuotas = async () => {seen.push('limits');};\n"
            "const loadTimers = loadQuotas, loadScenarios = loadQuotas, loadProgressions = loadQuotas;\n"
            "const loadSchedules = loadQuotas; const element = () => ({});\n"
            "const loadServiceLists = async () => {seen.push('service lists');};\n"
            "const refreshDashboard = async () => {seen.push('refresh');};\n",
            "(async () => {\n"
            " await pollDashboard(); const hidden = seen.length; document.hidden = false;\n"
            " const first = pollDashboard(); await pollDashboard();\n"
            " const updating = refresh(); await pollDashboard(); const before = seen.slice();\n"
            " release(); await first; await updating;\n"
            " page = 'schedules'; await pollDashboard();\n"
            " console.log(JSON.stringify({hidden, before, seen}));\n"
            "})();\n",
        )
        self.assertEqual(result["hidden"], 0)
        # A second poll while one runs does nothing, and a refresh waits for the poll.
        self.assertEqual(result["before"], ["activity"])
        # Only the visible page's readers run.
        self.assertEqual(
            result["seen"], ["activity", "refresh"] + ["limits"] * 5 + ["service lists"]
        )
    def test_refresh_requested_during_a_refresh_reloads_after_it(self) -> None:
        result = self._run_dashboard_functions(
            ["refresh"],
            "let dashboardPoll = null, dashboardRefresh = null, refreshAgain = false, refreshWanted;\n"
            "let release; const hold = new Promise(resolve => {release = resolve;});\n"
            "let active = 0, peak = 0, calls = 0;\n"
            "async function refreshDashboard() { calls++; active++; peak = Math.max(peak, active);\n"
            " if (calls === 1) await hold; active--; }\n",
            "(async () => { const first = refresh(); const second = refresh(); release();\n"
            " await Promise.all([first, second]); console.log(JSON.stringify({calls, peak})); })();\n",
        )
        self.assertEqual(result, {"calls": 2, "peak": 1})

    def test_activity_filters_are_combined_and_export_only_matches(self) -> None:
        result = self._run_dashboard_functions(
            ["filteredActivity", "activityMarkdown", "formatActivityTime"],
            self.activity_page() + "latestActivity = [\n"
            " {kind: 'scenario', happened_at: 100, message: 'Phone <x> started'},\n"
            " {kind: 'timer', happened_at: 101, message: 'Phone timer'},\n"
            " {kind: 'scenario', happened_at: 102, message: 'Tablet started'}];\n",
            "elements['activity-kind'].value = 'scenario'; elements['activity-search'].value = ' PHONE ';\n"
            "console.log(JSON.stringify(activityMarkdown(filteredActivity())));\n",
        )
        self.assertIn("Phone \\<x\\> started", result)
        self.assertNotIn("Phone timer", result)
        self.assertNotIn("Tablet", result)

    def test_tab_links_leave_modified_clicks_to_the_browser(self) -> None:
        result = self._run_dashboard_functions(
            ["followTabLink"],
            "const shown = [];\n"
            "const pushed = [];\n"
            "const TAB_PAGES = {devices: 'devices'};\n"
            "function showPage(name) { shown.push(name); }\n"
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
                # The card starts closed, so the JSON takes no room until opened.
                self.assertRegex(
                    DASHBOARD_HTML,
                    r'<section class="card" data-collapse="closed">\s*'
                    r'<div class="section-heading"><h2>[^<]+</h2><p>[^<]+</p></div>\s*'
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
            "function tabPanel(name) { return elements['tab-' + name]; }\n"
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

    def test_dashboard_getting_started_card_sits_on_the_devices_page(self) -> None:
        overview = self._panel("devices")
        self.assertIn('id="getting-started"', overview)
        self.assertLess(
            overview.index('id="getting-started"'), overview.index('id="connected-devices"')
        )
        refresh = DASHBOARD_HTML.split("async function refreshDashboard(only) {", 1)[1].split("\n    }\n", 1)[0]
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
            "  'mac-allowlist': {value: 'ba:bb:bb:bb:bb:bb'}};\n"
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

        self.assertEqual(result["list"], "ba:bb:bb:bb:bb:bb, aa:aa:aa:aa:aa:01")
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
            "    addMacToList('ba:bb:bb:bb:bb:bb', 'aa:aa:aa:aa:aa:01'),\n"
            "    addMacToList('AA:AA:AA:AA:AA:01, ba:bb:bb:bb:bb:bb', 'aa:aa:aa:aa:aa:01'),\n"
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
                "ba:bb:bb:bb:bb:bb, aa:aa:aa:aa:aa:01",
                # Already listed, whatever its case.
                "AA:AA:AA:AA:AA:01, ba:bb:bb:bb:bb:bb",
            ],
        )

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
        self.assertIn("Start", DASHBOARD_HTML)
        self.assertNotIn("Apply current configuration", DASHBOARD_HTML)
        self.assertIn("Reset network", DASHBOARD_HTML)
        self.assertIn("setButtonBusy(event.submitter, true)", DASHBOARD_HTML)
        self.assertIn("Dashboard refresh failed:", DASHBOARD_HTML)
        self.assertNotIn('element("admin-token").addEventListener("change"', DASHBOARD_HTML)
        self.assertIn(
            'element("check-admin-token").addEventListener("click", async () => {\n'
            "      await checkAdminToken();\n",
            DASHBOARD_HTML,
        )
        self.assertIn('id="action-announcer" class="action-announcer" role="status"', DASHBOARD_HTML)
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
        self.assertIn("Device download limit", DASHBOARD_HTML)
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
                "access_rules": False,
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
                "access_rules": False,
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
                "access_rules": False,
            },
        )

    def test_apply_says_whether_access_rules_were_started(self) -> None:
        # Block rules and an allow list and a MAC list are all enforced by Start. The words
        # after a start with nothing else saved must not say that nothing is running.
        from jittersim.config.models import MacPolicy, WebsitePolicy

        self.server.helper_client = FakeHelper()

        def started():
            status, _, body = self.server.write_response_for("/api/apply", {})
            self.assertEqual(status, 200, body)
            return json.loads(body)["applied"]["access_rules"]

        self.assertFalse(started())
        # An allow list that is not switched on does nothing.
        self.repository.save_website_policy(
            WebsitePolicy(mode="unrestricted", allowed_domains=("example.com",))
        )
        self.assertFalse(started())
        self.repository.save_website_policy(
            WebsitePolicy(mode="unrestricted", blocked_domains=("ads.example.com",))
        )
        self.assertTrue(started())
        self.repository.save_website_policy(
            WebsitePolicy(mode="allow_only", allowed_domains=("example.com",))
        )
        self.assertTrue(started())
        self.repository.save_website_policy(WebsitePolicy())
        self.assertFalse(started())
        self.repository.save_mac_policy(
            MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",))
        )
        self.assertTrue(started())

    def test_a_failed_apply_has_the_same_body_as_before_the_summary_existed(self) -> None:
        # The dashboard shows the error and the hint. A summary of what was sent would say
        # that something was started when nothing was.
        self.server.helper_client = FakeHelper(fail_action="apply")

        status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 502)
        failure = json.loads(body)
        self.assertEqual(set(failure), {"error", "hint", "state"})
        self.assertEqual(failure["error"], "network helper apply failed")
        self.assertEqual(failure["state"]["observed_state"], "error")

    def test_a_plan_that_cannot_be_built_has_the_same_body_as_before(self) -> None:
        from jittersim.engine.compiler import PlanError

        self.server.helper_client = FakeHelper()
        with patch("jittersim.api.server.compile_stored_plan", side_effect=PlanError("broken")):
            status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 409)
        self.assertEqual(
            json.loads(body), {"error": "stored configuration cannot be applied"}
        )

    def test_apply_without_a_helper_has_the_same_body_as_before(self) -> None:
        status, _, body = self.server.write_response_for("/api/apply", {})

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "network helper is not configured"})

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
            "function guardDraft() { return false; }\n"
            "function formatMinute(minute) { return String(minute); }\n",
            "editSchedule({id: 'family-day', device_id: null, group_id: 'family',\n"
            "  everyone_else: false, profile_id: 'slow', days_of_week: [0],\n"
            "  start_minute: 540, end_minute: 1020});\n"
            "console.log(JSON.stringify(fields['schedule-device'].value));\n",
        )

        self.assertEqual(result, "group:family")

    def test_loading_schedules_updates_what_depends_on_them(self) -> None:
        result = self._run_dashboard_functions(
            ["loadSchedules"],
            "let savedSchedules = [];\n"
            "const devices = [];\n"
            "const schedules = [{id: 'others', everyone_else: true, enabled: true}];\n"
            "const element = () => ({});\n"
            "async function request() { return {schedules, local_time: '2026-09-26T12:00:00+02:00', time_zone: 'CEST'}; }\n"
            "const rendered = [];\n"
            "function renderSchedules(list) { rendered.push(list.length); }\n"
            "const menus = [];\n"
            "function updateTrafficDeviceOptions() { menus.push(savedSchedules.length); }\n",
            "loadSchedules().then(() => console.log(JSON.stringify({\n"
            "  saved: savedSchedules.length, rendered: rendered, menus: menus})));\n",
        )

        # Live traffic offers Everyone else once an everyone-else schedule loads.
        self.assertEqual(result, {"saved": 1, "rendered": [1], "menus": [1]})

    def test_the_target_menus_keep_their_choice_when_rebuilt(self) -> None:
        result = self._run_dashboard_functions(
            ["deviceName", "scheduleTargetOptions", "renderTargetMenus"],
            "function devicePickerLabel(device) { return device.name + ' (' + device.ipv4_address + ')'; }\n"
            "let devices = [{id: 'alice', name: 'Alice', ipv4_address: '192.168.50.10'}];\n"
            "let groups = [{id: 'family', name: 'Family'}];\n"
            "const node = (tag) => ({tag: tag, children: [], value: '', label: '',\n"
            "  appendChild(child) { this.children.push(child); }});\n"
            # Like a real menu, it shows its first option unless told otherwise.
            "const menu = () => ({children: [], chosen: '',\n"
            "  appendChild(child) { this.children.push(child); },\n"
            "  replaceChildren() { this.children = []; this.chosen = ''; },\n"
            "  get options() { return this.children.flatMap((child) =>\n"
            "    child.tag === 'optgroup' ? child.children : [child]); },\n"
            "  get value() { const values = this.options.map((option) => option.value);\n"
            "    return values.includes(this.chosen) ? this.chosen : values[0] || ''; },\n"
            "  set value(value) { this.chosen = value; }});\n"
            "const menus = {'schedule-device': menu(), 'scenario-start-target': menu()};\n"
            "const element = (id) => menus[id];\n"
            "const document = {createElement: node};\n",
            "renderTargetMenus();\n"
            "menus['schedule-device'].value = 'group:family';\n"
            "menus['scenario-start-target'].value = 'everyone-else';\n"
            "devices = devices.concat([{id: 'bob', name: 'Bob', ipv4_address: '192.168.50.11'}]);\n"
            "renderTargetMenus();\n"
            "const kept = [menus['schedule-device'].value, menus['scenario-start-target'].value];\n"
            "groups = [];\n"
            "renderTargetMenus();\n"
            "const schedule = menus['schedule-device'];\n"
            "console.log(JSON.stringify({kept: kept,\n"
            "  labels: schedule.children.map((child) => child.label || child.value),\n"
            "  gone: schedule.value}));\n",
        )

        self.assertEqual(result["kept"], ["group:family", "everyone-else"])
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

    SCENARIO = {
        "id": "commute",
        "name": "Commute",
        "steps": [{"profile_id": "slow", "duration_seconds": 60}],
        "loop": False,
    }

    def _apply_scenario_network(self) -> None:
        self._serve_live(FakeHelper())
        background = patch.object(self.server.scheduler, "start")
        background.start()
        self.addCleanup(background.stop)
        status, _, body = self.server.write_response_for("/api/apply", {})
        self.assertEqual(status, 200, body)

    def test_saves_starts_and_stops_a_scenario(self) -> None:
        self._apply_scenario_network()
        status, _, body = self.server.write_response_for("/api/scenarios", self.SCENARIO)
        start_status, _, start_body = self.server.write_response_for(
            "/api/scenarios/start", {"scenario_id": "commute", "device_id": "alice"}
        )
        self.assertEqual((status, start_status), (200, 200), (body, start_body))
        run_id = json.loads(start_body)["run"]["id"]

        listing = json.loads(self.server.response_for("/api/scenarios")[2])
        stopped, _, _ = self.server.write_response_for("/api/scenarios/stop", {"id": run_id})
        stopped_again, _, _ = self.server.write_response_for("/api/scenarios/stop", {"id": run_id})
        after_stop = json.loads(self.server.response_for("/api/scenarios")[2])["runs"]
        deleted, _, _ = self.server.write_response_for("/api/scenarios/delete", {"id": "commute"})

        self.assertEqual(listing["scenarios"][0]["total_seconds"], 60)
        run = listing["runs"][0]
        self.assertFalse(run["loop"])
        self.assertTrue(0 < run["total_remaining_seconds"] <= 60)
        self.assertEqual(
            (run["id"], run["scenario_name"], run["target_name"], run["step_index"],
             run["step_count"], run["profile_name"]),
            (run_id, "Commute", "Alice's laptop", 0, 1, "Slow"),
        )
        self.assertTrue(0 < run["step_remaining_seconds"] <= 60)
        self.assertEqual((stopped, stopped_again, deleted), (200, 404, 200))
        self.assertEqual(after_stop, [])
        self.assertEqual(self.repository.list_scenarios(), ())

    def test_scenarios_name_what_is_missing(self) -> None:
        missing_profile = dict(
            self.SCENARIO, steps=[{"profile_id": "ghost", "duration_seconds": 60}]
        )
        status, _, body = self.server.write_response_for("/api/scenarios", missing_profile)
        self.assertEqual(status, 400)
        self.assertIn("profile ghost does not exist", json.loads(body)["error"])
        self.server.write_response_for("/api/scenarios", self.SCENARIO)
        cases = {
            "scenario does not exist": {"scenario_id": "ghost", "device_id": "alice"},
            "device does not exist": {"scenario_id": "commute", "device_id": "ghost"},
            "group does not exist": {"scenario_id": "commute", "group_id": "ghost"},
            "exactly one of a device": {"scenario_id": "commute"},
        }
        for message, payload in cases.items():
            with self.subTest(message=message):
                status, _, body = self.server.write_response_for("/api/scenarios/start", payload)
                self.assertEqual(status, 400)
                self.assertIn(message, json.loads(body)["error"])

    def test_a_scenario_step_names_its_problem(self) -> None:
        steps = [
            {"profile_id": "slow", "duration_seconds": 60},
            {"profile_id": "slow", "duration_seconds": 0},
        ]

        status, _, body = self.server.write_response_for(
            "/api/scenarios", dict(self.SCENARIO, steps=steps)
        )

        self.assertEqual(status, 400)
        self.assertIn("step 2: a step must last 10 seconds to 24 hours", json.loads(body)["error"])

    def test_a_repeating_scenario_reports_no_end(self) -> None:
        self._apply_scenario_network()
        self.server.write_response_for("/api/scenarios", dict(self.SCENARIO, loop=True))
        self.server.write_response_for(
            "/api/scenarios/start", {"scenario_id": "commute", "device_id": "alice"}
        )

        run = json.loads(self.server.response_for("/api/scenarios")[2])["runs"][0]

        self.assertEqual((run["loop"], run["total_remaining_seconds"]), (True, None))

    def test_a_running_everyone_else_scenario_holds_a_class_for_new_devices(self) -> None:
        self._apply_scenario_network()
        self.server.write_response_for("/api/scenarios", self.SCENARIO)
        for index in range(1, 14):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )
        self.server.write_response_for(
            "/api/scenarios/start", {"scenario_id": "commute", "everyone_else": True}
        )

        status, _, _ = self.server.write_response_for(
            "/api/devices",
            {"id": "d14", "name": "D14", "ipv4_address": "192.168.50.34", "profile_id": "slow"},
        )

        self.assertEqual(status, 409)

    def test_editing_a_finished_scenario_leaves_its_runs_finished(self) -> None:
        self._apply_scenario_network()
        self.server.write_response_for("/api/scenarios", self.SCENARIO)
        self.server.write_response_for(
            "/api/scenarios/start", {"scenario_id": "commute", "everyone_else": True}
        )
        for index in range(1, 15):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )

        # Two minutes later the 60-second scenario has ended, and the devices
        # use every class.
        with patch("time.time", return_value=time.time() + 120):
            status, _, _ = self.server.write_response_for(
                "/api/scenarios", dict(self.SCENARIO, loop=True)
            )
            runs = json.loads(self.server.response_for("/api/scenarios")[2])["runs"]

        self.assertEqual((status, runs), (200, []))

    def test_a_timer_stops_the_devices_scenario(self) -> None:
        self._apply_scenario_network()
        self.server.write_response_for("/api/scenarios", self.SCENARIO)
        self.server.write_response_for(
            "/api/scenarios/start", {"scenario_id": "commute", "device_id": "alice"}
        )

        self.server.write_response_for(
            "/api/timers", {"device_id": "alice", "profile_id": "slow", "duration_minutes": 10}
        )

        self.assertEqual(json.loads(self.server.response_for("/api/scenarios")[2])["runs"], [])

    def test_an_everyone_else_scenario_needs_a_free_traffic_class(self) -> None:
        self._apply_scenario_network()
        self.server.write_response_for("/api/scenarios", self.SCENARIO)
        for index in range(1, 15):
            self.repository.save_device(
                Device(f"d{index}", f"D{index}", f"192.168.50.{20 + index}", "slow")
            )
        others = {"scenario_id": "commute", "everyone_else": True}

        full, _, _ = self.server.write_response_for("/api/scenarios/start", others)
        self.repository.delete_device("d14")
        started, _, _ = self.server.write_response_for("/api/scenarios/start", others)
        # The running scenario holds everyone else's class, so a profile adds nothing.
        profile, _, _ = self.server.write_response_for(
            "/api/everyone-else", {"profile_id": "slow"}
        )

        self.assertEqual((full, started, profile), (409, 200, 200))

    def test_everyone_elses_profile_this_minute_follows_its_scenario(self) -> None:
        self._apply_scenario_network()
        self.server.write_response_for("/api/scenarios", self.SCENARIO)
        self.server.write_response_for(
            "/api/scenarios/start", {"scenario_id": "commute", "everyone_else": True}
        )

        others = json.loads(self.server.response_for("/api/everyone-else")[2])
        connected = json.loads(self.server.response_for("/api/connected-devices")[2])

        self.assertEqual(
            (others["active_profile_id"], others["active_schedule_id"], others["profile_id"]),
            ("slow", None, None),
        )
        self.assertEqual(connected["everyone_else_profile_id"], "slow")

    def test_imports_scenarios(self) -> None:
        self.server.write_response_for("/api/scenarios", dict(self.SCENARIO, loop=True))
        exported = self._export(self.server)
        scenarios = self.repository.list_scenarios()
        older = dict(exported)
        del older["scenarios"]

        with ConfigRepository(":memory:") as other_repository:
            other = ControlServer(("127.0.0.1", 0), other_repository)
            try:
                status, _, body = other.write_response_for("/api/import", exported)
                copied = other_repository.list_scenarios()
            finally:
                other.server_close()
        older_status, _, older_body = self.server.write_response_for("/api/import", older)

        self.assertEqual((status, older_status), (200, 200), (body, older_body))
        self.assertEqual(copied, scenarios)
        self.assertEqual(exported["scenarios"][0]["steps"], self.SCENARIO["steps"])
        self.assertEqual(self.repository.list_scenarios(), ())

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
            is_active = False

            @contextlib.contextmanager
            def exclusive(self):
                events.append("hold")
                yield
                events.append("release")

            def prepare_plan(self, *, repository, connected_snapshot=None):
                return PreparedRuntimePlan(plan=compile_stored_plan(repository=repository), states={})

            def sample_usage(self, *, repository):
                events.append("sample")

            def activate_plan(self, *, repository, prepared):
                events.append("activate")

        class RecordingHelper(FakeHelper):
            def apply(self, *, plan, explicit_apply=False):
                events.append("apply")
                return super().apply(plan=plan, explicit_apply=explicit_apply)

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

    def test_a_saved_device_can_be_protected_and_a_protected_one_can_be_saved(self) -> None:
        bob = Device("bob", "Bob", "192.168.50.11", "slow", "ba:bb:bb:bb:bb:bb")
        self.repository.save_device(bob)
        status, _, body = self.server.write_response_for(
            "/api/unaffected-devices", {"mac_address": "ba:bb:bb:bb:bb:bb"}
        )
        # The saved assignment stays and pauses while the device is protected.
        self.assertEqual(status, 200, body)
        self.assertEqual(self.repository.get_device("bob"), bob)

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
        # Saving or editing it is allowed. The assignment stays dormant while protected.
        self.assertEqual(status, 200, body)
        self.assertEqual(self.repository.get_device("carol").mac_address, "cc:cc:cc:cc:cc:cc")
        # A device without a MAC address cannot be told from a protected one so it
        # needs a MAC to keep its profile next to protected clients.
        self.repository.save_device(
            Device("alice", "Alice's laptop", "192.168.50.10", "slow", "ba:aa:aa:aa:aa:aa")
        )
        plan = compile_stored_plan(repository=self.repository)
        self.assertEqual([rule.device_id for rule in plan.mark_rules], ["alice"])
        self.assertIn("cc:cc:cc:cc:cc:cc", plan.protected_mac_addresses)

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
        self.repository.save_profile(Profile(id="slow", name="Renamed after apply"))

        conditions = json.loads(self.server.response_for("/api/conditions")[2])

        # The saved profile changed, but the network still runs the applied plan.
        self.assertIsNotNone(helper.applied_plan)
        self.assertTrue(conditions["applied"])
        self.assertEqual(conditions["devices"]["alice"]["condition"], "degraded")
        self.assertEqual(conditions["devices"]["alice"]["source"]["profile"], "Slow")
        self.assertEqual(conditions["devices"]["alice"]["source"]["kind"], "profile")
        self.assertIsNone(conditions["devices"]["alice"]["next_change_at"])

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
        self.server.wifi_operations.delay = 0

        status, _, body = self.server.write_response_for(
            "/api/access-point",
            {"ssid": "JitterSim-Test", "password": "test-password-123",
             "request_id": json.loads(self.server.response_for("/api/wifi-operation/request-id")[2])["request_id"]},
        )

        # The Wi-Fi restarts after this answer, so the answer only accepts the change.
        self.assertEqual(status, 202)
        self.assertNotIn("test-password-123", body.decode("utf-8"))
        deadline = time.monotonic() + 5
        while self.server.wifi_operations.busy() and time.monotonic() < deadline:
            time.sleep(0.01)
        _, _, stored = self.server.response_for(
            "/api/wifi-operation", query="id=" + json.loads(body)["operation_id"])
        operation = json.loads(stored)["operation"]
        self.assertEqual((operation["state"], operation["ssid"]), ("succeeded", "JitterSim-Test"))
        self.assertNotIn("test-password-123", stored.decode("utf-8"))

    def test_can_promote_an_observed_domain_to_a_profile_or_global_block(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domain": "Chat.OpenAI.com.", "confirm_allow_only": True},
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
            # The reason follows the summary.
            self.assertTrue(json.loads(body)["error"].startswith(text), body)
        self.assertEqual(self.repository.get_website_policy().blocked_domains, ())

        status, _, body = self.server.write_response_for(
            "/api/observations/add-to-profile",
            {"profile_id": "slow", "domains": ["fine.example.com", 5]},
        )
        self.assertEqual(status, 400)
        self.assertTrue(json.loads(body)["error"].startswith("could not add domain to profile"))
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
            {"profile_id": "slow", "domains": ["x.example.com", "y.example.com"], "confirm_allow_only": True},
        )
        self.assertEqual(status, 200)
        profile = self.repository.get_profile("slow")
        self.assertEqual(profile.website_mode, "allow_only")
        self.assertEqual(profile.website_domains[-2:], ("x.example.com", "y.example.com"))

    def test_adding_many_names_to_a_profile_keeps_what_it_has_and_skips_duplicates(self) -> None:
        self.server.write_response_for(
            "/api/observations/add-to-profile", {"profile_id": "slow", "domain": "one.example.com", "confirm_allow_only": True}
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
        self.assertTrue(json.loads(body)["error"].startswith("could not add domain to profile"))

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
        # is paused then and a saved change waits for the Start button.
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
        # The success message says a change applies on its own while profiles are running.
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
                self.assertIn("protected from profiles", json.loads(body)["error"])
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
                self.assertIn("protected from profiles", json.loads(body)["error"])
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
                self.assertIn("protected from profiles", json.loads(body)["error"])
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


