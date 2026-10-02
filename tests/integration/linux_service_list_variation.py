"""Verify service list rerolls and random outages with real routed packets.

Run it only in a disposable container with NET_ADMIN and NET_RAW capabilities,
for example `docker run --rm --privileged <image> python
tests/integration/linux_service_list_variation.py`. The runtime scheduler builds
each plan from saved settings with an injected clock and the real applier puts it
in the kernel, so the check covers the path from a saved profile to the packets.
"""

import json
import socket
import struct
import subprocess
import time
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, Randomness, ServiceList
from jittersim.config.storage import ConfigRepository
from jittersim.engine.randomizer import reroll_profile, seeded_generator
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.renderer import queue_handle
from jittersim.networking.topology import Topology

PROTECTED = "02:00:00:00:00:01"
AFFECTED = "02:00:00:00:00:02"
REMOTE = "02:00:00:00:00:03"
PI = "192.168.50.1"
LISTED = "198.18.0.2"
OTHER = "198.18.0.3"
LISTED_V6 = "2001:db8:1::2"
CLIENT = "192.168.50.10"
PROTECTED_CLIENT = "192.168.50.11"
NEW_CLIENT = "192.168.50.13"
ETH_P_IP = 0x0800
ETH_P_IPV6 = 0x86DD

NORMAL = Profile("normal", "Normal")
FLAKY = Profile(
    "flaky",
    "Flaky",
    outage_probability=100,
    outage_duration_seconds=5,
    outage_interval_seconds=10,
    random_seed=7,
)


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def mac_bytes(value):
    return bytes.fromhex(value.replace(":", ""))


def frame(source_mac, target_mac, source_ip, target_ip, tag, source_port=32000, target_port=32001):
    payload = tag.encode()
    udp = struct.pack("!HHHH", source_port, target_port, 8 + len(payload), 0) + payload
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


def arrives(receiver, send, tag):
    """Send once and tell whether a frame ending with `tag` reaches `receiver`."""

    receiver.settimeout(0.01)
    try:
        while receiver.recv(4096):
            pass
    except socket.timeout:
        pass
    send()
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        receiver.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            received, info = receiver.recvfrom(4096)
        except socket.timeout:
            return False
        if info[2] != socket.PACKET_OUTGOING and received.endswith(tag.encode()):
            return True
    return False


def queue(interface, handle):
    """Read one queue of the interface with its counters."""

    shown = json.loads(
        subprocess.check_output(["tc", "-s", "-j", "qdisc", "show", "dev", interface], text=True)
    )
    return next((item for item in shown if item.get("handle") == handle), None)


def seed_without_loss_after_one_reroll(profile):
    """Find a seed whose first reroll switches the loss off in both directions."""

    for seed in range(1_000):
        candidate = replace(profile, random_seed=seed)
        rolled = reroll_profile(
            profile=candidate,
            generator=seeded_generator(
                profile=candidate, device_id="servicelist:streaming", roll_number=0
            ),
        )
        if (
            rolled.upload.loss_percent == 0
            and rolled.download.loss_percent == 0
            and rolled.upload.delay_ms
            and rolled.download.delay_ms
        ):
            return candidate, rolled
    raise RuntimeError("no seed switches the loss off")


