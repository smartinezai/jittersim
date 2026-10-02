"""Verify the impact check's queue counters with real routed packets in an isolated container.

Run it only in a disposable container with NET_ADMIN and NET_RAW capabilities. A profile with
20% loss in both directions is applied. Thousands of frames cross the queues and the kernel
counters that the dashboard reads must show about one fifth of them dropped. A plan with the
most shaped marks is then joined against the real `tc -j` output. tc prints a class minor
number in hexadecimal so the queues of marks 9 and above sit under `10:a` and `10:10`.
"""

import json
import socket
import struct
import subprocess
import time
from pathlib import Path

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import MAX_SHAPED_MARKS_PER_PLAN, compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.helper import HelperClient, HelperService
from jittersim.networking.renderer import queue_handle, queue_parent
from jittersim.networking.topology import Topology

CLIENT_MAC = "02:00:00:00:00:02"
REMOTE_MAC = "02:00:00:00:00:03"
CLIENT_IP = "192.168.50.10"
REMOTE_IP = "198.18.0.2"
FRAMES = 2000
LOSS_PERCENT = 20


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


def drain(receiver):
    receiver.settimeout(0.001)
    try:
        while receiver.recv(2048):
            pass
    except socket.timeout:
        pass


def send_frames(sender, receiver, packet):
    """Send the same frame many times and keep the other end empty."""

    for index in range(FRAMES):
        sender.send(packet)
        if index % 100 == 99:
            drain(receiver)
            time.sleep(0.01)
    time.sleep(0.3)
    drain(receiver)


def project_queue(stats, interface, mark):
    """Return the one kernel queue the project installed for this mark."""

    handle, parent = queue_handle(mark), queue_parent(mark)
    matches = [
        item
        for item in stats[interface]
        if item.get("handle") == handle and item.get("parent") == parent
    ]
    assert len(matches) == 1, (interface, handle, parent, stats[interface])
    return matches[0]


class DirectClient(HelperClient):
    """A helper client that asks a service in this process instead of a socket."""

    def __init__(self, service):
        super().__init__(socket_path=Path("unused.sock"))
        self.service = service

    def _request(self, payload, *, timeout_seconds=None):
        return self.service.handle_payload(payload)


def check_one_direction(name, before, after, expected_kind="netem"):
    assert after["kind"] == expected_kind, (name, "expected a netem queue", after)
    packets = after["packets"] - before["packets"]
    drops = after["drops"] - before["drops"]
    offered = packets + drops
    assert drops > 0, (name, "drops did not rise", before, after)
    assert offered >= FRAMES * 0.75, (name, "too few frames reached the queue", offered)
    share = drops / offered
    assert 0.12 <= share <= 0.28, (name, "drop share is not about 20%", share, packets, drops)
    return packets, drops, share


def many_queue_plan():
    """One device per shaped mark with its own delay in each direction."""

    profiles = {}
    devices = []
    for mark in range(1, MAX_SHAPED_MARKS_PER_PLAN + 1):
        profiles[f"p{mark:02d}"] = Profile(
            f"p{mark:02d}",
            f"Profile {mark:02d}",
            upload=Impairment(delay_ms=100 + mark),
            download=Impairment(delay_ms=200 + mark),
        )
        devices.append(
            Device(f"d{mark:02d}", f"Device {mark:02d}", f"192.168.50.{10 + mark}", f"p{mark:02d}")
        )
    return compile_plan(devices=tuple(devices), profiles=profiles)


