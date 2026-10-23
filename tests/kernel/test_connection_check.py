"""Prove with a real netem queue what the connection check can and cannot see.

Needs root on Linux and JITTERSIM_KERNEL_TESTS=1 (tests/kernel/run-in-docker.sh). The Pi's own
probe sender runs inside the lab's Pi namespace against the lab's test client, through the queues
the real renderer built. The statistical checks use a 99.99 percent interval, so a correct kernel
fails an assertion about one run in ten thousand. The end-to-end verdict checks get one more
try, which makes a chance failure far less likely again. See
docs/implementation-plan-connection-check-2026-10-17.md.
"""

import json
import os
import shutil
import sys
import unittest

from jittersim import connection_check as check
from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import EveryoneElseRule
from jittersim.networking.applier import NetworkApplier

from .lab import (
    CLIENT_ADDRESS,
    CLIENT_MAC,
    PI,
    TOPOLOGY,
    NamespaceExecutor,
    create_lab,
    remove_lab,
)
from .lab import run_in as _in

ENABLED = (
    sys.platform.startswith("linux")
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and os.geteuid() == 0
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft"))
)
NORMAL = Profile(id="normal", name="Normal")
# z for a two-sided 99.99 percent interval.
Z_NEVER_BY_CHANCE = 3.89
# Runs the production burst loop and sender inside the Pi namespace and prints the records.
RUNNER = r"""
import json, sys, time
from jittersim.connection_check import PAYLOAD_BYTES, run_probe_burst
from jittersim.speed_test import IcmpProber

target, count, interval, grace = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
prober = IcmpProber(target, max_probes=count, payload_size=PAYLOAD_BYTES)
if prober.status != "measured":
    print(json.dumps({"error": prober.reason}))
    sys.exit(0)
run_probe_burst(prober, count, interval=interval, grace=grace, clock=time.monotonic,
                sleep=time.sleep, settle=True)
print(json.dumps(prober.records()))
prober.close()
"""


