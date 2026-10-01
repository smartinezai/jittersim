"""Serve a disposable dashboard and stateful Wi-Fi helper."""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from jittersim.api.server import ControlServer
from jittersim.config.models import DomainObservation
from jittersim.config.storage import ConfigRepository
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.dashboard_access import AccessStore
from jittersim.networking.helper import HelperClient, HelperService, UnixHelperServer
from jittersim.networking.router import RouterWifiSettings
from jittersim.networking.topology import Topology
from tests.wifi_support import WifiExecutor


class DashboardWifiExecutor(WifiExecutor):
    def run(self, command):
        if command.executable == "ip":
            if command.arguments[:4] == ("-4", "neigh", "show", "dev"):
                return CommandResult(
                    0, stdout="192.168.50.184 dev wlan0 lladdr 02:00:00:00:00:01 REACHABLE\n"
                )
            if command.arguments[:4] == ("-j", "-4", "route", "get"):
                return CommandResult(0, stdout=json.dumps([{"dev": "wlan0"}]))
            if command.arguments[:4] == ("-j", "-4", "neigh", "show"):
                return CommandResult(0, stdout=json.dumps([{
                    "dst": "192.168.50.184", "dev": "wlan0",
                    "lladdr": "02:00:00:00:00:01", "state": ["REACHABLE"]}]))
            return CommandResult(0, stdout="default via 192.168.0.1 dev eth0\n")
        if command.executable == "iw":
            return CommandResult(0, stdout="Station 02:00:00:00:00:01 (on wlan0)\n")
        if command.executable == "nmcli" and command.arguments[-1] == "eth0":
            return CommandResult(0, stdout="management\n")
        if command.executable == "nft":
            self.commands.append(command)
            return CommandResult(0, stdout='{"nftables":[]}')
        return super().run(command)


class DashboardRouter:
    enabled = True

    def limit_operation(self, seconds):
        pass

    def login(self, **kwargs):
        pass

    def read_wifi_enabled(self):
        return type(self).enabled

    def set_wifi_enabled(self, *, enabled):
        type(self).enabled = enabled
        return RouterWifiSettings("", "", enabled)

    def read_wifi_settings(self):
        return RouterWifiSettings("Browser home", "browser-wifi-password", True, "WPA2", "2.4 GHz")

    def close(self):
        pass


class FixtureServer(ControlServer):
    def get_request(self):
        connection, address = super().get_request()
        if os.environ.get("JITTERSIM_BROWSER_TEST_WIFI_PEER") == "1":
            return connection, ("192.168.50.184", address[1])
        return connection, address


def seed_observations(repository):
    """Save a few traffic observations when JITTERSIM_BROWSER_TEST_OBSERVATIONS is 1."""

    if os.environ.get("JITTERSIM_BROWSER_TEST_OBSERVATIONS") != "1":
        return
    now = int(time.time())
    rows = (
        ("192.168.50.124", "mmx-ds.cdn.whatsapp.net", 12, 0),
        ("192.168.50.124", "a1051.dscg4.akamai.net", 3, 0),
        ("192.168.50.124", "tags-eu.tiqcdn.com", 2, 399),
        ("192.168.50.124", "api.header.immobilienscout24.de", 4, 400),
        ("192.168.50.124", "cdn.somesmallsite.io", 1, 400),
        ("192.168.50.124", "very-long-unknown-name.sub.example-sample-site.co.uk", 1, 900),
        ("192.168.50.184", "10000.mt.eu.iot.dreame.tech", 7, 0),
        ("192.168.50.184", "gateway.icloud.com", 5, 0),
    )
    repository.save_domain_observations(
        [
            DomainObservation(address, domain, count, now - offset, now - offset)
            for address, domain, count, offset in rows
        ],
        now=now,
    )


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = HelperService(NetworkApplier(executor=DashboardWifiExecutor(),
                                              topology=Topology("eth0", "wlan0")),
                                router_factory=lambda *_: DashboardRouter(),
                                access_store=AccessStore(root / "access.json"))
        with UnixHelperServer(root / "wifi.sock", service) as helper:
            worker = threading.Thread(target=helper.serve_forever, daemon=True)
            worker.start()
            try:
                with ConfigRepository(root / "config.db") as repository:
                    seed_observations(repository)
                    with FixtureServer(
                        ("127.0.0.1", 0), repository, admin_token="browser-test",
                        helper_client=HelperClient(socket_path=root / "wifi.sock"),
                    ) as server:
                        print(server.server_address[1], flush=True)
                        server.serve_forever()
            finally:
                helper.shutdown()
                worker.join(timeout=3)


if __name__ == "__main__":
    main()
