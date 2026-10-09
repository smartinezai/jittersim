"""Applying a profile to one connected device starts it for that device only."""

import json
import threading
import time
import unittest
from unittest.mock import patch

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, ServiceList
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from tests.unit.test_api import FakeHelper, PlanRecordingHelper

AIR = {"ipv4_address": "192.168.50.178", "mac_address": "dc:1e:d5:3e:97:4c"}
AIR_ID = "client-dc1ed53e974c"
PHONE = {"ipv4_address": "192.168.50.179", "mac_address": "dc:1e:d5:3e:97:4d"}
PHONE_ID = "client-dc1ed53e974d"
TIMER = {"device_id": AIR_ID, "profile_id": "low", "duration_minutes": 10}


class TwoClientHelper(PlanRecordingHelper):
    def check_general_profile_support(self):
        return None

    def connected_devices(self):
        air = super().connected_devices()[0]
        return [air, {**air, **PHONE, "hostname": "Phone"}]


class DeviceApplyStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(Profile(id="low", name="daily climb manual",
                                             upload=Impairment(delay_ms=50)))
        self.repository.save_profile(Profile(id="slow-test", name="Slow test",
                                             upload=Impairment(delay_ms=3_000)))
        self.repository.save_device(Device(id="tv", name="TV", ipv4_address="192.168.50.20",
                                           profile_id="low"))
        self.helper = TwoClientHelper()
        self.server = ControlServer(("127.0.0.1", 0), self.repository, helper_client=self.helper)

    def tearDown(self) -> None:
        self.server.server_close()
        self.repository.close()

    def write(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def apply_to(self, client=AIR, profile_id="slow-test"):
        return self.write("/api/connected-devices/apply-profile", {**client, "profile_id": profile_id})

    def reset(self):
        self.assertEqual(self.write("/api/reset", {})[0], 200)
        self.helper.applied_plan = None
        self.helper.plans.clear()

    def running_ids(self):
        return [rule.device_id for rule in self.helper.applied_plan.mark_rules]

    def use_scheduler(self):
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        self.server.scheduler = scheduler
        patcher = patch.object(scheduler, "start")
        patcher.start()
        self.addCleanup(patcher.stop)
        return scheduler

    # The reported failure and the approved repair.

    def test_reset_with_everyone_saved_then_device_apply_starts_only_that_device(self) -> None:
        self.repository.save_general_profile("low")
        self.reset()

        status, result = self.apply_to()

        self.assertEqual(status, 200, result)
        self.assertTrue(result["live_applied"])
        plan = self.helper.applied_plan
        self.assertIsNone(plan.general_profile)
        self.assertIsNone(plan.everyone_else)
        self.assertEqual(self.running_ids(), [AIR_ID])
        upload = [queue for queue in plan.queues if queue.device_id == AIR_ID]
        self.assertEqual({queue.impairment.delay_ms for queue in upload}, {3_000, 0})
        # Everyone stays saved and paused. The Reset still describes the rest.
        self.assertEqual(self.repository.get_general_profile().id, "low")
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("reset", "applied"))
        self.assertIn("Slow test saved for this device", result["message"])
        self.assertIn("Everyone and other saved settings stay paused", result["message"])
        self.assertEqual(result["state"]["device_only"], [AIR_ID])

    def test_the_device_only_start_is_never_an_explicit_start(self) -> None:
        self.reset()
        with patch.object(self.helper, "apply", wraps=self.helper.apply) as apply:
            self.assertEqual(self.apply_to()[0], 200)
        self.assertFalse(apply.call_args.kwargs.get("explicit_apply", False))

    def test_a_second_device_joins_without_resuming_anything_else(self) -> None:
        self.repository.save_general_profile("low")
        self.repository.save_everyone_else_profile_id("low")
        self.repository.save_service_list(ServiceList(
            id="video", name="Video", profile_id="low", hostnames=("video.example",)))
        self.reset()

        self.assertEqual(self.apply_to()[0], 200)
        status, result = self.apply_to(PHONE, "low")
        self.assertEqual(status, 200, result)
        self.assertIn("too, beside 1 device already started on their own", result["message"])

        plan = self.helper.applied_plan
        self.assertEqual(sorted(self.running_ids()), [AIR_ID, PHONE_ID])
        self.assertIsNone(plan.general_profile)
        self.assertIsNone(plan.everyone_else)
        self.assertEqual(plan.service_list_rules, ())
        self.assertEqual(self.repository.get_device_start_scope(), {AIR_ID, PHONE_ID})

    def test_start_resumes_every_saved_setting_and_ends_the_device_only_start(self) -> None:
        self.reset()
        self.apply_to()

        self.assertEqual(self.write("/api/apply", {})[0], 200)

        self.assertEqual(sorted(self.running_ids()), [AIR_ID, "tv"])
        self.assertIsNone(self.repository.get_device_start_scope())
        self.assertEqual(self.repository.get_runtime_state().last_action, "apply")

    def test_apply_to_everyone_ends_the_device_only_start(self) -> None:
        self.reset()
        self.apply_to()

        self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)

        self.assertIsNotNone(self.helper.applied_plan.general_profile)
        self.assertIsNone(self.repository.get_device_start_scope())

    def test_a_running_network_without_everyone_applies_the_saved_plan_as_before(self) -> None:
        self.assertEqual(self.write("/api/apply", {})[0], 200)

        status, result = self.apply_to()

        self.assertEqual(status, 200)
        self.assertTrue(result["live_applied"])
        self.assertEqual(sorted(self.running_ids()), [AIR_ID, "tv"])
        self.assertIsNone(self.repository.get_device_start_scope())

    # Refusals leave everything as it was.

    def test_everyone_running_refuses_before_saving(self) -> None:
        self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
        before = self.helper.applied_plan

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("Everyone uses daily climb manual right now", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertIs(self.helper.applied_plan, before)

    def test_a_panic_latch_refuses_before_saving(self) -> None:
        self.reset()
        self.helper.gateway_state["panic_stop_latched"] = True

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("Panic", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertIsNone(self.helper.applied_plan)

    def test_a_protected_device_is_refused(self) -> None:
        self.repository.save_unaffected_device(
            UnaffectedDevice(mac_address=AIR["mac_address"], display_name="Air"))
        self.reset()

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("protected", result["error"])
        self.assertIsNone(self.helper.applied_plan)

    def test_a_failed_start_keeps_the_choice_and_ends_the_scope(self) -> None:
        self.reset()
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to()

        self.assertEqual(status, 502)
        self.assertFalse(result["live_applied"])
        self.assertIn("did not start", result["error"])
        self.assertEqual(self.repository.get_device(AIR_ID).profile_id, "slow-test")
        self.assertIsNone(self.repository.get_device_start_scope())
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("reset", "error"))

    # Reset, Panic and ordering.

    def test_reset_and_panic_end_the_device_only_start(self) -> None:
        for stop in ("/api/reset", "/api/normal-access"):
            with self.subTest(stop=stop):
                self.reset()
                self.apply_to()
                self.assertIsNotNone(self.repository.get_device_start_scope())

                self.assertEqual(self.write(stop, {})[0], 200)
                self.helper.applied_plan = None
                self.assertIsNone(self.repository.get_device_start_scope())
                # A later saved change stays saved only.
                status, result = self.write("/api/timers", TIMER)
                self.assertEqual(status, 200)
                self.assertFalse(result["live_applied"])
                self.assertIsNone(self.helper.applied_plan)

    def test_saved_changes_reach_only_the_running_device(self) -> None:
        self.use_scheduler()
        self.reset()
        self.apply_to()

        status, result = self.write("/api/timers", TIMER)

        self.assertEqual(status, 200)
        self.assertTrue(result["live_applied"])
        self.assertEqual(self.running_ids(), [AIR_ID])
        upload = [queue for queue in self.helper.applied_plan.queues if queue.device_id == AIR_ID]
        self.assertIn(50, {queue.impairment.delay_ms for queue in upload})
        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")

    def test_an_apply_queued_before_a_reset_starts_nothing(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
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
        self.assertIsNone(self.helper.applied_plan)

    def test_an_apply_overtaken_by_panic_starts_nothing(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
        with patch.object(scheduler, "sample_before_control_change",
                          side_effect=lambda **unused: scheduler.halt()):
            status, _ = self.apply_to()
        self.assertEqual(status, 409)
        self.assertIsNone(self.helper.applied_plan)
        self.assertIsNone(self.repository.get_device(AIR_ID))

    def test_a_changed_helper_intent_refuses_before_saving(self) -> None:
        self.reset()
        with patch.object(self.helper, "control_revision", create=True,
                          side_effect=["a" * 32, "b" * 32]):
            status, result = self.apply_to()
        self.assertEqual(status, 409)
        self.assertIn("Network control changed", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertIsNone(self.helper.applied_plan)

    # Everyone choices while stopped.

    def test_stopping_everyone_while_stopped_starts_nothing(self) -> None:
        self.repository.save_general_profile("low")
        self.reset()

        status, result = self.write("/api/general-profile/stop", {})

        self.assertEqual(status, 200)
        self.assertEqual(result["message"], "Everyone stopped. Nothing else started.")
        self.assertIsNone(self.helper.applied_plan)
        self.assertIsNone(self.repository.get_general_profile())

    def test_stopping_everyone_during_a_device_only_start_keeps_it(self) -> None:
        self.repository.save_general_profile("low")
        self.reset()
        self.apply_to()

        self.assertEqual(self.write("/api/general-profile/stop", {})[0], 200)

        self.assertEqual(self.repository.get_device_start_scope(), {AIR_ID})
        self.assertEqual(self.running_ids(), [AIR_ID])

    # Scheduler, restart and removal.

    def test_scheduled_ticks_keep_the_scope(self) -> None:
        scheduler = self.use_scheduler()
        self.repository.save_general_profile("low")
        self.reset()
        self.assertEqual(self.apply_to()[0], 200)
        self.assertTrue(scheduler.is_active)

        self.repository.save_profile(Profile(id="slow-test", name="Slow test",
                                             upload=Impairment(delay_ms=1_000)))
        self.assertTrue(scheduler.tick(repository=self.repository))

        plan = self.helper.plans[-1]
        self.assertIsNone(plan.general_profile)
        self.assertEqual([rule.device_id for rule in plan.mark_rules], [AIR_ID])
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("reset", "applied"))
        self.assertEqual(self.repository.get_device_start_scope(), {AIR_ID})

    def test_deleting_the_last_running_device_ends_the_start_on_the_next_tick(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
        self.apply_to()

        self.assertEqual(self.write("/api/devices/delete", {"id": AIR_ID})[0], 200)
        self.assertIsNone(self.repository.get_device_start_scope())
        self.helper.reset_called = False
        self.assertFalse(scheduler.tick(repository=self.repository))

        self.assertTrue(self.helper.reset_called)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")
        self.assertEqual(self.repository.list_activity()[0]["message"],
                         "Device-only start ended because its devices were removed")

    def test_restart_recovery_starts_only_the_scoped_devices_again(self) -> None:
        self.repository.save_general_profile("low")
        self.reset()
        self.apply_to()
        self.helper.applied_plan = None

        self.server.recover_saved_network_state()

        self.assertEqual(self.running_ids(), [AIR_ID])
        self.assertIsNone(self.helper.applied_plan.general_profile)
        self.assertFalse(self.helper.reset_called and self.helper.applied_plan is None)
        self.assertEqual(self.repository.list_activity()[0]["message"],
                         "Device-only start applied again after the dashboard restarted")

    def test_an_older_dashboard_sees_a_plain_reset(self) -> None:
        # Rollback: an older dashboard ignores the scope table and only reads the
        # runtime state, which still says the network is reset.
        self.repository.save_general_profile("low")
        self.reset()
        self.apply_to()

        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.last_result), ("reset", "success"))
        # Without the scope the saved plan would be the whole saved configuration.
        self.assertIsNotNone(compile_stored_plan(repository=self.repository).general_profile)


class DeviceStartScopeStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.repository.save_profile(Profile(id="low", name="Low"))
        self.repository.save_device(Device(id="tv", name="TV", ipv4_address="192.168.50.20",
                                           profile_id="low"))

    def tearDown(self) -> None:
        self.repository.close()

    def stop(self, **extra):
        return self.repository.update_runtime_state(
            observed_state="applied", last_action="reset", last_result="success",
            last_error=None, **extra)

    def test_the_scope_counts_only_while_the_network_is_reset(self) -> None:
        self.stop()
        self.repository.add_device_to_start_scope("tv")
        self.assertEqual(self.repository.get_device_start_scope(), {"tv"})
        self.stop(keep_device_scope=True)
        self.assertEqual(self.repository.get_device_start_scope(), {"tv"})
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success", last_error=None)
        self.assertIsNone(self.repository.get_device_start_scope())

    def test_a_device_only_start_never_records_a_non_reset_action(self) -> None:
        with self.assertRaises(ValueError):
            self.repository.update_runtime_state(
                observed_state="applied", last_action="apply", last_result="success",
                last_error=None, keep_device_scope=True)

    def test_scheduled_results_keep_a_scoped_reset_and_skip_a_plain_one(self) -> None:
        self.stop()
        self.assertFalse(self.repository.record_schedule_result(success=True))
        self.repository.add_device_to_start_scope("tv")
        self.assertTrue(self.repository.record_schedule_result(success=False))
        state = self.repository.get_runtime_state()
        self.assertEqual((state.last_action, state.observed_state), ("reset", "error"))
        self.assertEqual(self.repository.get_device_start_scope(), {"tv"})

    def test_deleting_a_device_removes_it_from_the_scope(self) -> None:
        self.stop()
        self.repository.add_device_to_start_scope("tv")
        self.repository.delete_device("tv")
        self.assertIsNone(self.repository.get_device_start_scope())

    def test_reopening_an_existing_database_creates_the_scope_table_once(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.db"
            with ConfigRepository(path) as first:
                first.save_profile(Profile(id="low", name="Low"))
                first.save_device(Device(id="tv", name="TV", ipv4_address="192.168.50.20",
                                         profile_id="low"))
                first.update_runtime_state(observed_state="applied", last_action="reset",
                                           last_result="success", last_error=None)
                first.add_device_to_start_scope("tv")
            with ConfigRepository(path) as second:
                self.assertEqual(second.get_device_start_scope(), {"tv"})


if __name__ == "__main__":
    unittest.main()
