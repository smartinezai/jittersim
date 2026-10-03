"""Check the owner controls, helper boundary and browser proxy together."""

import json
import socket
import threading
from contextlib import ExitStack, contextmanager
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.adblocking import ActiveBlocking
from jittersim.config.exemptions import UnaffectedDevice
from tests.adblocking_support import provision_guard
from jittersim.config.storage import ConfigRepository
from jittersim.config.workflows import WorkflowStore
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.access import AccessPlan
from jittersim.networking.access import AccessManager
from jittersim.networking.adblocking import AdRouting, AdStore, atomic_write, write_json
from jittersim.networking.browser_proxy import BrowserProxyServer
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClientError, HelperService
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor
from tests.helper_support import serving
from tests.unit.test_helper_access import apply_payload
from tests.integration.test_device_management import ClientsHelper
from tests.unit.test_adblocking import ALICE, BOB, applied


@pytest.fixture
def control(tmp_path):
    repo = ConfigRepository(tmp_path / "config.db")
    store = AdStore(
        AdRouting("ads0", FakeExecutor()),
        path=tmp_path / "policy.json",
        lock_path=tmp_path / "lock",
        active_path=tmp_path / "active",
        status_path=tmp_path / "status",
        intent_path=tmp_path / "intent", panic_guard=provision_guard(tmp_path),
    )

    class Helper(ClientsHelper):
        def configure_ad_blocking(self, macs, allowances, **kwargs):
            kwargs.pop("control_revision", None)
            return store.configure(macs, allowances, **kwargs)

        def ad_blocking_status(self):
            return store.status()

        def set_gateway_persistence(self, *, enabled):
            return {"persist_profiles_after_gateway_outage": enabled}

        def reset(self):
            store.pause()
            return {"commands_run": 1}

        def apply(self, *, plan, **kwargs):
            store.resume()
            return super().apply(plan=plan)

    helper = Helper()
    server = ControlServer(("127.0.0.1", 0), repo, helper_client=helper, admin_token="test-token")
    try:
        yield repo, store, helper, server
    finally:
        server.server_close()
        repo.close()


def write(server, payload):
    status, _, body = server.write_response_for("/api/ad-blocking", payload)
    return status, json.loads(body)


def test_device_choice_is_optional_and_does_not_start_profiles(control):
    repo, store, helper, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
    assert (
        json.loads(server.response_for("/api/unaffected-devices")[2])["devices"][0]["ad_blocking"]
        is False
    )
    status, result = write(server, {"mac_address": ALICE, "enabled": True})
    assert status == 200 and "Saved" in result["message"]
    assert helper.plans == []
    assert store.read()["mac_addresses"] == (ALICE,)
    assert repo.list_domain_observations() == ()
    assert result["ad_blocking"]["service"]["query_history_saved"] is False
    status, result = write(server, {"mac_address": ALICE, "enabled": False})
    assert status == 200 and store.read()["mac_addresses"] == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"mac_address": ALICE},
        {"enabled": True},
        {"mac_address": ALICE, "enabled": "false"},
        {"mac_address": BOB, "enabled": True},
        {"allowed_domains": ["address=/evil/"]},
        {"update_list": "yes"},
        {"source_url": "http://localhost/secrets"},
        {},
    ],
)
def test_invalid_controls_leave_saved_and_live_policy_unchanged(control, payload):
    repo, store, _, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE, "", 1))
    status, _ = write(server, payload)
    assert status == 409
    assert repo.list_ad_blocking_devices() == ()
    assert store.read()["mac_addresses"] == ()


def test_removal_syncs_policy_and_start_recovers_failed_helper_write(control):
    repo, store, helper, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE, "", 1))
    configure = helper.configure_ad_blocking
    helper.configure_ad_blocking = Mock(side_effect=HelperClientError("unavailable"))
    status, result = write(server, {"mac_address": ALICE, "enabled": True})
    assert status == 200 and "could not be confirmed" in result["message"]
    assert repo.list_ad_blocking_devices() == (ALICE,)
    assert store.read()["mac_addresses"] == ()
    helper.configure_ad_blocking = configure
    assert server._apply_configuration()[0] == 200
    assert store.read()["mac_addresses"] == (ALICE,)
    assert server._reset_network()[0] == 200
    assert store.read()["running"] is False
    write(server, {"allowed_domains": ["GOOD.TEST."]})
    assert store.read()["running"] is False
    assert server._apply_configuration()[0] == 200
    assert store.read()["running"] is True
    status, _, _ = server.write_response_for(
        "/api/unaffected-devices/delete", {"mac_address": ALICE}
    )
    assert status == 200 and store.read()["mac_addresses"] == ()
    assert repo.list_ad_blocking_devices() == ()


