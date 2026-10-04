"""Scheduled results obey the same cancellation and recovery fence as manual work."""

from unittest.mock import patch

from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.helper import HelperClientError
from tests.unit.test_scheduler_panic_race import OvertakenHelper


class LateHelper(OvertakenHelper):
    def apply(self, *, plan):
        self.applies += 1
        return {"commands_run": 1}


def test_panic_during_scheduled_publication_cannot_leave_success_or_an_active_plan():
    with ConfigRepository(":memory:") as repository:
        helper = LateHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        helper.scheduler = scheduler
        original = repository.rebase_quota_counters

        def late(**values):
            scheduler.halt()
            return original(**values)

        with (
            patch.object(scheduler, "start"),
            patch.object(repository, "rebase_quota_counters", side_effect=late),
        ):
            scheduler.activate(applied_payload=None)
            assert not scheduler.tick(repository=repository)
        assert helper.applies == 1 and helper.resets == 1
        state = repository.get_runtime_state()
        assert state.last_action == "reset" and state.last_result == "success"
        assert not scheduler.is_active and scheduler.applied_plan() is None


def test_failed_scheduled_cleanup_records_failure_and_requires_fresh_recovery():
    with ConfigRepository(":memory:") as repository:
        helper = OvertakenHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        helper.scheduler = scheduler
        with (
            patch.object(scheduler, "start"),
            patch.object(helper, "reset", side_effect=HelperClientError("unknown")),
        ):
            scheduler.activate(applied_payload=None)
            assert not scheduler.tick(repository=repository)
        state = repository.get_runtime_state()
        assert state.last_action == "reset" and state.last_result == "failure"
        assert state.observed_state == "error"
        assert not scheduler.is_active and not scheduler.can_apply(scheduler.halt_count)


def test_failed_scheduled_apply_after_panic_cannot_publish_schedule_failure():
    with ConfigRepository(":memory:") as repository:
        helper = LateHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        helper.scheduler = scheduler

        def fail(*, plan):
            scheduler.halt()
            raise HelperClientError("late apply failed")

        with patch.object(scheduler, "start"), patch.object(helper, "apply", side_effect=fail):
            scheduler.activate(applied_payload=None)
            assert not scheduler.tick(repository=repository)
        state = repository.get_runtime_state()
        assert state.last_action == "reset" and state.last_result == "success"
        assert helper.resets == 1
        assert not scheduler.is_active


def test_external_reset_helper_wait_does_not_hold_panic_admission_lock():
    import threading

    with ConfigRepository(":memory:") as repository:
        helper = LateHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        helper.scheduler = scheduler
        entered, release, halted = threading.Event(), threading.Event(), threading.Event()
        results = []

        def reset():
            entered.set()
            assert release.wait(3)
            return {"commands_run": 1}

        def run():
            results.append(scheduler.tick(repository=repository))

        with (
            patch.object(scheduler, "start"),
            patch.object(repository, "record_schedule_result", return_value=False),
            patch.object(helper, "reset", side_effect=reset),
        ):
            scheduler.activate(applied_payload=None)
            worker = threading.Thread(target=run)
            worker.start()
            assert entered.wait(1)
            panic = threading.Thread(target=lambda: (scheduler.halt(), halted.set()))
            panic.start()
            try:
                assert halted.wait(0.3), "Panic waited for external helper I/O under its state lock"
                assert not scheduler.is_active
            finally:
                release.set()
                worker.join(3)
                panic.join(3)
            assert not worker.is_alive() and not panic.is_alive()
        assert results == [False]
