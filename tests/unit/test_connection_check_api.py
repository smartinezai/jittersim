"""Connection check: the manager, its limits, the saved results and the routes."""

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from jittersim.api import connection_checks as checks_module
from jittersim.api.connection_checks import (
    CHECK_READS,
    CHECK_WRITES,
    ConnectionCheckManager,
)
from jittersim.api.server import WRITE_PATHS, ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Impairment
from jittersim.config.speed_tests import ConnectionCheckStore, SpeedTestStore
from jittersim.config.storage import ConfigRepository
from jittersim.connection_check import DEFAULT_PROBES, PROBE_INTERVAL_SECONDS
from jittersim.engine.plan import (
    GENERAL_QUEUE_ID,
    Direction,
    ServiceAssignmentRule,
)
from jittersim.networking.helper import HelperClientError
from jittersim.speed_test import NoProber
from tests.unit import test_device_speed_test as speed

PHONE = speed.PHONE
OTHER = speed.OTHER
IMPAIRMENT = {"delay_ms": 100, "jitter_ms": 0, "loss_percent": 10, "bandwidth_kbit": None,
              "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0}


class CheckHelper(speed.CountingHelper):
    """Reports the impairment the test sets on the lab device's queues."""

    def __init__(self, clock, owner):
        super().__init__(clock)
        self.owner = owner

    def queue_stats(self):
        if self.queue_id != "lab":
            return super().queue_stats()
        self.queue_reads += 1
        if self.on_read is not None:
            hook, self.on_read = self.on_read, None
            hook()
        if self.fail or self.fail_next > 0:
            self.fail_next = max(0, self.fail_next - 1)
            raise HelperClientError("helper is busy", reason="helper-busy")
        impairment = dict(self.owner.impairment)
        return [
            speed.queue(self.queue_id, "download", 0, generation=self.generation,
                        impairment=impairment),
            speed.queue(self.queue_id, "upload", 0, generation=self.generation,
                        impairment={key: (None if key == "bandwidth_kbit" else 0)
                                    for key in impairment}),
            speed.queue("someone-else", "download", 5),
        ]


class ScriptedCheckProber:
    """Answers each probe from a script: index to (round trip in ms or None, copies)."""

    status = "measured"
    reason = None

    def __init__(self, target, probes, script, on_send=None):
        self.target, self.probes, self.script, self.on_send = target, probes, script, on_send
        self.items = []
        self.closed = False
        self.arrivals = 0

    def send(self):
        index = len(self.items)
        rtt, copies = self.script(index)
        arrival = None
        if rtt is not None:
            arrival, self.arrivals = self.arrivals, self.arrivals + 1
        self.items.append({"rtt_ms": rtt, "copies": copies if rtt is not None else 0,
                           "arrival": arrival})
        if self.on_send is not None:
            self.on_send(index)

    def collect(self, until):
        pass

    def counts(self):
        return len(self.items), sum(1 for item in self.items if item["rtt_ms"] is not None)

    def records(self):
        return [dict(item) for item in self.items]

    def close(self):
        self.closed = True


def typical(index):
    """A device behind 100 ms delay and 10 percent loss."""

    return (None, 0) if index % 10 == 3 else (100.0 + index % 4, 1)


class CheckBase(speed.ManagerBase):
    def setUp(self) -> None:
        super().setUp()
        self.impairment = dict(IMPAIRMENT)
        self.burst = 0
        self.script = typical
        self.on_send = None
        self.probers = []
        self.helper = CheckHelper(self.clock, self)
        self.server.helper_client = self.helper
        self.checks = ConnectionCheckManager(self.manager)
        self.server._connection_check_manager = self.checks
        self.set_impairment()

        def factory(target, probes):
            prober = ScriptedCheckProber(target, probes, lambda index: self.script(index),
                                         lambda index: self.on_send and self.on_send(index))
            self.probers.append(prober)
            return prober

        self.server.connection_check_prober_factory = factory
        self.manager.sleep = self.advance

    def advance(self, seconds):
        self.clock[0] += seconds

    def set_impairment(self, **changes):
        """Change what the helper reports and what the scheduler's plan says, together."""

        # The helper's client cleaner drops the burst length: only the plan knows it.
        self.burst = changes.pop("loss_burst_packets", self.burst)
        self.impairment.update(changes)
        planned = Impairment(loss_burst_packets=self.burst, **self.impairment)
        queues = tuple(
            replace(queue, impairment=planned)
            if queue.device_id == "lab" and queue.direction == Direction.DOWNLOAD else queue
            for queue in self.scheduler.plan.queues)
        self.scheduler.plan = replace(self.scheduler.plan, queues=queues)

    def start(self, mac=PHONE, **payload):
        return self.write("/api/connection-checks/start", {"mac_address": mac, **payload})

    def finish(self, run_id):
        """Run the burst on this thread, as the sampler thread would."""

        self.checks._loop(self.manager.runs[run_id])
        return json.loads(self.read("/api/connection-checks/status", "run=" + run_id)[2])

    def get(self, path, query=""):
        status, content_type, body = self.read(path, query)
        return status, content_type, body


class StartTests(CheckBase):
    def test_a_start_returns_a_running_check_with_the_defaults(self) -> None:
        status, run = self.start(mac=PHONE.upper(), label="Before")
        self.assertEqual(status, 201)
        self.assertEqual((run["kind"], run["state"], run["device_name"], run["label"]),
                         ("check", "running", "Lab phone", "Before"))
        self.assertEqual((run["probes_planned"], run["probes_sent"], run["source"]),
                         (DEFAULT_PROBES, 0, "device"))
        self.assertEqual(self.probers[0].target, "192.168.50.10")
        self.assertEqual(len(self.manager.active("check")), 1)
        self.assertEqual(self.manager.active("speed"), [])

    def test_the_request_is_checked_field_by_field(self) -> None:
        cases = [
            ({"mac_address": PHONE, "speed": 1}, "Only mac_address"),
            ({"mac_address": "nonsense"}, "Choose a connected device"),
            ({"mac_address": None}, "Choose a connected device"),
            ({"mac_address": PHONE, "probes": 19}, "20 to 300"),
            ({"mac_address": PHONE, "probes": 301}, "20 to 300"),
            ({"mac_address": PHONE, "probes": True}, "20 to 300"),
            ({"mac_address": PHONE, "probes": 50.5}, "20 to 300"),
            ({"mac_address": PHONE, "probes": "100"}, "20 to 300"),
            ({"mac_address": PHONE, "label": "x" * 81}, "at most 80"),
            ({"mac_address": PHONE, "label": 7}, "Label must be text"),
        ]
        for payload, message in cases:
            status, body = self.write("/api/connection-checks/start", payload)
            self.assertEqual(status, 400, payload)
            self.assertIn(message, body["error"], payload)
        self.assertEqual(self.manager.runs, {})
        self.assertEqual(self.start(probes=20)[0], 201)

    def test_the_device_must_be_connected_and_the_status_known(self) -> None:
        self.helper.on_wifi = [OTHER]
        status, body = self.start()
        self.assertEqual(status, 409)
        self.assertIn("not connected", body["error"])
        self.helper.on_wifi = [PHONE, OTHER]
        self.helper.association = False
        status, body = self.start()
        self.assertEqual(status, 503)
        self.assertIn("unknown", body["error"])
        self.helper.association = True
        self.helper.snapshot_fail = True
        self.assertEqual(self.start()[0], 503)
        self.server.helper_client = None
        status, body = self.start()
        self.assertEqual(status, 503)
        self.assertIn("helper is not connected", body["error"])

    def test_one_per_device_and_two_in_total_across_both_kinds(self) -> None:
        self.assertEqual(self.start()[0], 201)
        status, body = self.start()
        self.assertEqual(status, 409)
        self.assertIn("connection check is already running", body["error"])
        # A speed test on the same device is refused too, with the right name.
        status, body = self.write("/api/speed-tests/start",
                                  {"mac_address": PHONE, "duration_seconds": 15})
        self.assertEqual(status, 409)
        self.assertIn("connection check is already running", body["error"])
        self.assertEqual(self.write("/api/speed-tests/start", {
            "mac_address": OTHER, "duration_seconds": 15})[0], 201)
        self.helper.on_wifi = [PHONE, OTHER, "02:00:00:00:00:99"]
        self.helper.addresses["02:00:00:00:00:99"] = "192.168.50.99"
        status, body = self.start(mac="02:00:00:00:00:99")
        self.assertEqual(status, 409)
        self.assertIn("At most 2", body["error"])

    def test_a_check_blocks_a_speed_test_on_the_same_device_and_the_other_way_round(self) -> None:
        self.write("/api/speed-tests/start", {"mac_address": PHONE, "duration_seconds": 15})
        status, body = self.start()
        self.assertEqual(status, 409)
        self.assertIn("speed test is already running", body["error"])

    def test_a_protected_device_is_allowed_and_reports_the_baseline(self) -> None:
        self.repository.save_unaffected_device(UnaffectedDevice(PHONE, "Phone", 2))
        reads = self.helper.queue_reads
        status, run = self.start()
        self.assertEqual(status, 201)
        self.assertEqual((run["protected"], run["source"]), (True, "none"))
        self.assertIn("protected", run["source_note"])
        # No queue is read for a device that has no profile.
        self.assertEqual(self.helper.queue_reads, reads)
        result = self.finish(run["id"])["result"]
        self.assertTrue(result["protected"])
        self.assertEqual(result["summary"]["verdict"], "baseline")
        self.assertFalse(result["summary"]["shaped"])
        self.assertEqual(result["profile"]["profile_name"], "No profile")

    def test_a_device_without_an_address_or_a_sender_is_refused_with_a_reason(self) -> None:
        self.helper.addresses[PHONE] = None
        status, body = self.start()
        self.assertEqual(status, 409)
        self.assertIn("no address", body["error"])
        self.helper.addresses[PHONE] = "192.168.50.10"
        self.server.connection_check_prober_factory = lambda ip, probes: NoProber("not allowed")
        status, body = self.start()
        self.assertEqual(status, 503)
        self.assertIn("not allowed", body["error"])
        self.assertIn("Test speed", body["error"])
        self.assertEqual(self.manager.runs, {})

    def test_the_default_sender_is_the_icmp_prober_with_room_for_every_probe(self) -> None:
        del self.server.connection_check_prober_factory
        built = {}

        class Recorder(NoProber):
            def __init__(self, target, **options):
                super().__init__("unavailable")
                built.update(target=target, **options)

        with mock.patch.object(checks_module, "IcmpProber", Recorder):
            self.start(probes=250)
        self.assertEqual(built["target"], "192.168.50.10")
        self.assertEqual((built["max_probes"], built["payload_size"]), (250, 56))

    def test_a_thread_that_cannot_start_leaves_no_phantom_run(self) -> None:
        self.manager.autostart = True
        original = threading.Thread.start

        def start_then_fail(thread):
            original(thread)
            raise RuntimeError("interrupted")

        with mock.patch.object(threading.Thread, "start", autospec=True,
                               side_effect=start_then_fail):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.checks.start({"mac_address": PHONE})
        self.assertEqual(self.manager.active(), [])
        self.assertTrue(self.manager.closed)


class ResultTests(CheckBase):
    def test_a_correct_profile_matches_and_the_result_is_saved(self) -> None:
        _, run = self.start(label="Lossy")
        ended = self.finish(run["id"])
        self.assertEqual(ended["state"], "ended")
        result = ended["result"]
        self.assertEqual((result["status"], result["label"], result["source"]),
                         ("complete", "Lossy", "device"))
        summary = result["summary"]
        self.assertEqual(summary["verdict"], "matches")
        self.assertEqual((summary["probes"]["sent"], summary["probes"]["lost"]), (100, 10))
        self.assertEqual(summary["expected"]["loss_percent"], 10)
        self.assertEqual(result["profile"]["profile_name"], "Slow")
        self.assertEqual(result["provider"], "icmp-echo")
        self.assertTrue(self.probers[0].closed)
        self.assertEqual(self.manager.active(), [])
        listing = json.loads(self.get("/api/connection-checks", "mac=" + PHONE)[2])
        self.assertEqual([item["id"] for item in listing["results"]], [result["id"]])
        self.assertEqual((listing["limits"]["default_probes"], listing["limits"]["max_probes"]),
                         (100, 300))
        self.assertIn("matches", listing["verdict_labels"])
        self.assertEqual(listing["active"], [])

    def test_a_profile_that_is_not_in_effect_is_less_than_set(self) -> None:
        self.script = lambda index: (4.0, 1)
        _, run = self.start()
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["verdict"], "less")

    def test_the_burst_length_comes_from_the_plan(self) -> None:
        self.set_impairment(loss_burst_packets=4)
        _, run = self.start()
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["burst_packets"], 4)
        # The same ten lost probes of a hundred are inside a much wider range with bursts.
        self.assertEqual(summary["verdict"], "matches")

    def test_a_late_second_answer_is_waited_for_when_duplication_is_set(self) -> None:
        self.set_impairment(duplicate_percent=10, loss_percent=0, delay_ms=0)
        self.script = lambda index: (5.0, 2 if index % 10 == 0 else 1)
        _, run = self.start()
        start = self.clock[0]
        summary = self.finish(run["id"])["result"]["summary"]
        elapsed = self.clock[0] - start
        # 100 probes at 0.1 s, then the whole grace time of one second.
        self.assertGreaterEqual(elapsed, 100 * PROBE_INTERVAL_SECONDS + 1.0 - 0.01)
        self.assertEqual(summary["probes"]["answered_twice"], 10)
        self.assertEqual(summary["verdict"], "matches")

    def test_the_wait_ends_at_once_when_every_probe_is_answered_and_nothing_duplicates(self) -> None:
        self.set_impairment(loss_percent=0, delay_ms=0)
        self.script = lambda index: (5.0, 1)
        _, run = self.start()
        start = self.clock[0]
        self.finish(run["id"])
        self.assertLess(self.clock[0] - start, 100 * PROBE_INTERVAL_SECONDS + 0.5)

    def test_a_shared_profile_is_not_comparable(self) -> None:
        self.helper.queue_id = GENERAL_QUEUE_ID
        _, run = self.start(mac=OTHER)
        self.assertEqual(run["source"], "everyone")
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["verdict"], "not-comparable")
        self.assertFalse(summary["shaped"])

    def test_a_device_without_a_profile_is_a_baseline(self) -> None:
        _, run = self.start(mac=OTHER)
        self.assertEqual(run["source"], "none")
        self.assertEqual(self.finish(run["id"])["result"]["summary"]["verdict"], "baseline")

    def test_a_reset_network_is_a_baseline_with_a_note(self) -> None:
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None)
        _, run = self.start()
        self.assertEqual(run["source"], "none")
        self.assertIn("reset", run["source_note"])
        self.assertEqual(self.finish(run["id"])["result"]["summary"]["verdict"], "baseline")

    def test_a_service_rule_withholds_the_verdict_but_keeps_the_numbers(self) -> None:
        rule = ServiceAssignmentRule(
            assignment_id="a", device_id="lab", device_ip="192.168.50.10", domains=("x.com",),
            mark=0, upload_impairment=Impairment(), download_impairment=Impairment())
        self.scheduler.plan = replace(self.scheduler.plan, service_assignment_rules=(rule,))
        _, run = self.start()
        self.assertIn("service rule", run["source_note"])
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["verdict"], "service-rules")
        self.assertEqual(summary["probes"]["lost"], 10)

    def test_a_missing_applied_plan_withholds_the_verdict(self) -> None:
        self.scheduler.applied_snapshot = lambda: (None, None)
        _, run = self.start()
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["verdict"], "service-rules-unknown")

    def test_a_profile_beyond_the_window_is_out_of_range(self) -> None:
        self.set_impairment(delay_ms=6000, loss_percent=0)
        self.script = lambda index: (None, 0)
        _, run = self.start()
        self.assertGreater(self.manager.runs[run["id"]].grace, 9)
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["verdict"], "out-of-range")

    def test_a_device_that_ignores_ping_is_no_answer(self) -> None:
        self.script = lambda index: (None, 0)
        _, run = self.start()
        summary = self.finish(run["id"])["result"]["summary"]
        self.assertEqual(summary["verdict"], "no-answer")
        self.assertIn("ignore ping", summary["notes"][0])


