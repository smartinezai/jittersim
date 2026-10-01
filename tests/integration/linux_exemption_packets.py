"""Verify exemptions using real routed Ethernet packets in an isolated container."""

import json
import socket
import struct
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GENERAL_QUEUE_ID, GeneralProfileRule, ServiceQuotaRule, WebsiteFilter
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.topology import Topology

PROTECTED = "02:00:00:00:00:01"
AFFECTED = "02:00:00:00:00:02"
REMOTE = "02:00:00:00:00:03"
OTHER_AFFECTED = "02:00:00:00:00:04"


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def mac_bytes(value):
    return bytes.fromhex(value.replace(":", ""))


def frame(source_mac, target_mac, source_ip, target_ip, tag):
    payload = tag.encode()
    udp = struct.pack("!HHHH", 32000, 32001, 8 + len(payload), 0) + payload
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(udp),
        1,
        0,
        64,
        17,
        0,
        socket.inet_aton(source_ip),
        socket.inet_aton(target_ip),
    )
    words = struct.unpack("!10H", header)
    checksum = sum(words)
    checksum = (checksum & 0xFFFF) + (checksum >> 16)
    checksum = (checksum & 0xFFFF) + (checksum >> 16)
    header = header[:10] + struct.pack("!H", (~checksum) & 0xFFFF) + header[12:]
    return mac_bytes(target_mac) + mac_bytes(source_mac) + b"\x08\x00" + header + udp


def packet_passes(sender, receiver, packet, tag):
    receiver.settimeout(0.01)
    try:
        while receiver.recv(2048):
            pass
    except socket.timeout:
        pass
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


def device_usage(applier, device_id="affected"):
    result = {"upload": 0, "download": 0, "service-upload": 0, "service-download": 0}
    for item in json.loads(applier.traffic_counters())["nftables"]:
        rule = item.get("rule", {})
        comment = rule.get("comment", "")
        for direction in ("upload", "download"):
            if comment == f"usage-device {device_id} {direction}":
                key = direction
            elif comment == f"usage-service video {direction}":
                key = "service-" + direction
            else:
                continue
            result[key] += sum(part.get("counter", {}).get("bytes", 0) for part in rule["expr"])
    return result


