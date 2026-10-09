"""Router writes remain blocked across HTTP and the privileged helper socket."""

import json
import time
from unittest.mock import Mock

import pytest

from jittersim.networking.helper import HelperService
from tests.integration.test_dashboard_access_http import dashboard


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("background", [True, False])
def test_router_write_is_refused_without_contacting_router(tmp_path, enabled, background):
    factory = Mock(side_effect=AssertionError("Router must not be contacted"))

    class GuardedService(HelperService):
        def __init__(self, applier, **kwargs):
            super().__init__(applier, router_factory=factory, **kwargs)

    with dashboard(tmp_path, service_class=GuardedService) as (request, _, _):
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Test Mac"})[0] == 200
        payload = {
            "admin_password": "private-test-password",
            "enabled": enabled,
            "confirm_disable": True,
            "background": background,
        }
        status, body = request("/api/router/wifi", "POST", payload)
        if background:
            assert status == 202, body
            identifier = json.loads(body)["operation_id"]
            deadline = time.monotonic() + 5
            while status == 202 and time.monotonic() < deadline:
                status, body = request("/api/router/operation", "POST", {
                    "admin_password": payload["admin_password"],
                    "operation_id": identifier,
                })
                if status == 202:
                    time.sleep(0.01)
        assert status == 502, body
        assert "error" in json.loads(body)
        assert payload["admin_password"].encode() not in body
        factory.assert_not_called()