class ChangeTests(CheckBase):
    def test_a_profile_that_changes_during_the_burst_is_seen_by_the_watch(self) -> None:
        def change(index):
            if index == 30:
                self.set_impairment(loss_percent=40)

        self.on_send = change
        _, run = self.start()
        ended = self.finish(run["id"])
        result = ended["result"]
        self.assertEqual(result["status"], "changed")
        self.assertEqual(result["summary"]["verdict"], "changed")
        self.assertIn("changed during the check", ended["ended_reason"])
        # The burst stopped early: the numbers are kept.
        self.assertLess(result["summary"]["probes"]["sent"], 100)
        self.assertGreater(result["summary"]["probes"]["sent"], 30)

    def test_a_new_apply_at_the_end_changes_the_queue_identity(self) -> None:
        def change(index):
            if index == 99:
                self.helper.generation = "0a1b2c3d-2"

        self.on_send = change
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]),
                         ("changed", "changed"))
        self.assertFalse(result["summary"]["profile_unchanged"])

    def test_a_device_that_left_the_wifi_or_moved_address_is_changed(self) -> None:
        def leave(index):
            if index == 99:
                self.helper.on_wifi = [OTHER]

        self.on_send = leave
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertIn("left the test Wi-Fi", result["summary"]["notes"][0])
        self.helper.on_wifi = [PHONE, OTHER]

        def move(index):
            if index == 99:
                self.helper.addresses[PHONE] = "192.168.50.11"

        self.on_send = move
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual(result["summary"]["verdict"], "changed")
        self.assertIn("address changed", result["summary"]["notes"][0])

    def test_protection_that_changes_during_the_burst_is_changed(self) -> None:
        def protect(index):
            if index == 99:
                self.repository.save_unaffected_device(UnaffectedDevice(PHONE, "Phone", 2))

        self.on_send = protect
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual(result["summary"]["verdict"], "changed")
        self.assertIn("protection changed", result["summary"]["notes"][0])

    def test_a_profile_applied_during_a_baseline_check_is_seen(self) -> None:
        # The device has no profile when the check starts. The scheduler then marks its address.
        def apply_profile(index):
            if index == 50:
                rule = replace(self.scheduler.plan.mark_rules[0],
                               ipv4_address=type(self.scheduler.plan.mark_rules[0].ipv4_address)(
                                   "192.168.50.77"))
                self.scheduler.plan = replace(self.scheduler.plan, mark_rules=(rule,))

        self.on_send = apply_profile
        _, run = self.start(mac=OTHER)
        self.assertEqual(run["source"], "none")
        ended = self.finish(run["id"])
        result = ended["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]), ("changed", "changed"))
        self.assertIn("changed during the check", ended["ended_reason"])

    def test_a_harmless_change_of_a_shared_rule_does_not_mark_a_good_check(self) -> None:
        def tweak(index):
            if index == 50:
                general = self.scheduler.plan.general_profile
                self.scheduler.plan = replace(
                    self.scheduler.plan,
                    general_profile=replace(general, outage_active=True, allowed_addresses=()))
            if index == 60:
                # The source's label and step only word a message: a ticking label is no change.
                state = self.scheduler.states["lab"]
                self.scheduler.states["lab"] = replace(
                    state, source=replace(state.source, label="3 min left", step="2"))

        self.on_send = tweak
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]), ("complete", "matches"))

    def test_a_new_source_for_the_device_marks_the_check_changed(self) -> None:
        def new_timer(index):
            if index == 50:
                self.scheduler.set_source("lab", "timer", "Slow")

        self.on_send = new_timer
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]), ("changed", "changed"))

    def test_a_missing_final_address_is_not_taken_as_unchanged(self) -> None:
        def lose_address(index):
            if index == 99:
                self.helper.addresses[PHONE] = None

        self.on_send = lose_address
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]), ("changed", "changed"))
        self.assertIn("address changed", result["summary"]["notes"][0])

    def test_a_cancel_during_the_final_reads_starts_no_further_read(self) -> None:
        _, run = self.start()
        reads = self.helper.queue_reads
        original = self.helper.connected_device_snapshot

        def snapshot_then_cancel():
            snapshot = original()
            self.write("/api/connection-checks/cancel", {"run_id": run["id"]})
            return snapshot

        self.helper.connected_device_snapshot = snapshot_then_cancel
        ended = self.finish(run["id"])
        self.assertEqual((ended["state"], ended["result"]), ("ended", None))
        self.assertEqual(self.helper.queue_reads, reads)

    def test_final_reads_that_fail_give_no_verdict(self) -> None:
        def break_queues(index):
            if index == 99:
                self.helper.fail = True

        self.on_send = break_queues
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]),
                         ("complete", "unverified"))
        self.assertEqual(result["summary"]["probes"]["lost"], 10)
        self.helper.fail = False

        def break_wifi(index):
            if index == 99:
                self.helper.snapshot_fail = True

        self.on_send = break_wifi
        _, run = self.start()
        self.assertEqual(self.finish(run["id"])["result"]["summary"]["verdict"], "unverified")

    def test_one_helper_attempt_for_each_final_read(self) -> None:
        _, run = self.start()
        reads = self.helper.queue_reads
        self.finish(run["id"])
        self.assertEqual(self.helper.queue_reads, reads + 1)

    def test_an_unreadable_final_state_of_a_baseline_run_does_not_matter(self) -> None:
        self.helper.queue_id = GENERAL_QUEUE_ID

        def break_queues(index):
            if index == 99:
                self.helper.fail = True

        self.on_send = break_queues
        _, run = self.start(mac=OTHER)
        self.assertEqual(self.finish(run["id"])["result"]["summary"]["verdict"],
                         "not-comparable")


