"""Dashboard actions and background work must describe the same network."""

import json
import unittest
from unittest.mock import patch

from jittersim.api.server import ControlServer
from jittersim.config.models import Device, Impairment, Profile, Scenario, ScenarioStep
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError


class RecordingHelper:
    def __init__(self):
        self.plans = []
        self.fail = False

    def apply(self, *, plan):
        if self.fail:
            raise HelperClientError("simulated apply failure")
        self.plans.append(plan)
        return {"commands_run": 1}

    def reset(self):
        return {"commands_run": 1}

    def traffic_counters(self):
        return {"devices": {}, "services": {}}

    def observe_dns_queries(self):
        return []


class ApiRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="normal", name="Normal"))
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=300))
        )
        self.repository.save_device(Device("alice", "Alice", "192.168.50.10", "normal"))
        self.helper = RecordingHelper()
        self.scheduler = RuntimeScheduler(database_path=":memory:", helper_client=self.helper)
        self.server = ControlServer(
            ("127.0.0.1", 0), self.repository,
            helper_client=self.helper, scheduler=self.scheduler,
        )
        self.addCleanup(self.server.server_close)
        clock = patch("time.time", return_value=1_000)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        background = patch.object(self.scheduler, "start")
        background.start()
        self.addCleanup(background.stop)

    def post(self, path, payload, expected=200):
        status, _, body = self.server.write_response_for(path, payload)
        response = json.loads(body)
        self.assertEqual(status, expected, response)
        return response

    def messages(self):
        return [event["message"] for event in reversed(self.repository.list_activity())]

    def start_timer(self, profile="slow", expected=200):
        return self.post(
            "/api/timers",
            {"id": "short", "device_id": "alice", "profile_id": profile, "duration_minutes": 1},
            expected=expected,
        )

    def save_scenario(self):
        self.repository.save_scenario(
            Scenario(id="brief", name="Brief", steps=(ScenarioStep("slow", 10),))
        )

    def start_scenario(self, expected=200):
        return self.post(
            "/api/scenarios/start", {"scenario_id": "brief", "device_id": "alice"},
            expected=expected,
        )

    def test_scenario_cannot_start_before_the_first_apply(self):
        self.save_scenario()

        response = self.start_scenario(expected=409)

        self.assertIn("Apply the configuration", response["error"])
        self.assertEqual(self.repository.list_scenario_runs(), ())
        self.assertEqual(self.helper.plans, [])

    def test_rejected_start_after_reset_preserves_the_timer(self):
        self.post("/api/apply", {})
        self.start_timer()
        self.post("/api/reset", {})
        self.save_scenario()
        timers = self.repository.list_timers()

        self.start_scenario(expected=409)
        self.clock.return_value = 1_011
        self.post("/api/apply", {})

        self.assertEqual(self.repository.list_scenario_runs(), ())
        self.assertEqual(self.repository.list_timers(), timers)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 300)

    def test_scenario_requires_hardware_playback(self):
        self.save_scenario()
        for missing in ("helper_client", "scheduler"):
            with self.subTest(missing=missing), patch.object(self.server, missing, None):
                response = self.start_scenario(expected=503)
                self.assertIn("hardware mode", response["error"])
                self.assertEqual(self.repository.list_scenario_runs(), ())

    def test_external_reset_rejects_start_before_the_scheduler_notices(self):
        self.post("/api/apply", {})
        self.save_scenario()
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None,
        )
        self.assertIsNotNone(self.scheduler.applied_plan())

        self.start_scenario(expected=409)

        self.assertEqual(self.repository.list_scenario_runs(), ())
        self.assertEqual(len(self.helper.plans), 1)
        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")

    def test_failed_scenario_start_stops_the_run_before_recovery(self):
        self.post("/api/apply", {})
        self.save_scenario()
        self.helper.fail = True

        with self.assertLogs("jittersim.api.server", "ERROR"):
            response = self.start_scenario(expected=502)

        self.assertFalse(response["live_applied"])
        self.assertEqual(self.repository.list_scenario_runs(), ())
        self.assertEqual(self.messages(), ["Configuration applied"])
        self.start_scenario(expected=409)
        self.helper.fail = False
        self.assertTrue(self.scheduler.tick(repository=self.repository, now=1_001))
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 0)
        self.clock.return_value = 1_020
        response = self.start_scenario()
        self.assertEqual(response["run"]["started_at"], 1_020)
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 300)

    def test_reset_recorded_during_start_stops_the_unapplied_run(self):
        self.post("/api/apply", {})
        self.save_scenario()
        start = self.repository.start_scenario_run

        def reset_after_saving(run):
            start(run)
            self.repository.update_runtime_state(
                observed_state="reset", last_action="reset", last_result="success", last_error=None,
            )

        with patch.object(self.repository, "start_scenario_run", side_effect=reset_after_saving):
            response = self.start_scenario(expected=409)

        self.assertFalse(response["live_applied"])
        self.assertEqual(self.repository.list_scenario_runs(), ())
        self.assertEqual(len(self.helper.plans), 1)
        self.assertEqual(self.repository.get_runtime_state().last_action, "reset")

    def test_first_scenario_step_after_apply_is_recorded_immediately(self):
        self.post("/api/apply", {})
        self.save_scenario()

        response = self.start_scenario()

        self.assertTrue(response["live_applied"])
        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 300)
        self.assertEqual(self.messages(), [
            "Configuration applied", "Alice: scenario Brief, step 1 of 1, now Slow",
        ])

    def test_start_and_cancel_between_ticks_record_both_applied_changes(self):
        self.post("/api/apply", {})
        self.start_timer()
        self.post("/api/timers/cancel", {"id": "short"})

        self.assertEqual(
            [plan.queues[1].impairment.delay_ms for plan in self.helper.plans], [0, 300, 0]
        )
        self.assertEqual(self.messages(), [
            "Configuration applied", "Alice: timer started, now Slow", "Alice: timer ended, back to Normal",
        ])
        self.assertFalse(self.scheduler.tick(repository=self.repository, now=1_001))
        self.assertEqual(len(self.repository.list_activity()), 3)

    def test_same_profile_timer_records_its_source_change(self):
        self.post("/api/apply", {})
        self.start_timer(profile="normal")
        self.post("/api/timers/cancel", {"id": "short"})

        self.assertEqual(self.messages(), [
            "Configuration applied", "Alice: timer started, now Normal", "Alice: timer ended, back to Normal",
        ])

    def test_failed_timer_apply_does_not_record_a_transition(self):
        self.post("/api/apply", {})
        self.helper.fail = True
        with self.assertLogs("jittersim.api.server", "ERROR"):
            self.start_timer(expected=502)
        self.assertEqual(self.messages(), ["Configuration applied"])

        self.helper.fail = False
        self.assertTrue(self.scheduler.tick(repository=self.repository, now=1_001))
        self.assertEqual(self.messages(), ["Configuration applied", "Alice: timer started, now Slow"])

    def test_apply_records_the_prepared_source_even_if_time_moves_during_apply(self):
        self.post("/api/apply", {})
        apply = self.helper.apply

        def slow_apply(*, plan):
            self.clock.return_value = 1_061
            return apply(plan=plan)

        with patch.object(self.helper, "apply", side_effect=slow_apply):
            self.start_timer()

        self.assertEqual(self.helper.plans[-1].queues[1].impairment.delay_ms, 300)
        self.assertEqual(self.messages(), ["Configuration applied", "Alice: timer started, now Slow"])
        self.assertTrue(self.scheduler.tick(repository=self.repository, now=1_061))
        self.assertEqual(self.messages()[-1], "Alice: timer ended, back to Normal")
