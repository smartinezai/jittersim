import http.client
import io
import json
import socket
import tempfile
import threading
import time
import unittest

from jittersim.api.wifi_operations import issue_request_id
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jittersim.api.server import ControlServer
from jittersim.cli import main
from jittersim.config.storage import ConfigRepository
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.helper import (
    MAX_HELPER_CONNECTIONS,
    HelperClient,
    HelperClientError,
    HelperService,
    UnixHelperServer,
)
from jittersim.networking.topology import Topology
from tests.wifi_support import WifiExecutor


class WifiRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.socket = Path(self.directory.name) / "wifi.sock"
        self.executor = WifiExecutor()
        self.service = HelperService(
            NetworkApplier(executor=self.executor, topology=Topology("eth0", "wlan0")),
        )
        self.helper = UnixHelperServer(self.socket, self.service)
        self.helper_thread = threading.Thread(target=self.helper.serve_forever,
                                              kwargs={"poll_interval": 0.01})
        self.helper_thread.start()
        self.addCleanup(self.stop_helper)
        self.database = Path(self.directory.name) / "settings.db"
        self.repository = ConfigRepository(self.database)
        self.addCleanup(self.repository.close)
        ready = threading.Event()
        startup_errors = []

        def serve_api():
            try:
                with ConfigRepository(self.database) as repository:
                    with ControlServer(
                        ("127.0.0.1", 0), repository, admin_token="wifi-test",
                        helper_client=HelperClient(socket_path=self.socket),
                    ) as self.api:
                        ready.set()
                        self.api.serve_forever(poll_interval=0.01)
            except BaseException as error:
                startup_errors.append(error)
                ready.set()

        self.api_thread = threading.Thread(target=serve_api)
        self.api_thread.start()
        self.assertTrue(ready.wait(timeout=3))
        self.assertFalse(startup_errors, startup_errors)
        self.api.wifi_operations.delay = 0
        self.addCleanup(self.stop_api)

    def stop_helper(self):
        self.helper.shutdown()
        self.helper.server_close()
        self.helper_thread.join(timeout=3)

    def stop_api(self):
        self.api.shutdown()
        self.api.server_close()
        self.api_thread.join(timeout=3)

    def call(self, method, path, headers=None, payload=None):
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=3)
        try:
            connection.request(method, path, body=json.dumps(payload or {}) if method == "POST" else None,
                               headers={"Content-Type": "application/json",
                                        "Accept": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def request(self, headers=None):
        """Ask for the reset and follow it to its stored outcome, as the page does."""
        status, body = self.call("POST", "/api/access-point/reset-name", headers,
                                 {"request_id": issue_request_id()})
        if status != 202:
            return status, body
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, stored = self.call("GET", "/api/wifi-operation?id=" + body["operation_id"],
                                       headers)
            operation = stored["operation"]
            if operation["state"] != "running":
                break
            time.sleep(0.02)
        if operation["state"] == "succeeded":
            return 200, {"result": {"ssid": operation["ssid"], "security": operation["security"]},
                         "message": operation["message"]}
        return operation["status"], {"error": operation["message"]}

    def console(self, *extra):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["wifi", "reset", "--helper-socket", str(self.socket), *extra])
        return code, output.getvalue(), errors.getvalue()

    def test_console_works_without_dashboard_or_database_and_preserves_password(self):
        self.api.shutdown()
        self.api_thread.join(timeout=3)
        state = self.repository.get_runtime_state()
        with patch("jittersim.config.storage.ConfigRepository",
                   side_effect=AssertionError("database used")):
            code, _, errors = self.console("--yes")
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.executor.settings["802-11-wireless.ssid"], "TestNet")
        self.assertEqual(self.executor.settings["802-11-wireless-security.psk"],
                         "fixture-wifi-password")
        self.assertEqual(self.repository.get_runtime_state(), state)

    def test_cancel_and_unavailable_helper_never_claim_success(self):
        with patch("builtins.input", return_value="no"):
            code, _, _ = self.console()
        self.assertEqual(code, 0)
        self.assertFalse(self.executor.commands)
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["wifi", "reset", "--yes", "--helper-socket",
                         str(Path(self.directory.name) / "absent.sock")])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("management", errors.getvalue())

    def test_api_requires_authentication_and_rejects_other_sites(self):
        self.assertEqual(self.request()[0], 401)
        # The stored outcome names the network, so it needs the token as well.
        self.assertEqual(self.call("GET", "/api/wifi-operation")[0], 401)
        self.assertFalse(self.executor.commands)
        headers = {"Authorization": "Bearer wifi-test"}
        self.assertEqual(self.request({**headers, "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request({**headers, "Origin": "https://another-site.invalid"})[0], 403)
        self.assertFalse(self.executor.commands)
        state = self.repository.get_runtime_state()
        status, body = self.request(headers)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["result"]["ssid"], "TestNet")
        self.assertNotIn("fixture-wifi-password", json.dumps(body))
        self.assertEqual(self.repository.get_runtime_state(), state)

    def test_api_reports_missing_helper_and_sanitizes_failed_recovery(self):
        headers = {"Authorization": "Bearer wifi-test"}
        self.api.helper_client = None
        self.assertEqual(self.request(headers)[0], 503)
        self.api.helper_client = HelperClient(socket_path=self.socket)
        self.executor.failures.update((("up", 1), ("up", 2)))
        with self.assertLogs("jittersim.networking.helper", level="WARNING") as logs:
            status, body = self.request(headers)
        self.assertEqual(status, 502)
        self.assertIn("recovery failed", body["error"])
        self.assertNotIn("fixture-wifi-password", json.dumps(body) + "".join(logs.output))

    def test_old_helper_default_is_not_confirmed_or_replayed(self):
        class OldHelper:
            calls = 0

            def reset_wifi_name(self):
                self.calls += 1
                return {"ssid": "JitterSim", "security": "WPA3"}

        helper = OldHelper()
        self.api.helper_client = helper
        status, body = self.request({"Authorization": "Bearer wifi-test"})
        self.assertEqual(status, 502)
        self.assertIn("error", body)
        self.assertEqual(helper.calls, 1)

    def test_competing_wifi_changes_are_refused_while_ping_still_answers(self):
        entered, release = threading.Event(), threading.Event()
        self.executor.on_up = lambda: (entered.set(), release.wait(timeout=3))
        client = HelperClient(socket_path=self.socket, timeout_seconds=1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.reset_wifi_name)
            try:
                self.assertTrue(entered.wait(timeout=2))
                self.assertTrue(client.ping()["ok"])
                for action in ("wifi-reset", "configure-ap", "router-sync-ap"):
                    with self.assertRaises(HelperClientError) as caught:
                        client._request({"action": action, "ssid": "Other", "password": "other-pass"})
                    self.assertEqual(caught.exception.reason, "wifi-busy")
                status, _ = self.request({"Authorization": "Bearer wifi-test"})
                self.assertEqual(status, 409)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=3)["ssid"], "TestNet")

    def test_idle_socket_clients_cannot_create_unbounded_workers(self):
        clients = []
        try:
            for _ in range(MAX_HELPER_CONNECTIONS):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(self.socket))
                clients.append(client)
            deadline = time.monotonic() + 2
            while self.helper._request_slots._value and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.helper._request_slots._value, 0)
            with self.assertRaises(HelperClientError) as caught:
                HelperClient(socket_path=self.socket, timeout_seconds=1).ping()
            self.assertEqual(caught.exception.reason, "helper-busy")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as delayed:
                delayed.settimeout(1)
                delayed.connect(str(self.socket))
                time.sleep(0.05)
                delayed.sendall(b'{"action":"ping"}\n')
                reply = bytearray()
                while b"\n" not in reply and len(reply) < 1024:
                    chunk = delayed.recv(1024)
                    if not chunk:
                        break
                    reply.extend(chunk)
                self.assertEqual(json.loads(reply)["reason"], "helper-busy")
        finally:
            for client in clients:
                client.close()

    def test_reset_refuses_to_wait_behind_an_active_network_change(self):
        with self.service._network_lock:
            with self.assertRaises(HelperClientError) as caught:
                HelperClient(socket_path=self.socket, timeout_seconds=1).reset_wifi_name()
        self.assertEqual(caught.exception.reason, "helper-busy")
        self.assertFalse(self.executor.commands)
