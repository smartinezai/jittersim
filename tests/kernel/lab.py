"""A router, a Pi and a test client in network namespaces, for real-kernel checks.

The namespaces are created and deleted here, so the host's own interfaces are
never touched. Everything needs root on Linux.
"""

import os
import subprocess
from dataclasses import replace

from jittersim.networking.executor import CommandExecutionError, SubprocessExecutor
from jittersim.networking.topology import Topology

PI, ROUTER, CLIENT, OTHER_CLIENT = (
    f"jt{os.getpid()}-{role}" for role in ("pi", "router", "client", "other")
)
TOPOLOGY = Topology(uplink_interface="eth0", test_interface="wlan0")
# The test client's first address, which it uses by default.
CLIENT_ADDRESS = "192.168.50.10"
CLIENT_MAC = "02:00:00:00:50:0a"
# A second test client with its own MAC address on the same Wi-Fi link.
OTHER_CLIENT_MAC = "02:00:00:00:50:14"
OTHER_CLIENT_ADDRESSES = ("192.168.50.20", "192.168.50.21")
ROUTER_ADDRESS = "192.168.0.1"


def run(*argv: str, check: bool = True, stdin: str = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, check=check)


def run_in(namespace: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return run("ip", "netns", "exec", namespace, *argv, check=check)


class NamespaceExecutor(SubprocessExecutor):
    """Run every command inside the simulated Pi's network namespace."""

    def __init__(self, namespace: str, *, fail_on_index: int = None) -> None:
        super().__init__()
        self.namespace = namespace
        self.fail_on_index = fail_on_index
        self.count = 0

    def run(self, command):
        index = self.count
        self.count += 1
        if index == self.fail_on_index:
            raise CommandExecutionError(f"simulated failure: {command.purpose}")
        wrapped = replace(
            command,
            executable="ip",
            arguments=("netns", "exec", self.namespace, command.executable, *command.arguments),
        )
        return super().run(wrapped)


def create_lab() -> None:
    """Connect a router, a Pi and two test clients like the real lab."""

    for namespace in (PI, ROUTER, CLIENT, OTHER_CLIENT):
        run("ip", "netns", "add", namespace)
    run("ip", "-n", PI, "link", "add", "pi-up", "type", "veth", "peer", "name", "up0",
        "netns", ROUTER)
    run("ip", "-n", PI, "link", "set", "pi-up", "name", "eth0")
    run("ip", "-n", PI, "link", "add", "pi-wl", "type", "veth", "peer", "name", "c0",
        "netns", CLIENT)
    run("ip", "-n", PI, "link", "set", "pi-wl", "name", "wlan0")
    run("ip", "-n", CLIENT, "link", "set", "c0", "address", CLIENT_MAC)
    # A macvlan on the client's link sends frames with its own MAC address, like
    # a second device on the Pi's Wi-Fi.
    run("ip", "-n", CLIENT, "link", "add", "link", "c0", "name", "c1", "address",
        OTHER_CLIENT_MAC, "type", "macvlan", "mode", "bridge")
    run("ip", "-n", CLIENT, "link", "set", "c1", "netns", OTHER_CLIENT)
    run("ip", "-n", ROUTER, "addr", "add", f"{ROUTER_ADDRESS}/24", "dev", "up0")
    run("ip", "-n", PI, "addr", "add", "192.168.0.2/24", "dev", "eth0")
    run("ip", "-n", PI, "addr", "add", "192.168.50.1/24", "dev", "wlan0")
    for octet in [10, 11, 12] + list(range(100, 115)):
        run("ip", "-n", CLIENT, "addr", "add", f"192.168.50.{octet}/24", "dev", "c0")
    for address in OTHER_CLIENT_ADDRESSES:
        run("ip", "-n", OTHER_CLIENT, "addr", "add", f"{address}/24", "dev", "c1")
    for namespace, interface in (
        (ROUTER, "up0"), (ROUTER, "lo"), (PI, "eth0"), (PI, "wlan0"), (PI, "lo"),
        (CLIENT, "c0"), (CLIENT, "lo"), (OTHER_CLIENT, "c1"), (OTHER_CLIENT, "lo"),
    ):
        run("ip", "-n", namespace, "link", "set", interface, "up")
    run("ip", "-n", CLIENT, "route", "add", "default", "via", "192.168.50.1")
    run("ip", "-n", OTHER_CLIENT, "route", "add", "default", "via", "192.168.50.1")
    run_in(PI, "sh", "-c", "echo 1 > /proc/sys/net/ipv4/ip_forward")
    # Like NetworkManager's shared mode on the Pi.
    run_in(PI, "nft", "add", "table", "ip", "nat")
    run_in(PI, "nft", "add", "chain", "ip", "nat", "post",
           "{ type nat hook postrouting priority 100; }")
    run_in(PI, "nft", "add", "rule", "ip", "nat", "post", "oifname", "eth0", "masquerade")


def remove_lab() -> None:
    for namespace in (PI, ROUTER, CLIENT, OTHER_CLIENT):
        run("ip", "netns", "del", namespace, check=False)