def setting(**values):
    base = {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "duplicate_percent": 0,
            "reorder_percent": 0, "corrupt_percent": 0, "bandwidth_kbit": None}
    base.update(values)
    return base


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class ConnectionCheckKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        create_lab()
        # The unprivileged ICMP socket of the dashboard needs this range, like on the Pi.
        _in(PI, "sh", "-c", "echo '0 2147483647' > /proc/sys/net/ipv4/ping_group_range")

    @classmethod
    def tearDownClass(cls) -> None:
        remove_lab()

    def setUp(self) -> None:
        self.applier = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)

    def tearDown(self) -> None:
        self.applier.reset()

    def apply(self, download=Impairment(), upload=Impairment(), protected=()):
        profile = Profile(id="lab", name="Lab", upload=upload, download=download)
        device = Device("alice", "alice", CLIENT_ADDRESS, profile.id)
        self.applier.apply(plan=compile_plan(
            devices=[device], profiles={NORMAL.id: NORMAL, profile.id: profile},
            protected_mac_addresses=tuple(protected)))

    def apply_everyone_else(self, download):
        rule = EveryoneElseRule(
            mark=0, upload_impairment=Impairment(), download_impairment=download)
        self.applier.apply(plan=compile_plan(
            devices=[], profiles={NORMAL.id: NORMAL}, everyone_else=rule))

    def probe(self, count=100, interval=0.02, grace=1.5):
        """The production sender and burst loop against the lab client."""

        result = _in(PI, sys.executable, "-c", RUNNER, CLIENT_ADDRESS, str(count),
                     str(interval), str(grace), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertNotIsInstance(data, dict, data)
        self.assertEqual(len(data), count)
        return data

    def assertShare(self, events, total, expected, what):
        """The expected share lies inside the 99.99 percent interval of what was seen."""

        low, high = check.wilson_interval(events, total, Z_NEVER_BY_CHANCE)
        self.assertTrue(low <= expected <= high,
                        f"{what}: {events} of {total} ({events / total:.1%}); the interval "
                        f"{low:.1%} to {high:.1%} does not hold the expected {expected:.1%}")

    def verdict(self, records, **setting_values):
        return check.build_summary(
            records, planned=len(records), source="device",
            impairment=setting(**setting_values), burst_packets=0, service_rules=False)

    # What a check can see.

    def test_a_configured_loss_is_measured_inside_its_interval(self) -> None:
        self.apply(download=Impairment(loss_percent=20))
        stats = check.probe_statistics(self.probe(count=300))
        self.assertShare(stats["lost"], 300, 0.20, "loss")
        # The upload side is not set, so nothing else is wrong.
        self.assertEqual(stats["answered_twice"], 0)

    def test_a_configured_delay_is_the_floor_of_the_round_trip(self) -> None:
        self.apply(download=Impairment(delay_ms=80))
        stats = check.probe_statistics(self.probe(count=60, interval=0.05, grace=2.0))
        self.assertEqual(stats["lost"], 0)
        # The request waits 80 ms in the queue. The answer waits nowhere.
        self.assertGreaterEqual(stats["rtt_min_ms"], 79.0, stats)
        self.assertLessEqual(stats["rtt_median_ms"], 80 + 20, stats)

    def test_a_configured_duplication_is_measured_inside_its_interval(self) -> None:
        self.apply(download=Impairment(duplicate_percent=30))
        records = self.probe(count=300)
        stats = check.probe_statistics(records)
        self.assertEqual(stats["lost"], 0)
        # A second copy of the request makes the device answer twice.
        self.assertShare(stats["answered_twice"], stats["received"], 0.30, "duplicates")

    def test_jitter_matches_the_simulation_of_netem(self) -> None:
        self.apply(download=Impairment(delay_ms=100, jitter_ms=20))
        stats = check.probe_statistics(self.probe(count=150, interval=0.1, grace=2.0))
        expected = check.simulated_timing(100, 20, 0.0)["jitter_ms"]
        self.assertEqual(stats["lost"], 0)
        self.assertGreaterEqual(stats["jitter_ms"], check.JITTER_LOW_FACTOR * expected, stats)
        self.assertLessEqual(stats["jitter_ms"],
                             check.JITTER_HIGH_FACTOR * expected + check.JITTER_SLACK_MS, stats)

    def test_reordering_matches_the_simulation_of_netem(self) -> None:
        self.apply(download=Impairment(delay_ms=300, reorder_percent=25))
        stats = check.probe_statistics(self.probe(count=150, interval=0.1, grace=2.0))
        expected = check.simulated_timing(300, 0, 0.25)["reorder_share"]
        share = stats["reordered"] / stats["received"]
        # Wide on purpose (four standard deviations): the model is checked, not the dice.
        self.assertTrue(0.5 * expected <= share <= 1.5 * expected,
                        f"{share:.1%} reordered, the simulation says {expected:.1%}")
        # Packets that skip the delay come back before the delayed ones: probes are not lost.
        self.assertEqual(stats["lost"], 0)

    def test_the_whole_check_gives_the_right_verdict_for_a_correct_profile(self) -> None:
        self.apply(download=Impairment(delay_ms=60, loss_percent=20))
        for attempt in (1, 2):
            records = self.probe(count=300)
            result = self.verdict(records, delay_ms=60, loss_percent=20)
            if result["verdict"] in ("matches", "borderline"):
                break
        self.assertIn(result["verdict"], ("matches", "borderline"), result["metrics"])

    # What a check must not be fooled by.

    def test_an_unshaped_path_is_called_less_than_set_so_the_check_can_fail(self) -> None:
        self.apply(download=Impairment(), upload=Impairment())
        records = self.probe(count=300)
        self.assertEqual(check.probe_statistics(records)["lost"], 0)
        result = self.verdict(records, delay_ms=100, loss_percent=20)
        self.assertEqual(result["verdict"], "less", result["metrics"])
        self.assertEqual({row["id"]: row["status"] for row in result["metrics"]}["loss"], "less")
        self.assertEqual({row["id"]: row["status"] for row in result["metrics"]}["delay"], "less")

    def test_upload_shaping_does_not_touch_the_answers(self) -> None:
        # Half of a device's own upload is lost and delayed, and the Pi's probes do not notice.
        self.apply(upload=Impairment(loss_percent=50, delay_ms=200))
        stats = check.probe_statistics(self.probe(count=100, grace=2.0))
        self.assertEqual(stats["lost"], 0, stats)
        self.assertLess(stats["rtt_median_ms"], 50, stats)
        # So a check of this profile has no download number to judge.
        result = self.verdict(self.probe(count=100), loss_percent=0)
        self.assertEqual(result["verdict"], "matches")

    def test_a_profile_shared_by_everyone_is_not_applied_to_the_pis_own_probes(self) -> None:
        self.apply_everyone_else(Impairment(loss_percent=50, delay_ms=150))
        stats = check.probe_statistics(self.probe(count=100, grace=2.0))
        self.assertEqual(stats["lost"], 0, stats)
        self.assertLess(stats["rtt_median_ms"], 50, stats)

    def test_a_protected_device_is_not_shaped_even_with_a_profile_of_its_own(self) -> None:
        self.apply(download=Impairment(loss_percent=50, delay_ms=150), protected=(CLIENT_MAC,))
        stats = check.probe_statistics(self.probe(count=100, grace=2.0))
        self.assertEqual(stats["lost"], 0, stats)
        self.assertLess(stats["rtt_median_ms"], 50, stats)


if __name__ == "__main__":
    unittest.main()
