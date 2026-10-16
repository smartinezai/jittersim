"""Applying a profile to one device while Everyone runs: only that device leaves Everyone.

The owner approved this on 4 October 2026. Everyone keeps running for every other
device, with the same profile and the same shared limit. The device runs the profile
that was chosen for it. Nothing else resumes and nothing is cleared silently.
"""

import threading
import time
import unittest
from unittest.mock import patch

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, ServiceList, ServiceQuota
from jittersim.engine.codec import plan_from_payload, plan_to_payload
from jittersim.engine.plan import GENERAL_QUEUE_ID, Direction
from jittersim.engine.service import compile_stored_plan
from tests.unit.test_device_apply_start import (
    AIR,
    AIR_ID,
    PHONE,
    PHONE_ID,
    TIMER,
    DeviceApplyFixture,
)


class EveryoneRunningFixture(DeviceApplyFixture):
    """Everyone runs `low`. TV has a saved profile that Everyone overrides."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
        self.helper.plans.clear()

    def delays(self, plan, device_id, direction=Direction.UPLOAD):
        return {queue.impairment.delay_ms for queue in plan.queues
                if queue.device_id == device_id and queue.direction == direction}

    def last_plan(self):
        plan = self.helper.applied_plan
        # The real helper decodes the same plan from the wire. It must accept it.
        decoded = plan_from_payload(plan_to_payload(plan))
        self.assertEqual(decoded.queues, plan.queues)
        return plan

    def exceptions(self):
        return self.repository.get_everyone_exceptions()

    def raw_rows(self):
        return frozenset(row[0] for row in self.repository._connection.execute(
            "SELECT device_id FROM everyone_exceptions"))


class DeviceApplyBesideEveryoneTests(EveryoneRunningFixture):
    def test_the_device_gets_its_own_profile_and_everyone_keeps_the_rest(self) -> None:
        status, result = self.apply_to()

        self.assertEqual(status, 200, result)
        self.assertTrue(result["live_applied"])
        plan = self.last_plan()
        self.assertEqual(plan.general_profile.profile_id, "low")
        self.assertEqual(self.running_ids(), [AIR_ID])
        self.assertEqual(self.delays(plan, AIR_ID), {3_000})
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), {50})
        # TV is saved with its own profile but Everyone still covers it.
        self.assertNotIn("tv", self.running_ids())
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertEqual(self.repository.get_general_profile().id, "low")
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("apply", "applied"))
        self.assertEqual(self.repository.get_device(AIR_ID).profile_id, "slow-test")
        self.assertEqual(result["state"]["everyone_exceptions"], [AIR_ID])
        self.assertIn("Slow test saved for this device", result["message"])
        self.assertIn("Everyone keeps using daily climb manual for every other device",
                      result["message"])
        self.assertNotIn("takes priority", result["message"])

    def test_it_is_never_an_explicit_start(self) -> None:
        with patch.object(self.helper, "apply", wraps=self.helper.apply) as apply:
            self.assertEqual(self.apply_to()[0], 200)
        self.assertFalse(apply.call_args.kwargs.get("explicit_apply", False))

    def test_a_second_device_joins_and_the_first_keeps_its_profile(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 200, result)
        plan = self.last_plan()
        self.assertEqual(sorted(self.running_ids()), [AIR_ID, PHONE_ID])
        self.assertEqual(self.delays(plan, AIR_ID), {3_000})
        self.assertEqual(self.delays(plan, PHONE_ID), {50})
        self.assertEqual(self.exceptions(), {AIR_ID, PHONE_ID})

    def test_applying_another_profile_to_the_same_device_changes_only_that_device(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)
        shared_before = self.delays(self.helper.applied_plan, GENERAL_QUEUE_ID)

        status, _ = self.apply_to(profile_id="low")

        self.assertEqual(status, 200)
        plan = self.last_plan()
        self.assertEqual(self.delays(plan, AIR_ID), {50})
        self.assertEqual(self.delays(plan, GENERAL_QUEUE_ID), shared_before)
        self.assertEqual(self.exceptions(), {AIR_ID})

    def test_service_lists_and_the_shared_profile_are_unchanged(self) -> None:
        self.repository.save_service_list(ServiceList(
            id="video", name="Video", profile_id="low", hostnames=("video.example",)))
        before = compile_stored_plan(repository=self.repository)

        self.assertEqual(self.apply_to()[0], 200)

        plan = self.helper.applied_plan
        self.assertEqual([rule.list_id for rule in plan.service_list_rules], ["video"])
        self.assertEqual(plan.general_profile, before.general_profile)
        self.assertEqual(plan.global_blocked_addresses, before.global_blocked_addresses)
        self.assertEqual(plan.protected_mac_addresses, before.protected_mac_addresses)

    def test_a_scheduled_tick_keeps_the_exception_and_never_widens_it(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        self.assertTrue(scheduler.is_active)

        self.repository.save_profile(Profile(id="slow-test", name="Slow test",
                                             upload=Impairment(delay_ms=1_000)))
        self.assertTrue(scheduler.tick(repository=self.repository))

        plan = self.last_plan()
        self.assertEqual([rule.device_id for rule in plan.mark_rules], [AIR_ID])
        self.assertEqual(self.delays(plan, AIR_ID), {1_000})
        self.assertEqual(plan.general_profile.profile_id, "low")
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("schedule", "applied"))
        self.assertEqual(self.exceptions(), {AIR_ID})

    def test_saved_changes_reach_only_the_exception_and_everyone(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)

        status, result = self.write("/api/timers", TIMER)

        self.assertEqual(status, 200)
        self.assertTrue(result["live_applied"])
        plan = self.last_plan()
        self.assertEqual(self.running_ids(), [AIR_ID])
        self.assertEqual(self.delays(plan, AIR_ID), {50})
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertEqual(self.repository.get_runtime_state().last_action, "apply")

    def test_a_timer_on_a_device_that_follows_everyone_changes_nothing(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)

        status, result = self.write(
            "/api/timers", {"device_id": "tv", "profile_id": "slow-test", "duration_minutes": 5})

        self.assertEqual(status, 200)
        self.assertTrue(result["live_applied"])
        self.assertEqual(self.running_ids(), [AIR_ID])
        self.assertEqual(self.exceptions(), {AIR_ID})

    def test_the_exception_has_its_own_data_limit_counters(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        scheduler._current_payload = plan_to_payload(self.helper.applied_plan)

        self.assertEqual(scheduler.counter_device_ids(repository=self.repository), {AIR_ID})


class EveryoneRunningRefusalTests(EveryoneRunningFixture):
    def test_a_panic_latch_refuses_before_saving(self) -> None:
        self.helper.gateway_state["panic_stop_latched"] = True
        before = self.helper.applied_plan

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("Panic", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), frozenset())
        self.assertIs(self.helper.applied_plan, before)

    def test_an_unknown_stop_state_refuses_before_saving(self) -> None:
        with patch.object(self.helper, "gateway_status", side_effect=_helper_error):
            status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("did not say", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))

    def test_a_protected_device_is_refused(self) -> None:
        self.repository.save_unaffected_device(
            UnaffectedDevice(mac_address=AIR["mac_address"], display_name="Air"))
        before = self.helper.applied_plan

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("protected", result["error"])
        self.assertIs(self.helper.applied_plan, before)
        self.assertEqual(self.exceptions(), frozenset())

    def test_a_network_that_never_started_is_still_refused_with_a_clear_answer(self) -> None:
        self.repository._connection.execute(
            "UPDATE runtime_state SET last_action = NULL, exceptions_kept = 1")

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("Everyone is saved as daily climb manual", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))

    def test_too_many_classes_refuse_before_saving_and_keep_the_earlier_exceptions(self) -> None:
        for index in range(13):
            self.repository.save_device(Device(
                f"extra-{index:02d}", f"Extra {index}", f"192.168.50.{100 + index}", "low"))
            self.repository.add_everyone_exception(f"extra-{index:02d}")
        self.assertEqual(len(self.exceptions()), 13)
        # Everyone's class and 13 exceptions fill 14 of the 15 classes. TV is not one of them.
        self.assertEqual(self.apply_to()[0], 200)
        self.assertEqual(len(self.exceptions()), 14)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409)
        self.assertIn("15", result["error"])
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertEqual(len(self.exceptions()), 14)
        self.assertNotIn(PHONE_ID, self.exceptions())

    def test_a_service_limit_counts_toward_the_classes_even_without_dns(self) -> None:
        for index in range(13):
            device_id = f"extra-{index:02d}"
            self.repository.save_device(Device(
                device_id, f"Extra {index}", f"192.168.50.{100 + index}", "low"))
            self.repository.add_everyone_exception(device_id)
        # Everyone and 13 exceptions are 14 classes. One service limit makes 15.
        self.repository.save_service_quota(ServiceQuota(
            id="video", device_id="extra-00", name="Video", domains=("video.example",),
            profile_id="low", limit_bytes=1_000))
        self.assertEqual(len(self.exceptions()), 13)
        before = self.exceptions()

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("15 traffic classes", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), before)

    def test_a_failed_start_keeps_the_choice_and_the_earlier_exceptions(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 502)
        self.assertFalse(result["live_applied"])
        self.assertIn("did not start", result["error"])
        self.assertEqual(self.repository.get_device(PHONE_ID).profile_id, "low")
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertEqual(self.repository.get_general_profile().id, "low")
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("error", "apply"))

    def test_a_failed_first_exception_leaves_everyone_alone(self) -> None:
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.apply_to()

        self.assertEqual(status, 502)
        self.assertEqual(self.repository.get_device(AIR_ID).profile_id, "slow-test")
        self.assertEqual(self.exceptions(), frozenset())
        self.helper.fail_action = ""
        # The saved state now plans Everyone for everyone, as before the failed start.
        plan = compile_stored_plan(repository=self.repository)
        self.assertEqual(plan.mark_rules, ())


class FailedSavedChangeTests(EveryoneRunningFixture):
    def test_a_failed_reapply_keeps_the_exception_for_the_retry(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.write("/api/timers", TIMER)

        self.assertEqual(status, 502, result)
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("error", "apply"))
        # A transient failure never moves the device back to Everyone's profile.
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.helper.fail_action = ""
        scheduler = self.server.scheduler
        scheduler.activate(applied_payload=None)
        self.assertTrue(scheduler.tick(repository=self.repository))
        self.assertEqual(self.running_ids(), [AIR_ID])
        self.assertEqual(self.exceptions(), {AIR_ID})


class FailedEveryoneChangeTests(EveryoneRunningFixture):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.apply_to()[0], 200)

    def test_a_failed_apply_to_everyone_keeps_the_exception_until_one_succeeds(self) -> None:
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.write("/api/general-profile", {"profile_id": "slow-test"})

        self.assertEqual(status, 502, result)
        self.assertEqual(self.repository.get_general_profile().id, "slow-test")
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.helper.fail_action = ""
        self.assertEqual(self.write("/api/general-profile", {"profile_id": "slow-test"})[0], 200)
        self.assertEqual(self.exceptions(), frozenset())
        self.assertEqual(self.raw_rows(), frozenset())
        self.assertEqual(self.helper.applied_plan.mark_rules, ())

    def test_a_failed_stop_keeps_the_record_and_a_later_start_ends_it(self) -> None:
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.write("/api/general-profile/stop", {})

        self.assertEqual(status, 502)
        self.assertIsNone(self.repository.get_general_profile())
        # Nothing counts without Everyone, and nothing starts again by surprise.
        self.assertEqual(self.exceptions(), frozenset())
        self.assertEqual(self.raw_rows(), {AIR_ID})
        self.helper.fail_action = ""
        self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
        self.assertEqual(self.raw_rows(), frozenset())
        self.assertEqual(self.helper.applied_plan.mark_rules, ())


class WholeScopeActionTests(EveryoneRunningFixture):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.apply_to()[0], 200)

    def test_start_runs_everyone_for_every_device_and_ends_the_exception(self) -> None:
        self.assertEqual(self.write("/api/apply", {})[0], 200)

        plan = self.last_plan()
        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(plan.general_profile.profile_id, "low")
        self.assertEqual(self.exceptions(), frozenset())
        self.assertEqual(self.repository.get_device(AIR_ID).profile_id, "slow-test")

    def test_a_failed_start_keeps_the_running_exceptions(self) -> None:
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.write("/api/apply", {})

        self.assertEqual(status, 502)
        # The network still runs what it ran. The record and the retry say the same.
        self.assertEqual(self.exceptions(), {AIR_ID})
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("error", "apply"))
        self.helper.fail_action = ""
        self.assertEqual(self.write("/api/apply", {})[0], 200)
        self.assertEqual(self.exceptions(), frozenset())
        self.assertEqual(self.helper.applied_plan.mark_rules, ())

    def test_start_records_the_devices_usage_before_it_ends_the_exception(self) -> None:
        scheduler = self.use_scheduler()
        seen = []
        original = scheduler.sample_usage

        def sample(**arguments):
            seen.append(self.exceptions())
            return original(**arguments)

        with patch.object(scheduler, "sample_usage", side_effect=sample):
            self.assertEqual(self.write("/api/apply", {})[0], 200)

        self.assertEqual(seen, [frozenset({AIR_ID})])
        self.assertEqual(self.exceptions(), frozenset())

    def test_apply_to_everyone_ends_the_exception(self) -> None:
        self.assertEqual(self.write("/api/general-profile", {"profile_id": "slow-test"})[0], 200)

        plan = self.last_plan()
        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(plan.general_profile.profile_id, "slow-test")
        self.assertEqual(self.exceptions(), frozenset())

    def test_stopping_everyone_ends_the_exception_and_starts_no_hidden_paused_setting(self) -> None:
        status, _ = self.write("/api/general-profile/stop", {})

        self.assertEqual(status, 200)
        self.assertEqual(self.exceptions(), frozenset())
        self.assertIsNone(self.repository.get_general_profile())
        plan = self.last_plan()
        # With no Everyone the saved plan is the saved devices, exactly like before.
        self.assertIsNone(plan.general_profile)
        self.assertEqual(sorted(self.running_ids()), [AIR_ID, "tv"])

    def test_reset_and_panic_end_it_and_later_saves_stay_saved_only(self) -> None:
        for stop in ("/api/reset", "/api/normal-access"):
            with self.subTest(stop=stop):
                self.assertEqual(self.write(stop, {})[0], 200)
                self.assertEqual(self.exceptions(), frozenset())
                self.assertEqual(self.raw_rows(), frozenset())
                self.helper.applied_plan = None
                status, result = self.write("/api/timers", TIMER)
                self.assertEqual(status, 200)
                self.assertFalse(result["live_applied"])
                self.assertIsNone(self.helper.applied_plan)
                # Put the network back for the next stop.
                self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
                self.assertEqual(self.apply_to()[0], 200)

    def test_an_older_dashboard_start_ends_it(self) -> None:
        # Rollback: an older dashboard knows nothing of the table. Its own runtime
        # writes still end the exception, so it can never come back by surprise.
        self.repository._connection.execute(
            "UPDATE runtime_state SET observed_state = 'applying', last_action = 'apply', "
            "last_result = NULL, last_error = NULL")

        self.assertEqual(self.raw_rows(), frozenset())
        self.assertEqual(self.exceptions(), frozenset())

    def test_removing_the_device_returns_it_to_everyone_without_resuming_anything(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.write("/api/devices/delete", {"id": AIR_ID})[0], 200)

        plan = self.last_plan()
        self.assertEqual(plan.mark_rules, ())
        self.assertEqual(plan.general_profile.profile_id, "low")
        self.assertEqual(self.exceptions(), frozenset())
        self.assertEqual(self.repository.get_general_profile().id, "low")
        # TV keeps its saved profile and is covered by Everyone as before.
        self.assertEqual(self.repository.get_device("tv").profile_id, "low")


class OrderingAndRestartTests(EveryoneRunningFixture):
    def test_an_apply_queued_before_a_reset_starts_nothing(self) -> None:
        scheduler = self.use_scheduler()
        results = {}
        with scheduler.exclusive():
            waiting = threading.Thread(target=lambda: results.setdefault("apply", self.apply_to()))
            waiting.start()
            time.sleep(0.2)
            resetting = threading.Thread(
                target=lambda: results.setdefault("reset", self.write("/api/reset", {})))
            resetting.start()
            time.sleep(0.2)
        waiting.join(5)
        resetting.join(5)

        self.assertEqual(results["reset"][0], 200)
        self.assertEqual(results["apply"][0], 409)
        self.assertIn("Reset or Panic ran while this Apply waited", results["apply"][1]["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), frozenset())
        self.assertTrue(all(not plan.mark_rules for plan in self.helper.plans))

    def test_an_apply_overtaken_by_panic_starts_nothing(self) -> None:
        scheduler = self.use_scheduler()
        with patch.object(scheduler, "sample_before_control_change",
                          side_effect=lambda **unused: scheduler.halt()):
            status, _ = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), frozenset())

    def test_a_changed_helper_intent_refuses_before_saving(self) -> None:
        with patch.object(self.helper, "control_revision", create=True,
                          side_effect=["a" * 32, "b" * 32]):
            status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("Network control changed", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), frozenset())

    def test_a_client_that_disappeared_is_refused(self) -> None:
        ghost = {"ipv4_address": "192.168.50.99", "mac_address": "dc:1e:d5:3e:97:99"}

        status, result = self.apply_to(ghost)

        self.assertEqual(status, 409)
        self.assertIn("no longer verified", result["error"])
        self.assertEqual(self.exceptions(), frozenset())

    def test_restart_recovery_applies_the_exception_beside_everyone_again(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)
        self.helper.applied_plan = None

        self.server.recover_saved_network_state()

        plan = self.last_plan()
        self.assertEqual(self.running_ids(), [AIR_ID])
        self.assertEqual(plan.general_profile.profile_id, "low")
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertEqual(self.repository.list_activity()[0]["message"],
                         "Configuration applied again after the dashboard restarted")

    def test_the_runtime_state_names_the_exceptions(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)

        payload = self.server._runtime_state_with_running()

        self.assertEqual(payload["everyone_exceptions"], [AIR_ID])
        self.assertEqual(payload["device_only"], [])


def _helper_error(*_args, **_kwargs):
    from jittersim.networking.helper import HelperClientError

    raise HelperClientError("no answer")


if __name__ == "__main__":
    unittest.main()
