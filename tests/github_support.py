"""A fake GitHub over TLS for the online release check.

The server answers on the loopback address. Its certificate is made by openssl for the test and
the tests give the update manager an SSL context that trusts only that certificate, so the
manager still verifies the certificate and the host name. "localhost" plays api.github.com and
"127.0.0.1" plays the asset host. No test reaches the real GitHub.
"""

import json
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = "smartinezai/jittersim"
ARCHIVE_NAME = "jittersim-release.tar.gz"
SIGNATURE_NAME = ARCHIVE_NAME + ".sig"
TOKEN = "github_pat_11TESTTESTTEST0abcdefghij_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUV"

_CERT = {}


def certificate():
    """One throwaway certificate for localhost and 127.0.0.1, made once per test run."""
    if not _CERT:
        folder = Path(tempfile.mkdtemp(prefix="jittersim-github-cert-"))
        crt, key = folder / "fake.crt", folder / "fake.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost",
             "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
             "-addext", "extendedKeyUsage=serverAuth",
             "-keyout", str(key), "-out", str(crt)],
            check=True, capture_output=True,
        )
        _CERT["files"] = (crt, key)
    return _CERT["files"]


class FakeGitHub:
    """Serves one release. Every field is changed by a test to make a broken answer."""

    def __init__(self, archive=b"", signature=b""):
        self.files = {11: archive, 12: signature}
        self.names = {11: ARCHIVE_NAME, 12: SIGNATURE_NAME}
        self.tag = "v7"
        self.latest_status = 200
        self.latest_body = None
        self.extra_assets = []
        self.sizes = {}
        self.redirect_to = {}
        self.hops = {}
        self.delay = 0
        self.asset_status = {}
        self.length_lie = {}
        self.requests = []
        self.port = 0
        self.server = None

    # What the tests change

    def release_json(self):
        assets = [
            {"id": number, "name": self.names[number],
             "size": self.sizes.get(number, len(self.files[number])),
             "url": f"https://localhost:{self.port}/repos/{REPO}/releases/assets/{number}"}
            for number in sorted(self.files)
        ] + self.extra_assets
        return {"id": 5001, "tag_name": self.tag, "draft": False, "prerelease": False,
                "assets": assets, "body": "notes"}

    def seen(self, host):
        return [item for item in self.requests if item["host"] == host]

    def asset_requests(self):
        return [item for item in self.requests if "/releases/assets/" in item["path"]]

    # Server

    def start(self, context_dir=None):
        crt, key = certificate()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def send(self, status, body=b"", headers=None, length=None):
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body) if length is None else length))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                owner.requests.append({
                    "host": self.headers.get("Host"), "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                })
                if owner.delay:
                    time.sleep(owner.delay)
                latest = f"/repos/{REPO}/releases/latest"
                prefix = f"/repos/{REPO}/releases/assets/"
                if self.path == latest:
                    if owner.latest_body is not None:
                        body = owner.latest_body
                    else:
                        body = json.dumps(owner.release_json()).encode()
                    return self.send(owner.latest_status, body,
                                     {"Content-Type": "application/json"})
                if self.path.startswith(prefix):
                    number = int(self.path[len(prefix):])
                    if number in owner.asset_status:
                        return self.send(owner.asset_status[number], b"error " + TOKEN.encode())
                    if number in owner.redirect_to:
                        return self.send(302, b"", {"Location": owner.redirect_to[number]})
                    return self.send(
                        302, b"",
                        {"Location": f"https://127.0.0.1:{owner.port}/dl/{number}"})
                if self.path.startswith("/dl/") or self.path.startswith("/hop"):
                    if self.path.startswith("/hop"):
                        return self.send(302, b"", {"Location": owner.hops[self.path]})
                    number = int(self.path[4:])
                    body = owner.files[number]
                    return self.send(200, body, {"Content-Type": "application/octet-stream"},
                                     length=owner.length_lie.get(number))
                return self.send(404, b"{}")

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(crt), str(key))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def trusting_context(self):
        return ssl.create_default_context(cafile=str(certificate()[0]))
