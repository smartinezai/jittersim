"""Check the owner controls, helper boundary and browser proxy together."""

import json
import socket
import threading
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jittersim.api.server import ControlServer
from jittersim.config.adblocking import ActiveBlocking
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.storage import ConfigRepository
from jittersim.networking.access import AccessManager
from jittersim.networking.adblocking import AdRouting, AdStore, atomic_write, write_json
from jittersim.networking.browser_proxy import BrowserProxyServer
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperClientError, HelperService
from jittersim.networking.topology import Topology
from tests.access_support import AccessExecutor
from tests.helper_support import serving
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
    )

    class Helper(ClientsHelper):
        def configure_ad_blocking(self, macs, allowances, **kwargs):
            kwargs.pop("control_revision", None)
            return store.configure(macs, allowances, **kwargs)

        def ad_blocking_status(self):
            return store.status()

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
        assert client.configure_ad_blocking([ALICE], [], resume=True)["available"]
        assert store.read()["running"] is True


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
