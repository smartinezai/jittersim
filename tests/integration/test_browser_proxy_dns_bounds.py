"""Keep real browser lookup work bounded after timeouts and server closure."""

import socket
import socketserver
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from jittersim.engine.lookup_pool import MAX_UNFINISHED_LOOKUPS, BoundedLookupPool
from jittersim.networking import browser_proxy
from tests.integration.test_browser_proxy import _EchoHandler


class BlockingResolver:
    def __init__(self):
        self.release = threading.Event()
        self.condition = threading.Condition()
        self.started = []
        self.active = 0
        self.peak = 0
        self.addresses = []

    def __call__(self, host):
        with self.condition:
            self.started.append(host)
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.condition.notify_all()
        try:
            assert self.release.wait(timeout=10), "lookup was not released by test cleanup"
            return self.addresses
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()


@pytest.fixture
def proxies(tmp_path, monkeypatch):
    pool = BoundedLookupPool(workers=8, name="proxy-dns-bounds-test")
    monkeypatch.setattr(browser_proxy, "_lookup_pool", pool, raising=False)
    monkeypatch.setattr(browser_proxy, "RESOLVE_TIMEOUT_SECONDS", 0.05)
    resolver = BlockingResolver()
    servers = []
    closed = set()
    errors = []

    def create():
        proxy = browser_proxy.BrowserProxyServer(
            ("127.0.0.1", 0), active_plan_path=tmp_path / "active.json",
            client_network="127.0.0.0/8", resolve_addresses=resolver,
            mark_socket=lambda *_: None,
        )
        proxy.handle_error = lambda *_: errors.append(sys.exc_info()[1])
        thread = threading.Thread(
            target=proxy.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        thread.start()
        servers.append((proxy, thread))
        return proxy

    def close(proxy):
        if proxy in closed:
            return
        closed.add(proxy)
        closer = threading.Thread(
            target=lambda: (proxy.shutdown(), proxy.server_close()), daemon=True,
        )
        closer.start()
        closer.join(timeout=2)
        assert not closer.is_alive(), "server closure stalled"
        thread = next(thread for candidate, thread in servers if candidate is proxy)
        thread.join(timeout=2)
        assert not thread.is_alive()

    try:
        yield create, close, resolver, errors
    finally:
        resolver.release.set()
        for proxy, _ in servers:
            close(proxy)
        executors = {pool._executor}
        for proxy, _ in servers:
            executors.add(getattr(proxy.resolver, "_executor", proxy.resolver))
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=True)


def connect(proxy, index=0):
    with socket.create_connection(proxy.server_address, timeout=2) as client:
        client.sendall(f"CONNECT example{index}.com:443 HTTP/1.1\r\n\r\n".encode())
        return client.recv(4096)


def batch(proxy, offset=0, size=16):
    with ThreadPoolExecutor(max_workers=size) as clients:
        responses = list(
            clients.map(lambda index: connect(proxy, index), range(offset, offset + size))
        )
    assert all(b"503 Service Unavailable" in response for response in responses)


def queued(proxy):
    executor = getattr(proxy.resolver, "_executor", proxy.resolver)
    return executor._work_queue.qsize()


def test_timed_out_connections_do_not_grow_unfinished_dns_work(proxies):
    create, _, resolver, errors = proxies
    proxy = create()
    for offset in (0, 16, 32):
        batch(proxy, offset)
        assert resolver.active + queued(proxy) <= MAX_UNFINISHED_LOOKUPS
    assert resolver.peak == 8
    assert errors == []


def test_recreating_servers_does_not_multiply_stalled_dns_workers(proxies):
    create, close, resolver, errors = proxies
    for offset in (0, 8, 16):
        proxy = create()
        batch(proxy, offset, size=8)
        close(proxy)
    assert resolver.peak == 8
    assert errors == []


