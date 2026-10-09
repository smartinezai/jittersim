"""The browser proxy shares one helper client and its in-flight client scan.

A completed scan is never reused for a later CONNECT: another device may have
taken the same IPv4 address since, and the tunnel must not inherit the earlier
device's protected identity.
"""

import threading
import time
import unittest
from ipaddress import IPv4Address
from unittest.mock import patch

from jittersim.networking import browser_proxy
from jittersim.networking.helper import HelperClient

SNAPSHOT = {
    "association_status_available": True,
    "devices": [
        {"mac_address": "02:00:00:00:00:0a", "ipv4_address": "192.168.50.10",
         "association_verified": True},
        {"mac_address": "02:00:00:00:00:0b", "ipv4_address": "192.168.50.11",
         "association_verified": True},
    ],
}


class ProxyHelperClientTests(unittest.TestCase):
    def setUp(self):
        self.scans = []
        self.created = []
        original_init = HelperClient.__init__

        def init(client, **arguments):
            self.created.append(client)
            original_init(client, **arguments)

        self.snapshots = [SNAPSHOT]
        self.scan_gate = None

        def scan(client, timeout_seconds=None):
            self.scans.append(client)
            if self.scan_gate is not None:
                self.scan_gate.wait(10)
            snapshot = self.snapshots[min(len(self.scans), len(self.snapshots)) - 1]
            return {"association_status_available": snapshot["association_status_available"],
                    "devices": [dict(device) for device in snapshot["devices"]]}

        # A long class-wide cache window: the proxy must still not reuse a
        # completed scan, whatever the helper client's default window is.
        for patcher in (patch.object(HelperClient, "__init__", init),
                        patch.object(HelperClient, "_read_connected_device_snapshot", scan),
                        patch.object(HelperClient, "snapshot_ttl_seconds", 60)):
            patcher.start()
            self.addCleanup(patcher.stop)
        # A fresh process-wide client for each test.
        if hasattr(browser_proxy, "_HELPER_CLIENT"):
            patcher = patch.object(browser_proxy, "_HELPER_CLIENT", None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_connections_reuse_one_client_but_never_a_completed_scan(self):
        self.assertEqual(browser_proxy._live_client_mac(IPv4Address("192.168.50.10")),
                         "02:00:00:00:00:0a")
        self.assertEqual(browser_proxy._live_client_mac(IPv4Address("192.168.50.11")),
                         "02:00:00:00:00:0b")
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.scans), 2, "a CONNECT reused a completed scan")

    def test_a_new_device_behind_the_same_address_gets_its_own_mac(self):
        # Scan 1: 192.168.50.10 is the protected device A. Scan 2, moments later:
        # device B has taken the address. The second tunnel must be B's.
        self.snapshots = [
            SNAPSHOT,
            {"association_status_available": True,
             "devices": [{"mac_address": "02:00:00:00:00:0c", "ipv4_address": "192.168.50.10",
                          "association_verified": True}]},
        ]
        self.assertEqual(browser_proxy._live_client_mac(IPv4Address("192.168.50.10")),
                         "02:00:00:00:00:0a")
        self.assertEqual(browser_proxy._live_client_mac(IPv4Address("192.168.50.10")),
                         "02:00:00:00:00:0c")

    def test_a_burst_of_connections_shares_one_helper_scan(self):
        results = []
        self.scan_gate = threading.Event()

        def connect():
            results.append(browser_proxy._live_client_mac(IPv4Address("192.168.50.10")))

        threads = [threading.Thread(target=connect) for _ in range(32)]
        for thread in threads:
            thread.start()
        # Every CONNECT is waiting on the one in-flight scan before it completes.
        deadline = time.monotonic() + 10
        while len(self.scans) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.5)
        self.scan_gate.set()
        for thread in threads:
            thread.join(10)
        self.assertEqual(results, ["02:00:00:00:00:0a"] * 32)
        self.assertEqual(len(self.scans), 1)
