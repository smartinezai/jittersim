"""Exercise real DNS, device isolation and automatic bypass in a disposable container."""

import grp
import os
import selectors
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from jittersim.config.adblocking import ActiveBlocking, domain_bytes
from jittersim.config.models import SiteBlock, WebsitePolicy
from jittersim.engine.access import AccessClient, AccessPlan
from jittersim.networking.adblocking import (
    AdController,
    AdRouting,
    AdStore,
    atomic_write,
    write_json,
)

ALICE = "02:00:00:00:00:01"
BOB = "02:00:00:00:00:02"
QUERY_HISTORY_MARKER = "private-browsing-marker.content.test"

CLIENT = r"""
import socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2)
s.bind(("192.168.50.10", 40999))
for line in sys.stdin:
    protocol, domain, kind = line.strip().split()
    labels = b"".join(bytes([len(p)]) + p.encode() for p in domain.split(".")) + b"\0"
    query = struct.pack("!HHHHHH", 1, 256, 1, 0, 0, 0) + labels + struct.pack("!HH", int(kind), 1)
    if protocol == "tcp":
        with socket.create_connection(("192.168.50.1", 53), timeout=2) as t:
            t.sendall(struct.pack("!H", len(query)) + query)
            header = t.recv(2)
            size = struct.unpack("!H", header)[0]
            data = b""
            while len(data) < size:
                part = t.recv(size - len(data))
                if not part:
                    raise RuntimeError("incomplete DNS reply")
                data += part
    else:
        s.sendto(query, ("192.168.50.1", 53))
        data, _ = s.recvfrom(4096)
    print(struct.unpack("!H", data[2:4])[0] & 15, flush=True)
"""


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)


