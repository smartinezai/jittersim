import unittest

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, ServiceList
from jittersim.config.storage import ConfigRepository
from jittersim.engine.compiler import PlanError
from jittersim.engine.plan import GENERAL_QUEUE_ID
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.engine.service_lists import build_service_list_rules

CHOPPY = Profile(
    id="choppy",
    name="Choppy",
    upload=Impairment(delay_ms=200, loss_percent=5),
    download=Impairment(delay_ms=400, bandwidth_kbit=1_500),
)


class ServiceListEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(CHOPPY)

    def save_list(self, list_id="streaming", services=("netflix",), **kwargs) -> ServiceList:
        item = ServiceList(
            id=list_id,
            name=list_id.title(),
            profile_id=kwargs.pop("profile_id", "choppy"),
            services=services,
            **kwargs,
        )
        self.repository.save_service_list(item)
        return item

    def test_rules_carry_the_profile_impairments_and_the_expanded_hostnames(self) -> None:
        saved = self.save_list(hostnames=("extra.example",))

        (rule,) = build_service_list_rules(self.repository)

        self.assertEqual(rule.list_id, "streaming")
        self.assertEqual(rule.domains, saved.domains)
        self.assertEqual(rule.upload_impairment, CHOPPY.upload)
        self.assertEqual(rule.download_impairment, CHOPPY.download)

    def test_disabled_lists_are_left_out(self) -> None:
        self.save_list("on")
        self.save_list("off", services=("claude",), enabled=False)

        self.assertEqual([rule.list_id for rule in build_service_list_rules(self.repository)], ["on"])

    def test_a_list_with_a_missing_profile_cannot_be_applied(self) -> None:
        self.save_list()
        self.repository._connection.execute("PRAGMA foreign_keys = OFF")
        self.repository._connection.execute("DELETE FROM profiles WHERE id = 'choppy'")

        with self.assertRaisesRegex(PlanError, "missing profile"):
            build_service_list_rules(self.repository)

    def test_a_plan_with_only_lists_needs_no_device_or_general_profile(self) -> None:
        self.save_list()

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.mark_rules, ())
        self.assertIsNone(plan.general_profile)
        self.assertEqual([rule.list_id for rule in plan.service_list_rules], ["streaming"])

    def test_lists_sit_beside_device_profiles(self) -> None:
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.save_list()

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual([rule.mark for rule in plan.mark_rules], [1])
        self.assertEqual(plan.service_list_rules[0].mark, 2)

    def test_lists_sit_beside_the_general_profile(self) -> None:
        self.repository.save_general_profile("normal")
        self.save_list()

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.general_profile.profile_id, "normal")
        self.assertEqual(plan.service_list_rules[0].mark, 2)
        self.assertIn(GENERAL_QUEUE_ID, {queue.device_id for queue in plan.queues})

    def test_unaffected_devices_stay_listed_in_the_plan(self) -> None:
        self.repository.save_unaffected_device(UnaffectedDevice("aa:bb:cc:dd:ee:ff"))
        self.save_list()

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.protected_mac_addresses, ("aa:bb:cc:dd:ee:ff",))
        self.assertEqual(len(plan.service_list_rules), 1)


class _Helper:
    def __init__(self) -> None:
        self.plans = []

    def apply(self, *, plan):
        self.plans.append(plan)
        return {"commands_run": 1}

    def observe_dns_queries(self):
        return []

    def traffic_counters(self):
        return {"devices": {}, "services": {}}


class RuntimeServiceListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(CHOPPY)
        self.repository.save_service_list(
            ServiceList(id="streaming", name="Streaming", profile_id="choppy",
                        services=("netflix",))
        )
        self.helper = _Helper()

    def test_the_scheduler_applies_lists_with_and_without_device_profiles(self) -> None:
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})
        self.assertTrue(scheduler.tick(repository=self.repository, now=100))
        self.assertEqual(len(self.helper.plans[-1].service_list_rules), 1)

        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.assertTrue(scheduler.tick(repository=self.repository, now=101))
        plan = self.helper.plans[-1]
        self.assertEqual((plan.mark_rules[0].mark, plan.service_list_rules[0].mark), (1, 2))

    def test_the_scheduler_applies_lists_during_the_general_profile(self) -> None:
        self.repository.save_general_profile("normal")
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})

        self.assertTrue(scheduler.tick(repository=self.repository, now=100))

        plan = self.helper.plans[-1]
        self.assertIsNotNone(plan.general_profile)
        self.assertEqual(plan.service_list_rules[0].list_id, "streaming")

    def test_the_plan_is_not_reapplied_while_nothing_changes(self) -> None:
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        scheduler.activate(applied_payload={})
        scheduler.tick(repository=self.repository, now=100)

        self.assertFalse(scheduler.tick(repository=self.repository, now=101))
        self.assertEqual(len(self.helper.plans), 1)


if __name__ == "__main__":
    unittest.main()
