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
def dashboard(tmp_path, configure=None, service_class=HelperService):
    applier = PeerApplier()
    store = AccessStore(tmp_path / "access.json")
    socket_directory = Path(tempfile.mkdtemp(prefix="js-access-", dir="/tmp"))
    socket_path = socket_directory / "helper.sock"
    helper = UnixHelperServer(socket_path, service_class(applier, access_store=store))
    worker = threading.Thread(target=helper.serve_forever)
    worker.start()
    ready = queue.Queue()

    class WifiServer(ControlServer):
        def get_request(self):
            connection, address = super().get_request()
            # A disposable loopback transport stands in for a real Wi-Fi peer.
            return connection, (getattr(self, "test_peer", PEER), address[1])

    def serve():
        with ConfigRepository(tmp_path / "config.db") as repository:
            with WifiServer(
                ("127.0.0.1", 0),
                repository,
                admin_token="test-token",
                helper_client=HelperClient(socket_path=socket_path),
            ) as server:
                if configure is not None:
                    configure(server)
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

    request.server = server
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
        for path in WRITE_PATHS - {
            "/api/dashboard-access/register-self",
            "/api/dashboard-access/move",
        }:
            status, body = request(path, "POST", {}, **{"X-Forwarded-For": "127.0.0.1"})
            assert status == 403, (path, body)
            assert json.loads(body)["registration_required"]
        for path in (
            "/api/devices",
            "/api/profiles",
            "/api/backup",
            "/api/diagnostics",
            "/api/action-history",
            "/api/dashboard-access",
            "/api/unaffected-devices",
        ):
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
        assert (
            request(move, "POST", {"from_mac": old}, Origin="https://untrusted.example")[0] == 403
        )
        assert request(move, "POST", {}, **{"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert [item["mac_address"] for item in store.list_devices()] == [old]
        assert request("/api/profiles")[0] == 403


def test_moving_from_a_device_that_was_never_saved_is_refused(tmp_path):
    with dashboard(tmp_path) as (request, store, _):
        store.add("02:00:00:00:00:99", "Aeromac")
        status, body = request(
            "/api/dashboard-access/move", "POST", {"from_mac": "02:00:00:00:00:55"}
        )
        assert status == 400, body
        assert [item["mac_address"] for item in store.list_devices()] == ["02:00:00:00:00:99"]


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
        assert (
            request("/api/dashboard-access/move-undo", "POST", {"from_mac": old}, token="wrong")[0]
            == 401
        )
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 200, body
        assert [item["mac_address"] for item in store.list_devices()] == [old]
        assert request("/api/profiles")[0] == 403


def prepare_protected_move(server):
    from jittersim.config.exemptions import UnaffectedDevice

    server.repository.save_unaffected_device(UnaffectedDevice("02:00:00:00:00:99", "Aeromac", 1))
    server._station_devices = lambda: [{"mac_address": MAC, "ipv4_address": PEER}]
    server._validate_saved_plan = lambda: None


def test_post_commit_conflict_keeps_saved_access_and_protection_together(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            409,
            "application/json",
            json.dumps(
                {
                    **fields,
                    "live_applied": False,
                    "error": "Settings saved but the network update failed.",
                }
            ).encode(),
        )

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["saved"] is True
        assert any(
            item["mac_address"] == MAC and "replaced_by" not in item
            for item in store.list_devices()
        )
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [MAC]
        assert repository.get_address_move(old).moved_access


def test_failed_compensation_retains_a_recoverable_move(tmp_path, monkeypatch):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)

        def refuse_plan():
            raise ValueError("simulated save refusal")

        server._validate_saved_plan = refuse_plan

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        original_undo = store.undo_move

        def failed_undo(*args, **kwargs):
            from jittersim.networking.dashboard_access import AccessError

            raise AccessError("simulated compensation failure")

        monkeypatch.setattr(store, "undo_move", failed_undo)
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 409, body
        result = json.loads(body)
        assert result["recovery_required"] and result["old_mac"] == old and result["new_mac"] == MAC
        monkeypatch.setattr(store, "undo_move", original_undo)
        assert request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})[0] == 200
        assert [item["mac_address"] for item in store.list_devices()] == [old]
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [old]
        assert repository.get_address_move(old) is None


