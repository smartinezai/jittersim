"""Panic keeps helper capacity of its own (gap audit 2026-10-09 finding 4, review T4).

Incomplete helper input no longer holds the operation slots, and one operation
slot is kept for a complete normal-access request. The status-waiter cap of 3
from PR 91 is unchanged.
"""

import tempfile
import threading
import time
from pathlib import Path

import pytest

from jittersim.networking import helper as helper_module
from jittersim.networking.helper import (
    ACCESS_STATUS_MAX_WAITERS,
    MAX_HELPER_CONNECTIONS,
    HelperClient,
    HelperClientError,
    HelperService,
)
from tests.integration.test_capacity_stalled_clients import (
    running_experiment,
    served_helper,
    stall_helper,
    wait_until,
)


@pytest.fixture
def helper_dir():
    with tempfile.TemporaryDirectory(prefix="js-cap-", dir="/tmp") as temporary:
        yield Path(temporary).resolve()


def test_stalled_input_does_not_take_the_operation_slots(helper_dir):
    with served_helper(helper_dir) as (service, client, server):
        running_experiment(service)
        stalled = stall_helper(helper_dir / "helper.sock", MAX_HELPER_CONNECTIONS)
        try:
            assert wait_until(lambda: server._input_slots._value
                              == helper_module.MAX_PENDING_HELPER_INPUT - MAX_HELPER_CONNECTIONS, 2)
            assert server._request_slots._value == MAX_HELPER_CONNECTIONS
            assert client.ping()["ok"] is True
            assert client.normal_access()["ok"] is True
            assert service._panic_stop_latched is True
            assert service._active_plan is None
        finally:
            for connection in stalled:
                connection.close()


def test_panic_gets_through_when_every_input_slot_is_stalled(helper_dir):
    with served_helper(helper_dir) as (service, client, server):
        running_experiment(service)
        stalled = stall_helper(helper_dir / "helper.sock", helper_module.MAX_PENDING_HELPER_INPUT)
        try:
            assert wait_until(lambda: server._input_slots._value == 0, 2)
            with pytest.raises(HelperClientError) as busy:
                client.ping()
            assert busy.value.reason == "helper-busy"
            # A complete Panic request is read promptly and uses the kept slot.
            assert client.normal_access()["ok"] is True
            assert service._panic_stop_latched is True
        finally:
            for connection in stalled:
                connection.close()


def test_one_operation_slot_stays_for_panic_when_all_others_are_busy(helper_dir, monkeypatch):
    release, entered = threading.Event(), threading.Semaphore(0)
    original = HelperService.handle_payload

    def held(self, payload):
        if isinstance(payload, dict) and payload.get("action") == "ping":
            entered.release()
            release.wait(10)
        return original(self, payload)

    monkeypatch.setattr(HelperService, "handle_payload", held)
    with served_helper(helper_dir) as (service, client, server):
        running_experiment(service)
        def held_ping():
            # The helper's listen backlog is small: a refused connect never reached
            # it, so repeat it rather than lose one of the eight held pings.
            deadline = time.monotonic() + 20
            while True:
                try:
                    HelperClient(socket_path=helper_dir / "helper.sock", timeout_seconds=15).ping()
                    return
                except HelperClientError:
                    if release.is_set() or time.monotonic() > deadline:
                        return
                    time.sleep(0.02)

        workers = [threading.Thread(target=held_ping, daemon=True)
                   for _ in range(MAX_HELPER_CONNECTIONS)]
        for worker in workers:
            worker.start()
        try:
            for _ in workers:
                # A loaded machine can take a while to start eight client threads.
                assert entered.acquire(timeout=30)
            assert server._request_slots._value == 0
            started = time.monotonic()
            with pytest.raises(HelperClientError) as busy:
                client._request({"action": "capabilities"})
            assert busy.value.reason == "helper-busy"
            assert client.normal_access()["ok"] is True
            assert time.monotonic() - started < 5
            assert service._panic_stop_latched is True
        finally:
            release.set()
            for worker in workers:
                worker.join(10)


def test_the_status_waiter_cap_is_unchanged():
    assert ACCESS_STATUS_MAX_WAITERS == 3
    assert 1 + ACCESS_STATUS_MAX_WAITERS <= MAX_HELPER_CONNECTIONS - 2
