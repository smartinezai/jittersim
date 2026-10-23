"""Progress and watch contracts missed by the original connection-check assertions."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from jittersim.api import connection_checks as checks
from jittersim.api.speed_tests import KEEP_ENDED_RUNS, SpeedTestManager
from tests.unit.test_connection_check_api import CheckBase, PHONE


class ProgressContracts(CheckBase):
    def test_progress_exposes_received_start_source_and_bounded_countdown(self):
        _, answer = self.start(label="Before")
        run = self.manager.runs[answer["id"]]
        run.prober.send()
        now = self.clock[0]
        run.deadline = now + 2.4
        payload = run.payload(now)
        self.assertEqual(payload["mac_address"], PHONE)
        self.assertEqual(payload["probes_received"], 1)
        self.assertEqual(payload["started_at"], int(run.started_at))
        self.assertEqual(payload["source_label"], "This device's own queues")
        self.assertEqual(payload["seconds_left"], 2)
        self.assertEqual(run.payload(now + 5)["seconds_left"], 0)
        run.state = "ended"
        self.assertEqual(run.payload(now)["seconds_left"], 0)

    def test_watch_checks_at_the_interval_and_retains_drift_even_if_profile_returns(self):
        _, answer = self.start()
        run = self.manager.runs[answer["id"]]
        run.last_watch = 10.0
        self.clock[0] = 10 + checks.WATCH_SECONDS / 2
        with patch.object(self.manager, "_applied_snapshot", side_effect=AssertionError("too early")):
            self.assertFalse(self.checks._watch(run))
        self.clock[0] = 10 + checks.WATCH_SECONDS
        self.set_impairment(delay_ms=250)
        self.assertTrue(self.checks._watch(run))
        self.assertIsNotNone(run.drift_reason)
        self.set_impairment(delay_ms=100)
        result = self.finish(answer["id"])["result"]
        self.assertEqual(result["summary"]["verdict"], "changed")


def test_trimming_keeps_running_uncertain_and_live_workers_and_eight_ended_of_each_kind():
    manager = object.__new__(SpeedTestManager)
    manager.runs = {}
    retained = set()
    for kind in ("speed", "check"):
        for name, state, uncertain, alive in (
            ("running", "running", False, False),
            ("uncertain", "ended", True, False),
            ("live", "ended", False, True),
        ):
            identifier = kind + "-" + name
            manager.runs[identifier] = SimpleNamespace(
                id=identifier, kind=kind, state=state, startup_uncertain=uncertain,
                thread=Mock(is_alive=Mock(return_value=alive)),
            )
            retained.add(identifier)
        for index in range(KEEP_ENDED_RUNS + 1):
            identifier = kind + "-ended-" + str(index)
            manager.runs[identifier] = SimpleNamespace(
                id=identifier, kind=kind, state="ended", startup_uncertain=False,
                thread=Mock(is_alive=Mock(return_value=False)),
            )
            if index:
                retained.add(identifier)
    manager._trim()
    assert set(manager.runs) == retained
