"""Device speed test: the arithmetic, storage, the measurement window and its routes."""

import csv
import hashlib
import http.client
import io
import json
import sqlite3
import struct
import tempfile
import threading
import unittest
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path
from unittest import mock

from jittersim.api import speed_tests as routes
from jittersim.api.server import WRITE_PATHS, ControlServer
from jittersim.api.speed_tests import SPEED_TEST_READS, SPEED_TEST_WRITES, SpeedTestManager
from jittersim.config import speed_tests as store_module
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Device, Impairment, Profile, ServiceList
from jittersim.config.speed_tests import SpeedTestStore, clean_label, clean_reported
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.activity import Source, TargetState
from jittersim.engine.general import compile_general_plan
from jittersim.engine.plan import (
    EVERYONE_ELSE_QUEUE_ID,
    GENERAL_QUEUE_ID,
    Direction,
    EveryoneElseRule,
    GeneralProfileRule,
    MarkRule,
    NetworkPlan,
    QueueSpec,
    ServiceAssignmentRule,
    ServiceListRule,
    ServiceQuotaRule,
)
from jittersim.networking.helper import HelperClient, HelperClientError
from jittersim.request_workers import WorkersStillRunning
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
    VERDICT_LABELS,
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
        probes = {1: {"rtt_ms": 10.0}, 2: {"rtt_ms": None}, 3: {"rtt_ms": 30.0},
                  4: {"rtt_ms": 20.0}}
        summary = summarize_probes(probes, "measured")
        self.assertEqual((summary["sent"], summary["received"], summary["loss_percent"]),
                         (4, 3, 25.0))
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

    def test_compare_says_a_shared_queue_round_trip_misses_the_profile_delay(self) -> None:
        baseline = {"id": 1, "mac_address": PHONE, "source": "device", "status": "complete",
                    "summary": {"latency": {"rtt_median_ms": 105.0, "shaped": True}}}
        later = {"id": 2, "mac_address": PHONE, "source": "everyone", "status": "complete",
                 "summary": {"latency": {"rtt_median_ms": 5.0, "shaped": False}}}

        warnings = compare_results(baseline, later)["warnings"]

        self.assertTrue(any("round trip" in warning and "delay" in warning
                            for warning in warnings), warnings)

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

    def test_the_two_addresses_and_their_stored_qr_rows_are_pinned(self) -> None:
        # A change to an address or to a stored code must be deliberate: update this with it.
        self.assertEqual([provider["url"] for provider in PROVIDERS],
                         ["https://speed.cloudflare.com/", "https://librespeed.org/"])
        text = "\n".join(
            f"{provider['id']} {provider['url']} {provider['qr_size']} "
            f"{' '.join(provider['qr_rows'])}" for provider in PROVIDERS)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),
                         "26c47bc3ab978fe12d1d1f621ae74558504f2972a96793282b81221236066f98")

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
        updated = self.store.update(saved["id"], label="  No   limits ",
                                    reported={"download_mbit": 48.5})
        self.assertEqual(updated["label"], "No limits")
        self.assertEqual(updated["reported"], {"download_mbit": 48.5})
        self.assertIsNone(self.store.update(999, label="x"))
        self.assertTrue(self.store.delete(saved["id"]))
        self.assertFalse(self.store.delete(saved["id"]))

    def test_update_and_delete_roll_back_and_report_a_database_fault(self) -> None:
        saved = self.store.save(self.row())
        for call in (lambda: self.store.update(saved["id"], label="x"),
                     lambda: self.store.delete(saved["id"])):
            with mock.patch.object(self.store, "db") as database, \
                    mock.patch.object(self.repository, "_rollback") as rollback:
                database.execute.side_effect = sqlite3.OperationalError("database is locked")
                with self.assertRaises(StorageError):
                    call()
                rollback.assert_called_once()
        self.assertEqual(self.store.get(saved["id"])["label"], "")

    def test_a_fault_is_a_saved_status(self) -> None:
        self.assertEqual(self.store.save(self.row(status="fault"))["status"], "fault")

    def test_a_database_fault_on_a_read_is_a_storage_error(self) -> None:
        saved = self.store.save(self.row())
        with mock.patch.object(self.store, "db") as database:
            database.execute.side_effect = sqlite3.OperationalError("database is locked")
            with self.assertRaises(StorageError):
                self.store.get(saved["id"])
            with self.assertRaises(StorageError):
                self.store.list(PHONE)
            with self.assertRaises(StorageError):
                self.store.list()

    def test_one_malformed_row_does_not_hide_the_others(self) -> None:
        first = self.store.save(self.row(label="first"))
        broken_json = self.store.save(self.row(label="broken json"))
        not_an_object = self.store.save(self.row(label="not an object"))
        last = self.store.save(self.row(label="last"))
        self.repository._connection.execute(
            "UPDATE speed_test_results SET summary_json = '{' WHERE id = ?", (broken_json["id"],))
        self.repository._connection.execute(
            "UPDATE speed_test_results SET profile_json = '[1]' WHERE id = ?",
            (not_an_object["id"],))
        for call in (lambda: self.store.list(PHONE), lambda: self.store.list()):
            with self.assertLogs(store_module.LOGGER, "WARNING") as logged:
                listed = call()
            self.assertEqual([item["id"] for item in listed], [last["id"], first["id"]])
            self.assertEqual(len(logged.records), 2)
        # A row that is asked for by number still fails clearly, as a storage error.
        with self.assertRaises(StorageError):
            self.store.get(broken_json["id"])
        with self.assertRaises(StorageError):
            self.store.get(not_an_object["id"])
        self.assertEqual(self.store.get(first["id"])["label"], "first")

    def test_label_and_reported_validation(self) -> None:
        self.assertEqual(clean_label(None), "")
        for bad in ("x" * 81, 5, "a\x00b", "a\x7fb"):
            with self.assertRaises(ValueError):
                clean_label(bad)
        # C1 controls (Cc) and format characters (Cf): the NEL control is whitespace and is folded
        # into a space, so the checks use the others.
        for bad in ("a\x80b", "a\x9bb", "a\u202eb", "\u2066a", "a\u200bb", "\ufeffa", "a\u00adb"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                clean_label(bad)
        self.assertEqual(clean_label("Café 日本語 😀  No\tlimits"), "Café 日本語 😀 No limits")
        # Emoji sequences and Persian words keep their joiner and non-joiner.
        for joined in ("👩\u200d💻", "🏳\ufe0f\u200d🌈", "می\u200cخواهم"):
            self.assertEqual(clean_label(joined), joined)
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
        "impairment": {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "loss_burst_packets": 0,
                       "bandwidth_kbit": 250 if direction == "download" else None,
                       "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0},
        "packets": moved // 100, "bytes": moved, "drops": 0, "overlimits": 0, "requeues": 0,
        "backlog_bytes": 0, "qlen": 0,
    }
    entry.update(changes)
    return entry


def through_the_client(entries):
    """What the dashboard sees: the helper's answer after the client has checked and cleaned it."""

    client = HelperClient(socket_path=Path("unused.sock"))
    client._request = lambda payload, **kwargs: {
        "ok": True, "action": "queue-stats", "queues": entries}
    return client.queue_stats()


class CountingHelper(FakeHelper):
    """Queues that move 250 kbit/s down and 100 kbit/s up per second of the test clock."""

    def __init__(self, clock, queue_id="lab"):
        super().__init__()
        self.clock = clock
        self.queue_id = queue_id
        self.generation = "0a1b2c3d-1"
        self.fail = False
        self.snapshot_fail = False
        self.association = True
        self.on_wifi = [PHONE, OTHER]
        self.addresses = {PHONE: "192.168.50.10", OTHER: "192.168.50.77"}
        self.queue_reads = 0
        self.fail_next = 0
        self.on_read = None

    def queue_stats(self):
        self.queue_reads += 1
        if self.on_read is not None:
            hook, self.on_read = self.on_read, None
            hook()
        if self.fail or self.fail_next > 0:
            self.fail_next = max(0, self.fail_next - 1)
            raise HelperClientError("helper is busy", reason="helper-busy")
        seconds = self.clock[0] - 100
        return [
            queue(self.queue_id, "download", int(250 * 125 * seconds), generation=self.generation),
            queue(self.queue_id, "upload", int(100 * 125 * seconds), generation=self.generation),
            queue("someone-else", "download", 5),
        ]

    def connected_devices(self):
        return [
            {"mac_address": mac, "ipv4_address": self.addresses[mac], "state": "connected",
             "association_verified": self.association}
            for mac in self.on_wifi
        ]

    def connected_device_snapshot(self):
        if self.snapshot_fail:
            raise HelperClientError("helper is busy", reason="helper-busy")
        return {"devices": self.connected_devices(),
                "association_status_available": self.association}


def planned_impairment(direction):
    return Impairment(bandwidth_kbit=250) if direction == Direction.DOWNLOAD else Impairment()


class FakeScheduler:
    """Answers the applied snapshot like the runtime scheduler does."""

    def __init__(self):
        self.states = {}
        queue_ids = ("lab", GENERAL_QUEUE_ID, EVERYONE_ELSE_QUEUE_ID)
        # The same impairments CountingHelper reports, so counters and plan correspond.
        queues = tuple(
            QueueSpec(device_id=queue_id, direction=direction, mark=1,
                      impairment=planned_impairment(direction))
            for queue_id in queue_ids
            for direction in (Direction.UPLOAD, Direction.DOWNLOAD)
        )
        # The lab phone's address is marked into its own queue. The shared rules are present so
        # every source can be tested with one plan; each test narrows the plan where it matters.
        self.plan = NetworkPlan(
            mark_rules=(MarkRule(device_id="lab", ipv4_address=IPv4Address("192.168.50.10"),
                                 mark=1),),
            queues=queues,
            general_profile=GeneralProfileRule(profile_id="slow"),
            everyone_else=EveryoneElseRule(mark=2, upload_impairment=Impairment(),
                                           download_impairment=Impairment()),
        )

    def set_source(self, key, kind, profile, label=""):
        self.states[key] = TargetState(name=key, source=Source(kind, kind, label, "", profile))

    def applied_snapshot(self):
        return self.plan, dict(self.states)

    def begin_shutdown(self):
        pass


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


class ManagerBase(unittest.TestCase):
    """A manager with a fake helper, scheduler and clock. Holds no tests of its own."""

    def setUp(self) -> None:
        self.clock = [100.0]
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(
            Profile(id="slow", name="Slow", download=Impairment(bandwidth_kbit=250)))
        self.repository.save_device(
            Device("lab", "Lab phone", "192.168.50.10", "slow", mac_address=PHONE))
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
        self.scheduler = FakeScheduler()
        self.scheduler.set_source("lab", "profile", "Slow")
        self.server.scheduler = self.scheduler
        self.sleeps = []
        # One test clock for both: wall time dates the rows, the monotonic clock times the window.
        self.manager = SpeedTestManager(
            self.server, autostart=False, clock=lambda: self.clock[0],
            monotonic=lambda: self.clock[0], sleep=self.sleeps.append)
        self.server._speed_test_manager = self.manager

    def write(self, path, payload):
        status, _, body = self.server.write_response_for(path, payload)
        return status, json.loads(body)

    def read(self, path, query=""):
        status, content_type, body = self.server.response_for(path, query=query)
        return status, content_type, body

    def start_short(self, mac=PHONE):
        return self.write("/api/speed-tests/start", {"mac_address": mac, "duration_seconds": 15})

    def run_seconds(self, run_id, seconds):
        for _ in range(seconds):
            self.clock[0] += 1
            self.manager.tick(run_id)


class ManagerTests(ManagerBase):
    def test_failed_sampler_thread_start_does_not_leave_an_active_run(self) -> None:
        self.manager.autostart = True
        self.manager.sleep = lambda seconds: threading.Event().wait(0.001)
        original_start = threading.Thread.start

        def start_then_fail(thread):
            original_start(thread)
            raise RuntimeError("thread start outcome was interrupted")

        with mock.patch.object(routes.threading.Thread, "start", autospec=True,
                               side_effect=start_then_fail):
            with self.assertRaisesRegex(RuntimeError, "thread start outcome was interrupted"):
                self.manager.start({"mac_address": PHONE})

        self.assertEqual(self.manager.active(), [])
        run = next(iter(self.manager.runs.values()))
        self.assertTrue(self.manager.closed)
        self.assertEqual(run.state, "ended")
        self.manager.shutdown(timeout=5)
        self.assertFalse(run.thread.is_alive())

    def test_unresolved_sampler_start_keeps_ownership_until_restart(self) -> None:
        self.manager.autostart = True
        with mock.patch.object(routes.threading.Thread, "start",
                               side_effect=RuntimeError("thread start was interrupted")):
            with self.assertRaisesRegex(RuntimeError, "thread start was interrupted"):
                self.manager.start({"mac_address": PHONE})

        run = next(iter(self.manager.runs.values()))
        self.assertTrue(run.startup_uncertain)
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.manager.active(), [])
        with self.assertRaises(WorkersStillRunning):
            self.manager.shutdown(timeout=5)

        # This test models an unresolved native start, so release its fake ownership at teardown.
        self.manager.runs.clear()
        run.prober.close()

    def test_shutdown_reports_a_sampler_blocked_in_a_helper_read(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        read_queues = self.helper.queue_stats

        def blocked_read():
            if self.helper.queue_reads == 0:
                return read_queues()
            entered.set()
            if not release.wait(2):
                raise HelperClientError("test helper was not released")
            return read_queues()

        self.manager.autostart = True
        self.helper.queue_stats = blocked_read
        _, run = self.start_short()
        sampler = self.manager.runs[run["id"]].thread

        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(WorkersStillRunning):
                self.manager.shutdown(timeout=0.02)
            self.assertTrue(sampler.is_alive())
        finally:
            release.set()
            sampler.join(timeout=2)

        self.assertFalse(sampler.is_alive())

    def test_a_full_window_measures_the_device_queues_and_saves_the_result(self) -> None:
        status, run = self.write("/api/speed-tests/start",
                                 {"mac_address": PHONE.upper(), "duration_seconds": 15,
                                  "label": "Slow"})
        self.assertEqual(status, 201)
        self.assertEqual((run["source"], run["state"], run["device_name"]),
                         ("device", "running", "Lab phone"))
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
        self.assertEqual(
            self.write("/api/speed-tests/update", {"result_id": 5, "label": "x"})[0], 404)
        self.assertEqual(self.write("/api/speed-tests/delete", {"result_id": 5})[0], 404)
        self.assertEqual(self.read("/api/speed-tests/compare", "baseline=1&result=x")[0], 400)
        self.assertEqual(self.read("/api/speed-tests/export", "format=xml")[0], 400)
        self.assertEqual(self.read("/api/speed-tests", "&".join(["a=1"] * 20))[0], 400)
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
        status, content_type, body = self.read(
            "/api/speed-tests/export", "format=json&mac=" + PHONE)
        exported = json.loads(body)
        self.assertEqual(exported["format"], "jittersim-speed-tests")
        self.assertEqual(len(exported["results"]), 2)
        for secret in ("token", "password", "secret", "Bearer"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, body.decode())
        self.assertEqual(self.write("/api/speed-tests/delete", {"result_id": baseline["id"]}),
                         (200, {"deleted": True}))


class StartChecksTests(ManagerBase):
    """Reading the helper before a run starts, and what the run records."""

    def test_a_busy_helper_is_retried_before_the_start_is_refused(self) -> None:
        self.helper.fail_next = 2
        status, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual((status, run["source"]), (201, "device"))
        self.assertEqual(self.sleeps, [0.25, 0.25])

    def test_a_helper_that_stays_busy_gives_a_retryable_refusal_not_a_blind_run(self) -> None:
        self.helper.fail = True
        status, body = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(status, 503)
        self.assertIn("Try again", body["error"])
        self.assertEqual(self.helper.queue_reads, 3)
        self.assertEqual(self.manager.active(), [])
        self.helper.fail = False
        self.assertEqual(self.write("/api/speed-tests/start", {"mac_address": PHONE})[0], 201)

    def test_none_is_only_for_a_good_read_with_no_queue(self) -> None:
        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        self.assertEqual(run["source"], "none")
        self.assertIsNone(self.manager.runs[run["id"]].service_rules)
        self.assertEqual(self.helper.queue_reads, 1)
        finished = self.manager.finish(run["id"])["result"]
        self.assertNotIn("service_rules", finished["summary"])

    def test_most_reads_failing_ends_the_run_as_a_fault_with_a_reason(self) -> None:
        _, run = self.start_short()
        self.helper.fail = True
        self.run_seconds(run["id"], 15)
        ended = self.manager.status(run["id"])
        self.assertEqual(ended["state"], "ended")
        self.assertIn("could not read", ended["ended_reason"])
        self.assertEqual(ended["result"]["status"], "fault")
        self.assertEqual(ended["result"]["summary"]["read_failures"], 15)
        self.assertEqual(ended["result"]["summary"]["ended_reason"], ended["ended_reason"])

    def test_a_few_failed_reads_are_counted_but_the_run_still_completes(self) -> None:
        _, run = self.start_short()
        self.helper.fail = True
        self.run_seconds(run["id"], 3)
        self.helper.fail = False
        self.run_seconds(run["id"], 12)
        result = self.manager.status(run["id"])["result"]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["summary"]["read_failures"], 3)

    def test_the_saved_profile_is_the_one_the_scheduler_applied(self) -> None:
        self.repository.save_profile(Profile(id="night", name="Night"))
        self.scheduler.set_source("lab", "schedule", "Night", label="Weeknights")
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        profile = self.manager.runs[run["id"]].profile
        self.assertEqual((profile["profile_name"], profile["profile_id"]), ("Night", "night"))
        self.assertEqual((profile["applied_by"], profile["applied_by_label"]),
                         ("schedule", "Weeknights"))

    def test_the_shared_general_profile_uses_the_applied_scheduler_source(self) -> None:
        self.repository.save_profile(Profile(id="night", name="Night"))
        self.repository.save_general_profile("slow")
        self.scheduler.set_source(GENERAL_QUEUE_ID, "schedule", "Night", label="Weeknights")
        self.helper.queue_id = GENERAL_QUEUE_ID

        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        profile = self.manager.runs[run["id"]].profile

        self.assertEqual((profile["profile_name"], profile["profile_id"]), ("Night", "night"))
        self.assertEqual((profile["applied_by"], profile["applied_by_label"]),
                         ("schedule", "Weeknights"))

    def test_the_real_general_plan_confirms_the_shared_profile_and_service_rules(self) -> None:
        # The scheduler's general plan has no per-queue source entry. Its general profile rule
        # and the general queue are what confirm the shared profile.
        self.repository.save_general_profile("slow")
        self.scheduler.plan = compile_general_plan(
            repository=self.repository, profile=self.repository.get_profile("slow"))
        self.scheduler.states = {}
        self.helper.queue_id = GENERAL_QUEUE_ID

        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        manager_run = self.manager.runs[run["id"]]

        self.assertEqual(run["source"], "everyone")
        self.assertEqual((manager_run.profile["profile_name"], manager_run.profile["profile_id"]),
                         ("Slow", "slow"))
        self.assertIs(manager_run.service_rules, False)

    def test_a_saved_device_without_a_mac_is_found_by_its_current_address(self) -> None:
        # The plan shapes a saved device with no MAC address by its address, in its own queue.
        self.repository.save_device(Device("lab", "Lab phone", "192.168.50.10", "slow"))

        status, run = self.start_short()

        self.assertEqual(status, 201, run)
        self.assertEqual(run["source"], "device")
        self.assertEqual(self.manager.runs[run["id"]].profile["profile_name"], "Slow")

    def test_the_queue_that_marks_the_current_address_is_measured(self) -> None:
        # The phone is saved at .20 but uses .10 now. The scheduler kept .10 with a saved
        # device that has no MAC address, so the phone's traffic goes through that queue.
        self.repository.save_profile(
            Profile(id="fast", name="Fast", download=Impairment(bandwidth_kbit=5000)))
        self.repository.save_device(
            Device("lab", "Lab phone", "192.168.50.20", "fast", mac_address=PHONE))
        self.repository.save_device(Device("old", "Old tablet", "192.168.50.10", "slow"))
        self.scheduler.plan = replace(self.scheduler.plan, mark_rules=(
            MarkRule(device_id="old", ipv4_address=IPv4Address("192.168.50.10"), mark=1),
            MarkRule(device_id="lab", ipv4_address=IPv4Address("192.168.50.20"), mark=2)),
            queues=self.scheduler.plan.queues + tuple(
                QueueSpec(device_id="old", direction=direction, mark=1,
                          impairment=planned_impairment(direction))
                for direction in (Direction.UPLOAD, Direction.DOWNLOAD)))
        self.scheduler.set_source("old", "profile", "Slow")
        self.scheduler.set_source("lab", "profile", "Fast")
        reading = self.helper.queue_stats
        self.helper.queue_stats = lambda: reading() + [
            dict(entry, device_id="old") for entry in reading() if entry["device_id"] == "lab"]

        _, run = self.start_short()
        manager_run = self.manager.runs[run["id"]]

        self.assertEqual(run["source"], "device")
        self.assertEqual(manager_run.queue_id, "old")
        self.assertEqual(manager_run.profile["profile_name"], "Slow")
        self.assertIn("Old tablet", run["source_note"])

    def test_a_saved_device_id_like_a_shared_queue_is_not_taken_for_it(self) -> None:
        # In a per-device plan there is no general profile rule. A saved device whose id looks
        # like the general queue must not be measured as the profile for everyone.
        self.repository.save_device(Device(GENERAL_QUEUE_ID, "Odd name", "192.168.50.30", "slow",
                                           mac_address="02:00:00:00:00:30"))
        self.scheduler.plan = replace(self.scheduler.plan, general_profile=None)
        reading = self.helper.queue_stats
        self.helper.queue_stats = lambda: [
            dict(entry, device_id=queue_id) for entry in reading()
            if entry["device_id"] == "lab"
            for queue_id in (GENERAL_QUEUE_ID, EVERYONE_ELSE_QUEUE_ID)]

        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})

        self.assertEqual(run["source"], "everyone-else")

    def test_a_queue_reading_with_one_direction_keeps_status_and_finish_working(self) -> None:
        reading = self.helper.queue_stats

        def download_only():
            return [entry for entry in reading() if entry["direction"] == "download"]

        self.helper.queue_stats = download_only
        _, run = self.start_short()
        self.run_seconds(run["id"], 3)

        status = self.manager.status(run["id"])
        self.assertIsNone(status["live"]["upload"]["kbit"])
        self.assertIsNotNone(status["live"]["download"]["kbit"])
        code, ended = self.write("/api/speed-tests/finish", {"run_id": run["id"]})
        self.assertEqual(code, 200, ended)
        self.assertEqual(ended["result"]["summary"]["upload"]["verdict"], "not-measured")

    def test_a_missing_applied_plan_does_not_confirm_a_saved_rule_is_absent(self) -> None:
        self.repository.save_service_list(
            ServiceList(id="streaming", name="Streaming", profile_id="slow",
                        services=("netflix",)))
        self.scheduler.plan = None

        _, run = self.start_short()
        self.assertIsNone(self.manager.runs[run["id"]].service_rules)
        self.assertIn("could not be confirmed", run["source_note"])
        self.run_seconds(run["id"], 15)
        result = self.manager.status(run["id"])["result"]

        self.assertIsNone(result["summary"]["service_rules"])
        self.assertEqual(result["summary"]["download"]["verdict"], "service-rules-unknown")
        self.assertIn("could not be confirmed", VERDICT_LABELS["service-rules-unknown"])
        comparison = compare_results(result, result)
        self.assertEqual(comparison["verdicts"]["download"]["result"],
                         "service-rules-unknown")
        self.assertTrue(any("could not confirm" in warning
                            for warning in comparison["warnings"]))
        self.assertIn("service-rules-unknown", export_csv([result]))

    def test_a_saved_but_unapplied_service_list_does_not_affect_the_verdict(self) -> None:
        self.repository.save_service_list(
            ServiceList(id="streaming", name="Streaming", profile_id="slow",
                        services=("netflix",)))

        _, run = self.start_short()
        self.assertFalse(self.manager.runs[run["id"]].service_rules)
        self.assertNotIn("service rule", run["source_note"] or "")
        self.run_seconds(run["id"], 15)
        result = self.manager.status(run["id"])["result"]

        self.assertIs(result["summary"]["service_rules"], False)
        self.assertEqual(result["summary"]["download"]["verdict"], "at-cap")

    def test_applied_service_assignments_and_active_byte_or_time_quotas_match_device(self) -> None:
        assignment = ServiceAssignmentRule(
            assignment_id="browser", device_id="someone-else",
            device_ip=IPv4Address("192.168.50.77"), domains=("example.test",), mark=3,
            upload_impairment=Impairment(), download_impairment=Impairment())
        self.scheduler.plan = replace(self.scheduler.plan,
                                      service_assignment_rules=(assignment,))
        _, unrelated = self.start_short()
        self.assertFalse(self.manager.runs[unrelated["id"]].service_rules)
        self.manager.cancel(unrelated["id"])

        assignment = replace(assignment, device_id="lab", device_ip=IPv4Address("192.168.50.10"))
        self.scheduler.plan = replace(self.scheduler.plan,
                                      service_assignment_rules=(assignment,))
        _, matched = self.start_short()
        self.assertTrue(self.manager.runs[matched["id"]].service_rules)
        self.manager.cancel(matched["id"])

        for quota_kind in ("bytes", "time"):
            for active, expected in ((False, False), (True, True)):
                quota = ServiceQuotaRule(
                    quota_id=f"{quota_kind}-quota", device_id="lab",
                    device_ip=IPv4Address("192.168.50.10"),
                    destination_addresses=(IPv4Address("203.0.113.10"),), mark=4,
                    upload_impairment=Impairment(), download_impairment=Impairment(),
                    active=active)
                self.scheduler.plan = replace(
                    self.scheduler.plan, service_assignment_rules=(), service_quota_rules=(quota,))
                _, run = self.start_short()
                self.assertIs(self.manager.runs[run["id"]].service_rules, expected,
                              (quota_kind, active))
                self.manager.cancel(run["id"])

    def test_counters_of_a_queue_the_applied_plan_lacks_are_refused_to_retry(self) -> None:
        # The helper shows the general queue but the published plan has none: the two describe
        # different applies, so no profile can be trusted for these counters.
        self.scheduler.set_source(GENERAL_QUEUE_ID, "profile", "Slow")
        self.helper.queue_id = GENERAL_QUEUE_ID
        self.scheduler.plan = replace(
            self.scheduler.plan,
            queues=tuple(queue for queue in self.scheduler.plan.queues
                         if queue.device_id != GENERAL_QUEUE_ID))

        status, body = self.write("/api/speed-tests/start", {"mac_address": OTHER})

        self.assertEqual(status, 503, body)
        self.assertEqual(self.manager.active(), [])

    def test_counters_from_a_newer_apply_than_the_published_plan_are_refused(self) -> None:
        # The helper already runs a 500 kbit/s queue while the scheduler still publishes the
        # 250 kbit/s plan. Recording "Slow" next to those counters would be wrong.
        reading = self.helper.queue_stats

        def newer_apply():
            return [dict(entry, impairment=dict(entry["impairment"], bandwidth_kbit=500))
                    if entry["direction"] == "download" else entry for entry in reading()]

        self.helper.queue_stats = newer_apply

        status, body = self.start_short()

        self.assertEqual(status, 503, body)
        self.assertIn("applying a change", body["error"])
        self.assertEqual(self.manager.active(), [])

    def burst_loss_plan_and_reading(self, planned_burst, reported_burst):
        """The lab phone's upload queue drops 5 percent in bursts, read through the real client."""

        plan = self.scheduler.plan
        self.scheduler.plan = replace(plan, queues=tuple(
            replace(spec, impairment=Impairment(loss_percent=5, loss_burst_packets=planned_burst))
            if spec.device_id == "lab" and spec.direction == Direction.UPLOAD else spec
            for spec in plan.queues))
        reading = self.helper.queue_stats

        def read_raw():
            return through_the_client([
                dict(entry, impairment=dict(entry["impairment"], loss_percent=5,
                                            loss_burst_packets=reported_burst))
                if entry["device_id"] == "lab" and entry["direction"] == "upload" else entry
                for entry in reading()])

        self.helper.queue_stats = read_raw

    def test_the_start_check_sees_the_burst_length_of_the_loss(self) -> None:
        # The helper client used to drop loss_burst_packets, so a queue that drops 5 percent in
        # bursts of 4 passed for a plan that asks for bursts of 8. The client keeps it now.
        self.burst_loss_plan_and_reading(planned_burst=8, reported_burst=4)

        status, body = self.start_short()

        self.assertEqual(status, 503, body)
        self.assertIn("applying a change", body["error"])
        self.assertEqual(self.manager.active(), [])

    def test_a_queue_with_the_planned_burst_length_starts(self) -> None:
        self.burst_loss_plan_and_reading(planned_burst=8, reported_burst=8)

        status, run = self.start_short()

        self.assertEqual((status, run["source"]), (201, "device"))

    def test_missing_dedicated_counters_never_fall_back_to_a_shared_queue(self) -> None:
        # The plan marks the phone into its own queue and keeps it out of Everyone else. If the
        # helper does not list that queue, Everyone else holds only other devices' traffic.
        reading = self.helper.queue_stats
        self.helper.queue_stats = lambda: [
            dict(entry, device_id=EVERYONE_ELSE_QUEUE_ID) for entry in reading()
            if entry["device_id"] == "lab"]

        status, body = self.start_short()

        self.assertEqual(status, 503, body)
        self.assertEqual(self.manager.active(), [])

    def test_a_dedicated_queue_with_a_shared_looking_id_is_measured_as_the_device(self) -> None:
        # A real per-device plan can give a saved device the id general-profile. Its mark rule
        # makes the queue dedicated, so it is measured as the device's own queue.
        self.repository.delete_device("lab")
        self.repository.save_device(Device(GENERAL_QUEUE_ID, "Lab phone", "192.168.50.10", "slow",
                                           mac_address=PHONE))
        self.scheduler.plan = replace(
            self.scheduler.plan, general_profile=None,
            mark_rules=(MarkRule(device_id=GENERAL_QUEUE_ID,
                                 ipv4_address=IPv4Address("192.168.50.10"), mark=1),))
        self.scheduler.set_source(GENERAL_QUEUE_ID, "profile", "Slow")
        self.helper.queue_id = GENERAL_QUEUE_ID

        status, run = self.start_short()
        self.assertEqual(status, 201, run)
        self.assertEqual(run["source"], "device")
        self.run_seconds(run["id"], 3)
        self.assertEqual(self.manager.status(run["id"])["state"], "running")

    def test_a_progression_has_no_single_profile_name(self) -> None:
        self.scheduler.set_source("lab", "progression", "", label="from Slow to Night")
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        profile = self.manager.runs[run["id"]].profile
        self.assertIsNone(profile["profile_id"])
        self.assertIn("Progression", profile["profile_name"])
        self.assertEqual(profile["applied_by"], "progression")

    def test_an_unconfirmed_source_is_not_shown_as_the_saved_assignment(self) -> None:
        self.scheduler.states.clear()
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        profile = self.manager.runs[run["id"]].profile
        self.assertTrue(profile["profile_name"].startswith("Unknown"))
        self.assertIsNone(profile["profile_id"])
        self.assertIsNone(profile["saved"])
        self.server.scheduler = None
        self.manager.runs.clear()
        _, again = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertTrue(self.manager.runs[again["id"]].profile["profile_name"].startswith("Unknown"))

    def test_the_device_must_be_connected_and_its_current_address_is_pinged(self) -> None:
        self.helper.addresses[PHONE] = "192.168.50.99"
        self.assertEqual(self.write("/api/speed-tests/start", {"mac_address": PHONE})[0], 201)
        self.assertEqual(self.targets, ["192.168.50.99"])
        self.helper.on_wifi = [OTHER]
        status, body = self.write("/api/speed-tests/start", {"mac_address": "02:00:00:00:00:09"})
        self.assertEqual(status, 409)
        self.assertIn("not connected", body["error"])
        self.manager.runs.clear()
        status, body = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(status, 409, "a saved device that left the Wi-Fi is not tested either")

    def test_an_unknown_connection_status_is_refused_as_something_to_retry(self) -> None:
        self.helper.association = False
        status, body = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(status, 503)
        self.assertIn("unknown", body["error"])
        self.helper.association = True
        self.helper.snapshot_fail = True
        self.assertEqual(self.write("/api/speed-tests/start", {"mac_address": PHONE})[0], 503)
        self.server.helper_client = None
        status, body = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(status, 503)
        self.assertIn("helper is not connected", body["error"])

    def test_the_helper_is_read_outside_the_lock_and_the_limits_are_checked_again(self) -> None:
        owned = []
        raced = []

        def during_the_read():
            owned.append(self.manager.lock._is_owned())
            raced.append(self.manager.start({"mac_address": PHONE}))

        self.helper.on_read = during_the_read
        status, body = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.assertEqual(owned, [False])
        self.assertEqual(len(raced), 1)
        self.assertEqual(status, 409)
        self.assertIn("already running", body["error"])
        self.assertEqual(len(self.manager.active()), 1)

    def test_a_service_list_withholds_the_cap_conclusion(self) -> None:
        self.repository.save_service_list(
            ServiceList(id="streaming", name="Streaming", profile_id="slow",
                        services=("netflix",)))
        rule = ServiceListRule(
            list_id="streaming", domains=("netflix.com",), mark=0,
            upload_impairment=Impairment(), download_impairment=Impairment())
        self.scheduler.plan = replace(self.scheduler.plan, service_list_rules=(rule,))
        _, run = self.start_short()
        self.assertIn("service rule", run["source_note"])
        self.assertIn("ordinary queue only", run["source_note"])
        self.run_seconds(run["id"], 15)
        result = self.manager.status(run["id"])["result"]
        self.assertTrue(result["summary"]["service_rules"])
        self.assertEqual(result["summary"]["download"]["verdict"], "service-rules")
        self.assertEqual(result["summary"]["upload"]["verdict"], "no-cap")
        self.assertEqual(result["summary"]["download"]["busy_average_kbit"], 250.0)
        comparison = compare_results(result, result)
        self.assertEqual(comparison["verdicts"]["download"]["result"], "service-rules")
        self.assertTrue(any("service rules" in warning for warning in comparison["warnings"]))

    def test_a_shared_queue_says_the_ping_does_not_pass_it(self) -> None:
        self.helper.queue_id = "general-profile"
        _, run = self.write("/api/speed-tests/start", {"mac_address": OTHER})
        self.assertIn("does not include the profile's delay", run["source_note"])
        _, finished = self.write("/api/speed-tests/finish", {"run_id": run["id"]})
        latency = finished["result"]["summary"]["latency"]
        self.assertFalse(latency["shaped"])
        self.assertIn("does not include", latency["note"])
        self.helper.queue_id = "lab"
        own = self.write("/api/speed-tests/start", {"mac_address": PHONE})[1]
        finished = self.write("/api/speed-tests/finish", {"run_id": own["id"]})[1]
        self.assertTrue(finished["result"]["summary"]["latency"]["shaped"])


