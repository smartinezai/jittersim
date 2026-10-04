"""Panic retry recovers after bounded slow input exhausts the finite worker pools."""

import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.api import server as api
from jittersim.networking.helper import MAX_HELPER_CONNECTIONS, HelperClientError
from tests.unit import test_dashboard_concurrency as concurrency
from tests.unit.test_panic_restart_latch import helper


class DashboardSaturationTests(unittest.TestCase):
    setUp = concurrency.DashboardConcurrencyTests.setUp
    _stop = concurrency.DashboardConcurrencyTests._stop
    request = concurrency.DashboardConcurrencyTests.request
    start_slow_apply = concurrency.DashboardConcurrencyTests.start_slow_apply

    def test_reserved_slow_headers_expire_then_panic_retry_recovers(self):
        sockets = []
        with (
            patch.object(api, "MAX_DASHBOARD_WORKERS", 1),
            patch.object(api, "RESERVED_PANIC_WORKERS", 2),
            patch.object(api, "REQUEST_INPUT_TIMEOUT_SECONDS", 0.5),
        ):
            result, worker = self.start_slow_apply()
            try:
                for _ in range(2):
                    connection = socket.create_connection(self.server.server_address[:2], timeout=2)
                    connection.sendall(b"POST /api/normal-access HTTP/1.1\r\nHost:")
                    sockets.append(connection)
                deadline = time.monotonic() + 0.4
                while self.server.active_workers != 3 and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert self.server.active_workers == 3
                assert self.request("POST", "/api/normal-access", {})[0] == 503
                assert self.helper.normal_access_calls == 0
                deadline = time.monotonic() + 1.5
                while self.server.active_workers != 1 and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert self.server.active_workers == 1
                assert self.request("POST", "/api/normal-access", {})[0] == 200
                self.helper.release_apply.set()
                worker.join(2)
                assert result["response"][0] == 409
                assert not self.scheduler.is_active
            finally:
                for connection in sockets:
                    connection.close()
                self.helper.release_apply.set()


def test_all_helper_input_slots_expire_then_normal_access_retry_recovers():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (_, client):
            sockets = []
            try:
                for _ in range(MAX_HELPER_CONNECTIONS):
                    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connection.connect(str(directory / "helper.sock"))
                    connection.sendall(b"{")
                    sockets.append(connection)
                # Each connection has a real admitted input worker before the rejection.
                time.sleep(0.05)
                with pytest.raises(HelperClientError) as failed:
                    client.normal_access()
                assert failed.value.reason == "helper-busy"
                time.sleep(3.2)
                assert client.normal_access()["ok"] is True
            finally:
                for connection in sockets:
                    connection.close()
