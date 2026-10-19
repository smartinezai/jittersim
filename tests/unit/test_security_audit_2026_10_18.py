"""Regression tests for the security audit of 2026-10-18 (docs/security-audit-2026-10-18.md)."""

import http.client
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from jittersim.api import server as server_module
from jittersim.api.server import ControlRequestHandler, ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking import browser_proxy


class CountingHelper:
    socket_path = "counting"

    def __init__(self, delay=0.0):
        self.pings = 0
        self.delay = delay
        self.lock = threading.Lock()

    def ping(self):
        with self.lock:
            self.pings += 1
        time.sleep(self.delay)
        return {"ok": True}


class PublicHealthReuseTests(unittest.TestCase):
    """A2: the health check without the token must not reach the root helper per request."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = ConfigRepository(Path(directory.name) / "settings.db")
        self.addCleanup(self.repository.close)
        self.helper = CountingHelper()
        self.server = ControlServer(("127.0.0.1", 0), self.repository, admin_token="admin-token",
                                    helper_client=self.helper)
        self.addCleanup(self.server.server_close)

    def get(self, authorization=None):
        handler = object.__new__(ControlRequestHandler)
        handler.server = self.server
        handler.path = "/api/health"
        handler.client_address = ("192.168.50.20", 45000)
        handler.headers = Message()
        if authorization:
            handler.headers["Authorization"] = authorization
        response = {}
        handler._send_response = lambda **values: response.update(values)
        handler.do_GET()
        return response

    def test_many_requests_without_the_token_ask_the_helper_once_a_second(self) -> None:
        for _ in range(50):
            response = self.get()
            self.assertEqual(response["status"], 200)
            self.assertIn(b'"helper_reachable": true', response["body"])
        self.assertEqual(self.helper.pings, 1)

    def test_concurrent_requests_without_the_token_share_one_ping(self) -> None:
        self.helper.delay = 0.2
        threads = [threading.Thread(target=self.get) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(self.helper.pings, 1)

    def test_the_answer_is_asked_again_after_the_reuse_time(self) -> None:
        self.get()
        with patch.object(server_module, "PUBLIC_HEALTH_REUSE_SECONDS", 0.0):
            self.get()
        self.assertEqual(self.helper.pings, 2)

    def test_a_request_with_the_token_always_asks_the_helper(self) -> None:
        for _ in range(3):
            self.get("Bearer admin-token")
        self.assertEqual(self.helper.pings, 3)

    def test_a_wrong_token_is_treated_as_no_token(self) -> None:
        for _ in range(3):
            self.get("Bearer wrong")
        self.assertEqual(self.helper.pings, 1)


class ProxyRequestDeadlineTests(unittest.TestCase):
    """A3: a CONNECT request that trickles in must not hold a tunnel slot for good."""

    def test_a_trickled_request_is_refused_at_the_deadline(self) -> None:
        server_side, client_side = socket.socketpair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        stop = threading.Event()

        def trickle():
            # One byte every 0.1 s: each read is quick, the request never ends. After
            # 3 s the sender gives up, so a regression fails the test instead of hanging.
            ends = time.monotonic() + 3.0
            while not stop.wait(0.1) and time.monotonic() < ends:
                try:
                    client_side.send(b"C")
                except OSError:
                    return
            client_side.close()

        sender = threading.Thread(target=trickle, daemon=True)
        sender.start()
        self.addCleanup(sender.join, 2)
        self.addCleanup(stop.set)
        started = time.monotonic()
        with patch.object(browser_proxy, "CONNECT_TIMEOUT_SECONDS", 0.5):
            with self.assertRaises((browser_proxy.ProxyError, socket.timeout, OSError)):
                browser_proxy._read_connect_request(server_side)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_whole_request_is_still_read(self) -> None:
        server_side, client_side = socket.socketpair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        client_side.sendall(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\nhello")
        host, first = browser_proxy._read_connect_request(server_side)
        self.assertEqual((host, first), ("example.com", b"hello"))


class CrossOriginHeaderTests(unittest.TestCase):
    """A4: every dashboard answer stays with the dashboard's own site."""

    def test_every_answer_carries_the_cross_origin_headers(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repository = ConfigRepository(Path(directory.name) / "settings.db")
        self.addCleanup(repository.close)
        server = ControlServer(("127.0.0.1", 0), repository, admin_token="admin-token")
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        for path, token in (("/", None), ("/api/health", None), ("/api/state", None),
                            ("/api/state", "admin-token")):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            headers = {"Authorization": "Bearer " + token} if token else {}
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            response.read()
            connection.close()
            for name in ("Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy"):
                self.assertEqual(response.getheader(name), "same-origin", path)


class _FakeRequest:
    """An accepted connection as far as the server looks at it."""

    def __init__(self) -> None:
        self.closed = False

    def shutdown(self, how) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class ProxyClientShareTests(unittest.TestCase):
    """A5: one device must not hold every connection slot of the browser proxy."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.server = browser_proxy.BrowserProxyServer(
            ("127.0.0.1", 0), active_plan_path=Path(directory.name) / "plan.json",
            client_network="127.0.0.0/8", mark_socket=lambda *_: None)
        self.addCleanup(self.server.server_close)
        # No worker thread starts: each test decides when a connection ends.
        self.started = []
        patcher = patch.object(
            socketserver.ThreadingMixIn, "process_request",
            lambda server, request, client_address: self.started.append(client_address))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server.finish_request = lambda request, client_address: None
        self.server.handle_error = lambda request, client_address: None

    def accept(self, address, count=1):
        requests = []
        for _ in range(count):
            request = _FakeRequest()
            self.server.process_request(request, (address, 50000))
            requests.append(request)
        return requests

    def finish(self, address, count=1):
        for _ in range(count):
            self.server.process_request_thread(_FakeRequest(), (address, 50000))

    def test_one_device_gets_16_slots_and_the_17th_is_closed(self) -> None:
        accepted = self.accept("127.0.0.2", 16)
        self.assertEqual(len(self.started), 16)
        self.assertFalse(any(request.closed for request in accepted))
        (refused,) = self.accept("127.0.0.2")
        self.assertTrue(refused.closed)
        self.assertEqual(len(self.started), 16)
        self.assertEqual(self.server.client_slots.held("127.0.0.2"), 16)

    def test_two_devices_are_independent_and_the_limit_for_all_stays_32(self) -> None:
        self.accept("127.0.0.2", 16)
        second = self.accept("127.0.0.3", 16)
        self.assertFalse(any(request.closed for request in second))
        self.assertEqual(len(self.started), 32)
        # The proxy is full for everyone now, also for a device that holds nothing.
        (third,) = self.accept("127.0.0.4")
        self.assertTrue(third.closed)
        self.assertEqual(self.server.client_slots.held("127.0.0.4"), 0)
        # The first device ends its connections. The second one still has its own limit,
        # and a device that held nothing gets slots again.
        self.finish("127.0.0.2", 16)
        (again,) = self.accept("127.0.0.3")
        self.assertTrue(again.closed)
        self.assertEqual(self.server.client_slots.held("127.0.0.3"), 16)
        (newcomer,) = self.accept("127.0.0.4")
        self.assertFalse(newcomer.closed)

    def test_a_refused_connection_gives_back_the_slot_for_all(self) -> None:
        self.accept("127.0.0.2", 16)
        refused = self.accept("127.0.0.2", 5)
        self.assertTrue(all(request.closed for request in refused))
        # If a refusal kept a slot for all, the second device would not get 16.
        second = self.accept("127.0.0.3", 16)
        self.assertFalse(any(request.closed for request in second))
        self.assertEqual(len(self.started), 32)

    def test_a_slot_is_free_again_when_a_connection_ends(self) -> None:
        self.accept("127.0.0.2", 16)
        self.finish("127.0.0.2")
        self.assertEqual(self.server.client_slots.held("127.0.0.2"), 15)
        (taken,) = self.accept("127.0.0.2")
        (refused,) = self.accept("127.0.0.2")
        self.assertFalse(taken.closed)
        self.assertTrue(refused.closed)

    def test_a_slot_is_free_again_when_the_worker_fails(self) -> None:
        def fail(request, client_address):
            raise RuntimeError("the request failed")

        self.accept("127.0.0.2", 16)
        self.server.finish_request = fail
        self.finish("127.0.0.2", 16)
        self.assertEqual(self.server.client_slots.held("127.0.0.2"), 0)
        self.server.finish_request = lambda request, client_address: None
        # Nothing leaked for the device or for all devices.
        self.assertFalse(any(request.closed for request in self.accept("127.0.0.2", 16)))
        self.assertFalse(any(request.closed for request in self.accept("127.0.0.3", 16)))

    def test_a_slot_is_free_again_when_the_worker_cannot_start(self) -> None:
        with patch.object(socketserver.ThreadingMixIn, "process_request",
                          side_effect=RuntimeError("cannot start a new thread")):
            for _ in range(40):
                with self.assertRaises(RuntimeError):
                    self.accept("127.0.0.2")
        self.assertEqual(self.server.client_slots.held("127.0.0.2"), 0)
        self.assertFalse(any(request.closed for request in self.accept("127.0.0.2", 16)))
        self.assertFalse(any(request.closed for request in self.accept("127.0.0.3", 16)))

    def test_the_refusal_is_logged_without_the_address_and_not_every_time(self) -> None:
        self.accept("127.0.0.2", 16)
        with patch.object(browser_proxy, "LOGGER") as logger:
            self.accept("127.0.0.2", 3)
        self.assertEqual(logger.warning.call_count, 1)
        arguments = logger.warning.call_args.args
        message = arguments[0] % arguments[1:]
        self.assertIn("browser tunnel rejected", message)
        self.assertIn("16 of 32", message)
        for word in ("127.0.0.2", "mac", "token", "bearer"):
            self.assertNotIn(word, message.lower())
        with patch.object(browser_proxy, "LOGGER") as logger, \
                patch.object(browser_proxy, "CLIENT_LIMIT_LOG_SECONDS", 0.0):
            self.accept("127.0.0.2", 3)
        self.assertEqual(logger.warning.call_count, 3)


class ProxyClientShareSocketTests(unittest.TestCase):
    """A5 with real connections: a close and a failed request each free a slot."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.server = browser_proxy.BrowserProxyServer(
            ("127.0.0.1", 0), active_plan_path=Path(directory.name) / "plan.json",
            client_network="127.0.0.0/8", mark_socket=lambda *_: None)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.clients = []
        # Cleanups run last in, first out: close the clients, stop the server, join.
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.close_clients)
        patcher = patch.object(browser_proxy, "CONNECT_TIMEOUT_SECONDS", 30)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_client(self) -> socket.socket:
        client = socket.create_connection(self.server.server_address, timeout=3)
        self.clients.append(client)
        return client

    def close_clients(self) -> None:
        for client in self.clients:
            client.close()

    def wait_for_held(self, count) -> None:
        ends = time.monotonic() + 5
        while self.server.client_slots.held("127.0.0.1") != count:
            if time.monotonic() > ends:
                self.fail(f"the device holds {self.server.client_slots.held('127.0.0.1')} "
                          f"slots, not {count}")
            time.sleep(0.01)

    def test_slots_come_back_after_a_close_and_after_a_failed_request(self) -> None:
        held = [self.open_client() for _ in range(16)]
        self.wait_for_held(16)
        # The 17th connection is closed by the proxy without an answer.
        extra = self.open_client()
        try:
            self.assertEqual(extra.recv(4096), b"")
        except ConnectionResetError:
            pass
        self.assertEqual(self.server.client_slots.held("127.0.0.1"), 16)
        # Closing one connection frees its slot.
        held.pop().close()
        self.wait_for_held(15)
        # A request that is not a CONNECT is answered, and its slot is free again.
        failing = self.open_client()
        failing.sendall(b"GET / HTTP/1.1\r\n\r\n")
        self.assertIn(b"503 Service Unavailable", failing.recv(4096))
        self.wait_for_held(15)