def check_many_queues(service, applier):
    """Join a plan with every shaped mark and check each entry against its own kernel queue."""

    applier.reset()
    plan = many_queue_plan()
    response = service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
    assert response["ok"], response
    response = service.handle_payload({"action": "queue-stats"})
    assert response["ok"], response
    joined = {(item["device_id"], item["direction"]): item for item in response["queues"]}
    expected = {(queue.device_id, queue.direction.value) for queue in plan.queues}
    assert set(joined) == expected, ("queues missing from the join", expected - set(joined))
    raw = applier.queue_stats()
    for (device_id, direction), item in joined.items():
        mark = int(device_id[1:])
        delay = (100 if direction == "upload" else 200) + mark
        kernel = [
            entry for entry in raw[item["interface"]] if entry.get("handle") == queue_handle(mark)
        ]
        assert len(kernel) == 1, (device_id, direction, kernel)
        assert kernel[0]["parent"] == queue_parent(mark), (device_id, direction, kernel[0])
        assert round(kernel[0]["options"]["delay"]["delay"] * 1000) == delay, kernel[0]
        assert item["impairment"]["delay_ms"] == delay, item
        assert item["packets"] == kernel[0]["packets"], (item, kernel[0])
    # Spelled out because a decimal parent for these marks names classes that do not exist.
    parents = {
        item["handle"]: [
            entry["parent"] for entry in raw[item["interface"]] if entry.get("handle") == item["handle"]
        ]
        for item in joined.values()
    }
    assert parents[queue_handle(9)] == ["10:a"], parents[queue_handle(9)]
    assert parents[queue_handle(15)] == ["10:10"], parents[queue_handle(15)]
    assert len(joined) == 2 * MAX_SHAPED_MARKS_PER_PLAN
    return len(joined)


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
    run("ip", "link", "set", "remote0", "address", REMOTE_MAC)
    run("ip", "link", "set", "client0", "address", CLIENT_MAC)
    run("ip", "addr", "add", "192.168.50.1/24", "dev", "jittersim0")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "up0")
    run("ip", "neigh", "replace", REMOTE_IP, "lladdr", REMOTE_MAC, "nud", "permanent", "dev", "up0")
    run(
        "ip", "neigh", "replace", CLIENT_IP, "lladdr", CLIENT_MAC,
        "nud", "permanent", "dev", "jittersim0",
    )
    test_mac = Path("/sys/class/net/jittersim0/address").read_text().strip()
    up_mac = Path("/sys/class/net/up0/address").read_text().strip()
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=Topology("up0", "jittersim0"))
    service = HelperService(applier)
    profile = Profile(
        "lossy",
        "Lossy",
        upload=Impairment(loss_percent=LOSS_PERCENT),
        download=Impairment(loss_percent=LOSS_PERCENT),
    )
    device = Device("lab", "Lab", CLIENT_IP, profile.id, CLIENT_MAC)
    plan = compile_plan(devices=(device,), profiles={profile.id: profile})
    marks = {queue.direction.value: queue.mark for queue in plan.queues}
    client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    client.bind(("client0", 0))
    remote.bind(("remote0", 0))
    try:
        response = service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})
        assert response["ok"], response
        before = applier.queue_stats()
        upload_before = project_queue(before, "up0", marks["upload"])
        download_before = project_queue(before, "jittersim0", marks["download"])
        assert upload_before["packets"] + upload_before["drops"] == 0, upload_before
        assert download_before["packets"] + download_before["drops"] == 0, download_before

        send_frames(
            client, remote, frame(CLIENT_MAC, test_mac, CLIENT_IP, REMOTE_IP, "queue-stats-up")
        )
        send_frames(
            remote, client, frame(REMOTE_MAC, up_mac, REMOTE_IP, CLIENT_IP, "queue-stats-down")
        )

        after = applier.queue_stats()
        upload = check_one_direction(
            "upload", upload_before, project_queue(after, "up0", marks["upload"])
        )
        download = check_one_direction(
            "download", download_before, project_queue(after, "jittersim0", marks["download"])
        )

        # The same counters reach the dashboard through the helper join and the client checks.
        response = service.handle_payload({"action": "queue-stats"})
        assert response["ok"] and response["action"] == "queue-stats", response
        joined = {(item["device_id"], item["direction"]): item for item in response["queues"]}
        assert set(joined) == {("lab", "upload"), ("lab", "download")}, joined
        assert joined[("lab", "upload")]["interface"] == "up0", joined
        assert joined[("lab", "download")]["interface"] == "jittersim0", joined
        for item in joined.values():
            assert item["kind"] == "netem", item
            assert item["impairment"]["loss_percent"] == LOSS_PERCENT, item
            assert item["drops"] > 0 and item["packets"] > 0, item
        validated = DirectClient(service).queue_stats()
        assert {(item["device_id"], item["direction"]) for item in validated} == set(joined)

        joined_count = check_many_queues(service, applier)
        print(
            "upload dropped %d of %d (%.1f%%) and download dropped %d of %d (%.1f%%)"
            % (
                upload[1], upload[0] + upload[1], upload[2] * 100,
                download[1], download[0] + download[1], download[2] * 100,
            )
        )
        print("%d queues joined with the real tc output up to mark %d"
              % (joined_count, MAX_SHAPED_MARKS_PER_PLAN))
        print("OK queue counters match the configured loss")
    finally:
        client.close()
        remote.close()
        applier.reset()
        run("ip", "link", "del", "jittersim0")
        run("ip", "link", "del", "up0")


if __name__ == "__main__":
    main()
