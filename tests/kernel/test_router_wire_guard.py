"""Router-side wire guard: every frame the Pi puts on the router cable, in every mode.

A raw socket in the router's namespace records each frame that arrives from the
Pi while a scenario runs. The test fails on any frame a household router must
never get from the Pi:

- a source MAC that is not the Pi's Ethernet address, or a destination that is
  neither the router, broadcast nor ordinary IPv4/IPv6 multicast
- an EtherType other than IPv4, ARP and IPv6 (LLDP, STP and damaged types)
- a damaged IPv4 header, a wrong IPv4 total length or a damaged ICMP message
- an IPv4 source other than the Pi's uplink address (above all a test Wi-Fi
  192.168.50.x address that skipped NAT), or an IPv6 source that is not one of
  the Pi's own (forwarded IPv6)
- an ARP frame whose sender is not the Pi's uplink address (the Pi answering for
  its Wi-Fi address on the household LAN), or an ARP request for an address
  outside the uplink network
- a DHCP server reply, a DHCPv6 server reply, an IPv6 router advertisement, an
  ICMP or ICMPv6 redirect, an IGMP query
- mDNS, SSDP, LLMNR or NetBIOS (the lab runs no Avahi, so any such frame was
  reflected from the test Wi-Fi), or any other IPv4 broadcast that is not the
  Pi's own DHCP client
- byte-identical copies of a forwarded frame within one second
- more than BURST_LIMIT broadcast or multicast frames, or more than ARP_LIMIT
  ARP frames, in any one second, and more upstream DNS questions than the
  clients asked distinct names (the Pi's resolver must not amplify)

The Pi runs what it runs on the real device as far as namespaces allow:
NetworkManager's shared-mode nftables table and dnsmasq command line with the
installer's dnsmasq-shared.d files, the shipped management firewall
(deploy/raspberry-pi/jittersim-control-firewall.nft) and the kernel settings its
unit applies, and the real NetworkApplier. Every profile type, the shared
profiles, protected devices, Panic and Reset, and a Wi-Fi restart (Recovery and
the router-name rename both restart the access point) run while two test
clients send ordinary traffic, household broadcast chatter and TCP segments
connection tracking cannot follow, and while the router side sends ARP, DHCP,
DHCPv6, router solicitations and a packet that only a router would forward
back. The audit is docs/router-safety-audit-round2-2026-10-17.md.

These tests need root on Linux and JITTERSIM_KERNEL_TESTS=1. On a Mac, run
tests/kernel/run-in-docker.sh.
"""

import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from ipaddress import IPv4Address, IPv4Network, IPv6Address
from pathlib import Path

from jittersim import localname
from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import (
    EveryoneElseRule,
    GeneralProfileRule,
    ServiceListRule,
    ServiceQuotaRule,
)
from jittersim.networking.applier import NetworkApplier

from .lab import (
    CLIENT,
    CLIENT_MAC,
    OTHER_CLIENT,
    OTHER_CLIENT_MAC,
    PI,
    ROUTER,
    ROUTER_ADDRESS,
    TOPOLOGY,
    NamespaceExecutor,
    create_lab,
    remove_lab,
)
from .outage_lab import NM_SHARED_TABLE, nm_dnsmasq_argv
from .outage_lab import run_in as _in

ROOT = Path(__file__).resolve().parents[2]
ENABLED = (
    sys.platform.startswith("linux")
    and os.environ.get("JITTERSIM_KERNEL_TESTS") == "1"
    and os.geteuid() == 0
    and all(shutil.which(tool) for tool in ("ip", "tc", "nft", "ping", "dnsmasq"))
)
PI_UPLINK = "192.168.0.2"
# Stands in for the internet behind the router. Test Wi-Fi clients may not reach
# 192.168.0.0/16 through the Pi (management firewall), so they talk to this.
INTERNET = "198.51.100.1"
TEST_WIFI = IPv4Network("192.168.50.0/24")
UPLINK_NETWORK = IPv4Network("192.168.0.0/24")
CLIENT_ADDRESS = "192.168.50.10"
OTHER_ADDRESS = "192.168.50.20"
# The rates a household router should never see from one wired client.
BURST_LIMIT = 10
ARP_LIMIT = 10
CAPTURE_SECONDS = 12.0
# Names each client asks the Pi's resolver: one name many times, then distinct ones.
REPEATED_QUERIES, DISTINCT_NAMES = 30, 10
PROBE_MAC = "02:00:00:00:77:01"

CAPTURE = r"""
import json, select, socket, sys, time
receiver = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
receiver.bind((sys.argv[1], 0))
receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
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
        frames.append((time.monotonic(), data.hex()))
print(json.dumps(frames), flush=True)
"""

