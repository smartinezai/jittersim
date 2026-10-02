"""Exercise every protected route across HTTP and the real helper socket."""

import http.client
import json
import queue
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from jittersim.api.server import WRITE_PATHS, ControlServer
from jittersim.config.storage import ConfigRepository
from jittersim.networking.dashboard_access import AccessStore
from jittersim.networking.helper import HelperClient, HelperService, UnixHelperServer
from tests.unit.test_dashboard_access import MAC, PEER, PeerApplier


@contextmanager
def dashboard(tmp_path):
    applier = PeerApplier()
    store = AccessStore(tmp_path / "access.json")
    socket_directory = Path(tempfile.mkdtemp(prefix="js-access-", dir="/tmp"))
    socket_path = socket_directory / "helper.sock"
    helper = UnixHelperServer(socket_path, HelperService(applier, access_store=store))
    worker = threading.Thread(target=helper.serve_forever)
    worker.start()
    ready = queue.Queue()

    class WifiServer(ControlServer):
        def get_request(self):
            connection, address = super().get_request()
            # A disposable loopback transport stands in for a real Wi-Fi peer.
            return connection, (PEER, address[1])

    def serve():
        with ConfigRepository(tmp_path / "config.db") as repository:
            with WifiServer(("127.0.0.1", 0), repository, admin_token="test-token",
                            helper_client=HelperClient(socket_path=socket_path)) as server:
                ready.put(server)
                server.serve_forever()

    http_worker = threading.Thread(target=serve)
    http_worker.start()
    server = ready.get(timeout=5)

    def request(path, method="GET", payload=None, token="test-token", **headers):
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        body = None if payload is None else json.dumps(payload)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    try:
        yield request, store, applier
    finally:
        server.shutdown()
        http_worker.join(timeout=5)
        helper.shutdown()
        helper.server_close()
        worker.join(timeout=5)
        shutil.rmtree(socket_directory)


def test_unregistered_token_cannot_read_change_or_download(tmp_path):
    with dashboard(tmp_path) as (request, store, _):
        assert request("/")[0] == 200
        assert request("/api/dashboard-access/status", token="wrong")[0] == 401
        assert request("/api/dashboard-access/register-self", "POST", {}, token=None)[0] == 401
        data = json.loads(request("/api/dashboard-access/status")[1])
        assert data["can_register"] and not data["allowed"]
        assert "devices" not in data
        for path in WRITE_PATHS - {"/api/dashboard-access/register-self",
                                   "/api/dashboard-access/move"}:
            status, body = request(path, "POST", {}, **{"X-Forwarded-For": "127.0.0.1"})
            assert status == 403, (path, body)
            assert json.loads(body)["registration_required"]
        for path in ("/api/devices", "/api/profiles", "/api/backup", "/api/diagnostics",
                     "/api/action-history", "/api/dashboard-access", "/api/unaffected-devices"):
            assert request(path)[0] == 403, path
        assert store.list_devices() == []


def test_self_registration_rejects_forgery_and_cross_site_then_unlocks_and_removes(tmp_path):
    with dashboard(tmp_path) as (request, store, _):
        register = "/api/dashboard-access/register-self"
        assert request(register, "POST", {"mac_address": MAC})[0] == 400
        assert request(register, "POST", {}, Origin="https://untrusted.example")[0] == 403
        assert request(register, "POST", {}, **{"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert request(register, "POST", {"name": "My Mac"})[0] == 200
        assert store.list_devices()[0]["mac_address"] == MAC
        assert request("/api/profiles")[0] == 200
        assert request("/api/backup")[0] == 200
        assert request("/api/profiles", token="wrong")[0] == 401
        assert request(register, "POST", {"name": "Retry"})[0] == 200
        assert len(store.list_devices()) == 1
        assert request("/api/dashboard-access/remove", "POST", {"mac_address": MAC})[0] == 200
        assert request("/api/profiles")[0] == 403
        assert request("/api/backup")[0] == 403


def test_ambiguous_and_unavailable_observations_fail_closed(tmp_path):
    with dashboard(tmp_path) as (request, store, applier):
        applier.neighbors *= 2
        status, body = request("/api/dashboard-access/register-self", "POST", {})
        assert status == 503, body
        assert store.list_devices() == []
        applier.route = {}
        assert request("/api/dashboard-access/register-self", "POST", {})[0] == 503
        assert request("/api/backup")[0] != 200


def test_moving_to_a_new_address_needs_the_token_and_the_connection_identity(tmp_path):
    from jittersim.config.exemptions import UnaffectedDevice

    old = "02:00:00:00:00:99"
    with dashboard(tmp_path) as (request, store, _):
        store.add(old, "Aeromac")
        move = "/api/dashboard-access/move"
        assert request(move, "POST", {"from_mac": old}, token=None)[0] == 401
        assert request(move, "POST", {"from_mac": old}, token="wrong")[0] == 401
        assert request("/api/dashboard-access/moves", token="wrong")[0] == 401
        # The new address comes from the connection and never from the browser.
        for forged in ({"from_mac": old, "to_mac": MAC}, {"from_mac": old, "mac_address": MAC}):
            assert request(move, "POST", forged)[0] == 400
        assert request(move, "POST", {"from_mac": old}, Origin="https://untrusted.example")[0] == 403
        assert request(move, "POST", {}, **{"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert [item["mac_address"] for item in store.list_devices()] == [old]
        assert request("/api/profiles")[0] == 403


def test_moving_registers_the_new_address_and_undo_can_reverse_it(tmp_path):
    old = "02:00:00:00:00:99"
    with dashboard(tmp_path) as (request, store, _):
        store.add(old, "Aeromac")
        offer = json.loads(request("/api/dashboard-access/moves")[1])
        assert offer["offer"] and offer["candidates"][0]["name"] == "Aeromac"
        assert offer["replaced"] == []
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 200, body
        assert json.loads(body)["allowed"]
        entries = store.list_devices()
        assert entries[0]["replaced_by"] == MAC and entries[1]["mac_address"] == MAC
        assert request("/api/profiles")[0] == 200
        moves = json.loads(request("/api/dashboard-access/moves")[1])
        assert moves["replaced"][0]["old_mac"] == old
        # Undo needs the token and a registered device.
        assert request("/api/dashboard-access/move-undo", "POST", {"from_mac": old}, token="wrong")[0] == 401
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 200, body
        assert [item["mac_address"] for item in store.list_devices()] == [old]
        assert request("/api/profiles")[0] == 403
