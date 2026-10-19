"""Applying a profile to one device while Everyone runs: only that device leaves Everyone.

The owner approved this on 4 October 2026. Everyone keeps running for every other
device, with the same profile and the same shared limit. The device runs the profile
that was chosen for it. Nothing else resumes and nothing is cleared silently.
"""

import json
import threading
import time
import unittest
from unittest.mock import patch

from jittersim.config.address_follow import AddressFollowStore
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, ServiceList, ServiceQuota
from jittersim.engine import runtime as runtime_module
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

# A private Wi-Fi address: the locally administered bit is set.
NEW_AIR_MAC = "02:11:22:33:44:55"


class EveryoneRunningFixture(DeviceApplyFixture):
    """Everyone runs `low`. TV has a saved profile that Everyone overrides."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
        self.helper.plans.clear()

    def use_scheduler(self):
        # Everyone was applied before the scheduler existed here. A real scheduler has
        # been running since that apply, so it knows the plan that runs.
        scheduler = super().use_scheduler()
        if self.helper.applied_plan is not None:
            prepared = scheduler.prepare_plan(repository=self.repository)
            scheduler.activate_plan(repository=self.repository, prepared=prepared)
        return scheduler

    def delays(self, plan, device_id, direction=Direction.UPLOAD):
        return {queue.impairment.delay_ms for queue in plan.queues
                if queue.device_id == device_id and queue.direction == direction}

    def last_plan(self):
        plan = self.helper.applied_plan
        # The real helper decodes the same plan from the wire. It must accept it.
        decoded = plan_from_payload(plan_to_payload(plan))
        self.assertEqual(decoded.queues, plan.queues)
        return plan

    def version(self):
        return self.repository.get_runtime_state().configuration_version

    def exceptions(self):
        return self.repository.get_everyone_exceptions()

    def mark_applied(self):
        """A setup that saved settings directly: the network still runs what it ran."""

        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success",
            last_error=None, keep_everyone_exceptions=bool(self.exceptions()))

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
        self.mark_applied()
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
            "UPDATE runtime_state SET last_action = NULL, observed_state = 'unknown', "
            "exceptions_kept = 1")
        self.repository._connection.commit()

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
        self.mark_applied()
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
        self.mark_applied()
        before = self.exceptions()

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("15 traffic classes", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), before)

    def test_a_failed_start_changes_nothing_and_keeps_the_earlier_exceptions(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 502)
        self.assertFalse(result["live_applied"])
        self.assertIn("did not start, so nothing changed", result["error"])
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertEqual(self.repository.get_general_profile().id, "low")
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("error", "apply"))

    def test_a_failed_first_exception_leaves_everyone_alone(self) -> None:
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.apply_to()

        self.assertEqual(status, 502)
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(self.exceptions(), frozenset())
        self.helper.fail_action = ""
        # The saved state now plans Everyone for everyone, as before the failed start.
        plan = compile_stored_plan(repository=self.repository)
        self.assertEqual(plan.mark_rules, ())


class EveryoneNotRunningTests(EveryoneRunningFixture):
    def test_a_failed_first_start_then_a_device_apply_starts_nothing_else(self) -> None:
        # Everyone is saved but the apply failed, so it does not run. A device Apply must
        # not start it across the household as a side effect.
        self.helper.fail_action = "apply"
        with self.assertLogs("jittersim.api.server", "ERROR"):
            self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 502)
        self.helper.fail_action = ""
        applied_before = len(self.helper.plans)

        status, result = self.apply_to()

        self.assertEqual(status, 409)
        self.assertIn("not running", result["error"])
        self.assertIn("Everyone is saved as daily climb manual", result["error"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertEqual(len(self.helper.plans), applied_before)
        self.assertEqual(self.exceptions(), frozenset())

    def test_a_device_apply_never_changes_everyone_after_a_failed_apply_to_all(self) -> None:
        from jittersim.networking.helper import HelperClientError

        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        scheduler.activate(applied_payload=plan_to_payload(self.helper.applied_plan),
                           configuration_version=self.version())
        real = self.helper.apply
        calls = []

        def apply(**arguments):
            calls.append(arguments["plan"])
            if len(calls) == 1:
                raise HelperClientError("simulated helper failure")
            return real(**arguments)

        with patch.object(self.helper, "apply", side_effect=apply), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.write("/api/general-profile", {"profile_id": "slow-test"})
        # The failed update put Everyone's old profile back. The new choice stays saved.
        self.assertEqual(status, 502)
        self.assertEqual(self.helper.applied_plan.general_profile.profile_id, "low")
        self.assertEqual(self.repository.get_general_profile().id, "slow-test")
        applied_before = len(self.helper.plans)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409)
        # The new choice was saved and never applied, so the state says so.
        self.assertIn("waiting to be applied", result["error"])
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertEqual(len(self.helper.plans), applied_before)
        self.assertEqual(self.helper.applied_plan.general_profile.profile_id, "low")
        # A state that claims success does not help: the plan that runs is older than the
        # saved settings.
        self.mark_applied()
        status, result = self.apply_to(PHONE, "low")
        self.assertEqual(status, 409)
        self.assertIn("waiting to be applied", result["error"])
        self.assertEqual(len(self.helper.plans), applied_before)

    def test_a_device_apply_waits_for_saved_settings_that_nothing_applied(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        # Another writer changed the Air's profile. No apply ran, so the state is unknown.
        self.repository.save_profile(Profile(
            id="slow-test", name="Slow test", upload=Impairment(loss_percent=100)))
        applied_before = len(self.helper.plans)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409)
        self.assertIn("waiting to be applied", result["error"])
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertEqual(len(self.helper.plans), applied_before)
        self.assertEqual(self.delays(self.helper.applied_plan, AIR_ID), {3_000})

    def test_only_a_full_apply_makes_a_saved_change_count_as_running(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        self.repository.save_profile(Profile(
            id="slow-test", name="Slow test", upload=Impairment(delay_ms=2_000)))

        # A scheduled apply leaves access and ad-blocking edits pending, so it does not count.
        self.assertTrue(scheduler.tick(repository=self.repository))
        self.assertIsNone(scheduler.running_plan()[0].configuration_version)
        self.assertEqual(self.apply_to(PHONE, "low")[0], 409)
        # Apply to all runs the saved settings and puts the Air back on Everyone.
        self.assertEqual(self.write("/api/apply", {})[0], 200)
        status, _ = self.apply_to(PHONE, "low")

        self.assertEqual(status, 200)
        plan = self.last_plan()
        self.assertEqual(self.delays(plan, PHONE_ID), {50})
        self.assertEqual(self.exceptions(), {PHONE_ID})

    def test_a_tick_without_a_saved_change_keeps_the_device_apply_open(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        version = self.version()
        scheduler.tick(repository=self.repository)

        self.assertEqual(scheduler.running_plan()[0].configuration_version, version)
        self.assertEqual(self.apply_to(PHONE, "low")[0], 200)

    def test_a_device_that_moved_address_in_a_tick_keeps_the_device_apply_open(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        moved = {**AIR, "ipv4_address": "192.168.50.190"}
        clients = self.helper.connected_devices()
        moved_clients = [{**clients[0], **moved}, clients[1]]
        scheduler._last_address_check_at = 0.0
        before = self.version()

        with patch.object(self.helper, "connected_devices", return_value=moved_clients):
            self.assertTrue(scheduler.tick(repository=self.repository))
            # The move is a saved change, and the plan of the same tick includes it.
            self.assertEqual(str(self.repository.get_device(AIR_ID).ipv4_address), "192.168.50.190")
            self.assertEqual(self.version(), before + 1)
            self.assertEqual(scheduler.running_plan()[0].configuration_version, self.version())
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 200, result)
        self.assertEqual(self.exceptions(), {AIR_ID, PHONE_ID})

    def follow_the_air_to_a_new_private_address(self):
        """The Air comes back with a new private Wi-Fi address and a new IPv4 address.

        Address changes is on Automatic, so the tick moves its saved settings to the
        new MAC address and then to the new IPv4 address.
        """

        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        now = int(time.time())
        store = AddressFollowStore(self.repository)
        store.set_mode("automatic")
        # A generic name such as MacBook-Pro is weak evidence, so the Air has its own.
        store.observe([{"mac_address": AIR["mac_address"], "hostname": "kitchen-air",
                        "ipv4_address": AIR["ipv4_address"], "connected_seconds": 10,
                        "ipv4_verified": True}],
                      saved={AIR["mac_address"]}, now=now - 300)
        air, phone = self.helper.connected_devices()
        moved = {**air, "mac_address": NEW_AIR_MAC, "ipv4_address": "192.168.50.190",
                 "hostname": "Kitchen-Air", "connected_seconds": 120}
        scheduler._last_address_check_at = 0.0
        return scheduler, store, now, [moved, phone]

    def test_an_automatic_address_follow_in_a_tick_keeps_the_device_apply_open(self) -> None:
        scheduler, store, now, clients = self.follow_the_air_to_a_new_private_address()
        before = self.version()

        with patch.object(self.helper, "connected_devices", return_value=clients):
            self.assertTrue(scheduler.tick(repository=self.repository, now=now))
            device = self.repository.get_device(AIR_ID)
            self.assertEqual((device.mac_address, str(device.ipv4_address)),
                             (NEW_AIR_MAC, "192.168.50.190"))
            self.assertEqual(store.history()[0]["mode"], "automatic")
            # The MAC move and the IPv4 move are two saves, both the tick's own.
            self.assertGreaterEqual(self.version(), before + 2)
            self.assertEqual(scheduler.running_plan()[0].configuration_version, self.version())
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 200, result)
        self.assertEqual(self.exceptions(), {AIR_ID, PHONE_ID})

    def test_an_automatic_address_follow_beside_another_save_stays_refused(self) -> None:
        scheduler, _, now, clients = self.follow_the_air_to_a_new_private_address()
        original = runtime_module.follow_automatically

        def move_then_another_save(*args, **kwargs):
            done = original(*args, **kwargs)
            self.repository.save_profile(Profile(
                id="slow-test", name="Slow test", upload=Impairment(delay_ms=2_000)))
            return done

        with patch.object(self.helper, "connected_devices", return_value=clients), \
                patch.object(runtime_module, "follow_automatically",
                             side_effect=move_then_another_save):
            self.assertTrue(scheduler.tick(repository=self.repository, now=now))
            self.assertEqual(self.repository.get_device(AIR_ID).mac_address, NEW_AIR_MAC)
            self.assertIsNone(scheduler.running_plan()[0].configuration_version)
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409, result)
        self.assertEqual(self.exceptions(), {AIR_ID})

    def test_a_save_by_another_process_during_the_apply_is_never_installed(self) -> None:
        for moment in ("before saving", "while building"):
            with self.subTest(moment=moment):
                self.write("/api/reset", {})
                self.assertEqual(
                    self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
                scheduler = self.use_scheduler()
                self.repository.delete_device(PHONE_ID) if self.repository.get_device(
                    PHONE_ID) else None
                applied_before = len(self.helper.plans)

                def other_writer(*_args, **_kwargs):
                    self.repository.save_profile(Profile(
                        id="slow-test", name="Slow test",
                        upload=Impairment(loss_percent=100)))

                target = ("sample_before_control_change" if moment == "before saving"
                          else "prepare_plan")
                original = getattr(scheduler, target)

                def wrapped(*args, **kwargs):
                    if target == "prepare_plan":
                        result = original(*args, **kwargs)
                        other_writer()
                        return result
                    other_writer()
                    return original(*args, **kwargs)

                with patch.object(scheduler, target, side_effect=wrapped):
                    status, result = self.apply_to(PHONE, "low")

                self.assertEqual(status, 409)
                self.assertIn("waiting to be applied", result["error"])
                self.assertEqual(len(self.helper.plans), applied_before)
                self.assertIsNone(self.repository.get_device(PHONE_ID))

    def test_a_device_apply_never_installs_an_edit_of_everyones_profile(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        scheduler.activate(applied_payload=plan_to_payload(self.helper.applied_plan),
                           configuration_version=self.version())
        # The saved Everyone profile was edited but the edit never reached the network.
        self.repository.save_profile(Profile(
            id="low", name="daily climb manual", upload=Impairment(loss_percent=100)))
        self.mark_applied()
        applied_before = len(self.helper.plans)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409)
        self.assertIn("waiting to be applied", result["error"])
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertEqual(len(self.helper.plans), applied_before)
        self.assertEqual(self.delays(self.helper.applied_plan, GENERAL_QUEUE_ID), {50})

    def test_a_device_apply_never_finishes_a_failed_stop_for_everyone(self) -> None:
        from jittersim.networking.helper import HelperClientError

        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        scheduler.activate(applied_payload=plan_to_payload(self.helper.applied_plan),
                           configuration_version=self.version())
        real = self.helper.apply
        calls = []

        def apply(**arguments):
            calls.append(arguments["plan"])
            if len(calls) == 1:
                raise HelperClientError("simulated helper failure")
            return real(**arguments)

        with patch.object(self.helper, "apply", side_effect=apply), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.write("/api/general-profile/stop", {})
        # Stop is saved but failed. The plan that ran, with Everyone, came back.
        self.assertEqual(status, 502)
        self.assertIsNone(self.repository.get_general_profile())
        self.assertEqual(self.helper.applied_plan.general_profile.profile_id, "low")
        applied_before = len(self.helper.plans)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409)
        self.assertIn("waiting to be applied", result["error"])
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertEqual(len(self.helper.plans), applied_before)
        self.assertEqual(self.helper.applied_plan.general_profile.profile_id, "low")

    def test_everyone_that_is_applying_or_unknown_is_not_running(self) -> None:
        for state in ("applying", "unknown", "error"):
            with self.subTest(state=state):
                self.repository._connection.execute(
                    "UPDATE runtime_state SET observed_state = ?, exceptions_kept = 1", (state,))
                self.repository._connection.commit()
                before = len(self.helper.plans)

                status, _ = self.apply_to()

                self.assertEqual(status, 409)
                self.assertEqual(len(self.helper.plans), before)
                self.assertIsNone(self.repository.get_device(AIR_ID))


class FailedDeviceApplyRecoveryTests(EveryoneRunningFixture):
    def fail_first_apply(self):
        from jittersim.networking.helper import HelperClientError

        real = self.helper.apply
        calls = []

        def apply(**arguments):
            calls.append(arguments["plan"])
            if len(calls) == 1:
                raise HelperClientError("simulated helper failure")
            return real(**arguments)

        return patch.object(self.helper, "apply", side_effect=apply), calls

    def test_a_failed_device_apply_puts_back_what_ran_before(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        patcher, calls = self.fail_first_apply()

        with patcher, self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 502)
        self.assertEqual(len(calls), 2)
        # The second apply is the plan from before: Everyone and the Air only.
        self.assertEqual([rule.device_id for rule in calls[1].mark_rules], [AIR_ID])
        self.assertEqual(calls[1].general_profile.profile_id, "low")
        self.assertEqual([rule.device_id for rule in self.last_plan().mark_rules], [AIR_ID])
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertIsNone(self.repository.get_device(PHONE_ID))
        self.assertIn("What ran before was put back", result["error"])
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("applied", "apply"))

    def test_putting_back_never_installs_the_profile_that_failed(self) -> None:
        # The Air already runs beside Everyone. Applying a profile that blocks everything
        # fails at the helper. What goes back is the plan that ran, not the saved choice.
        self.repository.save_profile(Profile(
            id="blocked", name="Blocked", upload=Impairment(loss_percent=100),
            download=Impairment(loss_percent=100)))
        self.mark_applied()
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        ran_before = self.helper.applied_plan
        patcher, calls = self.fail_first_apply()

        with patcher, self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to(profile_id="blocked")

        self.assertEqual(status, 502)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].queues, ran_before.queues)
        self.assertEqual(self.delays(self.helper.applied_plan, AIR_ID), {3_000})
        self.assertIn("What ran before was put back", result["error"])
        # Nothing changed: the Air keeps the profile it had, saved and running.
        self.assertEqual(self.repository.get_device(AIR_ID).profile_id, "slow-test")
        self.assertEqual(self.exceptions(), {AIR_ID})

    def test_putting_back_keeps_unapplied_saved_settings_unapplied(self) -> None:
        from jittersim.networking.helper import HelperClientError

        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        # An edit of the Air's profile was saved by another writer and never applied.
        self.repository.save_profile(Profile(
            id="slow-test", name="Slow test", upload=Impairment(loss_percent=100)))
        real = self.helper.apply
        calls = []

        def apply(**arguments):
            calls.append(arguments["plan"])
            if len(calls) == 1:
                raise HelperClientError("simulated helper failure")
            return real(**arguments)

        with patch.object(self.helper, "apply", side_effect=apply), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            self.assertEqual(self.write("/api/apply", {})[0], 502)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "unknown")
        applied_before = len(self.helper.plans)

        status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 409)
        self.assertIn("waiting to be applied", result["error"])
        self.assertEqual(len(self.helper.plans), applied_before)
        self.assertEqual(self.delays(self.helper.applied_plan, AIR_ID), {3_000})
        self.assertIsNotNone(scheduler)

    def test_a_later_device_apply_never_installs_an_earlier_failed_choice(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        patcher, calls = self.fail_first_apply()
        with patcher, self.assertLogs("jittersim.api.server", "ERROR"):
            self.assertEqual(self.apply_to(profile_id="low")[0], 502)

        status, _ = self.apply_to(PHONE, "low")

        self.assertEqual(status, 200)
        plan = self.last_plan()
        # The Air still runs its own profile. The failed choice was never saved.
        self.assertEqual(self.delays(plan, AIR_ID), {3_000})
        self.assertEqual(self.delays(plan, PHONE_ID), {50})
        self.assertEqual(self.exceptions(), {AIR_ID, PHONE_ID})

    def test_a_reset_recorded_before_the_put_back_is_never_undone(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        patcher, calls = self.fail_first_apply()

        def cli_reset(_rolls):
            # `jittersim reset` from another process, after the failure was recorded.
            self.repository.update_runtime_state(
                observed_state="reset", last_action="reset", last_result="success",
                last_error=None)

        with patcher, patch.object(scheduler, "restore_rolls", side_effect=cli_reset), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 502)
        self.assertEqual(len(calls), 1)
        self.assertIn("A reset was recorded meanwhile", result["error"])
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("reset", "reset"))

    def test_a_failed_restore_sends_the_device_back_to_everyone(self) -> None:
        from jittersim.config.storage import StorageError

        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        patcher, _ = self.fail_first_apply()
        real_save = self.repository.save_device

        def save_device(device, **arguments):
            if arguments.get("expected") is not None and device.profile_id == "slow-test":
                raise StorageError("simulated storage failure")
            return real_save(device, **arguments)

        with patcher, patch.object(self.repository, "save_device", side_effect=save_device), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.apply_to(profile_id="low")

        self.assertEqual(status, 502)
        # The saved choice that failed stays saved, so the device must not run it.
        self.assertEqual(self.repository.get_device(AIR_ID).profile_id, "low")
        self.assertEqual(self.exceptions(), frozenset())

    def test_if_putting_it_back_fails_too_the_record_still_says_what_ran(self) -> None:
        self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        self.helper.fail_action = "apply"

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, result = self.apply_to(PHONE, "low")

        self.assertEqual(status, 502)
        self.assertEqual(self.exceptions(), {AIR_ID})
        self.assertIn("could not be put back", result["error"])
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action), ("error", "apply"))

    def test_panic_between_the_failure_and_the_restore_leaves_nothing_behind(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.apply_to()[0], 200)
        self.helper.fail_action = "apply"
        original = self.server._apply_configuration_locked
        applies = []

        def failing_then_panic(*arguments, **keywords):
            result = original(*arguments, **keywords)
            if not applies:
                # Panic lands right after the failed apply: it stops and ends the exceptions.
                scheduler.halt()
                self.repository.update_runtime_state(
                    observed_state="reset", last_action="reset", last_result="success",
                    last_error=None)
            applies.append(result[0])
            return result

        with patch.object(self.server, "_apply_configuration_locked",
                          side_effect=failing_then_panic), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.apply_to(PHONE, "low")

        self.assertEqual(status, 502)
        self.assertEqual(applies, [502])
        self.assertEqual(self.raw_rows(), frozenset())
        self.assertEqual(self.exceptions(), frozenset())


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

    def test_a_failed_stop_then_a_scheduled_success_ends_the_record(self) -> None:
        scheduler = self.use_scheduler()
        self.helper.fail_action = "apply"
        with self.assertLogs("jittersim.api.server", "ERROR"):
            self.assertEqual(self.write("/api/general-profile/stop", {})[0], 502)
        self.assertEqual(self.raw_rows(), {AIR_ID})
        self.helper.fail_action = ""

        self.assertTrue(scheduler.tick(repository=self.repository))

        # Everyone is gone and the saved devices run, so no exception is left behind.
        self.assertEqual(self.raw_rows(), frozenset())
        self.assertIsNone(self.helper.applied_plan.general_profile)
        # A later failed Apply to everyone followed by a tick cannot bring one back.
        self.helper.fail_action = "apply"
        with self.assertLogs("jittersim.api.server", "ERROR"):
            self.assertEqual(
                self.write("/api/general-profile", {"profile_id": "low"})[0], 502)
        self.helper.fail_action = ""
        scheduler.tick(repository=self.repository)
        self.assertEqual(self.exceptions(), frozenset())

    def test_a_failed_scheduled_retry_after_a_failed_stop_keeps_the_record(self) -> None:
        scheduler = self.use_scheduler()
        self.helper.fail_action = "apply"
        with self.assertLogs("jittersim.api.server", "ERROR"):
            self.assertEqual(self.write("/api/general-profile/stop", {})[0], 502)

        # The scheduler fails too. Nothing replaced the running plan, so the record stays.
        with self.assertLogs("jittersim.engine.runtime", "ERROR"):
            self.assertFalse(scheduler.tick(repository=self.repository))
        self.assertEqual(self.raw_rows(), {AIR_ID})
        self.assertEqual(self.repository.get_runtime_state().last_action, "schedule")

        # It goes only with the first accepted plan.
        self.helper.fail_action = ""
        scheduler._plan_retry_at = 0.0
        self.assertTrue(scheduler.tick(repository=self.repository))
        self.assertEqual(self.raw_rows(), frozenset())

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

    def test_a_failed_whole_scope_update_puts_back_the_plan_that_ran(self) -> None:
        from jittersim.networking.helper import HelperClientError

        for path, payload in (
            ("/api/apply", {}),
            ("/api/general-profile", {"profile_id": "slow-test"}),
            ("/api/general-profile/stop", {}),
        ):
            with self.subTest(path=path):
                self.repository.save_general_profile("low")
                self.repository.restore_everyone_exceptions(frozenset({AIR_ID}))
                self.mark_applied()
                scheduler = self.use_scheduler()
                ran = self.helper.applied_plan
                real = self.helper.apply
                calls = []

                def apply(**arguments):
                    calls.append(arguments["plan"])
                    if len(calls) == 1:
                        raise HelperClientError("simulated helper failure")
                    return real(**arguments)

                scheduler.activate(applied_payload=plan_to_payload(ran),
                                   configuration_version=self.version())
                with patch.object(self.helper, "apply", side_effect=apply), \
                        self.assertLogs("jittersim.api.server", "ERROR"):
                    status, result = self.write(path, payload)

                self.assertEqual(status, 502)
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[1].queues, ran.queues)
                self.assertIn("What ran before was put back", result["error"])
                self.assertEqual(self.raw_rows(), {AIR_ID})
                # Leave a running Everyone with the Air beside it for the next case.
                self.write("/api/reset", {})
                self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
                self.assertEqual(self.apply_to()[0], 200)

    def test_a_failed_update_puts_back_the_scheduler_state_with_the_plan(self) -> None:
        from jittersim.networking.helper import HelperClientError

        for path, payload in (("/api/apply", {}), ("device", None)):
            with self.subTest(path=path):
                self.write("/api/reset", {})
                self.assertEqual(self.write("/api/general-profile", {"profile_id": "low"})[0], 200)
                scheduler = self.use_scheduler()
                self.assertEqual(self.apply_to()[0], 200)
                scheduler.activate(applied_payload=plan_to_payload(self.helper.applied_plan),
                                   configuration_version=self.version())
                before = {key: roll for key, roll in scheduler._rolls.items()}
                self.assertIn(AIR_ID, before)
                real = self.helper.apply
                calls = []

                def apply(**arguments):
                    calls.append(arguments["plan"])
                    if len(calls) == 1:
                        raise HelperClientError("simulated helper failure")
                    return real(**arguments)

                with patch.object(self.helper, "apply", side_effect=apply), \
                        self.assertLogs("jittersim.api.server", "ERROR"):
                    if payload is None:
                        status, _ = self.apply_to(PHONE, "low")
                    else:
                        status, _ = self.write(path, payload)

                self.assertEqual(status, 502)
                self.assertEqual(len(calls), 2)
                # The randomization state, outage deadlines and sessions of the devices that
                # ran are the ones that ran, so the next tick keeps the same policy.
                for key, roll in before.items():
                    self.assertEqual(scheduler._rolls.get(key), roll, key)

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
        captured, registered = threading.Event(), threading.Event()
        capture = self.server._capture_control_intent
        note = self.server.note_reset_request

        def captured_intent(*arguments, **keywords):
            result = capture(*arguments, **keywords)
            captured.set()
            return result

        def noted_reset():
            note()
            registered.set()

        with patch.object(self.server, "_capture_control_intent", side_effect=captured_intent), \
                patch.object(self.server, "note_reset_request", side_effect=noted_reset):
            with scheduler.exclusive():
                waiting = threading.Thread(
                    target=lambda: results.setdefault("apply", self.apply_to()))
                waiting.start()
                self.assertTrue(captured.wait(10))
                resetting = threading.Thread(
                    target=lambda: results.setdefault("reset", self.write("/api/reset", {})))
                resetting.start()
                self.assertTrue(registered.wait(10))
            waiting.join(10)
            resetting.join(10)

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

    def test_the_connected_list_does_not_call_the_exception_the_same_as_everyone(self) -> None:
        self.assertEqual(self.apply_to()[0], 200)

        status, _, body = self.server.response_for("/api/connected-devices")
        clients = {item["assigned_device_id"] or item["mac_address"]: item
                   for item in json.loads(body)["devices"]}

        self.assertEqual(status, 200)
        air = clients[AIR_ID]
        self.assertNotIn("general_profile_name", air)
        self.assertEqual(air["assigned_profile_id"], "slow-test")
        self.assertTrue(air["beside_everyone"])
        phone = clients[PHONE["mac_address"]]
        self.assertEqual(phone["general_profile_name"], "daily climb manual")
        self.assertNotIn("beside_everyone", phone)

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
