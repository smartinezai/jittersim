"""Invalidated scans cannot deliver old identities or multiply active scans."""

import threading
import time
from pathlib import Path

from jittersim.networking.helper import HelperClient, HelperClientError
from tests.unit.test_helper_snapshot_cache import CLIENT


def launch(client, results, errors):
    def read():
        try:
            results.append(client.connected_device_snapshot())
        except HelperClientError as error:
            errors.append(error)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


def test_generation_controls_delivery_to_original_leader_and_waiter(monkeypatch):
    client = HelperClient(socket_path=Path("/tmp/unused.sock"))
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def send(payload, timeout_seconds=None):
        calls.append(payload)
        device = {**CLIENT, "ipv4_address": "192.168.50.10" if len(calls) == 1 else "192.168.50.11"}
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return {"ok": True, "association_status_available": True, "devices": [device]}

    monkeypatch.setattr(client, "_send", send)
    results, errors = [], []
    threads = [launch(client, results, errors)]
    assert entered.wait(1)
    flight = client._snapshots.flight
    original_wait = flight.done.wait

    def joined(timeout=None):
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(flight.done, "wait", joined)
    threads.append(launch(client, results, errors))
    assert waiting.wait(1)
    client.invalidate_connected_devices()
    release.set()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert len(errors) == 2
    assert results == [], "original callers must not receive invalidated verified identity"
    assert client.recent_connected_devices(10) is None
    fresh = client.connected_device_snapshot()
    assert fresh["devices"][0]["ipv4_address"] == "192.168.50.11"
    assert len(calls) == 2


def test_waiter_has_its_own_total_deadline_even_if_leader_is_stuck(monkeypatch):
    client = HelperClient(socket_path=Path("/tmp/unused.sock"), timeout_seconds=0.05)
    entered, release = threading.Event(), threading.Event()

    def send(payload, timeout_seconds=None):
        entered.set()
        assert release.wait(2)
        return {"ok": True, "association_status_available": True, "devices": [dict(CLIENT)]}

    monkeypatch.setattr(client, "_send", send)
    results, errors = [], []
    leader = launch(client, results, errors)
    assert entered.wait(1)
    started = time.monotonic()
    waiter = launch(client, results, errors)
    try:
        waiter.join(0.4)
        assert not waiter.is_alive(), "waiter ignored its own deadline"
        assert errors
        assert time.monotonic() - started < 0.4
    finally:
        release.set()
        leader.join(2)
        waiter.join(2)
    assert results == [], "expired callers must not receive a late verified identity"


def test_new_generation_callers_wait_for_old_scan_before_one_fresh_scan(monkeypatch):
    client = HelperClient(socket_path=Path('/tmp/unused.sock'))
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    active = calls = maximum = 0

    def send(payload, timeout_seconds=None):
        nonlocal active, calls, maximum
        with lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
            number = calls
        try:
            if number == 1:
                entered.set()
                assert release.wait(2)
            return {'ok': True, 'association_status_available': True,
                    'devices': [{**CLIENT, 'ipv4_address': f'192.168.50.{number + 9}'}]}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(client, '_send', send)
    old_results, old_errors, fresh_results, fresh_errors = [], [], [], []
    leader = launch(client, old_results, old_errors)
    assert entered.wait(1)
    client.invalidate_connected_devices()
    client.invalidate_connected_devices()
    original_wait = client._snapshots.flight.done.wait

    def wait(timeout=None):
        joined.set()
        return original_wait(timeout)

    monkeypatch.setattr(client._snapshots.flight.done, 'wait', wait)
    readers = [launch(client, fresh_results, fresh_errors) for _ in range(6)]
    assert joined.wait(1)
    assert calls == 1
    release.set()
    for thread in [leader, *readers]:
        thread.join(2)
        assert not thread.is_alive()
    assert old_results == [] and len(old_errors) == 1
    assert fresh_errors == [] and len(fresh_results) == 6
    assert {item['devices'][0]['ipv4_address'] for item in fresh_results} == {'192.168.50.11'}
    assert calls == 2 and maximum == 1
