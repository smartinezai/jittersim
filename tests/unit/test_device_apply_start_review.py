"""Review defects of the device apply-and-start change (PR 96), each reproduced first."""

import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from jittersim.api import server as server_module
from jittersim.config.models import Device
from jittersim.config.storage import ConfigRepository
from jittersim.engine.compiler import PlanError
from jittersim.networking.helper import HelperClient, HelperClientError
from tests.unit.test_device_apply_start import AIR_ID, DeviceApplyFixture


class ReviewDefectTests(DeviceApplyFixture):
    """Reuses the fixture of the original tests. Each test names its defect."""

    def stopped_with_scope(self, *device_ids):
        self.reset()
        self.repository.update_runtime_state(
            observed_state="applied", last_action="reset", last_result="success",
            last_error=None, keep_device_scope=True)
        for device_id in device_ids:
            self.repository.add_device_to_start_scope(device_id)

    # D1: an Apply that arrives while a Reset is already running starts nothing.

    def test_d1_apply_arriving_during_a_running_reset_starts_nothing(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
        results = {}
        with scheduler.exclusive():
            resetting = threading.Thread(
                target=lambda: results.setdefault("reset", self.write("/api/reset", {})))
            resetting.start()
            time.sleep(0.2)  # Reset has entered and waits for the lock.
            applying = threading.Thread(target=lambda: results.setdefault("apply", self.apply_to()))
            applying.start()
            time.sleep(0.2)
        resetting.join(5)
        applying.join(5)

        self.assertEqual(results["reset"][0], 200)
        self.assertEqual(results["apply"][0], 409, results["apply"])
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertIsNone(self.helper.applied_plan)

    # D2: Panic before the save commits leaves nothing saved, on both paths.

    def test_d2_panic_during_preparation_on_a_running_network_saves_nothing(self) -> None:
        scheduler = self.use_scheduler()
        self.assertEqual(self.write("/api/apply", {})[0], 200)
        with patch.object(scheduler, "sample_before_control_change",
                          side_effect=lambda **unused: scheduler.halt()):
            status, _ = self.apply_to()
        self.assertEqual(status, 409)
        self.assertIsNone(self.repository.get_device(AIR_ID))

    def test_d2_panic_between_the_last_check_and_the_commit_saves_nothing(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
        original = self.repository.transaction
        observed = {}

        def transaction():
            halting = threading.Thread(target=scheduler.halt)
            halting.start()
            halting.join(0.3)
            observed.setdefault("panic_before_save", not halting.is_alive())
            return original()

        with patch.object(self.repository, "transaction", side_effect=transaction):
            self.apply_to()
        if observed.get("panic_before_save"):
            self.assertIsNone(self.repository.get_device(AIR_ID))
            self.assertIsNone(self.repository.get_device_start_scope())

    # D3: a start that cannot run leaves the scope as it was.

    def test_d3_a_sixteenth_device_is_refused_before_joining_the_scope(self) -> None:
        self.repository.save_general_profile("low")
        for index in range(1, 16):
            self.repository.save_device(Device(
                id=f"d{index}", name=f"Device {index}",
                ipv4_address=f"192.168.50.{100 + index}", profile_id="low"))
        self.stopped_with_scope(*(f"d{index}" for index in range(1, 16)))

        status, result = self.apply_to()

        self.assertEqual(status, 409, result)
        self.assertNotIn(AIR_ID, self.repository.get_device_start_scope())
        self.assertIsNone(self.repository.get_device(AIR_ID))

    def test_d3_a_plan_rejected_at_start_restores_the_previous_scope(self) -> None:
        self.stopped_with_scope("tv")
        real = server_module.compile_stored_plan
        calls = []

        def compile_once_then_fail(**arguments):
            calls.append(arguments.get("device_scope"))
            if len(calls) > 1 and arguments.get("device_scope"):
                raise PlanError("simulated rejection at start")
            return real(**arguments)

        with patch.object(server_module, "compile_stored_plan", side_effect=compile_once_then_fail):
            status, _ = self.apply_to()

        self.assertEqual(status, 409)
        self.assertEqual(self.repository.get_device_start_scope(), {"tv"})

    # D4: a failed device-only start or cleanup is retried and recorded.

    def test_d4_a_failed_start_resets_the_helper_and_records_a_clean_reset(self) -> None:
        self.reset()
        self.helper.fail_action = "apply"
        self.helper.reset_called = False

        with self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.apply_to()

        self.assertEqual(status, 502)
        self.assertTrue(self.helper.reset_called)
        state = self.repository.get_runtime_state()
        self.assertEqual((state.observed_state, state.last_action, state.last_result),
                         ("reset", "reset", "success"))
        # A restart then keeps the network reset instead of asking for Panic.
        self.helper.fail_action = ""
        self.helper.reset_called = False
        self.server.recover_saved_network_state()
        self.assertTrue(self.helper.reset_called)

    def test_d4_a_failed_cleanup_is_retried_until_the_reset_works(self) -> None:
        self.server.cleanup_retry_delays = (0.01, 0.01, 0.01)
        self.reset()
        resets = []

        def flaky_reset():
            resets.append(1)
            if len(resets) < 3:
                raise HelperClientError("simulated cleanup failure")
            return {"ok": True}

        self.helper.fail_action = "apply"
        with patch.object(self.helper, "reset", side_effect=flaky_reset), \
                self.assertLogs("jittersim.api.server", "ERROR"):
            status, _ = self.apply_to()
            self.assertEqual(status, 502)
            self.assertEqual(self.repository.get_runtime_state().observed_state, "error")
            deadline = time.monotonic() + 5
            while self.repository.get_runtime_state().observed_state != "reset":
                self.assertLess(time.monotonic(), deadline, "cleanup was not retried")
                time.sleep(0.02)

        self.assertEqual(len(resets), 3)
        self.assertEqual(self.repository.get_runtime_state().last_result, "success")

    def test_d4_ending_a_scope_keeps_retrying_a_failed_reset(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
        self.apply_to()
        self.write("/api/devices/delete", {"id": AIR_ID})
        self.helper.fail_action = "reset"
        self.assertFalse(scheduler.tick(repository=self.repository))
        self.assertTrue(scheduler.is_active, "a failed cleanup must stay pending")
        self.helper.fail_action = ""
        scheduler._clear_plan_failure()
        self.assertFalse(scheduler.tick(repository=self.repository))
        self.assertTrue(self.helper.reset_called)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")

    def test_d4_restart_after_a_scheduled_failure_restarts_the_scope(self) -> None:
        self.reset()
        self.apply_to()
        self.assertTrue(self.repository.record_schedule_result(success=False))
        self.helper.applied_plan = None

        self.server.recover_saved_network_state()

        self.assertEqual(self.running_ids(), [AIR_ID])

    # D5: a stop state that cannot be read refuses before saving.

    def test_d5_a_busy_gateway_status_refuses_before_saving(self) -> None:
        self.reset()
        production = HelperClient(socket_path="/nonexistent/helper.sock")
        busy = HelperClientError("Pi networking is busy. Try again later.", reason="helper-busy")
        with patch.object(production, "_request", side_effect=busy):
            self.helper.gateway_status = production.gateway_status
            status, result = self.apply_to()

        self.assertEqual(status, 409, result)
        self.assertIsNone(self.repository.get_device(AIR_ID))
        self.assertIsNone(self.helper.applied_plan)

    # D6: rows left by an older dashboard never come back.

    def test_d6_an_older_dashboard_reset_written_with_raw_sql_ends_the_scope(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.db"
            with ConfigRepository(path) as repository:
                repository.save_profile(self.repository.get_profile("low"))
                repository.save_device(Device(id="tv", name="TV", ipv4_address="192.168.50.20",
                                              profile_id="low"))
                repository.update_runtime_state(observed_state="applied", last_action="reset",
                                                last_result="success", last_error=None)
                repository.add_device_to_start_scope("tv")
            # The older dashboard knows nothing about the scope table.
            connection = sqlite3.connect(path)
            for observed in ("resetting", "reset"):
                connection.execute(
                    "UPDATE runtime_state SET observed_state = ?, last_action = 'reset', "
                    "last_result = ?, last_error = NULL WHERE id = 1",
                    (observed, None if observed == "resetting" else "success"))
            connection.commit()
            connection.close()
            with ConfigRepository(path) as repository:
                self.assertIsNone(repository.get_device_start_scope())

    def test_d6_startup_discards_scope_rows_paired_with_a_completed_reset(self) -> None:
        self.reset()
        self.repository.add_device_to_start_scope("tv")
        self.helper.reset_called = False

        self.server.recover_saved_network_state()

        self.assertIsNone(self.repository.get_device_start_scope())
        self.assertTrue(self.helper.reset_called)
        self.assertIsNone(self.helper.applied_plan)

    # D7: clearer refusal when Everyone is only saved and nothing runs yet.

    def test_d7_everyone_saved_before_the_first_start_is_not_called_running(self) -> None:
        self.repository.save_general_profile("low")
        status, result = self.apply_to()
        self.assertEqual(status, 409)
        self.assertNotIn("right now", result["error"])
        self.assertIn("Reset network", result["error"])

    def test_d7_an_outside_reset_is_not_called_a_removed_device(self) -> None:
        scheduler = self.use_scheduler()
        self.reset()
        self.apply_to()
        self.repository.update_runtime_state(observed_state="resetting", last_action="reset",
                                             last_result=None, last_error=None)
        scheduler.tick(repository=self.repository)
        self.assertEqual(self.repository.list_activity()[0]["message"],
                         "Network reset outside the dashboard")
