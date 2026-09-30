"""Verify dashboard identity against actual Linux route and neighbor output."""

import subprocess
from pathlib import Path

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.dashboard_access import identify_peer
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.topology import Topology


def run(*arguments):
    subprocess.run(arguments, check=True, capture_output=True, timeout=10)


def main():
    if not Path("/.dockerenv").exists():
        raise RuntimeError("Run only in a disposable container without host networking.")
    interface = "peer-id0"
    peer = "198.18.37.2"
    mac = "02:00:00:37:00:02"
    run("ip", "link", "add", interface, "type", "veth", "peer", "name", "peer-id1")
    try:
        run("ip", "link", "set", interface, "up")
        run("ip", "addr", "add", "198.18.37.1/24", "dev", interface)
        run("ip", "neigh", "replace", peer, "lladdr", mac, "nud", "reachable", "dev", interface)
        applier = NetworkApplier(executor=SubprocessExecutor(),
                                topology=Topology(interface, "test-id0"))
        identity = identify_peer(applier, peer)
        assert identity == {"network": "management", "mac_address": mac, "can_register": True}, identity
        run("ip", "neigh", "replace", peer, "lladdr", mac, "nud", "stale", "dev", interface)
        assert identify_peer(applier, peer)["can_register"] is False
        print("Real Linux peer lookup verifies the interface and rejects stale identity.")
    finally:
        run("ip", "link", "delete", interface)


if __name__ == "__main__":
    main()
