"""Access-rule edits through the API and privileged Unix helper."""

import http.client
import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from jittersim.config.models import Impairment, MacPolicy, Profile, WebsitePolicy
from jittersim.engine.runtime import RuntimeScheduler
from tests.integration import test_api_helper


class AccessApiTests(unittest.TestCase):
    setUp = test_api_helper.ApiHelperIntegrationTests.setUp
    tearDown = test_api_helper.ApiHelperIntegrationTests.tearDown

    def version(self):
        return self.repository.get_runtime_state().configuration_version

    def write(self, path, payload=None):
        return self.api_server.write_response_for(
            path, {"version": self.version(), **(payload or {})}
        )

    def status(self):
        status, _, body = self.api_server.response_for("/api/access")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_saved_applied_reset_and_disable_are_distinct(self):
        rule = {"id": "video", "name": "Video", "domains": ["example.com"], "device_id": "alice"}
        self.assertEqual(self.write("/api/site-blocks", {"rule": rule})[0], 200)
        self.assertFalse(self.status()["applied"])
        self.assertEqual(self.write("/api/access/apply")[0], 200)
        self.assertTrue(self.status()["applied"])
        self.assertFalse(
            self.access_manager.applied.permits_host("192.168.50.10", "sub.example.com")
        )
        self.assertEqual(self.write("/api/reset")[0], 200)
        self.assertTrue(self.status()["applied"])
        self.assertFalse(
            self.access_manager.applied.permits_host("192.168.50.10", "sub.example.com")
        )
        self.assertEqual(self.write("/api/access/disable")[0], 400)
        self.assertEqual(self.write("/api/access/disable", {"confirmed": True})[0], 200)
        self.assertTrue(
            self.access_manager.applied.permits_host("192.168.50.10", "sub.example.com")
        )
        self.assertEqual(len(self.repository.list_site_blocks()), 1)
        # An old tab cannot restore restrictions after another tab changes them.
        self.assertEqual(self.write("/api/access/apply", {"version": 0})[0], 409)

    def test_panic_is_normal_access_and_reports_the_runtime_state(self):
        rule = {"id": "video", "name": "Video", "domains": ["example.com"], "device_id": "alice"}
        self.write("/api/site-blocks", {"rule": rule})
        self.assertEqual(self.write("/api/access/apply")[0], 200)
        status, _, body = self.api_server.write_response_for("/api/normal-access", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"]["observed_state"], "reset")
        self.assertFalse(self.access_manager.applied.options.enabled)
        self.assertFalse(self.repository.access_settings().access_options.enabled)
        self.assertTrue(
            self.access_manager.applied.permits_host("192.168.50.10", "sub.example.com")
        )

    def test_failed_rule_change_reports_pending_without_replacing_working_policy(self):
        self.write(
            "/api/site-blocks",
            {"rule": {"id": "video", "name": "Video", "domains": ["example.com"]}},
        )
        self.assertEqual(self.write("/api/access/apply")[0], 200)
        # A changed rule still needs the DNS service. Removing every rule would not.
        self.write(
            "/api/site-blocks",
            {"rule": {"id": "video", "name": "Video", "domains": ["example.org"]}},
        )
        self.access_manager.executor.fail_restarts = 1
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.write("/api/access/apply")[0], 503)
        self.assertFalse(self.status()["applied"])
        self.assertIn("restored", self.status()["error"])
        self.assertFalse(self.access_manager.applied.permits_host("192.168.50.10", "example.com"))

    def test_quick_block_never_reactivates_a_dormant_mac_list(self):
        dormant = "02:00:00:00:00:01"
        selected = "02:00:00:00:00:02"
        self.repository.save_access_policies(MacPolicy("disabled", (), (dormant,)), WebsitePolicy())
        self.assertEqual(
            self.write("/api/access/device", {"mac_address": selected, "blocked": True})[0], 200
        )
        self.assertEqual(self.access_manager.applied.mac.denylist, (selected,))
        self.write("/api/access/disable", {"confirmed": True})
        self.assertEqual(
            self.write("/api/access/device", {"mac_address": dormant, "blocked": True})[0], 409
        )

    def test_automation_keeps_pending_access_edits_out_of_the_applied_rules(self):
        scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.api_server.helper_client
        )
        self.api_server.scheduler = scheduler
        rule = {"id": "blocked", "name": "Blocked", "domains": ["example.com"]}
        self.assertEqual(self.write("/api/site-blocks", {"rule": rule})[0], 200)
        with patch.object(scheduler, "start"):
            self.assertEqual(self.write("/api/apply")[0], 200)
        self.assertEqual(self.write("/api/site-blocks/delete", {"id": "blocked"})[0], 200)
        # A scheduled profile transition must not release the saved site block.
        self.repository.save_profile(Profile("slow", "Slow", upload=Impairment(delay_ms=200)))
        self.assertTrue(scheduler.tick(repository=self.repository))
        self.assertFalse(self.status()["applied"])
        self.assertFalse(self.access_manager.applied.permits_host("192.168.50.10", "example.com"))
        self.assertEqual(self.write("/api/access/apply")[0], 200)
        self.assertTrue(self.access_manager.applied.permits_host("192.168.50.10", "example.com"))
        # Nor may another transition turn an un-applied new block on.
        self.assertEqual(self.write("/api/site-blocks", {"rule": rule})[0], 200)
        self.repository.save_profile(Profile("slow", "Slow", upload=Impairment(delay_ms=400)))
        self.assertTrue(scheduler.tick(repository=self.repository))
        self.assertTrue(self.access_manager.applied.permits_host("192.168.50.10", "example.com"))

    def test_panic_stops_automation_and_access_even_when_dns_is_broken(self):
        scheduler = RuntimeScheduler(
            database_path=":memory:", helper_client=self.api_server.helper_client
        )
        self.api_server.scheduler = scheduler
        rule = {"id": "blocked", "name": "Blocked", "domains": ["example.com"]}
        self.write("/api/site-blocks", {"rule": rule})
        with patch.object(scheduler, "start"):
            self.assertEqual(self.write("/api/apply")[0], 200)
        self.access_manager.executor.fail_restarts = 100
        # Panic deliberately has no settings-version or confirmation precondition.
        self.assertEqual(self.api_server.write_response_for("/api/normal-access", {})[0], 200)
        self.assertTrue(self.status()["normal_access"])
        self.assertFalse(self.repository.get_access_options().enabled)
        self.assertEqual(len(self.repository.list_site_blocks()), 1)
        self.assertFalse(self.active_plan_path.exists())
        self.assertFalse(scheduler.tick(repository=self.repository))
        self.api_server.recover_saved_network_state()
        self.assertTrue(self.status()["normal_access"])
        self.assertFalse(scheduler.tick(repository=self.repository))

    def test_panic_reports_partial_failure_but_attempts_both_operations(self):
        self.assertEqual(self.write("/api/apply")[0], 200)
        self.helper_executor.fail_on_index = len(self.helper_executor.commands)
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.api_server.write_response_for("/api/normal-access", {})[0], 503)
        self.assertFalse(self.access_manager.applied.options.enabled)
        self.assertFalse(self.status()["normal_access"])
        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")
        self.assertEqual(self.api_server.write_response_for("/api/normal-access", {})[0], 200)
        self.assertTrue(self.status()["normal_access"])

    def test_panic_attempts_cleanup_even_when_settings_cannot_be_written(self):
        self.assertEqual(self.write("/api/apply")[0], 200)
        with patch.object(
            self.repository,
            "save_access_policies",
            side_effect=sqlite3.OperationalError("disk full"),
        ):
            status, _, body = self.api_server.write_response_for("/api/normal-access", {})
        self.assertEqual(status, 503)
        self.assertIn("could not be saved", json.loads(body)["error"])
        self.assertFalse(self.access_manager.applied.options.enabled)
        self.assertFalse(self.active_plan_path.exists())

    def test_private_reads_and_writes_require_the_admin_token(self):
        worker = threading.Thread(target=self.api_server.serve_forever, daemon=True)
        worker.start()
        try:
            host = f"127.0.0.1:{self.api_server.server_port}"
            for path, method in (
                ("/api/access", "GET"),
                ("/api/access/disable", "POST"),
                ("/api/site-blocks", "POST"),
                ("/api/normal-access", "POST"),
            ):
                connection = http.client.HTTPConnection(host, timeout=3)
                connection.request(
                    method,
                    path,
                    body="{}" if method == "POST" else None,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(connection.getresponse().status, 401)
                connection.close()
        finally:
            self.api_server.shutdown()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