class EndingTests(ManagerBase):
    """How a window ends: protection, faults, the clocks and shutdown."""

    def test_finish_and_cancel_do_not_save_a_device_that_became_protected(self) -> None:
        _, first = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.run_seconds(first["id"], 5)
        self.repository.save_unaffected_device(UnaffectedDevice(PHONE, "Phone", 2))
        status, finished = self.write("/api/speed-tests/finish", {"run_id": first["id"]})
        self.assertEqual((status, finished["result"]), (200, None))
        self.assertEqual(finished["state"], "ended")
        self.assertIn("Protected", finished["ended_reason"])
        self.assertEqual(SpeedTestStore(self.repository).list(), [])

    def test_the_protection_check_in_the_end_covers_a_run_that_ends_by_itself(self) -> None:
        _, run = self.start_short()
        self.run_seconds(run["id"], 14)
        self.repository.save_unaffected_device(UnaffectedDevice(PHONE, "Phone", 2))
        with mock.patch.object(self.manager, "_protected", side_effect=[False, True]):
            self.run_seconds(run["id"], 1)
        self.assertIsNone(self.manager.status(run["id"])["result"])
        self.assertEqual(SpeedTestStore(self.repository).list(), [])

    def test_protection_check_and_result_insert_share_a_repository_transaction(self) -> None:
        _, run = self.start_short()
        original_protected = self.manager._protected
        original_store = self.manager._store()
        depths = []

        def protected(mac_address):
            depths.append(self.repository._transaction_depth)
            return original_protected(mac_address)

        def save(row):
            depths.append(self.repository._transaction_depth)
            return original_store.save(row)

        with mock.patch.object(self.manager, "_protected", side_effect=protected), \
                mock.patch.object(self.manager, "_store", return_value=mock.Mock(save=save)):
            _, finished = self.write("/api/speed-tests/finish", {"run_id": run["id"]})

        self.assertIsNotNone(finished["result"])
        self.assertEqual(depths, [1, 1])

    def test_a_busy_database_does_not_leave_finish_as_a_server_error(self) -> None:
        _, run = self.start_short()
        with mock.patch.object(self.repository, "transaction",
                               side_effect=sqlite3.OperationalError("database is locked")):
            _, finished = self.write("/api/speed-tests/finish", {"run_id": run["id"]})

        self.assertEqual(finished["state"], "ended")
        self.assertIsNone(finished["result"])
        self.assertIn("could not be saved", finished["ended_reason"])

    def test_the_deadline_and_the_rates_follow_the_monotonic_clock(self) -> None:
        wall, mono = [1_000_000.0], [100.0]
        helper = CountingHelper(mono)
        self.server.helper_client = helper
        manager = SpeedTestManager(self.server, autostart=False, clock=lambda: wall[0],
                                   monotonic=lambda: mono[0], sleep=lambda seconds: None)
        run = manager.start({"mac_address": PHONE, "duration_seconds": 15})
        for _ in range(5):
            mono[0] += 1
            wall[0] += 1
            manager.tick(run["id"])
        wall[0] -= 7200  # somebody sets the clock back two hours
        self.assertEqual(manager.status(run["id"])["seconds_left"], 10)
        for _ in range(10):
            mono[0] += 1
            wall[0] += 1
            manager.tick(run["id"])
        ended = manager.status(run["id"])
        self.assertEqual(ended["state"], "ended")
        result = ended["result"]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["started_at"], 1_000_000)
        self.assertEqual(result["summary"]["download"]["busy_average_kbit"], 250.0)
        self.assertEqual(result["summary"]["seconds"], 15.0)

    def test_a_sampler_fault_is_logged_and_saved_as_a_fault_not_as_changed(self) -> None:
        class Failing(QuietProber):
            def send(self):
                raise RuntimeError("socket gone")

        self.server.speed_test_prober_factory = Failing
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        with self.assertLogs(routes.LOGGER, "ERROR") as logs:
            self.manager._loop(self.manager.runs[run["id"]])
        self.assertIn("sampler failed", logs.output[0])
        ended = self.manager.status(run["id"])
        self.assertEqual(ended["result"]["status"], "fault")
        self.assertIn("error", ended["ended_reason"])
        warnings = compare_results(ended["result"], ended["result"])["warnings"]
        self.assertTrue(any("fault" in warning for warning in warnings))
        self.assertFalse(any("profile changed" in warning for warning in warnings))

    def test_shutdown_stops_windows_without_saving_and_joins_the_threads(self) -> None:
        manager = SpeedTestManager(self.server, clock=lambda: self.clock[0],
                                   monotonic=lambda: self.clock[0],
                                   sleep=lambda seconds: threading.Event().wait(0.01))
        run = manager.start({"mac_address": PHONE, "duration_seconds": 15})
        thread = manager.runs[run["id"]].thread
        self.assertTrue(thread.is_alive())
        manager.shutdown(timeout=5)
        self.assertFalse(thread.is_alive())
        ended = manager.status(run["id"])
        self.assertEqual((ended["state"], ended["result"]), ("ended", None))
        self.assertIn("stopped", ended["ended_reason"])
        self.assertEqual(SpeedTestStore(self.repository).list(), [])
        with self.assertRaises(routes.SpeedTestRequestError) as refused:
            manager.start({"mac_address": PHONE})
        self.assertEqual(refused.exception.status, 503)

    def test_shutdown_reports_a_sampler_that_is_still_running(self) -> None:
        _, started = self.start_short()
        run = self.manager.runs[started["id"]]
        run.thread = mock.Mock(ident=1)
        run.thread.is_alive.return_value = True

        with self.assertRaises(WorkersStillRunning):
            self.manager.shutdown(timeout=5)

        run.thread.join.assert_called_once()
        self.assertAlmostEqual(run.thread.join.call_args.kwargs["timeout"], 4.0, places=3)
        run.thread.is_alive.return_value = False

    def test_closing_the_server_stops_the_speed_tests_first(self) -> None:
        with mock.patch.object(self.manager, "shutdown") as stop:
            self.server.server_close()
        stop.assert_called_once()

    def test_a_sampler_that_does_not_stop_still_lets_the_server_close_the_rest(self) -> None:
        stuck = WorkersStillRunning("A speed test sampler did not stop.")
        with mock.patch.object(self.manager, "shutdown", side_effect=stuck), \
                mock.patch.object(self.server._request_workers, "stop_admission",
                                  wraps=self.server._request_workers.stop_admission) as admission:
            with self.assertRaises(WorkersStillRunning):
                self.server.server_close()

        # The listener and every other owner were still told to stop.
        admission.assert_called()
        self.assertEqual(self.server.socket.fileno(), -1)

    def test_ids_in_the_query_and_the_body_never_cause_a_server_error(self) -> None:
        for query in ("baseline=%C2%B2&result=1", "baseline=%D9%A1&result=1",
                      "baseline=9223372036854775808&result=1", "baseline=1&result=" + "9" * 40):
            self.assertEqual(self.read("/api/speed-tests/compare", query)[0], 400, query)
        self.assertEqual(self.read("/api/speed-tests/compare", "baseline=1&result=1")[0], 404)
        self.assertEqual(
            self.read("/api/speed-tests/compare", "baseline=9223372036854775807&result=1")[0], 404)
        for path in ("/api/speed-tests/update", "/api/speed-tests/delete"):
            for identifier in (2**63, 10**30):
                body = {"result_id": identifier}
                if path.endswith("update"):
                    body["label"] = "x"
                self.assertEqual(self.write(path, body)[0], 400, (path, identifier))
        self.assertEqual(self.write("/api/speed-tests/delete", {"result_id": 2**63 - 1})[0], 404)

    def test_a_storage_fault_on_update_or_delete_is_a_clean_error(self) -> None:
        _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
        self.run_seconds(run["id"], 5)
        result = self.write("/api/speed-tests/finish", {"run_id": run["id"]})[1]["result"]
        with mock.patch.object(SpeedTestStore, "update", side_effect=StorageError("locked")), \
                self.assertLogs(routes.LOGGER, "ERROR"):
            status, body = self.write("/api/speed-tests/update",
                                      {"result_id": result["id"], "label": "x"})
        self.assertEqual(status, 500)
        self.assertIn("could not be saved", body["error"])

    def test_a_database_fault_on_a_read_is_a_clean_error_and_one_bad_row_is_skipped(self) -> None:
        for _ in range(2):
            _, run = self.write("/api/speed-tests/start", {"mac_address": PHONE})
            self.run_seconds(run["id"], 5)
            self.write("/api/speed-tests/finish", {"run_id": run["id"]})
        first, second = (item["id"] for item in reversed(SpeedTestStore(self.repository).list()))
        self.repository._connection.execute(
            "UPDATE speed_test_results SET reported_json = 'oops' WHERE id = ?", (second,))
        with self.assertLogs(store_module.LOGGER, "WARNING"):
            status, _, body = self.read("/api/speed-tests", "mac=" + PHONE)
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in json.loads(body)["results"]], [first])
        with self.assertLogs(routes.LOGGER, "ERROR"):
            status, _, body = self.read("/api/speed-tests/compare", f"baseline={first}&result={second}")
        self.assertEqual(status, 500)
        self.assertIn("could not be read", json.loads(body)["error"])
        broken = mock.MagicMock()
        broken.execute.side_effect = sqlite3.OperationalError("database is locked")
        for path, query in (("/api/speed-tests", "mac=" + PHONE),
                            ("/api/speed-tests/export", "format=csv"),
                            ("/api/speed-tests/compare", f"baseline={first}&result={first}")):
            with mock.patch.object(self.repository, "_connection", broken), \
                    self.assertLogs(routes.LOGGER, "ERROR"):
                status, _, body = self.read(path, query)
            self.assertEqual(status, 500, path)
            self.assertIn("could not be read", json.loads(body)["error"], path)

    def test_the_active_list_builds_each_run_while_holding_the_manager_lock(self) -> None:
        self.start_short()
        owned = []
        payload = routes.SpeedTestRun.payload

        def watching(run, now):
            # The sampler trims a run's samples under this lock, so the page's copy must be made
            # under it too.
            owned.append(self.manager.lock._is_owned())
            return payload(run, now)

        with mock.patch.object(routes.SpeedTestRun, "payload", watching):
            self.assertEqual(self.read("/api/speed-tests", "mac=" + PHONE)[0], 200)
        self.assertEqual(owned, [True])

    def test_a_label_with_a_hidden_control_or_format_character_is_refused(self) -> None:
        for label in ("a\u202eb", "a\x9bb", "a\u200bb"):
            status, body = self.write("/api/speed-tests/start",
                                      {"mac_address": PHONE, "label": label})
            self.assertEqual(status, 400, repr(label))
            self.assertIn("control character", body["error"])
        self.assertEqual(self.manager.active(), [])


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
                        connection = http.client.HTTPConnection(
                            *self.server.server_address, timeout=5)
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

    def test_the_default_manager_is_made_once_by_the_server(self) -> None:
        manager = routes.speed_test_manager(self.server)
        self.assertIsInstance(manager, SpeedTestManager)
        self.assertIs(manager, self.server._speed_test_manager)
        self.assertIs(manager, routes.speed_test_manager(self.server))


if __name__ == "__main__":
    unittest.main()