class EndingTests(CheckBase):
    def test_cancel_saves_nothing_and_a_second_cancel_is_refused(self) -> None:
        _, run = self.start()
        status, ended = self.write("/api/connection-checks/cancel", {"run_id": run["id"]})
        self.assertEqual(status, 200)
        self.assertEqual((ended["state"], ended["result"], ended["ended_reason"]),
                         ("ended", None, "Cancelled. Nothing was saved."))
        self.assertEqual(ConnectionCheckStore(self.repository).list(), [])
        status, body = self.write("/api/connection-checks/cancel", {"run_id": run["id"]})
        self.assertEqual(status, 409)
        self.assertIn("connection check has already ended", body["error"])
        # The burst notices at once and saves nothing.
        self.checks._loop(self.manager.runs[run["id"]])
        self.assertEqual(ConnectionCheckStore(self.repository).list(), [])
        self.assertEqual(self.write("/api/connection-checks/cancel", {"run_id": "nope"})[0], 404)
        self.assertEqual(self.write("/api/connection-checks/cancel", {})[0], 400)

    def test_a_fault_in_the_sender_is_saved_as_a_fault(self) -> None:
        def explode(index):
            raise RuntimeError("socket gone")

        self.script = explode
        _, run = self.start()
        with self.assertLogs(checks_module.LOGGER, level="ERROR"):
            result = self.finish(run["id"])["result"]
        self.assertEqual(result["status"], "fault")
        self.assertEqual(result["summary"]["ended_reason"], "The check stopped after an error.")
        self.assertEqual(result["summary"]["verdict"], "fault")

    def test_a_fault_after_matching_probes_gives_no_verdict(self) -> None:
        def explode_late(index):
            if index == 80:
                raise RuntimeError("socket gone")
            return typical(index)

        self.script = explode_late
        _, run = self.start()
        with self.assertLogs(checks_module.LOGGER, level="ERROR"):
            result = self.finish(run["id"])["result"]
        self.assertEqual((result["status"], result["summary"]["verdict"]), ("fault", "fault"))
        self.assertEqual(result["summary"]["probes"]["sent"], 80)
        self.assertTrue(all(row["status"] == "not-judged" for row in result["summary"]["metrics"]))

    def test_shutdown_stops_a_running_check_without_saving(self) -> None:
        _, run = self.start()
        self.manager.shutdown()
        self.assertEqual(self.manager.runs[run["id"]].state, "ended")
        self.checks._loop(self.manager.runs[run["id"]])
        self.assertEqual(ConnectionCheckStore(self.repository).list(), [])
        status, body = self.start()
        self.assertEqual(status, 503)

    def test_the_deadline_stops_a_sender_that_never_answers(self) -> None:
        self.script = lambda index: (None, 0)
        self.manager.sleep = lambda seconds: self.advance(seconds / 4)
        _, run = self.start()
        self.finish(run["id"])
        run_object = self.manager.runs[run["id"]]
        self.assertLessEqual(self.clock[0], run_object.deadline + 0.2)

    def test_a_storage_fault_is_reported_without_a_result(self) -> None:
        _, run = self.start()
        with mock.patch.object(ConnectionCheckStore, "save", side_effect=sqlite3.Error("busy")):
            ended = self.finish(run["id"])
        self.assertIsNone(ended["result"])
        self.assertEqual(ended["ended_reason"], "The result could not be saved.")


