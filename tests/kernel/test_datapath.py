"""Run generated tc and nftables commands on a real Linux kernel.

These tests need root on Linux and JITTERSIM_KERNEL_TESTS=1. They build a
router, a Pi and a test client in network namespaces they create and delete,
so they never touch the host's own interfaces. On a Mac, run
tests/kernel/run-in-docker.sh.
"""

import os
import re
import shutil
import subprocess
import sys
import unittest
from dataclasses import replace

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.executor import CommandExecutionError, SubprocessExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology

ENABLED = (
    sys.platform.startswith("linux")
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and os.geteuid() == 0
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft", "ping"))
)
PI, ROUTER, CLIENT = (f"jt{os.getpid()}-{role}" for role in ("pi", "router", "client"))
TOPOLOGY = Topology(uplink_interface="eth0", test_interface="wlan0")
NORMAL = Profile(id="normal", name="Normal")


def _run(*argv: str, check: bool = True, stdin: str = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, check=check)


def _in(namespace: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run("ip", "netns", "exec", namespace, *argv, check=check)


class NamespaceExecutor(SubprocessExecutor):
    """Run every command inside the simulated Pi's network namespace."""

    def __init__(self, namespace: str, *, fail_on_index: int = None) -> None:
        super().__init__()
        self.namespace = namespace
        self.fail_on_index = fail_on_index
        self.count = 0

    def run(self, command):
        index = self.count
        self.count += 1
        if index == self.fail_on_index:
            raise CommandExecutionError(f"simulated failure: {command.purpose}")
        wrapped = replace(
            command,
            executable="ip",
            arguments=("netns", "exec", self.namespace, command.executable, *command.arguments),
        )
        return super().run(wrapped)


def device(device_id: str, last_octet: int, profile_id: str) -> Device:
    return Device(device_id, device_id, f"192.168.50.{last_octet}", profile_id)


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class LinuxDatapathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for namespace in (PI, ROUTER, CLIENT):
            _run("ip", "netns", "add", namespace)
        _run("ip", "-n", PI, "link", "add", "pi-up", "type", "veth", "peer", "name", "up0",
             "netns", ROUTER)
        _run("ip", "-n", PI, "link", "set", "pi-up", "name", "eth0")
        _run("ip", "-n", PI, "link", "add", "pi-wl", "type", "veth", "peer", "name", "c0",
             "netns", CLIENT)
        _run("ip", "-n", PI, "link", "set", "pi-wl", "name", "wlan0")
        _run("ip", "-n", ROUTER, "addr", "add", "192.168.0.1/24", "dev", "up0")
        _run("ip", "-n", PI, "addr", "add", "192.168.0.2/24", "dev", "eth0")
        _run("ip", "-n", PI, "addr", "add", "192.168.50.1/24", "dev", "wlan0")
        for octet in [10, 11, 12] + list(range(100, 115)):
            _run("ip", "-n", CLIENT, "addr", "add", f"192.168.50.{octet}/24", "dev", "c0")
        for namespace, interface in (
            (ROUTER, "up0"), (ROUTER, "lo"), (PI, "eth0"), (PI, "wlan0"), (PI, "lo"),
            (CLIENT, "c0"), (CLIENT, "lo"),
        ):
            _run("ip", "-n", namespace, "link", "set", interface, "up")
        _run("ip", "-n", CLIENT, "route", "add", "default", "via", "192.168.50.1")
        _in(PI, "sh", "-c", "echo 1 > /proc/sys/net/ipv4/ip_forward")
        # Like NetworkManager's shared mode on the Pi.
        _in(PI, "nft", "add", "table", "ip", "nat")
        _in(PI, "nft", "add", "chain", "ip", "nat", "post",
            "{ type nat hook postrouting priority 100; }")
        _in(PI, "nft", "add", "rule", "ip", "nat", "post", "oifname", "eth0", "masquerade")

    @classmethod
    def tearDownClass(cls) -> None:
        for namespace in (PI, ROUTER, CLIENT):
            _run("ip", "netns", "del", namespace, check=False)

    def setUp(self) -> None:
        self.applier = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)

    def tearDown(self) -> None:
        self.applier.reset()

    def apply(self, devices, *profiles):
        plan = compile_plan(devices=devices, profiles={p.id: p for p in (NORMAL,) + profiles})
        self.applier.apply(plan=plan)
        return plan

    def ping(self, source=None, *, namespace=CLIENT, destination="192.168.0.1", count=3,
             interval="0.2"):
        argv = ["ping", "-n", "-c", str(count), "-i", interval, "-W", "1"]
        if source:
            argv += ["-I", source]
        result = _in(namespace, *argv, destination, check=False)
        replies = [int(seq) for seq in re.findall(r"icmp_seq=(\d+)", result.stdout)]
        return result.returncode == 0, replies

    def netem_lines(self, interface="eth0"):
        output = _in(PI, "tc", "qdisc", "show", "dev", interface).stdout
        return [line for line in output.splitlines() if "netem" in line]

    def test_unmarked_traffic_and_other_devices_are_never_impaired(self) -> None:
        blocked = Impairment(loss_percent=100)
        self.apply(
            [device("a-phone", 10, "blocked"), device("b-phone", 11, "normal")],
            Profile(id="blocked", name="Blocked", upload=blocked, download=blocked),
        )

        self.assertFalse(self.ping("192.168.50.10")[0])
        self.assertTrue(self.ping("192.168.50.11")[0])
        self.assertTrue(self.ping("192.168.50.12")[0], "unassigned client")
        self.assertTrue(self.ping(namespace=PI)[0], "the Pi's own traffic on eth0")
        self.assertTrue(
            self.ping(namespace=PI, destination="192.168.50.11")[0],
            "the Pi's own traffic on wlan0",
        )

    def test_reapplying_the_same_plan_leaves_the_same_state(self) -> None:
        slow = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=50, jitter_ms=10))
        devices = [device("a-phone", 10, "slow"), device("b-phone", 11, "normal")]

        def state():
            outputs = [
                _in(PI, "tc", "qdisc", "show", "dev", interface).stdout
                + _in(PI, "tc", "filter", "show", "dev", interface).stdout
                for interface in ("eth0", "wlan0")
            ]
            rules = _in(PI, "nft", "list", "table", "inet", "jittersim").stdout
            return outputs, re.sub(r"packets \d+ bytes \d+", "", rules)

        self.apply(devices, slow)
        first = state()
        self.apply(devices, slow)

        self.assertEqual(state(), first)

    def test_settings_a_profile_drops_do_not_linger(self) -> None:
        limited = Impairment(delay_ms=20, corrupt_percent=5, bandwidth_kbit=64)
        self.apply([device("a-phone", 10, "p")], Profile(id="p", name="P", upload=limited))
        self.apply(
            [device("a-phone", 10, "p")],
            Profile(id="p", name="P", upload=Impairment(delay_ms=20)),
        )

        (line,) = self.netem_lines()
        self.assertNotIn("rate", line)
        self.assertNotIn("corrupt", line)

    def test_fifteen_devices_each_get_their_own_queue(self) -> None:
        blocked = Profile(id="blocked", name="Blocked", upload=Impairment(loss_percent=100))
        slow = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=10))
        self.apply(
            [device(f"dev-{i:02d}", 100 + i, "blocked" if i == 11 else "slow") for i in range(15)],
            blocked,
            slow,
        )

        self.assertFalse(self.ping("192.168.50.111")[0], "mark 12 uses its own queue")
        self.assertTrue(self.ping("192.168.50.110")[0])
        self.assertTrue(self.ping("192.168.50.114")[0])

    def test_bursty_loss_keeps_the_configured_average(self) -> None:
        bursty = Profile(
            id="bursty", name="Bursty", upload=Impairment(loss_percent=10, loss_burst_packets=4)
        )
        self.apply([device("a-phone", 10, "bursty")], bursty)
        count = 3_000

        _, replies = self.ping("192.168.50.10", count=count, interval="0.002")

        received = set(replies)
        runs, current = [], 0
        for seq in range(1, count + 1):
            if seq in received:
                if current:
                    runs.append(current)
                current = 0
            else:
                current += 1
        if current:
            runs.append(current)
        lost = sum(runs)
        self.assertTrue(0.06 <= lost / count <= 0.14, f"{lost} of {count} lost")
        self.assertTrue(3 <= lost / len(runs) <= 5.5, f"mean run {lost / len(runs):.2f}")

    def test_jitter_keeps_packets_in_order(self) -> None:
        jitter = Profile(id="jitter", name="Jitter", upload=Impairment(delay_ms=100, jitter_ms=50))
        self.apply([device("a-phone", 10, "jitter")], jitter)

        _, replies = self.ping("192.168.50.10", count=300, interval="0.005")

        self.assertGreater(len(replies), 250)
        self.assertEqual(replies, sorted(replies))

    def test_a_failed_apply_leaves_no_project_state(self) -> None:
        slow = Profile(id="slow", name="Slow", upload=Impairment(delay_ms=50))
        plan = compile_plan(
            devices=[device("a-phone", 10, "slow")], profiles={"slow": slow}
        )
        failing = NetworkApplier(
            executor=NamespaceExecutor(PI, fail_on_index=3), topology=TOPOLOGY
        )

        with self.assertRaisesRegex(ApplyError, "cleanup completed"):
            failing.apply(plan=plan)

        self.assertNotEqual(_in(PI, "nft", "list", "table", "inet", "jittersim",
                                check=False).returncode, 0)
        for interface in ("eth0", "wlan0"):
            self.assertNotIn("10:", _in(PI, "tc", "qdisc", "show", "dev", interface).stdout)

    def test_helper_reports_what_each_queue_dropped(self) -> None:
        service = HelperService(self.applier)
        lossy = Profile(id="lossy", name="Lossy", upload=Impairment(loss_percent=50))
        plan = compile_plan(devices=[device("a-phone", 10, "lossy")], profiles={"lossy": lossy})
        applied = service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(applied["ok"])

        self.ping("192.168.50.10", count=200, interval="0.005")
        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        dropped = counters["queues"]["devices"]["a-phone"]["upload_dropped"]
        self.assertTrue(60 <= dropped <= 140, f"{dropped} of 200 dropped")
        self.assertEqual(counters["directions"]["devices"]["a-phone"]["upload_packets"], 200)


if __name__ == "__main__":
    unittest.main()
