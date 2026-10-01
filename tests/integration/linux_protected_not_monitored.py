"""Prove with real packets that protected clients are never monitored.

Run it only in a disposable container with --privileged (or in an empty separate network
namespace). It builds a Pi side with two veth pairs and checks two things.

1. The DNS capture. A real tcpdump runs through NetworkApplier.capture_dns_queries. A protected
   client and an ordinary client send DNS queries to a resolver on the Pi that answers both of
   them. A capture that leaves out the protected MAC must show only the ordinary client in
   either direction. A capture without the exclusion runs at the same time on the same traffic
   and must show both so that the comparison means something.
2. The traffic counters. Every counter in the project nftables tables must stay exactly where
   it was while packets of a protected MAC cross the Pi in both directions. This includes a
   protected MAC that now uses an address that was assigned to an ordinary device before. Only
   the counters of the ordinary client may move when it sends the same packets.
"""

import json
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GENERAL_QUEUE_ID, GeneralProfileRule
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.helper import _parse_dns_query_output
from jittersim.networking.topology import Topology

PROTECTED = "02:00:00:00:00:01"
ORDINARY = "02:00:00:00:00:02"
REMOTE = "02:00:00:00:00:03"
ORDINARY_IP = "192.168.50.10"
PROTECTED_IP = "192.168.50.11"
GENERAL_IP = "192.168.50.13"
PI_IP = "192.168.50.1"
REMOTE_IP = "198.18.0.2"
DEVICE_COUNTERS = {"usage-device affected upload", "usage-device affected download"}
GENERAL_COUNTERS = {
    "usage-device %s upload" % GENERAL_QUEUE_ID,
    "usage-device %s download" % GENERAL_QUEUE_ID,
}


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def mac_bytes(value):
    return bytes.fromhex(value.replace(":", ""))


def checksummed_header(source_ip, target_ip, payload_length):
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + payload_length,
        1,
        0,
        64,
        17,
        0,
        socket.inet_aton(source_ip),
        socket.inet_aton(target_ip),
    )
    checksum = sum(struct.unpack("!10H", header))
    checksum = (checksum & 0xFFFF) + (checksum >> 16)
    checksum = (checksum & 0xFFFF) + (checksum >> 16)
    return header[:10] + struct.pack("!H", (~checksum) & 0xFFFF) + header[12:]


def frame(source_mac, target_mac, source_ip, target_ip, payload, *, sport=32000, dport=32001):
    """Build one Ethernet frame that carries a UDP datagram."""

    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    header = checksummed_header(source_ip, target_ip, len(udp))
    return mac_bytes(target_mac) + mac_bytes(source_mac) + b"\x08\x00" + header + udp


def encode_name(value):
    return b"".join(bytes([len(label)]) + label.encode() for label in value.split(".")) + b"\x00"


def dns_query(name, identifier):
    return struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0) + encode_name(name) + struct.pack(
        "!HH", 1, 1
    )


def dns_answer(query, address):
    """Answer a one-question query with one A record."""

    return (
        query[:2]
        + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
        + query[12:]
        + b"\xc0\x0c"
        + struct.pack("!HHIH", 1, 1, 60, 4)
        + socket.inet_aton(address)
    )


def drain(receiver):
    receiver.settimeout(0.01)
    try:
        while receiver.recv(2048):
            pass
    except socket.timeout:
        pass


def packet_passes(sender, receiver, packet, tag):
    drain(receiver)
    sender.send(packet)
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        receiver.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            received, info = receiver.recvfrom(2048)
        except socket.timeout:
            return False
        if info[2] != socket.PACKET_OUTGOING and received.endswith(tag.encode()):
            return True
    return False


def counters(applier):
    """Return every counter of the project tables keyed by its rule."""

    snapshot = {}
    for item in json.loads(applier.traffic_counters())["nftables"]:
        rule = item.get("rule")
        if rule is None:
            continue
        parts = [part["counter"] for part in rule.get("expr", []) if "counter" in part]
        if not parts:
            continue
        key = (
            rule.get("family"),
            rule.get("table"),
            rule.get("chain"),
            rule.get("handle"),
            rule.get("comment", ""),
        )
        snapshot[key] = (
            sum(part.get("packets", 0) for part in parts),
            sum(part.get("bytes", 0) for part in parts),
        )
    return snapshot


