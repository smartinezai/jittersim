"""Panic retry recovers after bounded slow input exhausts the finite worker pools."""

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.api import server as api
from jittersim.networking import helper as helper_module
from jittersim.networking.helper import (
    ACCESS_STATUS_MAX_WAITERS,
    MAX_HELPER_CONNECTIONS,
    HelperClientError,
)
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


def test_all_helper_input_slots_stalled_still_let_normal_access_through():
    # Review T4: stalled input waits in its own pool and a complete Panic request
    # is read at once and served from the kept operation slot.
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (_, client):
            sockets = []
            try:
                # Repeats a connect that the small listen backlog refused.
                from tests.integration.test_capacity_stalled_clients import stall_helper

                sockets = stall_helper(directory / "helper.sock", helper_module.MAX_PENDING_HELPER_INPUT)
                # Each connection has a real admitted input worker first.
                time.sleep(0.05)
                with pytest.raises(HelperClientError) as failed:
                    client.ping()
                assert failed.value.reason == "helper-busy"
                assert client.normal_access()["ok"] is True
            finally:
                for connection in sockets:
                    connection.close()


def test_waiting_status_reads_never_take_the_slots_panic_and_reset_need():
    # The access lock holder plus every allowed waiter must leave room for
    # Panic and Reset at the same time.
    assert 1 + ACCESS_STATUS_MAX_WAITERS <= MAX_HELPER_CONNECTIONS - 2
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        directory = Path(temporary)
        with helper(directory) as (service, client):
            entered = threading.Event()
            release = threading.Event()
            slow = {"enabled": True}

            def access_check(payload):
                if slow["enabled"]:
                    entered.set()
                    assert release.wait(timeout=10)
                return {"ok": True, "allowed": True}

            results = []
            results_lock = threading.Lock()

            def status_read():
                reader = helper_module.HelperClient(socket_path=client.socket_path, timeout_seconds=15)
                try:
                    outcome = "ok" if reader.dashboard_access(peer="192.168.50.184")["ok"] else "refused"
                except HelperClientError as error:
                    outcome = error.reason or "error"
                with results_lock:
                    results.append(outcome)

            total = MAX_HELPER_CONNECTIONS + 4
            busy_expected = total - 1 - ACCESS_STATUS_MAX_WAITERS
            with patch.object(service, "_handle_dashboard_access", side_effect=access_check), \
                    patch.object(helper_module, "ACCESS_STATUS_LOCK_WAIT_SECONDS", 10):
                readers = [threading.Thread(target=status_read) for _ in range(total)]
                try:
                    readers[0].start()
                    assert entered.wait(timeout=5)
                    # Start the others one by one so the listen backlog never overflows:
                    # each one either becomes a waiter or is answered before the next.
                    for reader in readers[1:]:
                        with results_lock:
                            answered = len(results)
                        waiting = service._access_status_waiters._value
                        reader.start()
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            with results_lock:
                                if len(results) > answered:
                                    break
                            if service._access_status_waiters._value < waiting:
                                break
                            time.sleep(0.005)
                        else:
                            raise AssertionError("a status read neither waited nor was answered")
                    # One holder and the capped waiters still occupy their slots.
                    assert service._access_status_waiters._value == 0
                    with results_lock:
                        assert results == ["helper-busy"] * busy_expected
                    # Panic and Reset are served while the status reads still wait.
                    assert client.normal_access()["ok"] is True
                    assert client.reset()["ok"] is True
                finally:
                    release.set()
                    for reader in readers:
                        reader.join(15)
                assert not any(reader.is_alive() for reader in readers)
                assert sorted(results) == sorted(["ok"] * (total - busy_expected) + ["helper-busy"] * busy_expected)
                # Capacity recovers: the waiter budget is whole again and a full
                # burst of fast status reads plus Panic all succeed.
                assert service._access_status_waiters._value == ACCESS_STATUS_MAX_WAITERS
                slow["enabled"] = False
                results.clear()
                readers = [threading.Thread(target=status_read) for _ in range(1 + ACCESS_STATUS_MAX_WAITERS)]
                for reader in readers:
                    reader.start()
                for reader in readers:
                    reader.join(15)
                assert results == ["ok"] * (1 + ACCESS_STATUS_MAX_WAITERS)
                assert client.normal_access()["ok"] is True
