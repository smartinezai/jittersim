"""`systemctl stop` sends SIGTERM: the real dashboard must stop cleanly and exit 0."""

import http.client
import os
import re
import signal
import socket
import subprocess
import sys
import time

import pytest

SHUTDOWN_SECONDS = 8.0


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def dashboard(tmp_path):
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "jittersim.api", "--database", str(tmp_path / "dashboard.db"),
         "--port", str(port)],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "JITTERSIM_ADMIN_TOKEN": "sigterm-test-token"},
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            assert process.poll() is None, process.communicate()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("GET", "/api/health")
                if connection.getresponse().status == 200:
                    break
            except OSError:
                assert time.monotonic() < deadline, "dashboard did not start"
                time.sleep(0.1)
        yield process, port
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def stop(process, *, within=SHUTDOWN_SECONDS):
    started = time.monotonic()
    process.send_signal(signal.SIGTERM)
    try:
        _, errors = process.communicate(timeout=within)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("the dashboard did not stop within %s s of SIGTERM" % within)
    return time.monotonic() - started, errors


def test_sigterm_stops_an_idle_dashboard_with_exit_zero_and_a_closed_port(dashboard):
    process, port = dashboard
    elapsed, errors = stop(process, within=5)
    assert process.returncode == 0, errors
    assert elapsed < 5
    assert "Traceback" not in errors, errors
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_sigterm_lets_a_slow_request_finish_before_the_dashboard_exits(dashboard):
    process, port = dashboard
    # The request line and one header have arrived but not the blank line that ends them.
    client = socket.create_connection(("127.0.0.1", port), timeout=10)
    client.sendall(b"GET /api/health HTTP/1.1\r\nHost: dashboard\r\nConnection: close\r\n")
    time.sleep(0.5)
    process.send_signal(signal.SIGTERM)
    time.sleep(1)
    assert process.poll() is None, "the dashboard exited while a request was still in flight"
    # No new connection is accepted once the dashboard is stopping.
    with pytest.raises(OSError):
        late = socket.create_connection(("127.0.0.1", port), timeout=1)
        late.sendall(b"GET /api/health HTTP/1.1\r\nHost: dashboard\r\nConnection: close\r\n\r\n")
        if late.recv(1) == b"":
            raise ConnectionResetError("closed without an answer")
    started = time.monotonic()
    client.sendall(b"\r\n")
    answer = b""
    while chunk := client.recv(4096):
        answer += chunk
    client.close()
    assert re.match(rb"HTTP/1\.[01] 200 ", answer), answer[:80]
    try:
        _, errors = process.communicate(timeout=SHUTDOWN_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("the dashboard did not exit after the request finished")
    assert process.returncode == 0, errors
    assert time.monotonic() - started < SHUTDOWN_SECONDS
    assert "Traceback" not in errors, errors


def test_sigterm_does_not_wait_forever_for_a_request_that_never_finishes(dashboard):
    process, port = dashboard
    client = socket.create_connection(("127.0.0.1", port), timeout=30)
    client.sendall(b"GET /api/health HTTP/1.1\r\nHost: dashboard\r\n")
    time.sleep(0.5)
    started = time.monotonic()
    elapsed, errors = stop(process, within=20)
    client.close()
    # The wait is bounded. An unfinished request keeps a worker, so the exit says so.
    assert elapsed < 12, (elapsed, time.monotonic() - started)
    assert process.returncode in (0, 1), errors
