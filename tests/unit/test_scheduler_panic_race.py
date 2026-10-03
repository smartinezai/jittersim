"""A scheduled apply that Panic overtook must not leave the impairment in place."""

import unittest
from unittest.mock import patch

from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler


class OvertakenHelper:
    """A helper whose apply is the moment Panic lands."""

    def __init__(self):
        self.scheduler = None
        self.resets = 0
        self.applies = 0

    def gateway_status(self):
        return {"outage_stop_latched": False, "state_error": False}

    def connected_device_snapshot(self):
        return {"devices": [], "association_status_available": True}

    def traffic_counters(self):
        return {"devices": {}, "services": {}}

    def observe_dns_queries(self):
        return []

    def apply(self, *, plan):
        self.applies += 1
        self.scheduler.halt()
        return {"commands_run": 1}

    def reset(self):
        self.resets += 1
        return {"commands_run": 1}


class SchedulerPanicRaceTests(unittest.TestCase):
    def test_a_tick_that_panic_overtook_is_undone_and_not_recorded(self):
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        helper = OvertakenHelper()
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        helper.scheduler = scheduler
        with patch.object(scheduler, "start"):
            scheduler.activate(applied_payload=None)

        applied = scheduler.tick(repository=repository)

        self.assertEqual(helper.applies, 1)
        self.assertFalse(applied)
        self.assertEqual(helper.resets, 1, "the late scheduled apply was left in place")
        self.assertIsNone(scheduler.applied_plan())
        self.assertFalse(scheduler.is_active)


if __name__ == "__main__":
    unittest.main()