# What a test Wi-Fi client sends: ordinary UDP and TCP, DNS through the Pi,
# the broadcast and multicast chatter of a household device, IPv6 toward the
# Pi's uplink, and TCP segments connection tracking cannot follow (a reset, a
# FIN, a SYN-ACK and a RST-ACK for no known connection). Linux leaves such
# segments without NAT.
TRAFFIC = r"""
import random, socket, struct, sys, time
source, target, resolver, label = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
repeated, distinct = int(sys.argv[5]), int(sys.argv[6])

def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    udp.bind((source, 0))
    for number in range(150):
        udp.sendto(b"JSW" + struct.pack("!I", number) + bytes(40), (target, 9999))
        time.sleep(0.004)
# At most four seconds of TCP, however slow the profile makes it.
deadline = time.monotonic() + 4
try:
    with socket.create_connection((target, 5001), timeout=3, source_address=(source, 0)) as tcp:
        tcp.settimeout(1)
        for _ in range(48):
            if time.monotonic() > deadline:
                break
            tcp.sendall(bytes(1024))
except OSError:
    pass

def question(name, identifier):
    labels = b"".join(bytes([len(part)]) + part.encode() for part in name.split("."))
    return struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0) + labels + b"\0\0\1\0\1"

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
    dns.bind((source, 0))
    dns.settimeout(0.1)
    names = ["cached.example.org"] * repeated + [
        "%s-%d.example.org" % (label, number) for number in range(distinct)]
    for identifier, name in enumerate(names, 1):
        dns.sendto(question(name, identifier), (resolver, 53))
        try:
            dns.recv(512)
        except OSError:
            pass

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as chatter:
    chatter.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # A TTL above one, so only the Pi's lack of a multicast router stops it.
    chatter.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
    chatter.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(source))
    chatter.bind((source, 0))
    for _ in range(3):
        for address, port in (("224.0.0.251", 5353), ("239.255.255.250", 1900),
                              ("224.0.0.252", 5355), ("192.168.50.255", 137),
                              ("192.168.50.255", 138), ("255.255.255.255", 9),
                              ("239.1.2.3", 5000)):
            chatter.sendto(b"jittersim-chatter", (address, port))

raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
raw.bind((source, 0))
for flags in (0x04, 0x11, 0x12, 0x14):
    for port in range(41000, 41004):
        header = struct.pack("!HHIIBBHHH", port, 443, random.getrandbits(32),
                             random.getrandbits(32), 5 << 4, flags, 1024, 0, 0)
        pseudo = (socket.inet_aton(source) + socket.inet_aton(target)
                  + struct.pack("!BBH", 0, 6, len(header)))
        header = header[:16] + struct.pack("!H", checksum(pseudo + header)) + header[18:]
        raw.sendto(header, (target, 0))
raw.close()

try:
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as six:
        six.sendto(b"jittersim-ipv6", ("2001:db8::1", 9999))
except OSError:
    pass
"""

# A TCP sink on the router's internet address for every client connection.
SINK = r"""
import socket, sys, threading, time
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((sys.argv[1], 5001))
server.listen(8)
server.settimeout(1)
def drain(connection):
    connection.settimeout(5)
    try:
        while connection.recv(65536):
            pass
    except OSError:
        pass
while True:
    try:
        connection, _ = server.accept()
    except socket.timeout:
        continue
    threading.Thread(target=drain, args=(connection,), daemon=True).start()
"""