def moved(before, after):
    """Return the comments of every counter that is not where it was."""

    return {
        key[4] or "%s %s handle %s" % (key[1], key[2], key[3])
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }


def require_disposable_namespace():
    if Path("/.dockerenv").exists():
        return
    isolated = Path("/proc/self/ns/net").stat().st_ino != Path("/proc/1/ns/net").stat().st_ino
    interfaces = json.loads(subprocess.check_output(["ip", "-j", "link", "show"], text=True))
    if not isolated or {item["ifname"] for item in interfaces} != {"lo"}:
        raise RuntimeError(
            "Run only in a disposable container or an empty separate network namespace"
        )


def build_network():
    for interface, peer in (("jittersim0", "client0"), ("up0", "remote0")):
        run("ip", "link", "add", interface, "type", "veth", "peer", "name", peer)
        run("ip", "link", "set", interface, "up")
        run("ip", "link", "set", peer, "up", "promisc", "on")
    run("ip", "link", "set", "remote0", "address", REMOTE)
    run("ip", "link", "set", "client0", "address", PROTECTED)
    run("ip", "addr", "add", PI_IP + "/24", "dev", "jittersim0")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "up0")
    run("ip", "neigh", "replace", REMOTE_IP, "lladdr", REMOTE, "nud", "permanent", "dev", "up0")
    for address, mac in (
        (ORDINARY_IP, ORDINARY),
        (PROTECTED_IP, PROTECTED),
        (GENERAL_IP, ORDINARY),
    ):
        set_owner(address, mac)


def set_owner(address, mac):
    run("ip", "neigh", "replace", address, "lladdr", mac, "nud", "permanent", "dev", "jittersim0")


def check_dns_capture(applier, test_mac, client):
    """Run a leaking and a protecting capture side by side on the same DNS traffic."""

    resolver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    resolver.bind((PI_IP, 53))
    resolver.settimeout(0.05)
    stop = threading.Event()

    def answer():
        while not stop.is_set():
            try:
                query, peer = resolver.recvfrom(512)
            except socket.timeout:
                continue
            resolver.sendto(dns_answer(query, "93.184.216.34"), peer)

    outputs = {}

    def capture(name, excluded):
        outputs[name] = applier.capture_dns_queries(excluded)

    workers = [
        threading.Thread(target=answer),
        threading.Thread(target=capture, args=("open", [])),
        threading.Thread(target=capture, args=("protecting", [PROTECTED])),
    ]
    for worker in workers:
        worker.start()
    try:
        time.sleep(1.0)  # Let both tcpdump processes attach before the traffic starts.
        for round_number in range(25):
            for mac, address, name in (
                (ORDINARY, ORDINARY_IP, "ordinary-client.example.com"),
                (PROTECTED, PROTECTED_IP, "protected-client.example.com"),
            ):
                query = dns_query(name, 0x1000 + round_number)
                client.send(
                    frame(mac, test_mac, address, PI_IP, query, sport=40000, dport=53)
                )
            time.sleep(0.1)
        stop.set()
        workers[0].join(timeout=5)
        for worker in workers[1:]:
            worker.join(timeout=15)
    finally:
        stop.set()
        resolver.close()

    def sources(output):
        return {item["ipv4_address"] for item in _parse_dns_query_output(output, observed_at=0)}

    open_capture = outputs["open"]
    protecting_capture = outputs["protecting"]
    assert sources(open_capture) == {ORDINARY_IP, PROTECTED_IP}, (
        "The comparison capture must see both clients or the test proves nothing: "
        + repr(sources(open_capture))
    )
    assert "> %s." % PROTECTED_IP in open_capture, (
        "The comparison capture must see the answers to the protected client"
    )
    assert PROTECTED_IP not in sources(protecting_capture), (
        "The capture saw DNS queries from the protected client " + PROTECTED_IP
    )
    assert PROTECTED_IP not in protecting_capture, (
        "The capture saw DNS packets to or from the protected client " + PROTECTED_IP
    )
    assert sources(protecting_capture) == {ORDINARY_IP}, (
        "The capture lost the ordinary client: " + repr(sources(protecting_capture))
    )
    assert "> %s." % ORDINARY_IP in protecting_capture, (
        "The capture lost the answers to the ordinary client"
    )


