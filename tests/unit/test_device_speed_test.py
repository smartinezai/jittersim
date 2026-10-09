"""Device speed test: the arithmetic, storage, the measurement window and its routes."""

import csv
import http.client
import io
import json
import sqlite3
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jittersim.api import speed_tests as routes
from jittersim.api.server import ControlServer, WRITE_PATHS
from jittersim.api.speed_tests import SPEED_TEST_READS, SPEED_TEST_WRITES, SpeedTestManager
from jittersim.config import speed_tests as store_module
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.speed_tests import SpeedTestStore, clean_label, clean_reported
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError
from jittersim.speed_test import (
    PROVIDERS,
    IcmpProber,
    compare_results,
    echo_request,
    export_csv,
    icmp_checksum,
    parse_echo_reply,
    percentile,
    same_subnet,
    summarize_direction,
    summarize_probes,
)
from tests.unit.test_api import FakeHelper

PHONE = "dc:1e:d5:3e:97:4c"
OTHER = "02:00:00:00:00:07"


def samples(rates_kbit, *, start=100.0, direction="download"):
    """Samples one second apart whose bytes grow at the given kbit/s."""

    moved = 0
    result = [{"t": start, direction: {"bytes": 0, "packets": 0, "drops": 0}}]
    for index, rate in enumerate(rates_kbit, start=1):
        moved += int(rate * 125)
        result.append({"t": start + index, direction: {"bytes": moved, "packets": index * 10,
                                                        "drops": index}})
    return result


class ArithmeticTests(unittest.TestCase):
    def test_busy_seconds_leave_idle_time_out_of_the_average(self) -> None:
        summary = summarize_direction(samples([0, 0, 1000, 1000, 1000, 900, 0]), "download", None)
        self.assertEqual(summary["busy_seconds"], 4)
        self.assertEqual(summary["peak_kbit"], 1000)
        self.assertEqual(summary["busy_average_kbit"], 975)
        self.assertEqual(summary["median_kbit"], 1000)
        self.assertEqual(summary["p90_kbit"], 1000)
        self.assertEqual(summary["average_kbit"], round(3900 / 7, 1))
        self.assertEqual(summary["bytes"], 3900 * 125)
        self.assertEqual(summary["drops"], 7)
        self.assertEqual(summary["verdict"], "no-cap")

    def test_verdicts_against_the_cap(self) -> None:
        at_cap = summarize_direction(samples([250] * 10), "download", 250)
        self.assertEqual(at_cap["verdict"], "at-cap")
        below = summarize_direction(samples([100] * 10), "download", 250)
        self.assertEqual(below["verdict"], "below-cap")
        above = summarize_direction(samples([400] * 10), "download", 250)
        self.assertEqual(above["verdict"], "above-cap")

    def test_too_little_traffic_is_not_judged(self) -> None:
        self.assertEqual(summarize_direction(samples([250, 250]), "download", 250)["verdict"],
                         "too-little-traffic")
        self.assertEqual(summarize_direction(samples([1, 1, 1, 1, 1]), "download", None)["verdict"],
                         "too-little-traffic")
        self.assertEqual(summarize_direction(samples([]), "download", None)["verdict"],
                         "too-little-traffic")

    def test_percentile_is_nearest_rank(self) -> None:
        self.assertIsNone(percentile([], 0.5))
        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2)
        self.assertEqual(percentile(list(range(1, 11)), 0.9), 9)

    def test_probe_summary(self) -> None:
        probes = {1: {"rtt_ms": 10.0}, 2: {"rtt_ms": None}, 3: {"rtt_ms": 30.0}, 4: {"rtt_ms": 20.0}}
        summary = summarize_probes(probes, "measured")
        self.assertEqual((summary["sent"], summary["received"], summary["loss_percent"]), (4, 3, 25.0))
        self.assertEqual((summary["rtt_min_ms"], summary["rtt_median_ms"], summary["rtt_max_ms"]),
                         (10.0, 20.0, 30.0))
        self.assertEqual(summarize_probes({1: {"rtt_ms": None}}, "measured")["status"], "no-answer")

    def test_compare_gives_the_change_and_warns_about_shared_queues(self) -> None:
        baseline = {"id": 1, "mac_address": PHONE, "source": "device", "status": "complete",
                    "summary": {"download": {"busy_average_kbit": 1000.0, "verdict": "no-cap"},
                                "upload": {}, "latency": {"rtt_median_ms": 5.0}},
                    "reported": {"download_mbit": 50}}
        later = {"id": 2, "mac_address": PHONE, "source": "everyone", "status": "complete",
                 "summary": {"download": {"busy_average_kbit": 250.0, "verdict": "at-cap"},
                             "upload": {}, "latency": {"rtt_median_ms": 105.0}},
                 "reported": {"download_mbit": 0.25}}
        result = compare_results(baseline, later)
        changes = {item["field"]: item for item in result["changes"]}
        self.assertEqual(changes["download_busy_kbit"]["change"], -750.0)
        self.assertEqual(changes["download_busy_kbit"]["percent"], -75.0)
        self.assertEqual(changes["rtt_median_ms"]["change"], 100.0)
        self.assertEqual(changes["reported_download_mbit"]["change"], -49.75)
        self.assertIsNone(changes["upload_busy_kbit"]["change"])
        self.assertEqual(result["verdicts"]["download"], {"baseline": "no-cap", "result": "at-cap"})
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("shared queue", result["warnings"][0])

    def test_csv_export_defuses_spreadsheet_formulas(self) -> None:
        text = export_csv([{"id": 1, "device_name": "=HYPERLINK(1)", "label": "@x",
                            "summary": {}, "profile": {}, "reported": {}}])
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(rows[0]["device_name"], "'=HYPERLINK(1)")
        self.assertEqual(rows[0]["label"], "'@x")


