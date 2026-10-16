"""Dashboard Panic never waits for long behind the scheduler lock (carry-forward item c).

A scheduled or dashboard apply publishes its result while it holds the scheduler
lock, and that publish writes to the repository. A database backup can hold the
repository for its whole duration, so the scheduler lock was held just as long and
Panic waited in `begin_recovery()` before it called the helper. Panic now waits a
bounded time, still calls the helper, and reports the recovery as unconfirmed.
"""

import json
import threading
import time
import unittest

from jittersim.api import access as access_module
from jittersim.api import server as server_module
from tests.unit import test_dashboard_concurrency as concurrency


class PanicSchedulerLockTests(unittest.TestCase):
    setUp = concurrency.DashboardConcurrencyTests.setUp
    _stop = concurrency.DashboardConcurrencyTests._stop
    request = concurrency.DashboardConcurrencyTests.request

    def hold_scheduler_lock(self):
        held, release = threading.Event(), threading.Event()

        def publisher():
            # Stands in for a publish that waits for a backup under the lock.
            with self.scheduler._lock:
                held.set()
                release.wait(20)

        thread = threading.Thread(target=publisher, daemon=True)
        self.threads.append(thread)
        thread.start()
        self.assertTrue(held.wait(5))
        self.addCleanup(release.set)
        return release, thread

    def test_panic_calls_the_helper_while_the_scheduler_lock_is_held(self):
        generation = self.scheduler.apply_generation
        self.assertTrue(self.scheduler.can_apply(generation))
        release, thread = self.hold_scheduler_lock()

        started = time.monotonic()
        status, _, data = self.request("POST", "/api/normal-access", {}, timeout=10)
        elapsed = time.monotonic() - started

        self.assertEqual(self.helper.normal_access_calls, 1)
        self.assertLess(elapsed, 3, "Panic waited behind the scheduler lock")
        self.assertEqual(status, 503, data)
        self.assertIn("not confirmed", json.loads(data)["error"])
        self.assertFalse(self.scheduler.is_active)
        release.set()
        thread.join(5)
        # Work that started before Panic can no longer publish or apply.
        self.assertNotEqual(self.scheduler.halt_count, generation)
        self.assertFalse(self.scheduler.can_apply(generation))
        self.assertFalse(self.scheduler.can_apply(self.scheduler.apply_generation))
        # A later Panic that completes confirms the recovery.
        status, _, data = self.request("POST", "/api/normal-access", {})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.helper.normal_access_calls, 2)
        self.assertTrue(self.scheduler.can_apply(self.scheduler.apply_generation))

    def test_panic_calls_the_helper_when_the_lock_is_taken_after_it_began(self):
        # Review finding: another owner takes the lock between begin_recovery() and
        # publish_recovery(), then waits for a backup inside its publish.
        original = self.scheduler.begin_recovery
        holders = []

        def begin_then_lose_the_lock(*args, **kwargs):
            ticket = original(*args, **kwargs)
            holders.append(self.hold_scheduler_lock())
            return ticket

        self.scheduler.begin_recovery = begin_then_lose_the_lock
        started = time.monotonic()
        status, _, data = self.request("POST", "/api/normal-access", {}, timeout=10)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.helper.normal_access_calls, 1)
        self.assertEqual(status, 503, data)
        self.assertIn("not confirmed", json.loads(data)["error"])
        release, thread = holders[0]
        release.set()
        thread.join(5)
        self.scheduler.begin_recovery = original
        self.assertFalse(self.scheduler.can_apply(self.scheduler.apply_generation))
        status, _, data = self.request("POST", "/api/normal-access", {})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.scheduler.can_apply(self.scheduler.apply_generation))

    def test_concurrent_forced_halts_never_reuse_a_generation(self):
        release, thread = self.hold_scheduler_lock()
        seen = []

        def force():
            self.scheduler.begin_recovery(timeout=0)
            seen.append(self.scheduler._generation())

        workers = [threading.Thread(target=force) for _ in range(20)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(self.scheduler._forced_halts, 20)
        release.set()
        thread.join(5)

    def test_a_forced_halt_after_a_ticket_keeps_recovery_pending(self):
        ticket = self.scheduler.begin_recovery()
        release, thread = self.hold_scheduler_lock()
        self.assertIsNone(self.scheduler.begin_recovery(timeout=0.05))
        release.set()
        thread.join(5)
        # The older ticket finishes successfully but cannot confirm the newer Panic.
        self.scheduler.finish_recovery(ticket, lambda overlap_failed=False: (None, True))
        self.assertFalse(self.scheduler.can_apply(self.scheduler.apply_generation))

    def test_the_scheduler_wait_fits_the_panic_database_allowance(self):
        worst = (access_module.PANIC_SCHEDULER_WAIT_SECONDS
                 + access_module.PANIC_SCHEDULER_STEP_FLOOR_SECONDS
                 + access_module.PANIC_REPOSITORY_WAIT_SECONDS * access_module.PANIC_REPOSITORY_STEPS
                 + access_module.PANIC_RESULT_WAIT_SECONDS)
        self.assertLessEqual(worst, server_module.PANIC_DATABASE_ALLOWANCE_SECONDS)