# What the household LAN sends that the Pi must never answer as a server or a
# router: ARP for its Wi-Fi addresses, DHCP discovery and a request for a test
# Wi-Fi lease, an IPv6 router solicitation, a DHCPv6 solicit, NetBIOS and mDNS
# questions, and a packet addressed to the Pi's MAC for another host on the
# same LAN (a router would forward it back and send an ICMP redirect).
PROBES = r"""
import socket, struct, sys, time
interface, router_mac, pi_mac, router_ip, pi_ip, probe_mac, internet = sys.argv[1:8]
mac = lambda text: bytes.fromhex(text.replace(":", ""))
ip4 = socket.inet_aton

def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF

def ipv4(source, destination, protocol, payload, ttl=64):
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, ttl, protocol, 0,
                         ip4(source), ip4(destination))
    return header[:10] + struct.pack("!H", checksum(header)) + header[12:] + payload

def udp4(source, destination, sport, dport, payload):
    return ipv4(source, destination, 17, struct.pack("!HHHH", sport, dport, 8 + len(payload), 0)
                + payload)

def arp_request(target):
    return (b"\xff" * 6 + mac(router_mac) + b"\x08\x06"
            + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + mac(router_mac) + ip4(router_ip)
            + bytes(6) + ip4(target))

def dhcp(message_type, requested=None, xid=0x4A535731):
    options = bytes((53, 1, message_type))
    if requested:
        options += bytes((50, 4)) + ip4(requested)
    options += bytes((55, 3, 1, 3, 6, 255))
    bootp = struct.pack("!BBBBIHH4s4s4s4s16s64s128s4s", 1, 1, 6, 0, xid, 0, 0x8000, bytes(4),
                        bytes(4), bytes(4), bytes(4), mac(probe_mac) + bytes(10), bytes(64),
                        bytes(128), b"\x63\x82\x53\x63") + options
    return (b"\xff" * 6 + mac(probe_mac) + b"\x08\x00"
            + udp4("0.0.0.0", "255.255.255.255", 68, 67, bootp))

link_local = "fe80::%s" % router_mac.replace(":", "")[-4:]
def ipv6(destination, next_header, payload, destination_mac):
    source = socket.inet_pton(socket.AF_INET6, link_local)
    target = socket.inet_pton(socket.AF_INET6, destination)
    header = struct.pack("!IHBB", 6 << 28, len(payload), next_header, 255) + source + target
    pseudo = source + target + struct.pack("!I3xB", len(payload), next_header)
    at = 2 if next_header == 58 else 6
    payload = payload[:at] + struct.pack("!H", checksum(pseudo + payload)) + payload[at + 2:]
    return mac(destination_mac) + mac(router_mac) + b"\x86\xdd" + header + payload

router_solicitation = struct.pack("!BBHI", 133, 0, 0, 0)
solicit = struct.pack("!HHHH", 546, 547, 8 + 4, 0) + bytes((1, 0, 0, 1))
netbios = struct.pack("!HHHHHH", 0x4A53, 0x0110, 1, 0, 0, 0) + b"\x20" + b"CK" + b"A" * 30 + b"\0\0\x20\0\1"
mdns = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + b"\x06netlab\x05local\0\0\1\0\1"
frames = [
    arp_request(pi_ip),
    arp_request("192.168.50.1"),
    arp_request("192.168.50.10"),
    dhcp(1),
    # A household device that held a Pi lease asks for it again on the router LAN.
    # An authoritative server on the wrong side would refuse it (DHCPNAK).
    dhcp(3, requested="192.168.50.150"),
    ipv6("ff02::2", 58, router_solicitation, "33:33:00:00:00:02"),
    ipv6("ff02::1:2", 17, solicit, "33:33:00:01:00:02"),
    b"\xff" * 6 + mac(router_mac) + b"\x08\x00"
    + udp4(router_ip, "192.168.0.255", 137, 137, netbios),
    b"\x01\x00\x5e\x00\x00\xfb" + mac(router_mac) + b"\x08\x00"
    + udp4(router_ip, "224.0.0.251", 5353, 5353, mdns),
    # For a host on the router LAN, sent to the Pi's MAC: only a router forwards it.
    mac(pi_mac) + mac(router_mac) + b"\x08\x00"
    + udp4(router_ip, internet, 40000, 9999, b"jittersim-hairpin"),
]
sender = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
sender.bind((interface, 0))
for _ in range(2):
    for frame in frames:
        sender.send(frame)
    time.sleep(0.3)
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


ICMP6_FORBIDDEN = {134: "IPv6 router advertisement", 137: "ICMPv6 redirect"}
CHATTER_PORTS = {5353: "mDNS", 1900: "SSDP", 5355: "LLMNR", 137: "NetBIOS", 138: "NetBIOS"}


def audit_frames(frames, *, pi_mac, router_mac, pi_ip, pi_ipv6=(), test_wifi=TEST_WIFI,
                 uplink_network=UPLINK_NETWORK, clients_distinct_names=None):
    """Return (problems, stats) for frames the router received from the Pi.

    `frames` is a list of (seconds, bytes). Each problem names what is wrong
    with one frame, so a count of a problem is a count of frames.
    """

    problems = Counter()
    stats = Counter()
    burst_times, arp_times = [], []
    seen = {}
    for moment, frame in frames:
        stats["frames"] += 1
        if len(frame) < 14:
            problems["runt frame"] += 1
            continue
        destination, source = frame[0:6], frame[6:12]
        (ethertype,) = struct.unpack("!H", frame[12:14])
        if _mac(source) != pi_mac:
            problems[f"source MAC {_mac(source)} is not the Pi's"] += 1
            continue
        group = bool(destination[0] & 1)
        if group:
            if destination != b"\xff" * 6 and not destination.startswith(
                    (b"\x01\x00\x5e", b"\x33\x33")):
                problems[f"destination MAC {_mac(destination)} is an odd group address"] += 1
                continue
            burst_times.append(moment)
        elif _mac(destination) != router_mac:
            problems[f"destination MAC {_mac(destination)} is not the router"] += 1
            continue
        if ethertype == 0x0806:
            stats["arp"] += 1
            arp_times.append(moment)
            arp = frame[14:42]
            if len(arp) < 28 or arp[0:6] != b"\x00\x01\x08\x00\x06\x04" or \
                    struct.unpack("!H", arp[6:8])[0] not in (1, 2):
                problems["malformed ARP"] += 1
                continue
            sender_ip = IPv4Address(arp[14:18])
            target_ip = IPv4Address(arp[24:28])
            if arp[8:14] != source:
                problems["ARP sender MAC is not the frame's"] += 1
            elif str(sender_ip) != pi_ip:
                problems[f"ARP from the Pi for {sender_ip}, not its uplink address"] += 1
            elif struct.unpack("!H", arp[6:8])[0] == 1 and target_ip not in uplink_network:
                problems[f"ARP request for {target_ip} outside the uplink network"] += 1
            if struct.unpack("!H", arp[6:8])[0] == 2:
                stats["arp replies for the uplink address"] += str(sender_ip) == pi_ip
            continue
        if ethertype == 0x86DD:
            stats["ipv6"] += 1
            _audit_ipv6(frame[14:], pi_ipv6, problems)
            continue
        if ethertype != 0x0800:
            problems[f"EtherType {ethertype:#06x} on the router LAN"] += 1
            continue
        packet = frame[14:]
        header_length = (packet[0] & 0x0F) * 4 if packet else 0
        if len(packet) < 20 or packet[0] >> 4 != 4 or header_length < 20 or \
                _checksum(packet[:header_length]):
            problems["damaged IPv4 header"] += 1
            continue
        (total_length,) = struct.unpack("!H", packet[2:4])
        if total_length > len(packet) or total_length < header_length:
            problems["IPv4 length does not match the frame"] += 1
            continue
        packet = packet[:total_length]
        protocol = packet[9]
        source_ip, destination_ip = IPv4Address(packet[12:16]), IPv4Address(packet[16:20])
        fragment = struct.unpack("!H", packet[6:8])[0] & 0x1FFF
        body = packet[header_length:]
        ports = struct.unpack("!HH", body[:4]) if protocol in (6, 17) and len(body) >= 4 \
            and not fragment else (None, None)
        dhcp_client = protocol == 17 and ports == (68, 67)
        if str(source_ip) != pi_ip and not (dhcp_client and str(source_ip) == "0.0.0.0"):
            if source_ip in test_wifi:
                problems[f"test Wi-Fi source {source_ip} skipped NAT"] += 1
            else:
                problems[f"IPv4 source {source_ip} is not the Pi's (forwarded for someone else)"] += 1
            continue
        if protocol == 17 and (ports[0] == 67 or ports[1] == 68):
            problems["DHCP server reply on the router LAN"] += 1
            continue
        if protocol == 17 and (ports[1] in CHATTER_PORTS or ports[0] in CHATTER_PORTS):
            problems[f"{CHATTER_PORTS.get(ports[1], CHATTER_PORTS.get(ports[0]))} "
                     "on the router LAN (the lab runs no Avahi)"] += 1
            continue
        if destination_ip.is_multicast and protocol != 2:
            problems[f"IPv4 multicast to {destination_ip} on the router LAN"] += 1
            continue
        if (destination_ip == IPv4Address("255.255.255.255")
                or destination_ip == uplink_network.broadcast_address) and not dhcp_client:
            problems[f"IPv4 broadcast to {destination_ip} on the router LAN"] += 1
            continue
        if protocol == 2 and body[:1] == b"\x11":
            problems["IGMP query (the Pi is not a multicast router)"] += 1
            continue
        if protocol == 1 and not fragment:
            if _checksum(body):
                problems["damaged ICMP message"] += 1
                continue
            if body[:1] == b"\x05":
                problems["ICMP redirect"] += 1
                continue
            if body[:1] == b"\x09":
                problems["ICMP router advertisement"] += 1
                continue
        if protocol == 17 and ports[1] == 53:
            stats["dns questions"] += 1
        if protocol in (6, 17, 1) and not (protocol == 17 and 53 in ports):
            previous = seen.get(frame)
            if previous is not None and moment - previous < 1.0:
                problems["byte-identical copy of a forwarded frame"] += 1
            seen[frame] = moment
        stats["ipv4"] += 1
    for name, times, limit in (("broadcast or multicast", burst_times, BURST_LIMIT),
                               ("ARP", arp_times, ARP_LIMIT)):
        peak = _peak_per_second(times)
        stats[f"peak {name} per second"] = peak
        if peak > limit:
            problems[f"{peak} {name} frames in one second (limit {limit})"] += 1
    if clients_distinct_names is not None and stats["dns questions"] > clients_distinct_names:
        problems[f"{stats['dns questions']} DNS questions to the router for "
                 f"{clients_distinct_names} distinct names (amplified)"] += 1
    return problems, stats


def _audit_ipv6(packet, pi_ipv6, problems):
    if len(packet) < 40:
        problems["malformed IPv6"] += 1
        return
    source = IPv6Address(packet[8:24])
    if source != IPv6Address("::") and str(source) not in pi_ipv6:
        problems[f"IPv6 source {source} is not the Pi's (forwarded IPv6)"] += 1
        return
    next_header, offset = packet[6], 40
    # Hop-by-hop options come first in MLD reports.
    if next_header == 0 and len(packet) >= offset + 8:
        next_header, offset = packet[offset], offset + (packet[offset + 1] + 1) * 8
    if next_header == 58 and len(packet) > offset:
        kind = packet[offset]
        if kind in ICMP6_FORBIDDEN:
            problems[ICMP6_FORBIDDEN[kind]] += 1
    elif next_header == 17 and len(packet) >= offset + 4:
        sport, dport = struct.unpack("!HH", packet[offset:offset + 4])
        if sport == 547:
            problems["DHCPv6 server reply on the router LAN"] += 1
        elif dport in CHATTER_PORTS:
            problems[f"{CHATTER_PORTS[dport]} over IPv6 on the router LAN (the lab runs no Avahi)"] += 1


def _peak_per_second(times):
    peak, start = 0, 0
    for end, moment in enumerate(times):
        while moment - times[start] >= 1.0:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


def unit_kernel_settings():
    """The shell commands jittersim-control-firewall.service runs to set kernel settings."""

    commands = []
    unit = (ROOT / "deploy/raspberry-pi/jittersim-control-firewall.service").read_text()
    for line in unit.splitlines():
        if line.startswith("ExecStartPre=") and "/proc/sys/" in line:
            # systemd reads $$ as one dollar sign.
            commands.append(shlex.split(line.split("=", 1)[1].lstrip("-@").replace("$$", "$")))
    return commands


def control_firewall_text():
    text = (ROOT / "deploy/raspberry-pi/jittersim-control-firewall.nft").read_text()
    return text.replace("@TEST_INTERFACE@", TOPOLOGY.test_interface).replace(
        "@UPLINK_INTERFACE@", TOPOLOGY.uplink_interface)


HARSH = Impairment(delay_ms=5, jitter_ms=2, loss_percent=5, reorder_percent=30,
                   duplicate_percent=30, corrupt_percent=30)
# One entry per impairment type, the same setting on upload and download.
IMPAIRMENT_TYPES = {
    "delay": Impairment(delay_ms=40),
    "jitter": Impairment(delay_ms=20, jitter_ms=15),
    "loss": Impairment(loss_percent=20),
    "burst loss": Impairment(loss_percent=20, loss_burst_packets=5),
    "duplication": Impairment(duplicate_percent=40),
    "reordering": Impairment(delay_ms=10, reorder_percent=40),
    "corruption": Impairment(corrupt_percent=30),
    "rate": Impairment(bandwidth_kbit=256),
    "blackhole": Impairment(loss_percent=100),
    "every setting": HARSH,
}


def profile(identifier, impairment):
    return Profile(id=identifier, name=identifier, upload=impairment, download=impairment)


@unittest.skipUnless(ENABLED, "needs root on Linux, dnsmasq and JITTERSIM_KERNEL_TESTS=1")
class RouterWireGuardTests(unittest.TestCase):
    """Run every scenario against the same lab and fail on any bad frame toward the router."""

    @classmethod
    def setUpClass(cls) -> None:
        create_lab()
        cls.directory = Path(tempfile.mkdtemp(prefix="jittersim-wire-guard-"))
        cls.processes = []
        try:
            cls._build()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _build(cls) -> None:
        cls.pi_mac = _in(PI, "cat", "/sys/class/net/eth0/address").stdout.strip()
        cls.router_mac = _in(ROUTER, "cat", "/sys/class/net/up0/address").stdout.strip()
        # The router's own internet-side address and the Pi's default route to it.
        _in(ROUTER, "ip", "addr", "add", f"{INTERNET}/32", "dev", "lo")
        _in(PI, "ip", "route", "add", "default", "via", ROUTER_ADDRESS, "dev", "eth0")
        # NetworkManager's shared mode replaces the lab's plain masquerade.
        _in(PI, "nft", "delete", "table", "ip", "nat")
        _in(PI, "nft", "-f", "-", stdin=NM_SHARED_TABLE)
        cls.load_management_firewall()
        # The router's DNS answers everything with a cacheable address.
        cls.spawn(ROUTER, ["dnsmasq", "--keep-in-foreground", "--conf-file=/dev/null", "--no-hosts",
                           "--no-resolv", "--bind-interfaces", f"--listen-address={ROUTER_ADDRESS}",
                           f"--address=/#/{INTERNET}", "--local-ttl=300",
                           f"--pid-file={cls.directory}/router-dnsmasq.pid"])
        # NetworkManager's shared-mode dnsmasq on wlan0 with the installer's files
        # (dhcp-authoritative). NetworkManager hands it the uplink's DNS server.
        shared = cls.directory / "dnsmasq-shared.d"
        config = cls.directory / "jittersim-config"
        config.mkdir()
        localname.install_config(config, shared)
        argv = nm_dnsmasq_argv(cls.directory) + [f"--server={ROUTER_ADDRESS}"]
        cls.spawn(PI, argv)
        cls.spawn(ROUTER, [sys.executable, "-c", SINK, INTERNET])
        # IPv6 the way a client may have it from elsewhere, aimed at the Pi.
        pi_wifi_link_local = cls.ipv6_addresses("wlan0")[0]
        _in(CLIENT, "ip", "-6", "addr", "add", "2001:db8:50::10/64", "dev", "c0", "nodad")
        _in(CLIENT, "ip", "-6", "route", "add", "default", "via", pi_wifi_link_local, "dev", "c0")
        time.sleep(1.0)
        cls.pi_ipv6 = tuple(cls.ipv6_addresses("eth0"))

    @classmethod
    def load_management_firewall(cls) -> None:
        for command in unit_kernel_settings():
            _in(PI, *command)
        _in(PI, "nft", "-f", "-", stdin=control_firewall_text())

    @classmethod
    def spawn(cls, namespace, argv):
        process = subprocess.Popen(["ip", "netns", "exec", namespace, *argv],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.processes.append(process)
        return process

    @classmethod
    def ipv6_addresses(cls, interface):
        deadline = time.monotonic() + 5
        while True:
            listing = json.loads(_in(PI, "ip", "-6", "-j", "addr", "show", "dev", interface).stdout)
            found = [item["local"] for entry in listing for item in entry.get("addr_info", [])
                     if not item.get("tentative")]
            if found or time.monotonic() > deadline:
                return found
            time.sleep(0.2)

    @classmethod
    def tearDownClass(cls) -> None:
        for process in cls.processes:
            process.kill()
            process.wait(timeout=5)
        remove_lab()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def setUp(self) -> None:
        self.applier = NetworkApplier(executor=NamespaceExecutor(PI), topology=TOPOLOGY)
        self.addCleanup(self.applier.reset)

    # One run: capture on the router port while the clients and the LAN probes send.

    def capture(self, *, during=None, clients=(CLIENT,)):
        capture = subprocess.Popen(
            ["ip", "netns", "exec", ROUTER, sys.executable, "-c", CAPTURE, "up0",
             str(CAPTURE_SECONDS)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(capture.stdout.readline().strip(), "ready")
            sources = {CLIENT: CLIENT_ADDRESS, OTHER_CLIENT: OTHER_ADDRESS}
            started = time.monotonic()
            senders = []
            for namespace in clients:
                senders.append(subprocess.Popen(
                    ["ip", "netns", "exec", namespace, sys.executable, "-c", TRAFFIC,
                     sources[namespace], INTERNET, "192.168.50.1", namespace.rsplit("-", 1)[-1],
                     str(REPEATED_QUERIES), str(DISTINCT_NAMES)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                senders.append(subprocess.Popen(
                    ["ip", "netns", "exec", namespace, "ping", "-n", "-q", "-c", "150", "-i",
                     "0.005", "-W", "1", "-I", sources[namespace], INTERNET],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            senders.append(subprocess.Popen(
                ["ip", "netns", "exec", ROUTER, sys.executable, "-c", PROBES, "up0",
                 self.router_mac, self.pi_mac, ROUTER_ADDRESS, PI_UPLINK, PROBE_MAC, INTERNET],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            if during is not None:
                time.sleep(1.0)
                during()
            for sender in senders:
                try:
                    sender.wait(timeout=max(1.0, started + 40 - time.monotonic()))
                except subprocess.TimeoutExpired:
                    sender.kill()
                    sender.wait()
            output, _ = capture.communicate(timeout=60)
        finally:
            if capture.poll() is None:
                capture.kill()
                capture.wait()
        frames = [(moment, bytes.fromhex(data)) for moment, data in json.loads(output)]
        return audit_frames(frames, pi_mac=self.pi_mac, router_mac=self.router_mac,
                            pi_ip=PI_UPLINK, pi_ipv6=self.pi_ipv6,
                            clients_distinct_names=1 + len(clients) * DISTINCT_NAMES)

    def assert_clean(self, *, plan=None, service_addresses=None, during=None,
                     clients=(CLIENT,), forwarded=True):
        if plan is not None:
            self.applier.apply(plan=plan, service_addresses=service_addresses)
        problems, stats = self.capture(during=during, clients=clients)
        self.assertEqual(dict(problems), {}, f"bad frames toward the router ({dict(stats)})")
        # The capture saw the Pi answering ARP for its own uplink address and, unless
        # the scenario blocks it, the clients' traffic: an empty problem list is
        # never an empty capture.
        self.assertGreater(stats["arp replies for the uplink address"], 0, dict(stats))
        if forwarded:
            self.assertGreater(stats["ipv4"], 100, dict(stats))
        self.assertEqual(_in(PI, "cat", "/proc/sys/net/ipv6/conf/all/forwarding").stdout.strip(),
                         "0", "IPv6 forwarding was switched on")
        return stats

    # The scenarios.

    def test_no_profile_running(self) -> None:
        # Profiles stopped, after Reset and after Panic: no JitterSim tables at all.
        stats = self.assert_clean(clients=(CLIENT, OTHER_CLIENT))
        self.assertGreater(stats["dns questions"], 0, "the Pi's resolver never asked the router")

    def test_every_impairment_type_on_a_device(self) -> None:
        for name, impairment in IMPAIRMENT_TYPES.items():
            with self.subTest(impairment=name):
                plan = compile_plan(devices=[Device("a", "a", CLIENT_ADDRESS, "p")],
                                    profiles={"p": profile("p", impairment)})
                self.assert_clean(plan=plan, forwarded=name != "blackhole")

    def test_every_impairment_type_for_everyone(self) -> None:
        for name in ("duplication", "reordering", "corruption", "every setting"):
            with self.subTest(impairment=name):
                plan = compile_plan(devices=[], profiles={"p": profile("p", IMPAIRMENT_TYPES[name])},
                                    general_profile=GeneralProfileRule("p"))
                self.assert_clean(plan=plan, clients=(CLIENT, OTHER_CLIENT))

    def test_an_outage_for_everyone(self) -> None:
        plan = compile_plan(devices=[], profiles={"p": profile("p", HARSH)},
                            general_profile=GeneralProfileRule("p", outage_active=True))
        self.assert_clean(plan=plan, clients=(CLIENT, OTHER_CLIENT), forwarded=False)

    def test_everyone_else_with_a_protected_device(self) -> None:
        rule = EveryoneElseRule(mark=0, upload_impairment=HARSH, download_impairment=HARSH,
                                unaffected_macs=(CLIENT_MAC,))
        plan = compile_plan(devices=[], profiles={"p": profile("p", Impairment())},
                            everyone_else=rule)
        self.assert_clean(plan=plan, clients=(CLIENT, OTHER_CLIENT))

    def test_everyone_else_disconnected(self) -> None:
        rule = EveryoneElseRule(mark=0, upload_impairment=HARSH, download_impairment=HARSH,
                                blocked=True)
        plan = compile_plan(devices=[Device("a", "a", CLIENT_ADDRESS, "p")],
                            profiles={"p": profile("p", Impairment())}, everyone_else=rule)
        self.assert_clean(plan=plan, clients=(CLIENT, OTHER_CLIENT))

    def test_protected_devices_under_a_profile_for_everyone(self) -> None:
        plan = compile_plan(devices=[], profiles={"p": profile("p", HARSH)},
                            general_profile=GeneralProfileRule("p"),
                            protected_mac_addresses=(CLIENT_MAC, OTHER_CLIENT_MAC))
        self.assert_clean(plan=plan, clients=(CLIENT, OTHER_CLIENT))

    def test_website_rules_service_lists_and_quotas(self) -> None:
        internet = IPv4Address(INTERNET)
        listed = ServiceListRule("internet", ("internet.example",), 0, HARSH, HARSH)
        quota = ServiceQuotaRule(
            quota_id="internet", device_id="b", device_ip=IPv4Address(OTHER_ADDRESS),
            destination_addresses=(internet,), mark=0, upload_impairment=HARSH,
            download_impairment=HARSH, active=True)
        cases = {
            "blocked website": compile_plan(devices=[], profiles={},
                                            global_blocked_addresses=(internet,)),
            "website allowlist": compile_plan(devices=[], profiles={},
                                              global_allowed_addresses=(IPv4Address("203.0.113.9"),)),
            "service list": compile_plan(devices=[], profiles={}, service_list_rules=[listed]),
            "service quota": compile_plan(
                devices=[Device("b", "b", OTHER_ADDRESS, "p")],
                profiles={"p": profile("p", Impairment())}, service_quota_rules=[quota]),
            "disconnected device": compile_plan(
                devices=[Device("a", "a", CLIENT_ADDRESS, "p")],
                profiles={"p": profile("p", Impairment())},
                blocked_device_addresses=(IPv4Address(CLIENT_ADDRESS),)),
        }
        for name, plan in cases.items():
            with self.subTest(rule=name):
                self.assert_clean(plan=plan, service_addresses={"internet": [internet]},
                                  clients=(CLIENT, OTHER_CLIENT),
                                  forwarded=name not in ("blocked website", "website allowlist"))

    def test_panic_and_reset_while_traffic_runs(self) -> None:
        # Panic and Reset remove every JitterSim table and queue at once. Open
        # connections and queued packets must not leave in a bad state.
        plan = compile_plan(devices=[], profiles={"p": profile("p", HARSH)},
                            general_profile=GeneralProfileRule("p"))
        self.assert_clean(plan=plan, during=self.applier.reset, clients=(CLIENT, OTHER_CLIENT))

    def test_a_wifi_restart_while_traffic_runs(self) -> None:
        # Recovery, the router-name rename at boot and a router Wi-Fi copy all
        # restart the access point (nmcli connection up). NetworkManager removes
        # its NAT table while the radio may still pass frames for a moment, then
        # adds it back.
        plan = compile_plan(devices=[Device("a", "a", CLIENT_ADDRESS, "p")],
                            profiles={"p": profile("p", HARSH)})

        def restart():
            _in(PI, "nft", "delete", "table", "ip", "nm-shared-wlan0")
            time.sleep(1.5)
            _in(PI, "ip", "link", "set", "wlan0", "down")
            time.sleep(0.5)
            _in(PI, "ip", "link", "set", "wlan0", "up")
            _in(PI, "ip", "addr", "replace", "192.168.50.1/24", "dev", "wlan0")
            _in(PI, "nft", "-f", "-", stdin=NM_SHARED_TABLE)

        self.addCleanup(lambda: _in(PI, "nft", "-f", "-", stdin=NM_SHARED_TABLE, check=False))
        self.assert_clean(plan=plan, during=restart, clients=(CLIENT, OTHER_CLIENT))

    def test_a_management_firewall_restart_while_traffic_runs(self) -> None:
        # An install or update restarts jittersim-control-firewall.service: its
        # table goes and comes back.
        def restart():
            _in(PI, "nft", "delete", "table", "inet", "jittersim_control")
            self.load_management_firewall()

        self.assert_clean(during=restart, clients=(CLIENT, OTHER_CLIENT))


@unittest.skipUnless(ENABLED, "needs root on Linux, dnsmasq and JITTERSIM_KERNEL_TESTS=1")
class SameSubnetRouterTests(unittest.TestCase):
    """A router LAN on 192.168.50.0/24 with the router on .1, like many brands' default.

    The installer refuses such a network, but the router can change later. The
    Pi then holds 192.168.50.1 on its Wi-Fi and an address of the same network on
    Ethernet. It must still never answer ARP for the router's address on the LAN
    (that would steal every household device's gateway) and never answer DHCP
    there.
    """

    ROUTER_ON_SAME_SUBNET = "192.168.50.1"
    PI_ON_SAME_SUBNET = "192.168.50.23"
    # The probes come from a household device: Linux ignores ARP whose sender
    # uses one of its own addresses, and the Pi holds 192.168.50.1 itself.
    HOUSEHOLD_DEVICE = "192.168.50.77"

    def setUp(self) -> None:
        create_lab()
        self.addCleanup(remove_lab)
        self.directory = Path(tempfile.mkdtemp(prefix="jittersim-wire-overlap-"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        for command in unit_kernel_settings():
            _in(PI, *command)
        _in(PI, "nft", "delete", "table", "ip", "nat")
        _in(PI, "nft", "-f", "-", stdin=NM_SHARED_TABLE)
        _in(PI, "nft", "-f", "-", stdin=control_firewall_text())
        _in(ROUTER, "ip", "addr", "flush", "dev", "up0")
        _in(ROUTER, "ip", "addr", "add", f"{self.ROUTER_ON_SAME_SUBNET}/24", "dev", "up0")
        _in(PI, "ip", "addr", "flush", "dev", "eth0")
        _in(PI, "ip", "addr", "add", f"{self.PI_ON_SAME_SUBNET}/24", "dev", "eth0")
        shared = self.directory / "dnsmasq-shared.d"
        config = self.directory / "jittersim-config"
        config.mkdir()
        localname.install_config(config, shared)
        dnsmasq = subprocess.Popen(["ip", "netns", "exec", PI, *nm_dnsmasq_argv(self.directory)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(dnsmasq.wait, 5)
        self.addCleanup(dnsmasq.kill)
        time.sleep(1.0)
        self.pi_mac = _in(PI, "cat", "/sys/class/net/eth0/address").stdout.strip()
        self.router_mac = _in(ROUTER, "cat", "/sys/class/net/up0/address").stdout.strip()

    def test_the_pi_never_claims_the_router_address_or_serves_dhcp_on_the_lan(self) -> None:
        capture = subprocess.Popen(
            ["ip", "netns", "exec", ROUTER, sys.executable, "-c", CAPTURE, "up0", "4"],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(capture.stdout.readline().strip(), "ready")
            _in(ROUTER, sys.executable, "-c", PROBES, "up0", self.router_mac, self.pi_mac,
                self.HOUSEHOLD_DEVICE, self.PI_ON_SAME_SUBNET, PROBE_MAC, INTERNET)
            output, _ = capture.communicate(timeout=30)
        finally:
            if capture.poll() is None:
                capture.kill()
                capture.wait()
        frames = [(moment, bytes.fromhex(data)) for moment, data in json.loads(output)]
        problems, stats = audit_frames(
            frames, pi_mac=self.pi_mac, router_mac=self.router_mac, pi_ip=self.PI_ON_SAME_SUBNET,
            pi_ipv6=tuple(_ipv6_of(PI, "eth0")), test_wifi=IPv4Network("192.0.2.0/24"),
            uplink_network=IPv4Network("192.168.50.0/24"))
        self.assertEqual(dict(problems), {}, dict(stats))
        self.assertGreater(stats["arp replies for the uplink address"], 0, dict(stats))


def _ipv6_of(namespace, interface):
    listing = json.loads(_in(namespace, "ip", "-6", "-j", "addr", "show", "dev", interface).stdout)
    return [item["local"] for entry in listing for item in entry.get("addr_info", [])]


class GuardSelfTests(unittest.TestCase):
    """The guard itself must report each kind of bad frame. Pure Python, runs everywhere."""

    PI_MAC, ROUTER_MAC = "02:00:00:00:00:e0", "02:00:00:00:00:01"

    def frame(self, payload, *, source=None, destination=None, ethertype=0x0800):
        return (bytes.fromhex((destination or self.ROUTER_MAC).replace(":", ""))
                + bytes.fromhex((source or self.PI_MAC).replace(":", ""))
                + struct.pack("!H", ethertype) + payload)

    def ipv4(self, source, destination, protocol, body):
        header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 1, 0, 64, protocol, 0,
                             IPv4Address(source).packed, IPv4Address(destination).packed)
        return header[:10] + struct.pack("!H", _checksum(header)) + header[12:] + body

    def icmp(self, kind, code):
        body = bytes((kind, code)) + bytes(34)
        return body[:2] + struct.pack("!H", _checksum(body)) + body[4:]

    def audit(self, *frames):
        problems, _ = audit_frames([(0.0, frame) for frame in frames], pi_mac=self.PI_MAC,
                                   router_mac=self.ROUTER_MAC, pi_ip=PI_UPLINK)
        return " | ".join(problems)

    def test_each_kind_of_bad_frame_is_reported(self) -> None:
        udp = struct.pack("!HHHH", 40000, 9, 12, 0) + b"abcd"
        cases = {
            "not the Pi's": self.frame(self.ipv4(PI_UPLINK, INTERNET, 17, udp),
                                       source="02:00:00:00:00:e1"),
            "skipped NAT": self.frame(self.ipv4("192.168.50.10", INTERNET, 17, udp)),
            "damaged IPv4 header": self.frame(self.ipv4(PI_UPLINK, INTERNET, 17, udp)[:10]
                                              + b"\0\0" + bytes(8) + udp),
            "DHCP server": self.frame(self.ipv4(PI_UPLINK, "255.255.255.255", 17,
                                                struct.pack("!HHHH", 67, 68, 8, 0)),
                                      destination="ff:ff:ff:ff:ff:ff"),
            "SSDP": self.frame(self.ipv4(PI_UPLINK, "239.255.255.250", 17,
                                         struct.pack("!HHHH", 1900, 1900, 8, 0)),
                               destination="01:00:5e:7f:ff:fa"),
            "ARP from the Pi for 192.168.50.1": self.frame(
                struct.pack("!HHBBH", 1, 0x0800, 6, 4, 2)
                + bytes.fromhex(self.PI_MAC.replace(":", "")) + IPv4Address("192.168.50.1").packed
                + bytes.fromhex(self.ROUTER_MAC.replace(":", "")) + IPv4Address(ROUTER_ADDRESS).packed,
                ethertype=0x0806),
            "EtherType 0x88cc": self.frame(bytes(46), ethertype=0x88CC),
            "odd group address": self.frame(bytes(46), ethertype=0x88CC,
                                            destination="01:80:c2:00:00:0e"),
            "ICMP redirect": self.frame(self.ipv4(PI_UPLINK, ROUTER_ADDRESS, 1, self.icmp(5, 1))),
        }
        for expected, frame in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, self.audit(frame))
        burst = [(index * 0.01, self.frame(bytes(46), ethertype=0x0806,
                                           destination="ff:ff:ff:ff:ff:ff")) for index in range(30)]
        problems, _ = audit_frames(burst, pi_mac=self.PI_MAC, router_mac=self.ROUTER_MAC,
                                   pi_ip=PI_UPLINK)
        self.assertTrue(any("in one second" in problem for problem in problems), problems)
        copies = [(0.0, self.frame(self.ipv4(PI_UPLINK, INTERNET, 17, udp)))] * 2
        problems, _ = audit_frames(copies, pi_mac=self.PI_MAC, router_mac=self.ROUTER_MAC,
                                   pi_ip=PI_UPLINK)
        self.assertIn("byte-identical copy of a forwarded frame", problems)


if __name__ == "__main__":
    unittest.main()
