from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService, _build_parser
from jittersim.networking.topology import Topology


def service():
    factory = Mock(side_effect=AssertionError("Router must not be contacted"))
    applier = NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "wlan0"))
    return HelperService(applier, router_factory=factory), factory


def test_default_refuses_router_writes_even_with_request_opt_in():
    helper, factory = service()
    for enabled in (True, False):
        result = helper.handle_payload({"action": "router-wifi", "enabled": enabled,
                                       "admin_password": "private", "allow_router_wifi_changes": True})
        assert result["reason"] == "router-control-disabled"
    factory.assert_not_called()


def test_concurrent_requests_cannot_contact_router():
    helper, factory = service()
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: helper.handle_payload(
            {"action": "router-wifi", "enabled": False, "admin_password": "private"}), range(24)))
    assert all(r.get("reason") in ("router-control-disabled", "helper-busy") for r in results)
    factory.assert_not_called()


def test_startup_has_no_router_wifi_write_opt_in():
    args = ["--socket", "/tmp/helper.sock", "--uplink", "eth0", "--test-interface", "wlan0"]
    assert not hasattr(_build_parser().parse_args(args), "allow_router_wifi_changes")
    with pytest.raises(SystemExit):
        _build_parser().parse_args(args + ["--allow-router-wifi-changes"])
