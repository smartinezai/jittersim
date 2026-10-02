"""A disposable HTTPS dashboard for the remote command tests.

The certificates are made for each test run. Nothing here holds a real token.
"""

import json
import shutil
import ssl
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest.mock import patch

from jittersim.cli import main

TEST_TOKEN = "unit-test-token-1234"
_MADE: Dict[str, Tuple[Path, Path]] = {}


def make_certificate(directory: Path, name: str = "dashboard") -> Tuple[Path, Path]:
    """A self-signed certificate for 127.0.0.1 and localhost like the installer makes.

    Each name gets one key pair for the whole test run. Different names never match.
    """

    if name not in _MADE:
        _MADE[name] = _generate(Path(tempfile.mkdtemp(prefix="jittersim-cert-")), name)
    certificate = directory / f"{name}.crt"
    key = directory / f"{name}.key"
    shutil.copyfile(_MADE[name][0], certificate)
    shutil.copyfile(_MADE[name][1], key)
    return certificate, key


def _generate(directory: Path, name: str) -> Tuple[Path, Path]:
    certificate = directory / f"{name}.crt"
    key = directory / f"{name}.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
            "-addext", "extendedKeyUsage=serverAuth",
            "-keyout", str(key), "-out", str(certificate),
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


class FakeDashboard:
    """Answers set per route and a record of every request it received."""

    def __init__(self) -> None:
        self.routes: Dict[Tuple[str, str], Tuple[int, Any, Dict[str, str]]] = {}
        self.requests: List[Dict[str, Any]] = []
        self.url = ""
        self.certificate: Optional[Path] = None

    def answer(self, method: str, path: str, body: Any, status: int = 200,
               headers: Optional[Dict[str, str]] = None) -> None:
        self.routes[(method, path)] = (status, body, headers or {})

    def paths(self) -> List[Tuple[str, str]]:
        return [(item["method"], item["path"]) for item in self.requests]


def _handler(dashboard: FakeDashboard):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            dashboard.requests.append({
                "method": method,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(raw) if raw else None,
                "headers": dict(self.headers),
            })
            status, body, headers = dashboard.routes.get(
                (method, self.path), (404, {"error": "not found"}, {})
            )
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def log_message(self, *_: Any) -> None:
            pass

    return Handler


@contextmanager
def fake_dashboard(directory: Path, *, tls: bool = True) -> Iterator[FakeDashboard]:
    dashboard = FakeDashboard()
    with HTTPServer(("127.0.0.1", 0), _handler(dashboard)) as server:
        if tls:
            certificate, key = make_certificate(directory)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            dashboard.certificate = certificate
            dashboard.url = f"https://127.0.0.1:{server.server_port}"
        else:
            dashboard.url = f"http://127.0.0.1:{server.server_port}"
        worker = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        worker.start()
        try:
            yield dashboard
        finally:
            server.shutdown()
            worker.join(timeout=5)


# Shared by the remote command tests. The fixtures are in tests/unit/conftest.py.

ROUTER_PASSWORD = "router-admin-secret-77"
WIFI_PASSWORD = "household-wifi-pass-88"


def run(dashboard, capsys, *arguments, prompts=(TEST_TOKEN,)):
    """Run a command with --url and --ca-cert and the hidden prompts answered in order."""

    answers = list(prompts)
    with patch("jittersim.remote_client.getpass.getpass", side_effect=answers) as prompt:
        code = main([*arguments, "--url", dashboard.url, "--ca-cert", str(dashboard.certificate)])
    output = capsys.readouterr()
    for secret in (TEST_TOKEN, ROUTER_PASSWORD):
        assert secret not in output.out + output.err
    return code, output.out, output.err, prompt


def state(observed="applied", running=True, last_action="apply", last_result="success"):
    return {"state": {"observed_state": observed, "running": running, "last_action": last_action,
                      "last_result": last_result, "last_error": None, "configuration_version": 3}}


def client(name, mac, **fields):
    return {"display_name": name, "mac_address": mac, "unaffected": False,
            "association_verified": True, "assigned_device_id": None,
            "assigned_profile_id": None, "group_profile_id": None, **fields}


PROFILES = [
    {"id": "slow", "name": "Slow connection",
     "upload": {"delay_ms": 50, "jitter_ms": 10, "loss_percent": 2, "bandwidth_kbit": 5000},
     "download": {"delay_ms": 50, "jitter_ms": 10, "loss_percent": 2, "bandwidth_kbit": 5000},
     "outage_probability": 0, "website_mode": "block", "website_domains": []},
    {"id": "lossy", "name": "Lossy",
     "upload": {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 10, "bandwidth_kbit": None},
     "download": {"delay_ms": 300, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": 640},
     "outage_probability": 5, "website_mode": "allow_only", "website_domains": ["a.example"]},
]


RELEASE = {"installed": "abc1234", "available": None, "phase": "idle",
           "message": "No approved release has been staged on this Pi.",
           "rollback_available": False}


def serve_status(dashboard, *, general=None, active=False, observed="applied", running=True,
                 clients=(), lists=(), available=True, release=RELEASE):
    dashboard.answer("GET", "/api/health", {"status": "ok", "helper_configured": True})
    if release is not None:
        dashboard.answer("GET", "/api/updates", release)
    dashboard.answer("GET", "/api/state", state(observed, running))
    dashboard.answer("GET", "/api/general-profile", {
        "profile": general, "active": active, "state": state(observed)["state"]})
    dashboard.answer("GET", "/api/connected-devices", {
        "available": available, "association_status_available": True, "devices": list(clients)})
    dashboard.answer("GET", "/api/profiles", {"profiles": PROFILES})
    dashboard.answer("GET", "/api/service-lists", {"service_lists": list(lists), "catalogue": []})