class KernelHelper:
    """Hand the scheduler's plans to the real applier with the learned addresses."""

    def __init__(self, applier, addresses):
        self.applier = applier
        self.addresses = addresses
        self.plans = []

    def apply(self, *, plan):
        self.applier.apply(plan=plan, service_addresses=self.addresses)
        self.plans.append(plan)
        return {"commands_run": 1}

    def reset(self):
        self.applier.reset()
        return {"commands_run": 1}

    def connected_device_snapshot(self):
        return {
            "association_status_available": True,
            "devices": [
                {"mac_address": AFFECTED, "ipv4_address": CLIENT, "association_verified": True},
                {
                    "mac_address": PROTECTED,
                    "ipv4_address": PROTECTED_CLIENT,
                    "association_verified": True,
                },
            ],
        }


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
    run("ip", "addr", "add", f"{PI}/24", "dev", "jittersim0")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "up0")
    # Containers often start with IPv6 switched off. The browser proxy path can use it.
    for setting in ("all", "default", "up0"):
        Path(f"/proc/sys/net/ipv6/conf/{setting}/disable_ipv6").write_text("0\n")
    run("ip", "-6", "addr", "add", "2001:db8:1::1/64", "dev", "up0", "nodad")
    neighbours = [(LISTED_V6, REMOTE, "up0"), (LISTED, REMOTE, "up0"), (OTHER, REMOTE, "up0")]
    neighbours += [
        (CLIENT, AFFECTED, "jittersim0"),
        (PROTECTED_CLIENT, PROTECTED, "jittersim0"),
        (NEW_CLIENT, AFFECTED, "jittersim0"),
    ]
    for address, mac, interface in neighbours:
        run("ip", "neigh", "replace", address, "lladdr", mac, "nud", "permanent", "dev", interface)
    test_mac = Path("/sys/class/net/jittersim0/address").read_text().strip()
    up_mac = Path("/sys/class/net/up0/address").read_text().strip()
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=Topology("up0", "jittersim0"))
    client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_IP))
    remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_IP))
    remote6 = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_IPV6))
    client.bind(("client0", 0))
    remote.bind(("remote0", 0))
    remote6.bind(("remote0", 0))
    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.bind((PI, 53))
    counter = [0]

    def tag(kind):
        counter[0] += 1
        return f"{kind}-{counter[0]}"

    def upload(source_mac, source_ip, target_ip):
        label = tag(f"up-{source_ip}-{target_ip}")
        packet = frame(source_mac, test_mac, source_ip, target_ip, label)
        return arrives(remote, lambda: client.send(packet), label)

    def download(source_ip, target_ip):
        label = tag(f"down-{source_ip}-{target_ip}")
        packet = frame(REMOTE, up_mac, source_ip, target_ip, label)
        return arrives(client, lambda: remote.send(packet), label)

    def both(source_mac, client_ip, server_ip):
        return upload(source_mac, client_ip, server_ip), download(server_ip, client_ip)

    def pi_dns_works(source_mac, client_ip):
        """A query reaches the Pi's DNS port and the Pi's answer reaches the client."""

        label = tag("dns")
        query = frame(source_mac, test_mac, client_ip, PI, label, 32000, 53)
        dns.settimeout(0.35)
        client.send(query)
        try:
            received, sender = dns.recvfrom(4096)
        except socket.timeout:
            return False, False
        answer = tag("answer")
        return received.endswith(label.encode()), arrives(
            client, lambda: dns.sendto(answer.encode(), (sender[0], sender[1])), answer
        )

    def pi_socket_reaches(target, mark, family=socket.AF_INET):
        """Send from a Pi socket that carries `mark` like a browser proxy tunnel."""

        label = tag(f"pi-{mark}-{target}")
        receiver = remote6 if family == socket.AF_INET6 else remote

        def send():
            with socket.socket(family, socket.SOCK_DGRAM) as sender:
                if mark:
                    sender.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
                sender.sendto(label.encode(), (target, 33000))

        return arrives(receiver, send, label)

    repository = ConfigRepository(":memory:")
    for profile in (NORMAL, FLAKY):
        repository.save_profile(profile)
    repository.save_device(Device("affected", "Affected", CLIENT, NORMAL.id, AFFECTED))
    repository.save_unaffected_device(UnaffectedDevice(PROTECTED))
    def save_list(profile_id):
        repository.save_service_list(
            ServiceList(
                id="streaming",
                name="Streaming",
                profile_id=profile_id,
                hostnames=("streaming.example",),
            )
        )

    save_list("flaky")
    helper = KernelHelper(applier, {"streaming": [IPv4Address(LISTED)]})

    def new_scheduler():
        scheduler = RuntimeScheduler(database_path=":memory:", helper_client=helper)
        scheduler._paused = False
        scheduler._current_payload = {}
        return scheduler

    try:
        # 1. Before the first outage check the list is reachable.
        scheduler = new_scheduler()
        assert scheduler.tick(repository=repository, now=0)
        (rule,) = helper.plans[-1].service_list_rules
        assert rule.outage_active is False
        mark = rule.mark
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), "the list was cut before an outage"

        # 2. A random outage cuts the listed service for every affected client.
        assert scheduler.tick(repository=repository, now=10)
        assert helper.plans[-1].service_list_rules[0].outage_active is True
        assert both(AFFECTED, CLIENT, LISTED) == (False, False), (
            "a list outage left the listed service reachable in a direction"
        )
        assert both(AFFECTED, NEW_CLIENT, LISTED) == (False, False), (
            "a client without a saved device escaped the list outage"
        )
        assert both(PROTECTED, PROTECTED_CLIENT, LISTED) == (True, True), (
            "a list outage touched a protected device"
        )
        assert both(AFFECTED, CLIENT, OTHER) == (True, True), "a list outage cut another site"
        assert pi_dns_works(AFFECTED, CLIENT) == (True, True), (
            "a list outage cut the Pi's DNS for an affected client"
        )
        assert pi_dns_works(PROTECTED, PROTECTED_CLIENT) == (True, True)
        assert pi_socket_reaches(LISTED, mark) is False, (
            "a browser proxy socket of the list reached the service over IPv4"
        )
        assert pi_socket_reaches(LISTED_V6, mark, socket.AF_INET6) is False, (
            "a browser proxy socket of the list reached the service over IPv6"
        )
        assert pi_socket_reaches(LISTED, 0) is True, "the Pi's own traffic was cut"
        assert pi_socket_reaches(LISTED_V6, 0, socket.AF_INET6) is True
        assert pi_socket_reaches(LISTED, 1) is True, "a device's proxy socket was cut"

        # 3. The outage ends after its length and everything returns.
        assert scheduler.tick(repository=repository, now=15)
        assert helper.plans[-1].service_list_rules[0].outage_active is False
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), "the list stayed cut"
        assert pi_socket_reaches(LISTED, mark) is True
        assert pi_socket_reaches(LISTED_V6, mark, socket.AF_INET6) is True

        # 4. Beside the general profile the list outage still cuts only the list.
        repository.save_general_profile("normal")
        scheduler = new_scheduler()
        scheduler.tick(repository=repository, now=100)
        assert scheduler.tick(repository=repository, now=110)
        plan = helper.plans[-1]
        assert plan.general_profile is not None and plan.service_list_rules[0].outage_active
        assert both(AFFECTED, NEW_CLIENT, LISTED) == (False, False), (
            "the general profile let a listed service through its outage"
        )
        assert both(AFFECTED, NEW_CLIENT, OTHER) == (True, True)
        assert both(PROTECTED, PROTECTED_CLIENT, LISTED) == (True, True)
        assert pi_dns_works(AFFECTED, NEW_CLIENT) == (True, True)
        repository.save_general_profile(None)

        # 5. A reroll reaches the kernel by changing the list's queue in place.
        lossy, rolled = seed_without_loss_after_one_reroll(
            Profile(
                "variable",
                "Variable",
                upload=Impairment(
                    delay_ms=5,
                    loss_percent=100,
                    delay_randomness=Randomness(variation_percent=40),
                    loss_randomness=Randomness(enabled_probability=50),
                ),
                download=Impairment(
                    delay_ms=5,
                    loss_percent=100,
                    delay_randomness=Randomness(variation_percent=40),
                    loss_randomness=Randomness(enabled_probability=50),
                ),
                reroll_interval_seconds=60,
            )
        )
        repository.save_profile(lossy)
        save_list("variable")
        scheduler = new_scheduler()
        assert scheduler.tick(repository=repository, now=200)
        mark = helper.plans[-1].service_list_rules[0].mark
        handle = queue_handle(mark, "netem")
        assert both(AFFECTED, CLIENT, LISTED) == (False, False), "the saved values did not apply"
        before = queue("up0", handle)
        assert before is not None and before["kind"] == "netem" and before["drops"] > 0
        assert scheduler.tick(repository=repository, now=260)
        assert helper.plans[-1].service_list_rules[0].upload_impairment == rolled.upload
        assert both(AFFECTED, CLIENT, LISTED) == (True, True), "the rerolled values did not apply"
        after = queue("up0", handle)
        assert after is not None and after["kind"] == "netem"
        assert after["drops"] >= before["drops"], (
            "the reroll rebuilt the list's queue instead of changing it in place"
        )
        assert after["packets"] > 0

        # 6. Reset removes the drops together with the rest.
        applier.reset()
        assert both(AFFECTED, CLIENT, LISTED) == (True, True)
        assert pi_socket_reaches(LISTED, mark) is True
        print(
            "Service list outages cut listed services in both directions and the list's proxy "
            "sockets over IPv4 and IPv6 while the Pi's DNS, other sites and protected devices "
            "kept working. Rerolled values changed the list's queue in place."
        )
    finally:
        dns.close()
        client.close()
        remote.close()
        remote6.close()
        repository.close()
        applier.reset()
        run("ip", "link", "del", "jittersim0")
        run("ip", "link", "del", "up0")


if __name__ == "__main__":
    main()
