import json
import unittest

from jittersim.api.server import ControlServer, DASHBOARD_HTML
from jittersim.config.models import Device, DomainObservation, Impairment, Profile
from jittersim.config.storage import ConfigRepository
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

    def configure_access_point(self, *, ssid, password):
        return {"ok": True, "action": "configure-ap", "commands_run": 3}

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
        return {"devices": {"alice": 0}, "services": {}}


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

    def test_router_endpoint_describes_the_safe_manual_boundary(self) -> None:
        status, _, body = self.server.response_for("/api/router")

        self.assertEqual(status, 200)
        router = json.loads(body)
        self.assertEqual(router["model"], "Vodafone TG3442DE")
        self.assertFalse(router["automatic_wifi_control"])

    def test_uplink_endpoint_reports_the_pi_connection_and_manual_router_boundary(self) -> None:
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
                    "wifi_control": "manual",
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

    def test_dashboard_contains_configuration_and_network_controls(self) -> None:
        status, content_type, body = self.server.response_for("/")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertEqual(body.decode("utf-8"), DASHBOARD_HTML)
        self.assertIn("Checking control mode", DASHBOARD_HTML)
        self.assertIn("Save profile", DASHBOARD_HTML)
        self.assertIn("Save device", DASHBOARD_HTML)
        self.assertIn("Apply current configuration", DASHBOARD_HTML)
        self.assertIn("Reset network", DASHBOARD_HTML)
        self.assertIn("Connected clients", DASHBOARD_HTML)
        self.assertIn("connected-devices", DASHBOARD_HTML)
        self.assertIn("Upstream router connection", DASHBOARD_HTML)
        self.assertIn("/api/uplink", DASHBOARD_HTML)
        self.assertIn("Open router Wi-Fi settings", DASHBOARD_HTML)
        self.assertIn("Match the Pi test Wi-Fi manually", DASHBOARD_HTML)
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