@pytest.fixture
def echo():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
    )
    thread.start()
    try:
        yield [(socket.AF_INET, socket.SOCK_STREAM, 0, server.server_address)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def wait_until(condition):
    deadline = time.monotonic() + 2
    while not condition():
        assert time.monotonic() < deadline, "expected state did not arrive"
        time.sleep(0.001)


def occupy_workers(proxy, resolver):
    for index in range(8):
        assert proxy.resolver.submit(resolver, f"occupy{index}.example") is not None
    with resolver.condition:
        assert resolver.condition.wait_for(lambda: resolver.active == 8, timeout=2)


def assert_tunnel(proxy):
    with socket.create_connection(proxy.server_address, timeout=2) as client:
        client.sendall(b"CONNECT recovered.example:443 HTTP/1.1\r\n\r\n")
        assert b"200 Connection Established" in client.recv(4096)
        client.sendall(b"recovered-browser-body")
        assert client.recv(4096) == b"recovered-browser-body"


def test_cancelled_requests_are_skipped_and_new_tunnels_recover(proxies, echo):
    create, _, resolver, errors = proxies
    proxy = create()
    for offset in (0, 16, 32):
        batch(proxy, offset)
    assert resolver.active + queued(proxy) == MAX_UNFINISHED_LOOKUPS
    assert len(proxy._lookups) == 8
    resolver.addresses = echo
    resolver.release.set()
    wait_until(lambda: not proxy.resolver._unfinished)
    assert len(resolver.started) == 8
    wait_until(lambda: not proxy._lookups)

    assert_tunnel(proxy)

    assert resolver.started[-1] == "recovered.example"
    assert errors == []


def test_closing_a_server_cancels_queued_requests_without_freeing_physical_capacity(
    proxies, monkeypatch
):
    create, close, resolver, errors = proxies
    proxy = create()
    occupy_workers(proxy, resolver)
    monkeypatch.setattr(browser_proxy, "RESOLVE_TIMEOUT_SECONDS", 2)
    marks = []
    proxy.mark_socket = lambda *arguments: marks.append(arguments)
    with ThreadPoolExecutor(max_workers=1) as clients:
        response = clients.submit(connect, proxy)
        wait_until(lambda: len(proxy._lookups) == 1)
        pending, = proxy._lookups
        assert not pending.running()

        close(proxy)

        assert b"503 Service Unavailable" in response.result(timeout=2)
    assert pending.cancelled()
    assert len(proxy.resolver._unfinished) == 9
    assert marks == []
    resolver.release.set()
    wait_until(lambda: not proxy.resolver._unfinished)
    assert len(resolver.started) == 8
    assert errors == []


def test_server_closure_waits_for_submission_and_cancels_its_queued_result(
    proxies, monkeypatch
):
    create, close, resolver, errors = proxies
    proxy = create()
    occupy_workers(proxy, resolver)
    monkeypatch.setattr(browser_proxy, "RESOLVE_TIMEOUT_SECONDS", 2)
    admitted = threading.Event()
    let_submit_return = threading.Event()
    closing = threading.Event()
    submitted = []
    submit = proxy.resolver.submit
    original_close = proxy.server_close

    def paused_submit(*arguments):
        future = submit(*arguments)
        submitted.append(future)
        admitted.set()
        assert let_submit_return.wait(timeout=2)
        return future

    def observed_close():
        closing.set()
        original_close()

    monkeypatch.setattr(proxy.resolver, "submit", paused_submit)
    monkeypatch.setattr(proxy, "server_close", observed_close)
    with ThreadPoolExecutor(max_workers=2) as clients:
        response = clients.submit(connect, proxy)
        try:
            assert admitted.wait(timeout=2)
            stopped = clients.submit(close, proxy)
            assert closing.wait(timeout=2)
        finally:
            let_submit_return.set()
        stopped.result(timeout=2)
        assert b"503 Service Unavailable" in response.result(timeout=2)
    assert submitted[0].cancelled()
    assert len(proxy.resolver._unfinished) == 9
    assert errors == []


def test_a_running_lookup_cannot_open_a_tunnel_after_its_server_closes(
    proxies, echo, monkeypatch
):
    create, close, resolver, errors = proxies
    proxy = create()
    survivor = create()
    resolver.addresses = echo
    monkeypatch.setattr(browser_proxy, "RESOLVE_TIMEOUT_SECONDS", 2)
    marks = []
    proxy.mark_socket = lambda *arguments: marks.append(arguments)
    with ThreadPoolExecutor(max_workers=1) as clients:
        response = clients.submit(connect, proxy)
        with resolver.condition:
            assert resolver.condition.wait_for(lambda: resolver.active == 1, timeout=2)

        close(proxy)
        resolver.release.set()

        assert b"503 Service Unavailable" in response.result(timeout=2)
    assert marks == []
    assert_tunnel(survivor)
    assert errors == []


def test_an_already_completed_lookup_callback_does_not_hold_the_tracking_lock(
    proxies, echo, monkeypatch
):
    create, _, _, errors = proxies
    proxy = create()
    marks = []
    proxy.mark_socket = lambda _socket, mark: marks.append(mark)

    def completed(*_arguments):
        future = Future()
        future.set_result(echo)
        return future

    monkeypatch.setattr(proxy.resolver, "submit", completed)

    assert_tunnel(proxy)

    assert not proxy._lookups
    assert marks == [0, 0]
    assert errors == []
