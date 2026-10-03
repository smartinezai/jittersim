"""A real HTTPS health response cannot extend its time by dripping headers."""

import socket
import ssl
import threading
import time

import pytest

from jittersim import updates
from tests.unit.test_api import create_test_certificate


def test_health_header_drip_has_a_physical_total_deadline(tmp_path, monkeypatch):
    pair = create_test_certificate(tmp_path, hostname="deadline-test.local")
    if pair is None:
        pytest.skip("OpenSSL creates the throwaway TLS identity")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*pair)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    stop = threading.Event()

    def serve():
        try:
            plain, _ = listener.accept()
            with context.wrap_socket(plain, server_side=True) as connection:
                connection.settimeout(2)
                request = bytearray()
                while b"\r\n\r\n" not in request and len(request) <= 4096:
                    block = connection.recv(4096)
                    if not block:
                        return
                    request.extend(block)
                connection.sendall(b"HTTP/1.1 200 OK\r\nX-Drip: ")
                for _ in range(40):
                    if stop.wait(0.02):
                        return
                    connection.sendall(b"x")
                body = b'{"status":"ok","helper_reachable":true}'
                connection.sendall(b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        except (OSError, ssl.SSLError):
            pass

    class MemoryFile:
        def read_text(self):
            return "JITTERSIM_ADMIN_TOKEN=in-memory-health-input\n"

    class MemoryConfig:
        def __truediv__(self, name):
            return MemoryFile() if name == "control.env" else tmp_path / name

    budget = updates.UpdateBudget()
    budget.deadline = budget.started + 0.25
    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setattr(updates.socket, "gethostname", lambda: "deadline-test")
    try:
        started = time.monotonic()
        assert not updates.healthy(config=MemoryConfig(), port=listener.getsockname()[1],
                                   run=lambda *args: None, budget=budget)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, "continuing header bytes must not extend the total health deadline"
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=2)
        assert not thread.is_alive()