class ProviderTests(unittest.TestCase):
    def test_every_provider_has_a_well_formed_qr_code(self) -> None:
        for provider in PROVIDERS:
            size = provider["qr_size"]
            self.assertEqual((size - 21) % 4, 0, provider["id"])
            rows = [int(row, 16) for row in provider["qr_rows"]]
            self.assertEqual(len(rows), size)
            bits = [[(row >> (size - 1 - x)) & 1 for x in range(size)] for row in rows]
            finder = [[1] * 7, [1, 0, 0, 0, 0, 0, 1], [1, 0, 1, 1, 1, 0, 1], [1, 0, 1, 1, 1, 0, 1],
                      [1, 0, 1, 1, 1, 0, 1], [1, 0, 0, 0, 0, 0, 1], [1] * 7]
            for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
                self.assertEqual([line[left:left + 7] for line in bits[top:top + 7]], finder,
                                 provider["id"])
            self.assertTrue(provider["url"].startswith("https://"))

    def test_only_https_pages_with_a_public_engine_are_offered(self) -> None:
        self.assertEqual([provider["id"] for provider in PROVIDERS], ["cloudflare", "librespeed"])


class FakeSocket:
    def __init__(self, local):
        self.local = local
        self.sent = []
        self.closed = False

    def setblocking(self, flag):
        pass

    def connect(self, address):
        self.address = address

    def getsockname(self):
        return (self.local, 0)

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class ProberTests(unittest.TestCase):
    def test_nothing_is_sent_off_the_test_network(self) -> None:
        sock = FakeSocket("10.0.0.5")
        prober = IcmpProber("192.168.50.10", socket_factory=lambda: sock)
        prober.send()
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)
        self.assertEqual(prober.summary()["status"], "unavailable")

    def test_a_forbidden_socket_is_unavailable(self) -> None:
        def refuse():
            raise PermissionError("not in ping_group_range")

        summary = IcmpProber("192.168.50.10", socket_factory=refuse).summary()
        self.assertEqual(summary["status"], "unavailable")
        self.assertIn("ICMP", summary["reason"])

    def test_on_the_test_network_one_echo_per_tick(self) -> None:
        sock = FakeSocket("192.168.50.1")
        prober = IcmpProber("192.168.50.10", socket_factory=lambda: sock)
        prober.send()
        prober.send()
        self.assertEqual(len(sock.sent), 2)
        self.assertEqual(icmp_checksum(sock.sent[0]), 0)
        self.assertEqual(prober.summary()["status"], "no-answer")

    def test_reply_parsing_with_and_without_the_ip_header(self) -> None:
        nonce = b"abcdefgh"
        reply = bytearray(echo_request(7, nonce))
        reply[0] = 0
        self.assertEqual(parse_echo_reply(bytes(reply), nonce), 7)
        ip_header = bytes([0x45]) + bytes(19)
        self.assertEqual(parse_echo_reply(ip_header + bytes(reply), nonce), 7)
        self.assertIsNone(parse_echo_reply(bytes(reply), b"zzzzzzzz"))
        self.assertIsNone(parse_echo_reply(echo_request(7, nonce), nonce))
        self.assertEqual(struct.unpack("!H", echo_request(65541, nonce)[6:8])[0], 5)

    def test_same_subnet(self) -> None:
        self.assertTrue(same_subnet("192.168.50.1", "192.168.50.184"))
        self.assertFalse(same_subnet("192.168.50.1", "192.168.50.1"))
        self.assertFalse(same_subnet("192.168.1.2", "192.168.50.184"))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.store = SpeedTestStore(self.repository)

    def row(self, mac=PHONE, **changes):
        row = {"started_at": 1, "finished_at": 2, "mac_address": mac, "device_name": "Phone",
               "status": "complete", "provider": "cloudflare", "source": "device",
               "summary": {"download": {"bytes": 1}}, "profile": {"profile_name": "Slow"}}
        row.update(changes)
        return row

    def test_migration_adds_the_table_to_an_old_database_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            ConfigRepository(path).close()
            with sqlite3.connect(path) as connection:
                connection.execute("DROP TABLE speed_test_results")
            for _ in range(2):
                repository = ConfigRepository(path)
                SpeedTestStore(repository).save(self.row())
                repository.close()
            repository = ConfigRepository(path)
            self.addCleanup(repository.close)
            self.assertEqual(len(SpeedTestStore(repository).list()), 2)

    def test_rows_are_bounded_per_device_and_in_total(self) -> None:
        with mock.patch.object(store_module, "MAX_RESULTS_PER_DEVICE", 3), \
                mock.patch.object(store_module, "MAX_RESULTS_TOTAL", 5):
            for _ in range(5):
                self.store.save(self.row())
            for _ in range(4):
                self.store.save(self.row(mac=OTHER))
        self.assertEqual(len(self.store.list(PHONE)), 2)
        self.assertEqual(len(self.store.list(OTHER)), 3)
        self.assertEqual(len(self.store.list()), 5)

    def test_update_and_delete(self) -> None:
        saved = self.store.save(self.row(label="before"))
        updated = self.store.update(saved["id"], label="  No   limits ", reported={"download_mbit": 48.5})
        self.assertEqual(updated["label"], "No limits")
        self.assertEqual(updated["reported"], {"download_mbit": 48.5})
        self.assertIsNone(self.store.update(999, label="x"))
        self.assertTrue(self.store.delete(saved["id"]))
        self.assertFalse(self.store.delete(saved["id"]))

    def test_label_and_reported_validation(self) -> None:
        self.assertEqual(clean_label(None), "")
        for bad in ("x" * 81, 5, "a\x00b"):
            with self.assertRaises(ValueError):
                clean_label(bad)
        for bad in ({"other": 1}, {"download_mbit": -1}, {"download_mbit": True},
                    {"download_mbit": "fast"}, {"latency_ms": 1e9}, [1]):
            with self.assertRaises(ValueError):
                clean_reported(bad)
        self.assertEqual(clean_reported({"download_mbit": None, "upload_mbit": 3}),
                         {"upload_mbit": 3.0})


