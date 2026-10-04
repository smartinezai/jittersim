"""A trusted CA health connection keeps one deadline during slow headers."""

import socket
import ssl
import threading
import time

from jittersim import localca, updates


def test_actual_ca_header_drip_cannot_extend_the_total_health_deadline(tmp_path):
    localca.prepare(tmp_path, host_name="ignored.local", neutral_name="netlab.local",
                    ethernet_ip="192.168.1.20")
    (tmp_path / "control.env").write_text("JITTERSIM_ADMIN_TOKEN=disposable-health-fixture\n")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "dashboard.crt", tmp_path / "dashboard.key")
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

    class Budget:
        def __init__(self):
            self.deadline = time.monotonic() + 0.25

        def check(self):
            if time.monotonic() >= self.deadline:
                raise updates.UpdateError("health deadline expired")

        def limit(self, seconds):
            self.check()
            return min(seconds, self.deadline - time.monotonic())

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        started = time.monotonic()
        assert not updates.healthy(config=tmp_path, port=listener.getsockname()[1],
                                   run=lambda *args: None, budget=Budget())
        assert time.monotonic() - started < 0.5
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=2)
        assert not thread.is_alive()
