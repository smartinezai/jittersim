"""A slow byte stream cannot extend the helper's total protocol deadline."""

import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from jittersim.networking.helper import HelperClient, HelperClientError, UnixHelperServer


def test_helper_client_deadline_covers_the_whole_slow_response():
    with TemporaryDirectory(prefix="js70-", dir="/tmp") as folder:
        path = Path(folder) / "reply.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(1)
        stopped = threading.Event()

        def reply():
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(1)
                connection.recv(4096)
                for value in b'{"ok":true}\n':
                    if stopped.wait(0.025):
                        return
                    try:
                        connection.sendall(bytes([value]))
                    except OSError:
                        return

        worker = threading.Thread(target=reply, daemon=True)
        worker.start()
        started = time.monotonic()
        try:
            with pytest.raises(HelperClientError):
                HelperClient(socket_path=path, timeout_seconds=0.08).ping()
            assert time.monotonic() - started < 0.25
        finally:
            stopped.set()
            worker.join(1)
            listener.close()
        assert not worker.is_alive()


def test_helper_input_deadline_releases_a_slow_partial_request_without_dispatch():
    with TemporaryDirectory(prefix="js70-", dir="/tmp") as folder:
        calls = []
        service = SimpleNamespace(handle_payload=lambda payload: calls.append(payload) or {"ok": True})
        server = UnixHelperServer(Path(folder) / "helper.sock", service)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(server.socket_path))
        stopped = threading.Event()

        def slowly_send():
            for value in b'{"action":"ping"}\n':
                try:
                    connection.sendall(bytes([value]))
                except OSError:
                    return
                if stopped.wait(0.4):
                    return

        sender = threading.Thread(target=slowly_send, daemon=True)
        sender.start()
        try:
            # Three seconds is the helper's input allowance. Bytes keep arriving
            # within every inactivity timeout throughout this interval.
            deadline = time.monotonic() + 3.3
            while time.monotonic() < deadline:
                time.sleep(0.01)
            assert not server._request_workers.has_workers, "partial input extended the total deadline"
            assert calls == [], "an incomplete command must never be dispatched"
        finally:
            stopped.set()
            sender.join(1)
            connection.close()
            server.shutdown()
            serving.join(1)
            server.server_close()
        assert not sender.is_alive() and not serving.is_alive()