class RouteTests(CheckBase):
    def test_status_is_scoped_to_checks_and_the_speed_routes_do_not_see_them(self) -> None:
        _, check_run = self.start()
        _, speed_run = self.write("/api/speed-tests/start", {
            "mac_address": OTHER, "duration_seconds": 15})
        self.assertEqual(self.get("/api/connection-checks/status", "run=" + check_run["id"])[0],
                         200)
        self.assertEqual(self.get("/api/connection-checks/status", "run=" + speed_run["id"])[0],
                         404)
        self.assertEqual(self.get("/api/speed-tests/status", "run=" + check_run["id"])[0], 404)
        self.assertEqual(self.write("/api/speed-tests/finish", {"run_id": check_run["id"]})[0],
                         404)
        self.assertEqual(self.write("/api/speed-tests/cancel", {"run_id": check_run["id"]})[0],
                         404)
        self.assertEqual(self.write("/api/connection-checks/cancel",
                                    {"run_id": speed_run["id"]})[0], 404)
        listing = json.loads(self.get("/api/speed-tests")[2])
        self.assertEqual([item["id"] for item in listing["active"]], [speed_run["id"]])
        checks = json.loads(self.get("/api/connection-checks")[2])
        self.assertEqual([item["id"] for item in checks["active"]], [check_run["id"]])

    def test_results_export_and_delete_use_their_own_table(self) -> None:
        _, run = self.start()
        result = self.finish(run["id"])["result"]
        # A speed test result with the same number lives in the other table.
        saved = SpeedTestStore(self.repository).save({
            "started_at": 1, "finished_at": 2, "mac_address": PHONE, "device_name": "Phone",
            "status": "complete", "provider": "cloudflare", "source": "device"})
        self.assertEqual(saved["id"], result["id"])
        self.assertEqual(len(SpeedTestStore(self.repository).list()), 1)
        csv_text = self.get("/api/connection-checks/export", "format=csv&mac=" + PHONE)
        self.assertEqual(csv_text[0], 200)
        self.assertIn("text/csv", csv_text[1])
        self.assertIn("verdict", csv_text[2].decode().splitlines()[0])
        self.assertIn("matches", csv_text[2].decode())
        exported = json.loads(self.get("/api/connection-checks/export")[2])
        self.assertEqual(exported["format"], "jittersim-connection-checks")
        self.assertEqual(self.get("/api/connection-checks/export", "format=xml")[0], 400)
        self.assertEqual(self.get("/api/connection-checks", "mac=nonsense")[0], 400)
        status, body = self.write("/api/connection-checks/delete", {"result_id": result["id"]})
        self.assertEqual((status, body), (200, {"deleted": True}))
        self.assertEqual(ConnectionCheckStore(self.repository).list(), [])
        # The speed test row with the same number is untouched.
        self.assertEqual(len(SpeedTestStore(self.repository).list()), 1)
        self.assertEqual(self.write("/api/connection-checks/delete",
                                    {"result_id": result["id"]})[0], 404)
        for bad in ({}, {"result_id": 0}, {"result_id": True}, {"result_id": "1"},
                    {"result_id": 2 ** 70}, {"result_id": 1, "extra": 1}):
            self.assertEqual(self.write("/api/connection-checks/delete", bad)[0], 400, bad)

    def test_an_unknown_run_and_too_many_query_fields_are_clean_errors(self) -> None:
        self.assertEqual(self.get("/api/connection-checks/status", "run=nothing")[0], 404)
        self.assertEqual(self.get("/api/connection-checks/status")[0], 404)
        many = "&".join(f"a{index}=1" for index in range(20))
        self.assertEqual(self.get("/api/connection-checks", many)[0], 400)


