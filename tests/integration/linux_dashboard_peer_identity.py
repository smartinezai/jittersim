"""Verify dashboard identity against actual Linux route and neighbor output."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.dashboard_access import AccessStore, identify_peer
from jittersim.networking.executor import SubprocessExecutor
from jittersim.networking.helper import HelperService
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
        refreshed = identify_peer(applier, peer, require_reachable=False)
        assert refreshed["mac_address"] == mac and not refreshed["can_register"]

        class WifiPeerApplier(NetworkApplier):
            def list_connected_stations(self):
                return f"Station {mac} (on {interface})\n"

        wifi_applier = WifiPeerApplier(
            executor=SubprocessExecutor(), topology=Topology("uplink-id0", interface)
        )
        with TemporaryDirectory() as directory:
            store = AccessStore(Path(directory) / "access.json")
            store.add(mac)
            request = {"action": "dashboard-access", "peer": peer}
            for state in ("stale", "delay", "probe"):
                run("ip", "neigh", "del", peer, "dev", interface)
                run("ip", "neigh", "replace", peer, "lladdr", mac, "nud", state, "dev", interface)
                service = HelperService(wifi_applier, access_store=store)
                status = service.handle_payload(request)
                assert status["allowed"] and not status["can_register"], status
                assert not service.handle_payload({**request, "operation": "register-self"})["ok"]
            for state in ("failed", "incomplete"):
                run("ip", "neigh", "del", peer, "dev", interface)
                run("ip", "neigh", "replace", peer, "nud", state, "dev", interface)
                service = HelperService(wifi_applier, access_store=store)
                assert not service.handle_payload(request)["allowed"]
        print("Real Linux peer lookup preserves saved access during address refresh and requires fresh enrollment.")
    finally:
        run("ip", "link", "delete", interface)


if __name__ == "__main__":
    main()