def test_export_import_into_a_fresh_installation_keeps_ad_choices(control, tmp_path):
    repo, _, _, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
    assert write(server, {"mac_address": ALICE, "enabled": True})[0] == 200
    assert write(server, {"allowed_domains": ["working.example.net"]})[0] == 200
    payload = json.loads(server.response_for("/api/export")[2])
    with ConfigRepository(tmp_path / "imported.db") as target:
        imported = ControlServer(("127.0.0.1", 0), target, admin_token="fixture-token")
        try:
            assert imported.write_response_for("/api/import", payload)[0] == 200
            assert target.list_ad_blocking_devices() == (ALICE,)
            assert target.list_ad_blocking_allowances() == ("working.example.net",)
            assert json.loads(imported.response_for("/api/export")[2])["ad_blocking_devices"] == [ALICE]
        finally:
            imported.server_close()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_import_preserves_choices_only_for_retained_protected_devices(control, version):
    repo, store, _, server = control
    for mac in (ALICE, BOB):
        repo.save_unaffected_device(UnaffectedDevice(mac, "", 1))
        repo.set_ad_blocking(mac, True)
    repo.set_ad_blocking_allowances(["working.example.net"])
    payload = json.loads(server.response_for("/api/export")[2])
    payload["version"] = version
    payload.pop("ad_blocking_devices")
    payload.pop("ad_blocking_allowances")
    payload["everyone_else"]["unaffected_devices"] = [{"mac_address": ALICE, "name": "Alice"}]
    assert server._reset_network()[0] == 200
    assert server.write_response_for("/api/import", payload)[0] == 200
    assert repo.list_ad_blocking_devices() == (ALICE,)
    assert repo.list_ad_blocking_allowances() == ("working.example.net",)
    assert store.read()["mac_addresses"] == (ALICE,)
    assert not store.read()["running"]
    payload["ad_blocking_devices"] = []
    payload["ad_blocking_allowances"] = []
    assert server.write_response_for("/api/import", payload)[0] == 200
    assert repo.list_ad_blocking_devices() == ()
    assert repo.list_ad_blocking_allowances() == ()
    assert store.read()["mac_addresses"] == ()
    assert not store.read()["running"]


@pytest.mark.parametrize("restore_route", ["import", "history"])
def test_settings_restore_keeps_preferences_and_the_later_stop(control, restore_route):
    repo, store, _, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
    write(server, {"mac_address": ALICE, "enabled": True})
    write(server, {"allowed_domains": ["working.example.net"]})
    payload = json.loads(server.response_for("/api/export")[2])
    history = WorkflowStore(repo)
    history.checkpoint("Ad blocking enabled")
    identifier = history.history()[0]["id"]
    write(server, {"mac_address": ALICE, "enabled": False, "allowed_domains": []})
    assert server._reset_network()[0] == 200
    stopped_intent = store.intent_path.read_bytes()
    if restore_route == "import":
        assert server.write_response_for("/api/import", payload)[0] == 200
    else:
        preview = json.loads(server.write_response_for("/api/history/preview", {"id": identifier})[2])
        assert server.write_response_for("/api/history/restore", {
            "id": identifier, "current_version": preview["current_version"],
        })[0] == 200
    assert repo.list_ad_blocking_devices() == (ALICE,)
    assert repo.list_ad_blocking_allowances() == ("working.example.net",)
    assert store.read()["mac_addresses"] == (ALICE,)
    assert store.read()["allowed_domains"] == ("working.example.net",)
    assert not store.read()["running"]
    assert store.intent_path.read_bytes() == stopped_intent


def test_ad_controls_require_owner_token_for_reads_and_writes(control):
    _, _, _, server = control
    url = f"http://127.0.0.1:{server.server_address[1]}/api/ad-blocking"
    for data, authenticated in ((None, False), (b'{"update_list": true}', False), (None, True)):
        replies = []

        def ask():
            request = Request(url, data=data, headers={"Content-Type": "application/json"})
            if authenticated:
                request.add_header("Authorization", "Bearer test-token")
            try:
                with urlopen(request, timeout=2) as reply:
                    replies.append(reply.status)
            except HTTPError as error:
                replies.append(error.code)

        worker = threading.Thread(target=ask)
        worker.start()
        server.handle_request()
        worker.join(timeout=3)
        assert replies == [200 if authenticated else 401]


