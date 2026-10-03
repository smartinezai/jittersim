"""Real HTTPS regressions for interrupted dashboard clients."""

import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.api import server as api
from jittersim.config.storage import ConfigRepository


class DashboardConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        openssl = shutil.which("openssl")
        if openssl is None:
            raise unittest.SkipTest("OpenSSL is required for real HTTPS checks")
        cls.directory = tempfile.TemporaryDirectory(prefix="jittersim-connections-")
        cls.certificate = Path(cls.directory.name) / "dashboard.crt"
        cls.key = Path(cls.directory.name) / "dashboard.key"
        subprocess.run(
            [
                openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                "-nodes", "-days", "1", "-keyout", str(cls.key),
                "-out", str(cls.certificate), "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1",
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.server = api.ControlServer(
            ("127.0.0.1", 0), self.repository, admin_token="test-admin",
            tls_certfile=self.certificate, tls_keyfile=self.key,
        )
        self.server.timeout = 2
        self.context = ssl.create_default_context(cafile=str(self.certificate))
        self.timeouts = patch.multiple(
            api, TLS_HANDSHAKE_TIMEOUT_SECONDS=0.2,
            REQUEST_INPUT_TIMEOUT_SECONDS=0.3, RESPONSE_SEND_TIMEOUT_SECONDS=0.3,
        )
        self.timeouts.start()
        self.threads = []
        self.client_errors = []
        self.helper_calls = []

        class RecordingHelper:
            def ping(helper):
                return {"ok": True}

            def set_router_wifi(helper, *, admin_password, enabled):
                self.helper_calls.append(enabled)
                return {"ok": True, "enabled": enabled}

        self.server.helper_client = RecordingHelper()

    def tearDown(self) -> None:
        for thread in self.threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "client did not stop within its limit")
        self.server.server_close()
        self.repository.close()
        self.timeouts.stop()
        self.assertEqual(self.client_errors, [])

    def _start_client(self, function) -> None:
        def run() -> None:
            try:
                function()
            except Exception as error:
                self.client_errors.append(error)

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()

    def _wait_for_idle_server(self) -> None:
        # Requests run on worker threads, so wait for the one just started to finish.
        deadline = time.monotonic() + 3
        while self.server.active_workers and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_workers, 0)

    def _connect(self) -> ssl.SSLSocket:
        raw = socket.create_connection(self.server.server_address, timeout=2)
        return self.context.wrap_socket(raw, server_hostname="127.0.0.1")

    def _handle_next_request(self) -> None:
        # The live serving loop also advances sockets already accepted.
        if self.server._pending_connections:
            self.server.service_actions()
        else:
            self.server.handle_request()

    @staticmethod
    def _response(client: ssl.SSLSocket) -> bytes:
        response = bytearray()
        while True:
            part = client.recv(4096)
            if not part:
                return bytes(response)
            response.extend(part)
            if len(response) > 128 * 1024:
                raise AssertionError("unexpectedly large test response")

    def _assert_next_client_recovers(self, phase: str, *, drip: bool = False) -> None:
        ready = threading.Event()
        response = []

        def stall() -> None:
            raw = socket.create_connection(self.server.server_address, timeout=2)
            client = raw
            try:
                if phase != "handshake":
                    client = self.context.wrap_socket(raw, server_hostname="127.0.0.1")
                    if phase == "headers":
                        data = b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Slow: "
                    else:
                        data = (
                            b"POST /api/router/wifi HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                            b"Authorization: Bearer test-admin\r\n"
                            b"Content-Length: 65000\r\n\r\n{"
                        )
                    client.sendall(data)
                ready.set()
                if drip:
                    for _ in range(40):
                        time.sleep(0.03)
                        try:
                            client.sendall(b"x")
                        except (OSError, ssl.SSLError):
                            break
                else:
                    time.sleep(1.2)
            finally:
                client.close()

        def healthy() -> None:
            self.assertTrue(ready.wait(timeout=2))
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                response.append(self._response(client))

        self._start_client(stall)
        self._start_client(healthy)
        started = time.monotonic()
        self._handle_next_request()
        self.threads[1].join(timeout=0.1)
        if not response:
            self._handle_next_request()
        elapsed = time.monotonic() - started
        self.threads[1].join(timeout=2)
        self.assertLess(elapsed, 1.0, "unfinished client blocked the next request")
        self.assertEqual(len(response), 1)
        self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))
        self.assertEqual(len(self.repository.list_profiles()), 0)
        self.assertEqual(self.repository.list_recent_actions(), ())
        self.assertEqual(self.helper_calls, [])

    def test_incomplete_tls_handshake_releases_the_server(self) -> None:
        self._assert_next_client_recovers("handshake")

    def test_several_idle_handshakes_do_not_delay_a_healthy_connection(self) -> None:
        clients = [socket.create_connection(self.server.server_address, timeout=2)
                   for _ in range(3)]
        response = []

        def healthy() -> None:
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                response.append(self._response(client))

        self._start_client(healthy)
        try:
            with patch.object(api, "TLS_HANDSHAKE_TIMEOUT_SECONDS", 1.0):
                started = time.monotonic()
                self._handle_next_request()
                elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.5, "idle handshakes delayed the healthy connection")
            self.threads[-1].join(timeout=2)
            self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))
        finally:
            for client in clients:
                client.close()

    def test_unused_encrypted_connection_does_not_delay_a_healthy_connection(self) -> None:
        ready = threading.Event()
        release = threading.Event()
        response = []

        def idle() -> None:
            with self._connect():
                ready.set()
                self.assertTrue(release.wait(timeout=2))

        def healthy() -> None:
            self.assertTrue(ready.wait(timeout=2))
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                response.append(self._response(client))

        self._start_client(idle)
        self._start_client(healthy)
        try:
            with patch.object(api, "REQUEST_INPUT_TIMEOUT_SECONDS", 1.0):
                started = time.monotonic()
                self._handle_next_request()
                elapsed = time.monotonic() - started
            self.threads[-1].join(timeout=2)
            self.assertLess(elapsed, 0.5)
            self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))
        finally:
            release.set()

    def test_connection_capacity_rejects_excess_and_recovers(self) -> None:
        clients = [socket.create_connection(self.server.server_address, timeout=2)
                   for _ in range(3)]
        rejected = []

        def excess() -> None:
            started = time.monotonic()
            try:
                with self._connect():
                    self.fail("excess connection was accepted")
            except OSError:
                rejected.append(time.monotonic() - started)

        self._start_client(excess)
        try:
            with patch.object(api, "MAX_PENDING_DASHBOARD_CONNECTIONS", 3):
                self._handle_next_request()
            self.threads[-1].join(timeout=2)
            self.assertLess(rejected[0], 0.5)
            self.assertEqual(len(self.server._pending_connections), 0)
        finally:
            for client in clients:
                client.close()
        response = []

        def healthy() -> None:
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                response.append(self._response(client))

        self._start_client(healthy)
        self._handle_next_request()
        self.threads[-1].join(timeout=2)
        self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))

    def test_shutdown_releases_idle_connections_promptly(self) -> None:
        worker = threading.Thread(target=self.server.serve_forever)
        worker.start()
        client = socket.create_connection(self.server.server_address, timeout=2)
        try:
            started = time.monotonic()
            self.server.shutdown()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic() - started, 1)
            self.server.server_close()
            self.assertEqual(len(self.server._pending_connections), 0)
        finally:
            client.close()

    def test_incomplete_headers_release_the_server(self) -> None:
        self._assert_next_client_recovers("headers")

    def test_incomplete_authenticated_body_makes_no_change(self) -> None:
        self._assert_next_client_recovers("body")

    def test_header_bytes_cannot_extend_the_input_deadline(self) -> None:
        self._assert_next_client_recovers("headers", drip=True)

    def test_body_bytes_cannot_extend_the_input_deadline(self) -> None:
        self._assert_next_client_recovers("body", drip=True)

    def test_queued_handshake_survives_an_active_request_timeout(self) -> None:
        original = api.ControlRequestHandler._read_json_body

        def queue_healthy_connection(handler, *args, **kwargs):
            if not self.server._pending_connections:
                self.server.socket.settimeout(1)
                try:
                    self.server._accept_connection()
                finally:
                    self.server.socket.settimeout(None)
            return original(handler, *args, **kwargs)

        with patch.object(api.ControlRequestHandler, "_read_json_body", queue_healthy_connection):
            self._assert_next_client_recovers("body", drip=True)

    def test_a_running_request_does_not_extend_a_pending_connection_deadline(self) -> None:
        # Requests run on their own threads, so waiting connections no longer lose time to
        # active work and their deadlines stay exactly as set.
        connection, peer = socket.socketpair()
        try:
            pending = api._PendingConnection(
                address=("127.0.0.1", 1), deadline=100.1, handshake=True, expires_at=100.5,
            )
            self.server._pending_connections[connection] = pending
            with patch.object(api.HTTPServer, "finish_request"):
                self.server.finish_request(None, None)
            self.assertEqual(pending.deadline, 100.1)
            self.assertEqual(pending.expires_at, 100.5)
        finally:
            self.server._pending_connections.clear()
            connection.close()
            peer.close()

    def test_waiting_for_first_byte_uses_the_same_input_deadline(self) -> None:
        payload = json.dumps({"admin_password": "isolated-password", "enabled": True}).encode()

        def delayed_request() -> None:
            with self._connect() as client:
                time.sleep(0.2)
                client.sendall(
                    b"POST /api/router/wifi HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                    b"Authorization: Bearer test-admin\r\nContent-Length: "
                    + str(len(payload)).encode() + b"\r\n\r\n"
                )
                time.sleep(0.2)
                try:
                    client.sendall(payload)
                except OSError:
                    pass

        self._start_client(delayed_request)
        self._handle_next_request()
        self.threads[-1].join(timeout=2)
        self.assertEqual(self.helper_calls, [])
        self.assertEqual(self.repository.list_recent_actions(), ())

    def test_headers_and_body_share_one_input_deadline(self) -> None:
        ready = threading.Event()
        response = []
        payload = json.dumps({"admin_password": "isolated-password", "enabled": True}).encode()

        def delayed_body() -> None:
            with self._connect() as client:
                client.sendall(
                    b"POST /api/router/wifi HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                    b"Authorization: Bearer test-admin\r\nContent-Length: "
                    + str(len(payload)).encode() + b"\r\n"
                )
                ready.set()
                time.sleep(0.2)
                client.sendall(b"\r\n")
                time.sleep(0.2)
                try:
                    client.sendall(payload)
                except (OSError, ssl.SSLError):
                    pass

        def healthy() -> None:
            self.assertTrue(ready.wait(timeout=2))
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                response.append(self._response(client))

        self._start_client(delayed_body)
        self._start_client(healthy)
        self._handle_next_request()
        self._handle_next_request()
        self.threads[1].join(timeout=2)
        self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))
        self.assertEqual(self.helper_calls, [])
        self.assertEqual(self.repository.list_recent_actions(), ())

    def test_slow_response_reader_releases_the_server(self) -> None:
        original = self.server.response_for
        self.server.response_for = lambda path, query="": (
            (200, "application/octet-stream", b"x" * (8 * 1024 * 1024))
            if path == "/" else original(path, query)
        )
        response = []
        ready = threading.Event()

        def slow_reader() -> None:
            raw = socket.create_connection(self.server.server_address, timeout=2)
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            with self.context.wrap_socket(raw, server_hostname="127.0.0.1") as client:
                client.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                ready.set()
                time.sleep(1.2)

        def healthy() -> None:
            self.assertTrue(ready.wait(timeout=2))
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                response.append(self._response(client))

        self._start_client(slow_reader)
        self._start_client(healthy)
        started = time.monotonic()
        self._handle_next_request()
        self._handle_next_request()
        self.threads[1].join(timeout=2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))

    def _assert_completed_action_once(self, *, disconnect: bool) -> None:
        calls = []
        response = []

        class SlowHelper:
            def ping(self):
                return {"ok": True}

            def set_router_wifi(self, *, admin_password, enabled):
                calls.append(enabled)
                time.sleep(0.45)
                return {"ok": True, "enabled": enabled}

        self.server.helper_client = SlowHelper()
        payload = json.dumps({"admin_password": "isolated-password", "enabled": True}).encode()

        def client_action() -> None:
            with self._connect() as client:
                client.sendall(
                    b"POST /api/router/wifi HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                    b"Authorization: Bearer test-admin\r\nContent-Length: "
                    + str(len(payload)).encode() + b"\r\n\r\n" + payload
                )
                if not disconnect:
                    response.append(self._response(client))

        self._start_client(client_action)
        self._handle_next_request()
        self.threads[0].join(timeout=2)
        self._wait_for_idle_server()
        self.assertEqual(calls, [True])
        history = self.repository.list_recent_actions()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status_code"], 200)
        if not disconnect:
            self.assertTrue(response[0].startswith(b"HTTP/1.0 200 OK"))
        health = []

        def healthy() -> None:
            with self._connect() as client:
                client.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                health.append(self._response(client))

        self._start_client(healthy)
        self._handle_next_request()
        self.threads[-1].join(timeout=2)
        self.assertTrue(health[0].startswith(b"HTTP/1.0 200 OK"))
        self.assertEqual(calls, [True])

    def test_complete_operation_can_outlast_the_input_deadline(self) -> None:
        self._assert_completed_action_once(disconnect=False)

    def test_disconnect_does_not_repeat_a_completed_action(self) -> None:
        self._assert_completed_action_once(disconnect=True)

    def test_malformed_request_does_not_log_its_contents(self) -> None:
        response = []

        def malformed() -> None:
            with self._connect() as client:
                client.sendall(b"PRIVATE_REQUEST_MARKER\r\n\r\n")
                response.append(self._response(client))

        self._start_client(malformed)
        with self.assertLogs(api.LOGGER, level="INFO") as logs:
            self._handle_next_request()
            self._wait_for_idle_server()
        self.threads[0].join(timeout=2)
        self.assertNotIn("PRIVATE_REQUEST_MARKER", "\n".join(logs.output))
        self.assertIn(b"400", response[0])
