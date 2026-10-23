"""Dashboard-served speed test: start checks, the byte stream, its limits and the result.

See docs/implementation-plan-dashboard-speed-test-2026-10-23.md.
"""

import http.client
import json
import socket
import threading
import time
import unittest
from unittest import mock

from jittersim.api import speed_tests as routes
from jittersim.api.speed_tests import SPEED_TEST_DOWNLOAD
from jittersim.networking.helper import HelperClientError
from jittersim.speed_test import compare_results, export_csv
from tests.unit.test_device_speed_test import OTHER, PHONE, ManagerBase

PHONE_ADDRESS = "192.168.50.10"
START = "/api/speed-tests/start"


class FakeConnection:
    def __init__(self):
        self.tos = []
        self.priority = []
        self.calls = []
        self.timeouts = []

    def setsockopt(self, level, name, value):
        self.calls.append((name, value))
        (self.tos if name == socket.IP_TOS else self.priority).append(value)

    def settimeout(self, seconds):
        self.timeouts.append(seconds)


class FakeWriter:
    """Takes every block, or fails with `error` after `blocks` blocks."""

    def __init__(self, blocks=None, error=BrokenPipeError):
        self.blocks = blocks
        self.error = error
        self.written = 0
        self.calls = 0
        self.on_write = None

    def write(self, data):
        if self.blocks is not None and self.calls >= self.blocks:
            raise self.error()
        self.calls += 1
        self.written += len(data)
        # A real Wi-Fi is far slower than memory: keep the byte cap out of the time tests.
        time.sleep(0.001)
        if self.on_write is not None:
            self.on_write()


class FakeHandler:
    def __init__(self, server, path, peer=PHONE_ADDRESS, writer=None):
        self.server = server
        self.path = path
        self.client_address = (peer, 50000)
        self.connection = FakeConnection()
        self.wfile = writer or FakeWriter()
        self.status = None
        self.headers_sent = {}
        self.refused = None
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers_sent[name] = value

    def send_security_headers(self):
        self.headers_sent["security"] = True

    def end_headers(self):
        pass

    def _send_response(self, *, status, content_type, body, headers=None):
        self.refused = (status, json.loads(body)["error"])