def send_both_ways(client, remote, test_mac, up_mac, mac, address, label):
    """Send one upload and one download packet and require that both are forwarded."""

    tag = "%s-up-%s" % (label, address) + "x" * 200
    assert packet_passes(
        client, remote, frame(mac, test_mac, address, REMOTE_IP, tag.encode()), tag
    ), "The upload %s was not forwarded" % tag[:40]
    tag = "%s-down-%s" % (label, address) + "x" * 200
    assert packet_passes(
        remote, client, frame(REMOTE, up_mac, REMOTE_IP, address, tag.encode()), tag
    ), "The download %s was not forwarded" % tag[:40]


def check_device_counters(applier, client, remote, test_mac, up_mac):
    """Per-device counters follow an assigned device and never a protected MAC."""

    mild = Profile("mild", "Mild", upload=Impairment(delay_ms=1), download=Impairment(delay_ms=1))
    device = Device("affected", "Affected", ORDINARY_IP, mild.id, ORDINARY)
    plan = compile_plan(
        devices=(device,),
        profiles={mild.id: mild},
        protected_mac_addresses=(PROTECTED,),
    )
    applier.apply(plan=plan)

    before = counters(applier)
    send_both_ways(client, remote, test_mac, up_mac, ORDINARY, ORDINARY_IP, "ordinary")
    changed = moved(before, counters(applier))
    assert changed == DEVICE_COUNTERS, (
        "The ordinary device must be counted in both directions and nothing else: "
        + repr(sorted(changed))
    )

    # The address of the assigned device now belongs to the protected client.
    set_owner(ORDINARY_IP, PROTECTED)
    before = counters(applier)
    for address in (ORDINARY_IP, PROTECTED_IP):
        send_both_ways(client, remote, test_mac, up_mac, PROTECTED, address, "protected")
    changed = moved(before, counters(applier))
    assert not changed, "Protected packets moved counters: " + repr(sorted(changed))
    set_owner(ORDINARY_IP, ORDINARY)


def check_general_counters(applier, client, remote, test_mac, up_mac):
    """The general shared counters follow ordinary clients and never a protected MAC."""

    shared = Profile(
        "shared",
        "Shared",
        upload=Impairment(bandwidth_kbit=100_000),
        download=Impairment(bandwidth_kbit=100_000),
    )
    plan = compile_plan(
        devices=(),
        profiles={shared.id: shared},
        protected_mac_addresses=(PROTECTED,),
        general_profile=GeneralProfileRule(shared.id),
    )
    applier.apply(plan=plan)

    before = counters(applier)
    send_both_ways(client, remote, test_mac, up_mac, ORDINARY, GENERAL_IP, "general")
    changed = moved(before, counters(applier))
    assert changed == GENERAL_COUNTERS, (
        "The shared limit must count an ordinary client in both directions: "
        + repr(sorted(changed))
    )

    before = counters(applier)
    send_both_ways(client, remote, test_mac, up_mac, PROTECTED, PROTECTED_IP, "protected")
    changed = moved(before, counters(applier))
    assert not changed, "Protected packets moved general counters: " + repr(sorted(changed))


def main():
    require_disposable_namespace()
    build_network()
    test_mac = Path("/sys/class/net/jittersim0/address").read_text().strip()
    up_mac = Path("/sys/class/net/up0/address").read_text().strip()
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=Topology("up0", "jittersim0"))
    client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    client.bind(("client0", 0))
    remote.bind(("remote0", 0))
    try:
        check_dns_capture(applier, test_mac, client)
        check_device_counters(applier, client, remote, test_mac, up_mac)
        check_general_counters(applier, client, remote, test_mac, up_mac)
        print("OK protected clients are not monitored")
    finally:
        client.close()
        remote.close()
        applier.reset()
        run("ip", "link", "del", "jittersim0")
        run("ip", "link", "del", "up0")


if __name__ == "__main__":
    main()