def test_interrupted_undo_resumes_from_durable_helper_receipt(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            200,
            "application/json",
            json.dumps(fields).encode(),
        )
        original = server.repository.delete_address_move
        attempts = []

        def fail_once(address):
            from jittersim.config.storage import StorageError

            attempts.append(address)
            if len(attempts) == 1:
                raise StorageError("simulated Undo database failure")
            return original(address)

        server.repository.delete_address_move = fail_once

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        assert request("/api/dashboard-access/move", "POST", {"from_mac": old})[0] == 200
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
        request.server.test_peer = "127.0.0.1"
        assert request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})[0] == 200
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [old]
        assert repository.get_address_move(old) is None


def test_source_replacement_during_helper_wait_is_not_overwritten(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            200,
            "application/json",
            json.dumps(fields).encode(),
        )
        original = server.dashboard_access

        def edit_during_move(peer, operation="status", **values):
            if operation == "move-address-owned":
                source = server.repository.list_unaffected_devices()[0]
                server.repository.delete_unaffected_device(old)
                server.repository.save_unaffected_device(source)
            return original(peer, operation, **values)

        server.dashboard_access = edit_during_move

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 409, body
        assert [entry["mac_address"] for entry in store.list_devices()] == [old]
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert [entry.mac_address for entry in repository.list_unaffected_devices()] == [old]


def test_import_during_pending_move_preserves_recovery_and_imported_protection(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        original = server.dashboard_access

        def import_during_move(peer, operation="status", **values):
            if operation == "move-address-owned":
                server.repository.replace_settings(server.repository.saved_settings())
            return original(peer, operation, **values)

        server.dashboard_access = import_during_move

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
        request.server.test_peer = "127.0.0.1"
        assert request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})[0] == 409
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert repository.get_address_move(old).phase == "pending"
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [old]


def test_undo_parent_fsync_failure_uses_exact_completed_receipt(tmp_path, monkeypatch):
    import os
    import stat

    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            200,
            "application/json",
            json.dumps(fields).encode(),
        )

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        assert request("/api/dashboard-access/move", "POST", {"from_mac": old})[0] == 200
        original = os.fsync
        attempts = []

        def fail_directory_once(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not attempts:
                attempts.append(descriptor)
                raise OSError("simulated parent fsync failure")
            return original(descriptor)

        monkeypatch.setattr(os, "fsync", fail_directory_once)
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
        assert [item["mac_address"] for item in store.list_devices()] == [old]
        assert store.list_devices()[0]["undo_id"]
        request.server.test_peer = "127.0.0.1"
        recovery = json.loads(request("/api/dashboard-access/moves")[1])["replaced"]
        assert recovery[0]["recovery_required"] and recovery[0]["old_mac"] == old
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 200, body
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert repository.get_address_move(old) is None
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [old]


def test_same_pair_retry_keeps_pending_id_and_does_not_repeat_helper_write(tmp_path):
    old = "02:00:00:00:00:99"
    observed = []

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            200,
            "application/json",
            json.dumps(fields).encode(),
        )
        original = server.dashboard_access

        def retry_during_move(peer, operation="status", **values):
            if operation == "move-address-owned":
                before = server.repository.get_address_move(old)
                status, _, body = server.move_device_address_for_peer(old, peer)
                assert status == 409
                assert json.loads(body)["move_id"] == before.move_id
                assert server.repository.get_address_move(old) == before
                observed.append(before.move_id)
            return original(peer, operation, **values)

        server.dashboard_access = retry_during_move

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 200, body
        assert len(observed) == 1
        assert (
            next(item for item in store.list_devices() if item["mac_address"] == MAC)["owner_id"]
            == observed[0]
        )


