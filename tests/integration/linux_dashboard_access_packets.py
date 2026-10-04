"""Prove local dashboard connections survive loss and client blocks on Linux."""

import socket
import struct
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from linux_exemption_packets import frame, mac_bytes, packet_passes, run

from jittersim.config.models import Device, Impairment, MacPolicy, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.topology import Topology

CLIENT = "02:00:00:00:00:01"
REMOTE = "02:00:00:00:00:02"
CLIENT_IP = "192.168.50.184"


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!" + "H" * (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def tcp_frame(source_mac, target_mac, source_ip, target_ip, source_port, target_port, sequence):
    tcp = struct.pack("!HHLLBBHHH", source_port, target_port, sequence, 0, 5 << 4, 2, 8192, 0, 0)
    pseudo = socket.inet_aton(source_ip) + socket.inet_aton(target_ip) + struct.pack("!BBH", 0, 6, len(tcp))
    tcp = tcp[:16] + struct.pack("!H", checksum(pseudo + tcp)) + tcp[18:]
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, sequence & 0xffff, 0, 64, 6, 0,
                     socket.inet_aton(source_ip), socket.inet_aton(target_ip))
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    return mac_bytes(target_mac) + mac_bytes(source_mac) + b"\x08\x00" + ip + tcp


def connection_replies(client, test_mac, port, sequence):
    client.settimeout(0.01)
    try:
        while client.recv(2048):
            pass
    except socket.timeout:
        pass
    client.send(tcp_frame(CLIENT, test_mac, CLIENT_IP, "192.168.50.1", 40000 + sequence,
                          port, sequence))
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        client.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            packet, info = client.recvfrom(2048)
        except socket.timeout:
            return False
        if info[2] == socket.PACKET_OUTGOING or len(packet) < 54 or packet[23] != 6:
            continue
        offset = 14 + (packet[14] & 15) * 4
        source, destination, _, ack = struct.unpack("!HHLL", packet[offset:offset + 12])
        if source == port and destination == 40000 + sequence and ack == sequence + 1:
            return packet[offset + 13] & 0x12 == 0x12
    return False


def main():
    if not Path("/.dockerenv").exists():
        raise RuntimeError("Run only in a disposable container without host network access")
    for interface, peer in (("test0", "client0"), ("up0", "remote0")):
        run("ip", "link", "add", interface, "type", "veth", "peer", "name", peer)
        run("ip", "link", "set", interface, "up")
        run("ip", "link", "set", peer, "up")
    run("ip", "link", "set", "client0", "address", CLIENT)
    run("ip", "link", "set", "remote0", "address", REMOTE)
    run("ip", "addr", "add", "192.168.50.1/24", "dev", "test0")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "up0")
    run("ip", "addr", "add", "192.168.0.1/24", "dev", "up0")
    for address, interface, mac in ((CLIENT_IP, "test0", CLIENT),
                                    ("198.18.0.2", "up0", REMOTE),
                                    ("192.168.0.2", "up0", REMOTE)):
        run("ip", "neigh", "replace", address, "lladdr", mac, "nud", "permanent", "dev", interface)
    test_mac = Path("/sys/class/net/test0/address").read_text().strip()
    up_mac = Path("/sys/class/net/up0/address").read_text().strip()
    firewall = Path("deploy/raspberry-pi/jittersim-control-firewall.nft").read_text()
    firewall = firewall.replace("@TEST_INTERFACE@", "test0").replace("@UPLINK_INTERFACE@", "up0")
    subprocess.run(["nft", "-f", "-"], input=firewall, text=True, check=True)
    applier = NetworkApplier(executor=SubprocessExecutor(), topology=Topology("up0", "test0"))
    profile = Profile("offline", "Offline", upload=Impairment(loss_percent=100),
                      download=Impairment(loss_percent=100))
    device = Device("mac", "Mac", CLIENT_IP, profile.id, CLIENT)
    plan = compile_plan(devices=(device,), profiles={profile.id: profile})
    client = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    remote = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    client.bind(("client0", 0))
    remote.bind(("remote0", 0))
    listeners = []
    try:
        for port in (8080, 8081, 22):
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", port))
            listener.listen(128)
            listeners.append(listener)
        applier.apply(plan=plan)
        assert connection_replies(client, test_mac, 8080, 1), "Loss cut off dashboard replies"
        assert not connection_replies(client, test_mac, 8081, 2), "Other local traffic escaped loss"
        assert not connection_replies(client, test_mac, 22, 3), "SSH became reachable"
        assert not packet_passes(client, remote,
                                frame(CLIENT, test_mac, CLIENT_IP, "198.18.0.2", "internet-loss"),
                                "internet-loss")
        # Port 8080 on a forwarded server must not receive the local dashboard exception.
        packet = tcp_frame(REMOTE, up_mac, "198.18.0.2", CLIENT_IP, 8080, 45000, 12345)
        remote.send(packet)
        client.settimeout(0.3)
        try:
            while True:
                received, info = client.recvfrom(2048)
                assert not (info[2] != socket.PACKET_OUTGOING and received.endswith(packet[-20:])), \
                    "Forwarded port 8080 escaped the download profile"
        except socket.timeout:
            pass
        blocked = replace(plan, blocked_device_addresses=(device.ipv4_address,),
                          mac_policy=MacPolicy(denylist=(CLIENT,)))
        applier.apply(plan=blocked)
        assert connection_replies(client, test_mac, 8080, 4), "Client blocks cut off sign-in"
        assert not connection_replies(client, test_mac, 22, 5)
        applier.apply(plan=replace(plan, mac_policy=MacPolicy(mode="allowlist", allowlist=(REMOTE,))))
        assert connection_replies(client, test_mac, 8080, 6), "Allowlist cut off sign-in"
        for sequence in (7, 8):
            applier.reset()
            assert connection_replies(client, test_mac, 8080, sequence), "Reset cut off sign-in"
        assert not packet_passes(client, remote,
                                frame(CLIENT, test_mac, CLIENT_IP, "192.168.0.2", "private-upstream"),
                                "private-upstream"), "Router private access became reachable"
        assert not connection_replies(client, test_mac, 22, 9)
        print("Dashboard packet acceptance passed through full loss, blocks, reapply and reset.")
    finally:
        applier.reset()
        for listener in listeners:
            listener.close()
        client.close()
        remote.close()
        run("nft", "delete", "table", "inet", "jittersim_control")
        for interface in ("test0", "up0"):
            run("ip", "link", "del", interface)


if __name__ == "__main__":
    main()
