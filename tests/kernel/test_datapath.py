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
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceAssignmentRule, ServiceQuotaRule
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.helper import HelperService

from .lab import (
    CLIENT,
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
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft", "ping"))
)
NORMAL = Profile(id="normal", name="Normal")


def device(device_id: str, last_octet: int, profile_id: str) -> Device:
    return Device(device_id, device_id, f"192.168.50.{last_octet}", profile_id)


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class LinuxDatapathTests(unittest.TestCase):
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

    def test_new_settings_keep_the_packets_a_queue_holds(self) -> None:
        def slow(delay_ms):
            return Profile(id="slow", name="Slow", upload=Impairment(delay_ms=delay_ms))

        self.apply([device("a-phone", 10, "slow")], slow(300))
        # About 15 pings wait in the 300 ms queue while the settings change.
        pinging = subprocess.Popen(
            ["ip", "netns", "exec", CLIENT, "ping", "-n", "-c", "100", "-i", "0.02",
             "-W", "2", "-I", "192.168.50.10", "192.168.0.1"],
            stdout=subprocess.PIPE,
            text=True,
        )
        subprocess.run(["sleep", "1"], check=True)
        self.apply([device("a-phone", 10, "slow")], slow(250))
        output, _ = pinging.communicate(timeout=30)

        replies = set(re.findall(r"icmp_seq=(\d+)", output))
        self.assertEqual(len(replies), 100, f"{100 - len(replies)} of 100 pings lost")

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

    def test_frequent_bursty_loss_still_applies(self) -> None:
        frequent = Profile(
            id="frequent", name="Frequent", upload=Impairment(loss_percent=70, loss_burst_packets=2)
        )
        self.apply([device("a-phone", 10, "frequent")], frequent)

        _, replies = self.ping("192.168.50.10", count=1_000, interval="0.002")

        lost = 1_000 - len(set(replies))
        self.assertTrue(600 <= lost <= 800, f"{lost} of 1000 lost")

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

    def test_helper_counts_what_a_browser_service_profile_dropped(self) -> None:
        service = HelperService(self.applier)
        phone = device("a-phone", 10, "normal")
        plan = compile_plan(
            devices=[phone],
            profiles={"normal": NORMAL},
            service_assignment_rules=(
                ServiceAssignmentRule(
                    assignment_id="video",
                    device_id=phone.id,
                    device_ip=phone.ipv4_address,
                    domains=("youtube.com",),
                    mark=0,
                    upload_impairment=Impairment(loss_percent=50),
                    download_impairment=Impairment(),
                ),
            ),
        )
        self.assertTrue(
            service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})["ok"]
        )
        mark = plan.service_assignment_rules[0].mark

        # The browser proxy marks its upstream sockets the same way.
        _in(PI, "python3", "-c", (
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            f"s.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_MARK', 36), {mark})\n"
            "for _ in range(200):\n"
            "    s.sendto(b'x' * 100, ('192.168.0.1', 9))\n"
        ))
        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        dropped = counters["queues"]["devices"]["a-phone"]["upload_dropped"]
        self.assertTrue(60 <= dropped <= 140, f"{dropped} of 200 dropped")

    def test_helper_counts_what_an_active_service_limit_dropped(self) -> None:
        service = HelperService(self.applier)
        phone = device("a-phone", 10, "normal")
        plan = compile_plan(
            devices=[phone],
            profiles={"normal": NORMAL},
            service_quota_rules=(
                ServiceQuotaRule(
                    quota_id="router",
                    device_id=phone.id,
                    device_ip=phone.ipv4_address,
                    destination_addresses=(IPv4Address("192.168.0.1"),),
                    mark=0,
                    upload_impairment=Impairment(loss_percent=50),
                    download_impairment=Impairment(),
                    active=True,
                ),
            ),
        )
        self.assertTrue(
            service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})["ok"]
        )

        self.ping("192.168.50.10", count=200, interval="0.005")
        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        dropped = counters["queues"]["devices"]["a-phone"]["upload_dropped"]
        self.assertTrue(60 <= dropped <= 140, f"{dropped} of 200 dropped")

    def test_a_plan_without_devices_keeps_its_rules(self) -> None:
        self.apply([device("a-phone", 10, "normal")])
        denied = MacPolicy(mode="denylist", denylist=("aa:bb:cc:dd:ee:ff",))

        self.applier.apply(plan=compile_plan(devices=(), profiles={}, mac_policy=denied))

        for interface in ("eth0", "wlan0"):
            self.assertNotIn("prio", _in(PI, "tc", "qdisc", "show", "dev", interface).stdout)
        rules = _in(PI, "nft", "list", "table", "inet", "jittersim").stdout
        self.assertIn("deny listed test client", rules)


if __name__ == "__main__":
    unittest.main()