def test_helper_rejects_injection_and_panic_pauses_saved_policy(tmp_path):
    store = AdStore(
        AdRouting("wlan0", FakeExecutor()),
        path=tmp_path / "policy",
        lock_path=tmp_path / "lock",
        active_path=tmp_path / "active",
        status_path=tmp_path / "status",
        intent_path=tmp_path / "intent", panic_guard=provision_guard(tmp_path),
    )
    manager = AccessManager(
        executor=AccessExecutor(tmp_path),
        topology=Topology("eth0", "wlan0"),
        directory=tmp_path / "access",
        runtime_path=tmp_path / "access-policy",
        ad_blocking_store=store,
    )
    with serving(
        lambda applier: HelperService(applier, ad_blocking_store=store, access_manager=manager)
    ) as (client, _):
        assert client.configure_ad_blocking([ALICE], [], resume=True)["available"]
        with pytest.raises(HelperClientError):
            client.configure_ad_blocking([ALICE], ["evil.test\naddress=/other/"])
        assert store.read()["mac_addresses"] == (ALICE,)
        revision = client.control_revision()
        client.normal_access()
        assert client.ad_blocking_status()["paused"]
        assert store.read()["mac_addresses"] == (ALICE,)
        with pytest.raises(HelperClientError):
            client.configure_ad_blocking([ALICE], [], resume=True, control_revision=revision)
        assert store.read()["running"] is False
        store.path.unlink()
        store.intent_path.unlink()
        assert client.configure_ad_blocking([ALICE], [])["paused"]
        assert store.read()["running"] is False
        assert client.configure_ad_blocking([ALICE], [], resume=True)["available"]
        assert store.read()["running"] is True


