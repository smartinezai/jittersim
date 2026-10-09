"""A router password check is rate limited by the server for every way of asking."""

import json
from unittest.mock import Mock

from jittersim.networking.helper import HelperService
from jittersim.networking.router import RouterError
from tests.integration.test_dashboard_access_http import dashboard

PASSWORD = "private-test-password"
CHECK = "/api/router/check"


def router_service(factory):
    class RouterService(HelperService):
        def __init__(self, applier, **kwargs):
            applier.uplink_info = lambda: {"gateway": "192.0.2.1"}
            super().__init__(applier, router_factory=factory, **kwargs)

    return RouterService


def test_repeat_checks_by_any_route_or_password_do_not_log_in_to_the_router_again(tmp_path):
    router = Mock()
    factory = Mock(return_value=router)
    with dashboard(tmp_path, service_class=router_service(factory)) as (request, _, _):
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Test Mac"})[0] == 200
        status, body = request(CHECK, "POST", {"admin_password": PASSWORD})
        assert status == 200, body
        assert router.login.call_count == 1

        refused = [
            # Straight to the API, as a script or another tab without the dashboard would.
            request(CHECK, "POST", {"admin_password": PASSWORD}),
            request(CHECK, "POST", {"admin_password": "another password"}),
            request(CHECK, "POST", {"admin_password": PASSWORD, "background": False}),
            request(CHECK, "POST", {"admin_password": PASSWORD, "background": True}),
        ]
        identifier = json.loads(request("/api/wifi-operation/request-id")[1])["request_id"]
        refused.append(request(CHECK, "POST", {
            "admin_password": PASSWORD, "background": True, "request_id": identifier}))
        for status, body in refused:
            assert status == 429, body
            data = json.loads(body)
            assert 1 <= data["retry_after"] <= 15
            assert "Wait" in data["error"] and PASSWORD not in body.decode()
        assert router.login.call_count == 1

        # The wait is held by the server, so it also holds after the page is reloaded and the
        # status read shares it.
        state = json.loads(request("/api/router/status", "POST", {
            "admin_password": PASSWORD, "refresh": True})[1])["result"]
        assert state["enabled"] is None
        assert router.login.call_count == 1


def test_failed_check_waits_thirty_seconds_and_a_replay_still_returns_its_result(tmp_path):
    router = Mock()
    router.login.side_effect = RouterError("rejected", reason="login")
    factory = Mock(return_value=router)
    with dashboard(tmp_path, service_class=router_service(factory)) as (request, _, _):
        assert request("/api/dashboard-access/register-self", "POST", {"name": "Test Mac"})[0] == 200
        identifier = json.loads(request("/api/wifi-operation/request-id")[1])["request_id"]
        payload = {"admin_password": PASSWORD, "background": True, "request_id": identifier}
        status, body = request(CHECK, "POST", payload)
        assert status == 202, body
        status = 202
        while status == 202:
            status, body = request("/api/router/operation", "POST", {
                "admin_password": PASSWORD, "operation_id": identifier})
        assert status == 502, body
        assert router.login.call_count == 1

        # The same request answers from its stored result and does not count as a new try.
        assert request(CHECK, "POST", payload)[0] == 502
        status, body = request(CHECK, "POST", {"admin_password": PASSWORD})
        assert status == 429, body
        assert 25 <= json.loads(body)["retry_after"] <= 30
        assert router.login.call_count == 1
        assert PASSWORD not in body.decode()

        # Once the wait is over exactly one more check goes through.
        request.server.router_operations._next_read = 0.0
        assert request(CHECK, "POST", {"admin_password": PASSWORD})[0] == 502
        assert router.login.call_count == 2
        assert request(CHECK, "POST", {"admin_password": PASSWORD})[0] == 429
        assert router.login.call_count == 2
