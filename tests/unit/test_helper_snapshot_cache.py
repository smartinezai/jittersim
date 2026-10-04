"""Client scans are shared for a moment so many callers cost the Pi one scan."""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.engine import eligibility
from jittersim.networking.helper import HelperClient, HelperClientError

CLIENT = {
    "mac_address": "02:00:00:00:00:01", "ipv4_address": "192.168.50.10", "hostname": "phone",
    "state": "connected", "association_verified": True, "signal_dbm": -50,
    "connected_seconds": 5,
}


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = HelperClient(socket_path=Path("/tmp/not-used.sock"))
        self.actions = []
        self.gate = None
        self.fail_clients = False

        def request(_client, payload, timeout_seconds=None):
            self.actions.append(payload["action"])
            if payload["action"] == "control-revision":
                return {"ok": True, "control_revision": "0" * 32, "recovery_required": False}
            if payload["action"] != "clients":
                return {"ok": True}
            if self.gate is not None:
                self.gate.wait(5)
            if self.fail_clients:
                raise HelperClientError("busy", reason="helper-busy")
            return {"ok": True, "devices": [dict(CLIENT)], "association_status_available": True}

        patcher = patch.object(HelperClient, "_send", autospec=True, side_effect=request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def scans(self):
        return self.actions.count("clients")

    def test_a_second_read_inside_the_window_reuses_the_scan(self):
        first = self.client.connected_device_snapshot()
        second = self.client.connected_device_snapshot()

        self.assertEqual(first, second)
        self.assertEqual(self.scans(), 1)

    def test_the_scan_is_repeated_after_the_window(self):
        self.client.snapshot_ttl_seconds = 0.05
        self.client.connected_device_snapshot()
        time.sleep(0.1)
        self.client.connected_device_snapshot()

        self.assertEqual(self.scans(), 2)

    def test_callers_cannot_change_what_others_receive(self):
        self.client.connected_device_snapshot()["devices"][0]["hostname"] = "changed"

        self.assertEqual(self.client.connected_device_snapshot()["devices"][0]["hostname"], "phone")

    def test_simultaneous_reads_share_one_scan(self):
        self.gate = threading.Event()
        results = []
        readers = [
            threading.Thread(target=lambda: results.append(self.client.connected_device_snapshot()))
            for _ in range(8)
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + 3
        while not self.scans() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        self.gate.set()
        for reader in readers:
            reader.join(5)

        self.assertEqual(self.scans(), 1)
        self.assertEqual(len(results), 8)

    def test_a_failed_scan_reaches_every_waiter_and_is_not_kept(self):
        self.gate = threading.Event()
        self.fail_clients = True
        errors = []

        def read():
            try:
                self.client.connected_device_snapshot()
            except HelperClientError as error:
                errors.append(error)

        readers = [threading.Thread(target=read) for _ in range(4)]
        for reader in readers:
            reader.start()
        time.sleep(0.2)
        self.gate.set()
        for reader in readers:
            reader.join(5)
        self.assertEqual(len(errors), 4)

        self.fail_clients = False
        self.assertEqual(len(self.client.connected_device_snapshot()["devices"]), 1)

    def test_changes_to_the_network_start_a_fresh_scan(self):
        for change in (self.client.reset, self.client.normal_access,
                       lambda: self.client._request({"action": "router-wifi"})):
            self.client.invalidate_connected_devices()
            before = self.scans()
            self.client.connected_device_snapshot()
            self.client.connected_device_snapshot()
            change()
            self.client.connected_device_snapshot()
            self.assertEqual(self.scans() - before, 2, change)

    def test_reading_status_keeps_the_scan(self):
        self.client.connected_device_snapshot()
        self.client.ping()
        self.client.control_revision()
        self.client._request({"action": "traffic-counters"})
        self.client.connected_device_snapshot()

        self.assertEqual(self.scans(), 1)

    def test_a_scan_that_started_before_a_change_is_not_kept(self):
        self.gate = threading.Event()
        started = threading.Thread(target=eligibility.station_snapshot, args=(self.client,))
        started.start()
        time.sleep(0.1)
        self.client.reset()
        self.gate.set()
        started.join(5)
        self.gate = None

        self.client.connected_device_snapshot()

        self.assertEqual(self.scans(), 2)

    def test_clients_from_before_a_change_are_not_reused_as_a_stale_list(self):
        eligibility.station_snapshot(self.client)
        self.client.reset()
        self.fail_clients = True

        result = eligibility.station_snapshot(self.client)

        self.assertEqual(result, {"devices": [], "association_status_available": False})

    def test_a_busy_helper_still_gets_the_recent_list_when_nothing_changed(self):
        self.client.snapshot_ttl_seconds = 0.0
        good = eligibility.station_snapshot(self.client)
        self.fail_clients = True

        result = eligibility.station_snapshot(self.client)

        self.assertTrue(result["stale"])
        self.assertEqual(result["devices"], good["devices"])

    def test_a_scan_that_finished_after_a_change_never_becomes_the_stale_list(self):
        self.gate = threading.Event()
        started = threading.Thread(target=eligibility.station_snapshot, args=(self.client,))
        started.start()
        time.sleep(0.1)
        self.client.reset()
        self.gate.set()
        started.join(5)
        self.gate = None
        self.fail_clients = True

        result = eligibility.station_snapshot(self.client)

        self.assertEqual(result, {"devices": [], "association_status_available": False})


class StationSnapshotTests(unittest.TestCase):
    class Helper:
        def __init__(self):
            self.fail = False
            self.calls = 0

        def connected_device_snapshot(self):
            self.calls += 1
            if self.fail:
                raise HelperClientError("busy", reason="helper-busy")
            return {"devices": [dict(CLIENT)], "association_status_available": True}

    def test_a_busy_helper_keeps_the_last_good_clients_for_a_short_time(self):
        helper = self.Helper()
        with patch.object(eligibility.time, "monotonic", return_value=100.0):
            good = eligibility.station_snapshot(helper)
        self.assertNotIn("stale", good)
        helper.fail = True

        with patch.object(eligibility.time, "monotonic", return_value=105.0):
            stale = eligibility.station_snapshot(helper)

        self.assertTrue(stale["stale"])
        self.assertEqual(stale["devices"], good["devices"])
        self.assertTrue(stale["association_status_available"])

    def test_an_old_snapshot_is_not_reused(self):
        helper = self.Helper()
        with patch.object(eligibility.time, "monotonic", return_value=100.0):
            eligibility.station_snapshot(helper)
        helper.fail = True

        with patch.object(eligibility.time, "monotonic", return_value=111.0):
            result = eligibility.station_snapshot(helper)

        self.assertEqual(result, {"devices": [], "association_status_available": False})

    def test_no_earlier_snapshot_gives_the_unknown_state(self):
        helper = self.Helper()
        helper.fail = True

        self.assertEqual(
            eligibility.station_snapshot(helper),
            {"devices": [], "association_status_available": False},
        )


if __name__ == "__main__":
    unittest.main()
