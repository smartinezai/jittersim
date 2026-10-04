"""A partial HTTP chunk cannot occupy the sole refresh worker indefinitely."""

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from jittersim.networking.adblocking import download_domains


def test_slow_partial_chunk_expires_and_the_same_worker_can_refresh():
    trickle = [True]
    connected = threading.Event()
    payload = b"".join(f"0.0.0.0 ads{i}.example.net\n".encode() for i in range(100))

    class Publisher(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(100_000 if trickle[0] else len(payload)))
            self.end_headers()
            connected.set()
            try:
                if trickle[0]:
                    for _ in range(80):
                        self.wfile.write(b"#")
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Publisher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run = subprocess.run
    url = f"http://127.0.0.1:{server.server_address[1]}/hosts"

    def local_publisher(command, **options):
        assert command == [
            sys.executable,
            "-m",
            "jittersim.networking.adblocking",
            "--fetch-domains",
        ]
        code = (
            "import sys; from jittersim.networking import adblocking; "
            "adblocking.BLOCKLIST_URL = sys.argv[1]; "
            "sys.stdout.buffer.write(adblocking.download_bytes())"
        )
        return run(
            [sys.executable, "-c", code, url],
            env={**os.environ, "NO_PROXY": "127.0.0.1"},
            **options,
        )

    try:
        with (
            ThreadPoolExecutor(max_workers=1) as worker,
            patch("jittersim.networking.adblocking.subprocess.run", side_effect=local_publisher),
            patch("jittersim.networking.adblocking.MAX_DOWNLOAD_SECONDS", 1.5),
        ):
            started = time.monotonic()
            future = worker.submit(download_domains)
            assert connected.wait(1.3), "The child must reach the slow publisher before expiry"
            with pytest.raises(subprocess.TimeoutExpired):
                future.result(timeout=3)
            assert time.monotonic() - started < 2.8
            trickle[0] = False
            assert len(worker.submit(download_domains).result(timeout=3)) == 100
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
