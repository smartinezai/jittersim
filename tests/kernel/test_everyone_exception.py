"""Packet checks: a device with its own profile while Everyone runs for the rest.

These need root on Linux and JITTERSIM_KERNEL_TESTS=1 (tests/kernel/run-in-docker.sh).
They apply the generated nftables and tc rules to a real kernel and measure packets in
both directions. The device follows its own profile. Every other client keeps following
Everyone's, and Protected devices keep bypassing both.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule, ServiceListRule
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.helper import HelperService

from .lab import (
    CLIENT,
    OTHER_CLIENT,
    OTHER_CLIENT_MAC,
    PI,
    ROUTER_ADDRESS,
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
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft", "ping"))
)
NORMAL = Impairment()
NORMAL_PROFILE = Profile(id="normal", name="normal")
SLOW = Impairment(delay_ms=100)
LOSSY = Impairment(loss_percent=100)
EXCEPTION = "192.168.50.10"
FOLLOWER = "192.168.50.11"
OTHER_FOLLOWER = "192.168.50.12"


def profile(profile_id, *, upload=NORMAL, download=NORMAL):
    return Profile(id=profile_id, name=profile_id, upload=upload, download=download)


def mixed_plan(everyone, own, *, outage=False, protected=(), exceptions=(10,)):
    return compile_plan(
        devices=[Device(f"phone-{octet}", f"phone-{octet}", f"192.168.50.{octet}", "own")
                 for octet in exceptions],
        profiles={"everyone": everyone, "own": own},
        general_profile=GeneralProfileRule("everyone", outage_active=outage),
        protected_mac_addresses=protected,
    )


def everyone_only_plan(everyone, *, protected=()):
    return compile_plan(
        devices=[], profiles={"everyone": everyone},
        general_profile=GeneralProfileRule("everyone"), protected_mac_addresses=protected,
    )


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class EveryoneExceptionDatapathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        create_lab()

    @classmethod
    def tearDownClass(cls) -> None:
        remove_lab()

    def setUp(self) -> None:
        self.applier = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)

    def tearDown(self) -> None:
        self.applier.reset()

    def apply(self, plan):
        self.applier.apply(plan=plan)

    def round_trip(self, source=None, *, namespace=CLIENT):
        argv = ["ping", "-n", "-c", "4", "-i", "0.2", "-W", "2"]
        if source:
            argv += ["-I", source]
        result = _in(namespace, *argv, ROUTER_ADDRESS, check=False)
        times = [float(value) for value in re.findall(r"time=([\d.]+) ms", result.stdout)]
        self.assertTrue(times, result.stdout + result.stderr)
        return sum(times) / len(times)

    def reachable(self, source=None, *, namespace=CLIENT, destination=ROUTER_ADDRESS):
        argv = ["ping", "-n", "-c", "3", "-i", "0.2", "-W", "1"]
        if source:
            argv += ["-I", source]
        return _in(namespace, *argv, destination, check=False).returncode == 0

    def assert_split(self, *, exception_slow, others_slow):
        """The device and every other client, including a client on another MAC."""

        for label, measure in (
            ("the device", lambda: self.round_trip(EXCEPTION)),
            ("a client on Everyone", lambda: self.round_trip(FOLLOWER)),
            ("another client on Everyone", lambda: self.round_trip(OTHER_FOLLOWER)),
            ("a client with another MAC", lambda: self.round_trip(namespace=OTHER_CLIENT)),
        ):
            slow = exception_slow if label == "the device" else others_slow
            value = measure()
            if slow:
                self.assertGreater(value, 90, label)
            else:
                self.assertLess(value, 50, label)

    # Both directions follow the split.

    def test_uploads_follow_the_device_profile_and_everyones_profile(self) -> None:
        self.apply(mixed_plan(profile("everyone", upload=SLOW), profile("own")))
        self.assert_split(exception_slow=False, others_slow=True)

        self.apply(mixed_plan(profile("everyone"), profile("own", upload=SLOW)))
        self.assert_split(exception_slow=True, others_slow=False)

    def test_downloads_follow_the_device_profile_and_everyones_profile(self) -> None:
        # Only downloads are delayed, so the reply leg shows which class the download used.
        self.apply(mixed_plan(profile("everyone", download=SLOW), profile("own")))
        self.assert_split(exception_slow=False, others_slow=True)

        self.apply(mixed_plan(profile("everyone"), profile("own", download=SLOW)))
        self.assert_split(exception_slow=True, others_slow=False)

    def test_each_direction_uses_the_profile_that_owns_it(self) -> None:
        # Everyone slows uploads only and the device slows downloads only.
        self.apply(mixed_plan(
            profile("everyone", upload=SLOW), profile("own", download=SLOW)))

        self.assert_split(exception_slow=True, others_slow=True)
        self.apply(mixed_plan(
            profile("everyone", upload=SLOW), profile("own", upload=Impairment(delay_ms=300))))
        self.assertGreater(self.round_trip(EXCEPTION), 280)
        self.assertLess(self.round_trip(FOLLOWER), 150)
        self.assertGreater(self.round_trip(FOLLOWER), 90)

    def test_packet_loss_stays_with_its_own_class(self) -> None:
        lossy = profile("own", upload=LOSSY, download=LOSSY)
        self.apply(mixed_plan(profile("everyone"), lossy))
        self.assertFalse(self.reachable(EXCEPTION))
        self.assertTrue(self.reachable(FOLLOWER))
        self.assertTrue(self.reachable(namespace=OTHER_CLIENT))

        blocked_everyone = profile("everyone", upload=LOSSY, download=LOSSY)
        self.apply(mixed_plan(blocked_everyone, profile("own")))
        self.assertTrue(self.reachable(EXCEPTION))
        self.assertFalse(self.reachable(FOLLOWER))
        self.assertFalse(self.reachable(namespace=OTHER_CLIENT))
        # The Pi's own traffic never follows a client profile.
        self.assertTrue(self.reachable(namespace=PI))

    def test_an_everyone_outage_cuts_everyone_else_but_not_the_device(self) -> None:
        self.apply(mixed_plan(profile("everyone"), profile("own"), outage=True))

        self.assertTrue(self.reachable(EXCEPTION))
        self.assertFalse(self.reachable(FOLLOWER))
        self.assertFalse(self.reachable(namespace=OTHER_CLIENT))
        self.assertTrue(self.reachable(namespace=PI))
        # Everyone's clients still reach the Pi itself, like during any Everyone outage.
        self.assertTrue(self.reachable(FOLLOWER, destination="192.168.50.1"))

    def test_a_protected_client_bypasses_everyone_and_the_exception_alike(self) -> None:
        self.apply(mixed_plan(
            profile("everyone", upload=SLOW, download=SLOW),
            profile("own", upload=Impairment(delay_ms=300)),
            protected=(OTHER_CLIENT_MAC,)))

        self.assertLess(self.round_trip(namespace=OTHER_CLIENT), 50, "protected")
        self.assertGreater(self.round_trip(EXCEPTION), 280, "the device")
        self.assertGreater(self.round_trip(FOLLOWER), 180, "Everyone")

    def test_two_devices_each_keep_their_own_profile(self) -> None:
        self.apply(mixed_plan(
            profile("everyone"), profile("own", upload=SLOW), exceptions=(10, 11)))

        self.assertGreater(self.round_trip(EXCEPTION), 90)
        self.assertGreater(self.round_trip(FOLLOWER), 90)
        self.assertLess(self.round_trip(OTHER_FOLLOWER), 50)
        self.assertLess(self.round_trip(namespace=OTHER_CLIENT), 50)

    # What the helper counts and what a rule change disturbs.

    def test_counters_and_drops_are_kept_apart(self) -> None:
        service = HelperService(self.applier)
        lossy = profile("everyone", upload=Impairment(loss_percent=50))
        own = profile("own", upload=Impairment(loss_percent=50))
        plan = mixed_plan(lossy, own)
        applied = service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(applied["ok"], applied)

        for source in (EXCEPTION, FOLLOWER):
            _in(CLIENT, "ping", "-n", "-q", "-c", "200", "-i", "0.005", "-W", "1",
                "-I", source, ROUTER_ADDRESS, check=False)
        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        directions = counters["directions"]["devices"]
        self.assertEqual(directions["phone-10"]["upload_packets"], 200)
        self.assertEqual(directions["general-profile"]["upload_packets"], 200)
        for key in ("phone-10", "general-profile"):
            dropped = counters["queues"]["devices"][key]["upload_dropped"]
            self.assertTrue(40 <= dropped <= 160, f"{key}: {dropped} of 200 dropped")

    def test_reapplying_the_same_plan_leaves_the_same_state(self) -> None:
        plan = mixed_plan(profile("everyone", upload=SLOW), profile("own"))

        def state():
            outputs = [
                _in(PI, "tc", "qdisc", "show", "dev", interface).stdout
                + _in(PI, "tc", "filter", "show", "dev", interface).stdout
                for interface in ("eth0", "wlan0")
            ]
            rules = _in(PI, "nft", "list", "table", "inet", "jittersim").stdout
            download = _in(PI, "nft", "list", "table", "netdev", "jittersim_download").stdout
            return outputs, re.sub(r"packets \d+ bytes \d+", "", rules + download)

        self.apply(plan)
        first = state()
        self.apply(plan)

        self.assertEqual(state(), first)

    def test_listed_downloads_of_the_device_are_not_everyones_traffic(self) -> None:
        service = HelperService(self.applier)
        listed = ServiceListRule("router", ("router.example",), 0, NORMAL_PROFILE.upload,
                                 NORMAL_PROFILE.download)
        plan = compile_plan(
            devices=[Device("phone-10", "phone-10", EXCEPTION, "own")],
            profiles={"everyone": profile("everyone"), "own": profile("own")},
            general_profile=GeneralProfileRule("everyone"), service_list_rules=(listed,))
        self.applier.apply(plan=plan, service_addresses={"router": [IPv4Address(ROUTER_ADDRESS)]})

        for source in (EXCEPTION, FOLLOWER):
            _in(CLIENT, "ping", "-n", "-q", "-c", "50", "-i", "0.01", "-W", "1",
                "-I", source, ROUTER_ADDRESS, check=False)
        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        directions = counters["directions"]["devices"]
        # Every download to the router address is a listed download. Everyone counts the
        # follower's 50 and the device's own counter counts the device's 50.
        self.assertEqual(directions["general-profile"]["download_packets"], 50)
        self.assertEqual(directions["phone-10"]["download_packets"], 50)

    def test_switching_the_device_never_disturbs_the_other_clients(self) -> None:
        everyone = profile("everyone", upload=SLOW)
        self.apply(everyone_only_plan(everyone))

        class Ping:
            """One ping process: one connection, so later replies show the new rules."""

            def __init__(self, source):
                self.times = []
                self.process = subprocess.Popen(
                    ["ip", "netns", "exec", CLIENT, "ping", "-n", "-c", "80", "-i", "0.1",
                     "-I", source, ROUTER_ADDRESS],
                    stdout=subprocess.PIPE, text=True)
                self.reader = threading.Thread(target=self.read, daemon=True)
                self.reader.start()

            def read(self):
                for line in self.process.stdout:
                    match = re.search(r"time=([\d.]+) ms", line)
                    if match:
                        self.times.append(float(match.group(1)))

            def wait_for(self, count, timeout=30):
                deadline = time.monotonic() + timeout
                while len(self.times) < count and time.monotonic() < deadline:
                    time.sleep(0.05)
                return len(self.times)

            def finish(self):
                self.process.wait(timeout=60)
                self.reader.join(10)
                self.process.stdout.close()
                return self.times

        device_ping, follower_ping = Ping(EXCEPTION), Ping(FOLLOWER)
        # Each change waits for packets the device already got, not for a fixed time.
        device_ping.wait_for(12)
        self.apply(mixed_plan(everyone, profile("own")))
        left = len(device_ping.times)
        device_ping.wait_for(left + 30)
        self.apply(everyone_only_plan(everyone))
        back = len(device_ping.times)
        device_times, follower_times = device_ping.finish(), follower_ping.finish()

        self.assertGreaterEqual(len(device_times), 70, device_times)
        self.assertGreater(min(device_times[:8]), 90, device_times)
        # A few packets settle while the new rules replace the old.
        self.assertLess(max(device_times[left + 8:back - 2]), 50, device_times)
        self.assertGreater(min(device_times[back + 8:]), 90, device_times)
        slow = [value for value in follower_times if value > 90]
        self.assertGreaterEqual(len(slow), len(follower_times) - 2, follower_times)
        self.assertGreaterEqual(len(follower_times), 70, follower_times)


if __name__ == "__main__":
    unittest.main()
