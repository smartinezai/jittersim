"""Check by capture what the household router receives from the Pi.

The router must only ever see well-formed frames from the Pi: the Pi's own
Ethernet address, valid IPv4 and ICMP checksums, each packet once and in the
order it was sent. The Pi's own traffic must arrive complete. Every profile
type is applied with the harshest packet settings while a raw socket in the
router's namespace records what arrives on its port.

These tests need root on Linux and JITTERSIM_KERNEL_TESTS=1. On a Mac, run
tests/kernel/run-in-docker.sh.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
from collections import Counter, defaultdict
from ipaddress import IPv4Address

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import (
    EveryoneElseRule,
    GeneralProfileRule,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
)
from jittersim.networking.applier import NetworkApplier

from .lab import (
    CLIENT,
    OTHER_CLIENT,
    PI,
    ROUTER,
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
PI_UPLINK_ADDRESS = "192.168.0.2"
# Every packet setting at a level that makes damage, copies and reordering
# certain within a few hundred packets.
HARSH = Impairment(
    delay_ms=5, jitter_ms=2, loss_percent=5,
    reorder_percent=30, duplicate_percent=30, corrupt_percent=30,
)
# Downloads stay plain: damaged echo replies upset the test's own ping. Only
# uploads travel toward the router.
HARSH_PROFILE = Profile(id="harsh", name="Harsh", upload=HARSH)
PLAIN = Impairment()
NORMAL = Profile(id="normal", name="Normal")
PROFILES = {profile.id: profile for profile in (HARSH_PROFILE, NORMAL)}
CLIENT_PATTERN = 0xAA
PI_PATTERN = 0xBB
CLIENT_PINGS = 300
PI_PINGS = 60
# Records every frame that arrives on the router's port until the deadline.
CAPTURE = r"""
import json, select, socket, sys, time
receiver = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
receiver.bind((sys.argv[1], 0))
receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
print("ready", flush=True)
deadline = time.monotonic() + float(sys.argv[2])
frames = []
while True:
    left = deadline - time.monotonic()
    if left <= 0:
        break
    if not select.select([receiver], [], [], left)[0]:
        continue
    data, address = receiver.recvfrom(65535)
    if address[2] != socket.PACKET_OUTGOING:
        frames.append(data.hex())
