"""Saved protection reaches resolver updates and failures remain visible."""

import json
from unittest.mock import Mock

from jittersim.api.server import ControlServer
from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.storage import ConfigRepository

MAC = "02:00:00:00:00:99"


def test_access_settings_include_saved_protection_and_refresh_cached_content():
    with ConfigRepository(":memory:") as repository:
        original = repository.access_plan()
        saved = UnaffectedDevice(MAC, "Protected", 1)
        repository.save_unaffected_device(saved)
        assert repository.access_settings().unaffected_devices == (saved,)
        protected = repository.access_plan()
        assert protected is not original
        assert repository.access_plan() is protected
        repository.delete_unaffected_device(MAC)
        assert repository.access_settings().unaffected_devices == ()
        assert repository.access_plan() is not protected


def test_address_saved_response_refreshes_resolver_before_other_network_updates():
    server = ControlServer.__new__(ControlServer)
    calls = []
    server._apply_household_block_list = Mock(side_effect=lambda: calls.append("resolver"))
    server._sync_ad_blocking = Mock(side_effect=lambda: calls.append("adblocking"))
    response = (200, "application/json", b'{"live_applied":true}')
    server._saved_control_response = Mock(
        side_effect=lambda fields: (calls.append("saved") or response)
    )
    fields = {"moved": True}
    assert server._address_saved_response(fields) == response
    assert calls == ["resolver", "adblocking", "saved"]
    server._saved_control_response.assert_called_once_with(fields)


def test_address_saved_response_preserves_saved_fields_when_resolver_update_fails():
    server = ControlServer.__new__(ControlServer)
    server._apply_household_block_list = Mock(return_value=(
        503, "application/json", json.dumps({"error": "Saved but resolver update failed"}).encode()
    ))
    server._sync_ad_blocking = Mock()
    server._saved_control_response = Mock()
    status, content_type, body = server._address_saved_response({"moved": True, "name": "Tablet"})
    assert status == 503
    assert content_type == "application/json"
    assert json.loads(body) == {
        "moved": True, "name": "Tablet", "live_applied": False,
        "error": "Saved but resolver update failed",
    }
    server._sync_ad_blocking.assert_not_called()
    server._saved_control_response.assert_not_called()
