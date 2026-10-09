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
import re
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
from jittersim.networking.renderer import UPLOAD_DEVICE

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
UDP_PORT = 9999
TCP_PORT = 5001
UDP_DATAGRAMS = 300
# Sends numbered UDP datagrams and one TCP stream to the router, optionally from
# a socket with a JitterSim mark like the browser proxy's upstream connections.
SENDER = r"""
import socket, struct, sys, time
router, mark = sys.argv[1], int(sys.argv[2])
def marked(kind):
    sock = socket.socket(socket.AF_INET, kind)
    if mark:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
    return sock
with marked(socket.SOCK_DGRAM) as udp:
    for number in range(int(sys.argv[3])):
        udp.sendto(b"JSU" + struct.pack("!I", number) + bytes(40), (router, %d))
        time.sleep(0.005)
try:
    with marked(socket.SOCK_STREAM) as tcp:
        tcp.settimeout(5)
        tcp.connect((router, %d))
        tcp.sendall(bytes(64 * 1024))
except OSError:
    pass
""" % (UDP_PORT, TCP_PORT)
SINK = r"""
import socket, sys, threading
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", int(sys.argv[1])))
server.listen()
server.settimeout(15)
try:
    connection, _ = server.accept()
    connection.settimeout(10)
    while connection.recv(65536):
        pass
except OSError:
    pass
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
        if packet[9] == 17:
            datagram = packet[header_length + 8:]
            (port,) = struct.unpack("!H", packet[header_length + 2:header_length + 4])
            if port == UDP_PORT and datagram[:3] == b"JSU" and len(datagram) >= 7:
                echoes["udp"].append((0, struct.unpack("!I", datagram[3:7])[0]))
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

    def capture_while_pinging(self, *, source_namespace=CLIENT, source=None, sender_mark=None):
        sink = subprocess.Popen(
            ["ip", "netns", "exec", ROUTER, sys.executable, "-c", SINK, str(TCP_PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(sink.kill)
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
            # UDP and TCP from the client, or from a marked Pi socket like the proxy's.
            sending = subprocess.Popen(
                ["ip", "netns", "exec", PI if sender_mark else source_namespace,
                 sys.executable, "-c", SENDER, ROUTER_ADDRESS, str(sender_mark or 0),
                 str(UDP_DATAGRAMS)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _in(PI, "ping", "-n", "-q", "-c", str(PI_PINGS), "-i", "0.02", "-W", "1",
                "-s", "64", "-p", f"{PI_PATTERN:02x}", "-I", "eth0", ROUTER_ADDRESS, check=False)
            pinging.wait(timeout=30)
            sending.wait(timeout=30)
            output, _ = capture.communicate(timeout=30)
        finally:
            if capture.poll() is None:
                capture.kill()
        frames = [bytes.fromhex(item) for item in json.loads(output)]
        self.assertTrue(frames, "the router received nothing")
        return inspect_frames(frames, pi_mac=self.pi_mac, router_mac=self.router_mac)

    def assert_router_sees_only_clean_frames(self, plan, *, service_addresses=None,
                                             source_namespace=CLIENT, source="192.168.50.10",
                                             corruption=True, sender_mark=None):
        report = self.applier.apply(plan=plan, service_addresses=service_addresses)
        self.assertEqual(report.upload_corruption_applied, corruption)
        installed = _in(PI, "tc", "qdisc", "show", "dev", "eth0").stdout
        for setting in ("corrupt [1-9]", "duplicate", "reorder"):
            self.assertNotRegex(installed, setting, "a damaging setting is on the router side")
        problems, echoes = self.capture_while_pinging(source_namespace=source_namespace,
                                                      source=source, sender_mark=sender_mark)
        ifb = _in(PI, "tc", "-s", "qdisc", "show", "dev", UPLOAD_DEVICE, check=False)
        if corruption:
            sent = [int(value) for value in re.findall(r"Sent \d+ bytes (\d+) pkt", ifb.stdout)]
            self.assertTrue(sent and max(sent) > 0, "nothing passed the ifb device")
        else:
            self.assertNotEqual(ifb.returncode, 0, "the ifb device exists without corruption")
        self.assertEqual(problems, [], f"{len(problems)} bad frames reached the router")
        self.assertTrue(echoes["udp"], "no UDP reached the router")
        self.assertLess(len(echoes["udp"]), UDP_DATAGRAMS, "the UDP profile was not applied")
        for pattern in (CLIENT_PATTERN, PI_PATTERN, "udp"):
            requests = echoes[pattern]
            copies = [key for key, count in Counter(requests).items() if count > 1]
            self.assertEqual(copies, [], f"{pattern}: copies reached the router")
            sequences = [sequence for _, sequence in requests]
            self.assertEqual(sequences, sorted(sequences),
                             f"{pattern}: packets reached the router out of order")
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
        self.assert_router_sees_only_clean_frames(plan, corruption=False)
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
        # The UDP and TCP traffic comes from Pi sockets with the assignment's
        # mark, as the browser proxy's connections to websites do.
        self.assert_router_sees_only_clean_frames(
            plan, sender_mark=plan.service_assignment_rules[0].mark)

    def test_an_upgrade_drops_frames_an_older_release_already_damaged(self) -> None:
        # An older release queued corrupted frames on the uplink. Changing that
        # queue in place would still release them to the router.
        self.applier._upload_device_usable = False
        # Without duplication the band keeps its plain netem, which a newer
        # release would otherwise change in place.
        corrupting = Profile(id="corrupting", name="Corrupting",
                             upload=Impairment(delay_ms=5, corrupt_percent=30))
        plan = compile_plan(devices=[Device("a", "a", "192.168.50.10", "corrupting")],
                            profiles={"corrupting": corrupting})
        self.applier.apply(plan=plan)
        _in(PI, "tc", "qdisc", "replace", "dev", "eth0", "parent", "10:2", "handle", "41:",
            "netem", "delay", "4s", "corrupt", "50%", "limit", "10000")
        _in(CLIENT, "ping", "-n", "-q", "-c", "100", "-i", "0.01", "-W", "1", "-s", "64",
            "-p", f"{CLIENT_PATTERN:02x}", "-I", "192.168.50.10", ROUTER_ADDRESS, check=False)
        capture = subprocess.Popen(
            ["ip", "netns", "exec", ROUTER, sys.executable, "-c", CAPTURE, "up0", "6"],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(capture.stdout.readline().strip(), "ready")
            upgraded = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)
            self.assertTrue(upgraded.apply(plan=plan).upload_corruption_applied)
            output, _ = capture.communicate(timeout=30)
        finally:
            if capture.poll() is None:
                capture.kill()
        frames = [bytes.fromhex(item) for item in json.loads(output)]
        problems, echoes = inspect_frames(frames, pi_mac=self.pi_mac, router_mac=self.router_mac)
        self.assertEqual(problems, [])
        self.assertEqual(echoes[CLIENT_PATTERN], [], "queued frames of the older release left")


if __name__ == "__main__":
    unittest.main()
