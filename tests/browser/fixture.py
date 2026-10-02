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
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.dashboard_access import AccessStore
from jittersim.networking.helper import (
    HelperClient,
    HelperClientError,
    HelperService,
    UnixHelperServer,
)
from jittersim.networking.router import RouterWifiSettings
from jittersim.networking.topology import Topology
from tests.wifi_support import WifiExecutor


class DashboardWifiExecutor(WifiExecutor):
    def __init__(self):
        super().__init__()
        self.queue_reads = {}

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
        if command.executable == "tc":
            return self.run_tc(command)
        return super().run(command)

    def run_tc(self, command):
        """Answer queue counter reads with numbers that grow on every read.

        Every read adds 100 forwarded packets and 11 dropped ones to each project queue.
        A new root queue or a removed one starts the counters again like the kernel does.
        """

        interface = command.arguments[-1] if command.arguments[:3] == ("-s", "-j", "qdisc") else None
        if interface is not None:
            self.commands.append(command)
            reads = self.queue_reads[interface] = self.queue_reads.get(interface, 0) + 1
            queues = [
                {
                    "kind": "netem", "handle": f"{mark + 10}:", "parent": f"10:{mark + 1}",
                    "bytes": 150_000 * reads, "packets": 100 * reads, "drops": 11 * reads,
                    "overlimits": 0, "requeues": 0, "backlog": 0, "qlen": 0,
                }
                for mark in range(1, 5)
            ]
            root = {"kind": "prio", "handle": "10:", "root": True, "bytes": 1, "packets": 1,
                    "drops": 0, "overlimits": 0, "requeues": 0, "backlog": 0, "qlen": 0}
            return CommandResult(0, stdout=json.dumps([root] + queues))
        if command.arguments[:1] == ("qdisc",) and command.arguments[1] in ("replace", "del"):
            if "root" in command.arguments:
                self.queue_reads.pop(command.arguments[3], None)
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


BUSY_ATTEMPTS = 100
BUSY_WAIT_SECONDS = 0.05


class PatientHelperClient(HelperClient):
    """Wait for the helper when it is busy instead of failing the journey.

    The helper does one thing at a time and refuses a request that arrives while another
    runs. The page polls the helper every few seconds so a button that a journey presses can
    land in the middle of a poll. A person would press the button again. On the slower CI
    machine this now and then failed a journey with a network update that failed.
    """

    def _request(self, payload, *, timeout_seconds=None):
        for attempt in range(BUSY_ATTEMPTS):
            try:
                return super()._request(payload, timeout_seconds=timeout_seconds)
            except HelperClientError as error:
                if error.reason != "helper-busy" or attempt == BUSY_ATTEMPTS - 1:
                    raise
            time.sleep(BUSY_WAIT_SECONDS)


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
        ("192.168.50.124", "content.header.immobilienscout24.de", 4, 400),
        ("192.168.50.124", "osa.s24-media.immobilienscout24.de", 3, 400),
        ("192.168.50.124", "sso.immobilienscout24.de", 2, 400),
        ("192.168.50.124", "tenant-network.immobilienscout24.de", 2, 400),
        ("192.168.50.124", "tracking.immobilienscout24.de", 6, 400),
        ("192.168.50.124", "web.chatbot.immobilienscout24.de", 1, 400),
        ("192.168.50.124", "logs.dradis.netflix.com", 3, 1200),
        ("192.168.50.124", "ipv4-c179-fra002-ix.1.oca.nflxvideo.net", 40, 1200),
        ("192.168.50.124", "config.extension.grammarly.com", 5, 2000),
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


def keep_helper_busy(helper_client):
    """Ask the helper for the connected clients all the time when JITTERSIM_BROWSER_TEST_HELPER_NOISE
    is 1. The helper does one thing at a time so this reproduces on a fast machine what a slow CI
    machine does by chance when the page polls while a journey presses a button."""

    if os.environ.get("JITTERSIM_BROWSER_TEST_HELPER_NOISE") != "1":
        return

    def ask():
        while True:
            try:
                helper_client.connected_devices()
            except Exception:  # a refused request is the point
                pass
            time.sleep(0.002)

    threading.Thread(target=ask, daemon=True).start()


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
                    helper_client = PatientHelperClient(socket_path=root / "wifi.sock")
                    keep_helper_busy(HelperClient(socket_path=root / "wifi.sock"))
                    scheduler = None
                    if os.environ.get("JITTERSIM_BROWSER_TEST_SCHEDULER") == "1":
                        # Like a real install: an Apply keeps the scheduler running.
                        scheduler = RuntimeScheduler(
                            database_path=root / "config.db", helper_client=helper_client
                        )
                    try:
                        with FixtureServer(
                            ("127.0.0.1", 0), repository, admin_token="browser-test",
                            helper_client=helper_client, scheduler=scheduler,
                        ) as server:
                            print(server.server_address[1], flush=True)
                            server.serve_forever()
                    finally:
                        if scheduler is not None:
                            scheduler.stop()
            finally:
                helper.shutdown()
                worker.join(timeout=3)


if __name__ == "__main__":
    main()
