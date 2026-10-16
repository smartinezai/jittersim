"""The saved plan and the scheduler's plan put exception devices beside Everyone."""

import unittest
from dataclasses import replace

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import (
    Device,
    DeviceGroup,
    Impairment,
    Profile,
    ServiceList,
    Timer,
)
from jittersim.config.storage import ConfigRepository
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.plan import GENERAL_QUEUE_ID, Direction
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan

AIR_MAC = "dc:1e:d5:3e:97:4c"
EVERYONE = Profile(id="low", name="Low", upload=Impairment(delay_ms=50),
                   download=Impairment(delay_ms=60))
SLOW = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=3_000),
               download=Impairment(delay_ms=2_000))
CRAWL = Profile(id="crawl", name="Crawl", upload=Impairment(delay_ms=4_000))


class ExceptionPlanFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        for profile in (EVERYONE, SLOW, CRAWL):
            self.repository.save_profile(profile)
        self.repository.save_device(Device("air", "Air", "192.168.50.21", "slow", AIR_MAC))
        self.repository.save_device(Device("tv", "TV", "192.168.50.20", "crawl"))
        self.repository.save_general_profile("low")
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success",
            last_error=None)
        self.repository.add_everyone_exception("air")

    def tearDown(self) -> None:
        self.repository.close()

    def delays(self, plan, device_id, direction=Direction.UPLOAD):
        return [queue.impairment.delay_ms for queue in plan.queues
                if queue.device_id == device_id and queue.direction == direction]


class StoredPlanTests(ExceptionPlanFixture):
    def test_everyone_runs_for_all_and_the_exception_runs_its_own_profile(self) -> None:
        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.general_profile.profile_id, "low")
        self.assertEqual([rule.device_id for rule in plan.mark_rules], ["air"])
        self.assertEqual(plan.mark_rules[0].mark, 2)
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), [50])
        self.assertEqual(self.delays(plan, "air"), [3_000])
        # TV has a saved profile but Everyone covers it. It never joins the plan.
        self.assertEqual(self.delays(plan, "tv"), [])

    def test_a_whole_scope_plan_ignores_the_exceptions(self) -> None:
        plan = compile_stored_plan(repository=self.repository, everyone_exceptions=frozenset())

        self.assertIsNotNone(plan.general_profile)
        self.assertEqual(plan.mark_rules, ())
        self.assertEqual([queue.mark for queue in plan.queues], [1, 1])

    def test_a_plan_after_a_reset_has_no_exceptions(self) -> None:
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None)

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.mark_rules, ())

    def test_service_lists_still_cover_everyone_and_the_device(self) -> None:
        self.repository.save_service_list(ServiceList(
            id="video", name="Video", profile_id="crawl", hostnames=("video.example",)))

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual([rule.list_id for rule in plan.service_list_rules], ["video"])
        self.assertEqual(plan.service_list_rules[0].mark, 3)

    def test_the_device_keeps_its_group_profile_and_limit_fallback(self) -> None:
        self.repository.save_device_group(
            DeviceGroup(id="bedroom", name="Bedroom", device_ids=("air",), profile_id="crawl"))

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(self.delays(plan, "air"), [4_000])

    def test_an_exception_that_became_protected_falls_back_to_everyone(self) -> None:
        self.repository.save_unaffected_device(
            UnaffectedDevice(mac_address=AIR_MAC, display_name="Air"))

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(plan.protected_mac_addresses, (AIR_MAC,))
        self.assertIsNotNone(plan.general_profile)

    def test_a_device_removed_from_the_exceptions_returns_to_everyone_only(self) -> None:
        self.repository.restore_everyone_exceptions(frozenset())

        plan = compile_stored_plan(repository=self.repository)

        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), [50])


class SchedulerPlanTests(ExceptionPlanFixture):
    def scheduler(self):
        return RuntimeScheduler(database_path=":memory:", helper_client=None)

    def test_the_scheduler_plans_the_exception_beside_everyone(self) -> None:
        prepared = self.scheduler().prepare_plan(repository=self.repository, now=1_000.0)
        plan, states = prepared.plan, prepared.states

        self.assertEqual(plan.general_profile.profile_id, "low")
        self.assertEqual([rule.device_id for rule in plan.mark_rules], ["air"])
        self.assertEqual(self.delays(plan, "air"), [3_000])
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), [50])
        self.assertIn("air", states)
        self.assertNotIn("tv", states)

    def test_a_timer_on_the_exception_applies_but_one_on_another_device_does_not(self) -> None:
        self.repository.save_timer(Timer(
            id="air-timer", device_id="air", profile_id="crawl",
            started_at=900, expires_at=2_000))
        self.repository.save_timer(Timer(
            id="tv-timer", device_id="tv", profile_id="slow",
            started_at=900, expires_at=2_000))

        plan = self.scheduler().build_plan(repository=self.repository, now=1_000.0)

        self.assertEqual(self.delays(plan, "air"), [4_000])
        self.assertEqual(self.delays(plan, "tv"), [])
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), [50])

    def test_whole_scope_and_idle_plans_have_no_exceptions(self) -> None:
        scheduler = self.scheduler()
        whole = scheduler.build_plan(
            repository=self.repository, now=1_000.0, everyone_exceptions=frozenset())
        self.assertEqual(whole.mark_rules, ())
        self.assertEqual(self.delays(whole, GENERAL_QUEUE_ID), [50])

        self.repository.restore_everyone_exceptions(frozenset())
        idle = scheduler.build_plan(repository=self.repository, now=1_000.0)
        self.assertEqual(idle.mark_rules, ())

    def test_everyones_roll_is_the_same_with_or_without_the_exception(self) -> None:
        scheduler = self.scheduler()
        with_exception = scheduler.build_plan(repository=self.repository, now=1_000.0)
        without = scheduler.build_plan(
            repository=self.repository, now=1_000.0, everyone_exceptions=frozenset())

        self.assertEqual(
            [queue for queue in with_exception.queues if queue.device_id == GENERAL_QUEUE_ID],
            [queue for queue in without.queues if queue.device_id == GENERAL_QUEUE_ID])

    def test_a_device_with_a_profile_named_like_a_runtime_profile_does_not_collide(self) -> None:
        self.repository.save_profile(replace(EVERYONE, id="runtime-1", name="Runtime one"))
        self.repository.save_general_profile("runtime-1")
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success",
            last_error=None)
        self.repository.add_everyone_exception("air")

        plan = self.scheduler().build_plan(repository=self.repository, now=1_000.0)

        self.assertEqual(plan.general_profile.profile_id, "runtime-1")
        self.assertEqual(self.delays(plan, "air"), [3_000])
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), [50])

    def test_exception_counters_count_while_everyone_alone_has_none(self) -> None:
        scheduler = self.scheduler()
        plan = scheduler.build_plan(repository=self.repository, now=1_000.0)
        scheduler._current_payload = plan_to_payload(plan)
        self.assertEqual(scheduler.counter_device_ids(repository=self.repository), {"air"})

        self.repository.restore_everyone_exceptions(frozenset())
        self.assertEqual(scheduler.counter_device_ids(repository=self.repository), set())


if __name__ == "__main__":
    unittest.main()