def main():
    if not Path("/.dockerenv").exists():
        isolated = (
            Path("/proc/self/ns/net").stat().st_ino
            != Path("/proc/1/ns/net").stat().st_ino
        )
        interfaces = json.loads(
            subprocess.check_output(["ip", "-j", "link", "show"], text=True)
        )
        if not isolated or {item["ifname"] for item in interfaces} != {"lo"}:
            raise RuntimeError(
                "Run only in a disposable container or an empty separate network namespace"
            )
    for interface, peer in (("jittersim0", "client0"), ("up0", "remote0")):
        run("ip", "link", "add", interface, "type", "veth", "peer", "name", peer)
        run("ip", "link", "set", interface, "up")
        run("ip", "link", "set", peer, "up", "promisc", "on")
    run("ip", "link", "set", "remote0", "address", REMOTE)
    run("ip", "link", "set", "client0", "address", PROTECTED)
    run("ip", "addr", "add", "192.168.50.1/24", "dev", "jittersim0")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "up0")
    run("ip", "neigh", "replace", "198.18.0.2", "lladdr", REMOTE, "nud", "permanent", "dev", "up0")
    run("ip", "neigh", "replace", "198.18.0.3", "lladdr", REMOTE, "nud", "permanent", "dev", "up0")
    for address, mac in (
        ("192.168.50.10", AFFECTED),
        ("192.168.50.11", PROTECTED),
        ("192.168.50.12", PROTECTED),
    ):
        run(
            "ip",
            "neigh",
            "replace",
            address,
            "lladdr",
            mac,
            "nud",
            "permanent",
            "dev",
            "jittersim0",
        )
    test_mac = Path("/sys/class/net/jittersim0/address").read_text().strip()
    up_mac = Path("/sys/class/net/up0/address").read_text().strip()
    topology = Topology("up0", "jittersim0")
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=topology)
    profile = Profile(
        "offline",
        "Offline",
        upload=Impairment(loss_percent=100),
        download=Impairment(loss_percent=100),
    )
    device = Device("affected", "Affected", "192.168.50.10", profile.id, AFFECTED)
    quota = ServiceQuotaRule(
        "video",
        device.id,
        device.ipv4_address,
        (socket_to_ip("198.18.0.2"),),
        0,
        profile.upload,
        profile.download,
        True,
    )
    plan = compile_plan(
        devices=(device,),
        profiles={profile.id: profile},
        protected_mac_addresses=(PROTECTED,),
        service_quota_rules=(quota,),
    )
    client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    client.bind(("client0", 0))
    remote.bind(("remote0", 0))
    try:
        applier.apply(plan=plan)
        assert not packet_passes(
            client,
            remote,
            frame(AFFECTED, test_mac, "192.168.50.10", "198.18.0.2", "affected-up"),
            "affected-up",
        )
        assert not packet_passes(
            remote,
            client,
            frame(REMOTE, up_mac, "198.18.0.2", "192.168.50.10", "affected-down"),
            "affected-down",
        )
        before = device_usage(applier)
        assert all(value > 0 for value in before.values()), before
        # Change the old affected IP's owner after compiling and applying the plan.
        run(
            "ip",
            "neigh",
            "replace",
            "192.168.50.10",
            "lladdr",
            PROTECTED,
            "nud",
            "permanent",
            "dev",
            "jittersim0",
        )
        for address in ("192.168.50.10", "192.168.50.11", "192.168.50.12"):
            tag = "protected-up-" + address
            assert packet_passes(
                client, remote, frame(PROTECTED, test_mac, address, "198.18.0.2", tag), tag
            ), tag
            tag = "protected-down-" + address
            assert packet_passes(
                remote, client, frame(REMOTE, up_mac, "198.18.0.2", address, tag), tag
            ), tag
        assert device_usage(applier) == before, "Protected packets consumed a profile quota"
        profile_blocks = replace(
            plan,
            blocked_device_addresses=(device.ipv4_address,),
            website_filters=(WebsiteFilter(device.ipv4_address, ()),),
        )
        applier.apply(plan=profile_blocks)
        assert packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.10", "198.18.0.2", "profile-bypass"),
            "profile-bypass",
        )
        assert all(value == 0 for value in device_usage(applier).values())
        assert packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.3", "destination-baseline"),
            "destination-baseline",
        )
        globally_allowed = replace(plan, global_allowed_addresses=(socket_to_ip("198.18.0.2"),))
        applier.apply(plan=globally_allowed)
        assert packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.2", "global-allowed"),
            "global-allowed",
        )
        assert not packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.3", "global-rejected"),
            "global-rejected",
        )
        applier.apply(plan=replace(plan, global_allowed_addresses=()))
        assert not packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.2", "empty-global-allowlist"),
            "empty-global-allowlist",
        )
        # A separate global MAC rule still wins over profile protection.
        blocked = compile_plan(
            devices=(device,),
            profiles={profile.id: profile},
            protected_mac_addresses=(PROTECTED,),
            mac_policy=MacPolicy(mode="denylist", denylist=(PROTECTED,)),
        )
        applier.apply(plan=blocked)
        assert not packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.2", "globally-blocked"),
            "globally-blocked",
        )
        shared = Profile(
            "shared", "Shared", upload=Impairment(bandwidth_kbit=1),
            download=Impairment(bandwidth_kbit=1),
        )
        general = compile_plan(
            devices=(), profiles={shared.id: shared}, protected_mac_addresses=(PROTECTED,),
            general_profile=GeneralProfileRule(shared.id),
        )
        applier.apply(plan=general)
        # New clients are covered without applying another plan.
        for address, mac in (("192.168.50.13", AFFECTED), ("192.168.50.14", OTHER_AFFECTED)):
            run("ip", "neigh", "replace", address, "lladdr", mac,
                "nud", "permanent", "dev", "jittersim0")
            tag = "shared-up-" + address + "x" * 1000
            assert not packet_passes(
                client, remote, frame(mac, test_mac, address, "198.18.0.2", tag), tag
            ), "The shared upload limit did not delay an affected client"
            tag = "shared-down-" + address + "x" * 1000
            assert not packet_passes(
                remote, client, frame(REMOTE, up_mac, "198.18.0.2", address, tag), tag
            ), "The shared download limit did not delay an affected client"
        before = device_usage(applier, GENERAL_QUEUE_ID)
        assert before["upload"] > 0 and before["download"] > 0, before
        for address in ("192.168.50.10", "192.168.50.11"):
            tag = "uncapped-up-" + address + "x" * 1000
            assert packet_passes(
                client, remote, frame(PROTECTED, test_mac, address, "198.18.0.2", tag), tag
            ), "A protected upload entered the shared limited queue"
            tag = "uncapped-down-" + address + "x" * 1000
            assert packet_passes(
                remote, client, frame(REMOTE, up_mac, "198.18.0.2", address, tag), tag
            ), "A protected download entered the shared limited queue"
        assert device_usage(applier, GENERAL_QUEUE_ID) == before
        outage = replace(general, general_profile=replace(general.general_profile, outage_active=True))
        applier.apply(plan=outage)
        assert not packet_passes(
            client, remote, frame(AFFECTED, test_mac, "192.168.50.13", "198.18.0.2", "general-outage"),
            "general-outage",
        )
        assert packet_passes(
            client, remote, frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.2", "protected-outage"),
            "protected-outage",
        )
        empty_allowlist = replace(general, general_profile=GeneralProfileRule(shared.id, ()))
        applier.apply(plan=empty_allowlist)
        assert not packet_passes(
            client, remote, frame(AFFECTED, test_mac, "192.168.50.13", "198.18.0.2", "general-website"),
            "general-website",
        )
        assert packet_passes(
            client, remote, frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.2", "protected-website"),
            "protected-website",
        )
        applier.reset()
        applier.reset()
        assert packet_passes(
            client,
            remote,
            frame(PROTECTED, test_mac, "192.168.50.11", "198.18.0.2", "reset-up"),
            "reset-up",
        )
        print(
            "Packet protection passed in both directions with IP reuse, multiple addresses "
            "and quotas plus global access rules. General shared limits bypass protected clients."
        )
    finally:
        client.close()
        remote.close()
        applier.reset()
        run("ip", "link", "del", "jittersim0")
        run("ip", "link", "del", "up0")


def socket_to_ip(value):
    from ipaddress import IPv4Address

    return IPv4Address(value)


if __name__ == "__main__":
    main()
