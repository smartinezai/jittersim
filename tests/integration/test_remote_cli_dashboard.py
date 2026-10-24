"""Every remote command against the real dashboard app over real HTTPS on loopback.

The dashboard, helper and fake kernel are the ones the browser journeys use
(`tests/browser/fixture.py`). The token is the fixture's `browser-test`.
"""

import queue
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.api.server import ControlServer
from jittersim.cli import main
from jittersim.config.models import DomainObservation
from jittersim.config.storage import ConfigRepository
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.dashboard_access import AccessStore
from jittersim.networking.helper import UnixHelperServer
from jittersim.networking.topology import Topology
from jittersim.remote_client import CERTIFICATE_MESSAGE, Connection, DashboardClient
from tests.browser.fixture import (
    DashboardHelperService,
    DashboardRouter,
    DashboardWifiExecutor,
    FixtureAccessManager,
    PatientHelperClient,
)
from tests.remote_support import make_certificate

TOKEN = "browser-test"
MAC = "02:00:00:00:00:01"
ADDRESS = "192.168.50.184"


@contextmanager
def live_dashboard(directory: Path):
    root = Path(tempfile.mkdtemp(prefix="js-remote-", dir="/tmp"))
    executor = DashboardWifiExecutor()
    topology = Topology("eth0", "wlan0")
    access = FixtureAccessManager(
        executor=executor, topology=topology, directory=root / "access",
        runtime_path=root / "access-policy.json", hostapd_path=root / "hostapd",
    )
    service = DashboardHelperService(
        NetworkApplier(executor=executor, topology=topology),
        router_factory=lambda *_: DashboardRouter(),
        access_store=AccessStore(root / "access.json"), access_manager=access,
    )
    certificate, key = make_certificate(directory, "live")
    helper = UnixHelperServer(root / "helper.sock", service)
    helper_thread = threading.Thread(target=helper.serve_forever, daemon=True)
    helper_thread.start()
    helper_client = PatientHelperClient(socket_path=root / "helper.sock")
    scheduler = RuntimeScheduler(database_path=root / "config.db", helper_client=helper_client)
    ready = queue.Queue()

    def serve():
        # SQLite connections stay in the thread that made them.
        with ConfigRepository(root / "config.db") as repository:
            now = int(time.time())
            repository.save_domain_observations([
                DomainObservation("192.168.50.124", "logs.dradis.netflix.com", 3, now, now),
                DomainObservation("192.168.50.124", "ipv4-c1.oca.nflxvideo.net", 40, now, now),
                DomainObservation("192.168.50.124", "tags-eu.tiqcdn.com", 2, now, now),
            ], now=now)
            with ControlServer(("127.0.0.1", 0), repository, admin_token=TOKEN,
                               helper_client=helper_client, scheduler=scheduler,
                               tls_certfile=certificate, tls_keyfile=key) as server:
                ready.put(server)
                server.serve_forever()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    server = ready.get(timeout=10)
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}", certificate
    finally:
        server.shutdown()
        worker.join(timeout=5)
        scheduler.stop()
        helper.shutdown()
        helper.server_close()
        helper_thread.join(timeout=5)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("JITTERSIM_URL", "JITTERSIM_CA_CERT", "JITTERSIM_ADMIN_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with live_dashboard(tmp_path) as (url, certificate):
        yield url, certificate


def run(capsys, *arguments, prompts=(TOKEN,)):
    with patch("jittersim.remote_client.getpass.getpass", side_effect=list(prompts)):
        code = main(list(arguments))
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    return code, output.out, output.err


def test_every_remote_command_against_the_live_dashboard(dashboard, capsys):
    url, certificate = dashboard
    code, out, _ = run(capsys, "connect", "--url", url, "--ca-cert", str(certificate), prompts=())
    assert code == 0, out
    assert "The certificate matches the dashboard" in out

    code, out, _ = run(capsys, "status")
    assert code == 0
    assert "Nothing is running. Choose Start." in out
    assert "Everyone: none" in out
    # The fixture's helper answers the release status like a Pi without a release label.
    assert out.splitlines()[1] == "Release on the Pi: Unversioned installation"

    setup = DashboardClient(Connection(url, certificate), TOKEN)
    setup.post("/api/profiles", {
        "id": "lossy", "name": "Lossy",
        "upload": {"loss_percent": 10, "delay_ms": 100, "jitter_ms": 20},
        "download": {"loss_percent": 10, "bandwidth_kbit": 250},
    })
    setup.post("/api/connected-device-labels",
               {"mac_address": MAC, "display_name": "Fixture phone", "device_type": "phone"})
    setup.post("/api/connected-devices/apply-profile",
               {"ipv4_address": ADDRESS, "mac_address": MAC, "profile_id": "lossy"})
    setup.post("/api/apply", {}, timeout=60)

    code, out, _ = run(capsys, "status")
    assert code == 0
    assert "Running. Your saved settings are applied. New changes apply on their own." in out
    assert "1 device affected:" in out
    assert "  Fixture phone: Lossy" in out

    code, out, _ = run(capsys, "profiles", "list")
    assert code == 0
    assert "Lossy (lossy)" in out
    assert "  ↑ 100 ms ±20 · 10% loss · ↓ 10% loss · 250 kbit/s" in out

    code, out, _ = run(capsys, "impact", "--wait", "1")
    assert code == 0, out
    # The verdict names the time the command measured, not the page's minute.
    assert re.search(r"Fixture phone · Upload · Affected in the last \d+ seconds?\n", out)
    assert re.search(r"Fixture phone · Download · Affected in the last \d+ seconds?\n", out)
    assert "last minute" not in out
    assert "  Loss: set 10% · actual 9.9% (11 of 111 packets dropped)" in out
    assert "  Delay: set 100 ms ± 20 · configured, not measured" in out

    code, out, _ = run(capsys, "profiles", "apply-all", "lossy")
    assert code == 0, out
    assert "Protected devices remain unaffected." in out
    code, out, _ = run(capsys, "status", "--json")
    assert code == 0
    assert '"status": "active"' in out
    code, out, _ = run(capsys, "profiles", "stop-all")
    assert code == 0
    assert "General profile stopped. Saved device rules resumed." in out

    code, out, _ = run(capsys, "traffic", "--expand")
    assert code == 0
    assert "Netflix  [app]  2 names  43 queries" in out
    assert "tags-eu.tiqcdn.com" in out
    code, out, _ = run(capsys, "traffic", "block", "tags-eu.tiqcdn.com")
    assert code == 0
    assert "It applies on its own while profiles are running." in out
    code, out, _ = run(capsys, "traffic", "--blocked")
    assert "tags-eu.tiqcdn.com" in out and "On global block list" in out and "netflix" not in out
    code, out, _ = run(capsys, "traffic", "unblock", "tags-eu.tiqcdn.com")
    assert code == 0
    assert out.startswith("Saved: removed tags-eu.tiqcdn.com")
    code, out, _ = run(capsys, "traffic", "unblock", "tags-eu.tiqcdn.com")
    assert code == 0
    assert "was not on the global block list. Nothing changed." in out
    code, out, _ = run(capsys, "traffic", "--scan")
    assert code == 0
    assert "Traffic scan complete:" in out

    code, out, _ = run(capsys, "wifi", "show", prompts=(TOKEN, "router-fixture-password"))
    assert code == 0, out
    assert out == "Router Wi-Fi name: Browser home\n"
    code, out, err = run(capsys, "wifi", "show", "--password",
                         prompts=(TOKEN, "router-fixture-password"))
    assert code == 0
    assert "Router Wi-Fi password: browser-wifi-password" in out
    assert "router-fixture-password" not in out + err


def test_a_wrong_token_and_a_wrong_certificate_are_refused_by_the_live_dashboard(
        dashboard, capsys, tmp_path):
    url, certificate = dashboard
    code, _, err = run(capsys, "status", "--url", url, "--ca-cert", str(certificate),
                       prompts=("wrong-token",))
    assert code == 1
    assert "refused the admin token" in err
    other, _ = make_certificate(tmp_path, "other")
    code, _, err = run(capsys, "status", "--url", url, "--ca-cert", str(other))
    assert code == 1
    assert err.strip() == CERTIFICATE_MESSAGE
