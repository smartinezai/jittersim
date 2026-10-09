"""A disposable dashboard for the exploratory QA tests.

It is the browser fixture's dashboard (fake network executor behind the real helper over a unix
socket, a real SQLite repository) started inside the test process so a test can also read what
the server logged. Nothing touches a Pi, a router or the network.
"""

import contextlib
import http.client
import json
import logging
import os
import socket
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from jittersim.config.storage import ConfigRepository
from jittersim.networking.access import AccessManager  # noqa: F401  (documents the fixture's parts)
from jittersim.networking.adblocking import AdRouting, AdStore
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.dashboard_access import AccessStore
from jittersim.networking.helper import UnixHelperServer
from jittersim.networking.topology import Topology
from tests.adblocking_support import provision_guard
from tests.browser import fixture

TOKEN = "browser-test"


class QAHelperService(fixture.DashboardHelperService):
    """The fixture's helper with the release actions cut off from the real machine.

    The real update code works under /var/lib/jittersim-updates and /opt/jittersim, starts systemd
    units and may call GitHub. A test must not do that, least of all as root. Each such action is
    answered the way the helper answers a refusal.
    """

    def _handle_payload(self, payload):
        action = payload.get("action") if isinstance(payload, dict) else None
        if isinstance(action, str) and action.startswith("update-") and action != "update-status":
            return {"ok": False, "error": "release actions are switched off in the QA fixture",
                    "reason": "qa-stub"}
        return super()._handle_payload(payload)


class _Capture(logging.Handler):
    """Keep every record at WARNING or above, with its traceback text when there is one."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def tracebacks(self):
        return [record for record in self.records if record.exc_info]


@dataclass
class Dashboard:
    port: int
    token: str
    repository: object
    executor: object
    logs: _Capture
    outside_connections: list = field(default_factory=list)

    def request(self, method, path, *, body=None, headers=None, token=TOKEN, timeout=20.0):
        """One request. Returns (status, response headers, raw body bytes)."""

        sent = dict(headers or {})
        if token is not None and "Authorization" not in sent:
            sent["Authorization"] = "Bearer " + token
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
            sent.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def raw(self, data: bytes, *, timeout=10.0, read_bytes=65536):
        """Send raw bytes and return whatever the server answers before it closes or goes quiet."""

        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as connection:
            connection.sendall(data)
            chunks = []
            try:
                while True:
                    chunk = connection.recv(read_bytes)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\r\n\r\n" in b"".join(chunks) and len(b"".join(chunks)) >= 12:
                        head, _, rest = b"".join(chunks).partition(b"\r\n\r\n")
                        length = None
                        for line in head.split(b"\r\n"):
                            if line.lower().startswith(b"content-length:"):
                                length = int(line.split(b":", 1)[1])
                        if length is not None and len(rest) >= length:
                            break
            except socket.timeout:
                pass
            return b"".join(chunks)


@contextlib.contextmanager
def _no_outside_network(attempts):
    """Refuse every connection or name lookup that leaves this machine and write it down."""

    real_connect, real_connect_ex, real_getaddrinfo = (
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo)

    def local(address):
        host = address[0] if isinstance(address, tuple) else address
        return isinstance(host, str) and (host.startswith("127.") or host in ("::1", "localhost", "0.0.0.0", ""))

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address):
            attempts.append(("connect", address))
            raise OSError("the QA fixture does not reach outside this machine")
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address):
            attempts.append(("connect_ex", address))
            return 1
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, (str, bytes)) and not local((host.decode() if isinstance(host, bytes) else host,)):
            try:
                import ipaddress
                if not ipaddress.ip_address(host).is_loopback:
                    raise ValueError
            except ValueError:
                attempts.append(("getaddrinfo", host))
                raise socket.gaierror("the QA fixture does not look names up")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = (
            real_connect, real_connect_ex, real_getaddrinfo)


@contextlib.contextmanager
def running_dashboard(*, seed=True, observations=False):
    """Yield a Dashboard while the fixture server runs in this process."""

    attempts = []
    with _no_outside_network(attempts), _running_dashboard(seed, observations, attempts) as dashboard:
        yield dashboard


@contextlib.contextmanager
def _running_dashboard(seed, observations, attempts):

    saved = {name: os.environ.get(name) for name in (
        "JITTERSIM_BROWSER_TEST_SEED", "JITTERSIM_BROWSER_TEST_OBSERVATIONS")}
    os.environ["JITTERSIM_BROWSER_TEST_SEED"] = "1" if seed else "0"
    os.environ["JITTERSIM_BROWSER_TEST_OBSERVATIONS"] = "1" if observations else "0"
    capture = _Capture()
    logging.getLogger().addHandler(capture)
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = fixture.DashboardWifiExecutor()
            topology = Topology("eth0", "wlan0")
            ad_blocking = AdStore(
                AdRouting("wlan0", executor), path=root / "ad-policy.json", lock_path=root / "ad-lock",
                active_path=root / "ad-active.json", status_path=root / "ad-status.json",
                intent_path=root / "ad-intent.json", panic_guard=provision_guard(root))
            access_manager = fixture.FixtureAccessManager(
                executor=executor, topology=topology, directory=root / "access",
                runtime_path=root / "access-policy.json", hostapd_path=root / "hostapd",
                ad_blocking_store=ad_blocking)
            service = QAHelperService(
                NetworkApplier(executor=executor, topology=topology),
                router_factory=lambda *_: fixture.DashboardRouter(),
                access_store=AccessStore(root / "access.json"),
                ad_blocking_store=ad_blocking, access_manager=access_manager)
            with UnixHelperServer(root / "wifi.sock", service) as helper:
                helper_thread = threading.Thread(target=helper.serve_forever, daemon=True)
                helper_thread.start()
                try:
                    with ConfigRepository(root / "config.db") as repository:
                        fixture.seed_observations(repository)
                        fixture.seed_demo_data(repository)
                        helper_client = fixture.PatientHelperClient(socket_path=root / "wifi.sock")
                        with fixture.FixtureServer(
                            ("127.0.0.1", 0), repository, admin_token=TOKEN, helper_client=helper_client,
                            scheduler=None,
                        ) as server:
                            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                            server_thread.start()
                            try:
                                yield Dashboard(server.server_address[1], TOKEN, repository, executor, capture, attempts)
                            finally:
                                server.shutdown()
                                server_thread.join(timeout=5)
                finally:
                    helper.shutdown()
                    helper_thread.join(timeout=3)
    finally:
        logging.getLogger().removeHandler(capture)
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
