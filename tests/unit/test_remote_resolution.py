"""Name lookup obeys the request deadline without keeping commands alive."""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from jittersim import remote_client
from tests.remote_support import make_certificate


def test_held_name_lookup_stops_at_the_request_deadline(monkeypatch):
    release = threading.Event()
    opened = Mock()
    def held(*args):
        release.wait(0.15)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", held)
    monkeypatch.setattr(socket, "socket", opened)
    started = time.monotonic()
    try:
        with pytest.raises(socket.timeout):
            remote_client._connect_before("netlab.local", 8080, started + 0.02, None)
        assert time.monotonic() - started < 0.1
        opened.assert_not_called()
    finally:
        release.set()


def test_expired_request_starts_neither_lookup_nor_connection(monkeypatch):
    resolve = Mock(return_value=[])
    opened = Mock()
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", opened)
    with pytest.raises(socket.timeout):
        remote_client._connect_before("netlab.local", 8080, time.monotonic() - 1, None)
    resolve.assert_not_called()
    opened.assert_not_called()


def test_repeated_timeouts_retain_lookup_capacity_until_workers_finish(monkeypatch):
    release = threading.Event()
    lock = threading.Lock()
    called = []
    finished = []

    def held(*args):
        with lock:
            called.append(args)
        release.wait(0.15)
        with lock:
            finished.append(args)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", held)
    try:
        for _ in range(12):
            with pytest.raises(OSError):
                remote_client._connect_before("netlab.local", 8080,
                                              time.monotonic() + 0.005, None)
        assert 1 <= len(called) <= 4
    finally:
        release.set()
    limit = time.monotonic() + 1
    while len(finished) < len(called) and time.monotonic() < limit:
        time.sleep(0.001)
    assert len(finished) == len(called)
    resolve = Mock(return_value=[])
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(OSError, match="no address"):
        remote_client._connect_before("netlab.local", 8080, time.monotonic() + 1, None)
    resolve.assert_called_once()


def test_lookup_errors_and_multiple_addresses_keep_the_same_deadline(monkeypatch):
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
                 for port in (1, 2)]
    monkeypatch.setattr(socket, "getaddrinfo", Mock(return_value=addresses))
    first, second = Mock(), Mock()
    first.connect.side_effect = OSError("first address refused")
    monkeypatch.setattr(socket, "socket", Mock(side_effect=[first, second]))
    assert remote_client._connect_before("netlab.local", 8080,
                                         time.monotonic() + 1, None) is second
    first.close.assert_called_once()
    assert 0 < second.settimeout.call_args.args[0] <= first.settimeout.call_args.args[0] <= 1
    error = socket.gaierror("test lookup failure")
    monkeypatch.setattr(socket, "getaddrinfo", Mock(side_effect=error))
    with pytest.raises(socket.gaierror, match="test lookup failure"):
        remote_client._connect_before("netlab.local", 8080, time.monotonic() + 1, None)


def test_remote_cli_process_exits_while_name_lookup_is_still_held(tmp_path):
    certificate, _ = make_certificate(tmp_path)
    script = """
import socket, sys, threading
from jittersim import remote_cli
socket.getaddrinfo = lambda *args: (threading.Event().wait(10), [])[1]
remote_cli.read_token = lambda: 'toy-test-token'
original = remote_cli._client
def short_request(arguments):
    client = original(arguments)
    client.timeout = 0.02
    return client
remote_cli._client = short_request
raise SystemExit(remote_cli.main(['status', '--url', 'https://netlab.local:8080',
                                '--ca-cert', sys.argv[1]]))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run([sys.executable, "-c", script, str(certificate)],
                            env=environment, capture_output=True, text=True, timeout=2)
    assert result.returncode == 1
    assert "did not answer within 0.02 seconds" in result.stderr


def test_interrupted_thread_start_retains_running_resolver_capacity(monkeypatch):
    release = threading.Event()
    started, finished = [], []
    workers = []
    original_start = threading.Thread.start

    def held(*args):
        started.append(args)
        release.wait(2)
        finished.append(args)
        return []

    def interrupted_start(worker):
        workers.append(worker)
        original_start(worker)
        limit = time.monotonic() + 1
        while len(started) < len(workers) and time.monotonic() < limit:
            time.sleep(0.001)
        raise KeyboardInterrupt

    monkeypatch.setattr(socket, "getaddrinfo", held)
    monkeypatch.setattr(threading.Thread, "start", interrupted_start)
    try:
        for _ in range(4):
            with pytest.raises(KeyboardInterrupt):
                remote_client._connect_before("netlab.local", 8080, time.monotonic() + 1, None)
        outcome = None
        try:
            remote_client._connect_before("netlab.local", 8080, time.monotonic() + 0.01, None)
        except BaseException as error:
            outcome = type(error)
        assert outcome is socket.timeout
        assert len(started) == 4
    finally:
        release.set()
        limit = time.monotonic() + 1
        while len(finished) < len(started) and time.monotonic() < limit:
            time.sleep(0.001)
    assert len(finished) == len(started)


def test_failed_thread_creation_returns_unused_capacity(monkeypatch):
    def cannot_start(worker):
        raise RuntimeError("can't start new thread")

    with monkeypatch.context() as failing:
        failing.setattr(threading.Thread, "start", cannot_start)
        for _ in range(6):
            with pytest.raises(RuntimeError, match="can't start"):
                remote_client._connect_before("netlab.local", 8080, time.monotonic() + 1, None)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [])
    with pytest.raises(OSError, match="no address"):
        remote_client._connect_before("netlab.local", 8080, time.monotonic() + 1, None)
