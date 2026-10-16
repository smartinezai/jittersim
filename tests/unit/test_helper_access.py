"""The helper stays useful when the DNS service or the access restore fails."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jittersim.config.models import AccessOptions, Device, Impairment, MacPolicy, Profile, SiteBlock
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService, restore_access_at_start
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor

TOPOLOGY = Topology("eth0", "wlan0")


def apply_payload(access):
    plan = compile_plan(
        devices=(Device("child", "Child", "192.168.50.10", "slow", "02:00:00:00:00:01"),),
        profiles={"slow": Profile("slow", "Slow", upload=Impairment(delay_ms=50))},
        access=access,
    )
    return {"action": "apply", "plan": plan_to_payload(plan)}


class HelperAccessFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.executor = AccessExecutor(self.directory)
        self.manager = AccessManager(
            executor=self.executor,
            topology=TOPOLOGY,
            directory=self.directory,
            runtime_path=self.directory / "runtime.json",
        )
        self.service = HelperService(
            NetworkApplier(executor=FakeExecutor(), topology=TOPOLOGY),
            active_plan_path=self.directory / "active-plan.json",
            access_manager=self.manager,
        )
        self.blocking = AccessPlan(
            blocks=(SiteBlock("video", "Video", ("video.example.com",), device_id="child"),),
            clients=(AccessClient("child", "192.168.50.10", "02:00:00:00:00:01", ("video",)),),
        )

    def test_a_failing_dns_service_fails_the_apply_with_a_clear_message(self):
        self.executor.fail_restarts = 100
        with self.assertLogs(level="ERROR"):
            answer = self.service.handle_payload(apply_payload(self.blocking))
        self.assertFalse(answer["ok"])
        self.assertIn("filtered DNS service did not start", answer["error"])
        self.assertIn("Panic and Normal access still work", answer["error"])
        # The way out when Unbound cannot run on this Pi.
        self.assertIn(
            "Turn household access rules off on the Access and rules page or check jittersim-dns",
            answer["error"],
        )

    def test_panic_still_works_after_the_dns_service_failed(self):
        self.executor.fail_restarts = 100
        with self.assertLogs(level="ERROR"):
            self.service.handle_payload(apply_payload(self.blocking))
        answer = self.service.handle_payload({"action": "normal-access"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["access_disabled"])
        self.assertFalse(self.manager.applied.options.enabled)

    def test_panic_reports_incomplete_recovery_while_wifi_join_limits_stay(self):
        # G1: the firewall opens, but hostapd refuses to clear the join limits.
        # Excluded devices still cannot join, so Panic must ask for a retry.
        restrictive = AccessPlan(
            mac=MacPolicy("denylist", (), ("02:00:00:00:00:01",)),
            options=AccessOptions(reject_wifi_joins=True),
        )
        with patch.object(self.manager, "admission_supported", return_value=True):
            self.manager.apply(restrictive)
            original = self.executor.run

            def refuse(command):
                if command.executable == "hostapd_cli":
                    original(command)
                    return CommandResult(return_code=0, stdout="FAIL\n")
                return original(command)

            with patch.object(self.executor, "run", refuse), self.assertLogs(level="WARNING"):
                answer = self.service.handle_payload({"action": "normal-access"})
            self.assertFalse(answer["ok"], answer)
            self.assertFalse(answer["access_disabled"])
            self.assertIn("not fully restored", answer["error"])
            self.assertIn("join", answer["error"])
            self.assertFalse(self.manager.applied.options.enabled)
            revision = self.service.handle_payload({"action": "control-revision"})
            self.assertTrue(revision["recovery_required"])
            # The retry clears the join limits and confirms the recovery.
            answer = self.service.handle_payload({"action": "normal-access"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["access_disabled"])
        revision = self.service.handle_payload({"action": "control-revision"})
        self.assertFalse(revision["recovery_required"])

    def test_an_apply_without_dns_rules_succeeds_while_the_resolver_is_broken(self):
        self.executor.fail_restarts = 100
        mac_only = AccessPlan(mac=MacPolicy("denylist", (), ("02:00:00:00:00:02",)))
        answer = self.service.handle_payload(apply_payload(mac_only))
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.manager.applied, mac_only)

    def test_other_access_failures_keep_the_short_message(self):
        # The rules fail their check before the DNS step is reached.
        self.executor.fail_on_index = 0
        with self.assertLogs(level="ERROR"):
            answer = self.service.handle_payload(
                {"action": "access-apply", "policy": self.blocking.payload()}
            )
        self.assertEqual(answer, {"ok": False, "error": "access rules could not be applied"})

    def test_a_failed_restore_at_start_keeps_the_helper_and_normal_access_alive(self):
        (self.directory / "policy.json").write_text("{")
        with self.assertLogs(level="ERROR") as logs:
            self.assertFalse(restore_access_at_start(self.manager))
        self.assertIn("test network stays closed", "\n".join(logs.output))
        guard = [c for c in self.executor.commands if c.executable == "nft"]
        self.assertIn('iifname "wlan0" drop', guard[0].stdin)
        self.assertIn("closed", self.manager.status()["error"])
        # The helper still answers and Normal access repairs the damaged state.
        self.assertTrue(self.service.handle_payload({"action": "ping"})["ok"])
        answer = self.service.handle_payload({"action": "normal-access"})
        self.assertTrue(answer["ok"], answer)
        self.assertIsNone(self.manager.status()["error"])
        last = [c for c in self.executor.commands if c.executable == "nft"][-1]
        self.assertIn("delete table inet jittersim_access_transition", last.stdin)

    def test_a_good_restore_reports_success(self):
        self.assertTrue(restore_access_at_start(self.manager))


class IncompleteReleaseTests(unittest.TestCase):
    """N4: every caller of a household release honours ``release_incomplete``.

    The firewall opens, but hostapd refuses to clear the Wi-Fi join limits, so
    excluded household devices still cannot join. No caller may report that as
    a completed release.
    """

    MAC = "02:00:00:00:00:01"

    def setUp(self):
        HelperAccessFailureTests.setUp(self)
        self.restrictive = AccessPlan(
            mac=MacPolicy("denylist", (), (self.MAC,)),
            options=AccessOptions(reject_wifi_joins=True),
        )
        self.disabled = replace(
            self.restrictive, options=replace(self.restrictive.options, enabled=False))
        admission = patch.object(self.manager, "admission_supported", return_value=True)
        admission.start()
        self.addCleanup(admission.stop)

    def refusing_hostapd(self):
        original = self.executor.run

        def refuse(command):
            if command.executable == "hostapd_cli":
                original(command)
                return CommandResult(return_code=0, stdout="FAIL\n")
            return original(command)

        return patch.object(self.executor, "run", refuse)

    def test_an_ordinary_disable_reports_the_join_limits_that_remain(self):
        answer = self.service.handle_payload(
            {"action": "access-apply", "policy": self.restrictive.payload()})
        self.assertTrue(answer["ok"], answer)
        with self.refusing_hostapd(), self.assertLogs(level="WARNING"):
            answer = self.service.handle_payload(
                {"action": "access-apply", "policy": self.disabled.payload()})
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason"], "release-incomplete")
        self.assertIn("join", answer["error"])
        self.assertFalse(self.manager.applied.options.enabled)
        # A retry clears the join limits and confirms the release.
        answer = self.service.handle_payload(
            {"action": "access-apply", "policy": self.disabled.payload()})
        self.assertTrue(answer["ok"], answer)
        self.assertFalse(self.manager.release_incomplete)

    def test_a_combined_apply_does_not_start_or_clear_the_stop_on_an_incomplete_release(self):
        self.assertTrue(self.service.handle_payload(apply_payload(self.restrictive))["ok"])
        started = self.service._active_plan
        self.service._panic_stop_latched = True
        with self.refusing_hostapd(), self.assertLogs(level="WARNING"):
            answer = self.service.handle_payload(
                {**apply_payload(self.disabled), "explicit_apply": True})
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason"], "release-incomplete")
        self.assertIn("join", answer["error"])
        # The Panic stop is not cleared and the new profiles did not start.
        self.assertTrue(self.service._panic_stop_latched)
        self.assertIs(self.service._active_plan, started)

    def test_startup_restoration_of_a_panic_stop_reports_an_incomplete_release(self):
        self.manager.apply(self.restrictive)
        with self.refusing_hostapd(), self.assertLogs(level="WARNING") as logs:
            self.assertFalse(restore_access_at_start(self.manager, panic_stopped=True))
        self.assertIn("join", "\n".join(logs.output))
        self.assertFalse(self.manager.applied.options.enabled)

    def test_startup_restoration_of_saved_off_rules_reports_an_incomplete_release(self):
        self.manager.apply(self.restrictive)
        self.manager.apply(self.disabled)
        with self.refusing_hostapd(), self.assertLogs(level="WARNING"):
            self.assertFalse(restore_access_at_start(self.manager))


if __name__ == "__main__":
    unittest.main()