print(json.dumps(frames), flush=True)
"""


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def _mac(raw: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in raw)


def inspect_frames(frames, *, pi_mac: str, router_mac: str):
    """Return the problems found and the echo requests seen for each pattern."""

    problems = []
    echoes = defaultdict(list)
    for frame in frames:
        destination, source = _mac(frame[0:6]), _mac(frame[6:12])
        (ethertype,) = struct.unpack("!H", frame[12:14])
        if source != pi_mac:
            problems.append(f"source MAC {source} is not the Pi's")
            continue
        if destination not in (router_mac, "ff:ff:ff:ff:ff:ff") and not (
            ethertype == 0x86DD and destination.startswith("33:33:")
        ):
            problems.append(f"destination MAC {destination} is not the router")
            continue
        if ethertype == 0x0806:
            if len(frame) < 42 or _mac(frame[22:28]) != pi_mac:
                problems.append("ARP sender is not the Pi")
            continue
        if ethertype == 0x86DD:
            continue
        if ethertype != 0x0800:
            problems.append(f"unknown EtherType {ethertype:#06x}")
            continue
        header_length = (frame[14] & 0x0F) * 4
        header = frame[14:14 + header_length]
        if frame[14] >> 4 != 4 or header_length < 20 or _checksum(header):
            problems.append("IPv4 header is damaged")
            continue
        (total_length,) = struct.unpack("!H", frame[16:18])
        packet = frame[14:14 + total_length]
        if len(packet) != total_length:
            problems.append("IPv4 length does not match the frame")
            continue
        if str(IPv4Address(packet[12:16])) != PI_UPLINK_ADDRESS:
            problems.append(f"IPv4 source {IPv4Address(packet[12:16])} is not the Pi's")
            continue
        if packet[9] != 1:
            continue
        message = packet[header_length:]
        if _checksum(message):
            problems.append("ICMP message is damaged")
            continue
        if message[0] != 8 or len(message) < 8 + 17:
            continue
        identifier, sequence = struct.unpack("!HH", message[4:8])
        filler = set(message[8 + 16:])
        if len(filler) == 1:
            echoes[filler.pop()].append((identifier, sequence))
    return problems, echoes


@unittest.skipUnless(ENABLED, "needs root on Linux with JITTERSIM_KERNEL_TESTS=1")
class UpstreamSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        create_lab()
        cls.pi_mac = _in(PI, "cat", "/sys/class/net/eth0/address").stdout.strip()
        cls.router_mac = _in(ROUTER, "cat", "/sys/class/net/up0/address").stdout.strip()

    @classmethod
    def tearDownClass(cls) -> None:
        remove_lab()

    def setUp(self) -> None:
        self.applier = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)

    def tearDown(self) -> None:
        self.applier.reset()

    def capture_while_pinging(self, *, source_namespace=CLIENT, source=None):
        capture = subprocess.Popen(
            ["ip", "netns", "exec", ROUTER, sys.executable, "-c", CAPTURE, "up0", "6"],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(capture.stdout.readline().strip(), "ready")
            client = ["ping", "-n", "-q", "-c", str(CLIENT_PINGS), "-i", "0.005", "-W", "1",
                      "-s", "64", "-p", f"{CLIENT_PATTERN:02x}"]
            if source:
                client += ["-I", source]
            pinging = subprocess.Popen(
                ["ip", "netns", "exec", source_namespace, *client, ROUTER_ADDRESS],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _in(PI, "ping", "-n", "-q", "-c", str(PI_PINGS), "-i", "0.02", "-W", "1",
                "-s", "64", "-p", f"{PI_PATTERN:02x}", "-I", "eth0", ROUTER_ADDRESS, check=False)
            pinging.wait(timeout=30)
            output, _ = capture.communicate(timeout=30)
        finally:
            if capture.poll() is None:
                capture.kill()
        frames = [bytes.fromhex(item) for item in json.loads(output)]
        self.assertTrue(frames, "the router received nothing")
        return inspect_frames(frames, pi_mac=self.pi_mac, router_mac=self.router_mac)

    def assert_router_sees_only_clean_frames(self, plan, *, service_addresses=None,
                                             source_namespace=CLIENT, source="192.168.50.10"):
        self.applier.apply(plan=plan, service_addresses=service_addresses)
        installed = _in(PI, "tc", "qdisc", "show", "dev", "eth0").stdout
        for setting in ("corrupt [1-9]", "duplicate", "reorder"):
            self.assertNotRegex(installed, setting, "a damaging setting is on the router side")
        problems, echoes = self.capture_while_pinging(source_namespace=source_namespace,
                                                      source=source)
        self.assertEqual(problems, [], f"{len(problems)} bad frames reached the router")
        for pattern in (CLIENT_PATTERN, PI_PATTERN):
            requests = echoes[pattern]
            copies = [key for key, count in Counter(requests).items() if count > 1]
            self.assertEqual(copies, [], f"pattern {pattern:#x}: copies reached the router")
            sequences = [sequence for _, sequence in requests]
            self.assertEqual(sequences, sorted(sequences),
                             f"pattern {pattern:#x}: packets reached the router out of order")
        self.assertEqual(len(echoes[PI_PATTERN]), PI_PINGS, "the Pi's own pings were touched")
        # The profile still works: loss (and on the ifb path damaged headers the
        # Pi dropped) remove some client pings, but most arrive.
        self.assertGreater(len(echoes[CLIENT_PATTERN]), CLIENT_PINGS // 3)
        self.assertLess(len(echoes[CLIENT_PATTERN]), CLIENT_PINGS, "the profile was not applied")

    def test_a_device_profile(self) -> None:
        plan = compile_plan(devices=[Device("a", "a", "192.168.50.10", "harsh")],
                            profiles=PROFILES)
        self.assert_router_sees_only_clean_frames(plan)

    def test_a_device_profile_without_the_ifb_device(self) -> None:
        # A kernel without ifb or its tc actions: uploads run on the uplink
        # without corruption, duplication or reordering.
        self.applier._upload_device_usable = False
        plan = compile_plan(devices=[Device("a", "a", "192.168.50.10", "harsh")],
                            profiles=PROFILES)
        self.assert_router_sees_only_clean_frames(plan)
        self.assertIn("netem", _in(PI, "tc", "qdisc", "show", "dev", "eth0").stdout)

    def test_the_profile_for_everyone(self) -> None:
        plan = compile_plan(devices=[], profiles=PROFILES,
                            general_profile=GeneralProfileRule("harsh"))
        self.assert_router_sees_only_clean_frames(plan)

    def test_the_profile_for_everyone_else(self) -> None:
        rule = EveryoneElseRule(mark=0, upload_impairment=HARSH, download_impairment=PLAIN)
        plan = compile_plan(devices=[Device("a", "a", "192.168.50.10", "normal")],
                            profiles=PROFILES, everyone_else=rule)
        self.assert_router_sees_only_clean_frames(
            plan, source_namespace=OTHER_CLIENT, source="192.168.50.20")

    def test_an_active_service_limit(self) -> None:
        quota = ServiceQuotaRule(
            quota_id="router", device_id="a", device_ip=IPv4Address("192.168.50.10"),
            destination_addresses=(IPv4Address(ROUTER_ADDRESS),), mark=0,
            upload_impairment=HARSH, download_impairment=PLAIN, active=True,
        )
        plan = compile_plan(devices=[Device("a", "a", "192.168.50.10", "normal")],
                            profiles=PROFILES, service_quota_rules=[quota])
        self.assert_router_sees_only_clean_frames(plan)

    def test_a_service_list(self) -> None:
        listed = ServiceListRule("router", ("router.example",), 0, HARSH, PLAIN)
        plan = compile_plan(devices=[], profiles=PROFILES, service_list_rules=[listed])
        self.assert_router_sees_only_clean_frames(
            plan, service_addresses={"router": [IPv4Address(ROUTER_ADDRESS)]})

    def test_a_browser_service_profile(self) -> None:
        assignment = ServiceAssignmentRule(
            assignment_id="video", device_id="a", device_ip=IPv4Address("192.168.50.10"),
            domains=("video.example",), mark=0, upload_impairment=HARSH,
            download_impairment=PLAIN,
        )
        plan = compile_plan(devices=[Device("a", "a", "192.168.50.10", "harsh")],
                            profiles=PROFILES, service_assignment_rules=[assignment])
        self.assert_router_sees_only_clean_frames(plan)


if __name__ == "__main__":
    unittest.main()
