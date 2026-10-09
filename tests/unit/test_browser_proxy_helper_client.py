"""The browser proxy shares one helper client so its client scans are cached."""

import threading
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

        def scan(client, timeout_seconds=None):
            self.scans.append(client)
            return {"association_status_available": True,
                    "devices": [dict(device) for device in SNAPSHOT["devices"]]}

        # A long cache window keeps the test independent of a slow machine. The
        # proxy uses the client's normal window.
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

    def test_connections_reuse_one_client_and_hit_its_snapshot_cache(self):
        self.assertEqual(browser_proxy._live_client_mac(IPv4Address("192.168.50.10")),
                         "02:00:00:00:00:0a")
        self.assertEqual(browser_proxy._live_client_mac(IPv4Address("192.168.50.11")),
                         "02:00:00:00:00:0b")
        self.assertEqual(len(self.scans), 1, "every CONNECT started its own helper scan")
        self.assertEqual(len(self.created), 1)

    def test_a_burst_of_connections_shares_one_helper_scan(self):
        results = []

        def connect():
            results.append(browser_proxy._live_client_mac(IPv4Address("192.168.50.10")))

        threads = [threading.Thread(target=connect) for _ in range(32)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(results, ["02:00:00:00:00:0a"] * 32)
        self.assertEqual(len(self.scans), 1)
