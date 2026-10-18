"""Regression tests for the security audit of 2026-10-18 (docs/security-audit-2026-10-18.md)."""

import socket
import tempfile
import threading
import time
import unittest
from email.message import Message
from ipaddress import IPv4Address
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
        client_side.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\nhello")
        host, first = browser_proxy._read_connect_request(server_side)
        self.assertEqual((host, first), ("example.com", b"hello"))
