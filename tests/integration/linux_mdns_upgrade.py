"""Check obsolete-rule cleanup and current mDNS isolation using real packets."""

import json
import socket
import subprocess
import sys
from pathlib import Path

from linux_exemption_packets import frame

from jittersim.engine.compiler import compile_plan
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.topology import Topology


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs)


def nft(script):
    run("nft", "-f", "-", input=script, text=True)


def seed_old_rules():
    nft('''add table inet jittersim_access
add chain inet jittersim_access input { type filter hook input priority -250; policy accept; }
add rule inet jittersim_access input iifname != "mdns-t0" iifname != "lo" meta l4proto { tcp, udp } th dport 5353 drop
add table netdev jittersim_access
''')


def tables():
    data = json.loads(run("nft", "-j", "list", "tables", text=True).stdout)
    return {(entry["table"]["family"], entry["table"]["name"])
            for entry in data["nftables"] if "table" in entry}


def assert_cleanup():
    found = tables()
    assert ("inet", "jittersim_access") not in found
    assert ("netdev", "jittersim_access") not in found
    assert ("inet", "jittersim_control") in found
    assert ("inet", "mdns_foreign_guard") in found


def main():
    if not Path("/.dockerenv").exists():
        if Path("/proc/self/ns/net").stat().st_ino == Path("/proc/1/ns/net").stat().st_ino:
            raise RuntimeError("Use a separate empty network namespace.")
        if {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}:
            raise RuntimeError("The check namespace must initially contain only loopback.")
    names = (("mdns-m0", "mdns-m1", "192.0.2.1", "02:00:00:00:01:01", "02:00:00:00:01:02"),
             ("mdns-t0", "mdns-t1", "198.51.100.1", "02:00:00:00:02:01", "02:00:00:00:02:02"))
    if any(name in {p.name for p in Path("/sys/class/net").iterdir()} for row in names for name in row[:2]):
        raise RuntimeError("Check interfaces already exist.")
    if any(name in tables() for name in (("inet", "jittersim"), ("netdev", "jittersim_download"),
                                       ("inet", "jittersim_control"), ("inet", "mdns_foreign_guard"),
                                       ("inet", "jittersim_access"), ("netdev", "jittersim_access"))):
        raise RuntimeError("Use a clean namespace without project or fixture tables.")
    created = []
    senders = []
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("0.0.0.0", 5353))
    receiver.settimeout(0.25)
    try:
        for local, peer, address, local_mac, peer_mac in names:
            run("ip", "link", "add", local, "type", "veth", "peer", "name", peer)
            created.append(local)
            for interface, mac in ((local, local_mac), (peer, peer_mac)):
                run("ip", "link", "set", interface, "address", mac)
                run("ip", "link", "set", interface, "up")
            run("ip", "addr", "add", address + "/24", "dev", local)
            sender = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
            sender.bind((peer, 0))
            senders.append(sender)

        firewall = (Path("deploy/raspberry-pi/jittersim-control-firewall.nft").read_text()
                    .replace("@UPLINK_INTERFACE@", "mdns-m0").replace("@TEST_INTERFACE@", "mdns-t0"))
        nft(firewall)
        nft("add table inet mdns_foreign_guard\n")
        seed_old_rules()

        def reaches_responder(index):
            _, _, target_ip, target_mac, source_mac = names[index]
            source_ip = "192.0.2.2" if index == 0 else "198.51.100.2"
            tag = "jittersim-name-query"
            packet = bytearray(frame(source_mac, target_mac, source_ip, target_ip, tag))
            packet[34:38] = b"\x14\xe9\x14\xe9"  # UDP source and destination port 5353
            senders[index].send(packet)
            try:
                return receiver.recv(2048) == tag.encode()
            except socket.timeout:
                return False

        assert not reaches_responder(0), "The old rule must reproduce the management failure."
        applier = NetworkApplier(executor=SubprocessExecutor(),
                                 topology=Topology(uplink_interface="mdns-m0", test_interface="mdns-t0"))
        applier.apply(plan=compile_plan(devices=(), profiles={}))
        assert_cleanup()
        assert reaches_responder(0), "Management queries must reach the responder after apply."
        assert not reaches_responder(1), "Test-network mDNS input must remain blocked."
        try:
            receiver.sendto(b"jittersim-announcement", ("198.51.100.2", 5353))
        except PermissionError:
            pass
        control = json.loads(run("nft", "-j", "list", "table", "inet", "jittersim_control", text=True).stdout)
        blocked = {}
        for entry in control["nftables"]:
            rule = entry.get("rule", {})
            if "comment" in rule:
                blocked[rule["comment"]] = sum(part.get("counter", {}).get("packets", 0) for part in rule["expr"])
        assert blocked["deny mDNS queries from test Wi-Fi"] > 0
        assert blocked["deny mDNS announcements on test Wi-Fi"] > 0
        seed_old_rules()
        applier.reset()
        applier.reset()
        assert_cleanup()
        assert reaches_responder(0), "Management queries must work after repeated reset."
        seed_old_rules()
        run(sys.executable, "-m", "jittersim.networking.legacy")
        assert_cleanup()
        assert reaches_responder(0), "Installer cleanup must work without runtime state."
    finally:
        receiver.close()
        for sender in senders:
            sender.close()
        for family, name in (("inet", "jittersim"), ("netdev", "jittersim_download"),
                             ("inet", "jittersim_access"), ("netdev", "jittersim_access"),
                             ("inet", "jittersim_control"), ("inet", "mdns_foreign_guard")):
            subprocess.run(["nft", "delete", "table", family, name], check=False, stderr=subprocess.DEVNULL)
        for name in created:
            subprocess.run(["ip", "link", "del", name], check=False)
    print("Management name queries pass after apply, repeated reset and installer cleanup.")
    print("Test mDNS input and output remain blocked. Foreign tables are preserved.")


if __name__ == "__main__":
    main()
