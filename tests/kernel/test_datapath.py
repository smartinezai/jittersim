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
import tempfile
import time
import unittest
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, MacPolicy, Profile, Randomness
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import EveryoneElseRule, ServiceAssignmentRule, ServiceQuotaRule
from jittersim.measure import load_run
from jittersim.networking.applier import ApplyError, NetworkApplier
from jittersim.networking.helper import HelperService
from jittersim.networking.renderer import UPLOAD_DEVICE

from .lab import (
    CLIENT,
    CLIENT_MAC,
    OTHER_CLIENT,
    OTHER_CLIENT_ADDRESSES,
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
NORMAL = Profile(id="normal", name="Normal")
# Sends a DHCP-style request from 0.0.0.0, like a client that has no address
# yet. A raw frame is needed because the kernel would pick a real source.
DHCP_REQUEST_SENDER = r"""
import socket, struct, sys

interface, mac = sys.argv[1], bytes.fromhex(sys.argv[2].replace(":", ""))
payload = b"\x01\x01\x06\x00" + bytes(236)
udp = struct.pack("!HHHH", 68, 67, 8 + len(payload), 0) + payload
header = struct.pack(
    "!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, bytes(4), b"\xff" * 4
)
checksum = sum(struct.unpack("!10H", header))
while checksum >> 16:
    checksum = (checksum & 0xFFFF) + (checksum >> 16)
header = header[:10] + struct.pack("!H", ~checksum & 0xFFFF) + header[12:]
with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as sender:
    sender.bind((interface, 0))
    for _ in range(3):
        sender.send(b"\xff" * 6 + mac + b"\x08\x00" + header + udp)
"""


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

    def test_custom_packet_value_ranges_are_installed(self) -> None:
        profile = Profile(
            id="ranges", name="Custom whole percentages",
            upload=Impairment(delay_ms=10, loss_percent=1, reorder_percent=2,
                              duplicate_percent=3, corrupt_percent=6,
                              corrupt_randomness=Randomness(mode="range", minimum=0, maximum=13)),
            download=Impairment(loss_percent=1, loss_burst_packets=4),
        )
        plan = compile_plan(devices=[device("alice", 10, "ranges")],
                            profiles={"ranges": profile})
        response = HelperService(self.applier)._handle_payload(
            {"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(response["ok"], response)
        self.assertTrue(plan.queues)
        # Upload loss stays on the uplink. Corruption runs on the ifb device
        # before the Pi forwards. Uploads never duplicate or reorder.
        upload = _in(PI, "tc", "qdisc", "show", "dev", TOPOLOGY.uplink_interface)
        self.assertEqual(upload.returncode, 0, upload.stderr)
        self.assertRegex(upload.stdout, r"\bloss\s+1%(?:\s|$)")
        for field in ("reorder", "duplicate", "corrupt"):
            self.assertNotIn(field, upload.stdout)
        corruption = _in(PI, "tc", "qdisc", "show", "dev", UPLOAD_DEVICE)
        self.assertEqual(corruption.returncode, 0, corruption.stderr)
        self.assertRegex(corruption.stdout, r"\bcorrupt\s+6%(?:\s|$)")
        download = _in(PI, "tc", "qdisc", "show", "dev", TOPOLOGY.test_interface)
        self.assertEqual(download.returncode, 0, download.stderr)
        self.assertIn("loss gemodel p 0.2525% r 25%", download.stdout)
        for field in ("reorder", "duplicate", "corrupt"):
            self.assertNotIn(field, download.stdout)

    def ping(self, source=None, *, namespace=CLIENT, destination="192.168.0.1", count=3,
             interval="0.2"):
        argv = ["ping", "-n", "-c", str(count), "-i", interval, "-W", "1"]
        if source:
            argv += ["-I", source]
        result = _in(namespace, *argv, destination, check=False)
        replies = [int(seq) for seq in re.findall(r"icmp_seq=(\d+)", result.stdout)]
        return result.returncode == 0, replies

    def apply_everyone_else(self, devices, profile, *, unaffected=(), blocked=False):
        rule = EveryoneElseRule(
            mark=0,
            upload_impairment=profile.upload,
            download_impairment=profile.download,
            unaffected_macs=tuple(unaffected),
            blocked=blocked,
        )
        plan = compile_plan(devices=devices, profiles={NORMAL.id: NORMAL}, everyone_else=rule)
        self.applier.apply(plan=plan)
        return plan

    def round_trip(self, source=None, *, namespace=CLIENT):
        argv = ["ping", "-n", "-c", "4", "-i", "0.2", "-W", "2"]
        if source:
            argv += ["-I", source]
        result = _in(namespace, *argv, ROUTER_ADDRESS, check=False)
        times = [float(value) for value in re.findall(r"time=([\d.]+) ms", result.stdout)]
        self.assertTrue(times, result.stdout + result.stderr)
        return sum(times) / len(times)

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

    def test_measurements_show_what_a_profile_does(self) -> None:
        def measured(profile):
            self.apply([device("a-phone", 10, profile.id)], profile)
            with tempfile.TemporaryDirectory() as directory:
                output = os.path.join(directory, "run.json")
                # The test client measures through its queues, like a real device.
                _in(CLIENT, sys.executable, "-m", "jittersim.measure", "measure",
                    "--label", profile.name, "--target", ROUTER_ADDRESS,
                    "--count", "200", "--interval", "0.01", "--output", output)
                return load_run(output)["summary"]

        normal = measured(NORMAL)
        impaired = measured(
            Profile(
                id="slow",
                name="Slow",
                upload=Impairment(delay_ms=40, loss_percent=20),
                download=Impairment(delay_ms=40),
            )
        )

        added = impaired["rtt_avg_ms"] - normal["rtt_avg_ms"]
        self.assertEqual(normal["loss_percent"], 0.0)
        self.assertTrue(75 <= added <= 95, f"round trips grew by {added:.1f} ms")
        self.assertTrue(10 <= impaired["loss_percent"] <= 30, f"{impaired['loss_percent']}% lost")

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
        # The loss is random so the check allows four standard deviations. Bursts
        # make successive packets alike. With a 25% chance of leaving the lossy
        # state and a correlation of about 0.72 the lost fraction over 3000
        # packets varies by about 1.4 points, six times the variance of
        # independent loss. A fixed 4 point window was 2.9 deviations wide and
        # failed one run in about 250 (421 of 3000 lost).
        loss, burst = 0.10, 4
        leave = 1 / burst
        correlation = 1 - leave - loss * leave / (1 - loss)
        deviation = (
            loss * (1 - loss) / count * (1 + correlation) / (1 - correlation)
        ) ** 0.5
        self.assertLessEqual(
            abs(lost / count - loss), 4 * deviation, f"{lost} of {count} lost"
        )
        # Bad runs are geometric with mean 4 and variance burst * (burst - 1).
        run_deviation = (burst * (burst - 1)) ** 0.5 / len(runs) ** 0.5
        self.assertLessEqual(
            abs(lost / len(runs) - burst), 4 * run_deviation,
            f"mean run {lost / len(runs):.2f} over {len(runs)} runs",
        )

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
            # The second command of the plan, after two root queue checks and
            # the checks for a Wi-Fi intake and an ifb device.
            executor=NamespaceExecutor(PI, fail_on_index=5), topology=TOPOLOGY
        )

        with self.assertRaisesRegex(ApplyError, "cleanup completed"):
            failing.apply(plan=plan)

        self.assertNotEqual(_in(PI, "nft", "list", "table", "inet", "jittersim",
                                check=False).returncode, 0)
        for interface in ("eth0", "wlan0"):
            self.assertNotIn("10:", _in(PI, "tc", "qdisc", "show", "dev", interface).stdout)
        self.assertNotEqual(_in(PI, "ip", "link", "show", UPLOAD_DEVICE, check=False).returncode, 0)

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

    def test_helper_counts_what_everyone_else_sent_and_dropped(self) -> None:
        service = HelperService(self.applier)
        rule = EveryoneElseRule(
            mark=0,
            upload_impairment=Impairment(loss_percent=50),
            download_impairment=Impairment(),
        )
        plan = compile_plan(
            devices=[device("a-phone", 11, "normal")],
            profiles={NORMAL.id: NORMAL},
            everyone_else=rule,
        )
        applied = service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        self.assertTrue(applied["ok"])

        self.ping("192.168.50.10", count=200, interval="0.005")
        counters = service.handle_payload({"action": "traffic-counters"})["counters"]

        self.assertEqual(counters["everyone_else"]["upload_packets"], 200)
        dropped = counters["queues"]["everyone_else"]["upload_dropped"]
        self.assertTrue(60 <= dropped <= 140, f"{dropped} of 200 dropped")
        # The saved device's own queue counts none of everyone else's drops.
        self.assertEqual(set(counters["queues"]["devices"]), {"a-phone"})
        self.assertEqual(counters["queues"]["devices"]["a-phone"]["upload_dropped"], 0)

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

    def test_disabled_mac_filtering_lets_a_block_listed_client_through(self) -> None:
        for mode, reachable in (("disabled", True), ("denylist", False)):
            with self.subTest(mode=mode):
                policy = MacPolicy(mode=mode, denylist=(CLIENT_MAC,))
                self.applier.apply(plan=compile_plan(devices=(), profiles={}, mac_policy=policy))

                self.assertEqual(self.ping("192.168.50.12")[0], reachable)


    def test_everyone_else_gets_its_profile_except_saved_and_unaffected_clients(self) -> None:
        self.apply_everyone_else(
            [device("a-phone", 11, "normal")],
            Profile(id="slow", name="Slow", upload=Impairment(delay_ms=100)),
            unaffected=(OTHER_CLIENT_MAC,),
        )

        self.assertGreater(self.round_trip("192.168.50.10"), 90, "everyone else")
        self.assertLess(self.round_trip("192.168.50.11"), 50, "a saved device")
        self.assertLess(self.round_trip(namespace=OTHER_CLIENT), 50, "an unaffected client")

    def test_an_unaffected_client_keeps_a_normal_download_on_any_address(self) -> None:
        # Only downloads are delayed, so this checks the connection mark.
        self.apply_everyone_else(
            [],
            Profile(id="slow", name="Slow", download=Impairment(delay_ms=100)),
            unaffected=(OTHER_CLIENT_MAC,),
        )

        for address in OTHER_CLIENT_ADDRESSES:
            with self.subTest(address=address):
                self.assertLess(self.round_trip(address, namespace=OTHER_CLIENT), 50)
        self.assertGreater(self.round_trip("192.168.50.10"), 90)

    def test_an_open_connection_follows_the_list(self) -> None:
        both_ways = Impairment(delay_ms=50)
        profile = Profile(id="slow", name="Slow", upload=both_ways, download=both_ways)
        self.apply_everyone_else([], profile, unaffected=(OTHER_CLIENT_MAC, CLIENT_MAC))
        # One ping process keeps one connection, so its later replies show the new list.
        ping = subprocess.Popen(
            ["ip", "netns", "exec", OTHER_CLIENT, "ping", "-n", "-c", "30", "-i", "0.1",
             ROUTER_ADDRESS],
            stdout=subprocess.PIPE,
            text=True,
        )
        time.sleep(1)
        self.apply_everyone_else([], profile, unaffected=(CLIENT_MAC,))
        output = ping.communicate(timeout=30)[0]

        times = [float(value) for value in re.findall(r"time=([\d.]+) ms", output)]
        self.assertLess(max(times[:5]), 50, times)
        self.assertGreater(min(times[-5:]), 90, times)

    def test_an_outage_disconnects_only_everyone_else(self) -> None:
        self.apply_everyone_else(
            [device("a-phone", 11, "normal")], NORMAL, unaffected=(OTHER_CLIENT_MAC,), blocked=True
        )

        # Through the Pi, and to the Pi itself.
        for destination in (ROUTER_ADDRESS, "192.168.50.1"):
            with self.subTest(destination=destination):
                self.assertFalse(self.ping("192.168.50.10", destination=destination)[0])
                self.assertTrue(self.ping("192.168.50.11", destination=destination)[0])
                self.assertTrue(self.ping(namespace=OTHER_CLIENT, destination=destination)[0])

    def test_an_everyone_else_outage_still_lets_clients_ask_for_an_address(self) -> None:
        self.apply_everyone_else([device("a-phone", 11, "normal")], NORMAL, blocked=True)
        # Count DHCP requests that get past JitterSim's input chain.
        _in(PI, "nft", "add", "table", "inet", "probe")
        self.addCleanup(_in, PI, "nft", "delete", "table", "inet", "probe", check=False)
        _in(PI, "nft", "add", "chain", "inet", "probe", "input",
            "{ type filter hook input priority 10; }")
        _in(PI, "nft", "add", "rule", "inet", "probe", "input", "udp", "dport", "67", "counter")

        _in(CLIENT, sys.executable, "-c", DHCP_REQUEST_SENDER, "c0", CLIENT_MAC)

        rules = _in(PI, "nft", "list", "table", "inet", "probe").stdout
        self.assertEqual(re.search(r"counter packets (\d+)", rules).group(1), "3", rules)

if __name__ == "__main__":
    unittest.main()
