"""Verify service lists with real routed packets in an isolated container.

Run it only in a disposable container with NET_ADMIN and NET_RAW capabilities.
A profile that drops every packet makes shaping visible: a packet that does
not arrive went through the queue of the profile that applies to it.
"""

import json
import socket
import struct
import subprocess
import time
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.plan import (
    GeneralProfileRule,
    ServiceListRule,
    ServiceQuotaRule,
    WebsiteFilter,
)
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology

PROTECTED = "02:00:00:00:00:01"
AFFECTED = "02:00:00:00:00:02"
REMOTE = "02:00:00:00:00:03"
LISTED = "198.18.0.2"
OTHER = "198.18.0.3"
CLIENT = "192.168.50.10"
PROTECTED_CLIENT = "192.168.50.11"
NEW_CLIENT = "192.168.50.13"

OFFLINE = Profile(
    "offline", "Offline", upload=Impairment(loss_percent=100), download=Impairment(loss_percent=100)
)
NORMAL = Profile("normal", "Normal")


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
    checksum = sum(struct.unpack("!10H", header))
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


def list_counter(applier, comment):
    total = 0
    for item in json.loads(applier.traffic_counters())["nftables"]:
        rule = item.get("rule", {})
        if rule.get("comment") == comment:
            total += sum(part.get("counter", {}).get("bytes", 0) for part in rule["expr"])
    return total


def listed_rule(profile, list_id="streaming"):
    return ServiceListRule(list_id, ("streaming.example",), 0, profile.upload, profile.download)