class DashboardTestBase(ManagerBase):
    def setUp(self) -> None:
        super().setUp()
        # Short parts so the tests stay fast. The limits are the same code.
        for patcher in (mock.patch.dict(routes.DASHBOARD_PARTS, {"reference": 0.2, "shaped": 0.2}),
                        mock.patch.object(routes, "DASHBOARD_PART_GRACE_SECONDS", 0.1),
                        mock.patch.object(routes, "STREAM_WAIT_SECONDS", 0.2),
                        # Linux's value, so the reset is checked on any system.
                        mock.patch.object(routes, "SO_PRIORITY", 12)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start_dashboard(self, mac=PHONE, peer=PHONE_ADDRESS):
        status, _, body = routes.write_speed_test(
            self.server, START, {"mac_address": mac, "provider": "dashboard"}, peer=peer)
        return status, json.loads(body)

    def stream(self, run_id, part, **handler_options):
        handler = FakeHandler(self.server, f"{SPEED_TEST_DOWNLOAD}?run={run_id}&part={part}",
                              **handler_options)
        routes.stream_download(handler)
        return handler

    def finish(self, run_id, browser):
        status, _, body = routes.write_speed_test(
            self.server, "/api/speed-tests/finish", {"run_id": run_id, "browser": browser})
        return status, json.loads(body)


class StartTests(DashboardTestBase):
    def test_the_device_itself_starts_a_twenty_second_window_on_its_own_queue(self) -> None:
        status, run = self.start_dashboard()
        self.assertEqual(status, 201, run)
        self.assertEqual((run["provider"], run["source"], run["duration_seconds"]),
                         ("dashboard", "device", 20))

    def test_another_browser_is_refused_and_nothing_runs(self) -> None:
        for peer in ("192.168.50.77", "127.0.0.1", None):
            status, body = self.start_dashboard(peer=peer)
            self.assertEqual(status, 403, peer)
            self.assertIn("Open the dashboard on the device", body["error"])
        self.assertEqual(self.manager.active(), [])

    def test_a_device_without_its_own_queue_is_refused(self) -> None:
        # OTHER has no profile; with the shared queue it would not be shaped either.
        status, body = self.start_dashboard(OTHER, "192.168.50.77")
        self.assertEqual(status, 409)
        self.assertIn("profile of this device's own", body["error"])
        self.helper.queue_id = "general-profile"
        status, body = self.start_dashboard(OTHER, "192.168.50.77")
        self.assertEqual(status, 409)
        self.assertEqual(self.manager.active(), [])

    def test_a_latched_panic_or_an_unknown_state_refuses_the_start(self) -> None:
        self.helper.gateway_state["panic_stop_latched"] = True
        status, body = self.start_dashboard()
        self.assertEqual(status, 409)
        self.assertIn("Panic", body["error"])
        del self.helper.gateway_state["panic_stop_latched"]
        with mock.patch.object(self.helper, "gateway_status",
                               side_effect=HelperClientError("busy")):
            self.assertEqual(self.start_dashboard()[0], 503)
        self.assertEqual(self.start_dashboard()[0], 201)

    def test_one_dashboard_test_at_a_time_and_a_gap_per_device(self) -> None:
        _, run = self.start_dashboard()
        status, body = self.start_dashboard(OTHER, "192.168.50.77")
        self.assertEqual(status, 409)
        self.assertIn("Another dashboard speed test", body["error"])
        # A speed-test-site run of another device may still run beside it.
        self.assertEqual(self.write(START, {"mac_address": OTHER})[0], 201)
        self.manager.cancel(run["id"])
        status, body = self.start_dashboard()
        self.assertEqual(status, 429)
        self.assertIn("Wait 15 seconds", body["error"])
        self.clock[0] += 15
        self.assertEqual(self.start_dashboard()[0], 201)


class StreamTests(DashboardTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = self.start_dashboard()[1]["id"]

    def test_the_main_part_alone_is_marked_and_the_mark_is_cleared_after(self) -> None:
        reference = self.stream(self.run_id, "reference")
        shaped = self.stream(self.run_id, "shaped")
        self.assertEqual(reference.connection.tos, [0, 0])
        self.assertEqual(shaped.connection.tos, [0x08, 0])
        # The TOS sets the socket priority too: it goes back to 0 so both parts share a Wi-Fi class.
        # The order matters: on Linux a TOS set after the priority would leave priority 2.
        self.assertEqual(shaped.connection.priority, [0])
        self.assertEqual(shaped.connection.calls,
                         [(socket.IP_TOS, 0x08), (12, 0), (socket.IP_TOS, 0)])
        for handler in (reference, shaped):
            self.assertEqual(handler.status, 200)
            self.assertTrue(handler.close_connection)
            self.assertEqual(handler.headers_sent["Cache-Control"], "no-store")
            self.assertEqual(handler.headers_sent["Connection"], "close")
            self.assertEqual(handler.headers_sent["Content-Type"], "application/octet-stream")
            self.assertTrue(handler.headers_sent["security"])
            self.assertGreater(handler.wfile.written, 0)
        self.assertFalse(self.manager.runs[self.run_id].streaming)

    def test_the_priority_option_falls_back_to_the_linux_value_only_on_linux(self) -> None:
        # A Python that does not name the option: Linux still resets the priority, others do not.
        for platform, expected in (("linux", 12), ("darwin", None)):
            with mock.patch.object(routes, "socket", mock.Mock(spec=[])), \
                    mock.patch.object(routes, "sys", mock.Mock(platform=platform)):
                self.assertEqual(routes.socket_priority_option(), expected)

    def test_the_time_cap_ends_a_part_and_bounds_the_write_timeout(self) -> None:
        began = time.monotonic()
        handler = self.stream(self.run_id, "shaped")
        self.assertLess(time.monotonic() - began, 2)
        self.assertTrue(all(0 < seconds <= 0.3 + 1 for seconds in handler.connection.timeouts))

    def test_the_byte_cap_holds_for_each_part(self) -> None:
        with mock.patch.object(routes, "DASHBOARD_MAX_BYTES", 40_000):
            first = self.stream(self.run_id, "reference")
            second = self.stream(self.run_id, "shaped")
        self.assertEqual((first.wfile.written, second.wfile.written), (40_000, 40_000))

    def test_each_part_once_one_at_a_time_and_only_for_the_device(self) -> None:
        self.assertEqual(self.stream(self.run_id, "shaped", peer="192.168.50.77").refused[0], 403)
        self.assertEqual(self.stream(self.run_id, "upload").refused[0], 400)
        self.assertEqual(self.stream("nope", "shaped").refused[0], 404)
        overlap = {}
        writer = FakeWriter()
        writer.on_write = lambda: overlap.setdefault(
            "status", self.stream(self.run_id, "reference").refused)
        self.stream(self.run_id, "shaped", writer=writer)
        self.assertEqual(overlap["status"][0], 409)
        self.assertEqual(self.stream(self.run_id, "shaped").refused[0], 409)
        self.assertIn("already ran", self.stream(self.run_id, "shaped").refused[1])

    def test_the_main_part_waits_briefly_for_the_reference_part_to_end(self) -> None:
        run = self.manager.runs[self.run_id]
        run.parts.add("reference")
        run.streaming = True
        threading.Timer(0.05, self.manager.close_stream, args=(run,)).start()
        self.assertEqual(self.stream(self.run_id, "shaped").status, 200)
        # A part that never ends is refused after the short wait.
        run.parts.discard("shaped")
        run.streaming = True
        refused = self.stream(self.run_id, "shaped").refused
        self.assertEqual(refused, (409, "The other part of this test is still running"))

    def test_a_site_run_has_no_stream(self) -> None:
        self.manager.cancel(self.run_id)
        _, run = self.write(START, {"mac_address": OTHER})
        refused = self.stream(run["id"], "shaped", peer="192.168.50.77").refused
        self.assertEqual(refused[0], 409)

    def test_an_aborted_or_stalled_connection_frees_the_part(self) -> None:
        for error in (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.manager.runs[self.run_id].parts.clear()
            handler = self.stream(self.run_id, "shaped", writer=FakeWriter(2, error))
            self.assertEqual(handler.wfile.written, 2 * len(routes.FILLER))
            self.assertFalse(self.manager.runs[self.run_id].streaming)
            self.assertEqual(handler.connection.tos[-1], 0)

    def test_a_reset_or_panic_request_or_a_run_that_ends_stops_the_stream(self) -> None:
        writer = FakeWriter()
        writer.on_write = lambda: setattr(self.server, "_resets_pending", 1)
        self.stream(self.run_id, "shaped", writer=writer)
        self.assertEqual(writer.calls, 1)
        self.assertEqual(self.stream(self.run_id, "reference").refused[0], 409)
        self.server._resets_pending = 0
        writer = FakeWriter()
        writer.on_write = lambda: self.manager.cancel(self.run_id)
        self.stream(self.run_id, "reference", writer=writer)
        self.assertEqual(writer.calls, 1)

    def test_a_socket_that_cannot_be_marked_sends_nothing(self) -> None:
        handler = FakeHandler(self.server, f"{SPEED_TEST_DOWNLOAD}?run={self.run_id}&part=shaped")
        handler.connection.setsockopt = mock.Mock(side_effect=OSError("not allowed"))
        routes.stream_download(handler)
        self.assertEqual(handler.refused[0], 503)
        self.assertIsNone(handler.status)
        self.assertFalse(self.manager.runs[self.run_id].streaming)


class ResultTests(DashboardTestBase):
    def test_the_result_keeps_the_browser_speeds_and_does_not_test_upload(self) -> None:
        _, run = self.start_dashboard()
        self.run_seconds(run["id"], 8)
        status, ended = self.finish(run["id"], {"reference_kbit": 30000, "download_kbit": 249.6})
        self.assertEqual(status, 200, ended)
        summary = ended["result"]["summary"]
        self.assertEqual(summary["browser"], {"download_kbit": 249.6, "reference_kbit": 30000.0})
        self.assertEqual(summary["download"]["verdict"], "at-cap")
        self.assertEqual(summary["upload"]["verdict"], "not-tested")
        self.assertEqual((ended["result"]["provider"], ended["result"]["status"]),
                         ("dashboard", "complete"))
        self.assertIn("249.6", export_csv([ended["result"]]))

    def test_a_cap_near_the_pis_own_speed_is_not_judged(self) -> None:
        _, run = self.start_dashboard()
        self.run_seconds(run["id"], 8)
        _, ended = self.finish(run["id"], {"reference_kbit": 260, "download_kbit": 240})
        self.assertEqual(ended["result"]["summary"]["download"]["verdict"], "pi-limit")

    def test_a_rate_above_the_cap_is_still_reported_near_the_pis_speed(self) -> None:
        read = self.helper.queue_stats

        def twice_the_download():
            return [{**entry, "bytes": entry["bytes"] * 2} if entry["direction"] == "download"
                    else entry for entry in read()]

        self.helper.queue_stats = twice_the_download
        _, run = self.start_dashboard()
        self.run_seconds(run["id"], 8)
        _, ended = self.finish(run["id"], {"reference_kbit": 260, "download_kbit": 500})
        self.assertEqual(ended["result"]["summary"]["download"]["verdict"], "above-cap")

    def test_browser_speeds_are_checked(self) -> None:
        _, run = self.start_dashboard()
        for browser in ({"download_kbit": -1}, {"download_kbit": "9"}, {"download_kbit": True},
                        {"download_kbit": 1e12}, {"other": 1}, [1]):
            status, body = self.finish(run["id"], browser)
            self.assertEqual(status, 400, browser)
        self.assertEqual(self.finish(run["id"], {"download_kbit": None})[0], 200)
        _, site = self.write(START, {"mac_address": OTHER})
        self.assertEqual(self.finish(site["id"], {"download_kbit": 5})[0], 400)

    def test_compare_shows_the_browser_speeds(self) -> None:
        baseline = {"id": 1, "summary": {"browser": {"download_kbit": 9000.0}}}
        later = {"id": 2, "summary": {"browser": {"download_kbit": 250.0}}}
        changes = {item["field"]: item for item in compare_results(baseline, later)["changes"]}
        self.assertEqual(changes["browser_download_kbit"]["change"], -8750.0)

    def test_the_list_tells_the_page_the_browser_address(self) -> None:
        status, _, body = routes.read_speed_test(self.server, "/api/speed-tests", "",
                                                 peer=PHONE_ADDRESS)
        listing = json.loads(body)
        self.assertEqual(listing["browser_address"], PHONE_ADDRESS)
        self.assertIn("shaped", listing["limits"]["dashboard_parts"])


class HttpTests(DashboardTestBase):
    """The real request handler: token, headers and a stream that ends by itself."""

    def setUp(self) -> None:
        super().setUp()
        # A real socket: only this system's own socket priority option is valid.
        patcher = mock.patch.object(routes, "SO_PRIORITY", getattr(socket, "SO_PRIORITY", None))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server.admin_token = "admin-token"
        # The test client connects from 127.0.0.1, so that is the device's address here. Without
        # a scheduler the saved device decides which queue is measured.
        self.helper.addresses[PHONE] = "127.0.0.1"
        self.server.scheduler = None
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, token="admin-token"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        connection.request(method, path, body=None if body is None else json.dumps(body),
                           headers=headers)
        answer = connection.getresponse()
        data = answer.read()
        connection.close()
        return answer, data

    def test_the_stream_needs_the_token_and_carries_the_usual_headers(self) -> None:
        for token in (None, "wrong"):
            answer, _ = self.request("GET", SPEED_TEST_DOWNLOAD + "?run=x&part=shaped", token=token)
            self.assertEqual(answer.status, 401)
        answer, data = self.request("POST", START, {"mac_address": PHONE, "provider": "dashboard"})
        self.assertEqual(answer.status, 201, data)
        run = json.loads(data)
        answer, data = self.request("GET", f"{SPEED_TEST_DOWNLOAD}?run={run['id']}&part=shaped")
        self.assertEqual(answer.status, 200)
        self.assertGreater(len(data), 0)
        for name, value in (("Cache-Control", "no-store"), ("Connection", "close"),
                            ("X-Content-Type-Options", "nosniff"),
                            ("Cross-Origin-Resource-Policy", "same-origin"),
                            ("Cross-Origin-Opener-Policy", "same-origin")):
            self.assertEqual(answer.getheader(name), value)
        self.assertIsNone(answer.getheader("Content-Length"))
        answer, data = self.request("GET", "/api/speed-tests?mac=" + PHONE)
        self.assertEqual(json.loads(data)["browser_address"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
