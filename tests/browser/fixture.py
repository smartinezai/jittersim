"""Serve a disposable dashboard and stateful Wi-Fi helper."""

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import jittersim.api.server as server_module
from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, DomainObservation, Impairment, Profile, Scenario, ScenarioStep
from tests.adblocking_support import provision_guard
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.access import AccessManager
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.adblocking import AdRouting, AdStore
from jittersim.networking.commands import CommandResult
from jittersim.networking.dashboard_access import AccessStore
from jittersim.networking.renderer import queue_handle, queue_parent
from jittersim.networking.helper import (
    HelperClient,
    HelperClientError,
    HelperService,
    UnixHelperServer,
)
from jittersim.networking.router import RouterWifiSettings
from jittersim.networking.topology import Topology
from tests.wifi_support import WifiExecutor


# The fake kernel moves this much through every project queue for as long as it is read. It equals
# the 250 kbit/s cap the impact journey sets. The impact card calls a cap reached from 90% of it,
# so a poll that comes a little late still counts.
QUEUE_KBIT_PER_SECOND = 250


class DashboardWifiExecutor(WifiExecutor):
    def __init__(self, clock=time.time):
        super().__init__()
        self.queue_reads = {}
        self.queue_started = {}
        # The server stamps each sample with time.time(), so the byte counter uses the same clock.
        self.clock = clock

    def run(self, command):
        if command.executable == "ip":
            if command.arguments[:4] == ("-4", "neigh", "show", "dev"):
                return CommandResult(
                    0, stdout="192.168.50.184 dev wlan0 lladdr 02:00:00:00:00:01 REACHABLE\n"
                )
            if command.arguments[:3] in (("-j", "link", "show"), ("link", "add", "name")):
                # No ifb device on this fake Pi: upload corruption is left out.
                return CommandResult(1, stderr='Device "jittersim-ifb" does not exist.\n')
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
        """Answer queue counter reads with numbers that grow like a busy kernel's.

        Every read adds 100 forwarded packets and 11 dropped ones to each project queue, so the
        loss figure never depends on timing. The bytes grow with the time since the first read at
        QUEUE_KBIT_PER_SECOND, so the speed figure does not depend on how long a poll took.
        A new root queue or a removed one starts the counters again like the kernel does.
        """

        interface = command.arguments[-1] if command.arguments[:3] == ("-s", "-j", "qdisc") else None
        if interface is not None:
            self.commands.append(command)
            reads = self.queue_reads[interface] = self.queue_reads.get(interface, 0) + 1
            now = self.clock()
            queue_bytes = int(QUEUE_KBIT_PER_SECOND * 125 * (now - self.queue_started.setdefault(interface, now)))
            queues = [
                {
                    "kind": "netem", "handle": queue_handle(mark), "parent": queue_parent(mark),
                    "bytes": queue_bytes, "packets": 100 * reads, "drops": 11 * reads,
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
                self.queue_started.pop(command.arguments[3], None)
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


class FixtureAccessManager(AccessManager):
    """Household access rules against simulated commands: the DNS probe always answers."""

    def _probe(self, plan):
        return True


class DashboardHelperService(HelperService):
    """The real helper, plus a staged release and a reachable router when seeded."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if seeded():
            # Main's demo helper reported the router reachable; nothing probes it here.
            self._gateway_state = "available"

    def _handle_payload(self, payload):
        if seeded() and isinstance(payload, dict) and payload.get("action") == "update-status":
            return {
                "ok": True,
                "phase": "idle",
                "installed": "test-release",
                "available": "candidate-release",
                "rollback_available": False,
                "installable": True,
                "signed_by": "owner-mac",
                "reason": None,
                "trusted_keys": 1,
                "message": "A verified release is staged and ready.",
            }
        return super()._handle_payload(payload)


def seeded():
    return os.environ.get("JITTERSIM_BROWSER_TEST_SEED") == "1"


def seed_demo_data(repository):
    """Save two profiles, a device and a scenario when JITTERSIM_BROWSER_TEST_SEED is 1."""

    if not seeded():
        return
    repository.save_profile(Profile("normal", "Normal connection"))
    repository.save_profile(Profile("slow", "Slow connection", download=Impairment(delay_ms=300)))
    repository.save_device(Device("alice", "Test phone", "192.168.50.184", "normal", "02:00:00:00:00:01"))
    repository.save_scenario(
        Scenario("commute", "Commute", (ScenarioStep("slow", 10), ScenarioStep("normal", 10)))
    )


OLD_ADDRESS = "02:00:00:00:00:99"
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


# JITTERSIM_BROWSER_TEST_WIDE_FONTS=1 puts a wider face and a larger root size on the page so a
# journey can show that it holds on a machine whose fonts are taller than the ones it was
# written on.
WIDE_FONTS_STYLE = (
    b"<style>html { font-size: 112.5% !important; }"
    b" * { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }</style>"
)


def allow_wide_fonts_style():
    """Add the wide-font style's hash to the page's content security policy.

    The apply clarity journey also puts this style on the page itself to stand in
    for the fonts of another machine. Without the hash the policy drops it silently
    and the journey checks the normal fonts twice.
    """

    inner = WIDE_FONTS_STYLE[len(b"<style>"):-len(b"</style>")]
    digest = base64.b64encode(hashlib.sha256(inner).digest()).decode("ascii")
    server_module.DASHBOARD_CONTENT_SECURITY_POLICY = server_module.DASHBOARD_CONTENT_SECURITY_POLICY.replace(
        "style-src ", f"style-src 'sha256-{digest}' ", 1
    )


class FixtureServer(ControlServer):
    def response_for(self, path, query=""):
        status, content_type, body = super().response_for(path, query)
        if path == "/" and os.environ.get("JITTERSIM_BROWSER_TEST_WIDE_FONTS") == "1":
            body = body.replace(b"</head>", WIDE_FONTS_STYLE + b"</head>", 1)
        return status, content_type, body

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
    allow_wide_fonts_style()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        executor = DashboardWifiExecutor()
        topology = Topology("eth0", "wlan0")
        ad_blocking = AdStore(AdRouting("wlan0", executor), path=root / "ad-policy.json",
                              lock_path=root / "ad-lock", active_path=root / "ad-active.json",
                              status_path=root / "ad-status.json", intent_path=root / "ad-intent.json",
                              panic_guard=provision_guard(root))
        # Household access rules run against the same simulated commands.
        access_manager = FixtureAccessManager(
            executor=executor, topology=topology, directory=root / "access",
            runtime_path=root / "access-policy.json", hostapd_path=root / "hostapd",
            ad_blocking_store=ad_blocking,
        )
        access_store = AccessStore(root / "access.json")
        if os.environ.get("JITTERSIM_BROWSER_TEST_OLD_ADDRESS") == "1":
            # A device the owner saved earlier under an address it no longer uses.
            access_store.add(OLD_ADDRESS, "Old Mac")
        service = DashboardHelperService(NetworkApplier(executor=executor, topology=topology),
                                         router_factory=lambda *_: DashboardRouter(),
                                         access_store=access_store,
                                         ad_blocking_store=ad_blocking,
                                         access_manager=access_manager)
        with UnixHelperServer(root / "wifi.sock", service) as helper:
            worker = threading.Thread(target=helper.serve_forever, daemon=True)
            worker.start()
            try:
                with ConfigRepository(root / "config.db") as repository:
                    seed_observations(repository)
                    seed_demo_data(repository)
                    if os.environ.get("JITTERSIM_BROWSER_TEST_OLD_ADDRESS") == "1":
                        repository.save_unaffected_device(UnaffectedDevice(OLD_ADDRESS, "Old Mac", 1))
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
