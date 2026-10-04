"""Queued actions and failed cleanup cannot override emergency recovery."""

import threading
import unittest
from unittest.mock import patch

from jittersim.networking.helper import HelperClientError
from tests.unit import test_dashboard_concurrency as concurrency


class WaitingLock:
    def __init__(self, lock, waiting):
        self.lock, self.waiting = lock, waiting

    def __enter__(self):
        self.waiting.set()
        self.lock.acquire()
        return self

    def __exit__(self, *unused):
        self.lock.release()

    def acquire(self, *args, **kwargs):
        return self.lock.acquire(*args, **kwargs)

    def release(self):
        return self.lock.release()


class PanicIntentTests(unittest.TestCase):
    setUp = concurrency.DashboardConcurrencyTests.setUp
    _stop = concurrency.DashboardConcurrencyTests._stop
    request = concurrency.DashboardConcurrencyTests.request
    start_slow_apply = concurrency.DashboardConcurrencyTests.start_slow_apply

    def test_apply_queued_before_panic_cannot_become_a_fresh_start(self):
        first, first_thread = self.start_slow_apply()
        queued, waiting = {}, threading.Event()

        def run():
            queued["response"] = self.request("POST", "/api/apply", {})

        with patch.object(
            self.scheduler, "_apply_lock", WaitingLock(self.scheduler._apply_lock, waiting)
        ):
            second = threading.Thread(target=run)
            self.threads.append(second)
            second.start()
            assert waiting.wait(2)
            # The request has entered the scheduler wait while the first owns it.
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            self.helper.release_apply.set()
            first_thread.join(5)
            second.join(5)
        assert first["response"][0] == 409
        assert queued["response"][0] == 409
        assert not self.scheduler.is_active
        assert self.repository.get_runtime_state().last_action == "reset"

    def test_general_profile_queued_before_panic_cannot_save_or_start(self):
        first, first_thread = self.start_slow_apply()
        queued, waiting = {}, threading.Event()

        def run():
            queued["response"] = self.request("POST", "/api/general-profile", {"profile_id": "normal"})

        with patch.object(self.helper, "check_general_profile_support", create=True), patch.object(
            self.scheduler, "_apply_lock", WaitingLock(self.scheduler._apply_lock, waiting)
        ):
            worker = threading.Thread(target=run)
            self.threads.append(worker)
            worker.start()
            assert waiting.wait(2)
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            self.helper.release_apply.set()
            first_thread.join(5)
            worker.join(5)
        assert queued["response"][0] == 409
        assert self.repository.get_general_profile() is None
        assert not self.scheduler.is_active

    def test_failed_late_cleanup_remains_unconfirmed_and_requires_recovery(self):
        result, worker = self.start_slow_apply()
        assert self.request("POST", "/api/normal-access", {})[0] == 200
        with patch.object(self.helper, "normal_access", side_effect=HelperClientError("failed")):
            self.helper.release_apply.set()
            worker.join(5)
        state = self.repository.get_runtime_state()
        assert result["response"][0] == 503
        assert state.last_action == "reset" and state.last_result == "failure"
        assert state.observed_state == "error"
        assert not self.scheduler.is_active
        assert self.request("POST", "/api/apply", {})[0] == 503
        assert self.request("POST", "/api/normal-access", {})[0] == 200
        assert self.request("POST", "/api/apply", {})[0] == 200

    def test_failed_apply_after_panic_does_not_replace_its_reset_state(self):
        entered, release = threading.Event(), threading.Event()

        def fail(*, plan, explicit_apply=False):
            entered.set()
            assert release.wait(5)
            raise HelperClientError("late failure")

        result = {}
        with patch.object(self.helper, "apply", side_effect=fail):
            worker = threading.Thread(
                target=lambda: result.update(response=self.request("POST", "/api/apply", {}))
            )
            self.threads.append(worker)
            worker.start()
            assert entered.wait(2)
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            release.set()
            worker.join(5)
        assert result["response"][0] == 409
        assert self.repository.get_runtime_state().last_action == "reset"
        assert not self.scheduler.is_active

    def test_start_during_panic_recovery_is_refused_until_the_result_is_known(self):
        entered, release = threading.Event(), threading.Event()
        result = {}

        def recover():
            entered.set()
            assert release.wait(5)
            return {"commands_run": 1}

        def panic():
            result["response"] = self.request("POST", "/api/normal-access", {})

        with patch.object(self.helper, "normal_access", side_effect=recover):
            worker = threading.Thread(target=panic)
            self.threads.append(worker)
            worker.start()
            assert entered.wait(2)
            self.helper.release_apply.set()
            try:
                assert self.request("POST", "/api/apply", {})[0] == 503
                assert not self.scheduler.is_active
            finally:
                release.set()
                worker.join(5)
        assert result["response"][0] == 200
        assert self.request("POST", "/api/apply", {})[0] == 200

    def test_closing_service_does_not_run_a_queued_apply(self):
        self.helper.release_apply.set()
        waiting, entered = threading.Event(), threading.Event()
        result = {}

        def run():
            entered.set()
            result["response"] = self.server._apply_configuration()

        with self.scheduler.exclusive():
            with patch.object(
                self.scheduler, "_apply_lock", WaitingLock(self.scheduler._apply_lock, waiting)
            ):
                worker = threading.Thread(target=run)
                self.threads.append(worker)
                worker.start()
                assert entered.wait(1) and waiting.wait(1)
                self.scheduler.begin_shutdown()
        worker.join(2)
        assert result["response"][0] == 503
        assert not self.helper.apply_started.is_set()
        assert self.repository.get_runtime_state().last_action != "apply"