def main():
    if not Path("/.dockerenv").exists():
        isolated = Path("/proc/self/ns/net").stat().st_ino != Path("/proc/1/ns/net").stat().st_ino
        interfaces = json.loads(subprocess.check_output(["ip", "-j", "link", "show"], text=True))
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
    for address in (LISTED, OTHER):
        run("ip", "neigh", "replace", address, "lladdr", REMOTE, "nud", "permanent", "dev", "up0")
    for address, mac in (
        (CLIENT, AFFECTED),
        (PROTECTED_CLIENT, PROTECTED),
        (NEW_CLIENT, AFFECTED),
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
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=Topology("up0", "jittersim0"))
    client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    client.bind(("client0", 0))
    remote.bind(("remote0", 0))
    counter = [0]

    def upload(source_mac, source_ip, target_ip):
        counter[0] += 1
        tag = f"up-{counter[0]}-{source_ip}-{target_ip}"
        return packet_passes(
            client, remote, frame(source_mac, test_mac, source_ip, target_ip, tag), tag
        )

    def download(source_ip, target_ip):
        counter[0] += 1
        tag = f"down-{counter[0]}-{source_ip}-{target_ip}"
        return packet_passes(remote, client, frame(REMOTE, up_mac, source_ip, target_ip, tag), tag)

    def both(source_mac, client_ip, server_ip):
        return upload(source_mac, client_ip, server_ip), download(server_ip, client_ip)

    try:
        # 1. Lists alone. Ordinary browsing is normal and a listed service is not.
        plan = compile_plan(
            devices=(),
            profiles={"offline": OFFLINE},
            protected_mac_addresses=(PROTECTED,),
            service_list_rules=(listed_rule(OFFLINE),),
        )
        applier.apply(plan=plan, service_addresses={"streaming": [IPv4Address(LISTED)]})
        assert both(AFFECTED, CLIENT, OTHER) == (True, True), "unlisted traffic was shaped"
        assert both(AFFECTED, CLIENT, LISTED) == (False, False), (
            "a listed service was not shaped in both directions"
        )
        assert both(PROTECTED, PROTECTED_CLIENT, LISTED) == (True, True), (
            "an unaffected device was shaped"
        )
        assert both(AFFECTED, NEW_CLIENT, LISTED) == (False, False), "a new client escaped the list"
        assert list_counter(applier, "service-list streaming upload") > 0
        assert list_counter(applier, "service-list streaming download") > 0

        # 2. Addresses learned later reach the running rules without another apply.
        applier.update_service_addresses(
            plan=plan, additions={"streaming": [IPv4Address(OTHER)]}, removals={}
        )
        assert both(AFFECTED, CLIENT, OTHER) == (False, False), "a learned address was not shaped"
        applier.update_service_addresses(
            plan=plan, additions={}, removals={"streaming": [IPv4Address(OTHER)]}
        )
        assert both(AFFECTED, CLIENT, OTHER) == (True, True), "a removed address stayed shaped"

        # 3. A live apply reconciles learned addresses while retaining the counters.
        before = [list_counter(applier, f"service-list streaming {direction}") for direction in ("upload", "download")]
        report = applier.apply(plan=plan, service_addresses={"streaming": [IPv4Address(OTHER)]})
        assert report.counters_preserved, "an address-only change rebuilt the queues"
        assert before == [list_counter(applier, f"service-list streaming {direction}") for direction in ("upload", "download")]
        assert both(AFFECTED, CLIENT, OTHER) == (False, False), "known addresses were lost on apply"
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), (
            "an address that was not known stayed"
        )

        # Helper expiry must remove the physical set entry during a live apply.
        clock = [1000.0]
        helper = HelperService(applier, service_address_clock=lambda: clock[0])
        payload = {"action": "apply", "plan": plan_to_payload(plan)}
        assert helper.handle_payload(payload)["ok"]
        assert helper.handle_payload({
            "action": "service-addresses", "addresses": {"streaming": [LISTED]},
        })["ok"]
        assert both(AFFECTED, CLIENT, LISTED) == (False, False)
        before = [list_counter(applier, f"service-list streaming {direction}") for direction in ("upload", "download")]
        clock[0] += 6 * 3600 + 1
        reply = helper.handle_payload(payload)
        assert reply["ok"] and reply["counters_preserved"], reply
        assert before == [list_counter(applier, f"service-list streaming {direction}") for direction in ("upload", "download")]
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), "an expired address stayed in the kernel"
        assert helper.handle_payload({
            "action": "service-addresses", "addresses": {"streaming": [OTHER]},
        })["ok"]
        assert helper.handle_payload(payload)["counters_preserved"]
        assert both(AFFECTED, CLIENT, OTHER) == (False, False), "a later DNS update was lost"

        # 4. A list replaces the profile of a device, including its outage and website rules.
        device = Device("affected", "Affected", CLIENT, OFFLINE.id, AFFECTED)
        plan = compile_plan(
            devices=(device,),
            profiles={"offline": OFFLINE, "normal": NORMAL},
            service_list_rules=(listed_rule(NORMAL),),
        )
        applier.apply(plan=plan, service_addresses={"streaming": [IPv4Address(LISTED)]})
        assert upload(AFFECTED, CLIENT, OTHER) is False, "the device profile stopped applying"
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), (
            "the list did not replace the device profile"
        )
        harsher = replace(
            plan,
            blocked_device_addresses=(device.ipv4_address,),
            website_filters=(WebsiteFilter(device.ipv4_address, ()),),
        )
        applier.apply(plan=harsher, service_addresses={"streaming": [IPv4Address(LISTED)]})
        assert upload(AFFECTED, CLIENT, OTHER) is False
        assert upload(AFFECTED, CLIENT, LISTED) is True, (
            "a device outage or website rule cut a listed service"
        )

        # 5. A list replaces the general profile, its outage and its website list.
        general = compile_plan(
            devices=(),
            profiles={"offline": OFFLINE, "normal": NORMAL},
            protected_mac_addresses=(PROTECTED,),
            general_profile=GeneralProfileRule("offline"),
            service_list_rules=(listed_rule(NORMAL),),
        )
        addresses = {"streaming": [IPv4Address(LISTED)]}
        for variant in (
            general,
            replace(general, general_profile=GeneralProfileRule("offline", outage_active=True)),
            replace(general, general_profile=GeneralProfileRule("offline", ())),
        ):
            applier.apply(plan=variant, service_addresses=addresses)
            assert both(AFFECTED, NEW_CLIENT, LISTED) == (True, True), (
                "the general profile shaped a listed service"
            )
            assert upload(AFFECTED, NEW_CLIENT, OTHER) is False, (
                "the general profile stopped applying to other traffic"
            )

        applier.apply(plan=general, service_addresses=addresses)
        before = list_counter(applier, "usage-device general-profile download")
        assert download(LISTED, NEW_CLIENT) is True
        assert list_counter(applier, "usage-device general-profile download") > before, (
            "a listed download was left out of the general usage counter"
        )

        # 6a. A list replaces an exhausted quota's fallback profile in both directions.
        quota = ServiceQuotaRule(
            "video",
            "affected",
            device.ipv4_address,
            (IPv4Address(LISTED),),
            0,
            OFFLINE.upload,
            OFFLINE.download,
            True,
        )
        normal_device = Device("affected", "Affected", CLIENT, NORMAL.id, AFFECTED)
        quota_only = compile_plan(
            devices=(normal_device,),
            profiles={"offline": OFFLINE, "normal": NORMAL},
            service_quota_rules=(quota,),
        )
        applier.apply(plan=quota_only)
        assert both(AFFECTED, CLIENT, LISTED) == (False, False), "the quota did not apply"
        with_list = compile_plan(
            devices=(normal_device,),
            profiles={"offline": OFFLINE, "normal": NORMAL},
            service_quota_rules=(quota,),
            service_list_rules=(listed_rule(NORMAL),),
        )
        applier.apply(plan=with_list, service_addresses=addresses)
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), (
            "an exhausted quota beat the list in one direction"
        )

        # 6b. Each list has its own queue.
        two_lists = compile_plan(
            devices=(device,),
            profiles={"offline": OFFLINE, "normal": NORMAL},
            service_list_rules=(
                listed_rule(OFFLINE, "streaming"),
                ServiceListRule("work", ("work.example",), 0, NORMAL.upload, NORMAL.download),
            ),
        )
        applier.apply(
            plan=two_lists,
            service_addresses={
                "streaming": [IPv4Address(LISTED)],
                "work": [IPv4Address(OTHER)],
            },
        )
        assert both(AFFECTED, CLIENT, LISTED) == (False, False), "the first list lost its queue"
        assert both(AFFECTED, CLIENT, OTHER) == (True, True), (
            "the second list used the device profile or the first list"
        )

        # 6. Global access rules still win over a list.
        blocked = compile_plan(
            devices=(),
            profiles={"normal": NORMAL},
            global_blocked_addresses=(IPv4Address(LISTED),),
            service_list_rules=(listed_rule(NORMAL),),
        )
        applier.apply(plan=blocked, service_addresses=addresses)
        assert upload(AFFECTED, CLIENT, LISTED) is False, "a list bypassed a global block"
        assert upload(AFFECTED, CLIENT, OTHER) is True

        # 7. Reset removes the sets together with the rules.
        applier.reset()
        applier.reset()
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), "traffic stayed shaped after reset"
        assert (
            subprocess.run(
                ["nft", "list", "set", "inet", "jittersim", "svc_1"], capture_output=True
            ).returncode
            != 0
        ), "a service set outlived the reset"
        print(
            "Service lists shaped listed addresses in both directions, replaced device and "
            "general profiles and kept global rules and unaffected devices."
        )
    finally:
        client.close()
        remote.close()
        applier.reset()
        run("ip", "link", "del", "jittersim0")
        run("ip", "link", "del", "up0")


if __name__ == "__main__":
    main()
