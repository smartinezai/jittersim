"""Follow-up defects E1 to E5 of the device-only cleanup, each reproduced first."""

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.api.server import DEVICE_CLEANUP_ERROR
from jittersim.config.models import Device, Profile
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError
from tests.unit.test_device_apply_start import DeviceApplyFixture


class CleanupFollowupTests(DeviceApplyFixture):
    def pending_cleanup(self):
        self.reset()
        self.repository.update_runtime_state(
            observed_state="error", last_action="reset", last_result="failure",
            last_error=DEVICE_CLEANUP_ERROR)

    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while not condition():
            self.assertLess(time.monotonic(), deadline, "condition never became true")
            time.sleep(0.02)

    def test_e1_saving_settings_does_not_cancel_a_pending_cleanup(self) -> None:
        self.pending_cleanup()
        resets = []
        saved = threading.Event()

        def reset_after_a_save():
            resets.append(1)
            if len(resets) == 1:
                # A settings save lands between two retries.
                self.repository.save_profile(Profile(id="other", name="Other"))
                saved.set()
                raise HelperClientError("simulated cleanup failure")
            return {"ok": True}

        self.server.cleanup_retry_delays = (0.01, 0.01, 0.01)
        with patch.object(self.helper, "reset", side_effect=reset_after_a_save):
            self.server._retry_device_cleanup()
            self.wait_for(lambda: len(resets) >= 2)
            self.server._cleanup_worker.join(5)
        self.assertTrue(saved.is_set())
        self.assertEqual(self.repository.get_runtime_state().last_error, None)
        self.assertEqual(self.repository.get_runtime_state().observed_state, "reset")

    def test_e2_a_failed_startup_cleanup_keeps_retrying(self) -> None:
        self.pending_cleanup()
        self.server.cleanup_retry_delays = (0.01, 0.01)
        resets = []

        def fail_once():
            resets.append(1)
            if len(resets) == 1:
                raise HelperClientError("simulated cleanup failure")
            return {"ok": True}

        with patch.object(self.helper, "reset", side_effect=fail_once):
            self.server.recover_saved_network_state()
            state = self.repository.get_runtime_state()
            self.assertEqual(state.last_error, DEVICE_CLEANUP_ERROR, "the marker was lost")
            self.wait_for(lambda: self.repository.get_runtime_state().observed_state == "reset")
        self.assertEqual(len(resets), 2)

    def test_e4_a_retry_never_overwrites_a_newer_panic_result(self) -> None:
        scheduler = self.use_scheduler()
        self.pending_cleanup()
        self.server.cleanup_retry_delays = (0.01,)
        panic_error = "Normal access was not fully restored. Retry Panic."

        def panic_during_reset():
            scheduler.halt()
            self.repository.update_runtime_state(
                observed_state="error", last_action="reset", last_result="failure",
                last_error=panic_error)
            return {"ok": True}

        with patch.object(self.helper, "reset", side_effect=panic_during_reset):
            self.server._retry_device_cleanup()
            self.server._cleanup_worker.join(5)
        self.assertEqual(self.repository.get_runtime_state().last_error, panic_error)

    def test_e5_shutdown_waits_for_the_cleanup_worker(self) -> None:
        self.pending_cleanup()
        self.server.cleanup_retry_delays = (0.01,)
        entered = threading.Event()
        release = threading.Event()

        def slow_reset():
            entered.set()
            release.wait(2)
            return {"ok": True}

        with patch.object(self.helper, "reset", side_effect=slow_reset):
            self.server._retry_device_cleanup()
            self.assertTrue(entered.wait(5))
            threading.Timer(0.3, release.set).start()
            self.server.server_close()
            self.assertFalse(self.server._cleanup_worker.is_alive(),
                             "shutdown returned while the cleanup still ran")
        # tearDown closes again; a second close must be harmless.


class FirstUpgradeTests(unittest.TestCase):
    def test_e3_rows_left_before_the_trigger_existed_are_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.db"
            with ConfigRepository(path) as repository:
                repository.save_profile(Profile(id="low", name="Low"))
                repository.save_device(Device(id="tv", name="TV", ipv4_address="192.168.50.20",
                                              profile_id="low"))
            # A database from before the trigger: an older dashboard reset the network
            # and a later save marked the state unknown, leaving the scope row behind.
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER end_device_start_scope")
            connection.execute("INSERT INTO device_start_scope (device_id) VALUES ('tv')")
            connection.execute(
                "UPDATE runtime_state SET observed_state = 'unknown', last_action = 'reset', "
                "last_result = 'success', last_error = NULL WHERE id = 1")
            connection.commit()
            connection.close()
            with ConfigRepository(path) as repository:
                self.assertIsNone(repository.get_device_start_scope())