def main():
    if os.geteuid() != 0 or not Path("/.dockerenv").exists():
        raise RuntimeError("Run as root only in a disposable Linux container.")
    try:
        grp.getgrnam("jittersim")
    except KeyError:
        run("groupadd", "--system", "jittersim")
    run("useradd", "--system", "--gid", "jittersim", "jittersim")
    group_id = grp.getgrnam("jittersim").gr_gid
    run("ip", "link", "add", "ads0", "type", "veth", "peer", "name", "adsc0")
    run("ip", "netns", "add", "ads-client")
    run("ip", "link", "set", "adsc0", "netns", "ads-client")
    run("ip", "addr", "add", "192.168.50.1/24", "dev", "ads0")
    run("ip", "link", "set", "ads0", "up")
    run("ip", "link", "set", "lo", "up")
    run("ip", "-n", "ads-client", "addr", "add", "192.168.50.10/24", "dev", "adsc0")
    run("ip", "-n", "ads-client", "link", "set", "adsc0", "up")
    run("ip", "-n", "ads-client", "link", "set", "lo", "up")

    def identity(mac):
        run("ip", "-n", "ads-client", "link", "set", "adsc0", "address", mac)
        run("ip", "neigh", "flush", "dev", "ads0")

    identity(ALICE)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        ordinary = root / "ordinary.conf"
        ordinary.write_text(
            "port=53\nlisten-address=192.168.50.1\nbind-interfaces\nno-resolv\nno-hosts\n"
            "address=/advert.test/198.18.0.2\naddress=/content.test/198.18.0.3\n"
            "log-facility=/dev/null\n"
        )
        upstream = subprocess.Popen(
            ["dnsmasq", "--keep-in-foreground", "--conf-file=" + str(ordinary)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        store = AdStore(
            AdRouting("ads0"),
            path=root / "policy.json",
            lock_path=root / "lock",
            active_path=root / "active.json",
            status_path=root / "status.json",
            group_id=group_id,
        )
        store.routing.initialize()
        store.configure([ALICE], [])
        atomic_write(root / "domains", domain_bytes(("advert.test",)), group_id)
        write_json(root / "info", {"updated_at": int(time.time()), "domain_count": 1}, group_id)
        item = AdController(
            store,
            domains_path=root / "domains",
            info_path=root / "info",
            config_path=root / "filtered.conf",
            access_path=root / "access-policy.json",
        )
        item.update_request = store.read()["update_request"]
        item.next_fetch = time.monotonic() + 86400
        active = ActiveBlocking(store.active_path, root / "domains")
        client = subprocess.Popen(
            ["ip", "netns", "exec", "ads-client", "python", "-u", "-c", CLIENT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

        def query(expected, protocol="udp", domain="advert.test", kind=1):
            client.stdin.write(f"{protocol} {domain} {kind}\n")
            client.stdin.flush()
            with selectors.DefaultSelector() as ready:
                ready.register(client.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=4), "DNS client did not answer within its deadline"
            result = client.stdout.readline().strip()
            assert result == str(expected), (protocol, domain, kind, expected, result)

        def activate():
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                item.tick()
                if store.status()["ready"]:
                    return
                time.sleep(0.1)
            raise AssertionError("DNS filtering did not become ready")

        try:
            time.sleep(0.2)
            query(0)
            activate()
            for protocol in ("udp", "tcp"):
                for kind in (1, 28, 16):
                    query(3, protocol, kind=kind)
                query(0, protocol, domain="content.test")
            assert active.blocks(ALICE, "advert.test")
            assert not active.blocks(BOB, "advert.test")
            identity(BOB)
            query(0)  # same IP and UDP source port with a different live MAC
            identity(ALICE)
            query(3)
            household = AccessPlan(
                blocks=(
                    SiteBlock("global", "Global", ("global.content.test",)),
                    SiteBlock("child", "Child", ("child.content.test",), device_id="child"),
                ),
                clients=(AccessClient("child", "192.168.50.10", ALICE, ("child",)),),
            )
            write_json(item.access_path, household.payload(), group_id)
            store.configure([ALICE], ["global.content.test", "child.content.test"])
            activate()
            for protocol in ("udp", "tcp"):
                query(3, protocol, domain="global.content.test")
                query(3, protocol, domain="child.content.test")
                query(0, protocol, domain="content.test")
                query(3, protocol)
            strict = replace(
                household, websites=WebsitePolicy("allow_only", ("content.test", "advert.test"), ())
            )
            write_json(item.access_path, strict.payload(), group_id)
            activate()
            query(3, domain="outside.test")
            query(0, domain="content.test")
            query(3)  # Household permission does not override the shared ad tree.
            write_json(item.access_path, AccessPlan().payload(), group_id)
            store.configure([ALICE], [])
            activate()
            query(0, domain=QUERY_HISTORY_MARKER)
            child_status = Path(f"/proc/{item.child.pid}/status").read_text()
            uid_line = next(line for line in child_status.splitlines() if line.startswith("Uid:"))
            assert "0" not in uid_line.split()[1:], uid_line
            for path in root.iterdir():
                if path.is_file():
                    assert QUERY_HISTORY_MARKER.encode() not in path.read_bytes(), path
            # Controller stalls while DNS remains alive. Both paths expire independently.
            time.sleep(5.2)
            query(0)
            query(0, "tcp")
            assert not active.enabled()
            activate()
            query(3)
            # Killing DNS must also recover on the existing UDP socket.
            item.child.kill()
            item.child.wait(timeout=2)
            time.sleep(5.2)
            query(0)
            query(0, "tcp")
            activate()
            store.configure([ALICE], ["good.advert.test"])
            activate()
            query(0, domain="good.advert.test")
            query(3)
            store.pause()
            query(0)
            assert store.read()["mac_addresses"] == (ALICE,)
            assert not active.enabled()
            store.resume()
            activate()
            query(3)
            store.configure([], [])
            query(0)
            assert not active.enabled()
            print(
                "PASS: UDP, TCP, DNS record types, live MAC isolation, "
                "allowances, pause and removal."
            )
            print(
                "PASS: DNS death and controller stall restore ordinary DNS on the same UDP socket."
            )
            print("PASS: DNS runs without root identity and no browsing history is written.")
            print(
                "PASS: Household global, device and allow-only rules take priority over ad allowances."
            )
        finally:
            client.stdin.close()
            client.wait(timeout=4)
            item.close()
            upstream.terminate()
            upstream.wait(timeout=3)
            run("ip", "netns", "del", "ads-client")
            # Removing the namespace can already remove both ends of the pair.
            interfaces = run("ip", "-j", "link", "show").stdout
            if '"ifname":"ads0"' in interfaces.replace(" ", ""):
                run("ip", "link", "del", "ads0")


if __name__ == "__main__":
    main()
