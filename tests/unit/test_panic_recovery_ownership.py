"""Emergency recovery keeps every pending owner and uncertain result visible."""

import threading
import unittest
from unittest.mock import patch

import pytest

from jittersim.config.storage import StorageError
from jittersim.networking.helper import HelperClientError
from tests.unit import test_dashboard_concurrency as concurrency


class RecoveryOwnershipTests(unittest.TestCase):
    setUp = concurrency.DashboardConcurrencyTests.setUp
    _stop = concurrency.DashboardConcurrencyTests._stop
    request = concurrency.DashboardConcurrencyTests.request

    def test_older_initial_write_cannot_replace_newer_completed_recovery(self):
        entered, release = threading.Event(), threading.Event()
        original = self.scheduler.publish_recovery
        results = []

        def delayed(ticket, publish, **options):
            if ticket[0] == 1:
                entered.set()
                assert release.wait(5)
            return original(ticket, publish, **options)

        with patch.object(self.scheduler, "publish_recovery", side_effect=delayed):
            worker = threading.Thread(
                target=lambda: results.append(self.request("POST", "/api/normal-access", {}))
            )
            self.threads.append(worker)
            worker.start()
            assert entered.wait(2)
            assert self.request("POST", "/api/normal-access", {})[0] == 200
            release.set()
            worker.join(5)
        assert results[0][0] == 200
        state = self.repository.get_runtime_state()
        assert state.observed_state == "reset" and state.last_result == "success"
        assert not self.scheduler.is_active

    def test_failed_initial_save_attempts_cleanup_and_retains_failure(self):
        self.helper.release_apply.set()
        with patch.object(
            self.repository, "save_access_policies", side_effect=StorageError("full")
        ):
            result = self.request("POST", "/api/normal-access", {})
        assert result[0] == 503
        assert self.helper.normal_access_calls == 1
        state = self.repository.get_runtime_state()
        assert state.last_action == "reset" and state.last_result == "failure"
        assert self.request("POST", "/api/apply", {})[0] == 503
        assert self.request("POST", "/api/normal-access", {})[0] == 200
        assert self.request("POST", "/api/apply", {})[0] == 200

    def test_final_save_failure_blocks_start_until_a_fresh_saved_cleanup(self):
        self.helper.release_apply.set()
        with patch.object(
            self.repository, "update_runtime_state", side_effect=StorageError("full")
        ):
            assert self.request("POST", "/api/normal-access", {})[0] == 503
        assert self.request("POST", "/api/apply", {})[0] == 503
        assert not self.scheduler.is_active
        assert self.request("POST", "/api/normal-access", {})[0] == 200
        assert self.request("POST", "/api/apply", {})[0] == 200


@pytest.mark.parametrize("failure_finishes_first", [False, True])
def test_overlapping_failure_needs_a_fresh_recovery_after_both_owners_finish(
    failure_finishes_first,
):
    test = RecoveryOwnershipTests()
    test.setUp()
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    lock = threading.Lock()
    calls = 0
    results = [{}, {}]

    def recover():
        nonlocal calls
        with lock:
            index = calls
            calls += 1
        if index < 2:
            entered[index].set()
            assert release[index].wait(5)
            if index == 0:
                raise HelperClientError("older cleanup unknown")
        return {"commands_run": 1}

    def run(index):
        results[index]["response"] = test.request("POST", "/api/normal-access", {})

    try:
        with patch.object(test.helper, "normal_access", side_effect=recover):
            threads = []
            for index in range(2):
                worker = threading.Thread(target=run, args=(index,))
                test.threads.append(worker)
                threads.append(worker)
                worker.start()
                assert entered[index].wait(2)
            first = 0 if failure_finishes_first else 1
            release[first].set()
            threads[first].join(2)
            assert not threads[first].is_alive()
            test.helper.release_apply.set()
            assert test.request("POST", "/api/apply", {})[0] == 503
            release[1 - first].set()
            threads[1 - first].join(2)
            assert not threads[1 - first].is_alive()
            assert results[0]["response"][0] == 503
            state = test.repository.get_runtime_state()
            assert state.observed_state == "error" and state.last_result == "failure"
            assert test.request("POST", "/api/apply", {})[0] == 503
            assert not test.scheduler.is_active
            assert test.request("POST", "/api/normal-access", {})[0] == 200
            assert test.request("POST", "/api/apply", {})[0] == 200
    finally:
        for gate in release:
            gate.set()
        test.doCleanups()