def queue(device_id, direction, moved, **changes):
    entry = {
        "device_id": device_id, "direction": direction,
        "interface": "eth0" if direction == "upload" else "wlan0", "handle": "41:",
        "kind": "netem", "generation": "0a1b2c3d-1",
        "impairment": {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0,
                       "bandwidth_kbit": 250 if direction == "download" else None,
                       "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0},
        "packets": moved // 100, "bytes": moved, "drops": 0, "overlimits": 0, "requeues": 0,
        "backlog_bytes": 0, "qlen": 0,
    }
    entry.update(changes)
    return entry


class CountingHelper(FakeHelper):
    """Queues that move 250 kbit/s down and 100 kbit/s up per second of the test clock."""

    def __init__(self, clock, queue_id="lab"):
        super().__init__()
        self.clock = clock
        self.queue_id = queue_id
        self.generation = "0a1b2c3d-1"
        self.fail = False

    def queue_stats(self):
        if self.fail:
            raise HelperClientError("helper is busy", reason="helper-busy")
        seconds = self.clock[0] - 100
        return [
            queue(self.queue_id, "download", int(250 * 125 * seconds), generation=self.generation),
            queue(self.queue_id, "upload", int(100 * 125 * seconds), generation=self.generation),
            queue("someone-else", "download", 5),
        ]

    def connected_devices(self):
        return [{"mac_address": OTHER, "ipv4_address": "192.168.50.77"}]


class QuietProber:
    def __init__(self, target):
        self.target = target

    def send(self):
        pass

    def collect(self, until):
        pass

    def summary(self):
        return {"status": "measured", "sent": 3, "received": 3, "loss_percent": 0.0,
                "rtt_median_ms": 4.0}

    def close(self):
        pass


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [100.0]
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(bandwidth_kbit=250)))
        self.repository.save_device(Device("lab", "Lab phone", "192.168.50.10", "slow", mac_address=PHONE))
        self.repository.update_runtime_state(
            observed_state="applied", last_action="apply", last_result="success", last_error=None)
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)
        self.helper = CountingHelper(self.clock)
        self.server.helper_client = self.helper
        self.targets = []

        def prober(target):
            self.targets.append(target)
            return QuietProber(target)

        self.server.speed_test_prober_factory = prober
        self.manager = SpeedTestManager(self.server, autostart=False, clock=lambda: self.clock[0])
        self.server._speed_test_manager = self.manager

    def write(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def read(self, path, query=""):
        status, content_type, body = self.server.response_for(path, query=query)
        return status, content_type, body

    def run_seconds(self, run_id, seconds):
        for _ in range(seconds):
            self.clock[0] += 1
            self.manager.tick(run_id)

    def test_a_full_window_measures_the_device_queues_and_saves_the_result(self) -> None:
        status, run = self.write("/api/speed-tests/start",
                                 {"mac_address": PHONE.upper(), "duration_seconds": 15, "label": "Slow"})
        self.assertEqual(status, 201)
        self.assertEqual((run["source"], run["state"], run["device_name"]), ("device", "running", "Lab phone"))
        self.assertEqual(self.targets, ["192.168.50.10"])
        self.run_seconds(run["id"], 5)
        status, _, body = self.read("/api/speed-tests/status", "run=" + run["id"])
        live = json.loads(body)
        self.assertEqual(live["live"]["download"]["kbit"], 250.0)
        self.assertEqual(live["seconds_left"], 10)
        self.run_seconds(run["id"], 10)
        ended = json.loads(self.read("/api/speed-tests/status", "run=" + run["id"])[2])
        self.assertEqual(ended["state"], "ended")
        result = ended["result"]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["label"], "Slow")
        self.assertEqual(result["summary"]["download"]["busy_average_kbit"], 250.0)
        self.assertEqual(result["summary"]["download"]["verdict"], "at-cap")
        self.assertEqual(result["summary"]["upload"]["verdict"], "no-cap")
        self.assertEqual(result["summary"]["latency"]["rtt_median_ms"], 4.0)
        self.assertEqual(result["profile"]["profile_name"], "Slow")
        self.assertEqual(result["profile"]["applied"]["download"]["bandwidth_kbit"], 250)
        self.assertFalse(result["protected"])
        listing = json.loads(self.read("/api/speed-tests", "mac=" + PHONE)[2])
        self.assertEqual([item["id"] for item in listing["results"]], [result["id"]])
        self.assertEqual(listing["active"], [])
        self.assertEqual(listing["default_provider"], "cloudflare")

    def test_the_sampling_loop_probes_once_per_second_and_ends_on_time(self) -> None:
        sent = []

        class CountingProber(QuietProber):
            closed = False

            def send(self):
                sent.append(self.target)

            def close(self):
                CountingProber.closed = True

        self.server.speed_test_prober_factory = CountingProber
        clock = self.clock

        def sleep(seconds):
            clock[0] += max(0.0, seconds)

        manager = SpeedTestManager(self.server, autostart=False, clock=lambda: clock[0],
                                   monotonic=lambda: clock[0], sleep=sleep)
        run = manager.start({"mac_address": PHONE, "duration_seconds": 15})
        manager._loop(manager.runs[run["id"]])
        ended = manager.status(run["id"])
        self.assertEqual(ended["result"]["status"], "complete")
        self.assertEqual(ended["samples"], 16)
        self.assertEqual(len(sent), 15)
        self.assertTrue(CountingProber.closed)

    def test_one_run_per_device_and_two_in_total(self) -> None:
        self.assertEqual(self.write("/api/speed-tests/start", {"mac_address": PHONE})[0], 201)
        status, body = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(status, 409)
        self.assertIn("already running", body["error"])
        self.assertEqual(self.write("/api/speed-tests/start", {"mac_address": OTHER})[0], 201)
        status, body = self.write("/api/speed-tests/start", {"mac_address": "02:00:00:00:00:08"})
        self.assertEqual(status, 409)
        self.assertIn("At most 2", body["error"])

    def test_a_protected_device_is_refused_and_a_run_ends_when_it_becomes_protected(self) -> None:
        self.repository.save_unaffected_device(UnaffectedDevice(OTHER, "Dad", 1))
        status, body = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        self.assertEqual(status, 409)
        self.assertIn("Protected", body["error"])
        status, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.repository.save_unaffected_device(UnaffectedDevice(PHONE, "Phone", 2))
        self.run_seconds(run["id"], 1)
        ended = self.manager.status(run["id"])
        self.assertEqual(ended["state"], "ended")
        self.assertIsNone(ended["result"])
        self.assertEqual(SpeedTestStore(self.repository).list(), [])

    def test_a_new_apply_during_the_window_ends_it_as_changed(self) -> None:
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.run_seconds(run["id"], 4)
        self.helper.generation = "0a1b2c3d-2"
        self.run_seconds(run["id"], 1)
        result = self.manager.status(run["id"])["result"]
        self.assertEqual(result["status"], "changed")
        self.assertEqual(result["summary"]["download"]["seconds"], 4)

    def test_a_busy_helper_skips_a_sample_without_ending_the_run(self) -> None:
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.helper.fail = True
        self.run_seconds(run["id"], 2)
        self.helper.fail = False
        self.run_seconds(run["id"], 2)
        payload = self.manager.status(run["id"])
        self.assertEqual((payload["state"], payload["samples"]), ("running", 3))

    def test_finish_saves_now_and_cancel_saves_nothing(self) -> None:
        _, first = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.run_seconds(first["id"], 5)
        status, finished = self.write("/api/speed-tests/finish", {"run_id": first["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(finished["result"]["status"], "finished-early")
        self.assertEqual(self.write("/api/speed-tests/finish", {"run_id": first["id"]})[0], 409)
        _, second = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        status, cancelled = self.write("/api/speed-tests/cancel", {"run_id": second["id"]})
        self.assertEqual((status, cancelled["result"]), (200, None))
        self.assertEqual(len(SpeedTestStore(self.repository).list()), 1)
        self.assertEqual(self.write("/api/speed-tests/cancel", {"run_id": "nope"})[0], 404)

    def test_a_device_without_a_profile_is_not_measured_on_the_pi(self) -> None:
        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        self.assertEqual(run["source"], "none")
        self.assertIn("No profile applies", run["source_note"])
        self.assertEqual(self.targets, ["192.168.50.77"])
        _, finished = self.write("/api/speed-tests/finish", {"run_id": run["id"]})
        result = finished["result"]
        self.assertEqual(result["summary"]["download"]["verdict"], "not-measured")
        self.assertEqual(result["profile"]["profile_name"], "No profile")

    def test_the_profile_for_everyone_is_a_shared_source(self) -> None:
        self.helper.queue_id = "general-profile"
        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        self.assertEqual(run["source"], "everyone")
        self.assertIn("share", run["source_note"])

    def test_a_reset_network_has_nothing_to_measure(self) -> None:
        self.repository.update_runtime_state(
            observed_state="reset", last_action="reset", last_result="success", last_error=None)
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(run["source"], "none")
        self.assertIn("reset", run["source_note"])

    def test_bad_requests_are_refused(self) -> None:
        for payload in ({}, {"mac_address": "nope"}, {"mac_address": PHONE, "extra": 1},
                        {"mac_address": PHONE, "provider": "fast"},
                        {"mac_address": PHONE, "duration_seconds": 5},
                        {"mac_address": PHONE, "duration_seconds": "30"},
                        {"mac_address": PHONE, "label": "x" * 81}):
            self.assertEqual(self.write("/api/speed-tests/start", payload)[0], 400, payload)
        for payload in ({}, {"result_id": 1}, {"result_id": "1", "label": "x"},
                        {"result_id": 1, "label": "x", "other": 2},
                        {"result_id": 1, "reported": {"download_mbit": -1}}):
            self.assertEqual(self.write("/api/speed-tests/update", payload)[0], 400, payload)
        self.assertEqual(self.write("/api/speed-tests/update", {"result_id": 5, "label": "x"})[0], 404)
        self.assertEqual(self.write("/api/speed-tests/delete", {"result_id": 5})[0], 404)
        self.assertEqual(self.read("/api/speed-tests/compare", "baseline=1&result=x")[0], 400)
        self.assertEqual(self.read("/api/speed-tests/export", "format=xml")[0], 400)
        self.assertEqual(self.read("/api/speed-tests/status", "run=unknown")[0], 404)

    def test_compare_update_delete_and_export(self) -> None:
        _, first = self.write("/api/speed-tests/start", {"mac_address": PHONE, "label": "=cmd"})
        self.run_seconds(first["id"], 5)
        baseline = self.write("/api/speed-tests/finish", {"run_id": first["id"]})[1]["result"]
        _, second = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.run_seconds(second["id"], 5)
        later = self.write("/api/speed-tests/finish", {"run_id": second["id"]})[1]["result"]
        status, updated = self.write("/api/speed-tests/update", {
            "result_id": later["id"], "reported": {"download_mbit": 0.24, "latency_ms": 31}})
        self.assertEqual((status, updated["reported"]["download_mbit"]), (200, 0.24))
        status, _, body = self.read(
            "/api/speed-tests/compare", f"baseline={baseline['id']}&result={later['id']}")
        self.assertEqual(status, 200)
        changes = {item["field"]: item for item in json.loads(body)["changes"]}
        self.assertEqual(changes["download_busy_kbit"]["change"], 0.0)
        status, content_type, body = self.read("/api/speed-tests/export", "format=csv")
        self.assertEqual((status, content_type), (200, "text/csv; charset=utf-8"))
        text = body.decode()
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["label"], "'=cmd")
        status, content_type, body = self.read("/api/speed-tests/export", "format=json&mac=" + PHONE)
        exported = json.loads(body)
        self.assertEqual(exported["format"], "jittersim-speed-tests")
        self.assertEqual(len(exported["results"]), 2)
        for secret in ("token", "password", "secret", "Bearer"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, body.decode())
        self.assertEqual(self.write("/api/speed-tests/delete", {"result_id": baseline["id"]}),
                         (200, {"deleted": True}))


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="admin-token")
        self.addCleanup(self.server.server_close)

    def test_every_write_is_registered(self) -> None:
        self.assertTrue(SPEED_TEST_WRITES <= WRITE_PATHS)

    def test_every_route_needs_the_admin_token(self) -> None:
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            for method, paths in (("GET", SPEED_TEST_READS), ("POST", SPEED_TEST_WRITES)):
                for path in sorted(paths):
                    for headers in ({}, {"Authorization": "Bearer wrong"}):
                        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
                        connection.request(method, path, body="{}" if method == "POST" else None,
                                           headers={**headers, "Content-Type": "application/json"})
                        answer = connection.getresponse()
                        answer.read()
                        connection.close()
                        self.assertEqual(answer.status, 401, (method, path, headers))
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            connection.request("GET", "/api/speed-tests",
                               headers={"Authorization": "Bearer admin-token"})
            answer = connection.getresponse()
            body = json.loads(answer.read())
            connection.close()
            self.assertEqual(answer.status, 200)
            self.assertEqual(body["results"], [])
        finally:
            self.server.shutdown()
            thread.join(timeout=5)

    def test_the_default_manager_is_made_once(self) -> None:
        self.assertIs(routes.speed_test_manager(self.server), routes.speed_test_manager(self.server))


if __name__ == "__main__":
    unittest.main()