@pytest.mark.parametrize("panic_route", ["helper", "dashboard"])
def test_queued_enable_cannot_reverse_panic_but_a_fresh_enable_can(tmp_path, panic_route):
    store = AdStore(
        AdRouting("wlan0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "intent", panic_guard=provision_guard(tmp_path),
    )
    manager = AccessManager(
        executor=AccessExecutor(tmp_path), topology=Topology("eth0", "wlan0"),
        directory=tmp_path / "access", runtime_path=tmp_path / "access-policy",
        ad_blocking_store=store,
    )
    queued = threading.Event()

    class ObservedScheduler(RuntimeScheduler):
        @contextmanager
        def exclusive(self):
            if threading.current_thread().name == "queued-ad-enable":
                queued.set()
            with super().exclusive():
                yield

    with (
        serving(lambda applier: HelperService(
            applier, ad_blocking_store=store, access_manager=manager,
        )) as (client, _),
        ConfigRepository(tmp_path / "config.db") as repo,
    ):
        repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
        repo.set_ad_blocking(ALICE, True)
        client.configure_ad_blocking([ALICE], [], resume=True)
        scheduler = ObservedScheduler(database_path=tmp_path / "config.db", helper_client=client)
        server = ControlServer(
            ("127.0.0.1", 0), repo, helper_client=client,
            scheduler=scheduler, admin_token="fixture-token",
        )
        replies = []

        def enable():
            replies.append(write(server, {
                "mac_address": ALICE, "enabled": True,
                "allowed_domains": ["new.example.net"],
            }))

        try:
            with scheduler.exclusive():
                worker = threading.Thread(target=enable, name="queued-ad-enable")
                worker.start()
                assert queued.wait(2)
                if panic_route == "helper":
                    client.normal_access()
                    assert scheduler.halt_count == 0
                else:
                    assert server.write_response_for("/api/normal-access", {})[0] == 200
                assert not store.read()["running"]
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert replies and replies[0][0] == 409
            assert not store.read()["running"]
            assert repo.list_ad_blocking_allowances() == ()
            # Confirm recovery before choosing a new deliberate enable.
            assert server.write_response_for("/api/normal-access", {})[0] == 200
            assert write(server, {"mac_address": ALICE, "enabled": True})[0] == 200
            assert store.read()["running"]
        finally:
            server.server_close()


@pytest.mark.parametrize("helper_restart", [False, True])
@pytest.mark.parametrize("later_stop", [None, "no_enable", "reset", "panic", "failed_panic", "during_startup", "protection_removed", "sync_refused"])
def test_dashboard_restart_preserves_only_a_later_deliberate_ad_enable(tmp_path, later_stop, helper_restart):
    guard = provision_guard(tmp_path)
    store = AdStore(
        AdRouting("wlan0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "intent", panic_guard=guard,
    )
    manager = AccessManager(
        executor=AccessExecutor(tmp_path), topology=Topology("eth0", "wlan0"),
        directory=tmp_path / "access", runtime_path=tmp_path / "access-policy",
        ad_blocking_store=store,
    )
    def service(applier):
        return HelperService(applier, ad_blocking_store=store, access_manager=manager, panic_guard=guard)

    with ExitStack() as helpers, ConfigRepository(tmp_path / "config.db") as repo:
        client, _ = helpers.enter_context(serving(service))
        repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
        server = ControlServer(("127.0.0.1", 0), repo, helper_client=client, admin_token="fixture-token")
        try:
            if later_stop != "protection_removed":
                assert server._reset_network()[0] == 200
            if later_stop != "no_enable":
                assert write(server, {"mac_address": ALICE, "enabled": True})[0] == 200
                assert store.read()["running"]
            if later_stop == "reset":
                assert server._reset_network()[0] == 200
            elif later_stop == "panic":
                assert client.normal_access()["ok"]
            elif later_stop == "failed_panic":
                manager.disable = Mock(side_effect=ValueError("unconfirmed household recovery"))
                with pytest.raises(HelperClientError):
                    client.normal_access()
            elif later_stop == "protection_removed":
                repo.delete_unaffected_device(ALICE)
                assert repo.list_ad_blocking_devices() == ()
        finally:
            server.server_close()
        permission = store.intent_path.read_bytes()
        if helper_restart:
            helpers.close()
            client, _ = helpers.enter_context(serving(service))
            assert store.intent_path.read_bytes() == permission
        if later_stop == "during_startup":
            configure = client.configure_ad_blocking

            def panic_before_copy(*args, **kwargs):
                client.normal_access()
                return configure(*args, **kwargs)

            client.configure_ad_blocking = panic_before_copy
        elif later_stop == "sync_refused":
            client.configure_ad_blocking = Mock(side_effect=HelperClientError("helper busy"))
            client.ad_blocking_status = Mock(return_value={"available": True, "ready": True,
                                                           "message": "Ad blocking is active."})
        restarted = ControlServer(("127.0.0.1", 0), repo, helper_client=client, admin_token="fixture-token")
        try:
            restarted.recover_saved_network_state()
            assert store.read()["running"] is (later_stop in (None, "protection_removed", "sync_refused"))
            if later_stop != "during_startup":
                assert store.intent_path.read_bytes() == permission
            else:
                assert client.gateway_status()["panic_stop_latched"]
                assert json.loads(store.intent_path.read_bytes())["running"] is False
            choices = () if later_stop in ("no_enable", "protection_removed") else (ALICE,)
            assert repo.list_ad_blocking_devices() == choices
            assert store.read()["mac_addresses"] == choices
            assert repo.get_runtime_state().last_action == (None if later_stop == "protection_removed" else "reset")
            assert repo.get_runtime_state().last_result == (None if later_stop == "protection_removed" else "success")
            if later_stop == "sync_refused":
                status = json.loads(restarted.response_for("/api/ad-blocking")[2])["service"]
                assert not status["ready"] and "could not be confirmed" in status["message"]
        finally:
            restarted.server_close()


@pytest.mark.parametrize("error,reason", [("unknown action", None), ("busy", "helper-busy"), ("failed", None)])
def test_automatic_reset_falls_back_only_after_explicit_unknown_action(error, reason):
    requests = []

    class EarlierHelper(HelperService):
        def _handle_payload(self, payload):
            requests.append(payload["action"])
            if payload["action"] == "recover-reset":
                return {"ok": False, "error": error, "reason": reason}
            return super()._handle_payload(payload)

    with serving(EarlierHelper) as (client, _):
        if error == "unknown action":
            assert client.recover_reset()["action"] == "reset"
            assert requests == ["recover-reset", "reset"]
        else:
            with pytest.raises(HelperClientError) as failure:
                client.recover_reset()
            assert str(failure.value) == error and failure.value.reason == reason
            assert requests == ["recover-reset"]


def test_household_changes_preserve_only_current_permission_and_start_renews_after_guard(tmp_path):
    guard = provision_guard(tmp_path)
    store = AdStore(
        AdRouting("wlan0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "intent", panic_guard=guard,
    )
    manager = AccessManager(
        executor=AccessExecutor(tmp_path), topology=Topology("eth0", "wlan0"),
        directory=tmp_path / "access", runtime_path=tmp_path / "access-policy",
        ad_blocking_store=store,
    )
    with serving(lambda applier: HelperService(
        applier, ad_blocking_store=store, access_manager=manager, panic_guard=guard,
    )) as (client, _):
        client.configure_ad_blocking([ALICE], [], resume=True)
        before = json.loads(store.intent_path.read_bytes())["panic_witness"]
        client.apply_access(AccessPlan())
        assert store.read()["running"]
        after = json.loads(store.intent_path.read_bytes())["panic_witness"]
        assert after != before and after == store._panic_witness()
        client.normal_access()
        client.configure_ad_blocking([ALICE], [], resume=True)
        client.apply_access(AccessPlan())
        assert store.read()["running"]
        client.normal_access()
        assert not store.read()["running"]
        client.apply_access(AccessPlan())
        assert not store.read()["running"]
        payload = {**apply_payload(AccessPlan()), "explicit_apply": True,
                   "expected_control_revision": client.control_revision()}
        assert client._request(payload)["ok"]
        assert store.read()["running"]
        assert json.loads(store.intent_path.read_bytes())["panic_witness"] == store._panic_witness()
        store.path.write_bytes(b"broken ad settings")
        assert client.normal_access()["ok"]
        assert not manager.applied.options.enabled
        assert json.loads(store.intent_path.read_bytes())["running"] is False
        client.configure_ad_blocking([ALICE], ["working.example.net"])
        assert store.read()["mac_addresses"] == (ALICE,)
        assert not store.read()["running"]
        client.configure_ad_blocking([ALICE], ["working.example.net"], resume=True)
        assert store.read()["running"]


def test_removal_after_explicit_enable_while_profiles_are_stopped_clears_blocking(control):
    repo, store, helper, server = control
    repo.save_unaffected_device(UnaffectedDevice(ALICE, "Alice", 1))
    assert server._reset_network()[0] == 200
    assert write(server, {"mac_address": ALICE, "enabled": True})[0] == 200
    assert store.read()["running"] is True
    plans = len(helper.plans)
    assert (
        server.write_response_for("/api/unaffected-devices/delete", {"mac_address": ALICE})[0]
        == 200
    )
    assert store.read()["mac_addresses"] == ()
    assert repo.list_ad_blocking_devices() == ()
    assert len(helper.plans) == plans


def test_proxy_blocks_before_lookup_only_for_selected_identity_without_active_profiles(tmp_path):
    store = AdStore(
        AdRouting("ads0", FakeExecutor()),
        path=tmp_path / "policy",
        lock_path=tmp_path / "lock",
        active_path=tmp_path / "active",
        status_path=tmp_path / "status",
        intent_path=tmp_path / "intent", panic_guard=provision_guard(tmp_path),
    )
    state = applied(store, tmp_path)
    active = ActiveBlocking(store.active_path, tmp_path / "domains")
    identity = [ALICE]
    lookup = Mock(return_value=[])
    server = BrowserProxyServer(
        ("127.0.0.1", 0),
        active_plan_path=tmp_path / "absent-plan",
        client_network="127.0.0.0/8",
        mark_socket=lambda *_: None,
        identify_mac=lambda _: identity[0],
        resolve_addresses=lookup,
        ad_blocking=active,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def connect():
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.sendall(b"CONNECT advert.test:443 HTTP/1.1\r\n\r\n")
            return client.recv(4096)

    try:
        assert b"503" in connect()
        lookup.assert_not_called()
        identity[0] = BOB
        connect()
        lookup.assert_called_with("advert.test")
        identity[0] = ALICE
        lookup.reset_mock()
        write_json(store.active_path, {**state, "allowed_domains": ["advert.test"]})
        connect()
        lookup.assert_called_once_with("advert.test")
        lookup.reset_mock()
        atomic_write(store.active_path, b"bad state")
        connect()
        lookup.assert_called_once_with("advert.test")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