class StoreTests(unittest.TestCase):
    def row(self, mac=PHONE, **changes):
        row = {"started_at": 1, "finished_at": 2, "mac_address": mac, "device_name": "Phone",
               "status": "complete", "provider": "icmp-echo", "source": "device",
               "summary": {"verdict": "matches"}}
        row.update(changes)
        return row

    def test_the_table_is_added_to_an_old_database_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            ConfigRepository(path).close()
            with sqlite3.connect(path) as connection:
                connection.execute("DROP TABLE connection_check_results")
            for _ in range(2):
                repository = ConfigRepository(path)
                ConnectionCheckStore(repository).save(self.row())
                repository.close()
            repository = ConfigRepository(path)
            self.addCleanup(repository.close)
            self.assertEqual(len(ConnectionCheckStore(repository).list()), 2)
            self.assertEqual(SpeedTestStore(repository).list(), [])

    def test_rows_are_bounded_per_device_and_in_total_for_each_table(self) -> None:
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        checks, speeds = ConnectionCheckStore(repository), SpeedTestStore(repository)
        from jittersim.config import speed_tests as module
        with mock.patch.object(module, "MAX_RESULTS_PER_DEVICE", 3), \
                mock.patch.object(module, "MAX_RESULTS_TOTAL", 5):
            for _ in range(5):
                checks.save(self.row())
            speeds.save(self.row(provider="cloudflare"))
            for _ in range(4):
                checks.save(self.row(mac=OTHER))
        self.assertEqual(len(checks.list(PHONE)), 2)
        self.assertEqual(len(checks.list(OTHER)), 3)
        self.assertEqual(len(checks.list()), 5)
        self.assertEqual(len(speeds.list()), 1)

    def test_a_database_fault_on_delete_rolls_back(self) -> None:
        repository = ConfigRepository(":memory:")
        self.addCleanup(repository.close)
        store = ConnectionCheckStore(repository)
        saved = store.save(self.row())
        with mock.patch.object(store, "db") as database, \
                mock.patch.object(repository, "_rollback") as rollback:
            database.execute.side_effect = sqlite3.OperationalError("database is locked")
            with self.assertRaises(Exception):
                store.delete(saved["id"])
            rollback.assert_called_once()
        self.assertIsNotNone(store.get(saved["id"]))


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="admin-token")
        self.addCleanup(self.server.server_close)

    def test_every_write_is_registered(self) -> None:
        self.assertTrue(CHECK_WRITES <= WRITE_PATHS)
        self.assertEqual(len(CHECK_READS) + len(CHECK_WRITES), 6)

    def test_every_route_needs_the_admin_token(self) -> None:
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            for method, paths in (("GET", CHECK_READS), ("POST", CHECK_WRITES)):
                for path in sorted(paths):
                    for headers in ({}, {"Authorization": "Bearer wrong"}):
                        connection = http.client.HTTPConnection(
                            *self.server.server_address, timeout=5)
                        connection.request(method, path, body="{}" if method == "POST" else None,
                                           headers={**headers, "Content-Type": "application/json"})
                        answer = connection.getresponse()
                        answer.read()
                        connection.close()
                        self.assertEqual(answer.status, 401, (method, path, headers))
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            connection.request("GET", "/api/connection-checks",
                               headers={"Authorization": "Bearer admin-token"})
            answer = connection.getresponse()
            body = json.loads(answer.read())
            connection.close()
            self.assertEqual(answer.status, 200)
            self.assertEqual(body["results"], [])
        finally:
            self.server.shutdown()
            thread.join(timeout=5)

    def test_the_manager_is_made_once_by_the_server_and_shares_the_speed_test_table(self) -> None:
        manager = checks_module.connection_check_manager(self.server)
        self.assertIsInstance(manager, ConnectionCheckManager)
        self.assertIs(manager.speed, self.server._speed_test_manager)


if __name__ == "__main__":
    unittest.main()