def test_original_move_cannot_overwrite_completed_recovery(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        original = server.dashboard_access

        def reverse_before_reply(peer, operation="status", **values):
            result = original(peer, operation, **values)
            if operation == "move-address-owned":
                assert server.undo_device_address_move(old, peer)[0] == 200
            return result

        server.dashboard_access = reverse_before_reply

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        assert request("/api/dashboard-access/move", "POST", {"from_mac": old})[0] == 409
        assert [item["mac_address"] for item in store.list_devices()] == [old]
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert repository.get_address_move(old) is None
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [old]


def test_protection_edit_during_undo_is_not_overwritten(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            200,
            "application/json",
            json.dumps(fields).encode(),
        )
        original = server.dashboard_access

        def edit_during_undo(peer, operation="status", **values):
            if operation == "undo-move-owned":
                moved = server.repository.list_unaffected_devices()[0]
                server.repository.delete_unaffected_device(MAC)
                server.repository.save_unaffected_device(moved)
            return original(peer, operation, **values)

        server.dashboard_access = edit_during_undo

    with dashboard(tmp_path, configure) as (request, store, _):
        store.add(old, "Aeromac")
        assert request("/api/dashboard-access/move", "POST", {"from_mac": old})[0] == 200
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert repository.get_address_move(old).phase == "undoing"
        assert [item.mac_address for item in repository.list_unaffected_devices()] == [MAC]
        assert repository.address_move_protection_owner(MAC) == ""


def test_lost_reply_cannot_hide_access_transfer_after_initial_registration_changes(tmp_path):
    old = "02:00:00:00:00:99"
    shared = {}

    def configure(server):
        prepare_protected_move(server)
        original = server.dashboard_access

        def lose_after_registration_change(peer, operation="status", **values):
            if operation.startswith("move-address"):
                shared["store"].remove(MAC)
                original(peer, operation, **values)
                from jittersim.networking.helper import HelperClientError

                raise HelperClientError("simulated lost reply")
            return original(peer, operation, **values)

        server.dashboard_access = lose_after_registration_change

    with dashboard(tmp_path, configure) as (request, store, _):
        shared["store"] = store
        store.add(old, "Aeromac")
        store.add(MAC, "Initially registered")
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
        request.server.test_peer = "127.0.0.1"
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 200, body
        entries = store.list_devices()
        assert [item["mac_address"] for item in entries] == [old]
        assert "replaced_by" not in entries[0]


class BeforeOwnedOperations(HelperService):
    """An older action whitelist, even after a stale capability read says yes."""

    def _handle_dashboard_access(self, payload):
        operation = payload.get("operation")
        if operation == "capabilities":
            return {"ok": True, "owned_moves": True}
        if operation in ("move-address-owned", "undo-move-owned"):
            return {"ok": False, "error": "Unknown dashboard access action."}
        return super()._handle_dashboard_access(payload)


def test_older_helper_refuses_owned_move_before_mutating_access(tmp_path):
    old = "02:00:00:00:00:99"
    with dashboard(tmp_path, prepare_protected_move, BeforeOwnedOperations) as (request, store, _):
        store.add(old, "Aeromac")
        assert request.server.helper_client.dashboard_access("capabilities")["owned_moves"]
        before = store.list_devices()
        status, body = request("/api/dashboard-access/move", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
        assert store.list_devices() == before


def test_older_helper_refuses_owned_undo_and_keeps_recovery_evidence(tmp_path):
    old = "02:00:00:00:00:99"

    def configure(server):
        prepare_protected_move(server)
        server._saved_control_response = lambda fields: (
            200,
            "application/json",
            json.dumps(fields).encode(),
        )
        assert server.move_device_address(old, MAC, access_moved=True)[0] == 200
        record = server.repository.get_address_move(old)
        store = AccessStore(tmp_path / "access.json")
        store.add(old, "Aeromac")
        store.move(old, MAC, move_id=record.move_id)

    with dashboard(tmp_path, configure, BeforeOwnedOperations) as (request, store, _):
        assert request.server.helper_client.dashboard_access("capabilities")["owned_moves"]
        before = store.list_devices()
        status, body = request("/api/dashboard-access/move-undo", "POST", {"from_mac": old})
        assert status == 409, body
        assert json.loads(body)["recovery_required"]
        assert store.list_devices() == before
    with ConfigRepository(tmp_path / "config.db") as repository:
        assert repository.get_address_move(old).phase == "undoing"
