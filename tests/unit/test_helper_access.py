"""The helper stays useful when the DNS service or the access restore fails."""

import tempfile
import unittest
from pathlib import Path

from jittersim.config.models import Device, Impairment, MacPolicy, Profile, SiteBlock
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
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

    def test_panic_still_works_after_the_dns_service_failed(self):
        self.executor.fail_restarts = 100
        with self.assertLogs(level="ERROR"):
            self.service.handle_payload(apply_payload(self.blocking))
        answer = self.service.handle_payload({"action": "normal-access"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["access_disabled"])
        self.assertFalse(self.manager.applied.options.enabled)

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


if __name__ == "__main__":
    unittest.main()
