"""Verify DNS learning for service lists with real packets in an isolated container.

Run it only in a disposable container with NET_ADMIN and NET_RAW capabilities.
A DNS answer leaves the Pi towards a client. The watcher must read it through
the kernel filter, tell the helper about the address and make later traffic to
that address follow the list's profile. With household DNS rules off, answers
from other DNS servers and answers a client sends to the Pi teach nothing.
"""

import socket
import struct
import tempfile
import threading
import time
from pathlib import Path

from jittersim.config.models import Impairment, Profile
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import ServiceListRule
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.dns_watch import ServiceWatcher, open_dns_socket, watch
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.helper import HelperClientError, HelperService
from jittersim.networking.topology import Topology
from tests.integration.linux_service_list_packets import (
    AFFECTED,
    CLIENT,
    LISTED,
    OTHER,
    REMOTE,
    frame,
    mac_bytes,
    packet_passes,
    run,
)

OFFLINE = Profile(
    "offline", "Offline", upload=Impairment(loss_percent=100), download=Impairment(loss_percent=100)
)
LEARNED = "198.18.0.9"
FORGED = "198.18.0.10"
UNTRUSTED = "198.18.0.11"


def encode_name(value):
    return b"".join(bytes([len(label)]) + label.encode() for label in value.split(".")) + b"\x00"


def answer_frame(source_mac, target_mac, source_ip, target_ip, qname, address):
    """Build an Ethernet frame holding a DNS answer from UDP port 53."""

    message = (
        struct.pack("!HHHHHH", 0x4242, 0x8180, 1, 1, 0, 0)
        + encode_name(qname)
        + struct.pack("!HH", 1, 1)
        + b"\xc0\x0c"
        + struct.pack("!HHIH", 1, 1, 60, 4)
        + socket.inet_aton(address)
    )
    udp = struct.pack("!HHHH", 53, 40000, 8 + len(message), 0) + message
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(udp),
        7,
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


class DirectHelper:
    """Reach the helper service in this process the way the socket client would."""

    def __init__(self, service):
        self.service = service

    def service_addresses(self, addresses):
        response = self.service.handle_payload(
            {
                "action": "service-addresses",
                "addresses": {key: [str(a) for a in value] for key, value in addresses.items()},
            }
        )
        if not response.get("ok"):
            raise HelperClientError(response.get("error", "failed"), reason=response.get("reason"))
        return response["added"]


def wait_until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def main():
    if not Path("/.dockerenv").exists():
        raise RuntimeError("Run only in a disposable container")
    for interface, peer in (("jittersim0", "client0"), ("up0", "remote0")):
        run("ip", "link", "add", interface, "type", "veth", "peer", "name", peer)
        run("ip", "link", "set", interface, "up")
        run("ip", "link", "set", peer, "up", "promisc", "on")
    run("ip", "link", "set", "remote0", "address", REMOTE)
    run("ip", "link", "set", "client0", "address", AFFECTED)
    run("ip", "addr", "add", "192.168.50.1/24", "dev", "jittersim0")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "up0")
    for address in (LISTED, OTHER, LEARNED, FORGED, UNTRUSTED, "198.18.0.53"):
        run("ip", "neigh", "replace", address, "lladdr", REMOTE, "nud", "permanent", "dev", "up0")
    run(
        "ip",
        "neigh",
        "replace",
        CLIENT,
        "lladdr",
        AFFECTED,
        "nud",
        "permanent",
        "dev",
        "jittersim0",
    )
    test_mac = Path("/sys/class/net/jittersim0/address").read_text().strip()
    up_mac = Path("/sys/class/net/up0/address").read_text().strip()
    topology = Topology("up0", "jittersim0")
    with tempfile.TemporaryDirectory() as directory:
        service = HelperService(
            NetworkApplier(executor=SubprocessExecutor(), topology=topology),
            active_plan_path=Path(directory) / "active-plan.json",
        )
        plan = compile_plan(
            devices=(),
            profiles={"offline": OFFLINE},
            service_list_rules=(
                ServiceListRule(
                    "streaming", ("streaming.example",), 0, OFFLINE.upload, OFFLINE.download
                ),
            ),
        )
        assert service.handle_payload({"action": "apply", "plan": plan_to_payload(plan)})["ok"]
        watcher = ServiceWatcher(
            helper_client=DirectHelper(service), applied_plan=lambda: plan_to_payload(plan)
        )
        stop = threading.Event()
        worker = threading.Thread(
            target=watch, args=(watcher, lambda: open_dns_socket("jittersim0"), stop), daemon=True
        )
        client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
        remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
        resolver = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
        resolver.bind(("jittersim0", 0))
        client.bind(("client0", 0))
        remote.bind(("remote0", 0))
        counter = [0]

        def to_server(address):
            counter[0] += 1
            tag = f"probe-{counter[0]}"
            return packet_passes(
                client, remote, frame(AFFECTED, test_mac, CLIENT, address, tag), tag
            )

        try:
            worker.start()
            time.sleep(0.5)
            assert to_server(LEARNED), "unlisted traffic was shaped before any learning"

            # With household DNS rules off, a forwarded remote answer is not trusted.
            remote.send(
                answer_frame(
                    REMOTE, up_mac, "198.18.0.53", CLIENT, "cdn.streaming.example", UNTRUSTED
                )
            )
            time.sleep(1.0)
            assert to_server(UNTRUSTED), "an untrusted remote DNS answer taught the watcher an address"

            # The Pi resolver sends a real outgoing frame through the watched interface.
            resolver.send(
                answer_frame(
                    test_mac, AFFECTED, "192.168.50.1", CLIENT, "cdn.streaming.example", LEARNED
                )
            )
            assert wait_until(lambda: not to_server(LEARNED)), (
                "an address from a DNS answer was not shaped"
            )

            # Unrelated answers and answers a client sends to the Pi teach nothing.
            resolver.send(
                answer_frame(test_mac, AFFECTED, "192.168.50.1", CLIENT, "unrelated.example", OTHER)
            )
            client.send(
                answer_frame(
                    AFFECTED, test_mac, CLIENT, "192.168.50.1", "streaming.example", FORGED
                )
            )
            time.sleep(1.0)
            assert to_server(OTHER), "an unlisted name taught the watcher an address"
            assert to_server(FORGED), "a client forged a listed address"
            assert to_server(UNTRUSTED), "an untrusted remote DNS answer was learned later"
            assert not to_server(LEARNED)
        finally:
            stop.set()
            worker.join(timeout=5)
            client.close()
            remote.close()
            resolver.close()
            service.handle_payload({"action": "reset"})
            run("ip", "link", "del", "jittersim0")
            run("ip", "link", "del", "up0")
    print(
        "Trusted DNS answers taught listed addresses; remote, unlisted and client-sent answers did not."
    )


if __name__ == "__main__":
    main()
